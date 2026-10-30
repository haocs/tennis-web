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
PYTHON
docker restart "$name" >/dev/null
wait_healthy
docker exec "$name" python -c "from pathlib import Path; assert Path('/data/smoke-state').read_text() == 'persisted'"
echo 'Container smoke check passed'
