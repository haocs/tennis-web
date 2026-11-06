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


def _down(client,data,clip_id=None,value=True,version=0,key=None):
    body=dict(publication=data['published_at'],subject_type='clip' if clip_id else 'video_version',field='less_useful',value=value,version=version)
    if clip_id:body['clip_id']=clip_id
    if key:body['key']=key
    return put(client,f'/api/entities/videos/{ID}/metadata',body)


def test_up_down_are_exclusive_for_clip_and_video_with_history(setup):
    app,root,data=setup;c=app.test_client()
    for item in (CLIP,'video'):
        url=f'/api/videos/{ID}/likes/{item}'
        up=put(c,url,dict(liked=True,version=0,publication=data['published_at']))
        assert up.status_code==200
        down=_down(c,data,CLIP if item==CLIP else None)
        assert down.status_code==200
        current=c.get('/api/sessions/'+ID).json
        assert not (current['clips'][0] if item==CLIP else current)['like']['value']
        field=c.get('/api/entities/videos/'+ID+'/metadata').json['metadata']
        assert any(d['less_useful'] and d['subject']['type']==('clip' if item==CLIP else 'video_version') for d in field)
        like=(current['clips'][0] if item==CLIP else current)['like']
        # A stale positive write cannot undo a newer downvote unnoticed.
        assert put(c,url,dict(liked=True,version=up.json['version'],publication=data['published_at'])).status_code==409
        yes=put(c,url,dict(liked=True,version=like['version'],publication=data['published_at']))
        assert yes.status_code==200
        record=app.extensions['annotations'].get(down.json['key'])
        assert record['status']=='archived'
        revisions=c.get('/api/annotations/'+record['id']+'/revisions').json['revisions']
        assert [r['status'] for r in revisions]==['active','archived']
        neutral=put(c,url,dict(liked=False,version=yes.json['version'],publication=data['published_at']))
        assert neutral.status_code==200 and not neutral.json['value']


def test_concurrent_opposite_reactions_never_leave_both_active(setup):
    app,root,data=setup
    def vote(direction):
        client=app.test_client()
        return (_down(client,data,CLIP) if direction=='down' else put(client,f'/api/videos/{ID}/likes/{CLIP}',dict(liked=True,version=0,publication=data['published_at']))).status_code
    with ThreadPoolExecutor(2) as pool:codes=list(pool.map(vote,['up','down']))
    assert all(code in (200,409) for code in codes)
    current=app.test_client().get('/api/sessions/'+ID).json
    negative=any(a['kind']=='usefulness' and a['status']=='active' for a in app.extensions['annotations'].listing(ID))
    assert not (current['clips'][0]['like']['value'] and negative)


def test_legacy_likes_keep_versions_and_existing_downvote_wins_without_read_migration(setup):
    app,root,data=setup;c=app.test_client();state=app.extensions['state']
    state.update('like:'+ID+':'+CLIP,True,0)
    assert c.get('/api/sessions/'+ID).json['clips'][0]['like']==dict(value=True,version=1)
    down=_down(c,data,CLIP)
    assert down.status_code==200
    # Simulate a pre-upgrade snapshot with independent fields, retaining its history.
    store=app.extensions['annotations']
    with store.db() as db:db.execute('DELETE FROM reaction_likes')
    assert not c.get('/api/sessions/'+ID).json['clips'][0]['like']['value']
    with store.db() as db:assert db.execute('SELECT count(*) FROM reaction_likes').fetchone()[0]==0
    cleared=_down(c,data,CLIP,False,down.json['version'],down.json['key'])
    assert cleared.status_code==200
    assert not c.get('/api/sessions/'+ID).json['clips'][0]['like']['value']
    assert state.all()['like:'+ID+':'+CLIP]==dict(value=True,version=1)


def test_reaction_restart_version_boundary_and_transaction_rollback(setup,monkeypatch):
    from app import create_app
    app,root,data=setup;c=app.test_client();down=_down(c,data)
    assert down.status_code==200
    store=app.extensions['annotations'];original=store._save_like
    def fail(*args,**kwargs):raise RuntimeError('simulated interruption')
    monkeypatch.setattr(store,'_save_like',fail)
    import pytest
    with pytest.raises(RuntimeError):put(c,f'/api/videos/{ID}/likes/video',dict(liked=True,version=1))
    assert store.get(down.json['key'])['status']=='active'
    assert len(store.revisions(down.json['key']))==1
    monkeypatch.setattr(store,'_save_like',original)
    restored=create_app({'TESTING':True,'LIBRARY_ROOT':str(root),'DATA_ROOT':app.config['DATA_ROOT']})
    assert not restored.test_client().get('/api/sessions/'+ID).json['like']['value']
    changed=copy.deepcopy(data);changed['published_at']+=1
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(changed))
    current=restored.test_client().get('/api/entities/videos/'+ID+'/metadata').json['metadata']
    assert not any(d['less_useful'] for d in current)
    assert store.get(down.json['key'])['status']=='active'  # retained historical judgment
