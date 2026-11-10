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
        if target['scope']=='video':assert response.json['subject']['type']=='video_version'
    assert not c.get('/api/recordings/'+ID+'/moments').json['moments']
    assert len(c.get('/api/processing-annotations/sources/'+ID).json['annotations'])==3
    response=c.post('/api/processing-annotations',json={**body,'body':dict(label='invalid label',value=True)},headers=H)
    assert response.status_code==400


def test_typed_pagination_does_not_drop_moments_behind_other_metadata(setup):
    app,root,data=setup;c=app.test_client();store=app.extensions['annotations']
    template=dict(schema_version=2,source_id=ID,kind='note',target=dict(scope='video'),body=dict(text='Note'),context=dict(publication=data['published_at']),origin=dict(type='human'),status='active')
    for i in range(205):store.write({**template,'id':'n_000'+str(i).zfill(4)})
    for i in range(2):
        store.write({**template,'id':'n_zzz'+str(i),'target':dict(scope='point',start_seconds=8+i)})
        store.write({**template,'id':'n_yyy'+str(i),'kind':'processing_label','body':dict(label='tennis.playing',value=True)})
    for path,key in [('/api/recordings/'+ID+'/moments','moments'),('/api/processing-annotations/sources/'+ID,'annotations')]:
        first=c.get(path+'?limit=1').json;assert len(first[key])==1 and first['next_after']
        second=c.get(path+'?limit=1&after='+first['next_after']).json
        assert len(second[key])==1 and not second['next_after']
        assert first[key][0]['id']!=second[key][0]['id']


def test_optional_moment_note_and_half_open_end_preserve_entity_metadata_boundary(setup):
    app,root,data=setup;c=app.test_client()
    body=dict(original_recording_id=ID,publication=data['published_at'],type='comment',start_ms=8000,payload=dict(text=''),request_id='optional-moment-text-001')
    created=c.post('/api/moments',json=body,headers=H)
    assert created.status_code==201
    moment=created.json
    assert moment['type']=='comment' and moment['payload']==dict(text='')
    changed=put(c,'/api/moments/'+moment['id'],dict(publication=data['published_at'],version=1,start_ms=8000,end_ms=12000,payload=dict(text='Watch the recovery')))
    assert changed.status_code==200 and changed.json['end_ms']==12000
    cleared=put(c,'/api/moments/'+moment['id'],dict(publication=data['published_at'],version=2,payload=dict(text='')))
    assert cleared.status_code==200
    assert len(c.get('/api/annotations/'+moment['id']+'/revisions').json['revisions'])==3
    # Empty entity notes are still invalid, including a retarget of an empty Moment.
    assert create(c,data,target=dict(scope='video'),body=dict(text='')).status_code==400
    assert put(c,'/api/metadata/'+moment['id'],dict(publication=data['published_at'],version=3,target=dict(scope='video'))).status_code==400
    endpoint={**body,'start_ms':round(data['source']['duration_seconds']*1000),'request_id':'optional-moment-end-001'}
    assert c.post('/api/moments',json=endpoint,headers=H).status_code==400


def test_adjacent_clip_moments_are_half_open_and_spanning_moments_keep_original_bounds(setup):
    app,root,data=setup;c=app.test_client()
    next_clip=copy.deepcopy(data['clips'][0]);next_clip.update(id='c'*24,index=2,file='Clips/clip-002.mp4',original_start_seconds=15,original_end_seconds=20,duration_seconds=5,playback_start_seconds=10,playback_end_seconds=15)
    data['clips'].append(next_clip);data['video']['duration_seconds']=15;publish(root,data)
    base=dict(original_recording_id=ID,publication=data['published_at'],type='comment',payload=dict(text=''))
    point=c.post('/api/moments',json={**base,'start_ms':15000,'request_id':'clip-boundary-point-001'},headers=H)
    assert point.status_code==201 and point.json['clip_ids']==['c'*24]
    spanning=c.post('/api/moments',json={**base,'start_ms':14000,'end_ms':16000,'request_id':'clip-boundary-range-001'},headers=H)
    assert spanning.status_code==201 and spanning.json['clip_ids']==[CLIP,'c'*24]
    assert spanning.json['start_ms']==14000 and spanning.json['end_ms']==16000


def test_admit_version_immutability_and_collision_rejection(setup):
    import pytest
    app,root,data=setup;c=app.test_client()
    catalog=app.extensions['entity_catalog']
    v1_id=output_version(ID,data['published_at'])
    before=catalog.version_manifest(ID,v1_id)
    assert before is not None

    # Admitting a historical version with update_current_serving=False does not change current_version_id
    older=copy.deepcopy(data);older['published_at']-=500
    older_id=catalog.admit_version(older,update_current_serving=False)
    with catalog.db() as db:
        assert db.execute('SELECT current_version_id FROM videos WHERE id=?',(ID,)).fetchone()['current_version_id']==v1_id
    assert catalog.version_manifest(ID,older_id)['published_at']==older['published_at']

    # Collisions across clips, video duration, source metadata, media_directory, or processing are rejected
    for mutator in (
        lambda d: d['clips'][0].update(original_end_seconds=16),
        lambda d: d['video'].update(duration_seconds=12),
        lambda d: d['source'].update(duration_seconds=25),
        lambda d: d.update(media_directory='f'*64),
        lambda d: d.update(processing={'run_id':'tampered'}),
    ):
        tampered=copy.deepcopy(data)
        mutator(tampered)
        with pytest.raises(ValueError,match='collision'):
            catalog.admit_version(tampered)
        assert catalog.version_manifest(ID,v1_id)==before

    # Public versions endpoint returns 409 without overwriting retained manifest on generation collision
    tampered_disk=copy.deepcopy(data);tampered_disk['video']['duration_seconds']=11
    publish(root,tampered_disk)
    resp=c.get(f'/api/processing/videos/{ID}/versions')
    assert resp.status_code==409
    assert catalog.version_manifest(ID,v1_id)==before

    # Malformed or non-finite version_manifest lookup returns None cleanly
    assert catalog.version_manifest(ID,'not-a-version') is None
    with catalog.db() as db:
        db.execute('INSERT INTO video_versions VALUES(?,?,?)',('v_'+'9'*32,ID,'{"id":"'+ID+'","published_at":"bad"}'))
    assert catalog.version_manifest(ID,'v_'+'9'*32) is None
