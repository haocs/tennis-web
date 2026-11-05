"""Serving identities, original-time knowledge, and output-bound metadata."""
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from test_app import setup, ID, CLIP, put
from test_annotations import create, listing
from entity_model import EntityCatalog, output_version
from app import create_app
H={'X-Tennis-Request':'1'}


def publish(root,data):
    (root/'metadata/sessions'/f'{data["id"]}.json').write_text(json.dumps(data))


def field(c,data,field='less_useful',value=True,version=0,**extra):
    return put(c,f'/api/entities/videos/{ID}/metadata',dict(publication=data['published_at'],subject_type='clip',clip_id=CLIP,field=field,value=value,version=version,**extra))


def test_output_fields_do_not_transfer_even_with_identical_bounds_and_clip_id(setup):
    app,root,data=setup;c=app.test_client()
    flag=field(c,data);assert flag.status_code==200
    note=field(c,data,'note','Useful to watch later');assert note.status_code==200
    logical=put(c,f'/api/entities/videos/{ID}/metadata',dict(publication=data['published_at'],subject_type='video',field='note',value='General practice note',version=0));assert logical.status_code==200
    old_version=output_version(ID,data['published_at'])
    old_clip=c.get('/api/sessions/'+ID).json['clips'][0]['entity_id']
    changed=copy.deepcopy(data);changed['published_at']+=1;publish(root,changed)
    new_clip=c.get('/api/sessions/'+ID).json['clips'][0]['entity_id'];assert old_clip!=new_clip
    rows=listing(c)
    assert all(a['relation']=='historical_run' and not a['playback_eligible'] for a in rows if a['target']['scope']=='clip')
    documents=c.get(f'/api/entities/videos/{ID}/metadata').json['metadata']
    assert len(documents)==1 and documents[0]['subject']['type']=='video'
    assert documents[0]['notes'][0]['text']=='General practice note'
    historic=c.get(f'/api/entities/videos/{ID}/metadata?video_version_id='+old_version).json['metadata']
    assert any(d['less_useful'] and d['notes'][0]['text']=='Useful to watch later' for d in historic)
    assert field(c,changed).status_code==200
    # A new generation's usefulness is a different field, not an edit of old data.
    assert len([a for a in listing(c) if a['kind']=='usefulness'])==2
    assert field(c,changed,version=1,key=flag.json['key']).status_code==409
    assert c.get('/api/entities/clips/'+old_clip).status_code==200
    versions=c.get('/api/entities/videos/'+ID).json['versions'];assert len(versions)==2


def test_moment_optional_end_remaps_and_retains_original_context(setup):
    app,root,data=setup;c=app.test_client()
    body=dict(original_recording_id=ID,publication=data['published_at'],type='highlight',start_ms=8000,end_ms=17000,payload=dict(text='Recovery'),request_id='moment-contract-001')
    a=c.post('/api/moments',json=body,headers=H);assert a.status_code==201
    a=a.json;assert a['type']=='highlight' and a['start_ms']==8000 and a['end_ms']==17000
    changed=copy.deepcopy(data);changed['published_at']+=1;changed['clips'][0]['id']='c'*24;publish(root,changed)
    moment=c.get('/api/recordings/'+ID+'/moments').json['moments'][0]
    assert moment['id']==a['id'] and moment['clip_ids']==['c'*24] and moment['relation']=='partly_retained'
    assert moment['start_ms']==8000 and moment['end_ms']==17000
    edit=put(c,'/api/moments/'+a['id'],dict(publication=changed['published_at'],version=1,payload=dict(text='New cue'),start_ms=9000))
    assert edit.status_code==200 and 'end_ms' not in edit.json
    assert c.post('/api/moments',json={**body,'end_ms':7000},headers=H).status_code==400
    assert c.post('/api/moments',json={**body,'start_ms':True},headers=H).status_code==400
    # Compatibility links retain history; there is no new interval entity.
    assert len(c.get('/api/annotations/'+a['id']+'/revisions').json['revisions'])==2
    (root/'metadata/sessions'/f'{ID}.json').unlink()
    moment=c.get('/api/recordings/'+ID+'/moments').json['moments'][0]
    assert moment['start_ms']==9000 and moment['relation']=='unavailable'


def test_cut_feedback_separate_from_serving_fields_and_moments(setup):
    app,root,data=setup;c=app.test_client()
    report=create(c,data,'cut_feedback',dict(scope='clip',clip_id=CLIP,start_seconds=5,end_seconds=15),dict(reasons=['poor_cut']),rid='domain-cut-001').json
    note=field(c,data,'note','A note').json
    flag=field(c,data).json
    highlight=create(c,data,'highlight',rid='domain-highlight-001').json
    annotations=c.get('/api/processing-annotations/sources/'+ID).json['annotations']
    assert [a['id'] for a in annotations]==[report['id']]
    assert annotations[0]['record_class']=='processing_annotation' and annotations[0]['subject']['type']=='clip'
    assert [a['id'] for a in c.get('/api/recordings/'+ID+'/moments').json['moments']]==[highlight['id']]
    assert put(c,'/api/moments/'+note['key'],dict(publication=data['published_at'],version=1,status='archived')).status_code==404
    assert put(c,'/api/processing-annotations/'+highlight['id'],dict(publication=data['published_at'],version=1,status='archived')).status_code==404
    with app.extensions['annotations'].db() as db:
        assert db.execute('SELECT COUNT(*) FROM entity_metadata').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM serving_moments').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM processing_annotations').fetchone()[0]==1


def test_usefulness_field_concurrent_update_and_versioned_video_support(setup):
    app,root,data=setup;c=app.test_client();saved=field(c,data).json
    with ThreadPoolExecutor(2) as pool:
        codes=list(pool.map(lambda _:field(app.test_client(),data,value=False,version=1,key=saved['key']).status_code,range(2)))
    assert sorted(codes)==[200,409]
    body=dict(publication=data['published_at'],subject_type='video_version',field='less_useful',value=True,version=0)
    assert put(c,'/api/entities/videos/'+ID+'/metadata',body).status_code==200
    assert put(c,'/api/entities/videos/'+ID+'/metadata',{**body,'subject_type':'video'}).status_code==400
    assert put(c,'/api/entities/videos/'+ID+'/metadata',{**body,'value':'false'}).status_code==400
    old=copy.deepcopy(data);old['published_at']-=1
    assert field(c,old).status_code==409


def test_session_is_independent_editable_and_does_not_regroup(setup):
    app,root,data=setup;c=app.test_client()
    initial=c.get('/api/practices').json['sessions'];assert len(initial)==1
    identity=initial[0]['id']
    saved=put(c,'/api/practices/'+identity+'/metadata',dict(version=0,title='Morning drills',note='Work on recovery'))
    assert saved.status_code==200
    earlier=copy.deepcopy(data);earlier.update(id='c'*64);earlier['source']['sha256']=earlier['id'];earlier['source']['recorded_at']='2026-10-24T16:50:00-0700';publish(root,earlier)
    groups=c.get('/api/practices').json['sessions'];assert len(groups)==1
    assert groups[0]['id']==identity and groups[0]['title']=='Morning drills'
    assert groups[0]['recording_ids']==[earlier['id'],ID]
    assert c.get('/api/sessions/'+ID).json['title']==data['title']
    assert put(c,'/api/practices/'+identity+'/metadata',dict(version=0,note='Stale')).status_code==409
    changed=copy.deepcopy(data);changed['source']['recorded_at']='2026-12-01T10:00:00Z';publish(root,changed)
    assert len(c.get('/api/practices').json['sessions'])==1
    (root/'metadata/sessions'/f'{ID}.json').unlink();(root/'metadata/sessions'/f'{earlier["id"]}.json').unlink()
    retained=c.get('/api/entities/sessions/'+identity).json
    assert retained['metadata']['note']=='Work on recovery' and not retained['media_available']


def test_existing_sessions_do_not_silently_merge_on_bridge_recording(setup):
    app,root,data=setup;c=app.test_client();c.get('/api/practices')
    later=copy.deepcopy(data);later['id']='c'*64;later['source'].update(sha256=later['id'],recorded_at='2026-10-24T18:00:00-0700');publish(root,later)
    assert len(c.get('/api/practices').json['sessions'])==2
    bridge=copy.deepcopy(data);bridge['id']='d'*64;bridge['source'].update(sha256=bridge['id'],recorded_at='2026-10-24T17:30:00-0700');publish(root,bridge)
    assert len(c.get('/api/practices').json['sessions'])==2


def test_legacy_migration_is_additive_and_preserves_revisions(setup):
    app,root,data=setup;c=app.test_client()
    oldnote=create(c,data,rid='legacy-moment-note').json
    oldflag=create(c,data,'usefulness',dict(scope='clip',clip_id=CLIP,start_seconds=5,end_seconds=15),dict(value='less_useful'),rid='legacy-field-001').json
    store=app.extensions['annotations']
    history=store.revisions(oldnote['id'])
    # Recreate the exact pre-migration five-column schema and raw JSON envelope.
    with store.db() as db:
        for view in ('serving_moments','entity_metadata','processing_annotations'):db.execute('DROP VIEW '+view)
        for r in db.execute('SELECT id,data FROM annotations').fetchall():
            raw=json.loads(r['data'])
            for k in ('record_class','subject','moment_type'):raw.pop(k,None)
            db.execute('UPDATE annotations SET data=? WHERE id=?',(json.dumps(raw),r['id']))
        db.execute('ALTER TABLE annotations DROP COLUMN record_class')
    fresh=create_app(dict(TESTING=True,LIBRARY_ROOT=str(root),DATA_ROOT=app.config['DATA_ROOT']))
    migrated=fresh.extensions['annotations'];assert migrated.revisions(oldnote['id'])==history
    assert {v['id'] for v in migrated.listing(ID)}=={oldnote['id'],oldflag['id']}
    moments=fresh.test_client().get('/api/recordings/'+ID+'/moments').json['moments']
    assert moments[0]['type']=='comment' and moments[0]['payload']['text']=='Good recovery'
    again=create_app(dict(TESTING=True,LIBRARY_ROOT=str(root),DATA_ROOT=app.config['DATA_ROOT']))
    assert again.extensions['annotations'].revisions(oldnote['id'])==history


def test_legacy_usefulness_has_one_field_after_canonical_edit(setup):
    app,root,data=setup;c=app.test_client()
    old=create(c,data,'usefulness',dict(scope='clip',clip_id=CLIP,start_seconds=5,end_seconds=15),dict(value='less_useful'),rid='old-useful-001').json
    edited=field(c,data,value=False,version=old['version']);assert edited.status_code==200
    assert edited.json['key']==old['id'] and len(listing(c))==1
    assert field(c,data,value=True,version=2).status_code==200
    assert len(listing(c))==1


def test_processing_labels_accept_original_time_and_video_without_becoming_moments(setup):
    app,root,data=setup;c=app.test_client()
    for i,target in enumerate([dict(scope='video'),dict(scope='point',start_seconds=8),dict(scope='interval',start_seconds=6,end_seconds=9)]):
        body=dict(source_id=ID,publication=data['published_at'],kind='processing_label',target=target,
                  body=dict(label='tennis.playing',value=True,confidence=.8),request_id='processing-label-'+str(i))
        response=c.post('/api/processing-annotations',json=body,headers=H)
        assert response.status_code==201 and response.json['record_class']=='processing_annotation'
    assert not c.get('/api/recordings/'+ID+'/moments').json['moments']
    assert len(c.get('/api/processing-annotations/sources/'+ID).json['annotations'])==3
    response=c.post('/api/processing-annotations',json={**body,'body':dict(label='invalid label',value=True)},headers=H)
    assert response.status_code==400
