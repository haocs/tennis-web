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
    assert group['title']=='Local court · 2026/10/24 evening'
    assert group['location']['altitude_m']==18
    assert c.get(f'/api/sessions/{ID}').json['title']==data['title']
    renamed=c.put('/api/courts/'+court['id'],json={k:v for k,v in {**court,'name':'Evening courts'}.items() if k!='id'},headers={'X-Tennis-Request':'1'})
    assert renamed.status_code==200
    assert c.get('/api/sessions').json['practice_groups'][0]['title'].startswith('Evening courts · ')
    assert json.loads(manifest.read_text())==data
    restored=create_app({'LIBRARY_ROOT':str(root),'DATA_ROOT':app.config['DATA_ROOT']})
    assert restored.test_client().get('/api/courts').json['courts'][0]['name']=='Evening courts'


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
