# Clip Study Auto Select and Zoom

Provides a local **Auto select & zoom** button (`#study-auto-detect`) and **Cancel scan** button (`#study-auto-cancel`) in the Clip Study zoom toolbar (`.study-zoom-row`) to locate active player-motion intervals and frame/pan onto the player locally in the browser without automatically submitting AI requests or exports.

## Workflow scope

- **Local selection only (`#study-auto-detect`)**: When clicked, scans `#study-video` locally in the browser using the vendored MediaPipe person detector (`static/autofocus-worker.js` + `static/autofocus-core.js`), selects up to **5** (`maxWindows: 5`) non-overlapping intervals of primary-player presence and movement, computes the union `focusBox` for each window, sets `[start, end]`, applies snapped `targetFraming` zoom (`1×`–`4×`) and pan offsets (`x`, `y`), and seeks paused playback to the selected interval start.
- **Local cycling (`Next moment (k/N)`)**: When multiple candidate intervals are found, clicking the button again cycles through all detected windows (`Next moment (1/N)` → `…`) without re-scanning or spawning a worker.
- **Explicit submission only**: Auto-detection never submits `POST /api/coaching/exports` or `POST /api/coaching/requests`. After inspecting or adjusting the interval and zoom, the user clicks **Ask coach** (`#coach-ask`) manually when ready.
- **Historical API compatibility**: Backend `/api/coaching/exports` and `/api/coaching/requests` retain the `auto_detect` fields and per-clip slot tracking (`MAX_AUTO_REVIEWS_PER_CLIP = 2`) for historical job compatibility.

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
- UI labels and status messages describe selections strictly as player-motion moments, never as detected swings or contact points.

## Bounded sampling, focus framing, and cancellation

All sampling, window-ranking, and zoom-framing math lives in pure functions in `static/autofocus-core.js` shared with Node unit tests:

1. **Bounded sample schedule (`planAutoCoachSampleTimes(duration, maxSamples = 16)`)**:
   - For clip duration $D > 0$: selects $N = \text{clamp}(\text{round}(D / 1.5), 4, 16)$ interior timestamps (for very short clips $D < 1.0\text{ s}$, $N = \text{clamp}(\text{floor}(D / 0.2), 2, 4)$).
   - For clips where uniform spacing $D / N \le 2.2\text{ s}$ ($D \le 35.2\text{ s}$ at $N = 16$), places $N$ uniform interior timestamps.
   - For longer clips where uniform spacing would exceed the $2.5\text{ s}$ motion-continuity horizon ($D / N > 2.2\text{ s}$, e.g. $41\text{ s}, 60\text{ s}, 90\text{ s}$), places $P = \max(2, \lfloor N / 2 \rfloor) \le 8$ two-sample probes ($\le 16$ total samples) centered at $c_p = D \cdot \frac{p + 0.5}{P}$ with timestamps $\text{clamp}(c_p \mp 0.5\text{ s}, m, D - m)$ and propagates the second sample's measured `motionScore` ($1.0\times$) back to the leading sample of the pair so every probe measures true primary-player velocity within the 16-frame budget.
   - Hard ceiling of **16 frames per scan**, avoiding `0` and `D` boundary edge frames.
2. **Candidate window construction and `focusBox` zoom/pan (`selectAutoCoachWindows`)**:
   - **Window span**: $W = \text{clamp}(D \times 0.3, 2.5, 8.0)$ seconds, clamped to $[0.1, \min(30, D)]$.
   - **Up to 5 non-overlapping moments**: Greedily selects up to `maxWindows` (capped at `5`) non-overlapping intervals ranked by score, returning them in chronological order (`start` ascending) with each window's padded normalized `focusBox` union (`[minX, minY, spanW, spanH]`).
   - **Zoom & pan application**: `ClipStudy.applyDetectedWindow` computes `AutoFocusCore.targetFraming(win.focusBox, ...)` snapped to quarter-step zoom (`0.25×` increments within `1×`–`4×`) and centers pan (`x`, `y`) within stage bounds.
3. **Resource bounds and cancellation**:
   - Watch-page Auto focus (`static/player.js`) disables/terminates its worker whenever Clip Study opens. Clip Study creates `Worker('/static/autofocus-worker.js')` only on click and terminates it immediately in `finally`.
   - Timeouts: seek `6000 ms`, per-frame inference `8000 ms`, worker init `30000 ms`, total scan watchdog `45000 ms`.
   - Clicking **Cancel scan** (`#study-auto-cancel`), changing clips, or closing `#clip-study` aborts the scan controller, terminates the worker, and restores pre-scan playhead and `[start, end]`.
   - If WebGL2/`OffscreenCanvas` is unavailable (`unsupported` / `worker_graphics_unavailable`), `#study-auto-detect` switches to disabled **Auto select unavailable**, while manual interval/zoom selection and **Ask coach** remain enabled.
