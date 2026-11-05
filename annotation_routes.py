"""Common metadata API and a read adapter for retained coaching suggestions."""
import copy
import json
from pathlib import Path
import re
from flask import abort, jsonify, request
from annotations import finite, AnnotationStore, AnnotationConflict, KINDS, SOURCE_ID, canonical, project, validate_body, validate_target
from players import decorate_players
from entity_model import classify, moment_view, subject, output_version


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

    @app.get('/api/recordings/<source>/metadata')
    @app.get('/api/annotations/sources/<source>')
    def get_annotations(source):
        check_source(source);data=optional_source(source)
        items, after = page(listing(source,data))
        return jsonify(schema_version=1,annotations=items,next_after=after,source_available=data is not None,
                       publication=data['published_at'] if data else None)

    @app.get('/api/recordings/<source>/moments')
    def moments(source):
        check_source(source)
        items,after=page([a for a in listing(source,optional_source(source)) if a['record_class']=='moment'])
        return jsonify(schema_version=2,moments=[moment_view(a) for a in items],next_after=after,clock='original_milliseconds')

    @app.get('/api/processing-annotations/sources/<source>')
    def processing_annotations(source):
        check_source(source)
        items,after=page([a for a in listing(source,optional_source(source)) if a['record_class']=='processing_annotation'])
        return jsonify(schema_version=2,annotations=items,next_after=after,training_label_status='not_released')

    @app.get('/api/entities/videos/<source>/metadata')
    def entity_metadata(source):
        check_source(source)
        data=optional_source(source)
        # Full version histories are available even after the published manifest
        # disappears; no historical field is implicitly carried into a new cut.
        values=[];cursor=''
        while True:
            batch=store.listing(source,after=cursor,limit=200)
            values.extend(classify(a) for a in batch if classify(a)['record_class']=='entity_metadata')
            if len(batch)<200:break
            cursor=batch[-1]['id']
        requested=request.args.get('video_version_id')
        current=output_version(source,data['published_at']) if data else None
        version=requested or current
        documents={}
        for value in values:
            ref=value['subject']
            if ref['type']!='video' and ref['video_version_id']!=version:continue
            key=ref['id']
            document=documents.setdefault(key,dict(subject=ref,notes=[],less_useful=False,usefulness_reason='',field_versions={}))
            field='notes' if value['kind']=='note' else 'less_useful'
            document['field_versions'][value['id']]=value['version']
            if value['status']!='active':continue
            if field=='notes':document['notes'].append(dict(key=value['id'],text=value['body']['text'],player_id=value.get('player_id')))
            else:document.update(less_useful=True,usefulness_reason=value['body'].get('reason',''))
        return jsonify(schema_version=2,video_id=source,video_version_id=version,metadata=list(documents.values()))

    @app.post('/api/moments')
    def create_moment():
        body=request.get_json()
        if not isinstance(body,dict) or set(body)-{'original_recording_id','publication','type','start_ms','end_ms','payload','player_id','request_id'}:
            abort(400,'Invalid moment')
        kind={'highlight':'highlight','comment':'note'}.get(body.get('type')) if isinstance(body.get('type'),str) else None
        if not kind:abort(400,'Choose a supported moment type; coaching uses its review editor')
        start,end=body.get('start_ms'),body.get('end_ms')
        if type(start) is not int or start<0 or ('end_ms' in body and (type(end) is not int or end<=start)):
            abort(400,'Use original recording milliseconds and an optional later end')
        target=dict(scope='point' if end is None else 'interval',start_seconds=start/1000)
        if end is not None:target['end_seconds']=end/1000
        value=create_record(dict(source_id=body.get('original_recording_id'),publication=body.get('publication'),
            kind=kind,target=target,body=body.get('payload'),player_id=body.get('player_id'),request_id=body.get('request_id')))
        return jsonify(moment_view(value)),201

    @app.get('/api/learning')
    def learning():
        pid=request.args.get('player_id','')
        if not re.fullmatch('[a-f0-9]{32}',pid) or 'player:'+pid not in app.extensions['state'].all():
            abort(400,'Choose a saved player')
        after=request.args.get('after','')
        if len(after)>100:abort(400,'Invalid review cursor')
        try:limit=int(request.args.get('limit',30))
        except ValueError:abort(400,'Invalid page size')
        if not 1<=limit<=50:abort(400,'Invalid page size')
        with queue.db() as db:
            rows=db.execute("SELECT id,body,result,updated FROM jobs WHERE role='ai' AND state='completed' AND id>? AND (json_extract(body,'$.player_context.player_id')=? OR json_extract(body,'$.player_context.player_id') IS NULL) ORDER BY id LIMIT ?",(after,pid,limit+1)).fetchall()
        entries={};sources={}
        for row in rows[:limit]:
            body=json.loads(row['body']);result=json.loads(row['result']);sid=body['source_id']
            if sid not in sources:sources[sid]=optional_source(sid)
            data=sources[sid]
            if data:
                edits=app.extensions['state'].all().get('meta:'+sid,{}).get('value') or {}
                data['title']=edits.get('title',data['title'])
            profile=body.get('player_context') or {};explicit=profile.get('player_id')==pid
            start,end=body['original_start'],body['original_end']
            clips=[c for c in data['clips'] if max(start,c['original_start_seconds'])<min(end,c['original_end_seconds'])] if data else []
            # Never attribute an unlabelled legacy review from a recording roster:
            # only an explicitly assigned main player can supply that association.
            if not explicit:clips=[c for c in clips if (c.get('main_player') or {}).get('id')==pid]
            if not clips and not explicit:continue
            for clip in clips or [None]:
                cid=clip['id'] if clip else body.get('clip_id','unavailable')
                key=sid+':'+cid
                historical=data is None or body['publication']!=data['published_at']
                mismatch=bool(clip and explicit and (clip.get('main_player') or {}).get('id')!=pid)
                entry=entries.setdefault(key,dict(id=key,source_id=sid,clip_id=cid,clip_index=clip['index'] if clip else None,
                    title=data['title'] if data else 'Recording unavailable',recorded_at=data.get('source',{}).get('recorded_at') if data else None,
                    original_start=clip['original_start_seconds'] if clip else start,original_end=clip['original_end_seconds'] if clip else end,
                    duration_seconds=clip['duration_seconds'] if clip else end-start,media_available=clip is not None,
                    poster_url=f'/api/sessions/{sid}/clips/{cid}/poster?v={data["published_at"]}' if clip else None,reviews=[]))
                entry['reviews'].append(dict(id=row['id'],original_start=start,original_end=end,updated_at=row['updated'],
                    summary=(result.get('structured') or {}).get('summary','') or result.get('answer','')[:400],
                    answer=result.get('answer','')[:12000],answer_truncated=len(result.get('answer',''))>12000,
                    historical=historical,player_mismatch=mismatch,association='review' if explicit else 'current_clip',
                    response_url='/api/coaching/jobs/'+row['id']))
        return jsonify(entries=list(entries.values()),next_after=rows[limit-1]['id'] if len(rows)>limit else None)

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

    @app.post('/api/metadata')
    @app.post('/api/annotations')
    def create():
        return jsonify(create_record(request.get_json())),201

    def create_record(body):
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
            return project(store.write(value,request_id=rid),data)
        except ValueError as error:
            if isinstance(error,AnnotationConflict):raise
            abort(400,str(error))

    @app.put('/api/metadata/<identity>')
    @app.put('/api/annotations/<identity>')
    def edit(identity):
        return edit_record(identity,request.get_json())

    def edit_record(identity,body):
        if identity.startswith(('coach:','coach-response:')):
            abort(400,'Use the coaching review editor to edit this suggestion')
        try:current=store.get(identity)
        except KeyError:abort(404,'Note not found')
        if not isinstance(body,dict) or set(body)-{'publication','version','body','target','status','player_id'}:
            abort(400,'Invalid metadata edit')
        data=session(current['source_id']);check_publication(body,data)
        if type(body.get('version')) is not int or body['version']<1:abort(400,'The current note version is required')
        value=copy.deepcopy(current)
        try:
            if 'body' in body:value['body']=validate_body(value['kind'],body['body'])
            if current['kind'] in ('cut_feedback','processing_label') and 'target' in body:
                raise ValueError('Create a new report to review another cut or processed version')
            if 'target' in body:
                value['target']=validate_target(value['kind'],body['target'],data);value['context']=context(data)
            if 'status' in body:
                if body['status'] not in ('active','archived'):raise ValueError('Invalid note status')
                value['status']=body['status']
            if 'player_id' in body:value['player_id']=player_id(body)
            if current['kind']=='usefulness' and value['target'] != current['target']:
                raise ValueError('Create a separate assessment for a different cut')
            value['change_origin']='human'
            if value['status']=='active':value.pop('archive_reason',None)
            return jsonify(project(store.write(value,expected=body['version']),data))
        except ValueError as error:
            if isinstance(error,AnnotationConflict):raise
            abort(400,str(error))

    @app.put('/api/moments/<identity>')
    def edit_moment(identity):
        try:current=store.get(identity)
        except KeyError:abort(404,'Moment not found')
        if classify(current)['record_class']!='moment':abort(404,'Moment not found')
        body=request.get_json()
        if not isinstance(body,dict) or set(body)-{'publication','version','payload','start_ms','end_ms','status','player_id'}:abort(400,'Invalid moment edit')
        changes={k:body[k] for k in ('publication','version','status','player_id') if k in body}
        if 'payload' in body:changes['body']=body['payload']
        if 'start_ms' in body or 'end_ms' in body:
            start=body.get('start_ms');end=body.get('end_ms')
            if type(start) is not int or start<0 or ('end_ms' in body and (type(end) is not int or end<=start)):abort(400,'Invalid moment timing')
            changes['target']=dict(scope='point' if end is None else 'interval',start_seconds=start/1000)
            if end is not None:changes['target']['end_seconds']=end/1000
        response=edit_record(identity,changes)
        return jsonify(moment_view(response.get_json()))

    @app.post('/api/processing-annotations')
    def create_processing_annotation():
        body=request.get_json()
        if not isinstance(body,dict) or body.get('kind') not in ('cut_feedback','processing_label'):abort(400,'Choose a supported processing annotation')
        return jsonify(create_record(body)),201

    @app.put('/api/processing-annotations/<identity>')
    def edit_processing_annotation(identity):
        try:value=store.get(identity)
        except KeyError:abort(404,'Processing annotation not found')
        if classify(value)['record_class']!='processing_annotation':abort(404,'Processing annotation not found')
        return edit_record(identity,request.get_json())

    @app.put('/api/entities/videos/<source>/metadata')
    def set_entity_field(source):
        """Edit one field/notes entry; record IDs are internal revision keys."""
        body=request.get_json()
        if not isinstance(body,dict) or set(body)-{'publication','subject_type','clip_id','field','value','version','key'}:
            abort(400,'Invalid entity metadata')
        data=session(source);check_publication(body,data)
        scope=body.get('subject_type');field=body.get('field')
        if scope not in ('video','video_version','clip') or field not in ('note','less_useful'):abort(400,'Choose an editable metadata field')
        if field=='less_useful' and scope=='video':abort(400,'Usefulness belongs to a specific processed version')
        if field=='note' and scope=='video_version':abort(400,'General video notes belong to the logical video')
        if type(body.get('version')) is not int or body['version']<0:abort(400,'A current field version is required')
        value=body.get('value')
        if field=='less_useful' and type(value) is not bool:abort(400,'Use a boolean usefulness value')
        if field=='note' and (not isinstance(value,str) or len(value)>2000):abort(400,'Use a note up to 2000 characters')
        target=dict(scope='video')
        if scope=='clip':
            clip=next((c for c in data['clips'] if c['id']==body.get('clip_id')),None)
            if not clip:abort(404,'Clip not found')
            target=dict(scope='clip',clip_id=clip['id'],start_seconds=clip['original_start_seconds'],end_seconds=clip['original_end_seconds'])
        kind='note' if field=='note' else 'usefulness'
        proposed=dict(schema_version=2,source_id=source,kind=kind,target=target,
            body=dict(text=value.strip()) if field=='note' else dict(value='less_useful',text='',reason=''),
            context=context(data),origin=dict(type='human'),status='active' if value else 'archived',player_id=None)
        desired=subject(proposed);existing=None
        if 'key' in body and (not isinstance(body['key'],str) or len(body['key'])>100):abort(400,'Invalid metadata field key')
        if body.get('key'):
            try:existing=store.get(body['key'])
            except KeyError:abort(404,'Metadata field not found')
            if existing['kind']!=kind or classify(existing).get('subject')!=desired:abort(409,'Metadata belongs to another entity or output version')
        elif field=='less_useful':
            cursor=''
            while True:
                batch=store.listing(source,after=cursor,limit=200)
                existing=next((a for a in batch if a['kind']=='usefulness' and subject(a)==desired),None)
                if existing or len(batch)<200:break
                cursor=batch[-1]['id']
        if existing:
            proposed={**existing,**proposed,'id':existing['id']}
            # Preserve optional reasons and context while toggling a field.
            if field=='less_useful':proposed['body']=existing['body']
        try:
            saved=store.write(proposed,expected=body['version'] or None)
            return jsonify(subject=desired,field=field,value=value,key=saved['id'],version=saved['version'])
        except AnnotationConflict:raise
