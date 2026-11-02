# Coaching excerpts and a separate AI worker

Implements the first local version of issues #28, #29, #31 and #32. Practice plans
(#30) remain P2. Nothing is deployed or automatically enabled in an existing NAS
container. The original tennis-processing service and worker are untouched.

## User flow

Open **Study clip → Ask a coach**, mark a start/end (0.1–30 seconds), set a fixed
focus with existing zoom/pan and select playback speed. **Prepare coaching clip**
queues a media-only export. Preview the resulting muted SDR H.264 file, with the
crop and speed baked in. Balanced limits the longest edge to 960 pixels; Smaller
uses 640 pixels and higher compression. No upscaling; no exact byte-size promise.
A 30-second selection at 0.1× is a five-minute exported video.

Choose either path:

- **Ask coach · send frames to OpenAI** sends up to 32 timestamped stills and the
  question through a dedicated Mac AI worker using its ChatGPT/Codex sign-in. It
  saves the answer and analysis mode automatically. This is **not native video
  analysis**. Frame density is bounded and can miss events between frames.
- Download the prepared video, copy the question plus timing and open regular
  ChatGPT. Attach the video yourself. The media worker also keeps an excerpt,
  prompt and metadata in its local task folder; `--package JOB_ID --role render`
  prints that folder. No supported automatic local-video attachment/deep link has
  been established. An MCP pickup/response tool remains a separate investigation;
  no tunnel, custom connector or external inbound access is configured.

Review the saved answer and write a short playback cue. Cues are shared app data;
there are no inferred per-visitor AI accounts. A cue appears during the matching
clip or combined-practice interval, and can be read without interrupting playback.
Old publications retain their history but do not apply cues to new cuts silently.
Regular ChatGPT answers are not imported automatically in this first version.

## Placement

```text
Courtside (NAS container)
  ├── website.sqlite3       existing likes/courts, unchanged
  ├── coaching.sqlite3      dedicated render + AI queues, response history
  └── /coaching-media       separately configured shared artifact directory
          ↑ files (declared artifacts copied once after local render)
Mac or Windows host
  ├── media export process  claims render queue only, FFmpeg, no AI credentials
  └── AI coaching process   claims AI queue only, Codex, no video encoding
```

These are independent installable worker processes with independent identities,
local journals, status and lifecycle. Neither can consume detector/processing jobs.
The queue is hosted by the website backend in a **separate SQLite database**; a
separate physical machine or broker is not required for this initial scale.
SQLite remains on the NAS/container's local state mount, not opened by workers via
SMB. Workers use JSON APIs for metadata and mounted storage for large files.

The backend can be split out later using the same `/api/coaching` contract.
Video/excerpt rendering remains separate from AI inference. The worker package is
under `worker/`, copied by pip on install; it does not require an IDE or execution
from a checkout. Private deployment configuration is outside Git.

## API v1 (initial)

All mutations use JSON and `X-Tennis-Request: 1`; browser mutations retain the
existing same-origin check. Worker endpoints additionally require a random bearer
token, configured separately from any provider login.

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
- `POST /api/coaching/worker/{render|ai}/claim` — role-specific fenced lease.
- `POST /api/coaching/worker/jobs/{id}/{heartbeat|calling|complete|fail}` — fenced
  updates. Completion can be acknowledged again without repeating inference.

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
