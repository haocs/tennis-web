import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
import pytest
from test_app import setup, ID, CLIP
from coaching import Coaching, QueueConflict, recipe
from app import create_app


def post(c,path,body,token=None):
    h={'X-Tennis-Request':'1'}
    if token:h['Authorization']='Bearer '+token
    return c.post('/api/coaching/'+path,json=body,headers=h)


def enabled(setup):
    app,root,data=setup
    app.config['COACH_WORKER_TOKEN']='test-worker-secret'
    return app,root,data


def selection(data):return dict(source_id=ID,clip_id=CLIP,publication=data['published_at'],start=1,end=3,speed=.5,crop=[.25,.25,.5,.5],quality='balanced',request_id='test-request-001')


def test_queue_isolation_and_atomic_claim(tmp_path):
    q=Coaching(tmp_path/'q.sqlite3')
    q.create('render',{'source_id':ID},'render-001')
    ai=q.create('ai',{'source_id':ID},'ai-job-001')
    with ThreadPoolExecutor(4) as pool:claims=list(pool.map(lambda n:q.claim('ai',str(n)),range(4)))
    assert len([c for c in claims if c])==1
    assert next(c for c in claims if c)['id']==ai['id']
    assert q.claim('render','exporter')['role']=='render'


def test_lease_fencing_and_uncertain_provider_not_retried(tmp_path):
    now=[100.0];q=Coaching(tmp_path/'q.sqlite3',lambda:now[0]);job=q.create('ai',{'source_id':ID},'request-001')
    claim=q.claim('ai','one');q.worker_update(job['id'],claim['token'],'calling')
    now[0]+=91
    assert q.claim('ai','two') is None
    assert q.get(job['id'])['state']=='needs_review'
    with pytest.raises(QueueConflict):q.worker_update(job['id'],claim['token'],'complete',{'answer':'old'})
    render=q.create('render',{'source_id':ID},'request-002');old=q.claim('render','one');now[0]+=91;new=q.claim('render','two')
    assert old['token']!=new['token']
    with pytest.raises(QueueConflict):q.worker_update(render['id'],old['token'],'complete',{})


def test_idempotency_and_cue_revision(tmp_path):
    q=Coaching(tmp_path/'q.sqlite3');a=q.create('ai',{'source_id':ID},'request-001')
    assert q.create('ai',{'source_id':ID},'request-001')['id']==a['id']
    with pytest.raises(QueueConflict):q.create('ai',{'source_id':'changed'},'request-001')
    c=q.claim('ai','one');q.worker_update(a['id'],c['token'],'calling');done=q.worker_update(a['id'],c['token'],'complete',{'answer':'test'})
    saved=q.edit(a['id'],done['version'],'cue','Stay balanced')
    assert saved['result']['answer']=='test' and saved['result']['cue_reviewed']
    with pytest.raises(QueueConflict):q.edit(a['id'],done['version'],'cue','overwrite')


def test_api_origin_auth_recipe_and_retention(setup,tmp_path):
    app,root,data=enabled(setup);c=app.test_client();b=selection(data)
    assert post(c,'exports',b).status_code==201
    assert post(c,'exports',b).json['id']==post(c,'exports',b).json['id']
    bad=dict(b,request_id='bad-request',crop=[0,0,2,1]);assert post(c,'exports',bad).status_code==400
    assert post(c,'worker/ai/claim',{'worker_id':'x'}).status_code==403
    claim=post(c,'worker/render/claim',{'worker_id':'x'},'test-worker-secret').json['job']
    assert claim['role']=='render'
    assert post(c,'worker/ai/claim',{'worker_id':'x'},'test-worker-secret').json['job'] is None
    assert c.get('/api/coaching/jobs/'+claim['id']).json.get('token') is None
    assert post(c,'requests',{'export_id':claim['id'],'prompt':'coach','consent':True,'request_id':'ask-0001'}).status_code==409
    app.config['COACH_WORKER_TOKEN']=''
    assert post(c,'exports',dict(b,request_id='new-request')).status_code==503
    assert c.get('/api/coaching/sessions/'+ID).json['jobs']


def test_bounds_and_original_offsets(setup):
    app,root,data=setup;clip=data['clips'][0];b=selection(data)
    r=recipe(b,data,clip);assert r['original_start']==6 and r['original_end']==8
    for fields in [{'start':True},{'end':float('nan')},{'start':-1},{'end':100},{'speed':0},{'crop':[0,0,0,1]}]:
        with pytest.raises(ValueError):recipe(dict(b,**fields),data,clip)


def test_artifact_requires_fenced_manifest(setup):
    import hashlib
    app,root,data=enabled(setup);c=app.test_client();created=post(c,'exports',selection(data)).json
    claim=post(c,'worker/render/claim',{'worker_id':'media'},'test-worker-secret').json['job']
    fence=hashlib.sha256(claim['token'].encode()).hexdigest()[:24];folder=Path(app.config['COACH_MEDIA_ROOT'])/claim['id']/fence;folder.mkdir(parents=True)
    (folder/'excerpt.mp4').write_bytes(b'video');(folder/'frame-000.jpg').write_bytes(b'frame');(folder/'prompt.txt').write_text('question')
    payload=dict(recipe=claim['body'],lease_token=claim['token'],frames=[{'file':'frame-000.jpg'}],bytes=5,output_seconds=4,sha256='a'*64,render_seconds=1)
    (folder/'recipe.json').write_text(json.dumps(payload))
    done=post(c,'worker/jobs/'+claim['id']+'/complete',{'token':claim['token']},'test-worker-secret');assert done.status_code==200
    meta=c.get('/api/coaching/exports/'+claim['id']+'/recipe.json');assert claim['token'] not in meta.get_data(as_text=True)
    assert post(c,'requests',{'export_id':claim['id'],'prompt':'coach','consent':False,'request_id':'ask-0001'}).status_code==400
    asked=post(c,'requests',{'export_id':claim['id'],'prompt':'coach','consent':True,'request_id':'ask-0001'});assert asked.status_code==201
    assert asked.json['body']['artifact_directory']==fence
    (folder/'excerpt.mp4').unlink();assert c.get('/api/coaching/exports/'+claim['id']+'/excerpt.mp4').status_code==410
    assert c.get('/api/coaching/jobs/'+asked.json['id']).status_code==200


def test_completion_ack_can_be_retried_without_a_second_call(tmp_path):
    now=[100.0];q=Coaching(tmp_path/'q.sqlite3',lambda:now[0]);created=q.create('ai',{},'request-001');claim=q.claim('ai','one')
    q.worker_update(created['id'],claim['token'],'calling')
    first=q.worker_update(created['id'],claim['token'],'complete',{'answer':'first'})
    now[0]+=1000
    again=q.worker_update(created['id'],claim['token'],'complete',{'answer':'changed'})
    assert again['result']==first['result'] and again['version']==first['version']
    assert q.claim('ai','two') is None


def test_malformed_worker_and_browser_requests_are_rejected(setup):
    app,root,data=enabled(setup);c=app.test_client()
    for path in ('exports','requests','worker/ai/claim'):
        assert post(c,path,[],token='test-worker-secret').status_code==400
    b=selection(data);b['question']='x'*4001
    assert post(c,'exports',b).status_code==400


def test_worker_identity_persists_across_restart_and_roles_are_distinct(tmp_path):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
    from tennis_coach.worker import Worker
    cfg={'local_state':str(tmp_path)}
    a=Worker(cfg,'ai');again=Worker(cfg,'ai');render=Worker(cfg,'render')
    assert a.identity==again.identity and a.identity!=render.identity


def test_export_request_identity_conflict_is_409(setup):
    app,root,data=enabled(setup);c=app.test_client();b=selection(data)
    assert post(c,'exports',b).status_code==201
    assert post(c,'exports',dict(b,start=1.5)).status_code==409


def test_worker_does_not_claim_when_storage_is_unavailable(tmp_path):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
    from tennis_coach.worker import Worker
    worker=Worker({'local_state':str(tmp_path),'artifact_root':str(tmp_path/'missing')},'ai')
    worker.api=lambda *a:pytest.fail('No job should be claimed before storage is available')
    assert worker.once() is False
    assert json.loads((worker.root/'state.json').read_text())['status']=='storage_unavailable'
