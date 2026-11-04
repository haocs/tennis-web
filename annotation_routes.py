"""Common metadata API and a read adapter for retained coaching suggestions."""
import copy
import json
from pathlib import Path
import re
from flask import abort, jsonify, request
from annotations import finite, AnnotationStore, AnnotationConflict, KINDS, SOURCE_ID, canonical, project, validate_body, validate_target
from players import decorate_players


def register_annotations(app, library, session):
    store = AnnotationStore(Path(app.config['DATA_ROOT'])/'annotations.sqlite3')
    app.extensions['annotations'] = store
    queue = app.extensions['coaching']

    def optional_source(source):
        try:return decorate_players(copy.deepcopy(library.get(source)), app.extensions['state'].all())
        except (KeyError, OSError, ValueError, TypeError):return None

    def check_source(source):
        if not SOURCE_ID.fullmatch(source):abort(404, 'Recording not found')

    def check_publication(body, data):
        if not finite(body.get('publication')) or body.get('publication') != data['published_at']:
            abort(409, 'Recording changed. Refresh before saving metadata.')

    def context(data):
        processing = data.get('processing') or {}
        return dict(publication=data['published_at'], source_duration=data['source']['duration_seconds'],
                    processing_ref={k:v for k,v in processing.items() if k in ('run_id','algorithm_version') and isinstance(v,str) and len(v)<=120} if isinstance(processing,dict) else {})

    def coaches(source, data, after=''):
        # Keep one authoritative coaching record. No duplicate writes/migration.
        clips = data['clips'] if data else []
        publication = data['published_at'] if data else None
        if after and not after.startswith(('coach:','coach-response:')):return []
        rows = queue.annotation_list(source, publication, clips, after=after[6:] if after.startswith('coach:') else '')
        result = []
        for a in rows:
            provenance = a['provenance']
            current = a['current'];spans = copy.deepcopy(current['applies_to'])
            body = provenance
            if not spans:spans = [dict(start_seconds=body['original_start'], end_seconds=body['original_end'])]
            target = dict(scope='interval',start_seconds=min(s['start_seconds'] for s in spans),
                          end_seconds=max(s['end_seconds'] for s in spans),selectors=spans)
            value = dict(id='coach:'+a['id'],schema_version=1,source_id=source,kind='coach',target=target,
                         body=dict(text=current.get('cue',''),suggestion=current,review_status=a['status'],timing_verified=bool(current['applies_to'])),
                         context=dict(publication=provenance['publication']),origin=dict(type='ai',reference=a['id'],job_id=a['job_id'],provenance=provenance),
                         player_id=(provenance.get('player_context') or {}).get('player_id'),
                         status='archived' if a['status']=='dismissed' else 'active',version=a['version'],
                         updated_at=a['updated'],read_only=True,edit_url='/api/coaching/annotations/'+a['id'])
            value = project(value, data)
            value['playback_eligible'] = value['playback_eligible'] and a['playback_eligible']
            result.append(value)
        # Retain response-level context too: legacy/unstructured responses may have
        # no timed suggestions, and must not disappear or acquire invented timing.
        if not after or after.startswith('coach-response:'):
            cursor=after[len('coach-response:'):] if after else ''
            with queue.db() as db:
                jobs=db.execute("SELECT id,body,result,version,updated FROM jobs WHERE role='ai' AND state='completed' AND json_extract(body,'$.source_id')=? AND id>? ORDER BY id LIMIT 201",(source,cursor)).fetchall()
            for row in jobs:
                body=json.loads(row['body']);answer=json.loads(row['result'])
                target=dict(scope='interval',start_seconds=body['original_start'],end_seconds=body['original_end'])
                value=dict(id='coach-response:'+row['id'],schema_version=1,source_id=source,kind='coach',target=target,
                    body=dict(text=answer.get('answer','')[:12000],response=True,timing_verified=False,text_truncated=len(answer.get('answer',''))>12000),
                    context=dict(publication=body['publication']),origin=dict(type='ai',reference=row['id'],job_id=row['id'],
                    provenance={k:body.get(k) for k in ('clip_id','publication','export_id','player_context')}),
                    player_id=(body.get('player_context') or {}).get('player_id'),status='active',version=row['version'],
                    updated_at=row['updated'],read_only=True,edit_url='/api/coaching/jobs/'+row['id'])
                value=project(value,data);value['playback_eligible']=False;result.append(value)
        return result

    def listing(source, data):
        after=request.args.get('after','')
        return sorted([project(a,data) for a in store.listing(source,after)]+coaches(source,data,after), key=lambda a:a['id'])

    def page(items, default=200, maximum=200):
        try:limit = int(request.args.get('limit',default))
        except ValueError:abort(400,'Invalid page size')
        if not 1 <= limit <= maximum:abort(400,'Invalid page size')
        after = request.args.get('after','')
        if len(after)>100:abort(400,'Invalid cursor')
        items = [a for a in items if a['id']>after]
        return items[:limit], items[limit-1]['id'] if len(items)>limit else None

    @app.errorhandler(AnnotationConflict)
    def conflict(error):
        return jsonify(error=str(error),current=error.current),409

    @app.get('/api/annotations/types')
    def types():return jsonify(schema_version=1,kinds=KINDS,clock='original_seconds',training_label_status='not_released')

    @app.get('/api/annotations/sources/<source>')
    def get_annotations(source):
        check_source(source);data=optional_source(source)
        items, after = page(listing(source,data))
        return jsonify(schema_version=1,annotations=items,next_after=after,source_available=data is not None,
                       publication=data['published_at'] if data else None)

    @app.get('/api/annotations/sources/<source>/export')
    def export(source):
        check_source(source);items, after=page(listing(source,optional_source(source)),25,25)
        for a in items:
            if a['id'].startswith(('coach:','coach-response:')):
                a['revision_source']='/api/annotations/'+a['id']+'/revisions'
                a['evidence_source']='/api/coaching/evidence/'+a['origin']['job_id']
                a['response_source']='/api/coaching/jobs/'+a['origin']['job_id']
            else:a['revision_source']='/api/annotations/'+a['id']+'/revisions'
        return jsonify(schema_version=1,annotations=items,next_after=after,training_label_status='not_released')

    @app.get('/api/annotations/<identity>/revisions')
    def revisions(identity):
        try:after=int(request.args.get('after',0))
        except ValueError:abort(400,'Invalid revision cursor')
        if after<0:abort(400,'Invalid revision cursor')
        if identity.startswith('coach-response:'):
            with queue.db() as db:row=db.execute("SELECT version,updated,result FROM jobs WHERE id=? AND role='ai' AND state='completed'",(identity[len('coach-response:'):],)).fetchone()
            if not row:abort(404,'Coaching response not found')
            values=[dict(version=row['version'],created_at=row['updated'],content=dict(cue=json.loads(row['result']).get('answer','')),status='saved_response',history_complete=False)] if row['version']>after else []
        elif identity.startswith('coach:'):
            with queue.db() as db:
                if not db.execute('SELECT 1 FROM annotations WHERE id=?',(identity[6:],)).fetchone():abort(404,'Coaching suggestion not found')
                rows=db.execute('SELECT version,created,value FROM annotation_revisions WHERE annotation_id=? AND version>? ORDER BY version LIMIT 101',(identity[6:],after)).fetchall()
            values=[dict(version=r['version'],created_at=r['created'],**json.loads(r['value'])) for r in rows]
        else:
            try:store.get(identity)
            except KeyError:abort(404,'Note not found')
            values=store.revisions(identity,after)
        return jsonify(revisions=values[:100],next_after=values[99]['version'] if len(values)>100 else None)

    def player_id(body):
        pid=body.get('player_id')
        if pid is not None and (not isinstance(pid,str) or 'player:'+pid not in app.extensions['state'].all()):
            raise ValueError('Choose a saved player')
        return pid

    @app.post('/api/annotations')
    def create():
        body=request.get_json()
        if not isinstance(body,dict) or set(body)-{'source_id','publication','kind','target','body','player_id','request_id'}:
            abort(400,'Invalid metadata request')
        source=body.get('source_id')
        if not isinstance(source,str):abort(400,'Choose a recording')
        data=session(source);check_publication(body,data)
        kind=body.get('kind')
        if not isinstance(kind,str) or kind not in KINDS or KINDS[kind].get('read_only'):
            abort(400,'Choose a supported note type')
        rid=body.get('request_id')
        if not isinstance(rid,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',rid):
            abort(400,'A unique request ID is required')
        try:
            value=dict(schema_version=1,source_id=source,kind=kind,target=validate_target(kind,body.get('target'),data),
                       body=validate_body(kind,body.get('body')),player_id=player_id(body),context=context(data),
                       origin=dict(type='human'),status='active',training_label_status='not_released')
            return jsonify(project(store.write(value,request_id=rid),data)),201
        except ValueError as error:
            if isinstance(error,AnnotationConflict):raise
            abort(400,str(error))

    @app.put('/api/annotations/<identity>')
    def edit(identity):
        if identity.startswith(('coach:','coach-response:')):
            abort(400,'Use the coaching review editor to edit this suggestion')
        try:current=store.get(identity)
        except KeyError:abort(404,'Note not found')
        body=request.get_json()
        if not isinstance(body,dict) or set(body)-{'publication','version','body','target','status','player_id'}:
            abort(400,'Invalid metadata edit')
        data=session(current['source_id']);check_publication(body,data)
        if type(body.get('version')) is not int or body['version']<1:abort(400,'The current note version is required')
        value=copy.deepcopy(current)
        try:
            if 'body' in body:value['body']=validate_body(value['kind'],body['body'])
            if current['kind']=='cut_feedback' and 'target' in body:
                raise ValueError('Create a new report to review another cut or processed version')
            if 'target' in body:
                value['target']=validate_target(value['kind'],body['target'],data);value['context']=context(data)
            if 'status' in body:
                if body['status'] not in ('active','archived'):raise ValueError('Invalid note status')
                value['status']=body['status']
            if 'player_id' in body:value['player_id']=player_id(body)
            if current['kind']=='usefulness' and value['target'] != current['target']:
                raise ValueError('Create a separate assessment for a different cut')
            return jsonify(project(store.write(value,expected=body['version']),data))
        except ValueError as error:
            if isinstance(error,AnnotationConflict):raise
            abort(400,str(error))
