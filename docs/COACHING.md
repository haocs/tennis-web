# Coaching excerpts and a separate AI worker

Implements the first local version of issues #28, #29, #31 and #32. Practice plans
(#30) remain P2. Nothing is deployed or automatically enabled in an existing NAS
container. The original tennis-processing service and worker are untouched.

## User flow

Open **Study clip**, adjust the **Interval start** and **Interval end** pins on the
study progress bar (0.1–30 seconds for coaching), set framing with the zoom scrubber
and drag-to-pan, and choose playback speed (`0.1×`–`1×`).

In **Ask a coach**, confirm or change **Player to coach** (defaults to the clip's
assigned main player without mutating clip or recording rosters; see
[COACHING-SUBJECT.md](COACHING-SUBJECT.md)), optionally add a concise visible target
cue, pick a preset question or type a custom question, and click **Ask coach** (or
**Auto detect & ask** for up to two local-detector moments per clip; see
[AUTO-COACH.md](AUTO-COACH.md)). The browser captures an immutable snapshot of the
selected player context, interval, normalized crop, playback speed and prompt:

1. **Preparing your clip…** — queues a `render` job (`intent: "coaching"`) that
   renders the cropped, speed-adjusted SDR H.264 preview (`max_edge: 960`, muted)
   and extracts up to 32 chronological evidence frames.
2. **Getting response…** — once the coaching excerpt completes, automatically
   submits the `ai` request using the dedicated Mac/Windows AI worker
   (`coaching-annotations-v1`) and its locally configured provider(s) (`antigravity`,
   `codex` / ChatGPT, and/or `gemini`, optionally steered by the Processing Room
   **Default model** setting). This is **not native video analysis**; frame density is
   bounded and can miss events between frames.
3. **Review response** — displays the AI coach summary, structured suggestions, and
   clickable original-recording evidence stills. New AI suggestions remain
   `proposed` until explicitly accepted via **Use in playback** (or **Use for
   current video** when a recording was reprocessed), edited and saved via **Save**
   (with **Reset edits** to abandon unsaved form changes), or dismissed via the
   top-right **×** discard button. Accepted coaching Moments display a 5-second
   playback teaser in the Watch player, and clicking **Review** on a coaching
   Moment or Learning item deep-links directly into Clip study with the target
   interval and card highlighted. Coaching requests and cards record immutable
   `asked_at` timestamps so edits or status changes do not reorder historical
   questions.

### High-quality interval download (`intent: "share"`)

Clicking **Download interval** in Clip study queues a separate render profile
(`intent: "share"`, `quality: "high"`, `required_capability: "share-export-v2"`,
`renderer_version: 3`):

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

## Evidence-linked annotations

The new four-step coaching flow, structured response schema, immutable snapshots,
review/version rules and reprocessing behavior are documented in
[COACHING-ANNOTATIONS.md](COACHING-ANNOTATIONS.md). Rollout requires coaching worker
0.2.0 as well as the updated website; an old AI worker will safely leave new requests
queued. Prepared-export expiration must not remove retained evidence.
