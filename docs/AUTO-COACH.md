# Clip Study Auto Detect and Ask (M1)

Implements Milestone 1 (M1) of [issue 87](https://github.com/haocs/tennis-web/issues/87): an explicit **Auto detect & ask** workflow in Clip Study (`#coaching-panel`), capped at **2 automatic coaching reviews per clip**.

## Milestone scope (M1 vs M2)

- **M1 (implemented here)**: Explicit user action inside Clip Study (`#coach-auto-ask`) that scans the currently open clip (`#study-video`) locally in the browser using the vendored MediaPipe person detector (`static/autofocus-worker.js` + `static/autofocus-core.js`), selects **0, 1, or at most 2** non-overlapping candidate intervals of primary-player presence and movement, and submits at most the remaining automatic review quota for that clip through the existing coaching export and AI request queues (`POST /api/coaching/exports` → `POST /api/coaching/requests`).
- **M2 (deferred)**: Conditional batch or background processing across multiple clips or sessions is explicitly out of scope until M1's local detection heuristic and coaching output usefulness are validated in practice. Opening a session, clip, or modal never starts background scanning or automatic AI requests.

## Truthful local heuristic and non-claims

### What the local detector measures

The browser worker runs the vendored `@mediapipe/tasks-vision@0.10.14` `ObjectDetector` (`efficientdet_lite0.tflite`, `categoryAllowlist: ['person']`, `scoreThreshold: 0.22`) on bounded offscreen video frames (`maxEdge = 384`). Across chronological sample timestamps $t_0 < t_1 < \dots < t_{N-1}$, `AutoFocusCore` computes:

1. **Primary-player selection (`selectPrimaryPerson`)**:
   - Normalizes `person` detections into `[0, 1]` video coordinates (`cx, cy, width, height, area, bottom, score`).
   - Prioritizes the nearest/largest on-court player via proximity/size scoring (`sqrt(area) * 1.6 + bottom * 0.45 + score * 0.1`) with bounded continuity affinity (`iou` + center-distance proximity) from the previous sample so comparable detections do not flip-flop while a clearly closer foreground player takes focus.
2. **Per-sample presence and prominence (`presenceScore`)**:
   - Requires a valid primary-person detection (`score >= 0.22`, `area >= 0.004`).
   - Combines foreground scale, lower-frame court position, and detector confidence:
     $$\text{presenceScore}_i = 0.65 \cdot \text{clamp}\!\left(\frac{\sqrt{\text{area}_i}}{0.28}, 0, 1\right) + 0.20 \cdot \text{clamp}(\text{bottom}_i, 0, 1) + 0.15 \cdot \text{clamp}(\text{score}_i, 0, 1)$$
3. **Per-sample bounding-box motion (`motionScore`)**:
   - Between consecutive detected primary-player boxes separated by $\Delta t \in (0, 2.5\text{ s}]$ (with continuity check so an unrelated far-corner box jump is not treated as single-player velocity), measures scale-normalized center translation and box deformation per second:
     $$v_i = \frac{\sqrt{(\Delta cx)^2 + (\Delta cy)^2} + 0.35 \cdot \sqrt{(\Delta w)^2 + (\Delta h)^2}}{\Delta t \cdot \max(0.08, \sqrt{\text{area}_i})}, \qquad \text{motionScore}_i = \text{clamp}\!\left(\frac{v_i}{1.2}, 0, 1\right)$$
4. **Combined sample activity (`activityScore`)**:
   - `0` when no primary person is detected at $t_i$.
   - `0.45 * presenceScore + 0.55 * motionScore` when motion against a neighboring detection is available (or `0.50 * presenceScore` for an isolated detection on a short clip).

### Explicit non-claims

- The detector identifies **`person` bounding boxes and 2D box movement only**.
- It does **not** detect tennis balls, rackets, swing phases, ball contact, stroke types (forehand, backhand, serve, volley), or rally rules (`playVerdict: 'not measured'`, `swingVerdict: 'not measured'`).
- UI labels, status messages, and job metadata describe selections strictly as **auto-detected player-motion moments** (`auto_detect: true`), never as detected swings or contact points.

## Bounded sampling and candidate window selection

All sampling and window-ranking math lives in pure functions in `static/autofocus-core.js` shared with Node unit tests:

1. **Bounded sample schedule (`planAutoCoachSampleTimes(duration, maxSamples = 16)`)**:
   - For clip duration $D > 0$: selects $N = \text{clamp}(\text{round}(D / 1.5), 4, 16)$ interior timestamps (for very short clips $D < 1.0\text{ s}$, $N = \text{clamp}(\text{floor}(D / 0.2), 2, 4)$).
   - For clips where uniform spacing $D / N \le 2.2\text{ s}$ ($D \le 35.2\text{ s}$ at $N = 16$), places $N$ uniform interior timestamps $t_i = D \cdot \frac{i + 0.5}{N}$.
   - For longer clips where uniform spacing would exceed the $2.5\text{ s}$ motion-continuity horizon ($D / N > 2.2\text{ s}$, e.g. $41\text{ s}, 60\text{ s}, 90\text{ s}$), places $P = \max(2, \lfloor N / 2 \rfloor) \le 8$ two-sample probes ($\le 16$ total samples) centered at $c_p = D \cdot \frac{p + 0.5}{P}$ with timestamps $\text{clamp}(c_p \mp 0.5\text{ s}, m, D - m)$ ($m = \min(0.1\text{ s}, 0.02 D)$, intra-pair $\Delta t \le 1.0\text{ s} \le 2.5\text{ s}$) and propagates the second sample's measured `motionScore` ($1.0\times$) back to the leading sample of the pair so every probe measures true primary-player velocity without exceeding the 16-frame scan budget.
   - Hard ceiling of **16 frames per scan**, avoiding `0` and `D` boundary black/undecodable edge frames.
2. **Candidate window construction and 0 / 1 / 2 selection (`selectAutoCoachWindows(samples, duration, options)`)**:
   - **Window span**: $W = \text{clamp}(D \times 0.3, 2.5, 8.0)$ seconds, clamped to $[0.1, \min(30, D)]$ (if $D < 2.5\text{ s}$, $W = D$).
   - **Eligibility thresholds (never manufacturing windows without evidence)**:
     - Evaluates candidate windows $[s, e] \subseteq [0, D]$ of length $W$ anchored around sample timestamps and midpoints.
     - Requires at least `minDetected` ($\ge 2$ detected primary-person samples when $N \ge 4$ and $D \ge 1.5\text{ s}$; $\ge 1$ high-prominence sample with `presenceScore >= 0.45` when $D < 1.5\text{ s}$ or $N < 4$).
     - Requires window score $S = 0.50 \cdot \text{meanActivity} + 0.30 \cdot \text{detectedMeanActivity} + 0.20 \cdot \text{peakMotion} \ge 0.24$ (`minWindowScore`) and peak motion $\ge 0.08$ (`minMotionScore`, when $\ge 2$ detected samples exist), ranked by $S + 0.03 \cdot \text{centerAlign}$.
     - Clips with no person detections or only tiny/static background boxes return **`[]` (0 windows)**. Clips with only one active segment return **1 window**.
   - **Ranking, non-overlapping diversity, and retry exclusion**:
     - Eligible windows are ranked by descending `rankingScore` (tie-broken by earlier `start`).
     - Consumed or in-flight automatic coaching intervals for the current published clip (`ai` jobs in `queued`, `leased`, `calling`, `completed`, `needs_review`, or `failed` with `provider_called: true`, plus unsubmitted `auto_detect` `render` jobs in `queued`, `leased`, or `completed`) are passed as `excludedIntervals`. Pre-call failed or cancelled `render` / `ai` jobs are **not** excluded so their intervals remain eligible on retry.
     - Greedily selects up to `maxWindows = Math.min(2, remainingSlots)` windows that are non-overlapping (`cand.end <= ref.start - minGap` or `cand.start >= ref.end + minGap`, where `minGap = 0.5 s` for $D \ge 9.0\text{ s}$ else `0`) and center-separated by at least $\max(2.5\text{ s}, 0.85 \min(W, W_{\text{ref}}))$ from both `excludedIntervals` and any previously chosen window in the current scan, with score $\ge 0.45 \times \text{topEligibleWindow.score}$ whenever at least one window is already excluded or chosen (and $D \ge 5.0\text{ s}$ for a second distinct window). If no distinct eligible moment remains after excluding already reviewed/in-flight intervals, returns **`[]` (0 windows)** rather than duplicating the already reviewed interval.
     - Selected windows are returned in chronological order (`start` ascending), each with rounded clip-local `[start, end]` (`0.001 s` precision) and canonical original-recording `[original_start, original_end]` (`clip.original_start_seconds + start/end`, clamped to `[clip.original_start_seconds, clip.original_end_seconds]`).

## Resource bounds, lifecycle, and Watch Auto focus coexistence

- **No worker conflict**: Watch-page Auto focus (`static/player.js`) observes `#clip-study[open]` and disables/terminates its worker whenever Clip Study opens. Clip Study creates a dedicated `Worker('/static/autofocus-worker.js')` only when the user clicks **Auto detect & ask**, and terminates it (`close` message + `worker.terminate()`) immediately in `finally` when the scan finishes, fails, or is cancelled.
- **Single inference in flight & timeouts**:
  - At most one `detect` request is in flight at any time (`seq`/`epoch` fenced).
  - Reuses a single offscreen `<canvas>` (`maxEdge = 384`) during the scan and resets it to `1×1` on cleanup.
  - Per-sample seek timeout: `6000 ms`; per-sample inference timeout: `8000 ms`; cold worker initialization timeout: `30000 ms`; total scan watchdog: `45000 ms`.
- **Clean cancellation and state restoration**:
  - Before scanning, captures the user's pre-scan playback position (`returnTime = video.currentTime`) and interval (`returnInterval = {start, end}`).
  - Clicking **Cancel scan** (`#coach-auto-cancel`), changing clips, closing `#clip-study`, or navigating away aborts the scan controller, increments the coaching generation fence, terminates the worker, restores `video.currentTime` and `[start, end]` to their pre-scan values, and suppresses any further export or AI submissions from the cancelled scan.
- **Unsupported browsers vs recoverable runtime/timeout errors**:
  - If `Worker`, `WebAssembly`, or worker `OffscreenCanvas` WebGL2 (`unsupported` / `worker_graphics_unavailable`) is unavailable, the scanner terminates cleanly, marks `#study-video` and `#coach-auto-ask` with `data-auto-coach-state="unavailable"`, and displays: `Automatic detection is unavailable in this browser. Select an interval and use Ask coach.`
  - If a transient runtime or timeout error occurs during scanning (`init_timeout`, `detect_timeout`, `scan_timeout`, `detector_error`, `worker_error`), the scanner terminates its worker, restores the prior interval and playhead, marks `#study-video` with `data-auto-coach-state="error"`, keeps `#coach-auto-ask` in recoverable `ready` state so the user can retry, and displays a truthful failure message in `#coach-status`. Manual interval selection and **Ask coach** remain fully available in all cases.

## Clip Study UX, privacy/consent, and immutable context

- **Controls in `#coaching-panel`**:
  - Adds **Auto detect & ask** (`#coach-auto-ask`) alongside **Ask coach** (`#coach-ask`), plus a **Cancel scan** button (`#coach-auto-cancel`, visible while scanning) and a concise quota status line (`#coach-auto-quota`, e.g. `2 of 2 automatic reviews remaining`).
  - Displays a concise two-mode disclosure covering both manual and automatic coaching (`Ask coach uses your selected interval (0.1–30s); Auto detect & ask scans the clip locally for up to 2 player-motion intervals. Applies your zoom and playback speed, sending up to 32 stills per review and your question to OpenAI.`, `consent: true`).
- **Immutable context snapshot at action start**:
  - Clicking **Auto detect & ask** captures an immutable snapshot of:
    - `source_id`, `publication` (`session.published_at`), and `clip_id`
    - `player_id` and `player_profile_version` from `clip.main_player`
    - `question` from `#coach-question` (validated `1..4000` chars)
    - `speed` (`video.playbackRate`, default `0.5×`) and `crop` (current Clip Study zoom/pan crop `[x, y, w, h]`)
  - Editing the question, speed, zoom, or switching clips after starting a scan or after exports are queued never mutates an in-flight automatic review's captured recipe.
  - Automatic reviews are always standalone clip-interval reviews (`follow_up_of` is cleared/omitted).

## Persistent 2-review cap per clip and atomic server enforcement

### Scope and counting rules

- **Persistent scope**: Enforced in `coaching.sqlite3` per published clip `(source_id, publication, clip_id)` with `MAX_AUTO_REVIEWS_PER_CLIP = 2`.
- **Reviews vs suggestions**: The cap limits automatic coaching **reviews** (`auto_detect: true` export + AI request pairs) to 2 per clip. Each AI review may still return up to 6 structured coaching suggestions (`annotations`). Manual **Ask coach** and **Re-ask** (`follow_up_of`) requests have `auto_detect: false` and neither consume nor are blocked by the automatic review cap.
- **Slot lifecycle across `render` and `ai` jobs**:
  - Inside `Coaching.create` under `BEGIN IMMEDIATE`:
    1. **Idempotency first**: If `request_id` already exists with the same `fingerprint`, returns the existing job without consuming an additional slot; if `fingerprint` differs, raises `QueueConflict` (`409`).
    2. **Active/consumed slot count** (`active_auto_coach_slots(db, source_id, publication, clip_id, exclude_export_id=None)`):
       - Counts `ai` jobs for `(source_id, publication, clip_id)` with `auto_detect = true` whose state is `'queued'`, `'leased'`, `'calling'`, `'completed'`, `'needs_review'`, or `'failed'` after crossing `'calling'` (`result.provider_called = true`).
       - Plus `render` jobs for `(source_id, publication, clip_id)` with `auto_detect = true` whose state is `'queued'`, `'leased'`, or `'completed'`, whose `id != exclude_export_id`, and for which **no** `ai` job exists with `body.export_id = render.id`.
    3. **Atomic reservation and transition**:
       - On `POST /api/coaching/exports` with `auto_detect: true`: if active slots $\ge 2$, raises `QueueConflict('Auto coach is limited to 2 reviews per clip')` (`409`). Otherwise inserts the `render` job (`auto_detect: true`), reserving 1 slot immediately.
       - On `POST /api/coaching/requests`: inside `Coaching.create`'s `BEGIN IMMEDIATE` transaction, re-reads the parent `render` job row and verifies it still exists with `role == 'render'`, `state == 'completed'`, and matching `source_id`, `publication`, `clip_id`, `artifact_directory`, `player_context`, and `auto_detect` (preventing a concurrent `cancel` vs `requests` race from queuing an `ai` job against a cancelled export). When `auto_detect: true`, passes `exclude_export_id = render.id` into `active_auto_coach_slots` and verifies no active/completed/post-call `ai` job already references `render.id`, atomically transferring the reserved slot from `render.id` to the new `ai` job.

### Failure, cancellation, reopen, and `needs_review` semantics

| Outcome | Slot impact | Rationale |
| --- | --- | --- |
| Scan finds 0 windows or is cancelled during scan | **0 slots used** | No export or AI job created. |
| `render` job fails (`failed`) or is cancelled (`cancelled`) | **Slot released** | Failed/cancelled before any provider call; user can retry. |
| `ai` job cancelled while `queued` or `leased` | **Slot released** | Cancelled before provider boundary (`calling`). |
| `ai` job fails during worker preflight while `leased` (before `calling`) | **Slot released** | Local preflight error (`provider_called: false`); provider was never called. |
| `ai` job enters `calling`, `needs_review`, `completed`, or fails after `calling` (`provider_called: true`) | **1 slot permanently consumed** | Provider boundary was crossed; never auto-retry a paid call. |

- **Reopen and multi-tab recovery**:
  - `GET /api/coaching/sessions/<source_id>` returns all `ai` jobs, all active/unsubmitted `auto_detect` `render` reservations (`state IN ('queued', 'leased', 'completed')` without a linked `ai` job, even if older than the 100-row general render history cap), and the 100 most recent other jobs.
  - `#coach-history` renders in-flight and failed automatic `render` jobs that do not yet have an `ai` job, alongside `ai` jobs, so closing and reopening Clip Study preserves truthful progress and cancellation controls.
  - When `refresh()` observes a `completed` automatic `render` job for the current clip and publication with no corresponding `ai` job, `advancePendingAutoExports` checks `owner === current && gen === generation` before each submission and on every resolved or rejected async boundary (with `failedAutoExports` and `submittingAutoExports` scoped per active `owner` instance), and submits `POST /api/coaching/requests` with deterministic `request_id = 'auto-ai-' + render.id`.
