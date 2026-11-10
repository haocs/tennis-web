# Player learning plans and incremental practice tracking (#85)

Public-safe design for [issue #85](https://github.com/haocs/tennis-web/issues/85):
compact coaching reviews, per-player AI ability & practice plans, manual practice
progress logs, and incremental evidence-bounded updates reusing the existing
coaching AI worker.

---

## 1. UX & Information Architecture (`/learning`)

When a player is selected on `/learning`, the page provides two accessible tabs
(`role="tablist"`, URL/state-preserved) beneath the player selector:

1. **Reviews tab (`Reviews`)** — Compact coaching review cards:
   - Ordered by newest `asked_at` (with deterministic fallback to `updated_at` and
     stable ID tie-breaks).
   - Each card shows a compact header (clip title/number, recording date, original
     time span, poster thumbnail link when media is available), the 1–2 sentence
     summary or top accepted/proposed cue chips, an expandable `<details>` section
     for full suggestion details and full saved response text, and a direct
     **Study & review coaching** deep-link (`/review/<source_id>?clip=<clip_id>&coach_time=<start>&coach_end=<end>&coach_job=<job_id>`).
   - Cards remain anchored to `source_id` and original-recording timestamps
     (`[original_start, original_end]`), surviving video reprocessing and showing
     truthful badges when a review comes from an earlier processed version or has a
     reassigned clip player.

2. **Plan & progress tab (`Plan`)** — Structured ability summary, practice plan,
   next-video evidence requests, and manual practice log:
   - **Freshness & cost banner**:
     - Shows current plan status (`empty`, `up_to_date`, `eligible`, `queued`,
       `working`, `calling`, `needs_review`, `failed`), when the active plan was
       generated, how many new or revised coaching reviews/suggestions or practice
       log updates have occurred since the active plan's snapshot, and an explicit
       **Generate learning plan** / **Update learning plan** button (plus a
       **Confirm & retry** action only when an earlier job is in `needs_review` or
       `failed`).
     - **No automatic or background AI calls**: visiting `/learning`, switching
       players, or editing a practice log never triggers an AI call automatically.
     - **No-op suppression**: when the deterministic `input_digest` of current
       eligible inputs matches the active plan's `input_digest` (no new/revised/deleted
       reviews, no profile change, and no practice-log change since the last plan),
       the Generate/Update button is disabled with a clear explanation (`Plan is up
       to date with current reviews and practice logs`).
   - **Structured plan sections** (explicitly separating epistemological categories):
     1. **Observed ability & status summary** (`observed_summary`): What is directly
        supported by reviewed clip evidence, with clickable citation pills linking
        to the cited coaching reviews/suggestions (`Review` deep-links into Clip
        Study).
     2. **Focus areas & practice drills** (`focus_areas`): Up to 5 prioritized
        practice items, each with `topic`, `title`, `observed_issue`, `cue`,
        `drill`, `uncertainty`, cited `evidence_ids`, and an inline **Practice
        progress control** (`not_started` | `ongoing` | `practiced` |
        `working_well` | `archived`, plus an optional user note).
     3. **Uncertainties & unassessed skills** (`uncertainties`, `unassessed_areas`):
        Explicitly lists what sampled stills and on-demand clip selection could not
        verify (e.g., ball contact, full-point tactical patterns, serve/return if
        absent from reviewed clips) so absence of coaching criticism is never
        represented as mastery.
     4. **Self-reported practice vs. observed progress** (`progress_notes`):
        Distinguishes the player's self-reported practice log statuses from
        subsequent video evidence.
     5. **Next practice video requests** (`next_evidence_requests`): Concrete
        recording suggestions for the next session (camera angle, stroke/situation
        to capture) to resolve open uncertainties or check whether a practiced cue
        is transferring.
   - **Manual practice items & revision history**:
     - Players can also add standalone manual practice focus items or update the
       status/note of any plan focus item at any time without calling AI.
     - Previous plan revisions remain browsable in a collapsible **Plan history**
       disclosure showing each revision's timestamp, coverage summary, and delta.

---

## 2. Storage & Same-Database Atomic Persistence (`coaching.sqlite3`)

All learning-plan state and practice logs live in `coaching.sqlite3` alongside
`jobs`, `annotations`, and `evidence` so that AI job completion and plan persistence
execute inside the **same SQLite `BEGIN IMMEDIATE` transaction**—eliminating any
cross-database crash window where a worker completion could be accepted without
persisting the resulting plan.

### Schema additions in `coaching.sqlite3` (backward-compatible migration)

```sql
CREATE TABLE IF NOT EXISTS learning_plans(
  player_id TEXT PRIMARY KEY,
  active_revision INTEGER NOT NULL,
  active_job_id TEXT,
  input_digest TEXT NOT NULL,
  plan_json TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_plan_revisions(
  player_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  job_id TEXT NOT NULL UNIQUE,
  parent_revision INTEGER,
  input_digest TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  plan_json TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(player_id, revision)
);

CREATE TABLE IF NOT EXISTS learning_practice_logs(
  id TEXT PRIMARY KEY,
  player_id TEXT NOT NULL,
  item_key TEXT NOT NULL,
  title TEXT NOT NULL,
  topic TEXT NOT NULL,
  cue TEXT NOT NULL,
  drill TEXT NOT NULL,
  status TEXT NOT NULL,
  note TEXT NOT NULL,
  evidence_ids_json TEXT NOT NULL,
  origin_revision INTEGER,
  version INTEGER NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(player_id, item_key)
);
CREATE INDEX IF NOT EXISTS learning_practice_logs_player
  ON learning_practice_logs(player_id, updated_at DESC);
```

- All tables are serving metadata (`training_label_status: "not_released"`), kept
  strictly separate from `annotations.sqlite3` processing annotations (`cut_feedback`,
  `processing_label`) and detector training labels.

---

## 3. Generalized AI Worker Contract & Purpose Dispatch

Instead of introducing a new daemon, learning-plan synthesis reuses the existing
`role = 'ai'` worker process in `worker/tennis_coach/worker.py` with explicit
capability gating and `purpose` dispatch so legacy coaching jobs and text-only
learning-plan jobs never collide:

### Capability & Purpose Fencing

- **Constants in `worker/tennis_coach/annotations.py`**:
  - Existing frame-sequence coaching capability: `CAPABILITY = 'coaching-annotations-v1'`
  - New learning-plan capability: `LEARNING_PLAN_CAPABILITY = 'learning-plan-v1'`
  - New prompt & schema version: `LEARNING_PLAN_PROMPT_VERSION = 'tennis-learning-plan-v1'`,
    `LEARNING_PLAN_SCHEMA_VERSION = 1`
- **Worker claim (`Worker.once()` in `worker/tennis_coach/worker.py`)**:
  - When `self.role == 'ai'`, advertises `capabilities = [CAPABILITY, LEARNING_PLAN_CAPABILITY]`.
  - An older AI worker advertising only `['coaching-annotations-v1']` will not claim
    `required_capability = 'learning-plan-v1'` jobs; they remain safely queued.
- **Job body contract (`purpose = 'learning_plan'`)**:
  - `purpose`: `'learning_plan'` (existing frame coaching jobs have `purpose` omitted
    or `'coaching_frames'`).
  - `player_id`: validated 32-hex player ID; `player_profile`: anonymous `{height_cm, level, profile_version}`
    (never sending player name, GPS, file paths, or NAS URLs).
  - `parent_revision`: integer revision of the player's active plan at submission time
    (or `None` for the initial plan).
  - `input_digest`: deterministic SHA-256 hex digest of the canonical input snapshot.
  - `snapshot`: bounded incremental evidence snapshot (see Section 4).
  - `submitted_prompt` and `response_contract` (`learning_plan_schema()`).
  - Does **not** set `source_id`, `clip_id`, `export_id`, or `evidence_frames`.

### Dispatch across every lifecycle boundary

1. **Worker `analyze(config, job, local, before_call)`**:
   - Branches on `job['body'].get('purpose') == 'learning_plan'`.
   - For `'learning_plan'`:
     - Does **not** read `export_id`, `artifact_directory`, or image files.
     - Verifies `codex login status` before calling `before_call(None)`.
     - Writes `submitted-prompt.txt` and `response-schema.json` (`learning_plan_schema()`)
       to the local job journal directory.
     - Runs `codex exec` in read-only ephemeral sandbox with all tool flags disabled,
       `OPENAI_API_KEY`/`CODEX_API_KEY` stripped, `--output-schema response-schema.json`,
       and **no `--image` arguments**.
     - Returns `dict(answer=answer, analysis_mode='learning_plan', model=..., elapsed_seconds=..., usage=...)`.
2. **Worker completion & route validation (`coaching_routes.py` & `Coaching.worker_update`)**:
   - In `coaching_routes.py` (`POST /api/coaching/worker/jobs/<identity>/<action>`):
     - For `purpose == 'learning_plan'`, `action == 'complete'` validates
       `result.get('analysis_mode') == 'learning_plan'` and `1 <= len(result['answer']) <= 64000`,
       and `action == 'evidence'` is rejected (`400`, text-only plans do not register image manifests).
   - In `Coaching.worker_update(identity, token, 'complete', result)`:
     - Branches on `body.get('purpose') == 'learning_plan'`:
       - Does **not** call `self.save_annotations(db, row, result)` (never creates
         fake clip annotations or Moments).
       - Calls `self.save_learning_plan(db, row, result)` inside the same `BEGIN IMMEDIATE`
         transaction:
         - Parses and normalizes the JSON response against the submitted snapshot's
           valid `evidence_ids` (stripping any hallucinated IDs with recorded
           `validation_warnings`).
         - Checks player ownership (`body['player_id']`) and supersession: always
           records the completed output in `learning_plan_revisions` (keyed by
           `(player_id, new_revision)`), and updates `learning_plans` as the active
           plan **only if** `learning_plans.active_revision == body.get('parent_revision')`
           (or no active plan exists yet). If a newer plan revision was already
           committed while this job was in flight, the late completion is retained in
           `learning_plan_revisions` for audit without overwriting the newer active plan.
         - Seeds/updates `learning_practice_logs` entries for the plan's `focus_areas`
           without clobbering existing user-edited status/notes.
3. **Isolation from clip/session queries (`coaching.py` & `annotation_routes.py`)**:
   - `Coaching.listing(source)` filters by `json_extract(body, '$.source_id') = ?`,
     which naturally excludes player-level learning-plan jobs (`source_id` is absent).
   - `coaches(source, data)` and `/api/learning` review queries in `annotation_routes.py`
     explicitly require `COALESCE(json_extract(body, '$.purpose'), 'coaching_frames') = 'coaching_frames'`
     and `json_extract(body, '$.source_id') IS NOT NULL`, ensuring learning-plan jobs
     are never projected as clip Moments or clip review cards.

---

## 4. Incremental Evidence Snapshot, Digest, and Edge Cases

To keep prompt cost bounded and avoid resending a player's entire history on every
update, each plan generation builds a deterministic **incremental snapshot**:

### Canonical Evidence Units

For a given `player_id`, eligible evidence comes from:
1. **Reviewed coaching annotations (`annotations` joined with `jobs`)** attributed to
   `player_id` (where the clip's current `main_player.id == player_id` or the job's
   `player_context.player_id == player_id` and the clip has not been reassigned to a
   different player):
   - Each annotation is identified by `(annotation_id, version, status, job_id, asked_at)`.
   - Accepted and proposed annotations are included with their `status` (`accepted`
     weighted as user-confirmed, `proposed` marked as unconfirmed AI suggestion);
     `dismissed` annotations are excluded from active evidence.
2. **Unstructured completed coaching responses (`jobs`)** for `player_id` that have no
   structured rows in `annotations`:
   - Identified by `(job_id, version, asked_at)`.
3. **Manual practice logs (`learning_practice_logs`)** for `player_id`:
   - Identified by `(item_key, version, status, note)`.

### Incremental Delta Construction (Prior Plan + Bounded Changes)

When a player has an active plan at revision $R$ with stored `snapshot`:
- **Prior plan baseline**: The active plan's structured JSON (`observed_summary`,
  `focus_areas`, `uncertainties`, `unassessed_areas`, `next_evidence_requests`) and
  its `coverage_cutoff` metadata are included as the compact baseline.
- **Delta computation against revision $R$'s snapshot**:
  - **Added / revised evidence**: Any eligible review annotation or response not
    present in $R$'s `included_evidence_map` (`{evidence_id: version_digest}`) or whose
    version/content/status changed since $R$.
  - **Removed / invalidated evidence**: Any `evidence_id` that was in $R$'s
    `included_evidence_map` but is now dismissed (`status == 'dismissed'`), or whose
    clip was reassigned to a different player (`player_mismatch`). These IDs are
    passed in `removed_evidence_ids` with reason (`dismissed` or `reassigned_player`)
    so the updated plan drops claims that depended solely on invalidated evidence.
  - **Practice log state**: Current active `learning_practice_logs` items (max 20)
    so the coach sees what the player practiced or found working well.
- **Bounded payload cap**:
  - At most **24 new/changed evidence items** (newest `asked_at` first, each capped
    to compact fields: `evidence_id`, `asked_at`, `source_id`, `original_start`,
    `original_end`, `review_status`, `title`, `topic`, `observation`, `cue`, `drill`,
    `uncertainty`) and **20 practice log items** are sent in a single request.
  - If more than 24 new/changed items exist, `snapshot.truncated_count` records the
    overflow count and the newest 24 are included.
  - `included_evidence_map` records the full map of currently valid `{evidence_id: version_digest}`
    covered by the new plan revision.

### Deterministic Input Digest & Eligibility / Cost Controls

- `input_digest = identity({player_profile, parent_revision, delta_evidence, removed_evidence_ids, practice_logs, prompt_version})`
- **In-flight & duplicate protection**:
  - Before creating a new `learning_plan` AI job for `player_id`, the server checks inside
    `BEGIN IMMEDIATE` whether an existing job for `player_id` (`purpose == 'learning_plan'`)
    is in `'queued'`, `'leased'`, `'calling'`, or `'needs_review'`:
    - If an in-flight job (`queued` / `leased` / `calling`) already has the same
      `request_id`, it is returned idempotently.
    - If another in-flight job is active for `player_id`, `POST /api/learning/plan`
      returns `409 Conflict` (`A learning plan request is already in progress for this player`).
    - If the latest job for `player_id` is in `needs_review` (lease expired after
      `calling`), the UI surfaces the `needs_review` warning and requires explicit
      `retry_ambiguous: true` to queue a replacement call.
  - If `input_digest == active_plan.input_digest`, `POST /api/learning/plan` returns
    `409 Conflict` (`Learning plan is already up to date with current reviews and practice logs`).
- **Freshness metadata (`GET /api/learning?player_id=<id>`)**:
  - Returns `plan_state` alongside `entries`:
    - `active_plan`: current active plan revision, structured content, cited evidence
      summaries (resolved with current `source_id`, `clip_id`, and `original_start`/`original_end`
      for one-click `Review` navigation), `created_at`, `input_digest`, and `coverage`.
    - `revisions`: compact list of prior plan revisions (`revision`, `created_at`,
      `coverage`, `summary`).
    - `practice_logs`: player's practice log items with `version` for conflict-safe editing.
    - `eligibility`: `{can_generate, reason, new_or_revised_reviews_count, removed_reviews_count, practice_log_changes_count, days_since_last_plan, recommended_cadence_days: 14, input_digest}`.
    - `pending_job`: any active or `needs_review`/`failed` learning-plan job for `player_id`.

---

## 5. API Endpoints

1. `GET /api/learning?player_id=<id>[&after=<cursor>&limit=<n>]`
   - Existing `entries` and `next_after` preserved for backward compatibility.
   - Adds `plan_state` (`active_plan`, `revisions`, `practice_logs`, `eligibility`,
     `pending_job`, `enabled`) when `after` is empty (initial load).
2. `POST /api/learning/plan`
   - Body: `{player_id, consent: true, request_id, expected_parent_revision, input_digest, retry_ambiguous?: bool}`
   - Validates player existence, explicit OpenAI/Codex consent, `COACH_WORKER_TOKEN`
     configuration, `expected_parent_revision` against `active_revision` (`409` on
     concurrent plan update), `input_digest` against current computed digest (`409` if
     inputs changed in another tab or if unchanged from active plan), and queues a
     `role='ai'`, `purpose='learning_plan'`, `required_capability='learning-plan-v1'`
     job.
3. `POST /api/learning/practice-logs` & `PUT /api/learning/practice-logs/<log_id>`
   - Conflict-safe creation and update (`expected` `version` check returning `409` on
     stale version) of per-player practice focus items (`status` in
     `{'not_started', 'ongoing', 'practiced', 'working_well', 'archived'}`, `note` up
     to 1000 chars, `title` up to 120 chars, `cue` up to 400 chars, `drill` up to
     1200 chars).

---

## 6. Test Plan

- **Backend unit & integration tests (`tests/test_learning.py`)**:
  - Capability gating (`learning-plan-v1` vs `coaching-annotations-v1`), purpose
    dispatch in `Worker.once()` / `analyze()` (mocked `codex` binary with zero image
    attachments and no real AI calls), and atomic completion persistence into
    `learning_plans` + `learning_plan_revisions` + `learning_practice_logs`.
  - Incremental delta snapshot construction: initial plan, subsequent update sending
    only prior plan + new/edited reviews + removed/dismissed/reassigned reviews +
    practice logs, and 24-item cap truncation.
  - No-op suppression when `input_digest` is unchanged, duplicate/in-flight job
    rejection (`409`), `needs_review` lease expiry recovery with `retry_ambiguous`,
    cross-player isolation, and stale completion supersession (late job recorded in
    `learning_plan_revisions` without overwriting a newer `active_revision`).
  - Conflict-safe practice log creation and editing (`409` on version mismatch).
  - Isolation check: learning-plan jobs never appear in `Coaching.listing(source)`,
    Moment projections, or clip review cards.
- **Browser UI tests (`tests/ui_player.py` across Chromium & WebKit)**:
  - Compact `Reviews` tab and `Plan` tab switching, generating/updating a mocked plan,
    citation deep-links to Clip Study, and editing practice log statuses.
