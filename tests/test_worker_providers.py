"""Multi-provider worker tests (Codex, Antigravity CLI, Gemini API, and ordered fallback)."""
import base64
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import urllib.error
import pytest

from test_app import setup, ID
from test_coaching import post, enabled, selection
from test_coaching_annotations import prepared, claimed, suggestion
from worker.tennis_coach.annotations import (
    CAPABILITY,
    LEARNING_PLAN_CAPABILITY,
    evidence_manifest,
    gemini_compatible_schema,
    learning_plan_schema,
    response_schema,
)
from worker.tennis_coach.worker import Worker, analyze, configured_providers, redact_secrets

TOKEN = 'test-worker-secret'


def test_gemini_compatible_schema_strips_additional_properties_and_converts_numeric_enums():
    coach = gemini_compatible_schema(response_schema())
    assert 'additionalProperties' not in json.dumps(coach)
    sv = coach['properties']['schema_version']
    assert 'enum' not in sv
    assert sv['type'] == 'integer' and sv['minimum'] == 2 and sv['maximum'] == 2
    assert 'Must equal 2' in sv['description']

    plan = gemini_compatible_schema(learning_plan_schema())
    assert 'additionalProperties' not in json.dumps(plan)
    psv = plan['properties']['schema_version']
    assert 'enum' not in psv
    assert psv['minimum'] == 1 and psv['maximum'] == 1

    multi = gemini_compatible_schema({'type': 'integer', 'enum': [1, 3], 'description': 'Version'})
    assert 'enum' not in multi and multi['minimum'] == 1 and multi['maximum'] == 3
    assert 'Version; Allowed values: 1, 3' == multi['description']


def test_configured_providers_aliases_defaults_and_validation():
    assert configured_providers({}) == ['codex']
    assert configured_providers({'provider': 'agy'}) == ['antigravity']
    assert configured_providers({'providers': ['ChatGPT', 'agy', 'gemini-api', 'codex']}) == [
        'codex',
        'antigravity',
        'gemini',
    ]
    for invalid in ({'providers': []}, {'providers': ['unknown']}, {'provider': ''}, {'provider': 'claude'}):
        with pytest.raises(ValueError, match='codex, antigravity, or gemini'):
            configured_providers(invalid)


def test_antigravity_clip_coaching_pins_evidence_and_uses_compatible_schema(setup, tmp_path, monkeypatch):
    app, c, data, ai, folder = prepared(setup)
    job = claimed(c)
    manifest = evidence_manifest(job)
    local = tmp_path / 'local'
    local.mkdir()
    events = []
    structured_payload = dict(
        schema_version=2,
        summary='Antigravity reviewed both stills.',
        suggestions=[suggestion(manifest)],
    )

    def fake_run(args, **kwargs):
        if args == ['fake-agy', 'models']:
            events.append('preflight')
            for forbidden in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY'):
                assert forbidden not in kwargs['env']
            return SimpleNamespace(returncode=0, stdout='gemini-4-carbon-medium\n', stderr='')
        events.append('agy_call')
        assert kwargs['cwd'] == str(local)
        assert '--sandbox' in args and '--disable-slash-commands' in args
        assert '--output-format' in args and args[args.index('--output-format') + 1] == 'json'
        schema_file = Path(args[args.index('--json-schema') + 1])
        saved_schema = json.loads(schema_file.read_text())
        assert 'additionalProperties' not in json.dumps(saved_schema)
        assert saved_schema['properties']['schema_version']['minimum'] == 2
        prompt_arg = args[args.index('-p') + 1]
        assert '@frame-000.jpg @frame-001.jpg' in prompt_arg
        assert job['body']['submitted_prompt'] in prompt_arg
        envelope = dict(
            conversation_id='conv-123',
            status='SUCCESS',
            response=json.dumps(structured_payload),
            structured_output=structured_payload,
            usage={'input_tokens': 500, 'output_tokens': 120, 'total_tokens': 620},
        )
        return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr='')

    monkeypatch.setattr('worker.tennis_coach.worker.subprocess.run', fake_run)
    monkeypatch.setenv('GEMINI_API_KEY', 'ambient-gemini-key-must-be-stripped')
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-openai-key-must-be-stripped')

    def before(pinned):
        events.append('before_call')
        assert pinned == manifest
        assert post(c, f"worker/jobs/{job['id']}/evidence", {'token': job['token'], 'result': pinned}, TOKEN).status_code == 200
        assert post(c, f"worker/jobs/{job['id']}/calling", {'token': job['token']}, TOKEN).status_code == 200

    cfg = dict(
        artifact_root=app.config['COACH_MEDIA_ROOT'],
        provider='antigravity',
        antigravity='fake-agy',
    )
    result = analyze(cfg, job, local, before)
    assert events == ['preflight', 'before_call', 'agy_call']
    assert result['model'] == 'Antigravity account default'
    assert result['analysis_mode'] == 'timestamped_frames'
    assert result['usage']['total_tokens'] == 620

    done = post(c, f"worker/jobs/{job['id']}/complete", {'token': job['token'], 'result': result}, TOKEN)
    assert done.status_code == 200
    annots = c.get('/api/coaching/sessions/' + ID).json['annotations']
    assert len(annots) == 1 and annots[0]['proposal']['title'] == 'Balanced reset'


def test_gemini_api_clip_coaching_and_secret_redaction(setup, tmp_path, monkeypatch):
    app, c, data, ai, folder = prepared(setup)
    job = claimed(c)
    manifest = evidence_manifest(job)
    local = tmp_path / 'local'
    local.mkdir()
    structured_payload = dict(
        schema_version=2,
        summary='Gemini API reviewed attached frames.',
        suggestions=[suggestion(manifest)],
    )
    seen_requests = []

    class FakeHTTPResponse:
        def __init__(self, payload):
            self._raw = io.BytesIO(json.dumps(payload).encode())

        def read(self, *args):
            return self._raw.read(*args)

        def __enter__(self):
            return self._raw

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        seen_requests.append((req, timeout))
        assert 'secret-gemini-api-key-999' not in req.full_url
        headers = {k.lower(): v for k, v in req.header_items()}
        assert headers['x-goog-api-key'] == 'secret-gemini-api-key-999'
        sent = json.loads(req.data.decode())
        parts = sent['contents'][0]['parts']
        assert len(parts) == 3
        assert parts[0]['text'] == job['body']['submitted_prompt']
        assert base64.b64decode(parts[1]['inline_data']['data']) == b'jpeg-frame-0'
        assert base64.b64decode(parts[2]['inline_data']['data']) == b'jpeg-frame-1'
        assert 'additionalProperties' not in json.dumps(sent['generationConfig']['responseSchema'])
        return FakeHTTPResponse({
            'candidates': [{'content': {'parts': [{'text': json.dumps(structured_payload)}]}}],
            'usageMetadata': {
                'promptTokenCount': 400,
                'candidatesTokenCount': 90,
                'thoughtsTokenCount': 30,
                'totalTokenCount': 520,
            },
        })

    monkeypatch.setattr('worker.tennis_coach.worker.urllib.request.urlopen', fake_urlopen)
    cfg = dict(
        artifact_root=app.config['COACH_MEDIA_ROOT'],
        provider='gemini',
        gemini_api_key='secret-gemini-api-key-999',
        gemini_model='gemini-2.5-pro',
    )
    called = []
    result = analyze(cfg, job, local, lambda m: called.append(m))
    assert len(called) == 1 and len(seen_requests) == 1
    assert result['model'] == 'gemini:gemini-2.5-pro'
    assert result['usage'] == {'input_tokens': 400, 'output_tokens': 90, 'thinking_tokens': 30, 'total_tokens': 520}

    # Verify HTTP error redacts API key in local error logs and never leaks secret in raised ValueError.
    def failing_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url,
            401,
            'Unauthorized',
            {},
            io.BytesIO(b'{"error":"Invalid key secret-gemini-api-key-999"}'),
        )

    monkeypatch.setattr('worker.tennis_coach.worker.urllib.request.urlopen', failing_urlopen)
    local_fail = tmp_path / 'local_fail'
    local_fail.mkdir()
    with pytest.raises(ValueError, match='Gemini API HTTP 401') as exc:
        analyze(cfg, job, local_fail, lambda m: None)
    assert 'secret-gemini-api-key-999' not in str(exc.value)
    err_log = (local_fail / 'gemini-error.txt').read_text()
    assert 'secret-gemini-api-key-999' not in err_log and '[redacted]' in err_log
    assert redact_secrets('token=secret-gemini-api-key-999', cfg) == 'token=[redacted]'


def test_ordered_fallback_from_expired_codex_to_antigravity_crosses_calling_once(setup, tmp_path, monkeypatch):
    """Simulates revoked Codex OAuth token (returncode=1 after login status=0) falling back cleanly to Antigravity."""
    app, c, data, ai, folder = prepared(setup)
    structured_payload = None

    def fake_run(args, **kwargs):
        nonlocal structured_payload
        if args[:3] == ['fake-codex', 'login', 'status']:
            return SimpleNamespace(returncode=0, stdout='Logged in using ChatGPT', stderr='')
        if args[0] == 'fake-codex' and args[1] == 'exec':
            return SimpleNamespace(
                returncode=1,
                stdout='',
                stderr='401 Unauthorized: Encountered invalidated oauth token (token_revoked)',
            )
        if args == ['fake-agy', 'models']:
            return SimpleNamespace(returncode=0, stdout='gemini-4-carbon-medium\n', stderr='')
        if args[0] == 'fake-agy':
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    'status': 'SUCCESS',
                    'response': json.dumps(structured_payload),
                    'structured_output': structured_payload,
                    'usage': {'input_tokens': 300, 'output_tokens': 100, 'total_tokens': 400},
                }),
                stderr='',
            )
        raise AssertionError(f'Unexpected command: {args}')

    monkeypatch.setattr('worker.tennis_coach.worker.subprocess.run', fake_run)

    # Run full Worker.once() against the real Flask coaching routes so a duplicate /calling call would 409!
    worker_cfg = dict(
        server_url='http://localhost',
        worker_token=TOKEN,
        local_state=str(tmp_path / 'worker_state'),
        artifact_root=app.config['COACH_MEDIA_ROOT'],
        providers=['codex', 'antigravity', 'gemini'],
        codex='fake-codex',
        model='gpt-5-codex',  # Legacy Codex model must NOT leak into Antigravity fallback!
        antigravity='fake-agy',
        antigravity_model='gemini-4-carbon-medium',
    )
    w = Worker(worker_cfg, 'ai')
    manifest = evidence_manifest(ai)
    structured_payload = dict(
        schema_version=2,
        summary='Recovered via Antigravity after Codex 401.',
        suggestions=[suggestion(manifest)],
    )

    api_calls = []

    def local_api(path, body):
        api_calls.append(path)
        resp = post(c, path, body, TOKEN)
        assert resp.status_code < 400, (path, resp.status_code, resp.json)
        return resp.json

    w.api = local_api
    assert w.once() is True
    assert [p for p in api_calls if p.endswith('/calling')].__len__() == 1
    assert [p for p in api_calls if p.endswith('/evidence')].__len__() == 1

    saved_job = c.get(f"/api/coaching/jobs/{ai['id']}").json
    assert saved_job['state'] == 'completed'
    assert saved_job['result']['model'] == 'antigravity:gemini-4-carbon-medium'
    assert saved_job['result']['structured']['summary'] == 'Recovered via Antigravity after Codex 401.'

    attempts_files = list((w.root / 'jobs' / ai['id']).glob('*/provider-attempts.json'))
    assert len(attempts_files) == 1
    attempts = json.loads(attempts_files[0].read_text())
    assert [a['provider'] for a in attempts] == ['codex', 'antigravity']
    assert attempts[0]['status'] == 'failed' and attempts[0]['called'] is True
    assert attempts[1]['status'] == 'succeeded' and attempts[1]['called'] is True


def test_preflight_failures_never_cross_call_boundary_and_timeout_never_falls_back(setup, tmp_path, monkeypatch):
    app, c, data, ai, folder = prepared(setup)
    job = claimed(c)

    # 1. All providers fail preflight -> before_call is never invoked.
    def preflight_fail(args, **kwargs):
        if args == ['fake-agy', 'models']:
            return SimpleNamespace(returncode=1, stdout='', stderr='Not logged in')
        raise AssertionError(f'Unexpected subprocess call: {args}')

    monkeypatch.setattr('worker.tennis_coach.worker.subprocess.run', preflight_fail)
    monkeypatch.setattr('worker.tennis_coach.worker.shutil.which', lambda name: None)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)

    events = []
    local1 = tmp_path / 'preflight_fail'
    local1.mkdir()
    cfg_all_unready = dict(
        artifact_root=app.config['COACH_MEDIA_ROOT'],
        providers=['codex', 'antigravity', 'gemini'],
        codex='',
        antigravity='fake-agy',
        gemini_api_key='',
    )
    with pytest.raises(ValueError) as exc:
        analyze(cfg_all_unready, job, local1, lambda m: events.append('before_call'))
    assert events == []
    msg = str(exc.value)
    assert 'codex:' in msg and 'antigravity:' in msg and 'gemini:' in msg

    # 2. Timeout after call_boundary on provider 1 must NOT invoke provider 2!
    called_cmds = []

    def timeout_on_codex(args, **kwargs):
        called_cmds.append(args[0])
        if args[:3] == ['fake-codex', 'login', 'status']:
            return SimpleNamespace(returncode=0, stdout='Logged in using ChatGPT', stderr='')
        if args[0] == 'fake-codex':
            raise subprocess.TimeoutExpired(cmd=args, timeout=300)
        raise AssertionError('Fallback provider must NOT run after an ambiguous timeout!')

    monkeypatch.setattr('worker.tennis_coach.worker.subprocess.run', timeout_on_codex)
    local2 = tmp_path / 'timeout_no_fallback'
    local2.mkdir()
    cfg_timeout = dict(
        artifact_root=app.config['COACH_MEDIA_ROOT'],
        providers=['codex', 'antigravity'],
        codex='fake-codex',
        antigravity='fake-agy',
    )
    with pytest.raises(ValueError, match='timed out'):
        analyze(cfg_timeout, job, local2, lambda m: events.append('before_call'))
    assert events == ['before_call']
    assert 'fake-agy' not in called_cmds
    attempts = json.loads((local2 / 'provider-attempts.json').read_text())
    assert len(attempts) == 1 and attempts[0]['status'] == 'ambiguous_timeout'


def test_learning_plan_supports_antigravity_and_gemini_without_images(tmp_path, monkeypatch):
    plan_job = {
        'id': 'plan-job-001',
        'token': 'lease-tok',
        'body': {
            'purpose': 'learning_plan',
            'required_capability': LEARNING_PLAN_CAPABILITY,
            'player_id': 'p1',
            'submitted_prompt': 'Synthesize learning plan JSON.',
            'response_contract': learning_plan_schema(),
        },
    }
    plan_payload = {
        'schema_version': 1,
        'observed_summary': 'Steady baseline rally posture.',
        'summary_evidence_ids': [],
        'focus_areas': [],
        'uncertainties': ['Limited camera angles'],
        'unassessed_areas': ['Serve'],
        'progress_notes': '',
        'next_evidence_requests': ['Record side-view serve clip'],
    }

    def fake_agy(args, **kwargs):
        if args == ['fake-agy', 'models']:
            return SimpleNamespace(returncode=0, stdout='ok', stderr='')
        prompt_arg = args[args.index('-p') + 1]
        assert '@frame-' not in prompt_arg
        assert 'Synthesize learning plan JSON.' in prompt_arg
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                'status': 'SUCCESS',
                'response': json.dumps(plan_payload),
                'structured_output': plan_payload,
                'usage': {'input_tokens': 110, 'output_tokens': 55, 'total_tokens': 165},
            }),
            stderr='',
        )

    monkeypatch.setattr('worker.tennis_coach.worker.subprocess.run', fake_agy)
    local_agy = tmp_path / 'plan_agy'
    local_agy.mkdir()
    boundaries = []
    res = analyze(
        {'artifact_root': str(tmp_path), 'provider': 'antigravity', 'antigravity': 'fake-agy'},
        plan_job,
        local_agy,
        lambda m: boundaries.append(m),
    )
    assert boundaries == [None]
    assert res['analysis_mode'] == 'learning_plan'
    assert json.loads(res['answer'])['schema_version'] == 1


def test_worker_overview_and_optimistic_ai_config(setup):
    app, root, data = setup
    c = app.test_client()
    # 1. Initially disabled when COACH_WORKER_TOKEN is unset.
    res = c.get('/api/coaching/workers')
    assert res.status_code == 200
    assert res.json['enabled'] is False
    assert res.json['workers'] == []
    assert res.json['ai_config']['selection'] == 'worker_default'
    assert res.json['ai_config']['version'] == 0
    assert any(o['selection'] == 'antigravity:gemini-4-carbon-high' for o in res.json['ai_config']['options'])

    # 2. Enable token and register ai + render workers with sanitized metadata.
    app.config['COACH_WORKER_TOKEN'] = TOKEN
    wh = {'X-Tennis-Request': '1', 'Authorization': f'Bearer {TOKEN}'}
    claim_ai = c.post(
        '/api/coaching/worker/ai/claim',
        json={
            'worker_id': 'mac-ai',
            'capabilities': [CAPABILITY, LEARNING_PLAN_CAPABILITY],
            'meta': {
                'worker_version': '0.5.0',
                'providers': ['antigravity', 'codex', 'gemini'],
                'active_selection': 'worker_default',
                'active_model_label': 'Worker default (Antigravity)',
                'config_version': 0,
                'secret_token': 'MUST-NEVER-LEAK',
                'local_path': '/Users/private/secret',
                'available_models': [
                    {
                        'selection': 'antigravity:gemini-4-hydrogen-high',
                        'provider': 'antigravity',
                        'model': 'gemini-4-hydrogen-high',
                        'label': 'Antigravity · Gemini 4 Hydrogen (High)',
                    },
                    {
                        'selection': 'bad:model/with/slash',
                        'provider': 'antigravity',
                        'model': 'bad/slash',
                        'label': 'Invalid / Path',
                    },
                ],
            },
        },
        headers=wh,
    )
    assert claim_ai.status_code == 200
    assert claim_ai.json == {'job': None, 'config_version': 0}

    claim_render = c.post(
        '/api/coaching/worker/render/claim',
        json={
            'worker_id': 'mac-render',
            'capabilities': ['share-export-v2', 'share-export-v1'],
            'meta': {'worker_version': '0.5.0', 'config_version': True},
        },
        headers=wh,
    )
    assert claim_render.status_code == 200

    overview = c.get('/api/coaching/workers').json
    assert overview['enabled'] is True
    assert [w['role'] for w in overview['workers']] == ['ai', 'render']
    ai_w = overview['workers'][0]
    render_w = overview['workers'][1]
    assert ai_w['online'] is True and ai_w['meta']['providers'] == ['antigravity', 'codex', 'gemini']
    assert 'secret_token' not in ai_w['meta'] and 'local_path' not in ai_w['meta']
    # available_models stays out of workers[i].meta and merges into ai_config.options
    assert 'available_models' not in ai_w['meta']
    assert 'config_version' not in render_w['meta']  # boolean rejected
    assert any(o['selection'] == 'antigravity:gemini-4-hydrogen-high' for o in overview['ai_config']['options'])
    assert not any('bad/slash' in str(o) for o in overview['ai_config']['options'])

    # 3. Optimistic PUT /api/coaching/config and rejection rules.
    assert c.put('/api/coaching/config', json={'selection': 'codex', 'version': 0}).status_code == 415
    assert c.put(
        '/api/coaching/config',
        json={'selection': 'codex', 'version': 0},
        headers={'X-Tennis-Request': '1', 'Origin': 'https://evil.example'},
    ).status_code == 403
    for bad in (
        {'selection': 'invalid-provider', 'version': 0},
        {'selection': 'antigravity:bad/model', 'version': 0},
        {'selection': 'codex', 'version': True},
        {'selection': 'codex', 'version': 0, 'extra': 1},
    ):
        assert c.put('/api/coaching/config', json=bad, headers={'X-Tennis-Request': '1'}).status_code == 400

    upd1 = c.put(
        '/api/coaching/config',
        json={'selection': 'antigravity:gemini-4-carbon-high', 'version': 0},
        headers={'X-Tennis-Request': '1'},
    )
    assert upd1.status_code == 200
    assert upd1.json['version'] == 1
    assert upd1.json['provider'] == 'antigravity'
    assert upd1.json['model'] == 'gemini-4-carbon-high'

    stale = c.put(
        '/api/coaching/config',
        json={'selection': 'codex', 'version': 0},
        headers={'X-Tennis-Request': '1'},
    )
    assert stale.status_code == 409
    assert stale.json['current']['version'] == 1
    assert stale.json['current']['selection'] == 'antigravity:gemini-4-carbon-high'

    upd2 = c.put(
        '/api/coaching/config',
        json={'selection': 'codex', 'version': 1},
        headers={'X-Tennis-Request': '1'},
    )
    assert upd2.status_code == 200 and upd2.json['version'] == 2

    # Worker config endpoints require valid bearer auth.
    assert c.get('/api/coaching/worker/config').status_code == 403
    assert c.post('/api/coaching/worker/config', json={}, headers={'X-Tennis-Request': '1'}).status_code == 403
    wcfg = c.get('/api/coaching/worker/config', headers={'Authorization': f'Bearer {TOKEN}'}).json
    assert wcfg['ai_config']['selection'] == 'codex' and wcfg['ai_config']['version'] == 2


def test_effective_job_config_prioritizes_selected_provider_and_preserves_fallbacks():
    from worker.tennis_coach.worker import effective_job_config

    base = {
        'providers': ['antigravity', 'codex', 'gemini'],
        'antigravity_model': 'gemini-3.8-flash-medium',
        'codex_model': 'gpt-5-codex',
        'gemini_model': 'gemini-2.5-flash',
    }
    unchanged = effective_job_config(base, {'selection': 'worker_default', 'provider': None, 'model': None})
    assert unchanged == base and unchanged is not base

    select_codex = effective_job_config(base, {'selection': 'codex', 'provider': 'codex', 'model': None})
    assert select_codex['providers'] == ['codex', 'antigravity', 'gemini']
    assert select_codex['codex_model'] == ''
    assert base['codex_model'] == 'gpt-5-codex'

    select_agy = effective_job_config(
        base,
        {'selection': 'antigravity:gemini-4-carbon-high', 'provider': 'antigravity', 'model': 'gemini-4-carbon-high'},
    )
    assert select_agy['providers'] == ['antigravity', 'codex', 'gemini']
    assert select_agy['antigravity_model'] == 'gemini-4-carbon-high'

    select_gemini = effective_job_config(
        base,
        {'selection': 'gemini:gemini-2.5-pro', 'provider': 'gemini', 'model': 'gemini-2.5-pro'},
    )
    assert select_gemini['providers'] == ['gemini', 'antigravity', 'codex']
    assert select_gemini['gemini_model'] == 'gemini-2.5-pro'


def test_worker_live_config_sync_via_claim_heartbeat_and_404_compat(setup, tmp_path, monkeypatch):
    import threading
    from werkzeug.serving import make_server

    app, c, data, ai_job, folder = prepared(setup)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f'http://127.0.0.1:{server.server_port}'
    try:
        local_state = tmp_path / 'worker-state'
        worker_cfg = {
            'server_url': base_url,
            'worker_token': TOKEN,
            'artifact_root': app.config['COACH_MEDIA_ROOT'],
            'local_state': str(local_state),
            'worker_id': 'test-mac',
            'providers': ['antigravity', 'codex', 'gemini'],
            'antigravity': 'fake-agy',
            'codex': 'fake-codex',
            'gemini_api_key': 'secret-gemini-key',
        }
        seen_providers = []

        monkeypatch.setattr(
            'worker.tennis_coach.worker.subprocess.run',
            lambda args, **kw: SimpleNamespace(
                returncode=0,
                stdout='gemini-4-carbon-high         Gemini 4 Carbon (High)\n',
                stderr='',
            ),
        )

        # Cancel initial queued AI job to test idle sync first, then queue real jobs.
        c.put(f"/api/coaching/jobs/{ai_job['id']}", json={'action': 'cancel', 'version': ai_job['version']}, headers={'X-Tennis-Request': '1'})

        # Update config on server to codex before first poll -> idle worker syncs immediately.
        assert c.put('/api/coaching/config', json={'selection': 'codex', 'version': 0}, headers={'X-Tennis-Request': '1'}).status_code == 200
        w = Worker(worker_cfg, 'ai')
        assert w.once() is False
        assert w.remote_ai_config['version'] == 1 and w.remote_ai_config['selection'] == 'codex'
        overview = c.get('/api/coaching/workers').json
        assert overview['workers'][0]['meta']['config_version'] == 1
        assert overview['workers'][0]['meta']['active_selection'] == 'codex'
        assert any(o['selection'] == 'antigravity:gemini-4-carbon-high' for o in overview['ai_config']['options'])

        # Enqueue a job; during analyze(), update config to gemini and test mid-job heartbeat sync.
        render_id = ai_job['body']['export_id']
        j1 = post(c, 'requests', {'export_id': render_id, 'prompt': 'Check stance', 'consent': True, 'request_id': 'live-sync-job-001'})
        assert j1.status_code == 201

        def syncing_analyze(job_cfg, job, local, before_call):
            seen_providers.append((list(job_cfg['providers']), job_cfg.get('gemini_model')))
            manifest = evidence_manifest(job)
            cache = Path(job_cfg['artifact_root']) / 'evidence/frames'
            cache.mkdir(parents=True, exist_ok=True)
            for frame in manifest['frames']:
                (cache / f"{frame['sha256']}.jpg").write_bytes((folder / f"frame-{frame['order'] - 1:03d}.jpg").read_bytes())
            before_call(manifest)
            if len(seen_providers) == 1:
                resp = c.put(
                    '/api/coaching/config',
                    json={'selection': 'gemini:gemini-2.5-pro', 'version': 1},
                    headers={'X-Tennis-Request': '1'},
                )
                assert resp.status_code == 200
                hb = w.api('worker/jobs/' + job['id'] + '/heartbeat', {'token': job['token'], 'meta': w.worker_meta()})
                assert hb['config_version'] == 2
                assert w.maybe_sync_config(hb, acknowledge=True) is True
                # Running job keeps its pre-job snapshot (`codex` primary), while worker stored v2 for next job.
                assert job_cfg['providers'][0] == 'codex'
                assert w.remote_ai_config['version'] == 2
            payload = dict(schema_version=2, summary='Job complete', suggestions=[suggestion(manifest)])
            return dict(answer=json.dumps(payload), analysis_mode='timestamped_frames', model=job_cfg['providers'][0], elapsed_seconds=0.1, usage=None)

        monkeypatch.setattr('worker.tennis_coach.worker.analyze', syncing_analyze)
        assert w.once() is True
        assert c.get(f"/api/coaching/jobs/{j1.json['id']}").json['state'] == 'completed'
        assert seen_providers == [(['codex', 'antigravity', 'gemini'], None)]

        # Next job automatically uses the live-reloaded Gemini 2.5 Pro selection without restart!
        j2 = post(c, 'requests', {'export_id': render_id, 'prompt': 'Check follow through', 'consent': True, 'request_id': 'live-sync-job-002'})
        assert j2.status_code == 201
        assert w.once() is True
        assert c.get(f"/api/coaching/jobs/{j2.json['id']}").json['state'] == 'completed'
        assert seen_providers[-1] == (['gemini', 'antigravity', 'codex'], 'gemini-2.5-pro')
        assert json.loads((local_state / 'ai' / 'remote-config.json').read_text())['selection'] == 'gemini:gemini-2.5-pro'

        # Legacy server compatibility: HTTP 404 on /worker/config degrades cleanly without failing.
        from worker.tennis_coach.worker import _HttpStatusError
        real_api = w.api
        legacy_calls = []

        def legacy_api(path, body):
            legacy_calls.append(path)
            if path == 'worker/config':
                raise _HttpStatusError(404, 'Not Found')
            res = real_api(path, body)
            if path.endswith('/claim') and isinstance(res, dict):
                res = {k: v for k, v in res.items() if k != 'config_version'}
            return res

        monkeypatch.setattr(w, 'api', legacy_api)
        w._config_synced_once = False
        w._remote_config_unsupported = False
        assert w.load_remote_config() is False
        assert w._remote_config_unsupported is True
        legacy_calls.clear()
        assert w.once() is False
        assert 'worker/config' not in legacy_calls
    finally:
        server.shutdown()
        thread.join()
