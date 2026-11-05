import copy
import json
from concurrent.futures import ThreadPoolExecutor
from test_app import setup,ID,CLIP,put
from test_annotations import create as annotation
from test_players import create as player,roster


def review(app,data,identity,pid=None,start=6,end=8):
    q=app.extensions['coaching'];body=dict(source_id=ID,clip_id=CLIP,publication=data['published_at'],original_start=start,original_end=end)
    if pid:body['player_context']=dict(player_id=pid,profile_version=1)
    job=q.create('ai',body,identity);claim=q.claim('ai','test')
    q.worker_update(job['id'],claim['token'],'calling')
    q.worker_update(job['id'],claim['token'],'complete',dict(answer='Review '+identity))
    return job


def test_highlight_deduplicates_concurrent_creates_and_retarget_restore(setup):
    app,root,data=setup;c=app.test_client();target=dict(scope='point',start_seconds=8)
    def post(i):return annotation(app.test_client(),data,'highlight',target,dict(text=''),rid='highlight-race-'+str(i))
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(post,range(2)))
    assert [r.status_code for r in responses]==[201,201]
    a=responses[0].json;assert a['id']==responses[1].json['id']
    assert len(app.extensions['annotations'].listing(ID))==1
    same=annotation(c,data,'highlight',dict(scope='point',start_seconds=8.0004),dict(text=''),rid='same-ms-001')
    assert same.json['id']==a['id']
    other=annotation(c,data,'highlight',dict(scope='point',start_seconds=8.01),dict(text=''),rid='different-ms-001').json
    assert other['id']!=a['id']
    assert put(c,'/api/annotations/'+other['id'],dict(publication=data['published_at'],version=1,target=target)).status_code==409
    assert put(c,'/api/annotations/'+a['id'],dict(publication=data['published_at'],version=1,status='archived')).status_code==200
    replacement=annotation(c,data,'highlight',target,dict(text=''),rid='recreate-highlight').json
    assert replacement['id']!=a['id']
    assert put(c,'/api/annotations/'+a['id'],dict(publication=data['published_at'],version=2,status='active')).status_code==409
    assert app.extensions['annotations'].get(a['id'])['status']=='archived'


def test_learning_player_attribution_legacy_and_pagination(setup):
    app,root,data=setup;c=app.test_client();a=player(c);b=player(c,'Blair')
    first=review(app,data,'review-for-alex',a['id']);legacy=review(app,data,'unlabelled-review');review(app,data,'review-for-blair',b['id'])
    url='/api/learning?player_id='+a['id']
    rows=c.get(url).json['entries'];assert len(rows)==1 and len(rows[0]['reviews'])==1
    assert rows[0]['reviews'][0]['association']=='review' and rows[0]['reviews'][0]['player_mismatch']
    roster(c,[a['id']]);rows=c.get(url).json['entries']
    assert len(rows[0]['reviews'])==2
    assert {r['association'] for r in rows[0]['reviews']}=={'review','current_clip'}
    assert all(not r['player_mismatch'] for r in rows[0]['reviews'])
    all_reviews=[];after=''
    while True:
        page=c.get(url+'&limit=1'+('&after='+after if after else '')).json
        all_reviews += [r['id'] for e in page['entries'] for r in e['reviews']]
        after=page['next_after']
        if not after:break
    assert set(all_reviews)=={first['id'],legacy['id']} and len(all_reviews)==2
    assert c.get('/api/learning').status_code==400
    assert c.get(url+'&limit=999').status_code==400
    assert c.get('/learning').status_code==200


def test_learning_keeps_review_context_after_reprocess_and_media_removal(setup):
    app,root,data=setup;c=app.test_client();a=player(c);review(app,data,'historical-review',a['id'])
    app.extensions['state'].update('meta:'+ID,dict(title='Edited practice'),0)
    path=root/'metadata/sessions'/f'{ID}.json';data=copy.deepcopy(data);data['published_at']+=1;data['clips'][0]['id']='c'*24;path.write_text(json.dumps(data))
    row=c.get('/api/learning?player_id='+a['id']).json['entries'][0]
    assert row['clip_id']=='c'*24 and row['title']=='Edited practice'
    assert row['reviews'][0]['historical'] and row['media_available']
    path.unlink()
    row=c.get('/api/learning?player_id='+a['id']).json['entries'][0]
    assert not row['media_available'] and row['reviews'][0]['answer']=='Review historical-review'


def test_existing_duplicate_highlights_archive_without_losing_history(setup):
    app,root,data=setup;c=app.test_client();first=annotation(c,data,'highlight',body=dict(text=''),rid='old-highlight-one').json
    store=app.extensions['annotations'];duplicate=copy.deepcopy(store.get(first['id']));duplicate['id']='n_legacy_duplicate';duplicate['body']['text']='Keep this caption'
    from annotations import canonical,AnnotationStore
    with store.db() as db:
        db.execute('DROP INDEX unique_active_point_highlight')
        db.execute('DELETE FROM metadata_migrations')
        db.execute('INSERT INTO annotations(id,source_id,kind,data,version) VALUES(?,?,?,?,?)',(duplicate['id'],ID,'highlight',canonical(duplicate),1))
        db.execute('INSERT INTO revisions VALUES(?,?,?)',(duplicate['id'],1,canonical(duplicate)))
    migrated=AnnotationStore(store.path);rows=migrated.listing(ID)
    assert len(rows)==2 and len([a for a in rows if a['status']=='active'])==1
    assert migrated.get(duplicate['id'])['status']=='active'
    archived=migrated.get(first['id']);assert archived['status']=='archived' and archived['archive_reason']=='duplicate_highlight'
    assert [a['version'] for a in migrated.revisions(first['id'])]==[1,2]
    AnnotationStore(store.path)
    assert migrated.get(first['id'])['version']==2
