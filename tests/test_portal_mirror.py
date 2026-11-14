import hashlib
import json
import shutil
import threading
from flask import Flask, jsonify, request
from werkzeug.serving import make_server
from test_app import setup, ID, CLIP
from coaching import Coaching, recipe
from app import create_app


def _h(token=None):
    headers = {'X-Tennis-Request': '1'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    return headers


def test_portal_snapshot_covers_all_kinds_and_strips_private_fields(setup):
    app, _root, data = setup
    now = [1000.0]
    q = app.extensions['coaching']
    q.clock = lambda: now[0]
    clip = data['clips'][0]

    share_spec = recipe(
        {'source_id': ID, 'clip_id': CLIP, 'publication': data['published_at'], 'start': 1, 'end': 3, 'speed': 1, 'crop': [0, 0, 1, 1], 'quality': 'high', 'intent': 'share'},
        data,
        clip,
    )
    share_job = q.create('render', share_spec, 'share-export-001')

    render_spec = recipe(
        {'source_id': ID, 'clip_id': CLIP, 'publication': data['published_at'], 'start': 1, 'end': 3, 'speed': 0.5, 'crop': [0, 0, 1, 1], 'quality': 'balanced'},
        data,
        clip,
    )
    render_job = q.create('render', render_spec, 'coaching-render-001')
    r_claim = q.claim('render', 'render-mac-1', ['share-export-v2'])
    if r_claim['id'] == share_job['id']:
        q.worker_update(share_job['id'], r_claim['token'], 'complete', {'frames': [], 'bytes': 1234, 'output_seconds': 2.0, 'sha256': 'a' * 64, 'render_seconds': 1.2, 'artifact_directory': 'fence0'})
        r_claim = q.claim('render', 'render-mac-1', ['share-export-v2'])
    q.worker_update(r_claim['id'], r_claim['token'], 'complete', {'frames': [{'file': 'frame-001.jpg'}], 'bytes': 999, 'output_seconds': 4.0, 'sha256': 'b' * 64, 'render_seconds': 0.9, 'artifact_directory': 'fence1'})

    ai_job = q.create(
        'ai',
        dict(
            render_spec,
            export_id=render_job['id'],
            artifact_directory='fence1',
            prompt='Private question about forehand',
            submitted_prompt='Private assembled prompt',
            player_context={'name': 'Private Player Name'},
        ),
        'ai-coach-001',
    )
    a_claim = q.claim('ai', 'ai-mac-1', [], {'providers': ['antigravity', 'codex'], 'active_selection': 'codex'})
    secret_path = '/' + 'Users/fixture/secret.mov'
    q.worker_update(
        ai_job['id'],
        a_claim['token'],
        'fail',
        {
            'error': f'Failed reading {secret_path} with Bearer secret-token-123456',
            'error_code': 'provider_timeout',
            'error_detail': 'Upstream timeout on /var/log/worker.log',
        },
    )

    q.create(
        'ai',
        {'purpose': 'learning_plan', 'player_id': 'player-fixture', 'player_profile': {'name': 'Private Player Name'}},
        'learning-plan-001',
    )

    snap = q.portal_snapshot(session_lookup=lambda sid: 'Evening Practice' if sid == ID else None)
    assert snap['domain'] == 'coaching' and snap['snapshot'] is True and snap['complete'] is True
    assert {w['id'] for w in snap['workers']} == {'ai-mac-1', 'render-mac-1'}

    by_kind = {t['kind']: t for t in snap['tasks']}
    assert set(by_kind) == {'clip_interval_export', 'coaching_excerpt_render', 'ai_coach', 'learning_plan'}

    failed_ai = by_kind['ai_coach']
    assert failed_ai['status'] == 'failed'
    assert failed_ai['error_code'] == 'provider_timeout'
    assert '[path]' in failed_ai['error'] and '[redacted]' in failed_ai['error']
    assert secret_path not in failed_ai['error']
    assert 'secret-token-123456' not in failed_ai['error']
    assert '[path]' in failed_ai['error_detail']
    assert failed_ai['session_name'] == 'Evening Practice'

    dumped = json.dumps(snap)
    for sensitive in ('Private question', 'Private assembled prompt', 'Private Player Name', secret_path, 'secret-token-123456', 'submitted_prompt'):
        assert sensitive not in dumped


def test_portal_mirror_live_sync_and_deletion_via_http(setup):
    app, root, data = setup
    received = []
    receiver = Flask('portal-receiver')

    @receiver.post('/api/v1/external-sync')
    def sync():
        assert request.headers.get('X-Tennis-Request') == '1'
        payload = request.get_json()
        received.append(payload)
        return jsonify({'domain': payload['domain'], 'workers': len(payload.get('workers') or []), 'tasks': len(payload.get('tasks') or []), 'deleted': len(payload.get('deleted') or [])})

    server = make_server('127.0.0.1', 0, receiver, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        live_app = create_app({
            'TESTING': True,
            'PORTAL_SYNC_INLINE': True,
            'LIBRARY_ROOT': str(root),
            'DATA_ROOT': app.config['DATA_ROOT'],
            'COACH_WORKER_TOKEN': 'worker-secret',
            'COACH_MEDIA_ROOT': app.config['COACH_MEDIA_ROOT'],
            'PROCESSING_API_URL': f'http://127.0.0.1:{server.server_port}',
        })
        c = live_app.test_client()

        # 1. Clip interval export mirrors immediately.
        share_res = c.post(
            '/api/coaching/exports',
            json={'source_id': ID, 'clip_id': CLIP, 'publication': data['published_at'], 'start': 1, 'end': 3, 'speed': 1, 'crop': [0, 0, 1, 1], 'quality': 'high', 'intent': 'share', 'request_id': 'sync-share-001'},
            headers=_h(),
        )
        assert share_res.status_code == 201
        assert any(t['kind'] == 'clip_interval_export' for t in received[-1]['tasks'])

        # 2. Coaching excerpt + failed AI ask mirror redacted failure diagnostics.
        render_res = c.post(
            '/api/coaching/exports',
            json={'source_id': ID, 'clip_id': CLIP, 'publication': data['published_at'], 'start': 1, 'end': 3, 'speed': 0.5, 'crop': [0, 0, 1, 1], 'quality': 'balanced', 'request_id': 'sync-render-001'},
            headers=_h(),
        )
        assert render_res.status_code == 201
        render_id = render_res.json['id']

        claim_r = c.post('/api/coaching/worker/render/claim', json={'worker_id': 'render-live', 'capabilities': ['share-export-v2']}, headers=_h('worker-secret'))
        assert claim_r.status_code == 200
        # Complete both render jobs cleanly until render_id is completed.
        for _ in range(2):
            job = claim_r.json['job']
            if not job:
                break
            fence = hashlib.sha256(job['token'].encode()).hexdigest()[:24]
            folder = app.config['COACH_MEDIA_ROOT']
            job_dir = f'{folder}/{job["id"]}/{fence}'
            import os
            os.makedirs(job_dir, exist_ok=True)
            with open(f'{job_dir}/excerpt.mp4', 'wb') as fh:
                fh.write(b'mp4')
            if job['body'].get('intent') == 'share':
                frames = []
            else:
                frames = []
                for idx, original in enumerate((6.5, 7.5)):
                    name = f'frame-{idx:03}.jpg'
                    raw = f'jpeg-{idx}'.encode()
                    with open(f'{job_dir}/{name}', 'wb') as fh:
                        fh.write(raw)
                    frames.append({'file': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'original_seconds': original, 'clip_seconds': original - 5, 'excerpt_seconds': (original - 6) / 0.5})
            with open(f'{job_dir}/recipe.json', 'w') as fh:
                json.dump({'recipe': job['body'], 'lease_token': job['token'], 'frames': frames, 'bytes': 3, 'output_seconds': 4.0 if frames else 2.0, 'sha256': 'd' * 64, 'render_seconds': 0.5}, fh)
            done = c.post(f'/api/coaching/worker/jobs/{job["id"]}/complete', json={'token': job['token']}, headers=_h('worker-secret'))
            assert done.status_code == 200, done.data
            claim_r = c.post('/api/coaching/worker/render/claim', json={'worker_id': 'render-live', 'capabilities': ['share-export-v2']}, headers=_h('worker-secret'))

        ask_res = c.post(
            '/api/coaching/requests',
            json={'export_id': render_id, 'prompt': 'Check split step', 'consent': True, 'request_id': 'sync-ask-001'},
            headers=_h(),
        )
        assert ask_res.status_code == 201
        ai_id = ask_res.json['id']
        from worker.tennis_coach.annotations import CAPABILITY
        ai_claim = c.post('/api/coaching/worker/ai/claim', json={'worker_id': 'ai-live', 'capabilities': [CAPABILITY], 'meta': {'providers': ['chatgpt']}}, headers=_h('worker-secret')).json['job']
        assert ai_claim is not None and ai_claim['id'] == ai_id
        fail_res = c.post(
            f'/api/coaching/worker/jobs/{ai_id}/fail',
            json={'token': ai_claim['token'], 'result': {'error': 'Upstream model rate limit', 'error_code': 'rate_limit', 'error_detail': '429 from /var/log/relay.log'}},
            headers=_h('worker-secret'),
        )
        assert fail_res.status_code == 200
        failed_item = next(t for t in received[-1]['tasks'] if t['identity'] == ai_id)
        assert failed_item['status'] == 'failed'
        assert failed_item['error'] == 'Upstream model rate limit'
        assert failed_item['error_code'] == 'rate_limit'
        assert '[path]' in failed_item['error_detail']
        assert any(w['id'] == 'ai-live' for w in received[-1]['workers'])

        # 3. Deleting the AI review pushes deleted identities cleanly.
        del_res = c.delete(f'/api/coaching/jobs/{ai_id}', json={}, headers=_h())
        assert del_res.status_code == 200
        assert ai_id in received[-1]['deleted']
        assert all(t['identity'] != ai_id for t in received[-1]['tasks'])
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_portal_mirror_ignores_unreachable_processing_server(setup):
    app, root, data = setup
    resilient = create_app({
        'TESTING': True,
        'PORTAL_SYNC_INLINE': True,
        'LIBRARY_ROOT': str(root),
        'DATA_ROOT': app.config['DATA_ROOT'],
        'COACH_WORKER_TOKEN': 'worker-secret',
        'PROCESSING_API_URL': 'http://127.0.0.1:9',
    })
    c = resilient.test_client()
    r = c.post(
        '/api/coaching/exports',
        json={'source_id': ID, 'clip_id': CLIP, 'publication': data['published_at'], 'start': 1, 'end': 3, 'speed': 1, 'crop': [0, 0, 1, 1], 'quality': 'high', 'intent': 'share', 'request_id': 'offline-sync-001'},
        headers=_h(),
    )
    assert r.status_code == 201


def test_portal_mirror_syncs_available_models_and_applies_desired_ai_config(setup):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    app, root, data = setup
    received = []
    respond_desired = {'active': True}

    class SyncHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length') or '0')
            payload = json.loads(self.rfile.read(length).decode() or '{}')
            received.append(payload)
            if self.path != '/api/v1/external-sync':
                self.send_response(404)
                self.end_headers()
                return
            reply = {'ok': True}
            if respond_desired['active'] and len(received) == 1:
                reply['desired_ai_config'] = {'selection': 'gemini:gemini-2.5-pro', 'version': 4}
            raw = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), SyncHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        synced = create_app({
            'TESTING': True,
            'PORTAL_SYNC_INLINE': True,
            'LIBRARY_ROOT': str(root),
            'DATA_ROOT': app.config['DATA_ROOT'],
            'COACH_WORKER_TOKEN': 'worker-secret',
            'PROCESSING_API_URL': f'http://127.0.0.1:{server.server_port}',
        })
        c = synced.test_client()
        hb = c.post(
            '/api/coaching/worker/ai/claim',
            json={
                'worker_id': 'ai-models-1',
                'capabilities': [],
                'meta': {
                    'config_version': 2,
                    'active_selection': 'worker_default',
                    'available_models': [
                        {'selection': 'gemini:gemini-2.5-pro', 'provider': 'gemini', 'model': 'gemini-2.5-pro', 'label': 'Gemini 2.5 Pro'}
                    ],
                },
            },
            headers=_h('worker-secret'),
        )
        assert hb.status_code == 200
        # Trigger inline mirror sync via /api/coaching/exports or sync_now directly
        synced.extensions['portal_mirror'].sync_now()
        assert len(received) >= 2
        first_ai_worker = next(w for w in received[0]['workers'] if w['id'] == 'ai-models-1')
        assert first_ai_worker['available_models'][0]['selection'] == 'gemini:gemini-2.5-pro'
        assert received[0]['settings']['ai_config']['selection'] == 'worker_default'
        assert received[-1]['settings']['ai_config']['selection'] == 'gemini:gemini-2.5-pro'
        assert c.get('/api/coaching/config').json['selection'] == 'gemini:gemini-2.5-pro'
    finally:
        server.shutdown()
        thread.join(timeout=2)
