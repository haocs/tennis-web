import copy
import json
from concurrent.futures import ThreadPoolExecutor
from test_app import setup,ID,CLIP,put


def test_video_and_clip_hearts_are_independent_and_legacy_favorites_remain(setup):
    app,root,data=setup;c=app.test_client()
    assert put(c,f'/api/videos/{ID}/likes/video',dict(liked=True,version=0)).status_code==200
    assert put(c,f'/api/videos/{ID}/likes/{CLIP}',dict(liked=True,version=0,publication=data['published_at'])).status_code==200
    assert put(c,f'/api/videos/{ID}/likes/video',dict(liked=False,version=1)).status_code==200
    current=c.get('/api/sessions/'+ID).json
    assert not current['like']['value'] and current['clips'][0]['like']['value']
    assert put(c,f'/api/sessions/{ID}/likes/session',dict(liked=True,version=2)).status_code==200
    assert c.get('/api/sessions/'+ID).json['like']['value']
    assert put(c,f'/api/videos/{ID}/likes/{CLIP}',dict(liked=False,version=1,publication=data['published_at']-1)).status_code==409
    assert c.get('/api/sessions/'+ID).json['clips'][0]['like']['value']


def test_clip_details_are_output_bound_and_cannot_edit_video_or_session(setup):
    app,root,data=setup;c=app.test_client()
    practice=c.get('/api/practices').json['sessions'][0]
    url=f'/api/videos/{ID}/clips/{CLIP}/details';body=dict(title='Backhand recovery',version=0,publication=data['published_at'])
    assert put(c,url,body).status_code==200
    current=c.get('/api/sessions/'+ID).json;old_entity=current['clips'][0]['entity_id']
    assert current['clips'][0]['title']=='Backhand recovery' and current['title']==data['title']
    assert c.get('/api/entities/clips/'+old_entity).json['title']=='Backhand recovery'
    assert c.get('/api/practices').json['sessions'][0]['metadata']==practice['metadata']
    changed=copy.deepcopy(data);changed['published_at']+=1
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(changed))
    assert put(c,url,{**body,'version':1,'title':'Must not transfer'}).status_code==409
    current=c.get('/api/sessions/'+ID).json
    assert current['clips'][0]['title']=='Clip 01' and current['clips'][0]['details']['version']==0
    assert c.get('/api/entities/clips/'+old_entity).json['title']=='Backhand recovery'
    assert put(c,f'/api/videos/{ID}/details',dict(title='Edited video',location='Court',version=0)).status_code==200
    assert c.get('/api/sessions/'+ID).json['clips'][0]['title']=='Clip 01'


def test_clip_details_validation_and_concurrent_writes(setup):
    app,root,data=setup;c=app.test_client();url=f'/api/videos/{ID}/clips/{CLIP}/details'
    body=dict(title='One cut',version=0,publication=data['published_at'])
    for patch in (dict(title=''),dict(title='x'*161),dict(version=True),dict(location='Not clip metadata')):
        assert put(c,url,{**body,**patch}).status_code==400
    assert put(c,url,{**body,'publication':0}).status_code==409
    with ThreadPoolExecutor(2) as pool:
        codes=list(pool.map(lambda _:put(app.test_client(),url,body).status_code,range(2)))
    assert sorted(codes)==[200,409]
