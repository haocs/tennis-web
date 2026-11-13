import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
import pytest
from test_app import setup, ID, CLIP
from coaching import Coaching, QueueConflict, recipe
from app import create_app


def post(c,path,body,token=None):
    h={'X-Tennis-Request':'1'}
    if token:h['Authorization']='Bearer '+token
    return c.post('/api/coaching/'+path,json=body,headers=h)


def enabled(setup):
    app,root,data=setup
    app.config['COACH_WORKER_TOKEN']='test-worker-secret'
    return app,root,data


def selection(data):return dict(source_id=ID,clip_id=CLIP,publication=data['published_at'],start=1,end=3,speed=.5,crop=[.25,.25,.5,.5],quality='balanced',request_id='test-request-001')


def test_queue_isolation_and_atomic_claim(tmp_path):
    q=Coaching(tmp_path/'q.sqlite3')
    q.create('render',{'source_id':ID},'render-001')
    ai=q.create('ai',{'source_id':ID},'ai-job-001')
    with ThreadPoolExecutor(4) as pool:claims=list(pool.map(lambda n:q.claim('ai',str(n)),range(4)))
    assert len([c for c in claims if c])==1
    assert next(c for c in claims if c)['id']==ai['id']
    assert q.claim('render','exporter')['role']=='render'


def test_lease_fencing_and_uncertain_provider_not_retried(tmp_path):
    now=[100.0];q=Coaching(tmp_path/'q.sqlite3',lambda:now[0]);job=q.create('ai',{'source_id':ID},'request-001')
    claim=q.claim('ai','one');q.worker_update(job['id'],claim['token'],'calling')
    now[0]+=91
    assert q.claim('ai','two') is None
    assert q.get(job['id'])['state']=='needs_review'
    with pytest.raises(QueueConflict):q.worker_update(job['id'],claim['token'],'complete',{'answer':'old'})
    render=q.create('render',{'source_id':ID},'request-002');old=q.claim('render','one');now[0]+=91;new=q.claim('render','two')
    assert old['token']!=new['token']
    with pytest.raises(QueueConflict):q.worker_update(render['id'],old['token'],'complete',{})


def test_idempotency_and_cue_revision(tmp_path):
    q=Coaching(tmp_path/'q.sqlite3');a=q.create('ai',{'source_id':ID},'request-001')
    assert q.create('ai',{'source_id':ID},'request-001')['id']==a['id']
    with pytest.raises(QueueConflict):q.create('ai',{'source_id':'changed'},'request-001')
    c=q.claim('ai','one');q.worker_update(a['id'],c['token'],'calling');done=q.worker_update(a['id'],c['token'],'complete',{'answer':'test'})
    saved=q.edit(a['id'],done['version'],'cue','Stay balanced')
    assert saved['result']['answer']=='test' and saved['result']['cue_reviewed']
    with pytest.raises(QueueConflict):q.edit(a['id'],done['version'],'cue','overwrite')


def test_api_origin_auth_recipe_and_retention(setup,tmp_path):
    app,root,data=enabled(setup);c=app.test_client();b=selection(data)
    assert post(c,'exports',b).status_code==201
    assert post(c,'exports',b).json['id']==post(c,'exports',b).json['id']
    bad=dict(b,request_id='bad-request',crop=[0,0,2,1]);assert post(c,'exports',bad).status_code==400
    assert post(c,'worker/ai/claim',{'worker_id':'x'}).status_code==403
    claim=post(c,'worker/render/claim',{'worker_id':'x'},'test-worker-secret').json['job']
    assert claim['role']=='render'
    assert post(c,'worker/ai/claim',{'worker_id':'x'},'test-worker-secret').json['job'] is None
    assert c.get('/api/coaching/jobs/'+claim['id']).json.get('token') is None
    assert post(c,'requests',{'export_id':claim['id'],'prompt':'coach','consent':True,'request_id':'ask-0001'}).status_code==409
    app.config['COACH_WORKER_TOKEN']=''
    assert post(c,'exports',dict(b,request_id='new-request')).status_code==503
    assert c.get('/api/coaching/sessions/'+ID).json['jobs']


def test_bounds_and_original_offsets(setup):
    app,root,data=setup;clip=data['clips'][0];b=selection(data)
    r=recipe(b,data,clip);assert r['original_start']==6 and r['original_end']==8
    for fields in [{'start':True},{'end':float('nan')},{'start':-1},{'end':100},{'speed':0},{'crop':[0,0,0,1]}]:
        with pytest.raises(ValueError):recipe(dict(b,**fields),data,clip)


def test_artifact_requires_fenced_manifest(setup):
    import hashlib
    app,root,data=enabled(setup);c=app.test_client();created=post(c,'exports',selection(data)).json
    claim=post(c,'worker/render/claim',{'worker_id':'media'},'test-worker-secret').json['job']
    fence=hashlib.sha256(claim['token'].encode()).hexdigest()[:24];folder=Path(app.config['COACH_MEDIA_ROOT'])/claim['id']/fence;folder.mkdir(parents=True)
    (folder/'excerpt.mp4').write_bytes(b'video');(folder/'frame-000.jpg').write_bytes(b'frame');(folder/'prompt.txt').write_text('question')
    payload=dict(recipe=claim['body'],lease_token=claim['token'],frames=[{'file':'frame-000.jpg','sha256':hashlib.sha256(b'frame').hexdigest(),'original_seconds':7,'clip_seconds':2,'excerpt_seconds':2}],bytes=5,output_seconds=4,sha256='a'*64,render_seconds=1)
    (folder/'recipe.json').write_text(json.dumps(payload))
    done=post(c,'worker/jobs/'+claim['id']+'/complete',{'token':claim['token']},'test-worker-secret');assert done.status_code==200
    meta=c.get('/api/coaching/exports/'+claim['id']+'/recipe.json');assert claim['token'] not in meta.get_data(as_text=True)
    assert post(c,'requests',{'export_id':claim['id'],'prompt':'coach','consent':False,'request_id':'ask-0001'}).status_code==400
    asked=post(c,'requests',{'export_id':claim['id'],'prompt':'coach','consent':True,'request_id':'ask-0001'});assert asked.status_code==201
    assert asked.json['body']['artifact_directory']==fence
    (folder/'excerpt.mp4').unlink();assert c.get('/api/coaching/exports/'+claim['id']+'/excerpt.mp4').status_code==410
    assert c.get('/api/coaching/jobs/'+asked.json['id']).status_code==200


def test_completion_ack_can_be_retried_without_a_second_call(tmp_path):
    now=[100.0];q=Coaching(tmp_path/'q.sqlite3',lambda:now[0]);created=q.create('ai',{},'request-001');claim=q.claim('ai','one')
    q.worker_update(created['id'],claim['token'],'calling')
    first=q.worker_update(created['id'],claim['token'],'complete',{'answer':'first'})
    now[0]+=1000
    again=q.worker_update(created['id'],claim['token'],'complete',{'answer':'changed'})
    assert again['result']==first['result'] and again['version']==first['version']
    assert q.claim('ai','two') is None


def test_malformed_worker_and_browser_requests_are_rejected(setup):
    app,root,data=enabled(setup);c=app.test_client()
    for path in ('exports','requests','worker/ai/claim'):
        assert post(c,path,[],token='test-worker-secret').status_code==400
    b=selection(data);b['question']='x'*4001
    assert post(c,'exports',b).status_code==400


def test_worker_identity_persists_across_restart_and_roles_are_distinct(tmp_path):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
    from tennis_coach.worker import Worker
    cfg={'local_state':str(tmp_path)}
    a=Worker(cfg,'ai');again=Worker(cfg,'ai');render=Worker(cfg,'render')
    assert a.identity==again.identity and a.identity!=render.identity


def test_export_request_identity_conflict_is_409(setup):
    app,root,data=enabled(setup);c=app.test_client();b=selection(data)
    assert post(c,'exports',b).status_code==201
    assert post(c,'exports',dict(b,start=1.5)).status_code==409


def test_worker_does_not_claim_when_storage_is_unavailable(tmp_path):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
    from tennis_coach.worker import Worker
    worker=Worker({'local_state':str(tmp_path),'artifact_root':str(tmp_path/'missing')},'ai')
    worker.api=lambda *a:pytest.fail('No job should be claimed before storage is available')
    assert worker.once() is False
    assert json.loads((worker.root/'state.json').read_text())['status']=='storage_unavailable'


def test_mac_service_definition_uses_named_launcher(tmp_path,monkeypatch):
    import importlib.util
    module_path=Path(__file__).resolve().parents[1]/'worker/install_service.py'
    spec=importlib.util.spec_from_file_location('coach_install_service',module_path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    cfg=tmp_path/'config.json';cfg.write_text('{}')
    executable=tmp_path/'venv/bin/tennis-coach-worker';launcher=tmp_path/'Tennis Coach.app/Contents/MacOS/TennisCoach'
    monkeypatch.setattr(sys,'argv',['install_service.py','install','--role','ai','--config',str(cfg),'--executable',str(executable),'--launcher',str(launcher)])
    module.main()
    import plistlib
    data=plistlib.loads((tmp_path/'Library/LaunchAgents/local.tennis-coach.ai.plist').read_bytes())
    assert data['ProgramArguments']==[str(launcher),'--worker',str(executable),'--config',str(cfg),'--role','ai']
    assert data['KeepAlive'] and data['RunAtLoad']


def test_worker_server_error_is_actionable_and_redacted(tmp_path,monkeypatch):
    import io,urllib.error
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'worker'))
    from tennis_coach.worker import Worker
    worker=Worker({'local_state':str(tmp_path),'server_url':'http://localhost','worker_token':'private-test-token'},'ai')
    def rejected(*args,**kwargs):raise urllib.error.HTTPError('http://localhost',400,'Bad request',{},io.BytesIO(b'{"error":"Denied private-test-token"}'))
    monkeypatch.setattr('urllib.request.urlopen',rejected)
    with pytest.raises(ValueError,match='HTTP 400: Denied') as error:worker.api('worker/ai/claim',{'worker_id':'test'})
    assert 'private-test-token' not in str(error.value) and '[redacted]' in str(error.value)


def test_share_export_capability_gating_and_profile_isolation(setup):
    import hashlib
    from worker.tennis_coach.annotations import RENDER_SHARE_CAPABILITY, RENDER_SHARE_V1_CAPABILITY
    app,root,data=enabled(setup);c=app.test_client()
    body=dict(selection(data),intent='share',quality='high',request_id='share-request-001')
    created=post(c,'exports',body)
    assert created.status_code==201
    spec=created.json['body']
    assert spec['intent']=='share' and spec['quality']=='high'
    assert spec['required_capability']==RENDER_SHARE_CAPABILITY
    assert spec['audio']=='retime_if_present' and spec['analysis_mode']=='none'
    # Legacy render worker without share-export-v2 capability (including old v1-only renderer) must not claim v2 share jobs.
    assert post(c,'worker/render/claim',{'worker_id':'old-render','capabilities':[]},'test-worker-secret').json['job'] is None
    assert post(c,'worker/render/claim',{'worker_id':'v1-render','capabilities':[RENDER_SHARE_V1_CAPABILITY]},'test-worker-secret').json['job'] is None
    claim=post(c,'worker/render/claim',{'worker_id':'new-render','capabilities':[RENDER_SHARE_CAPABILITY,RENDER_SHARE_V1_CAPABILITY]},'test-worker-secret').json['job']
    assert claim['id']==created.json['id']
    fence=hashlib.sha256(claim['token'].encode()).hexdigest()[:24]
    folder=Path(app.config['COACH_MEDIA_ROOT'])/claim['id']/fence;folder.mkdir(parents=True)
    (folder/'excerpt.mp4').write_bytes(b'high-quality-video')
    payload=dict(recipe=claim['body'],lease_token=claim['token'],frames=[],bytes=18,output_seconds=4,sha256='b'*64,render_seconds=0.8,audio_output='retimed',color_output='sdr-bt709',video_codec='h264',pix_fmt='yuv420p',fps_output=60)
    (folder/'recipe.json').write_text(json.dumps(payload))
    # Unexpected coaching prompt file in share export directory is rejected.
    (folder/'prompt.txt').write_text('should-not-exist')
    assert post(c,'worker/jobs/'+claim['id']+'/complete',{'token':claim['token']},'test-worker-secret').status_code==400
    (folder/'prompt.txt').unlink()
    done=post(c,'worker/jobs/'+claim['id']+'/complete',{'token':claim['token']},'test-worker-secret')
    assert done.status_code==200 and done.json['result']['audio_output']=='retimed' and done.json['result']['color_output']=='sdr-bt709'
    # Share exports cannot be used as coaching evidence.
    rejected_ai=post(c,'requests',{'export_id':claim['id'],'prompt':'coach','consent':True,'request_id':'ask-from-share'})
    assert rejected_ai.status_code==400 and 'Share downloads cannot be used as coaching evidence' in rejected_ai.json['error']
    assert c.get('/api/coaching/exports/'+claim['id']+'/prompt.txt').status_code==404
    dl=c.get('/api/coaching/exports/'+claim['id']+'/excerpt.mp4?download=1')
    assert dl.status_code==200 and f'{CLIP}-interval-excerpt.mp4' in dl.headers.get('Content-Disposition','')


@pytest.mark.parametrize('profile', ['sdr', 'hlg', 'pq'])
def test_share_export_ffmpeg_synthetic_video_audio_and_hdr(setup, tmp_path, profile):
    import hashlib, os, re, shutil, subprocess
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
    from tennis_coach.worker import atempo_chain, render
    from worker.tennis_coach.annotations import RENDER_SHARE_CAPABILITY
    assert atempo_chain(1.0) == []
    assert atempo_chain(0.25) == ['atempo=0.5', 'atempo=0.500000']
    assert atempo_chain(0.1) == ['atempo=0.5', 'atempo=0.5', 'atempo=0.5', 'atempo=0.800000']
    ff = os.environ.get('TENNIS_FFMPEG') or shutil.which('ffmpeg')
    if not ff:
        pytest.skip('FFmpeg required for real media integration checks')
    app, root, data = enabled(setup)
    c = app.test_client()
    clip_rel = data.get('media_directory', data['id']) + '/' + data['clips'][0]['file']
    src_path = Path(app.config['LIBRARY_ROOT']) / 'media' / clip_rel
    hdr = profile != 'sdr'
    # Encoder VUI tags are a precondition, not a mocked tone-map command.
    if profile == 'sdr':
        encoding = ['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-crf', '18',
                    '-x264-params', 'colorprim=bt709:transfer=bt709:colormatrix=bt709',
                    '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709']
    else:
        trc = 'arib-std-b67' if profile == 'hlg' else 'smpte2084'
        transfer_code = '18' if profile == 'hlg' else '16'
        encoding = ['-vf', f'zscale=pin=bt709:tin=bt709:min=bt709:rin=limited:p=bt2020:t={trc}:m=bt2020nc:r=limited,format=yuv420p10le',
                    '-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p10le',
                    '-x265-params', f'pools=1:frame-threads=1:log-level=error:colorprim=9:transfer={transfer_code}:colormatrix=9',
                    '-tag:v', 'hvc1', '-color_primaries', 'bt2020', '-color_trc', trc, '-colorspace', 'bt2020nc']
    subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=60:duration=2',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
                    *encoding, '-c:a', 'aac', '-b:a', '128k', str(src_path)],
                   check=True, capture_output=True, timeout=30)
    if hdr:
        expected_trc = 'arib-std-b67' if profile == 'hlg' else 'smpte2084'
        input_probe = subprocess.run([ff, '-hide_banner', '-i', str(src_path)],
                                     capture_output=True, text=True, timeout=15).stderr
        assert 'yuv420p10le' in input_probe and f'bt2020nc/bt2020/{expected_trc}' in input_probe
    # Deliberately invert the manifest hdr flag to verify probed stream tags are authoritative over stale flags.
    data['video']['hdr'] = not hdr
    data.setdefault('media_checksums', {})[data['clips'][0]['file']] = hashlib.sha256(src_path.read_bytes()).hexdigest()
    (Path(app.config['LIBRARY_ROOT']) / 'metadata/sessions' / f'{ID}.json').write_text(json.dumps(data))
    body = dict(source_id=ID, clip_id=CLIP, publication=data['published_at'], start=0.25,
                end=0.75, speed=0.5, crop=[0.25, 0.25, 0.5, 0.5], intent='share',
                quality='high', request_id=f'share-ffmpeg-{profile}')
    created = post(c, 'exports', body)
    assert created.status_code == 201
    assert created.json['body']['color'] == 'preserve-hdr-or-sdr'
    job = post(c, 'worker/render/claim', {'worker_id': 'render-worker',
               'capabilities': [RENDER_SHARE_CAPABILITY]}, 'test-worker-secret').json['job']
    local = tmp_path / f'local-{profile}'
    local.mkdir()
    cfg = dict(library_media_root=str(Path(app.config['LIBRARY_ROOT']) / 'media'),
               artifact_root=str(app.config['COACH_MEDIA_ROOT']), ffmpeg=ff)
    info = render(cfg, job, local)
    assert info['frames'] == [] and info['audio_output'] == 'retimed' and info['fps_output'] == 60
    fence = hashlib.sha256(job['token'].encode()).hexdigest()[:24]
    dest = Path(app.config['COACH_MEDIA_ROOT']) / job['id'] / fence
    assert sorted(p.name for p in dest.iterdir()) == ['excerpt.mp4', 'recipe.json']
    probe = subprocess.run([ff, '-hide_banner', '-i', str(dest / 'excerpt.mp4')],
                           capture_output=True, text=True, timeout=15).stderr
    if hdr:
        expected_trc = 'arib-std-b67' if profile == 'hlg' else 'smpte2084'
        expected_color = 'hdr10-hlg-bt2020' if profile == 'hlg' else 'hdr10-pq-bt2020'
        assert info['color_output'] == expected_color and info['video_codec'] == 'hevc' and info['pix_fmt'] == 'yuv420p10le'
        assert 'Video: hevc' in probe and 'hvc1' in probe and 'yuv420p10le' in probe
        assert f'bt2020nc/bt2020/{expected_trc}' in probe and '320x180' in probe and '60 fps' in probe
    else:
        assert info['color_output'] == 'sdr-bt709' and info['video_codec'] == 'h264' and info['pix_fmt'] == 'yuv420p'
        assert 'Video: h264' in probe and 'yuv420p' in probe and '320x180' in probe and 'bt709' in probe and '60 fps' in probe
    assert 'Audio: aac' in probe
    measured = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', probe)
    assert measured, probe
    h, m, sec = map(float, measured.groups())
    assert h * 3600 + m * 60 + sec == pytest.approx(1.0, abs=0.12)
    completed = post(c, 'worker/jobs/' + job['id'] + '/complete',
                     {'token': job['token']}, 'test-worker-secret')
    assert completed.status_code == 200
    assert completed.json['result']['color_output'] == info['color_output']
    assert completed.json['result']['video_codec'] == info['video_codec']
    assert completed.json['result']['fps_output'] == 60
    if profile == 'hlg':
        # Verify coaching export on HDR clip still converts to 30fps 8-bit SDR H.264 and extracts frames.
        coach_body = dict(source_id=ID, clip_id=CLIP, publication=data['published_at'], start=0.25,
                          end=0.75, speed=0.5, crop=[0.25, 0.25, 0.5, 0.5], intent='coaching',
                          quality='balanced', request_id='coach-from-hlg')
        assert post(c, 'exports', coach_body).status_code == 201
        coach_job = post(c, 'worker/render/claim', {'worker_id': 'render-worker',
                         'capabilities': [RENDER_SHARE_CAPABILITY]}, 'test-worker-secret').json['job']
        coach_local = tmp_path / 'local-coach-hlg'
        coach_local.mkdir()
        coach_info = render(cfg, coach_job, coach_local)
        assert coach_info['color_output'] == 'sdr-bt709' and coach_info['video_codec'] == 'h264' and coach_info['fps_output'] == 30
        assert len(coach_info['frames']) >= 4
        # Verify 10-bit BT.709 SDR is treated as valid SDR rather than rejected as incomplete HDR.
        sdr10_src = src_path.with_name('sdr10.mp4')
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y',
                        '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=30:duration=1',
                        '-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p10le',
                        '-x265-params', 'pools=1:frame-threads=1:log-level=error:colorprim=1:transfer=1:colormatrix=1',
                        '-tag:v', 'hvc1', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                        str(sdr10_src)],
                       check=True, capture_output=True, timeout=20)
        sdr10_job = copy.deepcopy(job)
        sdr10_job['id'] = 'job-sdr10'
        sdr10_job['body']['hdr'] = True
        sdr10_job['body']['media_ref']['path'] = str(sdr10_src.relative_to(Path(app.config['LIBRARY_ROOT']) / 'media'))
        sdr10_job['body']['input_sha256'] = hashlib.sha256(sdr10_src.read_bytes()).hexdigest()
        sdr10_local = tmp_path / 'local-sdr10'
        sdr10_local.mkdir()
        sdr10_info = render(cfg, sdr10_job, sdr10_local)
        assert sdr10_info['color_output'] == 'sdr-bt709' and sdr10_info['video_codec'] == 'h264' and sdr10_info['pix_fmt'] == 'yuv420p'
        # Verify incomplete/untagged HDR metadata fails deterministically rather than guessing tags.
        bad_job = copy.deepcopy(job)
        bad_job['body']['hdr'] = True
        bad_src = src_path.with_name('untagged.mp4')
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y',
                        '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=30:duration=1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(bad_src)],
                       check=True, capture_output=True, timeout=20)
        bad_job['body']['media_ref']['path'] = str(bad_src.relative_to(Path(app.config['LIBRARY_ROOT']) / 'media'))
        bad_job['body']['input_sha256'] = hashlib.sha256(bad_src.read_bytes()).hexdigest()
        bad_local = tmp_path / 'local-bad-hdr'
        bad_local.mkdir()
        with pytest.raises(ValueError, match='HDR clip has incomplete or unsupported color metadata'):
            render(cfg, bad_job, bad_local)


def _complete_render_job(app, client, render_id):
    import hashlib
    claim = post(client, 'worker/render/claim', {'worker_id': 'render-auto'}, 'test-worker-secret').json['job']
    assert claim['id'] == render_id
    fence = hashlib.sha256(claim['token'].encode()).hexdigest()[:24]
    folder = Path(app.config['COACH_MEDIA_ROOT']) / claim['id'] / fence
    folder.mkdir(parents=True, exist_ok=True)
    frame_bytes = b'auto-frame-bytes'
    frame_sha = hashlib.sha256(frame_bytes).hexdigest()
    (folder / 'excerpt.mp4').write_bytes(b'auto-video')
    (folder / 'frame-000.jpg').write_bytes(frame_bytes)
    (folder / 'prompt.txt').write_text(claim['body'].get('question') or 'coach')
    evidence_dir = Path(app.config['COACH_MEDIA_ROOT']) / 'evidence/frames'
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / f'{frame_sha}.jpg').write_bytes(frame_bytes)
    payload = dict(
        recipe=claim['body'],
        lease_token=claim['token'],
        frames=[{
            'file': 'frame-000.jpg',
            'sha256': frame_sha,
            'original_seconds': claim['body']['original_start'] + 0.25,
            'clip_seconds': claim['body']['start'] + 0.25,
            'excerpt_seconds': 0.25 / claim['body']['speed'],
        }],
        bytes=10,
        output_seconds=2.0,
        sha256='c' * 64,
        render_seconds=0.4,
    )
    (folder / 'recipe.json').write_text(json.dumps(payload))
    done = post(client, 'worker/jobs/' + claim['id'] + '/complete', {'token': claim['token']}, 'test-worker-secret')
    assert done.status_code == 200
    return done.json


def test_auto_coach_cap_concurrency_idempotency_and_slot_lifecycle(setup):
    from worker.tennis_coach.annotations import CAPABILITY, evidence_manifest
    app, root, data = enabled(setup)
    c = app.test_client()

    # 1. Validation of auto_detect requirements on /exports
    base_auto = dict(
        selection(data),
        auto_detect=True,
        consent=True,
        question='Review my split step and recovery',
        auto_slot=1,
    )
    assert post(c, 'exports', dict(base_auto, consent=False, request_id='auto-no-consent')).status_code == 400
    assert post(c, 'exports', dict(base_auto, question='   ', request_id='auto-no-question')).status_code == 400
    assert post(c, 'exports', dict(base_auto, intent='share', quality='high', request_id='auto-share')).status_code == 400
    assert post(c, 'exports', dict(base_auto, auto_slot=3, request_id='auto-bad-slot')).status_code == 400

    # 2. Concurrent /exports race: 6 threads attempting to reserve auto-coach exports for the same clip -> exactly 2 succeed (201) and 4 conflict (409).
    def try_reserve(idx):
        client = app.test_client()
        payload = dict(
            base_auto,
            start=0.1 + idx * 0.05,
            end=1.2 + idx * 0.05,
            auto_slot=1 if idx % 2 == 0 else 2,
            request_id=f'auto-race-export-{idx:02d}',
        )
        res = post(client, 'exports', payload)
        return idx, res.status_code, res.json

    with ThreadPoolExecutor(6) as pool:
        outcomes = list(pool.map(try_reserve, range(6)))

    created = [o for o in outcomes if o[1] == 201]
    rejected = [o for o in outcomes if o[1] == 409]
    assert len(created) == 2
    assert len(rejected) == 4
    assert all('Auto coach is limited to 2 reviews per clip' in o[2]['error'] for o in rejected)

    r1_idx, _, r1 = created[0]
    r2_idx, _, r2 = created[1]

    # 3. Idempotent retry of an already-accepted request_id succeeds (201) even while 2/2 slots are full, while a mutated payload for that request_id conflicts (409).
    retry_r1 = post(
        c,
        'exports',
        dict(
            base_auto,
            start=0.1 + r1_idx * 0.05,
            end=1.2 + r1_idx * 0.05,
            auto_slot=1 if r1_idx % 2 == 0 else 2,
            request_id=f'auto-race-export-{r1_idx:02d}',
        ),
    )
    assert retry_r1.status_code == 201 and retry_r1.json['id'] == r1['id']
    conflict_r1 = post(
        c,
        'exports',
        dict(
            base_auto,
            start=0.5,
            end=1.9,
            request_id=f'auto-race-export-{r1_idx:02d}',
        ),
    )
    assert conflict_r1.status_code == 409 and 'Request ID already used' in conflict_r1.json['error']

    # 4. Failed render job releases its slot so a new auto-coach export can take its place.
    claim_r2 = post(c, 'worker/render/claim', {'worker_id': 'render-fail'}, 'test-worker-secret').json['job']
    # Claim order is FIFO; complete whichever was claimed first, fail the second.
    first_claimed, second_claimed = (r1, r2) if claim_r2['id'] == r1['id'] else (r2, r1)
    assert post(
        c,
        'worker/jobs/' + first_claimed['id'] + '/fail',
        {'token': claim_r2['token'], 'result': {'error': 'FFmpeg failed'}},
        'test-worker-secret',
    ).status_code == 200

    # Now 1 slot is free! Complete second_claimed and transition it to an AI request.
    _complete_render_job(app, c, second_claimed['id'])
    ai1_res = post(
        c,
        'requests',
        {
            'export_id': second_claimed['id'],
            'prompt': second_claimed['body']['question'],
            'consent': True,
            'auto_detect': True,
            'request_id': 'auto-ai-' + second_claimed['id'],
        },
    )
    assert ai1_res.status_code == 201
    ai1 = ai1_res.json
    assert ai1['body']['auto_detect'] is True

    # Duplicate AI request with a different request_id for the same completed export is rejected.
    dup_ai = post(
        c,
        'requests',
        {
            'export_id': second_claimed['id'],
            'prompt': second_claimed['body']['question'],
            'consent': True,
            'auto_detect': True,
            'request_id': 'auto-ai-duplicate-id',
        },
    )
    assert dup_ai.status_code == 409

    # 5. Reserve the 2nd slot again, complete render, then test pre-call AI failure vs post-calling failure / needs_review.
    r3_res = post(c, 'exports', dict(base_auto, start=0.3, end=1.7, auto_slot=2, request_id='auto-export-slot2-retry'))
    assert r3_res.status_code == 201
    r3 = r3_res.json
    # Cap is now 2/2 again (ai1 + r3).
    assert post(c, 'exports', dict(base_auto, start=0.4, end=1.8, request_id='auto-export-over-cap')).status_code == 409

    _complete_render_job(app, c, r3['id'])
    ai2_res = post(
        c,
        'requests',
        {
            'export_id': r3['id'],
            'prompt': r3['body']['question'],
            'consent': True,
            'auto_detect': True,
            'request_id': 'auto-ai-' + r3['id'],
        },
    )
    assert ai2_res.status_code == 201
    ai2 = ai2_res.json

    # Claim ai1 and fail it BEFORE calling (preflight failure while leased) -> releases slot!
    claim_ai1 = post(
        c,
        'worker/ai/claim',
        {'worker_id': 'ai-worker', 'capabilities': [CAPABILITY]},
        'test-worker-secret',
    ).json['job']
    assert claim_ai1['id'] == ai1['id']
    assert post(
        c,
        'worker/jobs/' + ai1['id'] + '/fail',
        {'token': claim_ai1['token'], 'result': {'error': 'Preflight login missing'}},
        'test-worker-secret',
    ).status_code == 200
    assert c.get('/api/coaching/jobs/' + ai1['id']).json['result'].get('provider_called') is None

    # Because ai1 failed before calling, 1 slot is free again!
    r4_res = post(c, 'exports', dict(base_auto, start=0.2, end=1.6, auto_slot=1, request_id='auto-export-after-preflight-fail'))
    assert r4_res.status_code == 201
    r4 = r4_res.json
    # Cancel r4 after it completes (before AI request is created) -> also releases slot!
    _complete_render_job(app, c, r4['id'])
    r4_done = c.get('/api/coaching/jobs/' + r4['id']).json
    cancel_r4 = c.put(
        '/api/coaching/jobs/' + r4['id'],
        json={'action': 'cancel', 'version': r4_done['version']},
        headers={'X-Tennis-Request': '1'},
    )
    assert cancel_r4.status_code == 200 and cancel_r4.json['state'] == 'cancelled'

    # Now claim ai2, register evidence, cross 'calling', and fail AFTER calling -> provider_called=True, permanently consumes slot!
    claim_ai2 = post(
        c,
        'worker/ai/claim',
        {'worker_id': 'ai-worker', 'capabilities': [CAPABILITY]},
        'test-worker-secret',
    ).json['job']
    assert claim_ai2['id'] == ai2['id']
    assert post(
        c,
        'worker/jobs/' + ai2['id'] + '/evidence',
        {'token': claim_ai2['token'], 'result': evidence_manifest(claim_ai2)},
        'test-worker-secret',
    ).status_code == 200
    assert post(
        c,
        'worker/jobs/' + ai2['id'] + '/calling',
        {'token': claim_ai2['token']},
        'test-worker-secret',
    ).status_code == 200
    assert post(
        c,
        'worker/jobs/' + ai2['id'] + '/fail',
        {'token': claim_ai2['token'], 'result': {'error': 'Provider timeout after sending'}},
        'test-worker-secret',
    ).status_code == 200
    assert c.get('/api/coaching/jobs/' + ai2['id']).json['result'].get('provider_called') is True

    # Create r5 to fill the remaining 1 slot; a 3rd slot attempt must fail because ai2 (failed post-calling) + r5 = 2/2.
    r5_res = post(c, 'exports', dict(base_auto, start=0.3, end=1.8, auto_slot=1, request_id='auto-export-r5'))
    assert r5_res.status_code == 201
    assert post(c, 'exports', dict(base_auto, start=0.5, end=1.9, request_id='auto-export-blocked-by-post-call-fail')).status_code == 409


def test_auto_coach_player_and_publication_validation_and_manual_coexistence(setup):
    app, root, data = enabled(setup)
    c = app.test_client()
    # Create a saved player and assign as clip main player
    p_res = c.post('/api/players', json={'name': 'Alex', 'height_cm': 178, 'level': 'intermediate'}, headers={'X-Tennis-Request': '1'})
    assert p_res.status_code == 201
    player = p_res.json
    assert c.put(
        f'/api/sessions/{ID}/clips/{CLIP}/player',
        json={'player_id': player['id'], 'version': 0, 'publication': data['published_at']},
        headers={'X-Tennis-Request': '1'},
    ).status_code == 200

    base_auto = dict(
        selection(data),
        auto_detect=True,
        consent=True,
        question='Check my preparation',
        player_id=player['id'],
        player_profile_version=player['version'],
    )
    # Stale player_profile_version or publication is rejected on /exports immediately
    assert post(c, 'exports', dict(base_auto, player_profile_version=player['version'] + 9, request_id='stale-player-ver')).status_code == 409
    assert post(c, 'exports', dict(base_auto, publication=data['published_at'] - 1, request_id='stale-pub')).status_code == 409

    # Fill 2/2 auto-coach slots for CLIP
    for idx in (1, 2):
        r = post(c, 'exports', dict(base_auto, start=0.1 * idx, end=1.4 + 0.1 * idx, auto_slot=idx, request_id=f'auto-full-{idx}'))
        assert r.status_code == 201

    assert post(c, 'exports', dict(base_auto, start=0.5, end=1.8, request_id='auto-full-3')).status_code == 409

    # Manual coaching export and manual AI request on the SAME clip are completely unaffected by the 2/2 auto cap!
    manual_exp = post(c, 'exports', dict(selection(data), question='Manual question', request_id='manual-while-auto-full'))
    assert manual_exp.status_code == 201
    # Complete the two auto exports first in FIFO order, then the manual export
    with app.extensions['coaching'].db() as db:
        queued_renders = [r['id'] for r in db.execute("SELECT id FROM jobs WHERE role='render' AND state='queued' ORDER BY updated").fetchall()]
    for rid in queued_renders:
        _complete_render_job(app, c, rid)

    manual_ai = post(
        c,
        'requests',
        {
            'export_id': manual_exp.json['id'],
            'prompt': 'Manual question',
            'consent': True,
            'player_id': player['id'],
            'player_profile_version': player['version'],
            'request_id': 'manual-ai-while-auto-full',
        },
    )
    assert manual_ai.status_code == 201 and not manual_ai.json['body'].get('auto_detect')

    # Another clip in the same session or a new publication has an independent 2-review cap.
    clip2_id = 'c' * 24
    (root / 'media' / ID / 'Clips/clip-002.mp4').write_bytes(b'video')
    data['video']['duration_seconds'] = 12
    data['kept_seconds'] = 12
    data['removed_seconds'] = 8
    data['clips'].append(
        dict(
            data['clips'][0],
            id=clip2_id,
            index=2,
            file='Clips/clip-002.mp4',
            duration_seconds=2,
            original_start_seconds=16,
            original_end_seconds=18,
            playback_start_seconds=10,
            playback_end_seconds=12,
        )
    )
    (Path(app.config['LIBRARY_ROOT']) / 'metadata/sessions' / f'{ID}.json').write_text(json.dumps(data))
    other_clip_auto = dict(
        selection(data),
        clip_id=clip2_id,
        start=0.2,
        end=1.8,
        auto_detect=True,
        consent=True,
        question='Check clip 2',
        request_id='auto-clip-2-slot-1',
    )
    assert post(c, 'exports', other_clip_auto).status_code == 201

    # Reprocessing into a new publication resets the per-publication clip auto-coach cap while keeping old history intact.
    data['published_at'] += 10
    (Path(app.config['LIBRARY_ROOT']) / 'metadata/sessions' / f'{ID}.json').write_text(json.dumps(data))
    new_pub_auto = dict(
        selection(data),
        publication=data['published_at'],
        auto_detect=True,
        consent=True,
        question='Check new publication clip 1',
        request_id='auto-new-pub-slot-1',
    )
    assert post(c, 'exports', new_pub_auto).status_code == 201


def test_auto_coach_cancel_create_race_and_listing_retention_beyond_limit_100(setup):
    app, root, data = enabled(setup)
    c = app.test_client()
    queue = app.extensions['coaching']
    base_auto = dict(
        selection(data),
        auto_detect=True,
        consent=True,
        question='Check my auto-detected clip moment',
    )

    # 1. Ordering A: completed auto render is cancelled after route precheck but immediately before queue.create('ai') enters BEGIN IMMEDIATE.
    exp_a = post(
        c,
        'exports',
        dict(base_auto, start=0.2, end=1.4, auto_slot=1, request_id='race-exp-a'),
    ).json
    _complete_render_job(app, c, exp_a['id'])
    completed_a = c.get(f"/api/coaching/jobs/{exp_a['id']}").json
    assert completed_a['state'] == 'completed'

    original_create = queue.create

    def cancel_before_ai_create(role, body, request_id):
        if role == 'ai' and body.get('export_id') == exp_a['id']:
            latest = queue.get(exp_a['id'])
            if latest['state'] == 'completed':
                queue.edit(exp_a['id'], latest['version'], 'cancel')
        return original_create(role, body, request_id)

    queue.create = cancel_before_ai_create
    try:
        r_ai_a = post(
            c,
            'requests',
            {
                'export_id': exp_a['id'],
                'prompt': 'Race A prompt',
                'consent': True,
                'auto_detect': True,
                'request_id': 'race-ai-a',
            },
        )
        assert r_ai_a.status_code == 409
        assert 'Prepare an excerpt first' in r_ai_a.json['error']
        # Idempotent replay with same request_id also rejects with 409 because no AI job was inserted.
        r_ai_a_replay = post(
            c,
            'requests',
            {
                'export_id': exp_a['id'],
                'prompt': 'Race A prompt',
                'consent': True,
                'auto_detect': True,
                'request_id': 'race-ai-a',
            },
        )
        assert r_ai_a_replay.status_code == 409
    finally:
        queue.create = original_create

    assert queue.get(exp_a['id'])['state'] == 'cancelled'
    with queue.db() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE role='ai' AND json_extract(body,'$.export_id')=?", (exp_a['id'],)).fetchone()[0] == 0
        assert queue.active_auto_coach_slots(db, ID, data['published_at'], CLIP) == 0

    # 2. Ordering B: AI creation commits immediately before cancel enters BEGIN IMMEDIATE; cancel must reject with 409 and AI stays queued.
    exp_b = post(
        c,
        'exports',
        dict(base_auto, start=0.3, end=1.5, auto_slot=1, request_id='race-exp-b'),
    ).json
    _complete_render_job(app, c, exp_b['id'])
    completed_b = c.get(f"/api/coaching/jobs/{exp_b['id']}").json

    original_edit = queue.edit

    def create_ai_before_cancel(identity, version, action, cue=None):
        if identity == exp_b['id'] and action == 'cancel':
            res = post(
                c,
                'requests',
                {
                    'export_id': exp_b['id'],
                    'prompt': 'Race B prompt',
                    'consent': True,
                    'auto_detect': True,
                    'request_id': 'race-ai-b',
                },
            )
            assert res.status_code == 201
        return original_edit(identity, version, action, cue)

    queue.edit = create_ai_before_cancel
    try:
        r_cancel_b = c.put(
            f"/api/coaching/jobs/{exp_b['id']}",
            json={'action': 'cancel', 'version': completed_b['version']},
            headers={'X-Tennis-Request': '1'},
        )
        assert r_cancel_b.status_code == 409
    finally:
        queue.edit = original_edit

    assert queue.get(exp_b['id'])['state'] == 'completed'
    # Idempotent replay of the winning AI request returns the same queued AI job.
    r_ai_b_replay = post(
        c,
        'requests',
        {
            'export_id': exp_b['id'],
            'prompt': 'Race B prompt',
            'consent': True,
            'auto_detect': True,
            'request_id': 'race-ai-b',
        },
    )
    assert r_ai_b_replay.status_code == 201
    assert r_ai_b_replay.json['state'] == 'queued'

    # Cancel the queued AI job to free the slot before testing >100 listing retention.
    ai_b = r_ai_b_replay.json
    assert (
        c.put(
            f"/api/coaching/jobs/{ai_b['id']}",
            json={'action': 'cancel', 'version': ai_b['version']},
            headers={'X-Tennis-Request': '1'},
        ).status_code
        == 200
    )

    # 3. Retained auto-detect render reservations older than 100 newer render jobs remain visible in listing(ID).
    old_auto_1 = post(
        c,
        'exports',
        dict(base_auto, start=0.1, end=1.1, auto_slot=1, request_id='old-auto-retain-1'),
    ).json
    old_auto_2 = post(
        c,
        'exports',
        dict(base_auto, start=1.2, end=2.2, auto_slot=2, request_id='old-auto-retain-2'),
    ).json
    _complete_render_job(app, c, old_auto_1['id'])
    # Insert 105 newer share/manual render jobs on the same recording ID so old_auto_1 and old_auto_2 fall outside the top 100 by timestamp.
    for i in range(105):
        r_share = post(
            c,
            'exports',
            dict(selection(data), intent='share', quality='high', start=0.1, end=0.5, request_id=f'bulk-share-{i:03d}'),
        )
        assert r_share.status_code == 201

    listed = c.get(f'/api/coaching/sessions/{ID}').json
    listed_ids = {j['id'] for j in listed['jobs']}
    assert old_auto_1['id'] in listed_ids, 'Completed unsubmitted auto_detect render must remain visible beyond LIMIT 100'
    assert old_auto_2['id'] in listed_ids, 'Queued unsubmitted auto_detect render must remain visible beyond LIMIT 100'
