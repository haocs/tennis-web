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


def test_cut_review_verdicts_legacy_null_boundary_suggestions_and_gap_intervals(setup):
    from entity_model import output_version
    app,root,data=setup;c=app.test_client()
    data['published_at']+=1
    data['processing']=dict(run_id='run-101',algorithm_version='v2.1',task_id='t'*32,attempt_id='a'*32,source_id='s'*32,private_path='/secret/raw.mov')
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    clip_target=dict(scope='clip',clip_id=CLIP,start_seconds=5,end_seconds=15)

    # 1. Legacy cut_feedback without verdict keeps verdict: None (never coerced to poor_cut).
    legacy=c.post('/api/processing-annotations',json=dict(
        source_id=ID,publication=data['published_at'],kind='cut_feedback',
        target=clip_target,body=dict(reasons=['end_late','lost_player'],text='Walked away'),
        request_id='cut-legacy-null-001'
    ),headers=H)
    assert legacy.status_code==201
    assert legacy.json['body']['verdict'] is None
    assert legacy.json['body']['reasons']==['end_late','lost_player']
    assert legacy.json['context']['video_version_id']==output_version(ID,data['published_at'])
    assert legacy.json['context']['processing_ref']==dict(run_id='run-101',algorithm_version='v2.1',task_id='t'*32,attempt_id='a'*32,source_id='s'*32)
    assert 'private_path' not in legacy.json['context']['processing_ref']

    # 2. Good cut verdict alone (without issue reasons) is valid evidence.
    good=c.post('/api/processing-annotations',json=dict(
        source_id=ID,publication=data['published_at'],kind='cut_feedback',
        target=clip_target,body=dict(verdict='good_cut',text='Clean rally bounds'),
        request_id='cut-good-verdict-001'
    ),headers=H)
    assert good.status_code==201
    assert good.json['body']['verdict']=='good_cut' and good.json['body']['reasons']==[]

    # 3. Boundary suggestions: can extend earlier/later within source [0, 20000ms] or trim,
    #    and omitted side defaults to original clip bound [5000, 15000).
    extended=c.post('/api/processing-annotations',json=dict(
        source_id=ID,publication=data['published_at'],kind='cut_feedback',
        target=clip_target,
        body=dict(verdict='needs_adjustment',reasons=['start_late','end_late'],suggested_start_ms=3500,suggested_end_ms=12500,heuristic_ref='h_clip01_tail'),
        request_id='cut-bounds-valid-001'
    ),headers=H)
    assert extended.status_code==201
    assert extended.json['body']['suggested_start_ms']==3500
    assert extended.json['body']['suggested_end_ms']==12500
    assert extended.json['body']['heuristic_ref']=='h_clip01_tail'

    # Omitted start defaults to 5000ms -> suggested_end_ms=4000 is invalid (5000 >= 4000).
    assert c.post('/api/processing-annotations',json=dict(
        source_id=ID,publication=data['published_at'],kind='cut_feedback',
        target=clip_target,body=dict(verdict='needs_adjustment',suggested_end_ms=4000),
        request_id='cut-bounds-bad-end-001'
    ),headers=H).status_code==400
    # Omitted end defaults to 15000ms -> suggested_start_ms=16000 is invalid (16000 >= 15000).
    assert c.post('/api/processing-annotations',json=dict(
        source_id=ID,publication=data['published_at'],kind='cut_feedback',
        target=clip_target,body=dict(verdict='needs_adjustment',suggested_start_ms=16000),
        request_id='cut-bounds-bad-start-001'
    ),headers=H).status_code==400
    # Outside source duration (20000ms) or boolean/float is rejected.
    for bad_body in (
        dict(verdict='needs_adjustment',suggested_end_ms=25000),
        dict(verdict='needs_adjustment',suggested_start_ms=True),
        dict(verdict='missed_play',reasons=['missed_play']),
        dict(reasons=[]),
    ):
        assert c.post('/api/processing-annotations',json=dict(
            source_id=ID,publication=data['published_at'],kind='cut_feedback',
            target=clip_target,body=bad_body,request_id='cut-bounds-invalid'
        ),headers=H).status_code==400

    # 4. Missed-play interval reports inside removed gaps ([0, 5) and [15, 20)):
    gap_report=c.post('/api/processing-annotations',json=dict(
        source_id=ID,publication=data['published_at'],kind='cut_feedback',
        target=dict(scope='interval',start_seconds=15.5,end_seconds=19.0),
        body=dict(verdict='missed_play',reasons=['missed_play'],text='Missed serve and return in trailing gap'),
        request_id='cut-gap-missed-play-001'
    ),headers=H)
    assert gap_report.status_code==201
    assert gap_report.json['record_class']=='processing_annotation'
    assert gap_report.json['relation']=='removed_gap'
    assert gap_report.json['subject']['type']=='video_version'
    # Never leaks into serving Moments:
    assert c.get(f'/api/recordings/{ID}/moments').json['moments']==[]

    # Rejects interval overlapping retained clip [5, 15) or spanning across clip from [2, 18):
    for bad_span in ((4.0, 6.0), (14.5, 16.0), (2.0, 18.0)):
        assert c.post('/api/processing-annotations',json=dict(
            source_id=ID,publication=data['published_at'],kind='cut_feedback',
            target=dict(scope='interval',start_seconds=bad_span[0],end_seconds=bad_span[1]),
            body=dict(verdict='missed_play',reasons=['missed_play']),
            request_id='cut-gap-overlap-invalid'
        ),headers=H).status_code==400


def test_cut_review_version_scoping_rejects_arbitrary_ids_and_guards_historical_writes(setup):
    from entity_model import output_version
    app,root,data=setup;c=app.test_client()
    v1_pub=data['published_at']
    v1_id=output_version(ID,v1_pub)
    clip_v1=dict(scope='clip',clip_id=CLIP,start_seconds=5,end_seconds=15)

    # Create review on v1 while v1 is current serving
    r1=c.post('/api/processing-annotations',json=dict(
        source_id=ID,publication=v1_pub,reviewed_video_version_id=v1_id,
        kind='cut_feedback',target=clip_v1,body=dict(verdict='needs_adjustment',reasons=['end_late']),
        request_id='v1-cut-review-001'
    ),headers=H)
    assert r1.status_code==201

    # Publish v2
    v2_data=copy.deepcopy(data)
    v2_data['published_at']=v1_pub+100
    v2_data['clips'][0].update(id='b'*24,original_start_seconds=4,original_end_seconds=14,duration_seconds=10,playback_end_seconds=10)
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(v2_data))
    v2_id=output_version(ID,v2_data['published_at'])

    versions_resp=c.get(f'/api/processing/videos/{ID}/versions').json
    assert versions_resp['current_version_id']==v2_id
    assert [v['id'] for v in versions_resp['versions']]==[v1_id,v2_id]

    # Writing to v1 without explicit allow_historical=True returns 409 Conflict
    stale=c.post('/api/processing-annotations',json=dict(
        source_id=ID,publication=v1_pub,reviewed_video_version_id=v1_id,
        kind='cut_feedback',target=clip_v1,body=dict(verdict='poor_cut',reasons=['poor_cut']),
        request_id='v1-stale-attempt-001'
    ),headers=H)
    assert stale.status_code==409

    # Editing r1 (from v1) without explicit historical scoping returns 409 Conflict
    assert put(c,f'/api/processing-annotations/{r1.json["id"]}',dict(
        publication=v2_data['published_at'],version=1,body=dict(verdict='poor_cut',reasons=['poor_cut'])
    )).status_code==409
    assert put(c,f'/api/processing-annotations/{r1.json["id"]}',dict(
        publication=v1_pub,reviewed_video_version_id=v1_id,version=1,body=dict(verdict='poor_cut',reasons=['poor_cut'])
    )).status_code==409

    # Rejects arbitrary/unvalidated historical or candidate version IDs even with allow_historical=True
    fake_vid=output_version(ID,v1_pub-999)
    for bad_vid in ('candidate-123','v_'+'f'*32,fake_vid):
        assert c.post('/api/processing-annotations',json=dict(
            source_id=ID,publication=v1_pub-999,reviewed_video_version_id=bad_vid,allow_historical=True,
            kind='cut_feedback',target=clip_v1,body=dict(verdict='poor_cut',reasons=['poor_cut']),
            request_id='arbitrary-vid-reject'
        ),headers=H).status_code==400

    # Explicitly scoped historical write to validated retained v1 succeeds and keeps v1 context
    scoped_create=c.post('/api/processing-annotations',json=dict(
        source_id=ID,publication=v1_pub,reviewed_video_version_id=v1_id,allow_historical=True,
        kind='cut_feedback',target=clip_v1,body=dict(verdict='poor_cut',reasons=['poor_cut'],text='Historical v1 note'),
        request_id='v1-explicit-scoped-001'
    ),headers=H)
    assert scoped_create.status_code==201
    assert scoped_create.json['context']['publication']==v1_pub
    assert scoped_create.json['context']['video_version_id']==v1_id

    scoped_edit=put(c,f'/api/processing-annotations/{r1.json["id"]}',dict(
        publication=v1_pub,reviewed_video_version_id=v1_id,allow_historical=True,
        version=1,body=dict(verdict='needs_adjustment',reasons=['end_late'],suggested_end_ms=13000,text='Updated on v1')
    ))
    assert scoped_edit.status_code==200
    assert scoped_edit.json['body']['suggested_end_ms']==13000
    assert scoped_edit.json['context']['video_version_id']==v1_id

    # Querying processing annotations scoped to v1 projects against v1 manifest
    v1_list=c.get(f'/api/processing-annotations/sources/{ID}?video_version_id={v1_id}').json
    assert v1_list['video_version_id']==v1_id and v1_list['current_version_id']==v2_id
    assert all(a['relation']=='exact' for a in v1_list['annotations'])
    assert c.get(f'/api/processing-annotations/sources/{ID}?video_version_id={fake_vid}').status_code==404

    # Create review on v2 and verify ?video_version_id filters before pagination
    r2=c.post('/api/processing-annotations',json=dict(
        source_id=ID,publication=v2_data['published_at'],reviewed_video_version_id=v2_id,
        kind='cut_feedback',target=dict(scope='clip',clip_id='b'*24,start_seconds=4,end_seconds=14),
        body=dict(verdict='good_cut',text='Current v2 cut'),request_id='v2-cut-review-001'
    ),headers=H)
    assert r2.status_code==201
    cursor='';paged_v2=[]
    while True:
        page_resp=c.get(f'/api/processing-annotations/sources/{ID}?video_version_id={v2_id}&limit=1&after={cursor}').json
        paged_v2.extend(page_resp['annotations'])
        cursor=page_resp['next_after']
        if not cursor:break
    assert [x['id'] for x in paged_v2]==[r2.json['id']]
    assert all(x['context']['publication']==v2_data['published_at'] for x in paged_v2)


def test_missed_play_gap_report_resize_archive_and_conflict(setup):
    app,root,data=setup;c=app.test_client()
    gap1=dict(scope='interval',start_seconds=0.5,end_seconds=2.5)
    created=c.post('/api/processing-annotations',json=dict(
        source_id=ID,publication=data['published_at'],kind='cut_feedback',
        target=gap1,body=dict(verdict='missed_play',reasons=['missed_play'],text='Rally in opening gap'),
        request_id='gap-resize-001'
    ),headers=H)
    assert created.status_code==201
    url=f'/api/processing-annotations/{created.json["id"]}'

    # Resizing within the same removed gap [0, 5) succeeds and preserves revision history
    resized_target=dict(scope='interval',start_seconds=1.0,end_seconds=4.0)
    resized=put(c,url,dict(publication=data['published_at'],version=1,target=resized_target,body=dict(verdict='missed_play',reasons=['missed_play'],text='Expanded gap range')))
    assert resized.status_code==200
    assert resized.json['target']==resized_target
    assert resized.json['version']==2

    # Stale CAS version fails with 409
    assert put(c,url,dict(publication=data['published_at'],version=1,status='archived')).status_code==409

    # Moving a gap report across a retained clip into another gap [15, 20) or overlapping clip [5, 15) is rejected
    for bad_target in (
        dict(scope='interval',start_seconds=16.0,end_seconds=18.0),
        dict(scope='interval',start_seconds=2.0,end_seconds=6.0),
        dict(scope='clip',clip_id=CLIP,start_seconds=5,end_seconds=15),
    ):
        assert put(c,url,dict(publication=data['published_at'],version=2,target=bad_target)).status_code==400

    # Archiving and checking revisions
    archived=put(c,url,dict(publication=data['published_at'],version=2,status='archived'))
    assert archived.status_code==200 and archived.json['status']=='archived'
    revs=c.get(f'/api/annotations/{created.json["id"]}/revisions').json['revisions']
    assert len(revs)==3
    assert revs[0]['target']==gap1 and revs[1]['target']==resized_target and revs[2]['status']=='archived'
