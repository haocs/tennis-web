# Player learning plans and incremental practice tracking (#85)

Public-safe design for [issue #85](https://github.com/haocs/tennis-web/issues/85):
compact coaching reviews, per-player AI ability & practice plans, manual practice
progress logs with append-only revision history, and incremental evidence-bounded
updates reusing the existing coaching AI worker.

---

## 1. UX & Information Architecture (`/learning`)

When a player is selected on `/learning`, the page provides two accessible tabs
(`role="tablist"`, URL/state-preserved via `?player=<id>&tab=reviews|plan`) beneath
the player selector:

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
   - **Freshness, cadence & cost banner**:
     - Shows current plan status (`empty`, `up_to_date`, `eligible`, `queued`,
       `leased`, `calling`, `needs_review`, `failed`), when the active plan was
       generated, how many new or revised coaching reviews/suggestions, invalidated
       reviews, unsent overflow backlog items, or manual practice log updates exist
       relative to the active plan's covered state, plus a 14-day cadence guidance
       note (`recommended_cadence_days: 14`).
     - Requires an explicit consent checkbox (`Send selected review summaries and practice progress to OpenAI`)
       and an explicit **Generate learning plan** / **Update learning plan** button
       (plus an explicit **Confirm & retry** action only when an earlier job is in
       `needs_review` or `failed`).
     - **No automatic or background AI calls**: visiting `/learning`, switching
       players, switching tabs, or editing a practice log never triggers an AI call
       automatically.
     - **No-op suppression**: when the active plan's `covered_state_digest` matches
       the live `target_state_digest` (no uncovered/revised/invalidated reviews, no
       profile change, and no manual practice-log change since the covered state),
       the Generate/Update button is disabled with a clear explanation (`Plan is up
       to date with current reviews and practice logs`).
   - **Structured plan sections** (explicitly separating epistemological categories):
     1. **Observed ability & status summary** (`observed_summary`): What is directly
        supported by reviewed clip evidence, with clickable citation pills linking
        to the cited coaching reviews/suggestions (`Review` deep-links into Clip
        Study) and badges distinguishing user-accepted reviews (`User confirmed`)
        from unconfirmed AI proposals (`Unconfirmed AI proposal`).
     2. **Focus areas & practice drills** (`focus_areas`): Up to 5 prioritized
        practice items, each with stable `item_key`, `topic`, `title`,
        `observed_issue`, `cue`, `drill`, `uncertainty`, cited `evidence_ids`, and
        an inline **Practice progress editor** (`not_started` | `ongoing` |
        `practiced` | `working_well` | `archived`, optional note, editable content,
        real **Cancel** button to discard unsaved edits, and expandable **History**
        showing append-only revisions).
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
     - Players can add any number of standalone manual practice focus items or edit
       the status/note/content of any focus item at any time without calling AI.
     - Every creation or update appends an immutable revision row to
       `learning_practice_revisions` (`actor` in `'ai_plan' | 'human'`, `version`,
       `status`, `note`, `value_json`, `created_at`).
     - Previous plan revisions remain browsable in a collapsible **Plan history**
       disclosure showing each revision's timestamp, status (`active`, `historical`,
       `superseded`), coverage summary, and delta counts.

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
  covered_state_digest TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
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
  status TEXT NOT NULL,
  covered_state_digest TEXT NOT NULL,
  request_digest TEXT NOT NULL,
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
  origin TEXT NOT NULL,
  title TEXT NOT NULL,
  topic TEXT NOT NULL,
  observed_issue TEXT NOT NULL,
  cue TEXT NOT NULL,
  drill TEXT NOT NULL,
  uncertainty TEXT NOT NULL,
  status TEXT NOT NULL,
  note TEXT NOT NULL,
  evidence_ids_json TEXT NOT NULL,
  origin_revision INTEGER,
  last_plan_revision INTEGER,
  user_edited_content INTEGER NOT NULL DEFAULT 0,
  user_version INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  request_id TEXT,
  UNIQUE(player_id, item_key)
);
CREATE INDEX IF NOT EXISTS learning_practice_logs_player
  ON learning_practice_logs(player_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS learning_practice_revisions(
  log_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  player_id TEXT NOT NULL,
  item_key TEXT NOT NULL,
  actor TEXT NOT NULL,
  status TEXT NOT NULL,
  note TEXT NOT NULL,
  value_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(log_id, version)
);
```

- All tables are serving metadata (`training_label_status: "not_released"`), kept
  strictly separate from `annotations.sqlite3` processing annotations (`cut_feedback`,
  `processing_label`) and detector training labels.

---

## 3. Generalized AI Worker Contract & Purpose Dispatch

Instead of introducing a new daemon, learning-plan synthesis reuses the existing
`role = 'ai'` worker process in `worker/tennis_coach/worker.py` with explicit
capability gating and `purpose` dispatch so legacy coaching jobs and text-only
learning-plan jobs never collide.

### Capability, Package Version & Purpose Fencing

- **Companion worker package version bump (`worker/pyproject.toml` -> `0.4.0`)**:
  - Constants in `worker/tennis_coach/annotations.py`:
    - Existing frame-sequence coaching capability: `CAPABILITY = 'coaching-annotations-v1'`
    - New learning-plan capability: `LEARNING_PLAN_CAPABILITY = 'learning-plan-v1'`
    - New prompt & schema version: `LEARNING_PLAN_PROMPT_VERSION = 'tennis-learning-plan-v1'`,
      `LEARNING_PLAN_SCHEMA_VERSION = 1`
- **Worker claim (`Worker.once()` in `worker/tennis_coach/worker.py`)**:
  - When `self.role == 'ai'`, advertises `capabilities = [CAPABILITY, LEARNING_PLAN_CAPABILITY]`.
  - An older AI worker advertising only `['coaching-annotations-v1']` will not claim
    `required_capability = 'learning-plan-v1'` jobs; they remain safely queued until
    the upgraded worker package is installed during batch deployment.
- **Job body contract (`purpose = 'learning_plan'`)**:
  - `purpose`: `'learning_plan'` (existing frame coaching jobs have `purpose` omitted
    or `'coaching_frames'`).
  - `player_id`: validated 32-hex player ID; `player_profile`: anonymous
    `{height_cm, level, profile_version}` (never sending player name, GPS, file paths,
    or NAS URLs).
  - `parent_revision`: integer revision of the player's active plan at submission time
    (or `None` for the initial plan).
  - `covered_state_digest`: deterministic SHA-256 hex digest of the canonical covered
    inventory (`{profile, evidence: covered_evidence_map, practice_logs: covered_logs_map, prompt_version}`)
    that this plan revision will cover upon completion.
  - `request_digest`: deterministic SHA-256 hex digest of the exact request snapshot
    (including `parent_revision`, `delta_evidence`, `removed_evidence`,
    `carried_citations`, and `practice_logs`).
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
2. **Worker completion, audit-only supersession & route validation**:
   - In `coaching_routes.py` (`POST /api/coaching/worker/jobs/<identity>/<action>`):
     - For `purpose == 'learning_plan'`, `action == 'complete'` validates
       `result.get('analysis_mode') == 'learning_plan'` and `1 <= len(result['answer']) <= 64000`,
       and `action == 'evidence'` is rejected (`400`, text-only plans do not register image manifests).
   - In `Coaching.worker_update(identity, token, 'complete', result)`:
     - Retrying `complete` with the same lease token on an already `completed` job
       returns the saved job idempotently without appending duplicate revisions or
       practice logs.
     - Branches on `body.get('purpose') == 'learning_plan'`:
       - Does **not** call `self.save_annotations(db, row, result)` (never creates
         fake clip annotations or Moments).
       - Calls `self.save_learning_plan(db, row, result)` inside the same `BEGIN IMMEDIATE`
         transaction:
         - Parses and normalizes the JSON response against the submitted snapshot's
           `covered_evidence_map` (which equals the exact supplied `delta_evidence`
           UNION `carried_citations` minus `removed_evidence`), stripping any
           unknown, cross-player, unsent-overflow, or invalidated `evidence_ids` with
           recorded `validation_warnings`.
         - Allocates `new_revision = COALESCE(MAX(revision), 0) + 1` monotonically per
           `player_id` in `learning_plan_revisions`, independent of `active_revision`.
         - Checks active-parent CAS (`current_active_revision == body.get('parent_revision')`)
           and player existence (`player_exists(player_id)`):
           - **Always** inserts the audit row into `learning_plan_revisions` (with
             `status = 'active'` if CAS winner and parse succeeded, or
             `status = 'superseded'` if a newer plan was already committed or the
             player was deleted).
           - **Only the active-parent CAS winner** updates `learning_plans` and
             seeds/updates `learning_practice_logs` + `learning_practice_revisions`.
           - Late/superseded completions are strictly audit-only and never mutate
             `learning_plans` or `learning_practice_logs`.
3. **Isolation from clip/session routes and queries (`coaching.py`, `coaching_routes.py`, `annotation_routes.py`)**:
   - `Coaching.listing(source)` filters by `json_extract(body, '$.source_id') = ?`,
     naturally excluding player-level learning-plan jobs (`source_id` is absent).
   - `POST /api/coaching/annotations` (`manual_annotation`) and `Coaching.manual_annotation`
     check `purpose != 'learning_plan'` and presence of `source_id` / `evidence_frames`,
     returning `400` cleanly rather than `KeyError: 'source_id'`.
   - `PUT /api/coaching/jobs/<id>` (`Coaching.edit` with `action == 'cue'`) rejects
     `purpose == 'learning_plan'` with `400`.
   - `GET /api/annotations/coach-response:<id>/revisions`, `coaches(source, data)`,
     and `GET /api/learning` clip-review queries explicitly require
     `COALESCE(json_extract(body, '$.purpose'), 'coaching_frames') = 'coaching_frames'`
     and `json_extract(body, '$.source_id') IS NOT NULL`.

---

## 4. Truthful Bounded Coverage, Freshness vs. Snapshot Identity, and Practice Logs

### Canonical Evidence Units & Dismissal Rules

For a given `player_id`, currently valid evidence (`all_valid_evidence_map`) is built
from completed `coaching_frames` jobs attributed to `player_id` where the clip has not
been reassigned to a different player (`not player_mismatch`):
1. **Structured coaching responses (`annotations` rows exists OR `result.structured` is present)**:
   - Represented **exclusively** by non-dismissed rows in `annotations`
     (`evidence_id = 'coach:' + annotation_id`, `status in ('accepted', 'proposed')`).
   - Preserves provenance trust: `accepted` (`user_confirmed`) vs `proposed`
     (`unconfirmed_ai_proposal`).
   - If all suggestions on a structured response are dismissed (`status == 'dismissed'`),
     it produces **zero** valid evidence items and **never** falls back to an eligible
     freeform `coach-response:<job_id>` item.
2. **Legacy/unstructured coaching responses (`result.structured` is absent and 0 rows in `annotations`)**:
   - Represented by `evidence_id = 'coach-response:' + job_id`, with
     `review_status = 'accepted'` if `cue_reviewed` else `'saved_response'`.

### Truthful Bounded Coverage & Backlog Preservation

When constructing a snapshot (with `active_plan` at revision $R$, or `None` initially):
- Let `prior_covered_evidence_map` be $R$'s `snapshot.covered_evidence_map` (or `{}`).
- **Removed / invalidated evidence (`removed_evidence`)**:
  - Any `eid` in `prior_covered_evidence_map` that is no longer in `all_valid_evidence_map`
    (with reason `'dismissed'`, `'reassigned_player'`, or `'removed'`), bounded to at
    most 64 items / 8 KB per request.
- **Uncovered or revised evidence (`uncovered_or_changed`)**:
  - Any item in `all_valid_evidence_map` where
    `prior_covered_evidence_map.get(eid) != item['version_digest']` (including brand-new
    reviews, edited/accepted reviews, and any overflow items from earlier plans that
    were truncated by the per-request cap).
  - Ordered by newest `asked_at` first; packed into `delta_evidence` up to
    **24 items** and **48,000 serialized UTF-8 bytes** (with individual text fields
    bounded).
  - Any remaining uncovered/changed items stay in the unsent backlog:
    `truncated_evidence_count = len(uncovered_or_changed) - len(delta_evidence)`.
- **Exact covered evidence map (`covered_evidence_map`)**:
  - `covered_evidence_map` is defined as:
    `(prior_covered_evidence_map minus sent removed_evidence_ids) updated with the exact items in delta_evidence`.
  - Unsent overflow items are **never** added to `covered_evidence_map`, so they
    remain truthfully reported in `backlog_reviews_count` and immediately eligible
    for a subsequent plan update.
- **Carried citations registry (`carried_citations`)**:
  - Previously covered, still-valid evidence items (`eid in covered_evidence_map` not
    in `delta_evidence`) are carried in `snapshot.carried_citations` with compact
    provenance (`evidence_id`, `asked_at`, `source_id`, `clip_id`, `original_start`,
    `original_end`, `review_status`, `provenance_trust`, `title`, `topic`, `cue`,
    bounded to 60 items / 24 KB) so incremental plans can continue citing earlier
    valid evidence without re-uploading full old text.
- **Equivalent truthful coverage for practice logs (`covered_logs_map`)**:
  - User-modified practice items (`user_version > 0`) form `all_user_logs_map = {item_key: user_digest}`.
    Auto-seeded default items (`user_version == 0`) are not user changes and do not
    make a newly completed plan stale.
  - Up to **20 practice logs** and **16,000 serialized UTF-8 bytes** are included in
    `snapshot.practice_logs` (prioritizing items whose `user_digest` differs from
    `prior_covered_logs_map`).
  - `covered_logs_map` records `prior_covered_logs_map` updated only with the
    user-modified logs actually included in `snapshot.practice_logs`; any overflow
    beyond 20 items / 16 KB remains in `truncated_logs_count` (`backlog_practice_logs_count`).

### Separating Freshness (`covered_state_digest`) from Request Identity (`request_digest`)

- **`covered_state_digest`**:
  `identity({'profile': player_profile, 'evidence': covered_evidence_map, 'practice_logs': covered_logs_map, 'prompt_version': LEARNING_PLAN_PROMPT_VERSION})`
- **`target_state_digest`**:
  `identity({'profile': current_player_profile, 'evidence': all_valid_evidence_map, 'practice_logs': all_user_logs_map, 'prompt_version': LEARNING_PLAN_PROMPT_VERSION})`
- **`request_digest`**:
  `identity({'player_profile': current_player_profile, 'parent_revision': parent_revision, 'delta_evidence': delta_evidence, 'removed_evidence': removed_evidence, 'carried_citations': carried_citations, 'practice_logs': prompt_practice_logs, 'prompt_version': LEARNING_PLAN_PROMPT_VERSION})`
- **Freshness & No-op Eligibility**:
  - A player's active plan is `up_to_date` (`can_generate = False`) when
    `active_plan.covered_state_digest == target_state_digest`.
  - Immediately after an initial or incremental plan completes with zero overflow and
    zero concurrent user edits, `active_plan.covered_state_digest == target_state_digest`
    holds immediately (independent of `parent_revision` or `delta_evidence`), so
    `GET /api/learning` reports `up_to_date` and `POST /api/learning/plan` rejects
    no-op regeneration with `409 Conflict`.
  - If inputs changed while the AI call was in flight (new review, dismissed review,
    profile edit, or manual practice edit) or if overflow backlog items remain,
    `active_plan.covered_state_digest != target_state_digest`, so `can_generate`
    remains `True` after completion.

### Stable Practice Item Keys & Preservation Rules

- Only existing `item_key`s belonging to the same `player_id` in
  `learning_practice_logs` may continue an existing practice item when a new plan
  completes.
- Genuinely new focus areas (or any unrecognized / cross-player key returned by the
  model) receive a fresh server-generated `item_key` (`fi_<16-hex>`). Manual items
  receive `manual_<16-hex>`. Identity is never derived from mutable `title` or `topic`.
- Across plan replacements:
  - Existing `status` and `note` are always preserved.
  - If `user_edited_content == 1`, user-edited `title`, `topic`, `cue`, and `drill`
    are also preserved.
  - Standalone manual items (`origin = 'manual'`) and archived items remain intact.

---

## 5. API Endpoints

1. `GET /api/learning?player_id=<id>[&after=<cursor>&limit=<n>]`
   - Existing `entries` and `next_after` preserved for backward compatibility.
   - Includes `plan_state` (`active_plan`, `revisions`, `practice_logs`, `eligibility`,
     `pending_job`, `enabled`) when `after` is empty.
2. `POST /api/learning/plan`
   - Body: `{player_id, consent: true, request_id, expected_parent_revision, input_digest, retry_ambiguous?: bool}`
   - Enforces player existence, explicit consent, worker configuration, per-player
     in-flight mutex (`409` if another job is `queued`/`leased`/`calling`, or if in
     `needs_review` without `retry_ambiguous: true`), `expected_parent_revision` CAS
     check (`409`), no-op check (`409` if `covered_state_digest == target_state_digest`),
     and `input_digest` freshness check (`409` if inputs changed in another tab).
3. `POST /api/learning/practice-logs`, `PUT /api/learning/practice-logs/<log_id>`, and
   `GET /api/learning/practice-logs/<log_id>/revisions`
   - Conflict-safe creation, update (`expected` `version` check returning `409` on
     stale version), and append-only revision history retrieval.

---

## 6. Companion Upgrade & Test Plan

- **Companion worker upgrade (`worker 0.4.0`)**:
  - Upgrade both `render` (`share-export-v2`, `share-export-v1`) and `ai`
    (`coaching-annotations-v1`, `learning-plan-v1`) worker processes from the
    reviewed `worker/` package during coordinator batch deployment.
- **Automated tests (`tests/test_learning.py` & `tests/ui_player.py`)**:
  - Truthful coverage & >24 review / >20 practice-log overflow backlog preservation.
  - Initial generation -> completion -> immediate `GET` (`up_to_date`) and `409` no-op
    `POST` with zero changes, plus re-enabling after review edit/dismissal/reassignment,
    profile edit, or manual practice edit.
  - In-flight input changes during an active call leaving the completed plan with
    truthful pending delta.
  - Stale completion supersession (audit-only in `learning_plan_revisions`, zero
    mutation of active plan or practice logs) and idempotent completion retry.
  - Stable per-player `item_key` continuation, rejection of cross-player/invented keys,
    preservation of user-edited status/note/content, and append-only
    `learning_practice_revisions` history.
  - Structured response with all suggestions dismissed never falling back to freeform
    `coach-response:<id>`.
  - Route & worker isolation across all clip-specific endpoints and Chromium/WebKit
    UI tests at `320px`, `390px`, and `1280px`.
