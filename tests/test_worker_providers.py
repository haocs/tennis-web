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
