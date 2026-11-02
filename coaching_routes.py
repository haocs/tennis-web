"""LAN UI APIs and fenced worker protocol; large artifacts stay on shared storage."""
import hashlib
import hmac
import json
from pathlib import Path
import re
from flask import Blueprint, abort, jsonify, request, send_file
from coaching import Coaching, QueueConflict, recipe
from storage import inside


def register_coaching(app,library,session):
    routes=Blueprint('coaching',__name__)
    queue=Coaching(Path(app.config['DATA_ROOT'])/'coaching.sqlite3')
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

    @routes.get('/api/coaching/sessions/<identity>')
    def list_coaching(identity):
        session(identity)
        return jsonify(**queue.listing(identity),enabled=bool(app.config['COACH_WORKER_TOKEN']))

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
        if not isinstance(prompt,str) or not 1<=len(prompt.strip())<=4000:abort(400,'Enter a question up to 4000 characters')
        if b.get('consent') is not True:abort(400,'Confirm sending selected frames and your question to OpenAI')
        spec=render['body'];data=session(spec['source_id'])
        if spec['publication']!=data['published_at']:abort(409,'Recording changed; prepare a fresh excerpt')
        body=dict(spec,export_id=render['id'],artifact_directory=render['result']['artifact_directory'],prompt=prompt.strip(),provider='chatgpt-codex',consent=True)
        try:return jsonify(queue.create('ai',body,b.get('request_id'))),201
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
        if job['role']!='render' or job['state']!='completed' or name not in ('excerpt.mp4','recipe.json','prompt.txt'):
            abort(404,'Export unavailable')
        try:
            path=inside(root,identity+'/'+job['result']['artifact_directory']+'/'+name)
            if not path.is_file():abort(410,'Preview expired. Prepare it again; saved advice is retained')
            if name=='recipe.json':
                return jsonify(recipe=job['body'],result=job['result'])
            return send_file(path,conditional=True,as_attachment=request.args.get('download')=='1',download_name='coaching-'+name)
        except ValueError:abort(404)

    @routes.post('/api/coaching/worker/<role>/claim')
    def claim(role):
        worker();b=request.get_json()
        if not isinstance(b,dict):abort(400,'Expected an object')
        try:return jsonify(job=queue.claim(role,b.get('worker_id')))
        except QueueConflict:raise
        except ValueError as e:abort(400,str(e))

    @routes.post('/api/coaching/worker/jobs/<identity>/<action>')
    def update(identity,action):
        worker();b=request.get_json()
        if not isinstance(b,dict):abort(400,'Expected an object')
        job=get(identity);result=b.get('result')
        if action=='complete' and job['state']=='completed':
            return jsonify(queue.worker_update(identity,b.get('token'),action,result))
        if action=='complete':
            if job['role']=='render':
                # Worker writes once to its fenced directory. The NAS only inspects small metadata.
                try:
                    fence=hashlib.sha256(str(b.get('token')).encode()).hexdigest()[:24]
                    manifest=inside(root,identity+'/'+fence+'/recipe.json')
                    if manifest.stat().st_size>65536:raise ValueError('Manifest too large')
                    saved=json.loads(manifest.read_text())
                    if saved.get('recipe')!=job['body'] or saved.get('lease_token')!=b.get('token'):raise ValueError('Artifact identity mismatch')
                    names=['excerpt.mp4']+[f['file'] for f in saved['frames']]
                    if not 1<=len(saved['frames'])<=48:raise ValueError('Invalid frame count')
                    for name in names:
                        if name!='excerpt.mp4' and not re.fullmatch('frame-[0-9]{3}.jpg',name):raise ValueError('Invalid artifact')
                        if not inside(root,identity+'/'+fence+'/'+name).is_file():raise ValueError('Missing artifact')
                    result={k:saved[k] for k in ('frames','bytes','output_seconds','sha256','render_seconds')}
                    result['artifact_directory']=fence
                except (ValueError,OSError,KeyError,TypeError) as e:abort(400,str(e))
            elif not isinstance(result,dict) or not isinstance(result.get('answer'),str) or not 1<=len(result['answer'])<=20000 or result.get('analysis_mode')!='timestamped_frames':
                abort(400,'Invalid coaching response')
            else:
                # Explicitly whitelist public result fields; no paths/tokens/provider logs.
                result={k:result[k] for k in ('answer','analysis_mode','model','elapsed_seconds','usage') if k in result}
        elif action=='fail':
            result={'error':str((result or {}).get('error','Worker failed'))[:500]}
        try:return jsonify(queue.worker_update(identity,b.get('token'),action,result))
        except QueueConflict:raise
        except ValueError as e:abort(400,str(e))

    app.register_blueprint(routes)
