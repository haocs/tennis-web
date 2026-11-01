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

## Home-page practices and original recording time

Newest/oldest sorting uses the original recording's timezone-aware creation timestamp from processing metadata, never upload, file modification, or publication time. UTC instants determine order; the recording's own date and time remain visible. Missing/invalid timestamps and timestamps with no timezone are not guessed from the server clock; those recordings sort last and remain separate.

The home page groups overlapping recordings and recordings starting within 30 minutes of the previous recording ending into one practice card. Each card retains a separate link and favorite control for every recording. Durations and clip counts are summed. This does not merge files, change stable identities, alter review pages, or migrate existing likes/edits. `TENNIS_SESSION_GAP_MINUTES` configures the gap (default 30). Liked views retain the existing individual recording/clip favorites.

## Home-page worker summary

The home header places a compact, live worker-status link to the left of session,
clip and practice-time totals. Slogans are removed; the logo and brand remain.
Set `TENNIS_PROCESSING_ROOM_URL` to your existing processing control portal in
private deployment settings. The link opens that portal in the same tab. Without
this setting, `/processing` provides a local read-only worker/queue status page.
Worker unavailable, stale and paused states remain visible and clickable. Status
refreshes every 15 seconds; the detailed queue is kept off the home summary.

## Saved courts and home-page names

Home practice titles use `YYYY/MM/DD Morning/Afternoon/Evening/Night`, using the first original recording's own date/timezone. When multiple practices share the same date and time period, each title adds ` @ Court name`. Collision detection uses the full library, so filtering and sorting keep titles stable. All recordings, including a single recording, are listed separately inside that card. Review-page titles remain the individual video titles.

Open the **three-dot options** at the top right of a practice thumbnail, then choose **Change court** (or **Name or choose court** for an unidentified court) to select a remembered court or add one. Identified practices display the court name in place of GPS coordinates; unidentified practices retain their GPS information. Adding a court pre-fills coordinates and optional altitude from an actual original recording; you can adjust its name, position, and matching range. A court saved without coordinates can learn them when selected for a practice with GPS. **Saved courts** lets you edit names, optional street addresses (up to 500 characters), and GPS locations centrally. Existing courts without an address remain valid, and address edits never change recording GPS or court matching.

The default matching range is 150 metres (configurable per court from 25 to 2000 metres). A GPS match is automatic only when one saved court contains every available recording position in the practice. Matching uses horizontal distance; altitude is retained/displayed but does not reject a match. Overlapping saved court ranges produce suggestions requiring a choice. An explicit practice selection takes priority over GPS. Missing GPS is never guessed or sent to an external geocoder.

Court names/coordinates and recording assignments live in the existing private website database, separate from read-only processing metadata. Renaming a saved court updates its card location and any title suffix used to distinguish practices. Per-recording assignment identities preserve selections across regrouping; conflicting selections in a newly merged practice are flagged. Court edits and all-recording assignments use optimistic versions and atomic transactions. Existing video names, likes, metadata edits, files, and review URLs are preserved. Database backups include saved courts automatically.

## Download the current video

The player has one download action: **Download full practice** while the combined video is loaded, or **Download clip NN** while an individual clip is loaded. Seeking within the full practice still downloads the full practice. Downloads use the website over HTTP and do not require SMB or HTTPS. On iPhone, open a downloaded video in Files and use Share → Save Video when available to save it to Photos.

## Locate a file on the NAS

Set `TENNIS_LIBRARY_SMB_URL` to the credential-free SMB address of the same published library folder mounted by `TENNIS_LIBRARY_ROOT`, for example `smb://nas/Shared/library`. The review shows a NAS address button for each clip and the currently playing file. It displays and copies the exact URL without opening SMB, authenticating, checking visitor permissions, or modifying the file.

NAS names and paths belong only in private deployment settings. They are never baked into the image. If a separate `TENNIS_MEDIA_ROOT` is configured, set `TENNIS_MEDIA_SMB_URL` to that media directory's SMB address; the app avoids guessing a link to the wrong location. Without an address setting, the controls stay hidden.

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
