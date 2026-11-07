from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from storage import Library, storage_path

ID='a'*64
CLIP='b'*24


@pytest.fixture
def setup(tmp_path):
    root=tmp_path/'library';media=root/'media'/ID;metadata=root/'metadata/sessions'
    (media/'Clips').mkdir(parents=True);(metadata/ID).mkdir(parents=True)
    data={"schema_version":1,"id":ID,"title":"Practice <script>","source":{"name":"test.mov","sha256":ID,"recorded_at":"2026-10-24T17:00:00-0700","duration_seconds":20,"location":None},"published_at":time.time(),"media_directory":ID,
          "video":{"file":"Practice.mp4","poster":"poster.jpg","duration_seconds":10,"width":1920,"height":1080,"hdr":False},
          "clips":[{"id":CLIP,"index":1,"file":"Clips/clip-001.mp4","original_start_seconds":5,"original_end_seconds":15,"duration_seconds":10,"playback_start_seconds":0,"playback_end_seconds":10}],"removed_seconds":10,"kept_seconds":10,"algorithm":"test"}
    (metadata/f'{ID}.json').write_text(json.dumps(data))
    (media/'Practice.mp4').write_bytes(b'0123456789abcdefghijklmnopqrstuvwxyz')
    (media/'Clips/clip-001.mp4').write_bytes(b'clip video')
    (metadata/ID/'edit-timeline.csv').write_text('action,start,end\npreserved,5,15\n')
    (metadata/ID/'Review.html').write_text('<video src="Practice.mp4"></video><a href="Clips/clip-001.mp4">Clip</a>')
    app=create_app({'TESTING':True,'LIBRARY_ROOT':str(root),'DATA_ROOT':str(tmp_path/'data')})
    return app,root,data


def put(client,path,body,**kwargs):
    return client.put(path,json=body,headers={'X-Tennis-Request':'1',**kwargs})


def test_library_and_byte_range_seeking(setup):
    app,root,data=setup;c=app.test_client()
    assert c.get('/').status_code==200
    assert c.get('/healthz').json=={'status':'ok'}
    assert len(c.get('/api/sessions').json['sessions'])==1
    r=c.get(f'/media/{ID}/Practice.mp4',headers={'Range':'bytes=2-6'})
    assert r.status_code==206 and r.data==b'23456'
    assert r.headers['Content-Range']=='bytes 2-6/36'
    assert c.head(f'/media/{ID}/Practice.mp4').status_code==200
    assert c.get(f'/media/{ID}/Practice.mp4',headers={'Range':'bytes=100-200'}).status_code==416


def test_only_declared_shared_media_can_be_served(setup,tmp_path):
    app,root,data=setup;c=app.test_client()
    private=tmp_path/'private.mov';private.write_bytes(b'private original')
    assert c.get(f'/media/{ID}/session.json').status_code==404
    assert c.get(f'/media/{ID}/../../private.mov').status_code==404
    (root/'media'/ID/'Practice.mp4').unlink()
    (root/'media'/ID/'Practice.mp4').symlink_to(private)
    assert c.get(f'/media/{ID}/Practice.mp4').status_code==404
    assert c.get('/api/sessions/'+'c'*64).status_code==404


def test_video_downloads_are_attachments_and_keep_media_boundaries(setup, tmp_path):
    app, root, data = setup
    client = app.test_client()
    for filename, expected, name in [
        ('Practice.mp4', b'0123456789abcdefghijklmnopqrstuvwxyz', 'test-Practice.mp4'),
        ('Clips/clip-001.mp4', b'clip video', 'test-clip-001.mp4'),
    ]:
        response = client.get(f'/media/{ID}/{filename}?download=1')
        assert response.data == expected
        assert response.mimetype == 'video/mp4'
        assert response.headers['Content-Disposition'] == f'attachment; filename={name}'
        assert client.head(f'/media/{ID}/{filename}?download=1').status_code == 200
    assert client.get(f'/media/{ID}/Practice.mp4?download=1', headers={'Range': 'bytes=2-6'}).data == b'23456'
    assert client.get(f'/media/{ID}/Practice.mp4').headers['Content-Disposition'].startswith('inline;')
    (root/'media'/ID/'poster.jpg').write_bytes(b'poster')
    assert client.get(f'/media/{ID}/poster.jpg?download=1').status_code == 404
    assert client.get(f'/media/{ID}/session.json?download=1').status_code == 404
    private = tmp_path/'private.mp4'
    private.write_bytes(b'private')
    (root/'media'/ID/'Practice.mp4').unlink()
    (root/'media'/ID/'Practice.mp4').symlink_to(private)
    assert client.get(f'/media/{ID}/Practice.mp4?download=1').status_code == 404


def test_like_persistence_and_stale_update_conflict(setup):
    app,root,data=setup;c=app.test_client();url=f'/api/sessions/{ID}/likes/{CLIP}'
    assert put(c,url,{'liked':True,'version':0}).json=={'value':True,'version':1}
    result=put(c,url,{'liked':False,'version':0})
    assert result.status_code==409 and result.json['current']=={'value':True,'version':1}
    fresh=create_app({'TESTING':True,'LIBRARY_ROOT':str(root),'DATA_ROOT':app.config['DATA_ROOT']})
    assert fresh.test_client().get(f'/api/sessions/{ID}').json['clips'][0]['like']=={'value':True,'version':1}
    assert put(c,url,{'liked':False,'version':1}).status_code==200


def test_concurrent_updates_have_exactly_one_winner(setup):
    app,root,data=setup
    def change(_):
        with app.test_client() as c:
            return put(c,f'/api/sessions/{ID}/likes/session',{'liked':True,'version':0}).status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes=list(pool.map(change,range(16)))
    assert codes.count(200)==1 and codes.count(409)==15
    assert app.test_client().get(f'/api/sessions/{ID}').json['like']['version']==1


def test_metadata_edit_conflicts_do_not_lose_other_changes(setup):
    app,root,data=setup;c=app.test_client();url=f'/api/sessions/{ID}/metadata'
    assert put(c,url,{'title':'Evening practice','location':'Local court','version':0}).status_code==200
    assert put(c,url,{'title':'Old form','location':'','version':0}).status_code==409
    saved=c.get(f'/api/sessions/{ID}').json
    assert saved['title']=='Evening practice' and saved['location_label']=='Local court'
    assert json.loads((root/'metadata/sessions'/f'{ID}.json').read_text())['title']=='Practice <script>'


def test_cross_origin_and_invalid_mutations_rejected(setup):
    app,root,data=setup;c=app.test_client();url=f'/api/sessions/{ID}/likes/session'
    assert c.put(url,json={'liked':True,'version':0}).status_code==415
    assert put(c,url,{'liked':True,'version':0},Origin='https://unrelated.example').status_code==403
    assert put(c,url,{'liked':'yes','version':0}).status_code==400
    assert put(c,url,{'liked':True,'version':True}).status_code==400
    assert put(c,f'/api/sessions/{ID}/likes/not-a-clip',{'liked':True,'version':0}).status_code==404


def test_status_is_stale_aware_and_does_not_expose_private_paths(setup):
    app,root,data=setup;c=app.test_client()
    assert c.get('/api/status').json['available'] is False
    raw={'heartbeat':time.time()-130,'stage':'Exporting clips','current_video':'/private/Originals/test.mov','files':[{'path':'/private/Originals/test.mov','status':'processing','output':'/private/result','error':'private path error'}],'scan_error':'private path error','pid':123}
    (root/'metadata/Processing-status.json').write_text(json.dumps(raw))
    result=c.get('/api/status');assert result.json['stale'] is True
    assert '/private' not in result.get_data(as_text=True) and 'pid' not in result.json
    assert result.json['files'][0]['name']=='test.mov'


def test_original_review_and_artifacts_use_shared_routes(setup):
    app,root,data=setup;c=app.test_client()
    result=c.get(f'/review/{ID}/original')
    assert f'/media/{ID}/Practice.mp4' in result.text
    assert "default-src 'none'" in result.headers['Content-Security-Policy']
    assert c.get(f'/api/sessions/{ID}/artifacts/edit-timeline.csv').status_code==200
    assert c.get(f'/api/sessions/{ID}/artifacts/session.json').status_code==404


def test_invalid_manifest_is_skipped_and_storage_links_are_explicit(setup):
    app,root,data=setup
    (root/'metadata/sessions'/('c'*64+'.json')).write_text('{}')
    response=app.test_client().get('/api/sessions').json
    assert response['invalid_sessions']==1 and len(response['sessions'])==1
    assert storage_path(root.as_uri())==root
    with pytest.raises(ValueError):storage_path('smb://nas/private')


def test_consistent_backup_restores_shared_preferences(setup,tmp_path):
    from manage import backup
    app,root,data=setup;c=app.test_client()
    assert put(c,f'/api/sessions/{ID}/likes/session',{'liked':True,'version':0}).status_code==200
    destination=tmp_path/'restored/website.sqlite3'
    backup(Path(app.config['DATA_ROOT'])/'website.sqlite3',destination)
    backup(Path(app.config['DATA_ROOT'])/'annotations.sqlite3',destination.parent/'annotations.sqlite3')
    restored=create_app({'TESTING':True,'LIBRARY_ROOT':str(root),'DATA_ROOT':str(destination.parent)})
    assert restored.test_client().get(f'/api/sessions/{ID}').json['like']=={'value':True,'version':1}
    with pytest.raises(ValueError):backup(Path(app.config['DATA_ROOT'])/'website.sqlite3',destination)


def test_home_groups_keep_original_video_review_and_manifest_unchanged(setup):
    app, root, original = setup
    other = copy.deepcopy(original)
    other['id'] = 'c' * 64
    other['source']['sha256'] = other['id']
    other['source']['recorded_at'] = '2026-10-24T17:20:00-0700'
    other['published_at'] = original['published_at'] - 100000
    (root/'metadata/sessions'/f"{other['id']}.json").write_text(json.dumps(other))
    client = app.test_client()
    data = client.get('/api/sessions').json
    assert len(data['sessions']) == 2 and len(data['practice_groups']) == 1
    assert data['practice_groups'][0]['recording_ids'] == [ID, other['id']]
    assert client.get(f'/api/sessions/{ID}').json['id'] == ID
    assert client.get(f'/review/{ID}').status_code == 200
    assert json.loads((root/'metadata/sessions'/f'{ID}.json').read_text()) == original


def test_configured_nas_addresses_are_display_only_and_url_encoded(setup):
    app, root, _ = setup
    configured = create_app({'TESTING':True, 'LIBRARY_ROOT':str(root),
                            'DATA_ROOT':app.config['DATA_ROOT'],
                            'LIBRARY_SMB_URL':'smb://nas/Shared Tennis/library/'})
    data = configured.test_client().get(f'/api/sessions/{ID}').json
    assert data['clips'][0]['nas_url'] == f'smb://nas/Shared%20Tennis/library/media/{ID}/Clips/clip-001.mp4'
    assert data['video']['nas_url'] == f'smb://nas/Shared%20Tennis/library/media/{ID}/Practice.mp4'
    assert app.test_client().get(f'/api/sessions/{ID}').json['clips'][0]['nas_url'] is None
    for value in ['https://nas/share', 'smb://user:secret@nas/share', 'smb://nas/share?token=secret']:
        with pytest.raises(ValueError):
            create_app({'LIBRARY_ROOT':str(root),'DATA_ROOT':app.config['DATA_ROOT'],'LIBRARY_SMB_URL':value})


def test_separate_media_root_requires_matching_nas_address(setup, tmp_path):
    app, root, _ = setup
    config={'LIBRARY_ROOT':str(root),'MEDIA_ROOT':str(tmp_path/'other-media'),
            'DATA_ROOT':app.config['DATA_ROOT'],'LIBRARY_SMB_URL':'smb://nas/library'}
    separate=create_app(config)
    assert separate.test_client().get(f'/api/sessions/{ID}').json['video']['nas_url'] is None
    separate=create_app({**config,'MEDIA_SMB_URL':'smb://nas/other-media'})
    assert separate.test_client().get(f'/api/sessions/{ID}').json['clips'][0]['nas_url'].startswith('smb://nas/other-media/')


def court_create(client, **fields):
    return client.post('/api/courts',json={'name':'Local court','latitude':12.0,'longitude':34.0,'radius_m':150,**fields},headers={'X-Tennis-Request':'1'})


def test_saved_court_gps_matching_and_renaming_preserves_video_names(setup):
    app,root,data=setup
    data['source']['location']={'latitude':12.0001,'longitude':34.0001,'altitude_m':18}
    manifest=root/'metadata/sessions'/f'{ID}.json'; manifest.write_text(json.dumps(data))
    c=app.test_client(); response=court_create(c);assert response.status_code==201
    court=response.json
    group=c.get('/api/sessions').json['practice_groups'][0]
    assert group['court_status']=='gps_match' and group['court']['id']==court['id']
    assert group['title']=='2026/10/24 Evening'
    assert group['location']['altitude_m']==18
    assert c.get(f'/api/sessions/{ID}').json['title']==data['title']
    renamed=c.put('/api/courts/'+court['id'],json={k:v for k,v in {**court,'name':'Evening courts'}.items() if k!='id'},headers={'X-Tennis-Request':'1'})
    assert renamed.status_code==200
    renamed_group=c.get('/api/sessions').json['practice_groups'][0]
    assert renamed_group['title']=='2026/10/24 Evening'
    assert renamed_group['court_name']=='Evening courts'
    assert json.loads(manifest.read_text())==data
    restored=create_app({'LIBRARY_ROOT':str(root),'DATA_ROOT':app.config['DATA_ROOT']})
    assert restored.test_client().get('/api/courts').json['courts'][0]['name']=='Evening courts'


def test_recording_and_liked_data_share_practice_court_resolution(setup):
    app, root, data = setup
    data['source']['location'] = {'latitude':12, 'longitude':34}
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    other = copy.deepcopy(data)
    other['id'] = 'c'*64
    other['source']['sha256'] = other['id']
    other['source']['recorded_at'] = '2026-10-24T17:01:00-0700'
    other['source']['location'] = None
    (root/'metadata/sessions'/f"{other['id']}.json").write_text(json.dumps(other))
    client = app.test_client()
    court = court_create(client, name='Park <West>').json
    # A sibling without GPS must still use the same resolved practice venue.
    collection = client.get('/api/sessions').json
    assert collection['practice_groups'][0]['court']['id'] == court['id']
    for recording in collection['sessions']:
        single = client.get('/api/sessions/'+recording['id']).json
        assert single['court'] == recording['court'] == court
        assert single['court_status'] == 'gps_match'
        assert single['source']['location'] == recording['source']['location']
    put(client, f'/api/sessions/{ID}/likes/{CLIP}', {'liked':True,'version':0})
    assert client.get('/api/sessions').json['sessions'][0]['court']['name'] == 'Park <West>'
    # A manual choice must win over a GPS match on every page.
    manual = court_create(client, name='Indoor court', latitude=None, longitude=None).json
    group = collection['practice_groups'][0]
    assert put(client, f'/api/practices/{group["id"]}/court',
               {'court_id':manual['id'], 'versions':group['court_versions']}).status_code == 200
    assert client.get('/api/sessions/'+other['id']).json['court']['name'] == 'Indoor court'


def test_recording_court_does_not_guess_when_gps_is_ambiguous(setup):
    app, root, data = setup
    data['source']['location'] = {'latitude':12, 'longitude':34}
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    client = app.test_client()
    court_create(client, name='One')
    court_create(client, name='Two')
    single = client.get('/api/sessions/'+ID).json
    assert single['court'] is None and single['court_status'] == 'ambiguous'
    assert single['source']['location'] == data['source']['location']


def test_nearby_multiple_courts_need_confirmation_and_manual_choice_wins(setup):
    app,root,data=setup;c=app.test_client()
    data['source']['location']={'latitude':12,'longitude':34}
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    a=court_create(c).json;b=court_create(c,name='Other court').json
    group=c.get('/api/sessions').json['practice_groups'][0]
    assert group['court_status']=='ambiguous' and group['court'] is None
    assert len(group['court_candidates'])==2
    url=f'/api/practices/{group["id"]}/court'
    assert put(c,url,{'court_id':b['id'],'versions':group['court_versions']}).status_code==200
    selected=c.get('/api/sessions').json['practice_groups'][0]
    assert selected['court']['id']==b['id'] and selected['court_status']=='selected'
    assert put(c,url,{'court_id':a['id'],'versions':group['court_versions']}).status_code==409
    assert put(c,url,{'court_id':None,'versions':selected['court_versions']}).status_code==200
    assert c.get('/api/sessions').json['practice_groups'][0]['court_status']=='ambiguous'


def test_assignment_all_or_nothing_when_another_visitor_changes_one_recording(setup):
    app,root,data=setup;c=app.test_client()
    other=copy.deepcopy(data);other['id']='c'*64;other['source']['recorded_at']='2026-10-24T17:01:00-0700'
    (root/'metadata/sessions'/f"{other['id']}.json").write_text(json.dumps(other))
    court=court_create(c).json;group=c.get('/api/sessions').json['practice_groups'][0]
    app.extensions['state'].update('court-assignment:'+other['id'],{'court_id':court['id']},0)
    assert put(c,f'/api/practices/{group["id"]}/court',{'court_id':court['id'],'versions':group['court_versions']}).status_code==409
    assert 'court-assignment:'+ID not in app.extensions['state'].all()
    assert put(c,f'/api/practices/{group["id"]}/court',{'court_id':court['id'],'versions':{ID:0}}).status_code==409


def test_saved_court_validation_duplicate_names_and_stale_edits(setup):
    app,root,_=setup;c=app.test_client()
    first=court_create(c).json
    assert court_create(c,name='LOCAL COURT').status_code==409
    for changes in [{'latitude':91},{'longitude':None},{'radius_m':0},{'radius_m':float('nan')},{'name':' '},{'latitude':True}]:
        assert court_create(c,**changes).status_code==400
    fields={k:v for k,v in first.items() if k!='id'}
    assert c.put('/api/courts/'+first['id'],json=fields,headers={'X-Tennis-Request':'1'}).status_code==200
    assert c.put('/api/courts/'+first['id'],json=fields,headers={'X-Tennis-Request':'1'}).status_code==409
    assert c.post('/api/courts',json={'name':'No request header'}).status_code==415
    assert c.post('/api/courts',json={'name':'Cross origin'},headers={'X-Tennis-Request':'1','Origin':'https://example.invalid'}).status_code==403


def test_court_names_without_gps_can_be_selected_without_changing_review(setup):
    app,root,data=setup;c=app.test_client()
    court=court_create(c,latitude=None,longitude=None).json
    group=c.get('/api/sessions').json['practice_groups'][0]
    assert group['court_status']=='unknown'
    assert put(c,f'/api/practices/{ID}/court',{'court_id':court['id'],'versions':group['court_versions']}).status_code==200
    assert c.get('/api/sessions').json['practice_groups'][0]['court']['id']==court['id']
    assert c.get(f'/api/sessions/{ID}').json['location_label']==''


def test_concurrent_visitors_do_not_create_duplicate_court_names(setup):
    app,root,data=setup
    def save(_):
        with app.test_client() as client:
            return court_create(client).status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        codes=list(pool.map(save,range(8)))
    assert codes.count(201)==1 and codes.count(409)==7
    assert len(app.test_client().get('/api/courts').json['courts'])==1


def test_versioned_processing_generation_preserves_review_and_likes(setup):
    app,root,data=setup;client=app.test_client();generation='d'*32
    put(client,f'/api/sessions/{ID}/likes/session',{'liked':True,'version':0})
    (root/'media'/ID).rename(root/'media'/generation)
    (root/'metadata/runs').mkdir()
    (root/'metadata/sessions'/ID).rename(root/'metadata/runs'/generation)
    data.update(media_directory=generation,artifact_directory=generation)
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    assert client.get(f'/media/{ID}/Practice.mp4').status_code==200
    assert client.get(f'/review/{ID}').status_code==200
    assert client.get(f'/api/sessions/{ID}').json['like']['value'] is True
    assert Library(root).artifact(ID,'Review.html')==root/'metadata/runs'/generation/'Review.html'
    data['artifact_directory']='../private'
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    assert client.get(f'/review/{ID}').status_code==503


def test_court_address_round_trip_legacy_edits_and_stale_writers(setup):
    app,root,_=setup;c=app.test_client()
    court=court_create(c,address='  123 Court Lane, Test City  ').json
    assert court['address']=='123 Court Lane, Test City'
    url='/api/courts/'+court['id']
    fields={k:v for k,v in court.items() if k!='id'}
    updated=c.put(url,json={**fields,'address':'456 Park Road'},headers={'X-Tennis-Request':'1'})
    assert updated.status_code==200 and updated.json['address']=='456 Park Road'
    assert c.put(url,json={**fields,'address':'Stale address'},headers={'X-Tennis-Request':'1'}).status_code==409
    legacy={k:v for k,v in updated.json.items() if k not in {'id','address'}}
    assert c.put(url,json=legacy,headers={'X-Tennis-Request':'1'}).json['address']=='456 Park Road'
    restored=create_app({'LIBRARY_ROOT':str(root),'DATA_ROOT':app.config['DATA_ROOT']})
    assert restored.test_client().get('/api/courts').json['courts'][0]['address']=='456 Park Road'
    latest=c.get('/api/courts').json['courts'][0]
    cleared=c.put(url,json={**{k:v for k,v in latest.items() if k!='id'},'address':''},headers={'X-Tennis-Request':'1'})
    assert cleared.json['address']==''


def test_existing_courts_without_address_remain_editable(setup):
    app,_,_=setup;c=app.test_client()
    identity='e'*32
    app.extensions['state'].save_court(identity,{'name':'Old court','latitude':None,'longitude':None,'altitude_m':None,'radius_m':150},0)
    court=c.get('/api/courts').json['courts'][0]
    assert court['address']==''
    assert c.put('/api/courts/'+identity,json={k:v for k,v in court.items() if k!='id'},headers={'X-Tennis-Request':'1'}).status_code==200


@pytest.mark.parametrize('address',[None,True,123,[],{},'x'*501])
def test_invalid_court_address_is_rejected(setup,address):
    app,_,_=setup
    response=court_create(app.test_client(),address=address)
    assert response.status_code==400 and 'address' in response.json['error']


def test_processing_room_route_and_configured_portal_link(setup):
    app,root,_=setup
    client=app.test_client()
    assert client.get('/processing').status_code==200
    assert b'data-processing-url="/processing"' in client.get('/').data
    configured=create_app({'LIBRARY_ROOT':str(root),'DATA_ROOT':app.config['DATA_ROOT'],
                           'PROCESSING_ROOM_URL':'http://processor.example:8089/'})
    assert b'data-processing-url="http://processor.example:8089/"' in configured.test_client().get('/').data


@pytest.mark.parametrize('url',['javascript:alert(1)','//example.invalid','http://user:password@example.invalid','',None])
def test_processing_room_url_requires_a_safe_navigation_target(setup,url):
    app,root,_=setup
    with pytest.raises(ValueError,match='Processing Room URL'):
        create_app({'LIBRARY_ROOT':str(root),'DATA_ROOT':app.config['DATA_ROOT'],'PROCESSING_ROOM_URL':url})


def test_clip_study_legacy_context_and_publication_conflict(setup):
    app, root, data = setup
    client = app.test_client()
    url = f'/api/sessions/{ID}/clips/{CLIP}/study'
    result = client.get(url)
    assert result.status_code == 200 and result.json['observations'] == []
    assert result.json['algorithm'] == 'test'
    assert result.json['original_start_seconds'] == 5
    assert result.headers['Cache-Control'] == 'no-store'
    assert client.get(url+'?publication='+str(data['published_at'])).status_code == 200
    assert client.get(url+'?publication=0').status_code == 409
    for invalid in ('nan', 'inf', 'not-a-number'):
        assert client.get(url+'?publication='+invalid).status_code == 400
    assert client.get(f'/api/sessions/{ID}/clips/'+('d'*24)+'/study').status_code == 404


def test_clip_study_filters_observations_without_exposing_raw_fields(setup):
    app, root, data = setup
    valid = {'label': '<script>Candidate movement</script>', 'detail': 'Pose movement, not verified ball contact',
             'original_start_seconds': 6, 'original_end_seconds': 8, 'score': 2.5, 'score_kind': 'margin',
             'private_path': '/private/original.mov', 'raw_log': 'secret'}
    invalid = [None, {}, {**valid, 'original_start_seconds': True},
               {**valid, 'original_start_seconds': 4}, {**valid, 'original_end_seconds': 17},
               {**valid, 'original_end_seconds': float('nan')}, {**valid, 'label': 'x'*121}]
    data['clips'][0]['processor_observations'] = invalid + [valid]
    path = root/'metadata/sessions'/f'{ID}.json'
    path.write_text(json.dumps(data))
    result = app.test_client().get(f'/api/sessions/{ID}/clips/{CLIP}/study')
    assert len(result.json['observations']) == 1
    observation = result.json['observations'][0]
    assert observation['local_start_seconds'] == 1 and observation['local_end_seconds'] == 3
    assert observation['score'] == 2.5 and observation['score_kind'] == 'margin'
    assert '/private' not in result.text and 'secret' not in result.text
    assert '<script>' in observation['label']  # Returned as data; frontend uses textContent.
    data['clips'][0]['processor_observations'] = [{**valid, 'score': 5, 'score_kind': 'probability'}]*30
    path.write_text(json.dumps(data))
    observations = app.test_client().get(f'/api/sessions/{ID}/clips/{CLIP}/study').json['observations']
    assert len(observations) == 20 and all('score' not in o for o in observations)
    data['clips'][0]['processor_observations'] = 'not a list'
    path.write_text(json.dumps(data))
    assert app.test_client().get(f'/api/sessions/{ID}/clips/{CLIP}/study').json['observations'] == []


def test_versioned_media_cannot_silently_change_under_study(setup):
    app, root, data = setup
    client = app.test_client()
    url = f'/media/{ID}/Clips/clip-001.mp4?v={data["published_at"]}'
    assert client.get(url).status_code == 200
    data['published_at'] += 10
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    assert client.get(url).status_code == 409
    assert client.get(url+'&download=1').status_code == 409
    assert client.get(f'/media/{ID}/Clips/clip-001.mp4?v={data["published_at"]}').status_code == 200
    assert client.get(f'/media/{ID}/Clips/clip-001.mp4').status_code == 200


def test_autofocus_vendored_assets_mime_types_and_csp(setup):
    import hashlib
    app, _, _ = setup
    client = app.test_client()
    page = client.get(f'/review/{ID}')
    assert page.status_code == 200
    csp = page.headers['Content-Security-Policy']
    assert "script-src 'self' 'wasm-unsafe-eval'" in csp
    assert "worker-src 'self'" in csp
    assert "'unsafe-eval'" not in csp

    worker = client.get('/static/autofocus-worker.js')
    assert worker.status_code == 200 and worker.mimetype in {'text/javascript', 'application/javascript'}

    manifest_res = client.get('/static/vendor/mediapipe/MANIFEST.json')
    assert manifest_res.status_code == 200
    manifest = json.loads(manifest_res.data)
    assert manifest['license'] == 'Apache-2.0'
    assert client.get('/static/vendor/mediapipe/LICENSE').status_code == 200

    expected_mimes = {
        'vision_bundle.js': {'text/javascript', 'application/javascript'},
        'vision_wasm_internal.js': {'text/javascript', 'application/javascript'},
        'vision_wasm_internal.wasm': {'application/wasm'},
        'efficientdet_lite0.tflite': {'application/octet-stream'},
    }
    for filename, entry in manifest['files'].items():
        res = client.get(f'/static/vendor/mediapipe/{filename}')
        assert res.status_code == 200
        assert res.mimetype in expected_mimes[filename]
        assert len(res.data) == entry['bytes']
        assert hashlib.sha256(res.data).hexdigest() == entry['sha256']

    from fnmatch import fnmatch
    dockerignore_lines = [
        line.strip()
        for line in (Path(__file__).resolve().parents[1] / '.dockerignore').read_text().splitlines()
        if line.strip() and not line.strip().startswith('#')
    ]
    def is_ignored(rel_path: str) -> bool:
        ignored = False
        for pat in dockerignore_lines:
            neg = pat.startswith('!')
            rule = pat[1:].rstrip('/') if neg else pat.rstrip('/')
            if rule == '**' or fnmatch(rel_path, rule) or fnmatch(rel_path, f'{rule}/*'):
                ignored = not neg
        return ignored

    for required in [
        'static/autofocus-core.js',
        'static/autofocus-worker.js',
        'static/vendor/mediapipe/LICENSE',
        'static/vendor/mediapipe/MANIFEST.json',
        *tuple(f'static/vendor/mediapipe/{name}' for name in manifest['files']),
    ]:
        assert not is_ignored(required), f'{required} must not be excluded by .dockerignore'
    assert is_ignored('tests/ui_player.py')
    assert is_ignored('docs/AUTO-FOCUS.md')


def test_session_api_projects_active_in_scope_clip_highlights_without_archived_or_out_of_range(setup):
    app, root, data = setup
    second_clip = 'c' * 24
    data['clips'].append({
        'id': second_clip,
        'index': 2,
        'file': 'Clips/clip-002.mp4',
        'original_start_seconds': 16,
        'original_end_seconds': 20,
        'duration_seconds': 4,
        'playback_start_seconds': 10,
        'playback_end_seconds': 14,
    })
    data['video']['duration_seconds'] = 14
    data['kept_seconds'] = 14
    (root / 'media' / ID / 'Clips/clip-002.mp4').write_bytes(b'clip video 2')
    (root / 'metadata/sessions' / f'{ID}.json').write_text(json.dumps(data))
    client = app.test_client()
    headers = {'X-Tennis-Request': '1'}
    pub = data['published_at']

    # Active point highlight inside clip 1 (original 5..15)
    r1 = client.post(
        '/api/annotations',
        json={'source_id': ID, 'publication': pub, 'kind': 'highlight', 'target': {'scope': 'point', 'start_seconds': 7.5}, 'body': {'text': 'Forehand winner'}, 'request_id': 'req-hl-001'},
        headers=headers,
    )
    assert r1.status_code == 201
    # Second active interval highlight inside clip 1
    r2 = client.post(
        '/api/annotations',
        json={'source_id': ID, 'publication': pub, 'kind': 'highlight', 'target': {'scope': 'interval', 'start_seconds': 9.0, 'end_seconds': 11.0}, 'body': {'text': 'Net finish'}, 'request_id': 'req-hl-002'},
        headers=headers,
    )
    assert r2.status_code == 201
    # Highlight in cut region (original 1.0, outside 5..15 and 16..20)
    r_cut = client.post(
        '/api/annotations',
        json={'source_id': ID, 'publication': pub, 'kind': 'highlight', 'target': {'scope': 'point', 'start_seconds': 1.0}, 'body': {'text': 'Cut region'}, 'request_id': 'req-hl-cut'},
        headers=headers,
    )
    assert r_cut.status_code == 201
    # Highlight on clip 2 that is then archived
    r_arch = client.post(
        '/api/annotations',
        json={'source_id': ID, 'publication': pub, 'kind': 'highlight', 'target': {'scope': 'point', 'start_seconds': 17.0}, 'body': {'text': 'Archived highlight'}, 'request_id': 'req-hl-arch'},
        headers=headers,
    )
    assert r_arch.status_code == 201
    arch_doc = r_arch.json
    assert put(client, f"/api/annotations/{arch_doc['id']}", {'publication': pub, 'version': arch_doc['version'], 'status': 'archived'}).status_code == 200
    # Regular note on clip 2 (not a highlight)
    r_note = client.post(
        '/api/annotations',
        json={'source_id': ID, 'publication': pub, 'kind': 'note', 'target': {'scope': 'point', 'start_seconds': 18.0}, 'body': {'text': 'Plain note'}, 'request_id': 'req-note-001'},
        headers=headers,
    )
    assert r_note.status_code == 201

    for payload in (client.get('/api/sessions').json['sessions'][0], client.get(f'/api/sessions/{ID}').json):
        c1, c2 = payload['clips']
        assert c1['id'] == CLIP and c1['highlighted'] is True and c1['highlight_count'] == 2
        assert [h['text'] for h in c1['highlights']] == ['Forehand winner', 'Net finish']
        assert c2['id'] == second_clip and c2['highlighted'] is False and c2['highlight_count'] == 0
        assert c2['highlights'] == []
