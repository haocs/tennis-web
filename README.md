# Courtside

A responsive, local-network tennis library. Browse processed sessions, watch chaptered practice videos or embedded clips, sort/filter clips, and save shared likes. No separate login or account system.

## Storage contract

Point `TENNIS_LIBRARY_ROOT` to a mounted filesystem path or `file://` URL:

```
library/
  .tennis-library-id
  media/<source-sha256>/Practice.mp4
  media/<source-sha256>/Clips/clip-001.mp4
  media/<source-sha256>/poster.jpg
  metadata/sessions/<source-sha256>.json
  metadata/sessions/<source-sha256>/edit-timeline.csv
  metadata/sessions/<source-sha256>/edit-timeline.json
  metadata/sessions/<source-sha256>/Review.html
  metadata/Processing-status.json
```

Media and processing metadata are read-only to the application. Mutable website state is a separate SQLite database in `TENNIS_DATA_ROOT`, backed by its own persistent Docker volume. The application never needs a mount of the private originals folder.

Supported storage adapters: mounted POSIX filesystems and local `file://` URLs. Mount SMB/NFS outside the application; an `smb://` link is not accepted as a browser media URL. Object-storage and HTTP-catalog adapters are future work. `TENNIS_MEDIA_ROOT` optionally supplies a separate mounted media root for read-only legacy previews; never point it at originals.

The processing pipeline publishes a JSON manifest last. The website discovers committed manifests only. Each source content hash is a stable session ID; each clip ID derives from the source hash and original cut boundaries. Like state survives moves and re-encoding with unchanged cut boundaries. Changed boundaries create new clip IDs; old preferences remain in the state database for future reconciliation rather than being silently applied to different cuts.

## Local run

Use Python 3.12 or newer:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export TENNIS_LIBRARY_ROOT=/path/to/published/library
export TENNIS_DATA_ROOT=/path/to/separate/website-data
.venv/bin/gunicorn --bind 127.0.0.1:8765 --workers 2 --threads 4 --timeout 120 'app:create_app()'
```

The server binds to loopback in this example. The Docker image listens on its internal port, while Compose publishes that port on the NAS loopback interface for Synology's reverse proxy.

## Synology deployment

See [Synology deployment](docs/SYNOLOGY.md), including the optional existing-owner UID and separate native-host state override. The NAS **pulls a published container image**; it never builds application source. Only `compose.yaml` and a private `.env` containing deployment settings are needed on the container host. Videos, processing metadata, and website state stay outside the image.

Set `TENNIS_IMAGE` to the real release image or immutable digest, `TENNIS_LIBRARY_PATH` to the actual shared library filesystem path, and `TENNIS_MEDIA_GID` to an appropriate read group. No release image is shipped or implied by this source folder.

```sh
cp .env.example .env
# Edit .env with the published image and your host paths.
docker compose pull
docker compose up -d --no-build
docker compose ps
docker compose logs --follow
docker compose restart
```

To update, back up website state, select a verified release digest in `.env`, then pull and recreate the service with `docker compose up -d --no-build`. Restart alone does not update an image. To roll back, restore the prior image digest using the same pull/recreate steps. Keep the Compose project name and persistent state volume unchanged. Do not use volume-removing cleanup operations for upgrades.

## GitHub releases and public images

The included [workflow](.github/workflows/release.yml) runs application tests and a real container smoke check on pull requests and pushes to `main`. Publishing a GitHub release tagged `vMAJOR.MINOR.PATCH` repeats those checks, then builds `linux/amd64` and `linux/arm64` images in GitHub Container Registry using the built-in `GITHUB_TOKEN`. No registry password is needed in the NAS configuration for a public image.

The image name is derived from the lowercase repository name: `ghcr.io/owner/repository`. Releases produce a version tag, a source-commit tag, and an immutable digest printed in the workflow summary. Use that digest for deployment. The Docker build context allows only runtime application files; private configuration, footage, databases, and documentation are excluded.

For the **first release**, set the package visibility to **Public** in GitHub package settings. Public repository visibility alone does not make a newly created package public. Confirm an anonymous pull from a clean Docker configuration before configuring the NAS. See [release operations](docs/RELEASE.md).

## Shared state and concurrency

All visitors share session likes, clip likes, titles, and location overrides. There are no website accounts. Changes use explicit desired values, optimistic version checks, SQLite transactions, WAL, and a busy timeout. A stale edit receives HTTP 409 instead of overwriting another visitor's change. Browsers refresh shared state periodically while preserving active playback. Cross-origin mutations are rejected.

Storage file permissions apply to the container process. A reverse proxy does not automatically apply a visitor's DSM identity or folder ACLs. Publish only content intentionally shared with all site visitors. Synology identity/SSO is optional future work and is not required by this application.

## Playback

Chapter starts are measured against actual keyframe timestamps in the combined export, including concat/audio rounding. Original timestamps are kept separately. The review uses one embedded player for full practice and individual clips, with Previous/Next following the current sort/filter. Removing a playing clip from a liked-only list lets it finish; navigation then uses the remaining list. Autoplay is opt-in. At the final clip it stops.

HEVC HDR playback requires a browser/device that supports the exported format. Codec support and HDR rendering vary by browser. The original generated review and timeline downloads remain available.

## Metadata

Recording date/time and GPS coordinates come from iPhone metadata extracted by the processor. Dates use the recording's calendar date, retaining its original timezone in metadata. Human-readable location labels can be entered locally; no external geocoding service is required. Overrides live in website state and do not rewrite processing metadata.

## Backups and restore

Create a consistent state backup, including committed WAL changes, without copying media:

```sh
docker compose exec courtside python manage.py backup /data/backups/website-backup.sqlite3
docker compose cp courtside:/data/backups/website-backup.sqlite3 ./website-backup.sqlite3
```

Keep the backup outside the application volume too. Back up the published library independently. For restore, stop the app first, verify the backup with SQLite's `PRAGMA integrity_check`, replace `website.sqlite3` in the state volume with the backup, remove stale `-wal`/`-shm` companions while stopped, and preserve UID 10001 ownership. Start the app and check likes/overrides. Do not place the live SQLite state on an SMB client mount; the named volume is local storage on the container host.

## Verification

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Tests cover ranged video serving, path/symlink isolation, optimistic concurrency, simultaneous writers, metadata persistence, original-review integration, and redacted/stale-aware status. Browser verification also covers sorting, embedded playback, chapter seeking, navigation, and responsive layouts.

Cut-quality feedback and algorithm improvement remain future work. See the project todo document and algorithm roadmap.
