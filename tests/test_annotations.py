"""Metadata selectors, conflicts and histories remain independent of predictions."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from test_app import setup,ID,CLIP,put
from test_coaching_annotations import prepared,claimed,pin,complete,suggestion
H={'X-Tennis-Request':'1'}


def create(c,data,kind='note',target=None,body=None,rid='metadata-request-001'):
    return c.post('/api/annotations',json=dict(source_id=ID,publication=data['published_at'],kind=kind,target=target or dict(scope='point',start_seconds=8),body=body or dict(text='Good recovery'),request_id=rid),headers=H)


def listing(c):return c.get('/api/annotations/sources/'+ID).json['annotations']


def test_target_types_validation_and_origin(setup):
    app,root,data=setup;c=app.test_client()
    assert 'coach' in c.get('/api/annotations/types').json['kinds']
    targets=[dict(scope='video'),dict(scope='point',start_seconds=8),dict(scope='interval',start_seconds=7,end_seconds=12),dict(scope='clip',clip_id=CLIP,start_seconds=5,end_seconds=15)]
    for i,t in enumerate(targets):assert create(c,data,target=t,rid='target-type-'+str(i)).status_code==201
    for t in [dict(scope='video',start_seconds=1),dict(scope='point',start_seconds=True),dict(scope='point',start_seconds=float('nan')),dict(scope='interval',start_seconds=9,end_seconds=8),dict(scope='clip',clip_id=CLIP,start_seconds=4,end_seconds=15),dict(scope='point',start_seconds=30)]:assert create(c,data,target=t).status_code==400
    assert create(c,data,kind='coach').status_code==400
    assert create(c,data,body=dict(text='x',training_label_status='accepted')).status_code==400
    assert create(c,dict(data,published_at=0)).status_code==409
    assert c.post('/api/annotations',json={}).status_code==415
    assert c.post('/api/annotations',json={},headers={**H,'Origin':'http://other'}).status_code==403
    assert c.get('/api/annotations/sources/'+ID+'?limit=999').status_code==400


def test_idempotency_cas_revisions_and_singleton(setup):
    app,root,data=setup;c=app.test_client();a=create(c,data).json
    assert create(c,data).json['id']==a['id']
    assert create(c,data,body=dict(text='different')).status_code==409
    url='/api/annotations/'+a['id'];body=dict(publication=data['published_at'],version=1,body=dict(text='<script>note</script>'))
    with ThreadPoolExecutor(2) as pool:codes=list(pool.map(lambda _:put(app.test_client(),url,body).status_code,range(2)))
    assert sorted(codes)==[200,409]
    assert put(c,url,dict(publication=data['published_at'],version=2,status='archived')).json['status']=='archived'
    assert put(c,url,dict(publication=data['published_at'],version=3,status='active')).json['status']=='active'
    rev=c.get(url+'/revisions').json['revisions'];assert [r['version'] for r in rev]==[1,2,3,4]
    assert rev[0]['body']['text']=='Good recovery'
    target=dict(scope='clip',clip_id=CLIP,start_seconds=5,end_seconds=15)
    u=create(c,data,'usefulness',target,dict(value='less_useful'),rid='usefulness-001')
    assert u.status_code==201
    dup=create(c,data,'usefulness',target,dict(value='less_useful'),rid='usefulness-002')
    assert dup.status_code==409 and dup.json['current']['id']==u.json['id']
    export=c.get('/api/annotations/sources/'+ID+'/export').json
    assert export['training_label_status']=='not_released' and all('revision_source' in r for r in export['annotations'])


def test_reprocess_mapping_and_retention(setup):
    app,root,data=setup;c=app.test_client()
    point=create(c,data,'highlight',body=dict(text='Winner'),rid='highlight-001').json
    interval=create(c,data,target=dict(scope='interval',start_seconds=6,end_seconds=19),rid='interval-001').json
    target=dict(scope='clip',clip_id=CLIP,start_seconds=5,end_seconds=15)
    flag=create(c,data,'usefulness',target,dict(value='less_useful'),rid='usefulness-001').json
    report=create(c,data,'cut_feedback',target,dict(reasons=['downtime'],text='Long wait'),rid='cut-report-001').json
    changed=copy.deepcopy(data);changed['published_at']+=1;changed['clips'][0]['id']='c'*24
    path=root/'metadata/sessions'/f'{ID}.json';path.write_text(json.dumps(changed))
    rows={a['id']:a for a in listing(c)}
    assert rows[flag['id']]['relation']=='historical_run' and not rows[flag['id']]['playback_eligible']
    assert rows[report['id']]['relation']=='historical_run' and not rows[report['id']]['playback_eligible']
    assert rows[interval['id']]['relation']=='partly_retained'
    changed['published_at']+=1;changed['clips'][0].update(original_start_seconds=9,duration_seconds=6,playback_end_seconds=6);path.write_text(json.dumps(changed))
    rows={a['id']:a for a in listing(c)}
    assert rows[point['id']]['relation']=='unavailable'
    assert rows[flag['id']]['relation']=='historical_run' and not rows[flag['id']]['playback_eligible']
    assert rows[interval['id']]['target']==interval['target'] and rows[interval['id']]['visible_intervals'][0]['original_start']==9
    edit=put(c,'/api/annotations/'+flag['id'],dict(publication=changed['published_at'],version=1,body=dict(value='less_useful',text='Saved')))
    assert edit.status_code==200 and edit.json['target']==target
    path.unlink()
    assert not c.get('/api/annotations/sources/'+ID).json['source_available']
    assert len(c.get('/api/annotations/sources/'+ID+'/export').json['annotations'])==4


def test_coaching_adapter_keeps_authority(setup):
    app,c,data,ai,folder=prepared(setup);job=claimed(c);manifest=pin(app,c,job,folder)
    complete(c,job,json.dumps(dict(schema_version=2,summary='Reset',suggestions=[suggestion(manifest)])))
    rows=listing(c);assert len(rows)==2
    assert any(a['id'].startswith('coach-response:') and not a['playback_eligible'] for a in rows)
    a=next(a for a in rows if a['id'].startswith('coach:'));assert a['id'].startswith('coach:') and a['read_only']
    assert a['target']['scope']=='interval' and a['target']['start_seconds']==6.4
    assert a['origin']['job_id']==job['id'] and not a['playback_eligible']
    assert put(c,'/api/annotations/'+a['id'],dict(version=1,publication=data['published_at'],status='archived')).status_code==400
    assert c.get('/api/annotations/'+a['id']+'/revisions').json['revisions']
    assert c.get('/api/annotations/sources/'+ID+'/export').json['annotations'][0]['evidence_source']=='/api/coaching/evidence/'+job['id']
    with app.extensions['annotations'].db() as db:assert db.execute('SELECT count(*) FROM annotations').fetchone()[0]==0


def test_half_open_point_and_pagination(setup):
    app,root,data=setup;c=app.test_client();other=copy.deepcopy(data['clips'][0])
    other.update(id='c'*24,index=2,original_start_seconds=15,original_end_seconds=20,duration_seconds=5,playback_start_seconds=10,playback_end_seconds=15,file='Clips/clip-002.mp4')
    data['clips'].append(other);data['video']['duration_seconds']=15
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    a=create(c,data,'highlight',dict(scope='point',start_seconds=15),dict(text='Boundary'),rid='boundary-001').json
    assert a['related_clip_ids']==['c'*24]
    create(c,data,rid='second-note-001')
    one=c.get('/api/annotations/sources/'+ID+'?limit=1').json
    two=c.get('/api/annotations/sources/'+ID+'?limit=1&after='+one['next_after']).json
    assert one['annotations'][0]['id']!=two['annotations'][0]['id'] and two['next_after'] is None


def test_submillisecond_clip_bounds_retain_millisecond_moments(setup):
    app,root,data=setup;c=app.test_client()
    sub=copy.deepcopy(data)
    sub['source']['duration_seconds']=1600
    sub['video']['duration_seconds']=16
    sub['clips'][0].update(original_start_seconds=1547.516842,original_end_seconds=1563.516842,duration_seconds=16,playback_start_seconds=0,playback_end_seconds=16)
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(sub))
    for i,(ms,inside) in enumerate(((1547516,False),(1547517,True),(1563516,True),(1563517,False))):
        res=c.post('/api/moments',json=dict(original_recording_id=ID,publication=sub['published_at'],type='comment',start_ms=ms,payload=dict(text='Start note'),request_id=f'subms-boundary-{i}'),headers=H)
        assert res.status_code==201
        body=res.json
        if inside:
            assert body['relation']=='retained' and body['playback_eligible'] and body['clip_ids']==[CLIP]
        else:
            assert body['relation']=='unavailable' and not body['playback_eligible'] and body['clip_ids']==[]
    adj=copy.deepcopy(data)
    adj['video']['duration_seconds']=2
    adj['clips']=[
        dict(data['clips'][0],id='b'*24,index=1,original_start_seconds=0,original_end_seconds=1.0004,duration_seconds=1.0004,playback_start_seconds=0,playback_end_seconds=1.0004),
        dict(data['clips'][0],id='c'*24,index=2,original_start_seconds=1.0004,original_end_seconds=2,duration_seconds=0.9996,playback_start_seconds=1.0004,playback_end_seconds=2,file='Clips/clip-002.mp4'),
    ]
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(adj))
    m1000=c.post('/api/moments',json=dict(original_recording_id=ID,publication=adj['published_at'],type='comment',start_ms=1000,payload=dict(text='Edge A'),request_id='adj-1000'),headers=H).json
    m1001=c.post('/api/moments',json=dict(original_recording_id=ID,publication=adj['published_at'],type='comment',start_ms=1001,payload=dict(text='Edge B'),request_id='adj-1001'),headers=H).json
    assert m1000['clip_ids']==['b'*24] and m1001['clip_ids']==['c'*24]


def test_unstructured_response_is_selection_context_not_invented_precise_timing(setup):
    app,c,data,ai,folder=prepared(setup);job=claimed(c);pin(app,c,job,folder)
    complete(c,job,'A useful response without precise event timing.')
    rows=listing(c);assert len(rows)==1
    a=rows[0];assert a['id']=='coach-response:'+job['id']
    assert a['target']==dict(scope='interval',start_seconds=6,end_seconds=8)
    assert not a['body']['timing_verified'] and not a['playback_eligible']
    history=c.get('/api/annotations/'+a['id']+'/revisions').json['revisions']
    assert not history[0]['history_complete']
