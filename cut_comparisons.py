"""Retained comparison evidence; the processing server owns media pins/lifecycle."""
import json
import re
import time
import uuid
from flask import abort,jsonify,request,send_file
from entity_model import output_version,canonical_manifest

OPAQUE=re.compile(r'[a-f0-9]{32}')


def register(app,session):
    catalog=app.extensions['entity_catalog'];library=app.extensions['library']
    with catalog.db() as db:db.execute('''CREATE TABLE IF NOT EXISTS version_comparisons(id TEXT PRIMARY KEY,video_id TEXT NOT NULL,body TEXT NOT NULL,version INTEGER NOT NULL,created REAL NOT NULL,updated REAL NOT NULL)''')
    def client():return app.extensions['processing_client']
    def manifest(source,version):
        if not isinstance(version,str) or not re.fullmatch('v_[a-f0-9]{32}',version):abort(400,'Invalid processed version')
        result=catalog.version_manifest(source,version)
        if not result:abort(404,'Processed version not found')
        return result
    def server_versions(source):
        result=client().call('/videos/'+source+'/versions')
        if not isinstance(result,dict) or not isinstance(result.get('items'),list):abort(503,'Invalid processing catalog')
        return result['items']
    def verified_version(source,version):
        value=next((v for v in server_versions(source) if v.get('id')==version),None)
        if not value or canonical_manifest(value.get('manifest',{}))!=canonical_manifest(manifest(source,version)):abort(409,'Version has no verified processing history')
        return value
    def pin(source,version,holder,kind,ttl=None):
        data=verified_version(source,version)
        if not OPAQUE.fullmatch(str(data.get('attempt_id',''))):abort(409,'No verified processing attempt')
        result=client().call('/attempts/'+data['attempt_id']+'/pins',dict(holder_kind=kind,holder_id=holder,ttl_seconds=ttl))
        if not isinstance(result,dict) or result.get('media_state') not in ('available','staged_candidate'):abort(409,'Retained media is unavailable')
        return dict(data,media_state=result['media_state'])

    @app.post('/api/processing/videos/<source>/versions/import')
    def import_versions(source):
        current=session(source);catalog.sync_serving_video(current);imported=[]
        for value in server_versions(source):
            data=value.get('manifest');attempt=value.get('attempt_id')
            if not isinstance(data,dict) or data.get('id')!=source or value.get('source_sha256')!=source or not OPAQUE.fullmatch(str(attempt)):abort(503,'Invalid processing catalog identity')
            if data.get('source',{}).get('sha256')!=source or data['source'].get('duration_seconds')!=current['source']['duration_seconds']:abort(409,'Original recording metadata mismatch')
            if (data.get('processing') or {}).get('attempt_id')!=attempt or data.get('media_directory')!=attempt:abort(409,'Unverified candidate publication')
            if data.get('clips'):library.validate(dict(data,schema_version=1),source)
            elif data.get('video',{}).get('duration_seconds')!=0 or data['video'].get('file')!='Practice.mp4':abort(400,'Invalid empty candidate')
            if value.get('id')!=output_version(source,data['published_at']):abort(409,'Version identity mismatch')
            try:catalog.admit_version(data)
            except ValueError as error:abort(409,str(error))
            imported.append(value['id'])
        return jsonify(imported=imported,current_version_id=output_version(source,current['published_at']))

    @app.post('/api/processing/videos/<source>/comparison-lease')
    def lease(source):
        body=request.get_json();holder=body.get('holder_id','') if isinstance(body,dict) else ''
        if not OPAQUE.fullmatch(holder):abort(400,'A tab lease ID is required')
        data=pin(source,body.get('video_version_id'),holder,'comparison_session',1800)
        return jsonify(acknowledged=True,media_state=data.get('media_state'),lease_seconds=1800)

    @app.delete('/api/processing/videos/<source>/comparison-lease')
    def release_lease(source):
        body=request.get_json();holder=body.get('holder_id','') if isinstance(body,dict) else ''
        if not OPAQUE.fullmatch(holder):abort(400,'A tab lease ID is required')
        data=verified_version(source,body.get('video_version_id'))
        client().call('/attempts/'+data['attempt_id']+'/pins',dict(holder_kind='comparison_session',holder_id=holder),method='DELETE')
        return jsonify(released=True)

    @app.get('/media/versions/<version>/<path:filename>')
    def version_media(version,filename):
        with catalog.db() as db:row=db.execute('SELECT video_id FROM video_versions WHERE id=?',(version,)).fetchone()
        if not row:abort(404)
        source=row['video_id'];data=manifest(source,version);current=session(source)
        if version!=output_version(source,current['published_at']):
            holder=request.args.get('lease_id','')
            if not OPAQUE.fullmatch(holder):abort(409,'Open a comparison to retain this media')
            pin(source,version,holder,'comparison_session',1800)
        try:path=library.media(data,filename)
        except (ValueError,KeyError):abort(404)
        if not path.is_file():abort(404,'Retained media is unavailable')
        return send_file(path,conditional=True,mimetype='image/jpeg' if filename.endswith('.jpg') else 'video/mp4',max_age=0)

    @app.get('/api/processing/videos/<source>/comparisons')
    def listing(source):
        session(source)
        with catalog.db() as db:rows=db.execute('SELECT * FROM version_comparisons WHERE video_id=? ORDER BY updated DESC LIMIT 100',(source,)).fetchall()
        return jsonify(items=[dict(id=r['id'],version=r['version'],created_at=r['created'],updated_at=r['updated'],**json.loads(r['body'])) for r in rows])

    @app.put('/api/processing/videos/<source>/comparisons/<identity>')
    def save(source,identity):
        session(source)
        if not re.fullmatch('cmp_[a-f0-9]{32}',identity):abort(400,'Invalid comparison ID')
        body=request.get_json()
        if not isinstance(body,dict) or set(body)-{'version_a_id','version_b_id','preferred_version_id','note','retain_versions','version'}:abort(400,'Invalid comparison')
        a,b=body.get('version_a_id'),body.get('version_b_id');left,right=manifest(source,a),manifest(source,b)
        if a==b or left['source']['duration_seconds']!=right['source']['duration_seconds']:abort(400,'Select two versions of the same recording')
        if body.get('preferred_version_id') not in (None,a,b):abort(400,'Invalid preferred version')
        note=body.get('note','');retain=body.get('retain_versions',[])
        if not isinstance(note,str) or len(note)>2000 or not isinstance(retain,list) or len(retain)>2 or set(retain)-{a,b}:abort(400,'Invalid comparison metadata')
        if type(body.get('version')) is not int or body['version']<0:abort(400,'A current comparison revision is required')
        def check_revision(old):
            if (old and (old['video_id']!=source or old['version']!=body['version'])) or (not old and body['version']!=0):abort(409,'Comparison changed; reload before saving')
        with catalog.db() as db:old=db.execute('SELECT * FROM version_comparisons WHERE id=?',(identity,)).fetchone()
        check_revision(old)
        holder=identity+'_'+uuid.uuid4().hex
        acquired=[]
        try:
            for version in retain:
                pin(source,version,holder,'saved_comparison');acquired.append(version)
            saved=dict(version_a_id=a,version_b_id=b,preferred_version_id=body.get('preferred_version_id'),note=note.strip(),retain_versions=retain,
                       pin_holders={v:holder for v in retain},retain_pin_acknowledged=bool(retain),training_label_status='not_released')
            with catalog.db() as db:
                db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT * FROM version_comparisons WHERE id=?',(identity,)).fetchone();check_revision(old)
                previous=json.loads(old['body']) if old else {}
                now=time.time();revision=body['version']+1
                db.execute('INSERT INTO version_comparisons VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,version=excluded.version,updated=excluded.updated',(identity,source,json.dumps(saved),revision,old['created'] if old else now,now));db.commit()
        except Exception:
            for version in acquired:
                try:
                    data=verified_version(source,version);client().call('/attempts/'+data['attempt_id']+'/pins',dict(holder_kind='saved_comparison',holder_id=holder),method='DELETE')
                except Exception:pass  # Failed release can retain extra media, never remove another revision's pin.
            raise
        pending=[]
        for version in previous.get('retain_versions',[]):
            try:
                data=verified_version(source,version);old_holder=previous.get('pin_holders',{}).get(version,identity)
                client().call('/attempts/'+data['attempt_id']+'/pins',dict(holder_kind='saved_comparison',holder_id=old_holder),method='DELETE')
            except Exception:pending.append(version)
        return jsonify(id=identity,version=revision,release_pending=pending,**saved)
