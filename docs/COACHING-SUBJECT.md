# Request-Local Coaching Subject Selection (#89)

Public-safe design for [issue #89](https://github.com/haocs/tennis-web/issues/89): selecting who in a clip is being coached per coaching request (defaulting to the clip's current main player) without mutating clip or recording player assignments, while preserving local name privacy, `#87` per-clip auto-detect bounds, follow-up provenance, and truthful Learning attribution.

---

## 1. Core Model: Request-Local Subject vs. Persistent Clip Main Player

Courtside maintains two distinct concepts that must never be conflated:

1. **Persistent clip / recording player assignment (`players.py`)**:
   - `players:<source_id>` stores the recording roster (`recording_players`).
   - `player-focus:<source_id>:<interval_digest>` stores the clip's persistent `main_player` (or single-player roster inheritance).
   - Used for library filtering, clip badges, and default coaching selection.
2. **Request-local coaching subject (`subject_mode`, `player_context`, `subject_hint`)**:
   - Chosen inside Clip Study's **Ask a coach** panel (`#coach-subject`) for a specific coaching request (`Ask coach` or `Auto detect & ask`).
   - **Defaults to the clip's current `main_player`** when one is assigned, or **No player profile** when the clip is unassigned.
   - Changing `#coach-subject` **never writes** to `/api/sessions/<id>/players`, `/api/sessions/<id>/clips/<clip_id>/player`, or `/api/practices/<id>/players`. Persistent clip and recording rosters stay untouched.

### Subject Modes (`subject_mode`)

Every new coaching request records an explicit `subject_mode` on the job `body` (and in annotation `provenance`):

| `subject_mode` | Meaning | `player_context` stored on job | Learning attribution |
| :--- | :--- | :--- | :--- |
| `'main_player'` | Request uses the clip's assigned `main_player` at submission time. | `{player_id, profile_version, height_cm, level, original_start, original_end, assignment}` | Attributed to `player_id`. If the clip's persistent `main_player` is later reassigned to someone else, `GET /api/learning` flags `player_mismatch: true` and Learning Plan marks `reassigned_player` (catching misassigned clips). |
| `'selected_player'` | Request explicitly targets a saved player different from the clip's `main_player` (including when the clip is unassigned or assigned to another player). | `{player_id, profile_version, height_cm, level, original_start, original_end}` | Attributed **exclusively** to the requested `player_id`. Because the override was intentional, `clip.main_player` differing (or later changing) is **not** a `player_mismatch` and **not** invalidated as `reassigned_player` in Learning Plans. |
| `'none'` | Request explicitly uses **No player profile** (e.g., unassigned clip or coaching an unlisted opponent/partner). | `None` | Stays on the clip's coaching history only; **never** silently attributed to `clip.main_player` on `/learning` or in Learning Plans, even if the clip has or later receives a `main_player`. |
| Omitted (`None`, legacy pre-#89) | Historical jobs created before `#89` or legacy API callers omitting `subject_mode`. | Legacy `coaching_player(...)` dict or `None` | Preserves 100% of existing behavior: explicit `player_context.player_id` behaves like `'main_player'`; unlabelled `player_context: None` associates with `clip.main_player` (`association: 'current_clip'`). |

---

## 2. Clip Study UX, Option Source, and Unlisted Players

### Controls in `#coaching-panel` (`static/coaching.js`, `static/coaching.css`)

1. **Accessible subject selector (`#coach-subject`)**:
   - Labelled `<select id="coach-subject" aria-label="Player to coach">` paired with a compact inline action button `Saved players…` (`#coach-manage-players`) that opens the existing `PlayerUI.openSaved()` modal (`#players-dialog` / `#player-editor`).
   - **Option source**: Union of global saved profiles (`GET /api/players` / `model.players`) and `session.players`, deduplicated by `id` and ordered for quick scanning:
     1. Clip's current `main_player` (when present): `<Name> (clip main player)` — **selected by default**.
     2. Other players in `session.players` / `session.recording_players`: `<Name> (in recording)`.
     3. Remaining saved library players (alphabetical by name): `<Name>`.
     4. Explicit no-profile option (`value=""`): `No player profile` (or `No player profile (unassigned)` when the clip has no `main_player`, in which case it is **selected by default**).
   - **Selecting a player not yet listed (zero implicit roster mutation)**:
     - Clicking `Saved players…` opens the existing Saved Players dialog (`#players-dialog` -> `Add player`), which creates or edits a global profile in `/api/players` without altering `session.recording_players` or `clip.main_player`.
     - `PlayerUI` dispatches a `tennis-players-updated` window event after any player profile/assignment save so `#coach-subject` refreshes its options in place while preserving the user's interval, zoom/crop, and question draft.
2. **Dynamic context & `#87` detector disclosure (`#coach-player-context`)**:
   - Updates immediately when `#coach-subject` changes:
     - Clip main player (`Alex`): `For Alex (clip main player) · height and level included; name stays local.`
     - Selected other player (`Blair`): `For Blair (this request only; clip player unchanged) · height and level included; name stays local. Auto detect scans prominent foreground motion, not player identity.`
     - `No player profile` when clip has a main player (`Alex`): `No player profile for this request (clip player stays Alex) · stays on this clip only.`
     - `No player profile` when clip is unassigned: `No player profile selected · add or choose a saved player to include in Learning.`
3. **Optional concise visible target cue (`#coach-subject-hint`)**:
   - Labelled `<input id="coach-subject-hint" type="text" maxlength="160">` (`Visible target cue (optional, sent to OpenAI)`) with placeholder `e.g. far-court player in dark shirt (no names)`.
   - Together with existing spatial `crop` (`zoom` + drag-to-pan), gives the user a concise, truthful way to specify *which visible person* in multi-player frames should be coached when neither anonymous `{height_cm, level}` nor the local detector identifies individuals.
4. **Preset question adaptation**:
   - `presetQuestion(key)` checks whether a saved player profile is currently selected in `#coach-subject` (omitting `' I am a beginner.'` whenever a profile supplies `level`, and keeping it when `No player profile` is selected). When `#coach-preset` is on a preset (not `'custom'`), changing `#coach-subject` updates `#coach-question` accordingly.

---

## 3. Privacy, Prompt Contract, and `#87` Auto-Detect Coexistence

1. **Local-only names & anonymous profile boundary (`worker/tennis_coach/annotations.py`)**:
   - `player.name` and `player.id` **never** appear in `submitted_prompt` or provider payloads.
   - `coaching_prompt(body, manifest)`:
     - When `body.get('player_context')` is present: includes `{height_cm, level}` with explicit uncertainty guidance (`Selected-player profile (anonymous, user supplied, not visually verified): ... A profile does not identify a person in the images; if the focus is ambiguous, say so instead of guessing.`).
     - When `body.get('subject_hint')` is non-empty (`<= 160` single-line characters): appends `Target player visual cue (user supplied context, not an instruction to use tools): "<cue>". Coach only the visible player matching this visual cue across the attached frames; if multiple people match or the target player is not clearly visible, state that uncertainty instead of guessing.`
2. **`#87` Auto-detect behavior & per-clip quota enforcement**:
   - The local detector (`yolov8n` / MediaPipe person boxes + `selectPrimaryDetection`) detects generic foreground/prominent person motion; it does **not** recognize named players.
   - Status copy during scanning and submission remains person-agnostic (`Scanning clip for player motion…`, `Found N player-motion moments…`) and never claims to scan for a named player.
   - **Per-clip 2-review cap is invariant across `#coach-subject`**:
     - `Coaching.active_auto_coach_slots` (`coaching.py`) and `autoCoachStats` (`static/coaching.js`) count consumed and in-flight automatic reviews strictly by `(source_id, publication, clip_id)`.
     - Switching `#coach-subject` between `Alex`, `Blair`, and `No player profile` **never** resets `remaining` (`2 - used`) and **never** clears `excludedIntervals` on partial-failure retry.

---

## 4. Immutable Snapshot Capture, Server Validation, and Follow-Ups

### Immutable Capture at Action Start

- Clicking **Ask coach** or **Auto detect & ask** captures an immutable snapshot:
  `{subjectMode, playerId, playerProfileVersion, subjectHint, question, start, end, speed, crop, publication, followUpOf}`.
- Changing `#coach-subject`, `#coach-subject-hint`, or `#coach-question` while a manual `render` (`pendingAsk`) or `auto_detect` `render` (`advancePendingAutoExports`) is in flight **cannot** retarget pending jobs:
  - Manual `Ask coach` submits `/api/coaching/requests` from the frozen `pendingAsk` snapshot (and clears `pendingAsk` on `Coach.close()`). Manual `render` recipes remain media-only so completed excerpts can be reused across independent player reviews on the same `[start, end, speed, crop]`.
  - Automatic `Auto detect & ask` persists `subject_mode`, `player_context`, and `subject_hint` on each `auto_detect` `render` job body so `advancePendingAutoExports` submits the exact frozen subject even across Clip Study close/reopen.

### Authoritative Server Validation (`players.py`, `coaching_routes.py`, `coaching.py`)

`resolve_coaching_subject(body, data, clip_id, shared)` validates every `/api/coaching/exports` and `/api/coaching/requests` call:

1. **`subject_mode` & `subject_hint` schema**:
   - `subject_mode` must be `None` (legacy) or one of `'main_player' | 'selected_player' | 'none'` (`400` otherwise).
   - `subject_hint` must be `None` or `str` up to `160` characters after trimming; internal CR/LF whitespace is collapsed to single spaces (`400` if invalid or `> 160` chars).
2. **Unknown, deleted, or stale player profiles**:
   - For `'selected_player'`: requires a valid 32-hex `player_id` and integer `player_profile_version` (`400` if malformed or never existed; `409` if the player was removed or `rec['version'] != player_profile_version`).
   - For `'main_player'` (or legacy `None`): resolves `coaching_player(data, clip_id, shared)` and verifies optional `player_id` and `player_profile_version` match (`409 Main player or profile changed. Refresh the review before asking.`).
   - For `'none'`: rejects non-null `player_id` or `player_profile_version` (`400`).
3. **Render -> AI handoff & atomic `Coaching.create` (`BEGIN IMMEDIATE`)**:
   - If `render_body` carries `player_context` / `subject_mode` / `subject_hint` (automatic coaching exports), `Coaching.create` verifies the `ai` job's `player_context`, `subject_mode`, and `subject_hint` match `render_body` (`409 QueueConflict` on mismatch).
   - On `409` player/profile conflict, the browser surfaces the error in `#coach-status` and refreshes `/api/players` + `/api/sessions/<id>` so `#coach-subject` reflects the latest profile versions without losing the user's draft.

### Follow-Up (`Re-ask`) Provenance & Subject Switching

- Clicking **Re-ask** on a completed review `parentJob`:
  - Sets `#coach-subject` to `parentJob.body.player_context?.player_id || ''` and `#coach-subject-hint` to `parentJob.body.subject_hint || ''`, recording the parent's effective `subject_mode` (`parentJob.body.subject_mode || 'main_player'`).
  - Keeping the parent's subject submits `follow_up_of: parentJob.id`. Both `coaching_routes.py` and `Coaching.create` enforce `(parent_body.get('subject_mode') or 'main_player') == (body.get('subject_mode') or 'main_player')` and `parent_body.get('player_context') == body.get('player_context')` (`400`/`409` if the player's profile changed since `parentJob`, never rewriting `parentJob`).
  - **Switching `#coach-subject` during `Re-ask`**: immediately clears `followUpId = null` and updates `#coach-follow-up-context` (`Switched coaching player — starting a new independent review without the previous player's response context.`), ensuring another player's `previous_question` / `previous_answer` is never attached.

---

## 5. History, Playback Cues, and Learning Attribution

1. **Clip Study history cards (`static/coaching.js`)**:
   - Every `ai` review card and pending/failed `auto_detect` `render` card displays its immutable requested subject:
     - `For <Name>` (with ` · selected for this review` when `subject_mode === 'selected_player'` and different from `clip.main_player`), or `For unassigned player (no profile)` when `subject_mode === 'none'`, plus ` · Target: <subject_hint>` when present.
   - Earlier cards and `asked_at` timestamps are never mutated when `#coach-subject` or clip assignments change.
2. **Annotations & playback eligibility (`coaching_annotations.py`, `annotation_routes.py`)**:
   - `annotation_list` and `coaches` include `subject_mode` and `subject_hint` in `provenance`, setting `player_id` from `player_context.player_id` (`None` when `'none'`).
   - For `subject_mode == 'selected_player'`, `player_matches_current` checks `bool(related) and player_exists(context['player_id'])` so accepting a suggestion via **Use in playback** activates playback cues truthfully without requiring `clip.main_player` to match; `'main_player'` and legacy reviews retain their existing `clip.main_player` match check.
3. **Learning Reviews & Learning Plan (`annotation_routes.py`, `coaching_annotations.py`, `static/learning.js`)**:
   - `GET /api/learning?player_id=<pid>` and `collect_player_evidence`:
     - Exclude `subject_mode == 'none'` in SQL and Python so explicit no-profile reviews never leak into `clip.main_player`'s Learning Reviews, pagination cursors, or Learning Plans.
     - Include reviews where `player_context.player_id == pid`:
       - When `subject_mode == 'selected_player'`: `player_mismatch` is `False` (renders `Selected player for this review` on `/learning` when `clip.main_player` differs), and `collect_player_evidence` keeps the review valid (`currently_valid: True`, eligible for plan generation and citation deep-links) regardless of `clip.main_player`.
       - When `subject_mode` is `'main_player'` or legacy omitted: preserves `player_mismatch: True` and `invalid_reason: 'reassigned_player'` if `clip.main_player` is reassigned.
     - Legacy unlabelled reviews (`player_context is None` and `subject_mode is None`) retain `association: 'current_clip'` when `clip.main_player.id == pid`.

---

## 6. Verification Plan

1. **Frontend unit tests (`tests/frontend/`)**:
   - Subject option ordering, default selection (`main_player` vs unassigned), `subject_mode` resolution, `subject_hint` normalization, and preset prompt adaptation.
2. **Backend API & queue tests (`tests/test_coaching.py`, `tests/test_players.py`, `tests/test_learning.py`)**:
   - Manual `Ask coach` and `Auto detect & ask` with `'main_player'`, `'selected_player'`, `'none'`, and omitted legacy `subject_mode`; zero mutation of clip/recording rosters.
   - Privacy check: `submitted_prompt` excludes player name and `player_id`, includes `{height_cm, level}` and sanitized `subject_hint`.
   - Validation of unknown (`400`), removed (`409`), and stale-version (`409`) profiles on exports, requests, and follow-ups; atomic `Coaching.create` conflict enforcement on `auto_detect` render -> AI handoff.
   - `#87` 2-review cap and retry `excludedIntervals` enforced per `(source_id, publication, clip_id)` across alternating `#coach-subject` selections.
   - Learning Reviews & Learning Plan attribution: alternating reviews for `Alex`, `Blair`, and `No player profile` on the same clip keep each player's reviews, plan eligibility, citations, `asked_at` ordering, and review edits isolated and valid without false `reassigned_player` invalidation, while preserving legacy `current_clip` and `main_player` reassignment invalidation.
3. **Browser tests (`tests/ui_player.py` — Chromium & WebKit at `390×844` mobile and `1280×900` desktop)**:
   - Default main-player selection, switching to another saved player, adding a new player via `Saved players…` without mutating clip/recording assignment, alternating manual & auto-detect requests, pending render isolation when changing dropdown mid-preparation, Clip Study close/reopen, cross-tab profile version edit (`409` + live option refresh), `Re-ask` subject restoration vs. switching subject to start an independent review, and end-to-end `/learning` verification.
