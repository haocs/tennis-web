# Evidence-linked coaching annotations (#38)

The coaching panel has four steps: **Choose moment → Preview → Ask coach → Review suggestions**. Preparing an excerpt is local to the worker/shared storage. Sending to ChatGPT is a separate explicit action. The current adapter reviews chronological stills, not continuous video. A draft question, selection, preparation/request IDs and current step survive reopening/reloading in the same browser tab. A prepared crop/speed is fixed; changing the player afterward does not change the already prepared media.

Each response has a summary and separate suggestions containing a title/topic, observation, cue, drill, uncertainty, evidence frame IDs and applicable intervals in **original-recording seconds**. A suggestion may be untimed. Evidence points and applicable cue intervals are different: a cited still does not imply a continuous action interval. Source timestamps remain approximate because the source clips were encoded; the retained images themselves are the exact attachments.

## Storage and identity

- `coaching.sqlite3`, under website data: immutable request/response, evidence manifest, immutable AI proposal, current human-reviewed content and append-only revisions. It is independent of the read-only published library and of the video-processing queue.
- Coaching-media storage: temporary `<render-job>/<attempt>/excerpt.mp4`, prompt and render manifest; retained `evidence/frames/<image-sha256>.jpg` snapshots. Identical bytes are stored once, including across requests. No snapshot re-export is performed on the NAS.
- Worker-local journal: job/attempt, original raw answer, submitted prompt, response schema, evidence manifest and exact attachments. Installation remains independent of an IDE.
- An annotation ID is stable across edits. Its source identity/hash and original times are canonical. Clip ID, publication, render/AI request and processing metadata are historical provenance, never reassigned to a new cut.

The worker freezes and verifies the attachment order, image checksums and three clocks: original recording, clip-local and prepared-excerpt playback. It pins the snapshots to shared evidence storage **before** crossing the non-retryable provider-call boundary. The server verifies the small manifest and image checksums before registering evidence. A lost completion acknowledgement is idempotent and cannot create duplicate proposals or change the raw answer.

Every evidence manifest is a retention pin, including failed/interrupted calls whose outcome is uncertain. **Do not apply a preview/export TTL to `evidence/` or delete files referenced by the `evidence` table.** There is currently no automatic evidence garbage collector. A future collector must trace manifest references and handle backup/retention explicitly. Back up coaching.sqlite3 together with evidence storage. The container only needs read access to coaching-media; the separate worker writes it.

## Review, validation and reprocessing

`proposed → accepted / dismissed` is a human review, with optimistic version checks. Edits are stored separately from the immutable proposal. Invalid model references/ranges are removed from the usable proposal with visible warnings; the original output is retained. Warnings require an explicit correction before acceptance. A malformed response remains a recoverable unstructured response with its snapshots; users can add a separate suggestion marked as human-authored. Unknown timing stays untimed and produces no automatic cue.

After reprocessing, annotations are linked to overlapping current clips by original time and shown as historical. Playback cues require an explicit acceptance for the **current publication**. Reprocessing never transfers an old judgment silently. Evidence from an interval not retained in current clips remains inspectable, with the historical timestamp; the UI does not seek an unrelated moment. Legacy freeform notes remain labeled unstructured, with no guessed frame associations.

All visitors share reviews. A stale review returns HTTP 409; the browser keeps unsaved edits. Revision actors distinguish AI proposals from human actions, not authenticated user identities. Technique coaching is separate from cut accuracy. Every export carries `training_label_status: not_released`: accepting advice is not ground truth or authorization to train a model.

## Worker contract

AI worker 0.2.0 advertises `coaching-annotations-v1` in `POST /api/coaching/worker/ai/claim`. The server skips structured requests for workers that do not advertise this capability. Upgraded workers can still process legacy requests.

New AI request bodies freeze `evidence_frames`, `submitted_prompt`, `response_contract`, `prompt_version`, source/hash, selection and processing provenance. Worker sequence:

1. Verify render package, attachments and ChatGPT sign-in locally.
2. Copy only missing/checksum-mismatched evidence assets; reuse identical snapshots.
3. `POST /worker/jobs/{id}/evidence` with lease token and deterministic manifest.
4. `POST /worker/jobs/{id}/calling`, then Codex `exec --output-schema ...` with the exact ordered images. The worker removes API-key environment variables and disables tools as before.
5. Complete with the raw answer and bounded provider metadata; the server validates the response and creates proposals transactionally. No model output is trusted as an arbitrary path or timestamp.

The configured model/default choice and available usage/timing are retained. A subscription's default can change; configure a model explicitly when doing reproducible comparisons. This feature adds no training or recommendation ranking.

Response schema 2 (all fields required; empty topic/drill/uncertainty allowed):

```json
{
  "schema_version": 2,
  "summary": "One observable improvement",
  "suggestions": [{
    "title": "Ready position",
    "topic": "Recovery",
    "observation": "Describe only what is visible",
    "cue": "A short playback cue",
    "drill": "A simple drill",
    "uncertainty": "What these stills cannot establish",
    "evidence_frame_ids": ["f_<immutable-id>"],
    "applies_to": [{"start_seconds": 316.5, "end_seconds": 318.0}]
  }]
}
```

Maximums: six suggestions, 48 evidence references and 12 intervals per suggestion. Text limits and finite clock validation are authoritative in the shared pure-Python contract, `worker/tennis_coach/annotations.py`. The Docker image includes that contract only, not the media/AI execution worker.

## Website API additions

All mutation requests retain the same-origin, JSON and `X-Tennis-Request: 1` requirements. Worker endpoints additionally require the existing bearer credential.

- `GET /api/coaching/sessions/{source}`: jobs, workers/capabilities and durable annotations with current-clip links/playback eligibility. Polling omits the redundant full submitted prompt/schema; the export retains them.
- `GET /api/coaching/evidence/{job}`: immutable submitted manifest.
- `GET /api/coaching/evidence/{job}/{frame_id}.jpg`: only a frame declared in that job's manifest; no arbitrary path lookup.
- `PUT /api/coaching/annotations/{id}`: `{version, publication, status, content?}`. Status is `accepted`, `dismissed` or `proposed`. Content uses suggestion fields; validated original-clock intervals are optional.
- `POST /api/coaching/annotations`: `{job_id, publication, content, request_id}`. Idempotent creation of a human-authored recovery/additional suggestion for a completed evidenced response.
- `GET /api/coaching/annotations/export/{source}?after={cursor}`: versioned JSON containing responses (including unstructured/legacy/empty results), annotations, provenance, evidence manifests and revisions. At most 100 requests per page; follow `next_after` until null. The UI export follows every page and downloads a single JSON file. Snapshot binaries stay deduplicated in storage and are retrievable through frame URLs.

## Rollout and verification

Rollout must update both the Docker website and independent coaching worker package. An old worker safely leaves new requests queued. Existing notes, likes, courts, originals, processing tasks and published media are retained. The SQLite migration is serialized for concurrent application startup.

Local checks cover checksums, capability gating, frame-clock bounds, lease fencing, exact-image preflight, raw-output recovery, append-only revisions, concurrent conflicts, migration, preview expiry and publication replacement. Synthetic browser previews exercise desktop/mobile flow, seeking and accepted cues without sending private footage or consuming provider quota. Chromium/WebKit regression cases are included for the project CI; running that CI requires a later push/review action.
