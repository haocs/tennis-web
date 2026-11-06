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

For a complete application backup, stop the website container and copy its entire private state directory/volume, including `website.sqlite3`, `annotations.sqlite3`, `coaching.sqlite3` and any WAL/SHM companions. Retain coaching evidence storage alongside it. Reaction changes and Moment history live in `annotations.sqlite3`; a `website.sqlite3` backup alone does not include them. Back up published media separately.

The following utility backs up **only `website.sqlite3`**, including committed WAL changes, without copying media:

```sh
docker compose exec courtside python manage.py backup /data/backups/website-backup.sqlite3
docker compose cp courtside:/data/backups/website-backup.sqlite3 ./website-backup.sqlite3
```

Keep the backup outside the application volume too. Back up the published library independently. For restore, stop the app first, verify the backup with SQLite's `PRAGMA integrity_check`, replace `website.sqlite3` in the state volume with the backup, remove stale `-wal`/`-shm` companions while stopped, and preserve UID 10001 ownership. For a full restore, restore the matching stopped-state bundle of all databases and retained evidence, then start the app and check likes, dislikes, overrides and coaching. Single-database restoration is only suitable for the fields owned by that database. Do not place the live SQLite state on an SMB client mount; the named volume is local storage on the container host.

## Verification

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Tests cover ranged video serving, path/symlink isolation, optimistic concurrency, simultaneous writers, metadata persistence, original-review integration, and redacted/stale-aware status. Browser verification also covers sorting, embedded playback, chapter seeking, navigation, and responsive layouts.

Cut-quality feedback and algorithm improvement remain future work. See the project todo document and algorithm roadmap.

## Player controls and browser regression tests

Recording reviews and liked cards use the same resolved saved court as their practice on the library page. A manual practice choice wins over GPS; ambiguous matches keep the existing location fallback.

Click or keyboard-activate an entire on-court moment to load and play that clip. Like the current clip with the heart beside the player. The icon-only download action follows the loaded video; the three-dot menu contains the NAS address.

Previous, next, play/pause, autoplay, and a thin seek bar overlay the picture and hide while playing. Move the pointer or tap the picture to reveal them; paused playback and keyboard navigation keep controls visible. Clips are available from a collapsed menu below the player. This normal player has no audio or speed controls; speed adjustments belong to the future deep-dive page.

Desktop and supported tablet fullscreen include the overlay. On iPhone, the fullscreen button opens the real native video player rather than enlarging a page element. Apple supplies the fullscreen controls there. The review registers native Media Session previous/next actions using the current clip queue, plus playback metadata and position. System controls are browser-dependent; registering handlers does not guarantee visible next/previous buttons in the iPhone fullscreen view. Site buttons remain available inline. There is one Fullscreen action. If native controls do not offer clip navigation, exit fullscreen and use the inline previous/next controls. An inline hint appears after entering native fullscreen; the site does not substitute an expanded page view for true fullscreen. Keyboard Space/K toggles playback and Left/Right seeks when the player region has focus.

Run the application suite with `python -m pytest -q`. For the isolated desktop/mobile browser suite:

```sh
python -m pip install -r requirements-ui.txt
python -m playwright install chromium webkit
python -m pytest tests/ui_player.py -q
```

The tiny MP4s in `tests/fixtures` are generated solid-color test videos, not user footage. Browser tests use a temporary synthetic library. They cover court names, clickable rows, current-video downloads, fullscreen navigation, autoplay, overlay geometry/auto-hide, and native iPhone fullscreen API routing, native media-action queue boundaries, unsupported API fallback, cleanup during Clip study/page transitions, and the single fullscreen action across desktop/mobile sizes. CI cannot display or verify the actual iPhone system player, so physical-device fullscreen/touch testing remains a limitation.

## Clip study

Select an individual clip, then use the magnifier **Study clip** action beside Like for focused slow playback,
precise time seeking, looping and zoom/pan. Published processor observations appear
when available. Prepare a clip or small selected-frame package with a coaching
prompt for manual use in your AI chat. No footage uploads automatically.
See [Clip study design and local validation](docs/CLIP-STUDY.md).

### Saved players

Saved players (name, initial avatar, optional height in cm and level) live in
`website.sqlite3`, alongside other shared website preferences. They never modify
published recording metadata or media. There is no account/login relationship.

- Library **Saved players** creates and edits profiles. Session options →
  **Players** assigns a roster to every recording atomically. **Players** above
  a review player edits only that recording's roster.
- Select a clip, then **Main player** to choose/add its focus player. A recording
  with exactly one roster player supplies a default; multiple roster players
  never imply which visible person is the main player. Explicit clip selections
  (including Unassigned) override the default. Clip main players are included in
  the recording/session's displayed players, even if not in its explicit roster.
- Library and review filters narrow sessions/clips by player. Clip navigation and
  autoplay follow the filtered clip list; existing playback continues on a filter change.
- Assignments use source ID and original millisecond interval, not generated clip
  filenames. Exact intervals survive reprocessing with new clip IDs. A changed
  cut overlapping an explicit assignment requires confirmation; historical
  assignments remain in app state. They are never remapped by overlap alone.
- AI requests freeze a local player ID/profile version and height/level snapshot.
  Only anonymous height/level enter the generated AI prompt. Names and local
  player IDs are excluded. A profile cannot identify a person visually; crop to
  the intended player and review uncertainty. Existing annotations/export retain
  the historical player link. Changing profile/main player requires a new review,
  rather than reusing a follow-up with mismatched context. Timed player-specific
  cues do not play for a different currently assigned player.

API: `GET/POST /api/players`, `PUT /api/players/<id>`,
`PUT /api/sessions/<source>/players` (`player_ids`, `version`),
`PUT /api/practices/<group>/players` (`player_ids`, per-recording `versions`),
`PUT /api/sessions/<source>/clips/<clip>/player`
(`player_id` or null, `publication`, `version`). JSON mutations use the existing
same-origin/header checks and return 409 for stale shared-state writes. Player
learning journeys and cross-recording coaching dashboards remain future work.

## Clip thumbnails and selection

Each clip uses its own cached JPEG thumbnail in the watch list, liked clips, and player poster. The runtime image includes FFmpeg; thumbnails are generated lazily at 320px and stored under `TENNIS_DATA_ROOT/clip-posters`. Published media remains read-only. Cache keys include the clip, publication, and source-file version; a shared generation lock limits NAS CPU use. HDR posters are tone-mapped to SDR.

Use **Select** in the watch list to choose clips and **Assign main player** to update them together. Selection follows the visible filters. Bulk updates check every clip version and save atomically, so a conflict cannot partially update the selection. Session-card player avatars appear below the location; recording-player editing is under **Edit details** on the watch page. Saved-player and selection dialogs retain full names.

### Moments, notes and cut feedback

Watch pages offer **Highlight moment**, **Less useful**, and **Moments & notes**.
Notes can refer to a moment, interval, clip or whole original recording. Timeline
markers and list jumps use current playback positions; saved targets always use
original-video time. **Clip focus** shows highlights or hides less-useful clips.
Archive/restore and revision history keep edits reversible. Hearts remain separate.

Use **More video actions → Review cuts** for processing-quality reports, including
early/late boundaries and downtime. These are tied to the reviewed publication;
no processing job or training label is generated automatically. Changed cuts and
removed moments remain visible as historical/unavailable metadata.

The [annotation design and API](docs/ANNOTATION-METADATA.md) describes storage,
original-clock projection, provenance and the boundary to future training datasets.
`annotations.sqlite3` is permanent website app data, not a processing output or
TTL-managed preview. Existing coaching notes appear through the common metadata
API while retaining their authoritative coaching database, editor and evidence.

### Learning and clip filters

**Learning** in the main navigation lists clips with saved coaching reviews for a
selected player. Review-time player links remain authoritative; unlabelled older
reviews appear only through an assigned current main player and are labelled as
such. Reprocessed or unavailable media keeps its saved review with a clear notice.
No new AI request is generated.

The clip list uses one content filter: All clips, Liked only, Highlights, or Hide
less useful. Player and duration controls remain independent. **Select clips** is
inside the clip list's three-dot menu. Point highlights are unique per original
source and millisecond; repeated clicks reuse the saved highlight, and conflicting
retarget/restore edits return a conflict instead of creating duplicates. Existing
duplicate point marks are archived with their captions and revision history
retained; a captioned mark is preferred as the active highlight.

## Entity model

See [serving entities and processing annotations](docs/ENTITY-MODEL.md) for Session,
Video/version, Clip, Moment, metadata ownership, reprocessing and compatibility APIs.
