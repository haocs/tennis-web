"""Posters come from the selected clip and never change the read-only library."""
import copy
import json
import shutil
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_app import setup, ID, CLIP


def ffmpeg():
    binary = shutil.which('ffmpeg')
    if not binary:
        pytest.skip('ffmpeg is required for thumbnail integration tests')
    return binary


def video(binary, path, color, hdr=False):
    args = [binary, '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'color={color}:s=96x64:d=1',
            '-pix_fmt', 'yuv420p']
    if hdr:
        args += ['-vf', 'setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc', '-color_primaries', 'bt2020', '-color_trc', 'smpte2084', '-colorspace', 'bt2020nc']
    subprocess.run(args+[str(path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def test_clip_posters_are_distinct_cached_and_publication_bound(setup):
    binary=ffmpeg();app,root,data=setup;c=app.test_client()
    second=copy.deepcopy(data['clips'][0]);second.update(id='c'*24,index=2,file='Clips/clip-002.mp4',original_start_seconds=15,original_end_seconds=20,playback_start_seconds=10,playback_end_seconds=15,duration_seconds=5)
    data['video']['duration_seconds']=15;data['clips'].append(second)
    manifest=root/'metadata/sessions'/f'{ID}.json';manifest.write_text(json.dumps(data))
    one=root/'media'/ID/'Clips/clip-001.mp4';two=one.with_name('clip-002.mp4')
    video(binary,one,'red');video(binary,two,'blue')
    originals={p:p.read_bytes() for p in [one,two,manifest]}
    url=f'/api/sessions/{ID}/clips/{CLIP}/poster?v={data["published_at"]}'
    # Concurrent requests reuse a single atomic cached image.
    def read(_):return app.test_client().get(url)
    with ThreadPoolExecutor(4) as pool:responses=list(pool.map(read,range(4)))
    assert all(r.status_code==200 and r.data.startswith(b'\xff\xd8') for r in responses)
    cache=Path(app.config['DATA_ROOT'])/'clip-posters'
    assert len(list(cache.glob('*.jpg')))==1
    cached=next(cache.glob('*.jpg'));before=cached.stat().st_mtime_ns
    assert c.get(url).data==responses[0].data and cached.stat().st_mtime_ns==before
    other=c.get(f'/api/sessions/{ID}/clips/{second["id"]}/poster')
    assert other.status_code==200 and other.data!=responses[0].data
    assert c.get(url,headers={'If-None-Match':responses[0].headers['ETag']}).status_code==304
    assert c.get(url.replace(str(data['published_at']),'0')).status_code==409
    data['published_at']+=1;manifest.write_text(json.dumps(data))
    assert c.get(url).status_code==409
    assert c.get(url.split('?')[0]+f'?v={data["published_at"]}').status_code==200
    assert len(list(cache.glob('*.jpg')))==3
    assert one.read_bytes()==originals[one] and two.read_bytes()==originals[two]


def test_hdr_thumbnail_and_media_boundaries(setup,tmp_path):
    binary=ffmpeg();app,root,data=setup;c=app.test_client()
    source=root/'media'/ID/'Clips/clip-001.mp4';video(binary,source,'gray',hdr=True)
    data['video']['hdr']=True;(root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    url=f'/api/sessions/{ID}/clips/{CLIP}/poster'
    r=c.get(url);assert r.status_code==200 and r.mimetype=='image/jpeg'
    assert c.get(f'/api/sessions/{ID}/clips/'+('f'*24)+'/poster').status_code==404
    source.unlink();private=tmp_path/'private.mp4';private.write_bytes(b'private');source.symlink_to(private)
    assert c.get(url).status_code==404
    source.unlink();assert c.get(url).status_code==404
    source.write_bytes(b'invalid movie')
    assert c.get(url).status_code==503
    assert not list((Path(app.config['DATA_ROOT'])/'clip-posters').glob('tmp*.jpg'))
