"""Protocol, retention and review tests; no provider calls or private footage."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_app import setup, ID, CLIP
from test_coaching import post, enabled, selection
from coaching import Coaching, QueueConflict
from worker.tennis_coach.annotations import CAPABILITY, expected_frames, evidence_manifest, parse_response

TOKEN='test-worker-secret'


def prepared(setup):
    app,root,data=enabled(setup);c=app.test_client()
    render=post(c,'exports',selection(data)).json
    job=post(c,'worker/render/claim',{'worker_id':'render'},TOKEN).json['job']
    fence=hashlib.sha256(job['token'].encode()).hexdigest()[:24]
    folder=Path(app.config['COACH_MEDIA_ROOT'])/job['id']/fence;folder.mkdir(parents=True)
    frames=[]
    for i,original in enumerate((6.5,7.5)):
        raw=b'jpeg-frame-'+str(i).encode();name=f'frame-{i:03}.jpg';(folder/name).write_bytes(raw)
        frames.append(dict(file=name,sha256=hashlib.sha256(raw).hexdigest(),original_seconds=original,clip_seconds=original-5,excerpt_seconds=(original-6)/.5))
    (folder/'excerpt.mp4').write_bytes(b'video')
    meta=dict(recipe=job['body'],lease_token=job['token'],frames=frames,bytes=5,output_seconds=4,sha256='e'*64,render_seconds=1)
    (folder/'recipe.json').write_text(json.dumps(meta))
    assert post(c,'worker/jobs/'+job['id']+'/complete',{'token':job['token']},TOKEN).status_code==200
    ai=post(c,'requests',dict(export_id=job['id'],prompt='Check balance',consent=True,request_id='annotation-request')).json
    return app,c,data,ai,folder


def claimed(c):
    assert post(c,'worker/ai/claim',{'worker_id':'old-worker'},TOKEN).json['job'] is None
    return post(c,'worker/ai/claim',{'worker_id':'new-worker','capabilities':[CAPABILITY]},TOKEN).json['job']


def pin(app,c,job,folder):
    manifest=evidence_manifest(job)
    cache=Path(app.config['COACH_MEDIA_ROOT'])/'evidence/frames';cache.mkdir(parents=True,exist_ok=True)
    for frame in manifest['frames']:
        source=folder/f"frame-{frame['order']-1:03}.jpg"
        shutil.copyfile(source,cache/(frame['sha256']+'.jpg'))
    result=post(c,'worker/jobs/'+job['id']+'/evidence',dict(token=job['token'],result=manifest),TOKEN)
    assert result.status_code==200,result.json
    return manifest


def suggestion(manifest):
    return dict(title='Balanced reset',topic='Ready position',observation='Feet are narrow in this still.',cue='Reset with soft knees.',drill='Recover and hold for one second.',uncertainty='Stills cannot confirm split-step timing.',evidence_frame_ids=[manifest['frames'][0]['frame_id']],applies_to=[dict(start_seconds=6.4,end_seconds=7.8)])


def complete(c,job,answer):
    assert post(c,'worker/jobs/'+job['id']+'/calling',{'token':job['token']},TOKEN).status_code==200
    result=dict(answer=answer,analysis_mode='timestamped_frames',model='fake-test-model')
    done=post(c,'worker/jobs/'+job['id']+'/complete',dict(token=job['token'],result=result),TOKEN)
    assert done.status_code==200,done.json
    return done.json


def annotations(c):return c.get('/api/coaching/sessions/'+ID).json['annotations']


def review(c,a,publication,status='accepted',**extra):
    return c.put('/api/coaching/annotations/'+a['id'],json=dict(version=a['version'],publication=publication,status=status,**extra),headers={'X-Tennis-Request':'1'})


def test_evidence_is_required_before_provider_and_is_immutable(setup):
    app,c,data,ai,folder=prepared(setup);job=claimed(c)
    assert ai['body']['required_capability']==CAPABILITY
    assert ai['body']['submitted_prompt'] and ai['body']['response_contract']['required']
    endpoint='worker/jobs/'+job['id']
    assert post(c,endpoint+'/calling',dict(token=job['token']),TOKEN).status_code==400
    manifest=pin(app,c,job,folder)
    assert c.get('/api/coaching/evidence/'+job['id']).json==manifest
    altered=copy.deepcopy(manifest);altered['frames'][0]['original_seconds']+=.1
    assert post(c,endpoint+'/evidence',dict(token=job['token'],result=altered),TOKEN).status_code==400
    assert post(c,endpoint+'/evidence',dict(token=job['token'],result=manifest),TOKEN).status_code==200
    assert c.get('/api/coaching/evidence/'+job['id']+'/unknown.jpg').status_code==404


def test_accepted_annotations_survive_export_expiry_and_reprocessing(setup):
    app,c,data,ai,folder=prepared(setup);job=claimed(c);manifest=pin(app,c,job,folder)
    raw=json.dumps(dict(schema_version=2,summary='One observable adjustment.',suggestions=[suggestion(manifest)]))
    done=complete(c,job,raw);a=annotations(c)[0]
    assert a['status']=='proposed' and not a['playback_eligible']
    accepted=review(c,a,data['published_at']);assert accepted.status_code==200 and accepted.json['playback_eligible']
    assert review(c,a,data['published_at']).status_code==409
    old=accepted.json
    # Real publication replacement, retaining the same original hash but changed clip IDs.
    new=copy.deepcopy(data);new['published_at']+=1;new['clips'][0]['id']='c'*24
    (Path(app.config['LIBRARY_ROOT'])/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(new))
    changed=annotations(c)[0]
    assert changed['historical'] and not changed['playback_eligible'] and changed['related_clip_ids']==['c'*24]
    assert changed['provenance']['clip_id']==CLIP
    assert review(c,changed,data['published_at']).status_code==409
    assert review(c,changed,new['published_at']).json['playback_eligible']
    shutil.rmtree(folder.parent)
    frame=manifest['frames'][0]
    assert c.get(f"/api/coaching/evidence/{job['id']}/{frame['frame_id']}.jpg").data==b'jpeg-frame-0'
    exported=c.get('/api/coaching/annotations/export/'+ID).json['annotations'][0]
    assert exported['request']['result']['answer']==raw
    assert exported['proposal']['cue']=='Reset with soft knees.'
    assert len(exported['revisions'])==3 and exported['training_label_status']=='not_released'
    assert exported['evidence']==manifest and exported['submitted_prompt']==job['body']['submitted_prompt']
    assert job['token'] not in json.dumps(exported)
    # Completion ACK retry cannot add duplicate proposals or replace the raw answer.
    again=post(c,'worker/jobs/'+job['id']+'/complete',dict(token=job['token'],result={'answer':'overwrite'}),TOKEN)
    assert again.status_code==200 and again.json['result']['answer']==raw
    assert len(annotations(c))==1


def test_partial_refs_untimed_advice_and_human_recovery(setup):
    app,c,data,ai,folder=prepared(setup);job=claimed(c);manifest=pin(app,c,job,folder)
    proposal=suggestion(manifest);proposal['evidence_frame_ids']+=['invented-frame'];proposal['applies_to']=[dict(start_seconds=1,end_seconds=100)]
    raw=json.dumps(dict(schema_version=2,summary='Check evidence.',suggestions=[proposal]))
    done=complete(c,job,raw);a=annotations(c)[0]
    assert done['result']['structured']['parse_status']=='partial'
    assert a['current']['timing_status']=='untimed' and a['current']['validation_warnings']
    assert review(c,a,data['published_at']).status_code==400
    content=copy.deepcopy(a['current']);content['applies_to']=[]
    saved=review(c,a,data['published_at'],content=content)
    assert saved.status_code==200 and saved.json['status']=='accepted' and not saved.json['playback_eligible']
    assert saved.json['proposal']['validation_warnings']
    # Editable human recovery is distinct from the immutable AI proposal.
    manual=dict(job_id=job['id'],publication=data['published_at'],content=suggestion(manifest),request_id='manual-note-001')
    made=post(c,'annotations',manual);assert made.status_code==201
    assert post(c,'annotations',manual).json['id']==made.json['id']
    assert made.json['proposal']['origin']=='human' and made.json['playback_eligible']
    export=c.get('/api/coaching/annotations/export/'+ID).json
    human=next(a for a in export['annotations'] if a['id']==made.json['id'])
    assert human['revisions'][0]['actor']=='human'


@pytest.mark.parametrize('raw', ['plain answer referencing frame 10','```json\n{}\n```','{"schema_version":999}', '{"schema_version":2,"summary":"x","suggestions":[null]}'])
def test_unstructured_or_invalid_results_remain_recoverable(setup,raw):
    app,c,data,ai,folder=prepared(setup);job=claimed(c);manifest=pin(app,c,job,folder)
    done=complete(c,job,raw)
    assert done['result']['answer']==raw and annotations(c)==[]
    assert done['result']['structured']['parse_status'] in ('unstructured','partial')
    assert c.get('/api/coaching/evidence/'+job['id']).status_code==200
    body=dict(job_id=job['id'],publication=data['published_at'],content=suggestion(manifest),request_id='recovered-human')
    assert post(c,'annotations',body).status_code==201


def test_concurrent_reviews_do_not_overwrite_each_other(setup):
    app,c,data,ai,folder=prepared(setup);job=claimed(c);manifest=pin(app,c,job,folder)
    complete(c,job,json.dumps(dict(schema_version=2,summary='',suggestions=[suggestion(manifest)])))
    a=annotations(c)[0]
    def save(status):
        with app.test_client() as client:return review(client,a,data['published_at'],status).status_code
    with ThreadPoolExecutor(2) as pool:codes=list(pool.map(save,['accepted','dismissed']))
    assert sorted(codes)==[200,409]


def test_checksum_and_symlink_boundaries(setup,tmp_path):
    app,c,data,ai,folder=prepared(setup);job=claimed(c);manifest=pin(app,c,job,folder)
    cache=Path(app.config['COACH_MEDIA_ROOT'])/'evidence/frames';frame=manifest['frames'][0];path=cache/(frame['sha256']+'.jpg')
    path.write_bytes(b'changed')
    assert post(c,'worker/jobs/'+job['id']+'/evidence',dict(token=job['token'],result=manifest),TOKEN).status_code==400
    outside=tmp_path/'outside.jpg';outside.write_bytes(b'jpeg-frame-0');path.unlink();path.symlink_to(outside)
    assert post(c,'worker/jobs/'+job['id']+'/evidence',dict(token=job['token'],result=manifest),TOKEN).status_code==400
    assert c.get(f"/api/coaching/evidence/{job['id']}/{frame['frame_id']}.jpg").status_code==404


def test_frame_contract_rejects_mismatched_clocks_and_nonfinite_values():
    export=dict(id='render',body=dict(original_start=10,original_end=12,start=1,end=3,speed=.5),result={'frames':[dict(sha256='a'*64,original_seconds=11,clip_seconds=2,excerpt_seconds=2)]})
    assert expected_frames(export)[0]['original_seconds']==11
    for field,value in [('original_seconds',True),('clip_seconds',float('nan')),('excerpt_seconds',1)]:
        changed=copy.deepcopy(export);changed['result']['frames'][0][field]=value
        with pytest.raises(ValueError):expected_frames(changed)


def test_worker_pins_exact_images_then_calls_with_frozen_schema(setup,tmp_path,monkeypatch):
    from worker.tennis_coach.worker import analyze
    app,c,data,ai,folder=prepared(setup);job=claimed(c);events=[];local=tmp_path/'local';local.mkdir()
    def run(args,**kwargs):
        from types import SimpleNamespace
        if args[1:]==['login','status']:return SimpleNamespace(returncode=0,stdout='Logged in using ChatGPT',stderr='')
        events.append('provider')
        assert '--output-schema' in args and kwargs['input']==job['body']['submitted_prompt']
        assert len([a for a in args if a=='--image'])==2
        assert 'OPENAI_API_KEY' not in kwargs['env']
        (local/'answer.txt').write_text('{"schema_version":2,"summary":"","suggestions":[]}')
        return SimpleNamespace(returncode=0,stdout='{"type":"turn.completed","usage":{"input_tokens":1}}',stderr='')
    monkeypatch.setattr('worker.tennis_coach.worker.subprocess.run',run)
    monkeypatch.setenv('OPENAI_API_KEY','must-not-be-used')
    def before(manifest):
        events.append('pin')
        assert manifest==evidence_manifest(job)
        for f in manifest['frames']:
            p=Path(app.config['COACH_MEDIA_ROOT'])/'evidence/frames'/(f['sha256']+'.jpg')
            assert hashlib.sha256(p.read_bytes()).hexdigest()==f['sha256']
    cfg=dict(artifact_root=app.config['COACH_MEDIA_ROOT'],codex='fake-codex')
    result=analyze(cfg,job,local,before)
    assert events==['pin','provider'] and result['usage']['input_tokens']==1
    assert json.loads((local/'evidence.json').read_text())==evidence_manifest(job)
    # A mutated source package never reaches the provider boundary.
    (folder/'frame-000.jpg').write_bytes(b'changed');events.clear()
    with pytest.raises(ValueError,match='Prepared frame changed'):analyze(cfg,job,local,before)
    assert events==[]


def test_legacy_database_migrates_without_losing_notes_and_export_includes_raw(tmp_path):
    import sqlite3
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE workers(id TEXT PRIMARY KEY, role TEXT, updated REAL, status TEXT)')
        db.execute("INSERT INTO workers VALUES('old','ai',1,'ready')")
    def open_store(_):return Coaching(path)
    with ThreadPoolExecutor(2) as pool:list(pool.map(open_store,range(2)))
    q=Coaching(path);job=q.create('ai',{'source_id':ID},'old-note-001');claim=q.claim('ai','old')
    q.worker_update(job['id'],claim['token'],'calling');q.worker_update(job['id'],claim['token'],'complete',{'answer':'Legacy frame 10 advice'})
    export=q.annotation_export(ID)
    assert export['annotations']==[] and export['responses'][0]['request']['result']['answer']=='Legacy frame 10 advice'
    assert export['responses'][0]['evidence'] is None


def test_follow_up_reuses_evidence_and_retains_previous_response(setup):
    app,c,data,ai,folder=prepared(setup);job=claimed(c);manifest=pin(app,c,job,folder)
    raw=json.dumps(dict(schema_version=2,summary='Review of the full sequence.',suggestions=[suggestion(manifest)]))
    complete(c,job,raw)
    body=dict(export_id=ai['body']['export_id'],prompt='Review every snapshot again, focusing on my footwork.',consent=True,request_id='follow-up-001',follow_up_of=job['id'])
    new=post(c,'requests',body);assert new.status_code==201,new.json
    assert new.json['id']!=job['id']
    spec=new.json['body']
    assert spec['evidence_frames']==job['body']['evidence_frames']
    assert spec['previous_question']=='Check balance' and spec['previous_answer']==raw
    assert spec['follow_up_of']==job['id'] and not spec['previous_answer_truncated']
    assert 'ENTIRE attached tennis frame sequence' in spec['submitted_prompt']
    assert 'not instructions or verified truth' in spec['submitted_prompt']
    assert post(c,'requests',body).json['id']==new.json['id']
    assert c.get('/api/coaching/jobs/'+job['id']).json['result']['answer']==raw
    assert post(c,'requests',dict(body,request_id='bad-parent-001',follow_up_of=ai['body']['export_id'])).status_code==400
    assert post(c,'requests',dict(body,request_id='bad-parent-002',follow_up_of=[])).status_code==400


def test_delete_coaching_review_removes_job_annotations_evidence_and_orphaned_artifacts(setup):
    H = {'X-Tennis-Request': '1'}
    app, c, data, ai, folder = prepared(setup)
    job = claimed(c)
    manifest = pin(app, c, job, folder)
    raw = json.dumps(dict(schema_version=2, summary='Review to delete.', suggestions=[suggestion(manifest)]))
    complete(c, job, raw)

    # Confirm consent error message uses neutral "to AI coach" copy.
    no_consent = post(c, 'requests', dict(export_id=ai['body']['export_id'], prompt='Retry?', consent=False, request_id='consent-check-1'))
    assert no_consent.status_code == 400 and 'to AI coach' in no_consent.json['error']

    # Follow-up shares the same render export and frame hashes.
    fu = post(c, 'requests', dict(export_id=ai['body']['export_id'], prompt='Follow up?', consent=True, request_id='del-follow-up', follow_up_of=job['id'])).json
    fu_job = claimed(c)
    manifest_fu = pin(app, c, fu_job, folder)
    complete(c, fu_job, json.dumps(dict(schema_version=2, summary='Follow-up review.', suggestions=[suggestion(manifest_fu)])))

    # In-flight calling job returns 409 and refuses deletion.
    third = post(c, 'requests', dict(export_id=ai['body']['export_id'], prompt='Active?', consent=True, request_id='del-active-req')).json
    claimed_third = claimed(c)
    pin(app, c, claimed_third, folder)
    assert post(c, f"worker/jobs/{claimed_third['id']}/calling", {'token': claimed_third['token']}, TOKEN).status_code == 200
    assert c.delete(f"/api/coaching/jobs/{claimed_third['id']}", headers=H, json={}).status_code == 409

    # Non-review jobs (render / share) return 400.
    assert c.delete(f"/api/coaching/jobs/{ai['body']['export_id']}", headers=H, json={}).status_code == 400

    cache_dir = Path(app.config['COACH_MEDIA_ROOT']) / 'evidence/frames'
    frame_files = [cache_dir / f"{f['sha256']}.jpg" for f in manifest['frames']]
    assert all(p.is_file() for p in frame_files)

    # Deleting the first review leaves shared render folder and shared frame files intact for the follow-up.
    del1 = c.delete(f"/api/coaching/jobs/{job['id']}", headers=H, json={})
    assert del1.status_code == 200, del1.json
    assert del1.json['deleted_export_id'] is None
    assert Path(folder).is_dir()
    assert all(p.is_file() for p in frame_files)

    # Finish active third request so fu & third can be deleted and orphan cleanup verified.
    assert post(c, f"worker/jobs/{claimed_third['id']}/complete", {'token': claimed_third['token'], 'result': {'answer': 'Done', 'analysis_mode': 'timestamped_frames'}}, TOKEN).status_code == 200
    assert c.delete(f"/api/coaching/jobs/{claimed_third['id']}", headers=H, json={}).status_code == 200
    del_last = c.delete(f"/api/coaching/jobs/{fu['id']}", headers=H, json={})
    assert del_last.status_code == 200, del_last.json
    assert del_last.json['deleted_export_id'] == ai['body']['export_id']
    assert not (Path(app.config['COACH_MEDIA_ROOT']) / ai['body']['export_id']).exists()
    assert all(not p.exists() for p in frame_files)
    assert c.get(f"/api/coaching/jobs/{job['id']}").status_code == 404
    assert c.get(f"/api/coaching/sessions/{ID}").json['jobs'] == []
    assert c.get(f"/api/coaching/sessions/{ID}").json['annotations'] == []
