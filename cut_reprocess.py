"""Explicit evidence selection, preview, and server-owned rerun submission."""
import re
import json
from flask import abort,jsonify,request
from entity_model import output_version,canonical_manifest
from processing_bridge import ProcessingClient,ProcessingUnavailable,ProcessingRejected,version_catalog


def register(app,session):
    app.extensions['processing_client']=ProcessingClient(app.config.get('PROCESSING_API_URL'))
    catalog=app.extensions['entity_catalog'];annotations=app.extensions['annotations']

    @app.errorhandler(ProcessingUnavailable)
    def unavailable(error):return jsonify(error=str(error)),503
    @app.errorhandler(ProcessingRejected)
    def rejected(error):
        value=error.body.get('error') if isinstance(error.body,dict) else None
        code=value.get('code') if isinstance(value,dict) else value
        if not isinstance(code,str) or len(code)>120:code='processing_request_rejected'
        return jsonify(error=code.replace('_',' ').capitalize(),code=code),error.status if error.status in (400,404,409,422) else 503

    def selection(source,body):
        if not isinstance(body,dict):abort(400,'Choose review revisions')
        current=session(source)
        try:catalog.sync_serving_video(current)
        except ValueError as e:abort(409,str(e))
        manifest=catalog.version_manifest(source,body.get('video_version_id',''))
        if not manifest:abort(404,'Reviewed version not found')
        refs=body.get('evidence_revisions')
        if not isinstance(refs,list) or not 1<=len(refs)<=50:abort(400,'Select between 1 and 50 reviews')
        evidence=[];seen=set()
        for ref in refs:
            if not isinstance(ref,dict) or set(ref)!={'annotation_id','version'} or not isinstance(ref['annotation_id'],str) or type(ref['version']) is not int:abort(400,'Invalid review revision')
            if ref['annotation_id'] in seen:abort(400,'Duplicate review selection')
            seen.add(ref['annotation_id'])
            try:record=annotations.get(ref['annotation_id'])
            except KeyError:abort(409,'A selected review is no longer available')
            if record['version']!=ref['version'] or record['status']!='active':abort(409,'A selected review changed; refresh the preview')
            if record['source_id']!=source or record['kind']!='cut_feedback' or record['context']['publication']!=manifest['published_at']:abort(409,'Review belongs to another output')
            evidence.append(dict(annotation_id=record['id'],**{k:record.get(k) for k in ('version','created_at','updated_at','kind','target','body','context')}))
        return manifest,evidence

    @app.post('/api/processing/videos/<source>/reprocess-preview')
    def preview(source):
        body=request.get_json();manifest,evidence=selection(source,body)
        return jsonify(video_version_id=output_version(source,manifest['published_at']),manifest=manifest,evidence=evidence,
                       server_configured=bool(app.config.get('PROCESSING_API_URL')),requires_confirmation=True)

    @app.post('/api/processing/videos/<source>/reprocess')
    def submit(source):
        body=request.get_json();manifest,evidence=selection(source,body)
        rid=body.get('request_id','')
        if not isinstance(rid,str) or not re.fullmatch('[A-Za-z0-9_-]{8,100}',rid):abort(400,'A stable request ID is required')
        commands=body.get('confirmed_commands')
        if not isinstance(commands,dict):abort(400,'Confirm exact commands before creating a task')
        processing=manifest.get('processing',{})
        if not processing.get('attempt_id'):abort(409,'This output has no verified processing attempt. Import its history in the processing portal first.')
        client=app.extensions['processing_client']
        versions=version_catalog(client,source,body['video_version_id'])
        verified=next((v for v in versions if v.get('id')==body['video_version_id'] and v.get('attempt_id')==processing['attempt_id']),None)
        if not verified or canonical_manifest(verified['manifest'])!=canonical_manifest(manifest):abort(409,'Processing history does not match this version')
        snapshot=dict(schema_version=1,source_sha256=source,base_attempt_id=processing['attempt_id'],base_video_version_id=body['video_version_id'],
                      selected_evidence_revisions=evidence,confirmed_commands=commands,parameter_overrides=body.get('parameter_overrides',{}),compiler_version='cut-constraints-compiler-v1')
        if len(json.dumps(snapshot,separators=(',',':'),allow_nan=False).encode())>128*1024:abort(400,'Selected feedback exceeds 128 KiB; select fewer reviews')
        created=client.call('/feedback-snapshots',snapshot,key=rid+'-snapshot')
        task=client.call('/tasks/'+verified['task_id']+'/rerun',dict(feedback_snapshot_id=created['snapshot_id'],publication_policy='never'),key=rid+'-rerun')
        return jsonify(task=task,snapshot_id=created['snapshot_id'],publication_policy='never'),201
