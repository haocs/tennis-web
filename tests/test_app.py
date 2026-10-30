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
