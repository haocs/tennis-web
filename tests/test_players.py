"""Player assignments use original clocks; private profiles never enter AI prompts."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_app import setup, ID, CLIP, put
from test_coaching_annotations import prepared, claimed, pin, complete, suggestion, annotations

H={'X-Tennis-Request':'1'}


def create(c, name='Alex', height=172, level='beginner'):
    r=c.post('/api/players',json=dict(name=name,height_cm=height,level=level),headers=H)
    assert r.status_code==201,r.json
    return r.json


def roster(c, ids, version=0):
    return put(c,f'/api/sessions/{ID}/players',dict(player_ids=ids,version=version))


def focus(c, data, pid, version=0, clip=CLIP):
    return put(c,f'/api/sessions/{ID}/clips/{clip}/player',dict(player_id=pid,version=version,publication=data['published_at']))


def current(c):
    return c.get('/api/sessions/'+ID).json


def test_profiles_validation_persistence_and_concurrency(setup):
    app,root,data=setup;c=app.test_client();p=create(c,name='<Alex> & Co')
    assert c.get('/api/players').json['players']==[p]
    body=dict(name='Alex',height_cm=180,level='experienced',version=1)
    with ThreadPoolExecutor(2) as pool:
        responses=list(pool.map(lambda _:put(app.test_client(),'/api/players/'+p['id'],body).status_code,range(2)))
    assert sorted(responses)==[200,409]
    from app import create_app
    fresh=create_app(dict(TESTING=True,LIBRARY_ROOT=str(root),DATA_ROOT=app.config['DATA_ROOT']))
    assert fresh.test_client().get('/api/players').json['players'][0]['height_cm']==180
    for bad in [[],{},dict(name=''),dict(name='a'*101),dict(name='X',height_cm=True),dict(name='X',height_cm=float('nan')),dict(name='X',level=[]),dict(name='X',level='expert'),dict(name='X',extra='no')]:
        assert c.post('/api/players',json=bad,headers=H).status_code==400
    assert c.post('/api/players',json=dict(name='X')).status_code==415
    assert c.post('/api/players',json=dict(name='X'),headers={**H,'Origin':'http://elsewhere'}).status_code==403
    assert put(c,'/api/players/'+'f'*32,body).status_code==404


def test_rosters_inheritance_explicit_main_and_unknown(setup):
    app,root,data=setup;c=app.test_client();a=create(c);b=create(c,'Blair')
    assert roster(c,[a['id']]).status_code==200
    s=current(c);clip=s['clips'][0]
    assert clip['main_player']['id']==a['id'] and clip['player_assignment']['inherited']
    assert focus(c,data,b['id']).status_code==200
    s=current(c);assert s['clips'][0]['main_player']['id']==b['id']
    assert {p['id'] for p in s['players']}=={a['id'],b['id']}
    assert {p['id'] for p in c.get('/api/sessions').json['practice_groups'][0]['players']}=={a['id'],b['id']}
    assert focus(c,data,a['id']).status_code==409
    assert focus(c,data,None,1).status_code==200
    assert current(c)['clips'][0]['main_player'] is None
    for ids in [None,'x',[],[a['id'],a['id']],[True],['unknown']]:
        response=roster(c,ids,1)
        assert response.status_code==(200 if ids==[] else 400)
    assert roster(c,[a['id']],0).status_code==409
    assert focus(c,dict(data,published_at=0),a['id'],2).status_code==409
    assert focus(c,data,'unknown',2).status_code==400
    assert focus(c,data,[],2).status_code==400


def test_multiple_roster_players_never_guess_a_main_player(setup):
    app,root,data=setup;c=app.test_client();a=create(c);b=create(c,'Blair')
    assert roster(c,[a['id'],b['id']]).status_code==200
    assert current(c)['clips'][0]['main_player'] is None


def test_reprocessed_exact_interval_survives_changed_boundary_requires_review(setup):
    app,root,data=setup;c=app.test_client();a=create(c);b=create(c,'Blair')
    roster(c,[a['id']]);assert focus(c,data,b['id']).status_code==200
    path=root/'metadata/sessions'/f'{ID}.json'
    data=copy.deepcopy(data);data['published_at']+=1;data['clips'][0]['id']='c'*24;path.write_text(json.dumps(data))
    clip=current(c)['clips'][0];assert clip['main_player']['id']==b['id']
    data['published_at']+=1;data['clips'][0].update(original_start_seconds=6,duration_seconds=9,playback_end_seconds=9);path.write_text(json.dumps(data))
    clip=current(c)['clips'][0]
    assert clip['main_player'] is None and clip['player_assignment']['needs_review']
    assert focus(c,data,a['id'],clip='c'*24).status_code==200
    assert current(c)['clips'][0]['main_player']['id']==a['id']
    assert len([k for k in app.extensions['state'].all() if k.startswith('player-focus:')])==2


def test_practice_roster_updates_all_recordings_atomically(setup):
    app,root,data=setup;c=app.test_client();a=create(c)
    sibling=copy.deepcopy(data);sibling['id']='d'*64;sibling['source']['recorded_at']='2026-10-24T17:20:00-0700'
    (root/'metadata/sessions'/f"{sibling['id']}.json").write_text(json.dumps(sibling))
    group=c.get('/api/sessions').json['practice_groups'][0]
    assert len(group['recording_ids'])==2
    body=dict(player_ids=[a['id']],versions=group['player_versions'])
    url='/api/practices/'+group['id']+'/players'
    assert put(c,url,body).status_code==200
    assert all(s['players'][0]['id']==a['id'] for s in c.get('/api/sessions').json['sessions'])
    roster(c,[],1)
    stale=dict(player_ids=[],versions={ID:1,sibling['id']:1})
    assert put(c,url,stale).status_code==409
    assert c.get('/api/sessions/'+sibling['id']).json['player_assignment']['value']['player_ids']==[a['id']]
    assert put(c,url,dict(player_ids=[],versions={ID:2})).status_code==409


def test_coaching_profile_snapshot_is_anonymous_and_retained(setup):
    app,root,data=setup;c=app.test_client();a=create(c,'PrivateName',181,'experienced')
    roster(c,[a['id']])
    app,c,data,ai,folder=prepared(setup)
    body=ai['body'];context=body['player_context']
    assert context['player_id']==a['id'] and context['height_cm']==181
    assert context['level']=='experienced' and context['profile_version']==1
    prompt=body['submitted_prompt']
    assert 'PrivateName' not in prompt and a['id'] not in prompt
    assert 'height_cm' in prompt and 'experienced' in prompt
    assert 'focus is ambiguous' in prompt
    job=claimed(c);manifest=pin(app,c,job,folder)
    complete(c,job,json.dumps(dict(schema_version=2,summary='Reset',suggestions=[suggestion(manifest)])))
    saved_annotation=annotations(c)[0]
    assert saved_annotation['provenance']['player_context']==context
    from test_coaching_annotations import review
    assert review(c,saved_annotation,data['published_at']).json['playback_eligible']
    assert focus(c,data,None).status_code==200
    assert not annotations(c)[0]['player_matches_current'] and not annotations(c)[0]['playback_eligible']
    assert focus(c,data,a['id'],1).status_code==200
    assert annotations(c)[0]['playback_eligible']
    assert put(c,'/api/players/'+a['id'],dict(name='Renamed',height_cm=182,level='pro',version=1)).status_code==200
    saved=c.get('/api/coaching/jobs/'+ai['id']).json
    assert saved['body']['player_context']==context and saved['body']['submitted_prompt']==prompt
    export=c.get('/api/coaching/annotations/export/'+ID).json
    assert export['responses'][0]['request']['body']['player_context']==context
    from test_coaching import post
    stale=post(c,'requests',dict(export_id=body['export_id'],prompt='Check',consent=True,request_id='stale-player-request',player_id=a['id'],player_profile_version=1))
    assert stale.status_code==409
    r=post(c,'requests',dict(export_id=body['export_id'],prompt='Again',consent=True,request_id='changed-profile-followup',follow_up_of=ai['id']))
    assert r.status_code==400


def test_bulk_clip_assignment_is_atomic_versioned_and_explicit(setup):
    app,root,data=setup;c=app.test_client();a=create(c);b=create(c,'Blair')
    second=copy.deepcopy(data['clips'][0]);second.update(id='d'*24,index=2,file='Clips/clip-002.mp4',duration_seconds=5,original_start_seconds=15,original_end_seconds=20,playback_start_seconds=10,playback_end_seconds=15)
    data['clips'].append(second);data['video']['duration_seconds']=15
    manifest=root/'metadata/sessions'/f'{ID}.json';manifest.write_text(json.dumps(data))
    roster(c,[a['id']])
    url=f'/api/sessions/{ID}/clips/players'
    body=dict(player_id=b['id'],publication=data['published_at'],clips=[dict(id=CLIP,version=0),dict(id=second['id'],version=0)])
    assert put(c,url,body).json==dict(saved=True,updated=2)
    assert all(x['main_player']['id']==b['id'] for x in current(c)['clips'])
    assert focus(c,data,a['id'],1).status_code==200
    stale=copy.deepcopy(body);stale['player_id']=None
    for item in stale['clips']:item['version']=1
    assert put(c,url,stale).status_code==409
    clips=current(c)['clips'];assert clips[0]['main_player']['id']==a['id'] and clips[1]['main_player']['id']==b['id']
    stale['clips'][0]['version']=2
    assert put(c,url,stale).status_code==200
    assert all(x['main_player'] is None and not x['player_assignment']['inherited'] for x in current(c)['clips'])
    assert manifest.read_text()==json.dumps(data)
    for bad in [[],[dict(id=CLIP,version=True)],[dict(id=CLIP,version=3)]*2,[dict(id='e'*24,version=0)],[dict(id=CLIP,version=-1)]]:
        assert put(c,url,dict(body,clips=bad)).status_code==400
    assert put(c,url,dict(body,player_id='unknown')).status_code==400
    assert put(c,url,dict(body,publication=0)).status_code==409
    assert c.put(url,json=body).status_code==415
    assert put(c,url,body,Origin='http://elsewhere').status_code==403
