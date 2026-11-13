"""LAN UI APIs and fenced worker protocol; large artifacts stay on shared storage."""
import hashlib
import hmac
import json
from pathlib import Path
import re
from flask import Blueprint, abort, jsonify, request, send_file
from coaching import Coaching, QueueConflict, recipe
from storage import inside
from players import coaching_player, resolve_coaching_subject
from worker.tennis_coach.annotations import CAPABILITY, PROMPT_VERSION, expected_frames, evidence_manifest, coaching_prompt, response_schema


def register_coaching(app,library,session):
    routes=Blueprint('coaching',__name__)
    queue=Coaching(Path(app.config['DATA_ROOT'])/'coaching.sqlite3')
    queue.player_exists=lambda pid:isinstance(pid,str) and bool((app.extensions['state'].all().get('player:'+pid) or {}).get('value'))
    app.extensions['coaching']=queue
    root=Path(app.config['COACH_MEDIA_ROOT']).resolve()

    def worker():
        expected=app.config['COACH_WORKER_TOKEN']
        if not expected or not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+expected):
            abort(403,'Worker is not configured or its credential is invalid')

    def get(identity):
        try:return queue.get(identity)
        except KeyError:abort(404,'Coaching request not found')

    @app.errorhandler(QueueConflict)
    def conflict(e):return jsonify(error=str(e)),409

    @routes.get('/api/coaching/workers')
    def list_workers():
        return jsonify(queue.worker_overview(enabled=bool(app.config['COACH_WORKER_TOKEN'])))

    @routes.get('/api/coaching/config')
    def get_config():
        return jsonify(queue.ai_config())

    @routes.put('/api/coaching/config')
    def put_config():
        b=request.get_json()
        if not isinstance(b,dict) or not set(b)<= {'selection','version'}:
            abort(400,'Expected a selection and version')
        try:return jsonify(queue.update_ai_config(b.get('selection'),b.get('version')))
        except QueueConflict as e:return jsonify(error=str(e),current=queue.ai_config()),409
        except ValueError as e:abort(400,str(e))

    @routes.get('/api/coaching/sessions/<identity>')
    def list_coaching(identity):
        data=session(identity)
        return jsonify(**queue.listing(identity),annotations=queue.annotation_list(identity,data['published_at'],data['clips']),enabled=bool(app.config['COACH_WORKER_TOKEN']))

    @routes.post('/api/coaching/exports')
    def export():
        if not app.config['COACH_WORKER_TOKEN']:abort(503,'Coaching workers are not configured')
        b=request.get_json()
        if not isinstance(b,dict):abort(400,'Expected an object')
        try:
            data=session(b['source_id'])
            if b.get('publication')!=data['published_at']:abort(409,'Recording changed. Refresh before exporting')
            clip=next(c for c in data['clips'] if c['id']==b.get('clip_id'))
            spec=recipe(b,data,clip)
            has_subject_fields=any(k in b for k in ('subject_mode','player_id','player_profile_version','subject_hint'))
            if spec.get('intent')=='share' and has_subject_fields:
                raise ValueError('Share downloads do not accept coaching player subjects')
            if spec.get('auto_detect') or has_subject_fields:
                subject_mode,context,subject_hint=resolve_coaching_subject(b,data,clip['id'],app.extensions['state'].all(),conflict_cls=QueueConflict)
                if spec.get('auto_detect'):
                    spec['player_context']=context
                    if subject_mode is not None:
                        spec['subject_mode']=subject_mode
                    if subject_hint:
                        spec['subject_hint']=subject_hint
            return jsonify(queue.create('render',spec,b.get('request_id'))),201
        except QueueConflict:raise
        except (ValueError,TypeError,KeyError,StopIteration) as e:abort(400,str(e) or 'Invalid selection')

    @routes.post('/api/coaching/requests')
    def ask():
        if not app.config['COACH_WORKER_TOKEN']:abort(503,'Coaching workers are not configured')
        b=request.get_json()
        if not isinstance(b,dict):abort(400,'Expected an object')
        render=get(b.get('export_id',''))
        prompt=b.get('prompt')
        if render['role']!='render' or render['state']!='completed':abort(409,'Prepare an excerpt first')
        if render['body'].get('intent')=='share':abort(400,'Share downloads cannot be used as coaching evidence')
        if not isinstance(prompt,str) or not 1<=len(prompt.strip())<=4000:abort(400,'Enter a question up to 4000 characters')
        if b.get('consent') is not True:abort(400,'Confirm sending selected frames and your question to OpenAI')
        spec=render['body'];data=session(spec['source_id'])
        if spec['publication']!=data['published_at']:abort(409,'Recording changed; prepare a fresh excerpt')
        parent_id=b.get('follow_up_of')
        raw_auto=b.get('auto_detect',bool(spec.get('auto_detect')) and parent_id is None)
        if type(raw_auto) is not bool:abort(400,'Invalid auto_detect flag')
        if spec.get('auto_detect') and not raw_auto and parent_id is None:
            abort(400,'Automatic coaching exports must be submitted as automatic coaching requests')
        auto_slot=b.get('auto_slot',spec.get('auto_slot'))
        if auto_slot is not None and (type(auto_slot) is not int or auto_slot not in (1,2)):
            abort(400,'Invalid auto coach slot')
        body=dict(spec,export_id=render['id'],artifact_directory=render['result']['artifact_directory'],prompt=prompt.strip(),provider='chatgpt-codex',consent=True)
        for inherited in ('auto_detect','auto_slot','subject_mode','subject_hint','player_context'):
            body.pop(inherited,None)
        try:
            req_input=dict(b)
            if raw_auto:
                if 'subject_mode' not in req_input and 'subject_mode' in spec:
                    req_input['subject_mode']=spec['subject_mode']
                if 'subject_hint' not in req_input and 'subject_hint' in spec:
                    req_input['subject_hint']=spec['subject_hint']
            subject_mode,context,subject_hint=resolve_coaching_subject(req_input,data,spec['clip_id'],app.extensions['state'].all(),conflict_cls=QueueConflict)
            body['player_context']=context
            if subject_mode is not None:
                body['subject_mode']=subject_mode
            if subject_hint:
                body['subject_hint']=subject_hint
            if 'player_context' in spec:
                if spec.get('player_context')!=context:
                    raise QueueConflict('Selected player profile changed. Refresh the review before asking.' if (spec.get('subject_mode') or subject_mode)=='selected_player' else 'Main player or profile changed. Refresh the review before asking.')
                if 'subject_mode' in spec and spec.get('subject_mode')!=subject_mode:
                    raise QueueConflict('Coaching subject changed. Refresh the review before asking.')
                if parent_id is None and spec.get('subject_hint')!=subject_hint:
                    raise QueueConflict('Coaching subject changed. Refresh the review before asking.')
            if raw_auto:
                if parent_id is not None:
                    raise ValueError('Automatic coaching requests cannot be follow-ups')
                body['auto_detect']=True
                if auto_slot is not None:
                    body['auto_slot']=auto_slot
            elif parent_id is not None:
                if not isinstance(parent_id,str):raise ValueError('Invalid previous coaching response')
                parent=get(parent_id)
                if parent['role']!='ai' or parent['state']!='completed' or parent['body'].get('export_id')!=render['id'] or parent['body'].get('publication')!=spec['publication']:
                    raise ValueError('Follow-ups must refer to a completed response for this excerpt')
                parent_eff_mode=parent['body'].get('subject_mode') or ('main_player' if parent['body'].get('player_context') else 'none')
                curr_eff_mode=subject_mode or ('main_player' if body.get('player_context') else 'none')
                if parent['body'].get('player_context') != body['player_context'] or parent_eff_mode != curr_eff_mode:
                    raise ValueError('Selected player or profile changed. Start a new review for this excerpt.' if 'selected_player' in (parent_eff_mode, curr_eff_mode) else 'Main player or profile changed. Start a new review for this excerpt.')
                answer=parent['result']['answer']
                body.update(follow_up_of=parent_id,previous_question=parent['body']['prompt'],previous_answer=answer[:12000],previous_answer_truncated=len(answer)>12000)
            body.update(evidence_frames=expected_frames(render),required_capability=CAPABILITY,prompt_version=PROMPT_VERSION,source_sha256=data.get('source',{}).get('sha256',data['id']),processing=data.get('processing'))
            body.update(submitted_prompt=coaching_prompt(body,{'frames':body['evidence_frames']}),response_contract=response_schema())
            return jsonify(queue.create('ai',body,b.get('request_id'))),201
        except QueueConflict:raise
        except ValueError as e:abort(400,str(e))

    @routes.get('/api/coaching/jobs/<identity>')
    def get_job(identity):return jsonify(get(identity))

    @routes.put('/api/coaching/jobs/<identity>')
    def edit_job(identity):
        b=request.get_json()
        if not isinstance(b,dict):abort(400,'Expected an object')
        try:return jsonify(queue.edit(identity,b.get('version'),b.get('action'),b.get('cue')))
        except KeyError:abort(404,'Coaching request not found')
        except QueueConflict:raise
        except ValueError as e:abort(400,str(e))

    @routes.get('/api/coaching/exports/<identity>/<name>')
    def artifact(identity,name):
        job=get(identity)
        is_share=job['body'].get('intent')=='share'
        allowed=('excerpt.mp4','recipe.json') if is_share else ('excerpt.mp4','recipe.json','prompt.txt')
        if job['role']!='render' or job['state']!='completed' or name not in allowed:
            abort(404,'Export unavailable')
        try:
            path=inside(root,identity+'/'+job['result']['artifact_directory']+'/'+name)
            if not path.is_file():abort(410,'Preview expired. Prepare it again; saved advice is retained')
            if name=='recipe.json':
                return jsonify(recipe=job['body'],result=job['result'])
            prefix=f"{job['body'].get('clip_id','clip')}-interval-" if is_share else 'coaching-'
            return send_file(path,conditional=True,as_attachment=request.args.get('download')=='1',download_name=prefix+name)
        except ValueError:abort(404)

    @routes.route('/api/coaching/worker/config', methods=['GET', 'POST'])
    def worker_config():
        worker()
        if request.method == 'POST':
            b = request.get_json()
            if not isinstance(b, dict):
                abort(400, 'Expected an object')
            if b.get('worker_id') and b.get('role'):
                try:
                    queue.touch_worker_meta(b.get('role'), b.get('worker_id'), b.get('meta'))
                except ValueError as e:
                    abort(400, str(e))
        return jsonify(ai_config=queue.worker_ai_config())

    @routes.post('/api/coaching/worker/<role>/claim')
    def claim(role):
        worker();b=request.get_json()
        if not isinstance(b,dict):abort(400,'Expected an object')
        try:
            claimed = queue.claim(role,b.get('worker_id'),b.get('capabilities'),b.get('meta'))
            return jsonify(job=claimed, config_version=queue.worker_ai_config()['version'])
        except QueueConflict:raise
        except ValueError as e:abort(400,str(e))

    @routes.post('/api/coaching/worker/jobs/<identity>/<action>')
    def update(identity,action):
        worker();b=request.get_json()
        if not isinstance(b,dict):abort(400,'Expected an object')
        job=get(identity);result=b.get('result')
        if action=='complete' and job['state']=='completed':
            updated = queue.worker_update(identity,b.get('token'),action,result,b.get('meta'))
            return jsonify(**updated, config_version=queue.worker_ai_config()['version'])
        if action=='evidence':
            if job['body'].get('purpose')=='learning_plan':abort(400,'Learning plan requests do not use frame evidence manifests')
            try:
                if result!=evidence_manifest(job):raise ValueError('Evidence manifest mismatch')
                for frame in result['frames']:
                    path=inside(root,'evidence/frames/'+frame['sha256']+'.jpg')
                    if not path.is_file() or not 0<path.stat().st_size<=4*1024*1024:raise ValueError('Missing or oversized evidence image')
                    if hashlib.sha256(path.read_bytes()).hexdigest()!=frame['sha256']:raise ValueError('Evidence checksum mismatch')
            except (ValueError,OSError,KeyError,TypeError) as e:abort(400,str(e))
        if action=='complete':
            if job['role']=='render':
                # Worker writes once to its fenced directory. The NAS only inspects small metadata.
                try:
                    fence=hashlib.sha256(str(b.get('token')).encode()).hexdigest()[:24]
                    manifest=inside(root,identity+'/'+fence+'/recipe.json')
                    if manifest.stat().st_size>65536:raise ValueError('Manifest too large')
                    saved=json.loads(manifest.read_text())
                    if saved.get('recipe')!=job['body'] or saved.get('lease_token')!=b.get('token'):raise ValueError('Artifact identity mismatch')
                    is_share=job['body'].get('intent')=='share'
                    if is_share:
                        if saved.get('frames')!=[]:raise ValueError('Share exports must not include coaching frames')
                        names=['excerpt.mp4']
                    else:
                        names=['excerpt.mp4']+[f['file'] for f in saved['frames']]
                        if not 1<=len(saved['frames'])<=48:raise ValueError('Invalid frame count')
                    for name in names:
                        if name!='excerpt.mp4' and not re.fullmatch('frame-[0-9]{3}.jpg',name):raise ValueError('Invalid artifact')
                        if not inside(root,identity+'/'+fence+'/'+name).is_file():raise ValueError('Missing artifact')
                    if is_share and (inside(root,identity+'/'+fence+'/prompt.txt').exists() or any(inside(root,identity+'/'+fence).glob('frame-*.jpg'))):
                        raise ValueError('Unexpected coaching artifacts in share export')
                    result={k:saved[k] for k in ('frames','bytes','output_seconds','sha256','render_seconds')}
                    for optional in ('audio_output','color_output','video_codec','pix_fmt','fps_output'):
                        if optional in saved:
                            result[optional]=saved[optional]
                    result['artifact_directory']=fence
                except (ValueError,OSError,KeyError,TypeError) as e:abort(400,str(e))
            elif (
                not isinstance(result,dict)
                or not isinstance(result.get('answer'),str)
                or not 1<=len(result['answer'])<=64000
                or result.get('analysis_mode')!=('learning_plan' if job['body'].get('purpose')=='learning_plan' else 'timestamped_frames')
            ):
                abort(400,'Invalid coaching response')
            else:
                # Explicitly whitelist public result fields; no paths/tokens/provider logs.
                result={k:result[k] for k in ('answer','analysis_mode','model','elapsed_seconds','usage') if k in result}
        elif action=='fail':
            result={'error':str((result or {}).get('error','Worker failed'))[:500]}
        try:
            updated = queue.worker_update(identity,b.get('token'),action,result,b.get('meta'))
            return jsonify(**updated, config_version=queue.worker_ai_config()['version'])
        except QueueConflict:raise
        except ValueError as e:abort(400,str(e))


    @routes.get('/api/coaching/evidence/<identity>')
    def evidence(identity):
        try:return jsonify(queue.evidence(identity))
        except KeyError:abort(404,'No retained evidence for this response')

    @routes.get('/api/coaching/evidence/<identity>/<frame_id>.jpg')
    def evidence_frame(identity,frame_id):
        try:
            manifest=queue.evidence(identity)
            frame=next(f for f in manifest['frames'] if f['frame_id']==frame_id)
            path=inside(root,'evidence/frames/'+frame['sha256']+'.jpg')
            if not path.is_file():abort(410,'Retained evidence is unavailable; the annotation is still saved')
            return send_file(path,mimetype='image/jpeg',conditional=True)
        except (KeyError,StopIteration,ValueError):abort(404,'Evidence frame not found')

    @routes.put('/api/coaching/annotations/<identity>')
    def review_annotation(identity):
        b=request.get_json()
        if not isinstance(b,dict):abort(400,'Expected an object')
        try:
            with queue.db() as db:row=db.execute('SELECT source_id FROM annotations WHERE id=?',(identity,)).fetchone()
            if not row:abort(404,'Suggestion not found')
            data=session(row['source_id'])
            if b.get('publication')!=data['published_at']:abort(409,'Recording changed. Reload before reviewing this suggestion')
            queue.review_annotation(identity,b,data['published_at'])
            return jsonify(next(a for a in queue.annotation_list(data['id'],data['published_at'],data['clips']) if a['id']==identity))
        except QueueConflict:raise
        except KeyError:abort(404,'Suggestion not found')
        except ValueError as e:abort(400,str(e))

    @routes.post('/api/coaching/annotations')
    def manual_annotation():
        b=request.get_json()
        if not isinstance(b,dict):abort(400,'Expected an object')
        job=get(b.get('job_id',''))
        if job['body'].get('purpose')=='learning_plan' or not job['body'].get('source_id'):
            abort(400,'A completed response with retained evidence is required')
        data=session(job['body']['source_id'])
        if b.get('publication')!=data['published_at']:abort(409,'Recording changed. Reload before saving')
        try:
            key=queue.manual_annotation(job,b.get('content'),data['published_at'],b.get('request_id'))
            return jsonify(next(a for a in queue.annotation_list(data['id'],data['published_at'],data['clips']) if a['id']==key)),201
        except QueueConflict:raise
        except (ValueError,KeyError) as e:abort(400,str(e))

    @routes.get('/api/coaching/annotations/export/<source>')
    def export_annotations(source):
        # Historical annotations remain exportable even after an active publication disappears.
        if not re.fullmatch('[a-f0-9]{64}',source):abort(404)
        return jsonify(queue.annotation_export(source,request.args.get('after','')))

    app.register_blueprint(routes)
