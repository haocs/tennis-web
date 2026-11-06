#!/usr/bin/env bash
# Run only against a locally built disposable test image, never against NAS data.
set -euo pipefail
image="${1:?Usage: container-smoke.sh IMAGE}"
scratch="$(mktemp -d)"
name="courtside-smoke-$(basename "$scratch" | tr '[:upper:]' '[:lower:]')"
volume="$name-state"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  rm -rf "$scratch"
}
trap cleanup EXIT
chmod 755 "$scratch"
mkdir -p "$scratch/library/metadata/sessions" "$scratch/library/media"
printf 'read-only fixture' > "$scratch/library/probe.txt"
python3 - "$scratch/library" <<'FIXTURE'
import json,sys,shutil
from pathlib import Path
root=Path(sys.argv[1]);identity='a'*64;clip='b'*24
media=root/'media'/identity/'Clips';media.mkdir(parents=True)
shutil.copyfile('tests/fixtures/clip.mp4',media/'clip-001.mp4')
shutil.copyfile('tests/fixtures/practice.mp4',media.parent/'Practice.mp4')
data=dict(schema_version=1,id=identity,title='Container fixture',published_at=1,
          source=dict(name='fixture.mov',duration_seconds=6),video=dict(file='Practice.mp4',duration_seconds=6,hdr=False),
          clips=[dict(id=clip,index=1,file='Clips/clip-001.mp4',duration_seconds=2,original_start_seconds=0,original_end_seconds=2,playback_start_seconds=0,playback_end_seconds=2)])
(root/'metadata/sessions'/f'{identity}.json').write_text(json.dumps(data))
FIXTURE
chmod -R a+rX "$scratch/library"
docker volume create "$volume" >/dev/null
docker run -d --name "$name" --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --mount "type=bind,source=$scratch/library,target=/library,readonly" \
  --mount "type=volume,source=$volume,target=/data" "$image" >/dev/null
wait_healthy() {
  for attempt in {1..30}; do
    if docker exec "$name" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2)" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  docker logs "$name"
  return 1
}
wait_healthy
docker exec -i "$name" python - <<'PYTHON'
import os
from pathlib import Path
assert os.getuid() == 10001
assert Path('/library/probe.txt').read_text() == 'read-only fixture'
assert os.statvfs('/library').f_flag & os.ST_RDONLY
try:
    Path('/library/probe.txt').write_text('unexpected mutation')
except OSError:
    pass
else:
    raise AssertionError('Library is writable')
Path('/data/smoke-state').write_text('persisted')
import urllib.request,json
base='http://127.0.0.1:8000'
body=dict(source_id='a'*64,publication=1,kind='highlight',target=dict(scope='point',start_seconds=.5),body=dict(text='Container metadata'),request_id='container-highlight-001')
req=urllib.request.Request(base+'/api/annotations',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Tennis-Request':'1'},method='POST')
note=json.load(urllib.request.urlopen(req));assert note['training_label_status']=='not_released'
Path('/data/smoke-note-id').write_text(note['id'])
assert Path('/data/annotations.sqlite3').is_file()
image=urllib.request.urlopen('http://127.0.0.1:8000/api/sessions/'+('a'*64)+'/clips/'+('b'*24)+'/poster').read()
assert image.startswith(b'\xff\xd8')
assert len(list(Path('/data/clip-posters').glob('*.jpg')))==1
import hashlib
review=urllib.request.urlopen(base+'/review/'+('a'*64))
csp=review.headers.get('Content-Security-Policy','')
assert "script-src 'self' 'wasm-unsafe-eval'" in csp and "worker-src 'self'" in csp and "'unsafe-eval'" not in csp
worker_res=urllib.request.urlopen(base+'/static/autofocus-worker.js')
assert worker_res.status==200 and 'javascript' in worker_res.headers.get('Content-Type','')
manifest=json.load(urllib.request.urlopen(base+'/static/vendor/mediapipe/MANIFEST.json'))
assert urllib.request.urlopen(base+'/static/vendor/mediapipe/LICENSE').status==200
expected_types={'vision_bundle.js':'javascript','vision_wasm_internal.js':'javascript','vision_wasm_internal.wasm':'application/wasm','efficientdet_lite0.tflite':'application/octet-stream'}
for name,meta in manifest['files'].items():
    res=urllib.request.urlopen(base+'/static/vendor/mediapipe/'+name)
    payload=res.read()
    assert res.status==200 and expected_types[name] in res.headers.get('Content-Type','')
    assert len(payload)==meta['bytes'] and hashlib.sha256(payload).hexdigest()==meta['sha256']
PYTHON
docker restart "$name" >/dev/null
wait_healthy
docker exec "$name" python -c "from pathlib import Path; assert Path('/data/smoke-state').read_text() == 'persisted'"
docker exec -i "$name" python - <<'PYTHON'
import json,urllib.request
from pathlib import Path
source='a'*64
items=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/annotations/sources/'+source))['annotations']
assert any(a['id']==Path('/data/smoke-note-id').read_text() and a['body']['text']=='Container metadata' for a in items)
assert Path('/library/probe.txt').read_text()=='read-only fixture'
PYTHON
echo 'Container smoke check passed'
