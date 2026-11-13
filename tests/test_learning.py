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


def test_asked_at_is_immutable_across_lifecycle_and_orders_reviews_with_legacy_fallback(setup, tmp_path):
    import sqlite3
    from coaching import Coaching
    app, root, data = setup
    c = app.test_client()
    person = player(c, 'Chrono')
    now = [1000.0]
    q = app.extensions['coaching']
    q.clock = lambda: now[0]

    # Create first review at t=1000, second review at t=1100.
    r1 = q.create('ai', dict(source_id=ID, clip_id=CLIP, publication=data['published_at'],
                             original_start=6, original_end=8,
                             player_context=dict(player_id=person['id'], profile_version=1)), 'asked-at-001')
    assert r1['asked_at'] == 1000.0
    now[0] = 1050.0
    # Idempotent retry with same request_id preserves original asked_at.
    r1_retry = q.create('ai', dict(source_id=ID, clip_id=CLIP, publication=data['published_at'],
                                   original_start=6, original_end=8,
                                   player_context=dict(player_id=person['id'], profile_version=1)), 'asked-at-001')
    assert r1_retry['id'] == r1['id'] and r1_retry['asked_at'] == 1000.0

    now[0] = 1100.0
    r2 = q.create('ai', dict(source_id=ID, clip_id=CLIP, publication=data['published_at'],
                             original_start=7, original_end=9,
                             player_context=dict(player_id=person['id'], profile_version=1)), 'asked-at-002')
    assert r2['asked_at'] == 1100.0

    # Complete r2 first (at t=1200), then complete and edit r1 later (at t=1500) so r1 has newer updated_at.
    now[0] = 1200.0
    c2 = q.claim('ai', 'worker-a')
    if c2['id'] == r1['id']:
        c1, c2 = c2, q.claim('ai', 'worker-b')
    else:
        c1 = q.claim('ai', 'worker-b')
    q.worker_update(r2['id'], c2['token'], 'calling')
    q.worker_update(r2['id'], c2['token'], 'complete', dict(answer='Second asked'))
    now[0] = 1250.0
    q.worker_update(r1['id'], c1['token'], 'calling')
    done1 = q.worker_update(r1['id'], c1['token'], 'complete', dict(answer='First asked'))
    now[0] = 1500.0
    edited1 = q.edit(r1['id'], done1['version'], 'cue', 'Edited later')
    assert edited1['asked_at'] == 1000.0 and edited1['updated'] == 1500.0
    assert q.get(r2['id'])['asked_at'] == 1100.0 and q.get(r2['id'])['updated'] == 1200.0

    # Simulate a legacy row whose asked_at is NULL (created before schema migration) with updated=900.
    with q.db() as db:
        db.execute("INSERT INTO jobs(id,role,state,body,request_id,fingerprint,worker,token,expires,version,updated,result,asked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                   ('legacy001', 'ai', 'completed', json.dumps(dict(source_id=ID, clip_id=CLIP, publication=data['published_at'],
                    original_start=5, original_end=7, player_context=dict(player_id=person['id'], profile_version=1))),
                    'legacy-req-001', 'legacy-fp', None, None, 0, 1, 900.0, json.dumps(dict(answer='Legacy review'))))
        db.commit()
    assert q.get('legacy001')['asked_at'] is None

    # Coaching session listing and Learning API order by asked_at (falling back to updated when asked_at is None).
    session_jobs = [j for j in c.get('/api/coaching/sessions/' + ID).json['jobs'] if j['role'] == 'ai']
    assert [j['id'] for j in session_jobs] == [r2['id'], r1['id'], 'legacy001']
    learning_reviews = c.get('/api/learning?player_id=' + person['id']).json['entries'][0]['reviews']
    assert [r['id'] for r in learning_reviews] == [r2['id'], r1['id'], 'legacy001']
    assert [r['asked_at'] for r in learning_reviews] == [1100.0, 1000.0, None]

    # Verify automatic ALTER TABLE migration on an existing database without asked_at column.
    old_db = tmp_path / 'legacy_coaching.sqlite3'
    conn = sqlite3.connect(old_db)
    conn.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, role TEXT, state TEXT, body TEXT, request_id TEXT UNIQUE, fingerprint TEXT, worker TEXT, token TEXT, expires REAL, version INTEGER, updated REAL, result TEXT)')
    conn.execute("INSERT INTO jobs VALUES('oldjob','ai','completed','{\"source_id\":\"x\"}','req-old','fp-old',NULL,NULL,0,1,500.0,'{\"answer\":\"ok\"}')")
    conn.commit()
    conn.close()
    migrated_q = Coaching(old_db)
    assert migrated_q.get('oldjob')['asked_at'] is None


def _structured_review(app, data, req_id, pid, suggestions, start=6, end=8):
    from worker.tennis_coach.annotations import SCHEMA_VERSION, CAPABILITY, PROMPT_VERSION, evidence_manifest
    q = app.extensions['coaching']
    frames = [
        dict(frame_id=f'f_{req_id}_1', order=1, sha256='a' * 64, original_seconds=float(start), clip_seconds=0.0, excerpt_seconds=0.0),
    ]
    body = dict(
        source_id=ID,
        clip_id=CLIP,
        publication=data['published_at'],
        export_id='exp_' + req_id,
        original_start=start,
        original_end=end,
        crop=[0, 0, 1, 1],
        speed=1.0,
        evidence_frames=frames,
        required_capability=CAPABILITY,
        prompt_version=PROMPT_VERSION,
        player_context=dict(player_id=pid, profile_version=1, height_cm=175, level='intermediate'),
    )
    job = q.create('ai', body, req_id)
    claim = q.claim('ai', 'worker-struct', [CAPABILITY])
    assert claim['id'] == job['id']
    manifest = evidence_manifest(claim)
    q.worker_update(job['id'], claim['token'], 'evidence', manifest)
    q.worker_update(job['id'], claim['token'], 'calling')
    answer = json.dumps(dict(
        schema_version=SCHEMA_VERSION,
        summary='Structured summary ' + req_id,
        suggestions=suggestions,
    ))
    completed = q.worker_update(job['id'], claim['token'], 'complete', dict(answer=answer, analysis_mode='timestamped_frames'))
    with q.db() as db:
        annots = [dict(r) for r in db.execute('SELECT * FROM annotations WHERE job_id=? ORDER BY id', (job['id'],)).fetchall()]
    return completed, annots


def _plan_answer(summary, summary_eids, focus_areas, uncertainties=None, unassessed=None, progress_notes='', next_requests=None):
    from worker.tennis_coach.annotations import LEARNING_PLAN_SCHEMA_VERSION
    return json.dumps(dict(
        schema_version=LEARNING_PLAN_SCHEMA_VERSION,
        observed_summary=summary,
        summary_evidence_ids=summary_eids,
        focus_areas=focus_areas,
        uncertainties=uncertainties or ['Sampled frames cannot verify exact ball contact.'],
        unassessed_areas=unassessed or ['Serve and return were not present in the reviewed clips.'],
        progress_notes=progress_notes,
        next_evidence_requests=next_requests or ['Record 5 baseline rallies from behind the baseline.'],
    ))


def _post_json(client, url, payload, token=None):
    headers = {'X-Tennis-Request': '1'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    return client.post(url, json=payload, headers=headers)


def test_learning_plan_lifecycle_noop_freshness_incremental_citations_and_dismissal(setup):
    from worker.tennis_coach.annotations import CAPABILITY, LEARNING_PLAN_CAPABILITY
    app, root, data = setup
    app.config['COACH_WORKER_TOKEN'] = 'secret-worker-token'
    c = app.test_client()
    alex = player(c, 'Alex')
    blair = player(c, 'Blair')
    roster(c, [alex['id']])

    # 1. With zero reviews, player cannot generate an AI learning plan yet.
    s0 = c.get('/api/learning?player_id=' + alex['id']).json['plan_state']
    assert s0['enabled'] is True
    assert s0['active_plan'] is None
    assert s0['eligibility']['can_generate'] is False
    assert s0['eligibility']['reason'] == 'no_reviews'
    assert _post_json(c, '/api/learning/plan', dict(
        player_id=alex['id'], consent=True, expected_parent_revision=None,
        input_digest=s0['eligibility']['input_digest'], request_id='plan-empty-001',
    )).status_code == 400

    # 2. Add a structured review for Alex (with 2 suggestions) and one for Blair.
    job1, annots1 = _structured_review(app, data, 'rev-alex-1', alex['id'], [
        dict(
            title='Split step timing', topic='Footwork', observation='Late landing before opponent contact.',
            cue='Land split step just before opponent contact', drill='Shadow split-step rhythm',
            uncertainty='Camera angle hides opponent contact.', evidence_frame_ids=['f_rev-alex-1_1'],
            applies_to=[dict(start_seconds=6.2, end_seconds=7.4)],
        ),
        dict(
            title='Unit turn preparation', topic='Forehand', observation='Shoulders turn late.',
            cue='Turn shoulders as ball crosses net', drill='Early unit turn feeds',
            uncertainty='Single rally sample.', evidence_frame_ids=['f_rev-alex-1_1'],
            applies_to=[dict(start_seconds=6.5, end_seconds=7.8)],
        ),
    ])
    _, blair_annots = _structured_review(app, data, 'rev-blair-1', blair['id'], [
        dict(
            title='Blair backhand', topic='Backhand', observation='Blair cue.',
            cue='Blair backhand cue', drill='Blair drill', uncertainty='',
            evidence_frame_ids=['f_rev-blair-1_1'], applies_to=[dict(start_seconds=6.0, end_seconds=7.0)],
        ),
    ])
    # Accept the first suggestion so provenance distinguishes user_confirmed vs unconfirmed_ai_proposal.
    assert put(c, '/api/coaching/annotations/' + annots1[0]['id'], dict(
        publication=data['published_at'], version=1, status='accepted',
    )).status_code == 200

    s1 = c.get('/api/learning?player_id=' + alex['id']).json['plan_state']
    assert s1['eligibility']['can_generate'] is True
    assert s1['eligibility']['reason'] == 'initial_eligible'
    assert s1['eligibility']['valid_reviews_count'] == 2
    assert s1['eligibility']['new_or_revised_reviews_count'] == 2
    digest1 = s1['eligibility']['input_digest']

    # Missing consent or disabled worker checks.
    assert _post_json(c, '/api/learning/plan', dict(
        player_id=alex['id'], consent=False, expected_parent_revision=None,
        input_digest=digest1, request_id='plan-noconsent-001',
    )).status_code == 400
    app.config['COACH_WORKER_TOKEN'] = ''
    assert _post_json(c, '/api/learning/plan', dict(
        player_id=alex['id'], consent=True, expected_parent_revision=None,
        input_digest=digest1, request_id='plan-noworker-001',
    )).status_code == 503
    app.config['COACH_WORKER_TOKEN'] = 'secret-worker-token'

    # Queue initial learning plan.
    res_plan1 = _post_json(c, '/api/learning/plan', dict(
        player_id=alex['id'], consent=True, expected_parent_revision=None,
        input_digest=digest1, request_id='plan-alex-rev1',
    ))
    assert res_plan1.status_code == 201
    plan_job1 = res_plan1.json
    assert plan_job1['body']['purpose'] == 'learning_plan'
    assert plan_job1['body']['required_capability'] == LEARNING_PLAN_CAPABILITY
    assert 'source_id' not in plan_job1['body']

    # Idempotent retry with same request_id returns same job; different request_id while in-flight returns 409.
    retry_same = _post_json(c, '/api/learning/plan', dict(
        player_id=alex['id'], consent=True, expected_parent_revision=None,
        input_digest=digest1, request_id='plan-alex-rev1',
    ))
    assert retry_same.status_code == 201 and retry_same.json['id'] == plan_job1['id']
    dup_inflight = _post_json(c, '/api/learning/plan', dict(
        player_id=alex['id'], consent=True, expected_parent_revision=None,
        input_digest=digest1, request_id='plan-alex-dup',
    ))
    assert dup_inflight.status_code == 409

    # Legacy AI worker without learning-plan-v1 capability cannot claim the job.
    q = app.extensions['coaching']
    assert q.claim('ai', 'legacy-ai-worker', [CAPABILITY]) is None
    claimed1 = q.claim('ai', 'upgraded-ai-worker', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    assert claimed1 is not None and claimed1['id'] == plan_job1['id']

    # Complete initial plan, including one valid citation and one cross-player/hallucinated citation that must be stripped.
    eid_a0 = 'coach:' + annots1[0]['id']
    eid_a1 = 'coach:' + annots1[1]['id']
    eid_blair = 'coach:' + blair_annots[0]['id']
    q.worker_update(claimed1['id'], claimed1['token'], 'calling')
    ans1 = _plan_answer(
        summary='Alex shows solid early unit turn when reminded, with split step timing in progress.',
        summary_eids=[eid_a0, eid_blair, 'coach:nonexistent'],
        focus_areas=[
            dict(
                item_key='invented_key_from_model',
                topic='Footwork',
                title='Split step before opponent contact',
                observed_issue='Landing late on split step.',
                cue='Land split step just before opponent contact',
                drill='Shadow split-step rhythm',
                uncertainty='Opponent contact out of frame.',
                evidence_ids=[eid_a0, eid_blair],
            ),
            dict(
                item_key='',
                topic='Forehand',
                title='Early shoulder turn',
                observed_issue='Late preparation.',
                cue='Turn shoulders as ball crosses net',
                drill='Cross-court unit turn feeds',
                uncertainty='',
                evidence_ids=[eid_a1],
            ),
        ],
    )
    done_resp = _post_json(c, f"/api/coaching/worker/jobs/{claimed1['id']}/complete", dict(
        token=claimed1['token'],
        result=dict(answer=ans1, analysis_mode='learning_plan', model='gpt-5-codex', elapsed_seconds=2.5),
    ), token='secret-worker-token')
    assert done_resp.status_code == 200
    # Idempotent completion retry with same token does not duplicate revisions or practice logs.
    done_retry = _post_json(c, f"/api/coaching/worker/jobs/{claimed1['id']}/complete", dict(
        token=claimed1['token'],
        result=dict(answer=ans1, analysis_mode='learning_plan', model='gpt-5-codex', elapsed_seconds=2.5),
    ), token='secret-worker-token')
    assert done_retry.status_code == 200

    # 3. Verify immediate post-completion freshness: auto-seeded practice rows (user_version=0) do NOT make plan stale!
    s2 = c.get('/api/learning?player_id=' + alex['id']).json['plan_state']
    assert s2['active_plan']['revision'] == 1
    assert s2['active_plan']['input_digest'] == digest1
    assert s2['eligibility']['can_generate'] is False
    assert s2['eligibility']['reason'] == 'up_to_date'
    assert s2['eligibility']['new_or_revised_reviews_count'] == 0
    assert s2['eligibility']['removed_reviews_count'] == 0
    assert s2['eligibility']['practice_log_changes_count'] == 0
    assert s2['eligibility']['input_digest'] == digest1
    assert len(s2['revisions']) == 1
    assert len(s2['practice_logs']) == 2

    # Stripped cross-player and hallucinated citations recorded warnings and were removed.
    active_plan_data = s2['active_plan']['plan']
    assert active_plan_data['summary_evidence_ids'] == [eid_a0]
    assert active_plan_data['focus_areas'][0]['evidence_ids'] == [eid_a0]
    assert active_plan_data['focus_areas'][0]['item_key'].startswith('fi_')
    assert active_plan_data['validation_warnings']
    assert s2['active_plan']['citations'][eid_a0]['provenance_trust'] == 'user_confirmed'
    assert s2['active_plan']['citations'][eid_a1]['provenance_trust'] == 'unconfirmed_ai_proposal'
    assert '/review/' in s2['active_plan']['citations'][eid_a0]['study_url']

    # No-op POST with zero subsequent changes is rejected with 409 Conflict.
    noop_post = _post_json(c, '/api/learning/plan', dict(
        player_id=alex['id'], consent=True, expected_parent_revision=1,
        input_digest=digest1, request_id='plan-alex-noop',
    ))
    assert noop_post.status_code == 409
    assert 'up to date' in noop_post.json['error']

    # 4. Dismissing ALL suggestions on a structured coaching job must NOT fall back to a freeform coach-response:<job_id>!
    job2, annots2 = _structured_review(app, data, 'rev-alex-dismissed', alex['id'], [
        dict(
            title='Temporary suggestion', topic='Serve', observation='To be dismissed.',
            cue='Dismiss me', drill='', uncertainty='',
            evidence_frame_ids=['f_rev-alex-dismissed_1'], applies_to=[dict(start_seconds=6.1, end_seconds=7.1)],
        ),
    ])
    assert put(c, '/api/coaching/annotations/' + annots2[0]['id'], dict(
        publication=data['published_at'], version=1, status='dismissed',
    )).status_code == 200
    # Because rev-alex-dismissed had structured annotations and all were dismissed, it produces 0 valid evidence items
    # and never falls back to coach-response:<job2_id>, so Revision 1 remains up_to_date!
    s_after_dismiss_all = c.get('/api/learning?player_id=' + alex['id']).json['plan_state']
    assert s_after_dismiss_all['eligibility']['valid_reviews_count'] == 2
    assert s_after_dismiss_all['eligibility']['can_generate'] is False

    # 5. Now dismiss eid_a1 (which WAS covered in Revision 1) and add a new review job3, plus edit a practice log item.
    assert put(c, '/api/coaching/annotations/' + annots1[1]['id'], dict(
        publication=data['published_at'], version=1, status='dismissed',
    )).status_code == 200
    job3 = review(app, data, 'rev-alex-freeform-3', alex['id'], start=6.5, end=7.5)
    eid_job3 = 'coach-response:' + job3['id']

    # Edit first practice log item (status + note + custom title) and add a standalone manual practice item.
    log0 = next(l for l in s2['practice_logs'] if l['item_key'] == active_plan_data['focus_areas'][0]['item_key'])
    trusted_key_0 = log0['item_key']
    upd_log = put(c, '/api/learning/practice-logs/' + log0['id'], dict(
        player_id=alex['id'], version=log0['version'], status='practiced',
        note='Practiced 20 minutes; felt much more balanced.',
        title='User custom split step title',
    ))
    assert upd_log.status_code == 200
    assert upd_log.json['user_edited_content'] is True
    assert upd_log.json['user_version'] == 1
    assert len(upd_log.json['revisions']) == 2

    manual_res = _post_json(c, '/api/learning/practice-logs', dict(
        player_id=alex['id'], title='Manual serve toss target', topic='Serve',
        cue='Consistent toss in front', drill='20 tosses without swinging',
        status='ongoing', note='Self-directed focus', request_id='manual-prac-001',
    ))
    assert manual_res.status_code == 201
    manual_log = manual_res.json

    # Check eligibility for Revision 2: 1 new review (eid_job3), 1 removed review (eid_a1), 2 practice log updates.
    s3 = c.get('/api/learning?player_id=' + alex['id']).json['plan_state']
    assert s3['eligibility']['can_generate'] is True
    assert s3['eligibility']['reason'] == 'updates_available'
    assert s3['eligibility']['new_or_revised_reviews_count'] == 1
    assert s3['eligibility']['removed_reviews_count'] == 1
    assert s3['eligibility']['practice_log_changes_count'] == 2
    # Outdated citation on active plan is flagged before update.
    assert s3['active_plan']['citations'][eid_a1]['currently_valid'] is False
    assert s3['active_plan']['citations'][eid_a1]['invalid_reason'] == 'dismissed'

    # Queue Revision 2 and inspect incremental snapshot:
    # delta_evidence has ONLY eid_job3; carried_citations has eid_a0; removed_evidence has eid_a1.
    res_plan2 = _post_json(c, '/api/learning/plan', dict(
        player_id=alex['id'], consent=True, expected_parent_revision=1,
        input_digest=s3['eligibility']['input_digest'], request_id='plan-alex-rev2',
    ))
    assert res_plan2.status_code == 201
    plan_job2 = res_plan2.json
    snap2 = plan_job2['body']['snapshot']
    assert [d['evidence_id'] for d in snap2['delta_evidence']] == [eid_job3]
    assert [c_item['evidence_id'] for c_item in snap2['carried_citations']] == [eid_a0]
    assert [r['evidence_id'] for r in snap2['removed_evidence']] == [eid_a1]

    # Complete Revision 2: model cites BOTH carried eid_a0 and new eid_job3, and also tries to cite removed eid_a1
    # (which must be stripped!). Model continues trusted_key_0 with a reworded title/cue.
    claimed2 = q.claim('ai', 'upgraded-ai-worker', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    q.worker_update(claimed2['id'], claimed2['token'], 'calling')
    ans2 = _plan_answer(
        summary='Updated plan combining carried split step evidence and new rally review.',
        summary_eids=[eid_a0, eid_job3, eid_a1],
        focus_areas=[
            dict(
                item_key=trusted_key_0,
                topic='Footwork',
                title='Model reworded title that should not overwrite user edit',
                observed_issue='Continuing split step timing.',
                cue='Model reworded cue',
                drill='Updated split step drill',
                uncertainty='',
                evidence_ids=[eid_a0, eid_job3, eid_a1],
            ),
        ],
        progress_notes='Player reported practicing split steps; video shows improved balance.',
    )
    assert _post_json(c, f"/api/coaching/worker/jobs/{claimed2['id']}/complete", dict(
        token=claimed2['token'],
        result=dict(answer=ans2, analysis_mode='learning_plan', model='gpt-5-codex', elapsed_seconds=2.1),
    ), token='secret-worker-token').status_code == 200

    s4 = c.get('/api/learning?player_id=' + alex['id']).json['plan_state']
    assert s4['active_plan']['revision'] == 2
    assert s4['eligibility']['can_generate'] is False
    assert s4['eligibility']['reason'] == 'up_to_date'
    # Carried citation eid_a0 AND delta citation eid_job3 are preserved; invalidated eid_a1 was stripped!
    assert s4['active_plan']['plan']['summary_evidence_ids'] == [eid_a0, eid_job3]
    assert s4['active_plan']['plan']['focus_areas'][0]['evidence_ids'] == [eid_a0, eid_job3]
    assert s4['active_plan']['plan']['focus_areas'][0]['item_key'] == trusted_key_0

    # User-edited status, note, and custom title on trusted_key_0 were preserved; standalone manual item also intact!
    logs_by_key = {l['item_key']: l for l in s4['practice_logs']}
    assert logs_by_key[trusted_key_0]['status'] == 'practiced'
    assert logs_by_key[trusted_key_0]['note'] == 'Practiced 20 minutes; felt much more balanced.'
    assert logs_by_key[trusted_key_0]['title'] == 'User custom split step title'
    assert manual_log['item_key'] in logs_by_key
    assert logs_by_key[manual_log['item_key']]['status'] == 'ongoing'

    # Revision history endpoint for practice log returns append-only revisions in order.
    rev_hist = c.get(f"/api/learning/practice-logs/{log0['id']}/revisions?player_id={alex['id']}").json['revisions']
    assert [r['actor'] for r in rev_hist] == ['ai_plan', 'human', 'ai_plan']
    assert [r['version'] for r in rev_hist] == [1, 2, 3]


def test_learning_plan_overflow_backlog_24_reviews_and_20_practice_logs(setup):
    from worker.tennis_coach.annotations import CAPABILITY, LEARNING_PLAN_CAPABILITY
    app, root, data = setup
    app.config['COACH_WORKER_TOKEN'] = 'secret-worker-token'
    c = app.test_client()
    person = player(c, 'BacklogPlayer')
    roster(c, [person['id']])
    q = app.extensions['coaching']

    # Create 28 coaching reviews (4 over the 24-item cap) and 23 manual practice logs (3 over the 20-log cap).
    for i in range(28):
        review(app, data, f'overflow-rev-{i:03d}', person['id'], start=6.0, end=8.0)
    for i in range(23):
        res = _post_json(c, '/api/learning/practice-logs', dict(
            player_id=person['id'],
            title=f'Manual drill {i + 1}',
            topic='Rally',
            cue=f'Cue {i + 1}',
            drill=f'Drill {i + 1}',
            status='ongoing',
            note=f'Note {i + 1}',
            request_id=f'overflow-prac-{i:03d}',
        ))
        assert res.status_code == 201

    # Storage/read has NO 20-item cap: all 23 practice logs are returned.
    ps0 = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    assert len(ps0['practice_logs']) == 23
    assert ps0['eligibility']['valid_reviews_count'] == 28
    assert ps0['eligibility']['new_or_revised_reviews_count'] == 28
    assert ps0['eligibility']['backlog_reviews_count'] == 4
    assert ps0['eligibility']['practice_log_changes_count'] == 23
    assert ps0['eligibility']['backlog_practice_logs_count'] == 3

    # Generate Revision 1: sends 24 reviews and 20 practice logs.
    job1 = _post_json(c, '/api/learning/plan', dict(
        player_id=person['id'], consent=True, expected_parent_revision=None,
        input_digest=ps0['eligibility']['input_digest'], request_id='overflow-plan-1',
    )).json
    snap1 = job1['body']['snapshot']
    assert len(snap1['delta_evidence']) == 24
    assert snap1['truncated_evidence_count'] == 4
    assert len(snap1['covered_evidence_map']) == 24
    assert len(snap1['practice_logs']) == 20
    assert snap1['truncated_logs_count'] == 3
    assert len(snap1['covered_logs_map']) == 20

    claim1 = q.claim('ai', 'worker-1', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    q.worker_update(claim1['id'], claim1['token'], 'calling')
    first_eid = snap1['delta_evidence'][0]['evidence_id']
    # Find an unsent overflow review ID and verify the parser refuses to allow citing unsent overflow items!
    all_job_eids = {f"coach-response:{r['id']}" for e in c.get('/api/learning?player_id=' + person['id']).json['entries'] for r in e['reviews']}
    overflow_eid = next(eid for eid in all_job_eids if eid not in snap1['covered_evidence_map'])
    ans1 = _plan_answer('First batch plan.', [first_eid, overflow_eid], [])
    q.worker_update(claim1['id'], claim1['token'], 'complete', dict(answer=ans1, analysis_mode='learning_plan'))

    # Immediately after Revision 1 completes, the 4 overflow reviews and 3 overflow practice logs remain eligible!
    ps1 = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    assert ps1['active_plan']['revision'] == 1
    assert ps1['active_plan']['plan']['summary_evidence_ids'] == [first_eid]
    assert ps1['active_plan']['coverage']['covered_reviews_count'] == 24
    assert ps1['active_plan']['coverage']['truncated_reviews_count'] == 4
    assert ps1['eligibility']['can_generate'] is True
    assert ps1['eligibility']['reason'] == 'updates_available'
    assert ps1['eligibility']['new_or_revised_reviews_count'] == 4
    assert ps1['eligibility']['backlog_reviews_count'] == 4
    assert ps1['eligibility']['practice_log_changes_count'] == 3
    assert ps1['eligibility']['backlog_practice_logs_count'] == 3

    # Generate Revision 2: sends ONLY the 4 remaining overflow reviews in delta_evidence and covers the remaining 3 logs.
    job2 = _post_json(c, '/api/learning/plan', dict(
        player_id=person['id'], consent=True, expected_parent_revision=1,
        input_digest=ps1['eligibility']['input_digest'], request_id='overflow-plan-2',
    )).json
    snap2 = job2['body']['snapshot']
    assert len(snap2['delta_evidence']) == 4
    assert snap2['truncated_evidence_count'] == 0
    assert len(snap2['covered_evidence_map']) == 28
    assert len(snap2['carried_citations']) == 24
    assert snap2['truncated_logs_count'] == 0
    assert len(snap2['covered_logs_map']) == 23

    claim2 = q.claim('ai', 'worker-1', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    q.worker_update(claim2['id'], claim2['token'], 'calling')
    ans2 = _plan_answer('Second batch complete.', [first_eid, overflow_eid], [])
    q.worker_update(claim2['id'], claim2['token'], 'complete', dict(answer=ans2, analysis_mode='learning_plan'))

    # Now all 28 reviews and 23 logs are covered, so the plan is up_to_date!
    ps2 = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    assert ps2['active_plan']['revision'] == 2
    assert ps2['active_plan']['coverage']['covered_reviews_count'] == 28
    assert ps2['active_plan']['coverage']['truncated_reviews_count'] == 0
    assert ps2['eligibility']['can_generate'] is False
    assert ps2['eligibility']['reason'] == 'up_to_date'
    assert ps2['eligibility']['new_or_revised_reviews_count'] == 0
    assert ps2['eligibility']['backlog_reviews_count'] == 0
    assert ps2['eligibility']['practice_log_changes_count'] == 0
    assert ps2['eligibility']['backlog_practice_logs_count'] == 0


def test_learning_plan_inflight_input_changes_and_stale_completion_audit_only(setup):
    from worker.tennis_coach.annotations import CAPABILITY, LEARNING_PLAN_CAPABILITY
    app, root, data = setup
    app.config['COACH_WORKER_TOKEN'] = 'secret-worker-token'
    c = app.test_client()
    person = player(c, 'ConcurrentPlayer')
    roster(c, [person['id']])
    q = app.extensions['coaching']
    now = [2000.0]
    q.clock = lambda: now[0]

    r1 = review(app, data, 'conc-rev-1', person['id'], start=6.0, end=7.0)
    ps0 = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    job1 = _post_json(c, '/api/learning/plan', dict(
        player_id=person['id'], consent=True, expected_parent_revision=None,
        input_digest=ps0['eligibility']['input_digest'], request_id='conc-plan-1',
    )).json
    claim1 = q.claim('ai', 'worker-a', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    q.worker_update(claim1['id'], claim1['token'], 'calling')

    # While job1 is in flight (calling), user adds a new review r2, adds a manual practice log, and updates profile level.
    now[0] = 2010.0
    r2 = review(app, data, 'conc-rev-2', person['id'], start=7.0, end=8.0)
    manual_log = _post_json(c, '/api/learning/practice-logs', dict(
        player_id=person['id'], title='Inflight manual focus', topic='Recovery',
        cue='Recover to middle', drill='Split and recover', status='ongoing',
        note='Added during call', request_id='inflight-prac-1',
    )).json
    assert put(c, '/api/players/' + person['id'], dict(
        name='ConcurrentPlayer', height_cm=180, level='experienced', version=person['version'],
    )).status_code == 200

    # Complete job1: it must preserve its frozen snapshot (only r1 covered) and leave r2 + manual_log + profile change pending!
    now[0] = 2020.0
    ans1 = _plan_answer('Initial plan from r1.', ['coach-response:' + r1['id']], [
        dict(item_key='', topic='Recovery', title='AI focus 1', observed_issue='Issue 1', cue='Cue 1', drill='Drill 1', uncertainty='', evidence_ids=['coach-response:' + r1['id']]),
    ])
    q.worker_update(claim1['id'], claim1['token'], 'complete', dict(answer=ans1, analysis_mode='learning_plan'))

    ps1 = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    assert ps1['active_plan']['revision'] == 1
    assert ps1['active_plan']['coverage']['covered_reviews_count'] == 1
    assert ps1['eligibility']['can_generate'] is True
    assert ps1['eligibility']['reason'] == 'updates_available'
    assert ps1['eligibility']['new_or_revised_reviews_count'] == 1
    assert ps1['eligibility']['practice_log_changes_count'] == 1
    assert ps1['eligibility']['profile_changed'] is True

    # Now test needs_review expiry and stale completion audit-only supersession:
    # Queue job_stale (parent_revision=1), transition to calling, then let its lease expire (>90s).
    now[0] = 2100.0
    job_stale = _post_json(c, '/api/learning/plan', dict(
        player_id=person['id'], consent=True, expected_parent_revision=1,
        input_digest=ps1['eligibility']['input_digest'], request_id='conc-plan-stale',
    )).json
    claim_stale = q.claim('ai', 'worker-stale', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    q.worker_update(claim_stale['id'], claim_stale['token'], 'calling')

    # Advance clock past 90s lease expiry.
    now[0] = 2200.0
    ps_expired = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    assert ps_expired['pending_job']['id'] == job_stale['id']
    assert ps_expired['pending_job']['state'] == 'needs_review'
    assert ps_expired['eligibility']['requires_ambiguous_retry'] is True

    # Calling POST /api/learning/plan without retry_ambiguous=True must fail with 409.
    no_retry = _post_json(c, '/api/learning/plan', dict(
        player_id=person['id'], consent=True, expected_parent_revision=1,
        input_digest=ps_expired['eligibility']['input_digest'], request_id='conc-plan-no-retry',
    ))
    assert no_retry.status_code == 409
    assert 'needs review' in no_retry.json['error']

    # With retry_ambiguous=True, replacement job2 is queued and completed as active Revision 2.
    job2 = _post_json(c, '/api/learning/plan', dict(
        player_id=person['id'], consent=True, expected_parent_revision=1,
        input_digest=ps_expired['eligibility']['input_digest'], retry_ambiguous=True,
        request_id='conc-plan-retry-2',
    )).json
    claim2 = q.claim('ai', 'worker-2', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    q.worker_update(claim2['id'], claim2['token'], 'calling')
    ans2 = _plan_answer('Active revision 2 summary.', ['coach-response:' + r2['id']], [
        dict(item_key='', topic='Footwork', title='Active Rev 2 Focus', observed_issue='Obs', cue='Active Cue 2', drill='Drill 2', uncertainty='', evidence_ids=['coach-response:' + r2['id']]),
    ])
    q.worker_update(claim2['id'], claim2['token'], 'complete', dict(answer=ans2, analysis_mode='learning_plan'))

    ps2 = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    assert ps2['active_plan']['revision'] == 2
    practice_Snapshot_before_stale = copy.deepcopy(ps2['practice_logs'])

    # Now simulate a late completion whose parent_revision=1 is stale (active_revision is already 2).
    with q.db() as db:
        db.execute('BEGIN IMMEDIATE')
        stale_row = db.execute('SELECT * FROM jobs WHERE id=?', (job_stale['id'],)).fetchone()
        stale_ans = _plan_answer('Stale late answer that must be audit-only.', ['coach-response:' + r1['id']], [
            dict(item_key='', topic='Stale', title='Stale Focus Should Not Seed', observed_issue='Stale', cue='Stale cue', drill='Stale drill', uncertainty='', evidence_ids=[]),
        ])
        stale_res = q.save_learning_plan(db, stale_row, dict(answer=stale_ans, analysis_mode='learning_plan'))
        db.commit()
    assert stale_res['plan_revision'] == 3
    assert stale_res['plan_status'] == 'superseded'

    # Verify active_plan is STILL Revision 2, practice_logs were NOT mutated at all, and Revision 3 is recorded as superseded in revisions!
    ps3 = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    assert ps3['active_plan']['revision'] == 2
    assert ps3['active_plan']['plan']['observed_summary'] == 'Active revision 2 summary.'
    assert ps3['practice_logs'] == practice_Snapshot_before_stale
    revs_by_num = {r['revision']: r for r in ps3['revisions']}
    assert revs_by_num[1]['status'] == 'historical'
    assert revs_by_num[2]['status'] == 'active'
    assert revs_by_num[3]['status'] == 'superseded'


def test_learning_plan_worker_execution_and_route_isolation(setup, tmp_path):
    import stat
    from worker.tennis_coach.worker import Worker
    app, root, data = setup
    app.config['COACH_WORKER_TOKEN'] = 'secret-worker-token'
    c = app.test_client()
    person = player(c, 'WorkerPlayer')
    roster(c, [person['id']])
    r1 = review(app, data, 'worker-rev-1', person['id'], start=6.0, end=8.0)

    ps0 = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    plan_job = _post_json(c, '/api/learning/plan', dict(
        player_id=person['id'], consent=True, expected_parent_revision=None,
        input_digest=ps0['eligibility']['input_digest'], request_id='worker-plan-req-1',
    )).json

    # Create a fake codex executable that verifies no --image flag is passed and writes a valid learning plan JSON.
    fake_codex = tmp_path / 'fake-codex'
    args_log = tmp_path / 'codex-args.json'
    ans_json = _plan_answer(
        'Synthesized by worker without images.',
        ['coach-response:' + r1['id']],
        [dict(item_key='', topic='Forehand', title='Worker Focus', observed_issue='Obs', cue='Keep head still', drill='Target rally', uncertainty='', evidence_ids=['coach-response:' + r1['id']])],
    )
    fake_codex.write_text(
        '#!/usr/bin/env python3\n'
        'import json, pathlib, sys\n'
        'args = sys.argv[1:]\n'
        'if args[:2] == ["login", "status"]:\n'
        '    print("Logged in using ChatGPT")\n'
        '    sys.exit(0)\n'
        f'pathlib.Path({str(args_log)!r}).write_text(json.dumps(args))\n'
        'out_idx = args.index("-o") + 1\n'
        f'pathlib.Path(args[out_idx]).write_text({ans_json!r})\n'
        'print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 120, "output_tokens": 80}}))\n'
    )
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IEXEC)

    artifact_dir = tmp_path / 'artifacts'
    artifact_dir.mkdir(parents=True, exist_ok=True)
    worker_cfg = dict(
        server_url='http://localhost',
        worker_token='secret-worker-token',
        local_state=str(tmp_path / 'worker_state'),
        artifact_root=str(artifact_dir),
        codex=str(fake_codex),
        model='gpt-5-codex',
    )
    w = Worker(worker_cfg, 'ai')
    def local_api(path, body):
        resp = _post_json(c, '/api/coaching/' + path, body, token='secret-worker-token')
        if resp.status_code >= 400:
            raise ValueError(f"HTTP {resp.status_code}: {(resp.json or {}).get('error')}")
        return resp.json
    w.api = local_api

    assert w.once() is True
    recorded_args = json.loads(args_log.read_text())
    assert '--image' not in recorded_args
    assert '--output-schema' in recorded_args

    # Verify plan was persisted and all clip-specific endpoints cleanly isolate/reject the player-level learning_plan job.
    ps1 = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    assert ps1['active_plan']['revision'] == 1
    assert ps1['active_plan']['provenance']['usage'] == {'input_tokens': 120, 'output_tokens': 80}

    # Route isolation checks:
    q = app.extensions['coaching']
    assert _post_json(c, '/api/coaching/annotations', dict(
        job_id=plan_job['id'], publication=data['published_at'],
        content=dict(title='T', topic='', observation='', cue='C', drill='', uncertainty='', evidence_frame_ids=[], applies_to=[]),
        request_id='manual-on-plan-job',
    )).status_code == 400
    assert put(c, '/api/coaching/jobs/' + plan_job['id'], dict(
        version=q.get(plan_job['id'])['version'], action='cue', cue='Not a clip cue',
    )).status_code == 400
    assert c.get('/api/coaching/evidence/' + plan_job['id']).status_code == 404
    assert c.get(f"/api/annotations/coach-response:{plan_job['id']}/revisions").status_code == 404

    # Session coaching list, Moments, and Learning clip entries must NOT include the learning_plan job.
    session_job_ids = [j['id'] for j in c.get('/api/coaching/sessions/' + ID).json['jobs']]
    assert plan_job['id'] not in session_job_ids
    moment_ids = [m['id'] for m in c.get(f'/api/recordings/{ID}/moments').json['moments']]
    assert f"coach-response:{plan_job['id']}" not in moment_ids
    learning_review_ids = [r['id'] for e in c.get('/api/learning?player_id=' + person['id']).json['entries'] for r in e['reviews']]
    assert plan_job['id'] not in learning_review_ids


def test_learning_plan_player_reassignment_invalidation_practice_conflict_and_malformed_output(setup):
    from worker.tennis_coach.annotations import CAPABILITY, LEARNING_PLAN_CAPABILITY
    app, root, data = setup
    app.config['COACH_WORKER_TOKEN'] = 'secret-worker-token'
    c = app.test_client()
    alex = player(c, 'Alex')
    blair = player(c, 'Blair')
    roster(c, [alex['id']])
    q = app.extensions['coaching']

    r1 = review(app, data, 'reassign-rev-1', alex['id'], start=5.5, end=7.5)
    r2 = review(app, data, 'reassign-rev-2', alex['id'], start=16.0, end=18.0)
    eid1 = 'coach-response:' + r1['id']
    eid2 = 'coach-response:' + r2['id']

    ps0 = c.get('/api/learning?player_id=' + alex['id']).json['plan_state']
    job1 = _post_json(c, '/api/learning/plan', dict(
        player_id=alex['id'], consent=True, expected_parent_revision=None,
        input_digest=ps0['eligibility']['input_digest'], request_id='reassign-plan-1',
    )).json
    claim1 = q.claim('ai', 'worker-1', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    q.worker_update(claim1['id'], claim1['token'], 'calling')
    ans1 = _plan_answer('Initial plan covering both clips.', [eid1, eid2], [])
    q.worker_update(claim1['id'], claim1['token'], 'complete', dict(answer=ans1, analysis_mode='learning_plan'))

    # Reassign clip 1 (0..2s) to Blair; clip 2 (2..4s) stays with Alex.
    session_data = c.get('/api/sessions/' + ID).json
    clip1 = session_data['clips'][0]
    assert put(c, f"/api/sessions/{ID}/clips/{clip1['id']}/player", dict(
        player_id=blair['id'], version=clip1['player_assignment']['version'], publication=session_data['published_at'],
    )).status_code == 200

    # eid1 is now invalidated with reason 'reassigned_player', making the plan eligible for update!
    ps1 = c.get('/api/learning?player_id=' + alex['id']).json['plan_state']
    assert ps1['eligibility']['can_generate'] is True
    assert ps1['eligibility']['removed_reviews_count'] == 1
    assert ps1['active_plan']['citations'][eid1]['currently_valid'] is False
    assert ps1['active_plan']['citations'][eid1]['invalid_reason'] == 'reassigned_player'
    assert ps1['active_plan']['citations'][eid2]['currently_valid'] is True

    # Test conflict-safe practice log update (409 on stale version) and cross-player isolation (404).
    prac = _post_json(c, '/api/learning/practice-logs', dict(
        player_id=alex['id'], title='Footwork drill', topic='Footwork',
        cue='Quick steps', drill='Ladder', status='ongoing', note='v1', request_id='prac-conf-1',
    )).json
    assert put(c, '/api/learning/practice-logs/' + prac['id'], dict(
        player_id=alex['id'], version=1, status='practiced', note='v2',
    )).status_code == 200
    assert put(c, '/api/learning/practice-logs/' + prac['id'], dict(
        player_id=alex['id'], version=1, status='working_well', note='stale version',
    )).status_code == 409
    assert put(c, '/api/learning/practice-logs/' + prac['id'], dict(
        player_id=blair['id'], version=2, status='working_well', note='wrong player',
    )).status_code == 404

    # Test malformed model output handling: unstructured output is recorded as status='invalid' in revisions
    # without replacing active_plan Revision 1 or advancing covered_state_digest, so immediate retry stays eligible.
    ps2 = c.get('/api/learning?player_id=' + alex['id']).json['plan_state']
    job2 = _post_json(c, '/api/learning/plan', dict(
        player_id=alex['id'], consent=True, expected_parent_revision=1,
        input_digest=ps2['eligibility']['input_digest'], request_id='reassign-plan-2',
    )).json
    assert job2['body']['snapshot']['removed_evidence'] == [dict(evidence_id=eid1, reason='reassigned_player')]
    claim2 = q.claim('ai', 'worker-1', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    q.worker_update(claim2['id'], claim2['token'], 'calling')
    q.worker_update(claim2['id'], claim2['token'], 'complete', dict(answer='{"not_valid_schema": true}', analysis_mode='learning_plan'))

    ps3 = c.get('/api/learning?player_id=' + alex['id']).json['plan_state']
    assert ps3['active_plan']['revision'] == 1
    assert ps3['active_plan']['plan']['observed_summary'] == 'Initial plan covering both clips.'
    assert ps3['eligibility']['can_generate'] is True
    revs_by_num = {r['revision']: r for r in ps3['revisions']}
    assert revs_by_num[1]['status'] == 'active'
    assert revs_by_num[2]['status'] == 'invalid'
    assert revs_by_num[2]['provenance']['parse_status'] == 'unstructured'
    assert revs_by_num[2]['provenance']['validation_warnings']

    # Also verify a malformed first plan for Blair does not activate a broken Revision 1 and stays immediately retryable.
    review(app, data, 'blair-rev-1', blair['id'], start=5.5, end=7.5)
    ps_blair_0 = c.get('/api/learning?player_id=' + blair['id']).json['plan_state']
    assert ps_blair_0['eligibility']['can_generate'] is True
    _post_json(c, '/api/learning/plan', dict(
        player_id=blair['id'], consent=True, expected_parent_revision=None,
        input_digest=ps_blair_0['eligibility']['input_digest'], request_id='blair-malformed-1',
    ))
    claim_b1 = q.claim('ai', 'worker-1', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    q.worker_update(claim_b1['id'], claim_b1['token'], 'calling')
    q.worker_update(claim_b1['id'], claim_b1['token'], 'complete', dict(answer='not json at all', analysis_mode='learning_plan'))
    ps_blair_1 = c.get('/api/learning?player_id=' + blair['id']).json['plan_state']
    assert ps_blair_1['active_plan'] is None
    assert ps_blair_1['revisions'][0]['status'] == 'invalid'
    assert ps_blair_1['eligibility']['can_generate'] is True
    assert ps_blair_1['eligibility']['expected_parent_revision'] is None


def test_learning_plan_citation_validation_and_immutable_evidence_revision_provenance(setup):
    from worker.tennis_coach.annotations import CAPABILITY, LEARNING_PLAN_CAPABILITY, parse_learning_plan
    app, root, data = setup
    app.config['COACH_WORKER_TOKEN'] = 'secret-worker-token'
    c = app.test_client()
    person = player(c, 'ProvenancePlayer')
    roster(c, [person['id']])
    q = app.extensions['coaching']

    # 1. Verify parse_learning_plan only allows citations actually supplied in delta_evidence or carried_citations.
    raw = _plan_answer('Summary text.', ['coach:supplied', 'coach:unsupplied_covered'], [
        dict(item_key='', topic='Serve', title='Focus', observed_issue='', cue='Cue', drill='Drill', uncertainty='', evidence_ids=['coach:supplied', 'coach:unsupplied_covered']),
    ])
    parsed = parse_learning_plan(raw, dict(
        covered_evidence_map={'coach:supplied': 'd1', 'coach:unsupplied_covered': 'd2'},
        delta_evidence=[dict(evidence_id='coach:supplied')],
        carried_citations=[],
        removed_evidence=[],
    ))
    assert parsed['summary_evidence_ids'] == ['coach:supplied']
    assert parsed['focus_areas'][0]['evidence_ids'] == ['coach:supplied']
    assert any('evidence reference removed' in w for w in parsed['validation_warnings'])

    # 2. Verify deferred review revision under the 24-item delta cap is not mixed with older coverage in carried_citations.
    r_old, anns = _structured_review(app, data, 'prov-old', person['id'], [
        dict(
            title='Old title', topic='Footwork', observation='Split step',
            cue='Old cue v1', drill='', uncertainty='',
            evidence_frame_ids=['f_prov-old_1'], applies_to=[dict(start_seconds=6.0, end_seconds=7.5)],
        ),
    ])
    eid_old = 'coach:' + anns[0]['id']

    ps0 = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    _post_json(c, '/api/learning/plan', dict(
        player_id=person['id'], consent=True, expected_parent_revision=None,
        input_digest=ps0['eligibility']['input_digest'], request_id='prov-plan-1',
    ))
    claim1 = q.claim('ai', 'worker-1', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    q.worker_update(claim1['id'], claim1['token'], 'calling')
    q.worker_update(claim1['id'], claim1['token'], 'complete', dict(
        answer=_plan_answer('Initial plan from v1.', [eid_old], []), analysis_mode='learning_plan',
    ))

    # Edit the annotation to v2, then add 24 newer reviews so eid_old@v2 is deferred out of the 24-item delta cap.
    assert put(c, f"/api/coaching/annotations/{anns[0]['id']}", dict(
        publication=data['published_at'], version=1, status='accepted',
        content=dict(
            title='Old title', topic='Footwork', observation='Split step',
            cue='Edited cue v2', drill='', uncertainty='',
            evidence_frame_ids=['f_prov-old_1'], applies_to=[dict(start_seconds=6.0, end_seconds=7.5)],
        ),
    )).status_code == 200
    for i in range(24):
        review(app, data, f'prov-new-{i:02d}', person['id'], start=6.0, end=8.0)

    ps1 = c.get('/api/learning?player_id=' + person['id']).json['plan_state']
    # Active plan Revision 1 citation still reflects frozen v1 cue and marks revision_changed=True, currently_valid=False.
    assert ps1['active_plan']['citations'][eid_old]['cue'] == 'Old cue v1'
    assert ps1['active_plan']['citations'][eid_old]['version'] == 1
    assert ps1['active_plan']['citations'][eid_old]['currently_valid'] is False
    assert ps1['active_plan']['citations'][eid_old]['revision_changed'] is True

    job2 = _post_json(c, '/api/learning/plan', dict(
        player_id=person['id'], consent=True, expected_parent_revision=1,
        input_digest=ps1['eligibility']['input_digest'], request_id='prov-plan-2',
    )).json
    snap2 = job2['body']['snapshot']
    carried = {item['evidence_id']: item for item in snap2['carried_citations']}[eid_old]
    assert carried['cue'] == 'Old cue v1'
    assert carried['version'] == 1
    assert carried['version_digest'] == snap2['covered_evidence_map'][eid_old]


def test_learning_reviews_and_plan_attribute_explicit_selected_subject_and_exclude_none(setup):
    from worker.tennis_coach.annotations import CAPABILITY, LEARNING_PLAN_CAPABILITY
    from test_players import focus

    app, root, data = setup
    app.config['COACH_WORKER_TOKEN'] = 'secret-worker-token'
    c = app.test_client()
    alex = player(c, 'Alex')
    blair = player(c, 'Blair')
    dana = player(c, 'Dana')

    # Assign Alex as persistent main_player on CLIP; Blair is NOT assigned to CLIP or recording roster.
    roster(c, [alex['id']])
    assert focus(c, data, alex['id']).status_code == 200
    q = app.extensions['coaching']
    now = [2000.0]
    q.clock = lambda: now[0]

    # 1. Create a main_player review for Alex (t=2000), a selected_player review for Blair (t=2100),
    #    and an explicit No player profile ('none') review (t=2200) on the same clip.
    r_alex, anns_alex = _structured_review(app, data, 'subj-learn-alex', alex['id'], [
        dict(title='Alex split step', topic='Footwork', observation='Late split', cue='Split as opponent hits', drill='', uncertainty='',
             evidence_frame_ids=['f_subj-learn-alex_1'], applies_to=[dict(start_seconds=6.0, end_seconds=7.5)]),
    ])
    with q.db() as db:
        body_a = json.loads(db.execute('SELECT body FROM jobs WHERE id=?', (r_alex['id'],)).fetchone()['body'])
        body_a['subject_mode'] = 'main_player'
        db.execute('UPDATE jobs SET body=? WHERE id=?', (json.dumps(body_a), r_alex['id']))

    now[0] = 2100.0
    r_blair, anns_blair = _structured_review(app, data, 'subj-learn-blair', blair['id'], [
        dict(title='Blair unit turn', topic='Preparation', observation='Late shoulder turn', cue='Turn shoulders early', drill='Shadow unit turns', uncertainty='',
             evidence_frame_ids=['f_subj-learn-blair_1'], applies_to=[dict(start_seconds=6.2, end_seconds=7.8)]),
    ])
    with q.db() as db:
        body_b = json.loads(db.execute('SELECT body FROM jobs WHERE id=?', (r_blair['id'],)).fetchone()['body'])
        body_b['subject_mode'] = 'selected_player'
        body_b['subject_hint'] = 'far court white visor'
        db.execute('UPDATE jobs SET body=? WHERE id=?', (json.dumps(body_b), r_blair['id']))

    now[0] = 2200.0
    r_none = q.create('ai', dict(
        source_id=ID, clip_id=CLIP, publication=data['published_at'],
        original_start=6.0, original_end=8.0, player_context=None, subject_mode='none',
    ), 'subj-learn-none')
    claim_none = q.claim('ai', 'worker-1', [CAPABILITY])
    q.worker_update(claim_none['id'], claim_none['token'], 'calling')
    q.worker_update(claim_none['id'], claim_none['token'], 'complete', dict(answer='Unassigned opponent review'))

    # 2. Alex sees ONLY r_alex (never Blair's review and never the 'none' review, even under limit=1 pagination).
    alex_payload = c.get(f"/api/learning?player_id={alex['id']}").json
    assert [r['id'] for e in alex_payload['entries'] for r in e['reviews']] == [r_alex['id']]
    assert alex_payload['entries'][0]['reviews'][0]['player_mismatch'] is False
    assert alex_payload['plan_state']['eligibility']['valid_reviews_count'] == 1

    paged_alex = []
    cursor = ''
    while True:
        page = c.get(f"/api/learning?player_id={alex['id']}&limit=1" + (f'&after={cursor}' if cursor else '')).json
        paged_alex.extend(r['id'] for e in page['entries'] for r in e['reviews'])
        cursor = page['next_after']
        if not cursor:
            break
    assert paged_alex == [r_alex['id']]

    # 3. Blair sees ONLY r_blair, with player_mismatch=False, selected_override=True, and valid Learning Plan eligibility!
    blair_payload = c.get(f"/api/learning?player_id={blair['id']}").json
    assert len(blair_payload['entries']) == 1
    blair_rev = blair_payload['entries'][0]['reviews'][0]
    assert blair_rev['id'] == r_blair['id']
    assert blair_rev['player_mismatch'] is False
    assert blair_rev['selected_override'] is True
    assert blair_rev['subject_mode'] == 'selected_player'
    assert blair_rev['subject_hint'] == 'far court white visor'
    assert blair_payload['plan_state']['eligibility']['valid_reviews_count'] == 1
    assert blair_payload['plan_state']['eligibility']['can_generate'] is True

    eid_blair = 'coach:' + anns_blair[0]['id']
    plan_req = _post_json(c, '/api/learning/plan', dict(
        player_id=blair['id'], consent=True, expected_parent_revision=None,
        input_digest=blair_payload['plan_state']['eligibility']['input_digest'], request_id='blair-subj-plan-1',
    ))
    assert plan_req.status_code == 201
    claim_plan = q.claim('ai', 'worker-1', [CAPABILITY, LEARNING_PLAN_CAPABILITY])
    q.worker_update(claim_plan['id'], claim_plan['token'], 'calling')
    q.worker_update(claim_plan['id'], claim_plan['token'], 'complete', dict(
        answer=_plan_answer('Blair early preparation plan.', [eid_blair], [
            dict(item_key='', topic='Preparation', title='Early unit turn', observed_issue='Late turn', cue='Turn shoulders early', drill='Shadow swings', uncertainty='', evidence_ids=[eid_blair]),
        ]),
        analysis_mode='learning_plan',
    ))

    # 4. Reassign CLIP's persistent main_player from Alex to Dana:
    #    - Alex's 'main_player' review becomes player_mismatch=True and invalid_reason='reassigned_player'.
    #    - Blair's explicit 'selected_player' review stays valid (player_mismatch=False, citation currently_valid=True)!
    assert focus(c, data, dana['id'], version=1).status_code == 200
    alex_after = c.get(f"/api/learning?player_id={alex['id']}").json
    assert alex_after['entries'][0]['reviews'][0]['player_mismatch'] is True
    assert alex_after['plan_state']['eligibility']['valid_reviews_count'] == 0

    blair_after = c.get(f"/api/learning?player_id={blair['id']}").json
    assert blair_after['entries'][0]['reviews'][0]['player_mismatch'] is False
    assert blair_after['entries'][0]['reviews'][0]['selected_override'] is True
    cit = blair_after['plan_state']['active_plan']['citations'][eid_blair]
    assert cit['currently_valid'] is True
    assert f"coach_job={r_blair['id']}" in cit['study_url']

    # Editing Blair's suggestion preserves immutable asked_at=2100.0 and triggers plan delta eligibility.
    now[0] = 2500.0
    assert put(c, f"/api/coaching/annotations/{anns_blair[0]['id']}", dict(
        publication=data['published_at'], version=1, status='accepted',
        content=dict(title='Blair unit turn', topic='Preparation', observation='Late shoulder turn', cue='Turn shoulders as ball crosses net', drill='Shadow unit turns', uncertainty='',
                     evidence_frame_ids=['f_subj-learn-blair_1'], applies_to=[dict(start_seconds=6.2, end_seconds=7.8)]),
    )).status_code == 200
    blair_edited = c.get(f"/api/learning?player_id={blair['id']}").json
    assert blair_edited['entries'][0]['reviews'][0]['asked_at'] == 2100.0
    assert blair_edited['plan_state']['eligibility']['new_or_revised_reviews_count'] == 1
    assert blair_edited['plan_state']['eligibility']['can_generate'] is True
