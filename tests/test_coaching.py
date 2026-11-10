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
