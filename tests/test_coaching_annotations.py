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


def test_schema_v3_and_v2_compatibility_and_gemini_schema(setup):
    from worker.tennis_coach.annotations import (
        BODY_AREAS,
        SCHEMA_VERSION,
        STROKE_PHASES,
        gemini_compatible_schema,
        response_schema,
    )

    assert SCHEMA_VERSION == 3
    schema = response_schema()
    compat = gemini_compatible_schema(schema)

    def walk(node):
        if isinstance(node, dict):
            assert 'additionalProperties' not in node
            assert 'anyOf' not in node
            assert node.get('type') != 'null'
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(compat)
    sug_props = compat['properties']['suggestions']['items']['properties']
    for key in ('now_summary', 'primary_frame_id', 'stroke_phase', 'body_areas', 'focus_region'):
        assert key in sug_props

    _app, _c, _data, ai, _folder = prepared(setup)
    manifest = dict(
        frames=ai['body']['evidence_frames'],
        original_start=ai['body']['original_start'],
        original_end=ai['body']['original_end'],
    )
    f0 = manifest['frames'][0]

    # Backward compatibility: schema_version 2 without v3 fields parses cleanly.
    v2_raw = json.dumps(dict(schema_version=2, summary='Legacy v2 review.', suggestions=[suggestion(manifest)]))
    v2 = parse_response(v2_raw, manifest)
    assert v2['parse_status'] == 'valid'
    assert v2['validation_warnings'] == []
    s2 = v2['suggestions'][0]
    assert s2['now_summary'] == ''
    assert s2['primary_frame_id'] == ''
    assert s2['stroke_phase'] == ''
    assert s2['body_areas'] == []
    assert s2['focus_region'] is None

    # Schema v3 with all inspection fields parses and preserves normalized geometry.
    v3_item = dict(
        suggestion(manifest),
        now_summary='Right elbow stays close to your ribs before contact.',
        primary_frame_id=f0['frame_id'],
        stroke_phase='contact_window',
        body_areas=['hitting_arm', 'hips_trunk', 'hitting_arm'],
        focus_region=dict(x=0.25, y=0.2, width=0.35, height=0.4),
    )
    assert 'contact_window' in STROKE_PHASES and 'hitting_arm' in BODY_AREAS
    v3 = parse_response(json.dumps(dict(schema_version=3, summary='v3 review.', suggestions=[v3_item])), manifest)
    assert v3['parse_status'] == 'valid'
    assert v3['validation_warnings'] == []
    s3 = v3['suggestions'][0]
    assert s3['primary_frame_id'] == f0['frame_id']
    assert s3['stroke_phase'] == 'contact_window'
    assert s3['body_areas'] == ['hitting_arm', 'hips_trunk']
    assert s3['focus_region'] == {'x': 0.25, 'y': 0.2, 'width': 0.35, 'height': 0.4}


def test_v3_invalid_metadata_stripping_strict_rejection_and_immutable_history(setup):
    app, c, data, ai, folder = prepared(setup)
    job = claimed(c)
    manifest = pin(app, c, job, folder)
    f0, f1 = manifest['frames'][0], manifest['frames'][1]

    # Invalid references / enums / out-of-bounds focus_region yield partial warnings and stripped fields.
    bad_v3_item = dict(
        suggestion(manifest),
        evidence_frame_ids=[f0['frame_id']],
        primary_frame_id=f1['frame_id'],  # Not inside cited evidence_frame_ids!
        stroke_phase='nonexistent_phase',
        body_areas=['shoulders_chest', 'unknown_limb'],
        focus_region=dict(x=0.95, y=0.9, width=0.2, height=0.2),  # Extends beyond 1.0!
    )
    done = complete(c, job, json.dumps(dict(schema_version=3, summary='Partial v3.', suggestions=[bad_v3_item])))
    assert done['result']['structured']['parse_status'] == 'partial'
    a = annotations(c)[0]
    assert a['current']['primary_frame_id'] == ''
    assert a['current']['stroke_phase'] == ''
    assert a['current']['body_areas'] == ['shoulders_chest']
    assert a['current']['focus_region'] is None
    assert a['current']['validation_warnings']

    # Strict validation rejects uncleaned v3 warnings with 400.
    assert review(c, a, data['published_at']).status_code == 400
    assert (
        review(
            c,
            a,
            data['published_at'],
            content=dict(a['current'], stroke_phase='bad_phase'),
        ).status_code
        == 400
    )

    # Human edits & accepts valid v3 fields; evidence manifest remains immutable and training status unreleased.
    cleaned = copy.deepcopy(a['current'])
    cleaned.update(
        now_summary='Left shoulder stays closed slightly longer into contact.',
        primary_frame_id=f0['frame_id'],
        stroke_phase='forward_swing',
        body_areas=['shoulders_chest', 'hips_trunk'],
        focus_region=dict(x=0.2, y=0.2, width=0.3, height=0.3),
    )
    accepted = review(c, a, data['published_at'], status='accepted', content=cleaned)
    assert accepted.status_code == 200
    assert accepted.json['playback_eligible']
    assert accepted.json['training_label_status'] == 'not_released'
    assert accepted.json['current']['primary_frame_id'] == f0['frame_id']
    assert accepted.json['current']['stroke_phase'] == 'forward_swing'
    assert accepted.json['current']['body_areas'] == ['shoulders_chest', 'hips_trunk']
    assert accepted.json['current']['focus_region'] == {'x': 0.2, 'y': 0.2, 'width': 0.3, 'height': 0.3}

    # Pinning a mutated manifest against the same job fails; stored manifest stays unchanged.
    mutated = copy.deepcopy(manifest)
    mutated['frames'][0]['sha256'] = '0' * 64
    assert post(c, f"worker/jobs/{job['id']}/evidence", {'token': job['token'], 'result': mutated}, TOKEN).status_code == 400
    assert c.get(f"/api/coaching/evidence/{job['id']}").json['frames'][0]['sha256'] == f0['sha256']

    # Reprocessing the session marks accepted v3 annotation historical & ineligible for automatic playback, while keeping immutable proposal intact.
    reprocessed = copy.deepcopy(data)
    reprocessed['published_at'] += 1
    reprocessed['clips'][0]['id'] = 'd' * 24
    (Path(app.config['LIBRARY_ROOT']) / 'metadata/sessions' / f'{ID}.json').write_text(json.dumps(reprocessed))
    after_reprocess = annotations(c)[0]
    assert after_reprocess['historical'] and not after_reprocess['playback_eligible']
    assert after_reprocess['proposal']['primary_frame_id'] == ''
    assert after_reprocess['training_label_status'] == 'not_released'

    export_payload = c.get(f'/api/coaching/annotations/export/{ID}').json
    assert export_payload['annotations'][0]['training_label_status'] == 'not_released'


def test_lazy_pose_enrichment_export_strip_and_delete_cascade(setup):
    app, _root, data = enabled(setup)
    c = app.test_client()
    render_req = post(
        c,
        'exports',
        dict(
            selection(data),
            start=0.5,
            end=2.5,
            question='Check trophy pose',
            consent=True,
            subject_hint='near-court server',
            request_id='pose-render-req-1',
        ),
    )
    assert render_req.status_code == 201
    rjob = post(c, 'worker/render/claim', {'worker_id': 'r1'}, TOKEN).json['job']
    assert rjob['id'] == render_req.json['id']
    fence = hashlib.sha256(rjob['token'].encode()).hexdigest()[:24]
    folder = Path(app.config['COACH_MEDIA_ROOT']) / rjob['id'] / fence
    folder.mkdir(parents=True, exist_ok=True)
    spec = rjob['body']
    f0_bytes = b'pose-frame-0'
    f1_bytes = b'pose-frame-1'
    f0_sha = hashlib.sha256(f0_bytes).hexdigest()
    f1_sha = hashlib.sha256(f1_bytes).hexdigest()
    (folder / 'excerpt.mp4').write_bytes(b'video-bytes')
    (folder / 'frame-000.jpg').write_bytes(f0_bytes)
    (folder / 'frame-001.jpg').write_bytes(f1_bytes)
    (folder / 'prompt.txt').write_text('Check trophy pose')
    ev_dir = Path(app.config['COACH_MEDIA_ROOT']) / 'evidence/frames'
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / f'{f0_sha}.jpg').write_bytes(f0_bytes)
    (ev_dir / f'{f1_sha}.jpg').write_bytes(f1_bytes)
    render_frames = [
        {
            'file': 'frame-000.jpg',
            'sha256': f0_sha,
            'original_seconds': spec['original_start'] + 0.5,
            'clip_seconds': spec['start'] + 0.5,
            'excerpt_seconds': 0.5 / spec['speed'],
        },
        {
            'file': 'frame-001.jpg',
            'sha256': f1_sha,
            'original_seconds': spec['original_start'] + 1.5,
            'clip_seconds': spec['start'] + 1.5,
            'excerpt_seconds': 1.5 / spec['speed'],
        },
    ]
    (folder / 'recipe.json').write_text(
        json.dumps(
            dict(
                recipe=spec,
                lease_token=rjob['token'],
                frames=render_frames,
                bytes=11,
                output_seconds=2.0,
                sha256='e' * 64,
                render_seconds=0.3,
            )
        )
    )
    pose_doc = {
        'schema_version': 1,
        'coordinate_space': 'display_oriented_evidence_normalized',
        'model': {'name': 'mediapipe_pose_landmarker_lite', 'version': '0.1', 'sha256': 'a' * 64, 'license': 'Apache-2.0'},
        'limitations': 'Approximate 2D monocular still-frame pose estimates.',
        'frames': [
            {
                'file': 'frame-000.jpg',
                'sha256': f0_sha,
                'width': 640,
                'height': 360,
                'person_count': 1,
                'subject_ambiguous': False,
                'ambiguity_reason': '',
                'joints': {'right_shoulder': {'x': 0.42, 'y': 0.31, 'confidence': 0.92}},
            },
            {
                'file': 'frame-001.jpg',
                'sha256': f1_sha,
                'width': 640,
                'height': 360,
                'person_count': 2,
                'subject_ambiguous': False,
                'ambiguity_reason': '',
                'joints': {'right_shoulder': {'x': 0.5, 'y': 0.4, 'confidence': 0.85}},
            },
        ],
    }
    (folder / 'pose.json').write_text(json.dumps(pose_doc))
    done_render = post(c, f"worker/jobs/{rjob['id']}/complete", {'token': rjob['token']}, TOKEN)
    assert done_render.status_code == 200
    assert done_render.json['result']['pose_status'] == 'ok'

    # Listing and job fetch never leak pose_artifact while keeping pose_status.
    assert 'pose_artifact' not in c.get(f"/api/coaching/jobs/{rjob['id']}").json['result']
    listed = {j['id']: j for j in c.get(f'/api/coaching/sessions/{ID}').json['jobs']}
    assert listed[rjob['id']]['result']['pose_status'] == 'ok'
    assert 'pose_artifact' not in listed[rjob['id']]['result']

    # Creating AI request with subject_hint maps pose onto evidence_frames & suppresses ambiguous frame-001.
    ai_req = post(
        c,
        'requests',
        dict(
            export_id=rjob['id'],
            prompt='Check trophy pose',
            consent=True,
            subject_hint='near-court server',
            request_id='pose-ai-req-1',
        ),
    )
    assert ai_req.status_code == 201
    assert ai_req.json['body']['has_pose_enrichment'] is True
    ai_job = claimed(c)
    assert ai_job['id'] == ai_req.json['id']
    manifest = pin(app, c, ai_job, folder)
    complete(c, ai_job, json.dumps(dict(schema_version=2, summary='Pose test.', suggestions=[suggestion(manifest)])))

    ann = [a for a in annotations(c) if a['job_id'] == ai_job['id']][0]
    assert ann['provenance']['has_pose_enrichment'] is True

    # Lazy /pose endpoint returns mapped enrichment with frame-000 intact and multi-person frame-001 suppressed.
    pose_resp = c.get(f"/api/coaching/evidence/{ai_job['id']}/pose")
    assert pose_resp.status_code == 200
    f0_id, f1_id = manifest['frames'][0]['frame_id'], manifest['frames'][1]['frame_id']
    assert pose_resp.json['frames'][f0_id]['joints']['right_shoulder']['x'] == 0.42
    assert pose_resp.json['frames'][f1_id]['subject_ambiguous'] is True
    assert pose_resp.json['frames'][f1_id]['joints'] == {}

    exported = c.get(f'/api/coaching/annotations/export/{ID}').json
    pose_ai_exports = [r for r in exported['responses'] if r['request']['id'] == ai_job['id']]
    assert pose_ai_exports and pose_ai_exports[0]['pose_enrichment']['frames'][f0_id]['joints']

    # Deleting the AI job deletes evidence_enrichment as well.
    assert c.delete(f"/api/coaching/jobs/{ai_job['id']}", headers={'X-Tennis-Request': '1'}, json={}).status_code == 200
    assert c.get(f"/api/coaching/evidence/{ai_job['id']}/pose").status_code == 404
