# Coaching excerpts and a separate AI worker

Implements the first local version of issues #28, #29, #31 and #32. Practice plans
(#30) remain P2. Nothing is deployed or automatically enabled in an existing NAS
container. The original tennis-processing service and worker are untouched.

## User flow

Open **Study & AI Coach**, adjust the **Interval start** and **Interval end** pins on the
study progress bar (0.1–30 seconds for coaching), or click **Auto-frame motion**
next to the zoom slider to locally scan and suggest up to 5 player-motion
windows (cycling locally via **Prev** / `1 of N` / **Next**), adjust framing/pan (or reset cleanly via **Full clip** / **Reset framing**), and choose
playback speed (`0.1×`–`1×`). Model-neutral UI copy refers to **AI Coach**
rather than naming a specific provider; actual provider/model routing is
managed in `/processing`.

In the **Ask a question** composer inside **AI Coach** (auto-collapsed once completed reviews exist), confirm or change **Player to coach** via **Change player** (auto-opened only when player review is needed or customized, without mutating clip or recording rosters; see
[COACHING-SUBJECT.md](COACHING-SUBJECT.md)), optionally add a concise visible target
cue, pick a preset question (or open **Edit question** to type a custom question), and click **Ask AI Coach**. The
browser captures an immutable snapshot of the selected player context, interval,
normalized crop, playback speed and prompt, and persists an application-level request coordinator (`sessionStorage`) so progress continues cleanly in the background across modal close, clip switches, and reloads:

1. **Preparing clip (`render`)** — queues a `render` job (`intent: "coaching"`) that
   renders the cropped, speed-adjusted SDR H.264 preview (`max_edge: 960`, muted)
   and extracts up to 32 chronological evidence frames.
2. **AI Coach review (`ai`)** — once the coaching excerpt completes, automatically
   submits the `ai` request (even if Study was closed) using the dedicated Mac/Windows AI worker
   (`coaching-annotations-v1`) and its locally configured provider(s) (`antigravity`,
   `codex` / ChatGPT, and/or `gemini`, optionally steered by the Processing Room
   **Default model** setting). Completed reviews show a clear 2-step progress transition while running, and remain atomically cancellable until the worker enters `calling` (after which cancellation is disabled truthfully). Failed pre-handoff/submit requests support idempotent **Retry** with the same `request_id`. This is **not native video analysis**; frame density is bounded and can miss events between frames.
3. **Review response** — puts suggestions and active progress first, showing the AI Coach summary, compact **New tip** / **Saved to Moments** cards, primary clickable key frame thumbnails (`View key frame`), **Loop moment**, **Save to Moments** (or **Use for current video** when a recording was reprocessed), and a **More actions** menu holding **Play walkthrough**, **Dismiss suggestion**, and **Edit suggestion or timing**. Secondary per-review tools (**Question asked**, **All submitted snapshots**, **Add your own suggestion**, **Original AI response**, and **Delete review**) are grouped inside **Review details**, and **Export coaching data (JSON)** exports full session annotations/revision history. Accepted coaching Moments display during their playback-time interval (5-second playback-time span from start for point cues,
   bounded by the clip) without wall-clock expiry timers, and clicking **Review**
   on a coaching Moment or Learning item deep-links directly into Clip study with
   the target interval and card highlighted. Coaching requests and cards record
   immutable `asked_at` timestamps so edits or status changes do not reorder
   historical questions. Saved coaching reviews require an explicit inline confirmation (`Confirm delete` / `Cancel`) before deleting from
   Clip Study (`Review details` → `Delete review`), Moments (`⋯` → `Delete review`), or the Learning
   room (`Delete review`).

### High-quality interval download (`intent: "share"`)

Clicking **Prepare video download** in Clip study queues a separate render profile
(`intent: "share"`, `quality: "high"`, `required_capability: "share-export-v2"`,
`renderer_version: 3`), persisting a durable frozen-selection card (**Cancel download** while queued/working, **Save video** once ready) across selection edits, modal close, and reloads without overwriting newer selection exports:

- Preserves the selected `[start, end]` interval, normalized zoom/pan crop, and
  playback speed at high spatial quality (`max_edge: 1920` without upscaling,
  `crf: 18`, up to 60 fps when the source clip is ≥45 fps, otherwise 30 fps).
- Probes authoritative published input stream properties (`codec`, `pix_fmt`,
  `colorspace`, `primaries`, `transfer`, `fps`, `has_audio`) before encoding:
  - Verified 10-bit BT.2020 HDR clips (`yuv420p10le`/`p010le`, `bt2020` primaries,
    `bt2020nc`/`bt2020c` matrix, and `arib-std-b67` HLG or `smpte2084` PQ transfer)
    are encoded as 10-bit HEVC (`libx265`, `yuv420p10le`, `hvc1`, explicit VUI and
    container color tags), keeping HLG (`hdr10-hlg-bt2020`) and PQ
    (`hdr10-pq-bt2020`) distinct.
  - Verified SDR clips are encoded as 8-bit H.264 (`libx264`, `yuv420p`, explicit
    `bt709` VUI and container tags, `sdr-bt709`).
  - Clips flagged as HDR or carrying partial/unsupported HDR stream tags without
    complete 10-bit BT.2020 HLG/PQ metadata fail deterministically rather than
    guessing or silently stripping transfer metadata.
- Retimes source audio when present using chained FFmpeg `atempo` filters (covering
  `0.1×`–`1×` speeds) rather than inheriting the silent `-an` coaching preview path;
  falls back cleanly to video-only when the source clip has no audio stream.
- Keeps the in-browser Clip study preview separate from the downloadable file (10-bit
  HEVC downloads target compatible media players/devices), and keeps coaching renders
  (`intent: "coaching"`) on the 30 fps 8-bit SDR H.264 path with linear-light Mobius
  tone-mapping (`zscale` + `tonemap=mobius` after trim/crop/scale).
- Skips coaching frame extraction (`frame-*.jpg`) and `prompt.txt` generation,
  publishing only `excerpt.mp4` and `recipe.json` (including `color_output`,
  `video_codec`, `pix_fmt`, `fps_output`, and `audio_output`) to shared storage.
  Share exports cannot be submitted to `/api/coaching/requests` as coaching evidence.
- Requires render workers advertising `share-export-v2` so older `share-export-v1`
  renderers cannot claim HDR-preserving share jobs, while upgraded render workers
  advertise both `share-export-v2` and `share-export-v1` for queue compatibility.

Export tests encode synthetic 60 fps SDR (`bt709`), 10-bit HLG (`arib-std-b67`), and
10-bit PQ (`smpte2084`) fixtures with audio, verify input and output stream tags,
bit depth, frame rate, crop, duration, coaching SDR tone-mapping, and deterministic
failure on incomplete HDR metadata. These checks verify encoding and metadata
preservation; they are not a display-reference color calibration.

## Placement

```text
Courtside (NAS container)
  ├── website.sqlite3       courts/profiles and legacy favorites
  ├── annotations.sqlite3   Moments, entity metadata and coordinated reactions
  ├── coaching.sqlite3      dedicated render + AI queues, worker status, default AI model setting, response history
  └── /coaching-media       separately configured shared artifact directory
          ↑ files (declared artifacts copied once after local render)
Mac or Windows host
  ├── media export process  claims render queue only, FFmpeg, no AI credentials
  └── AI coaching process   claims AI queue only, local AI provider(s), no video encoding
```

These are independent installable worker processes with independent identities,
local journals, status and lifecycle. Neither can consume detector/processing jobs.
The queue is hosted by the website backend in a **separate SQLite database**; a
separate physical machine or broker is not required for this initial scale.
SQLite remains on the NAS/container's local state mount, not opened by workers via
SMB. Workers use JSON APIs for metadata and mounted storage for large files.

The `/processing` Processing Room groups live status into two worker shelves:
1. **Video processing** — status and queue progress from `/api/status`.
2. **Coaching & media export** — status cards for registered `ai` coaching workers and `render` media-export workers from `/api/coaching/workers`, including a **Default model** selector on the **AI coach** card. Selecting a provider/model updates `coaching.sqlite3` optimistically (`PUT /api/coaching/config`), while provider tokens, API keys, CLI binaries, and local paths remain strictly on the worker host. AI workers receive the latest `config_version` via `claim` and `heartbeat` responses and fetch updates through `GET /api/coaching/worker/config` (and acknowledge via `POST /api/coaching/worker/config`) without restarting.

The backend can be split out later using the same `/api/coaching` contract.
Video/excerpt rendering remains separate from AI inference. The worker package is
under `worker/`, copied by pip on install; it does not require an IDE or execution
from a checkout. Private deployment configuration is outside Git.

## API v1 (initial)

All mutations use JSON and `X-Tennis-Request: 1`; browser mutations retain the
existing same-origin check. Worker endpoints additionally require a random bearer
token, configured separately from any provider login.

- `GET /api/coaching/workers` — registered coaching worker shelf status (`ai` and
  `render`), active/queued job counts, and current default AI model configuration
  with selectable options (including safe model IDs advertised by online AI workers).
- `GET /api/coaching/config` / `PUT /api/coaching/config` — read or update the shared
  default AI provider/model (`{selection, version}`) with optimistic concurrency (`409`
  with `current` state on stale writes).
- `GET /api/coaching/sessions/{source_id}` — history plus recent worker status.
- `POST /api/coaching/exports` — immutable selection, publication, crop normalized
  to the oriented video, speed, quality, caller request ID. Returns render job.
- `POST /api/coaching/requests` — completed export ID, prompt, explicit consent,
  caller request ID. Returns AI job. Rejects a changed source publication.
- `GET /api/coaching/jobs/{id}` — public job without lease credentials.
- `PUT /api/coaching/jobs/{id}` — desired cancellation or reviewed cue with current
  version; stale writes return conflict. A started provider call is not cancelled.
- `DELETE /api/coaching/jobs/{id}` — completely delete a non-active (`queued`,
  `completed`, `failed`, `cancelled`, or `needs_review`) `ai` coaching review, its
  linked `annotations`, `annotation_revisions`, and `evidence` rows, orphaned
  coaching `render` artifacts on disk, and unreferenced evidence frame images
  (`409` while `leased` or `calling`).
- `GET /api/coaching/exports/{id}/{excerpt.mp4|prompt.txt|recipe.json}` — browser
  preview/download; expired files return 410. Recipe response strips credentials.
- `GET|POST /api/coaching/worker/config` — worker-authenticated endpoint returning
  `ai_config` (`{selection, provider, model, version, updated_at}`); `POST` also
  acknowledges refreshed worker metadata (`config_version`, active providers).
- `POST /api/coaching/worker/{render|ai}/claim` — role-specific fenced lease
  (also accepts sanitized worker `meta` and returns `config_version`).
- `POST /api/coaching/worker/jobs/{id}/{heartbeat|calling|complete|fail}` — fenced
  updates (also accepts sanitized `meta` and returns `config_version`). Completion
  can be acknowledged again without repeating inference.

Request IDs are idempotent within their original payload; reuse for a different
payload conflicts. Export attempts use distinct artifact directories derived from
the lease, so a stale renderer cannot replace a completed export's media.

## Recovery and retention

Render queue: queued → leased → completed/failed/cancelled. AI queue additionally
marks the provider boundary as calling. Lease expiration requeues work before that
boundary; expiration after it produces needs_review without another provider call.
A provider call can succeed despite a lost response; exactly-once inference cannot
be promised. Check local result journals before explicitly asking again.

Metadata and advice are anchored to source identity, run/publication, reviewed clip
and original interval. Timing offsets are approximate: source and encoded clip
boundaries can differ slightly. Compression, orientation, audio, crop, speed and
renderer version are retained in the recipe.

Automatic TTL/pinning and local-journal cleanup are not implemented yet. No files
are deleted automatically. Removing an old export's media must not remove its
recipe/response/cue. Future cleanup must skip active/pinned artifacts.

## Setup and validation

Set `TENNIS_COACH_WORKER_TOKEN` and `TENNIS_COACH_MEDIA_ROOT` in the website
container; mount coaching media separately from library and application state.
The library stays read-only. No provider token or account login belongs in NAS
compose. Worker setup and separate macOS service commands: `worker/README.md`.

Executed locally: 59 Python tests, real FFmpeg crop/rate/frame export using synthetic
footage, pip-installed worker execution, real ChatGPT-authenticated synthetic-frame
request (~8 seconds), stored response, cue edit and timed playback display in the
browser. No private tennis media was uploaded for this test. Physical iPhone,
Windows service installation, HDR conversion on actual tennis footage and NAS
container deployment remain release/device checks. Existing Chromium/WebKit CI was
not run for this unpushed branch.

## Evidence-linked annotations and lazy pose enrichment

The four-step coaching flow, structured response schema (`SCHEMA_VERSION = 3`, backward-compatible with `2`), immutable snapshots,
review/version rules, and reprocessing behavior are documented in
[COACHING-ANNOTATIONS.md](COACHING-ANNOTATIONS.md) and [COACHING-INSPECTION-DESIGN.md](COACHING-INSPECTION-DESIGN.md). Optional render-worker pose extraction requires coaching worker `0.6.0` (`pip install ".[pose]"`), while base `0.6.0` and older `0.2.0+` workers continue operating safely with text/frame fallback (`GET /api/coaching/evidence/{id}/pose` serves retained pose lazily while `pose_artifact` is stripped from queue/job polling responses and cleaned up with `DELETE /api/coaching/jobs/{id}`). Prepared-export expiration must not remove retained evidence or pose enrichment.
