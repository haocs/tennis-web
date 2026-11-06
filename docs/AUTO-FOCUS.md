# Desktop player Auto focus

Local implementation of [issue 70](https://github.com/haocs/tennis-web/issues/70).
Entry control: **Auto focus** (`#video-autofocus`) in the desktop review player (`#video-stage`).

## Goals and scope boundaries

Auto focus is an opt-in, browser-local playback aid for the desktop review player that automatically pans and zooms `<video id="player">` toward the primary (nearest/largest) player on court.

To preserve the Courtside system architecture and domain model (`courtside/docs/architecture.md`, `courtside/docs/domain-model.md`):

- **Browser-only ephemeral view state**: Detection and camera framing run entirely inside the user's browser during playback. Auto focus never alters published video files, session manifests, clip boundaries, or `website.sqlite3`.
- **No media or frame uploads**: Video frames are sampled into a small offscreen canvas in memory, transferred as raw pixel buffers to a same-origin Web Worker (`worker-src 'self'`), and discarded immediately after each inference pass. Zero network requests are made to external services or CDNs.
- **No new server queues or persisted annotations**: Auto focus does not create Moments, entity metadata, or coaching jobs.
- **Explicit non-goals (future work per #70)**: Click-to-select-and-follow a specific player, automatic Moment/annotation generation from detections, and server-side processing reuse remain out of scope for this change.

## Vendored runtime, model, and CSP contract

Because Courtside runs on a local-network NAS over plain HTTP (`http://<nas-host>:...`), Auto focus cannot rely on secure-context-only APIs or runtime CDN downloads. All inference assets are vendored under `static/vendor/mediapipe/`:

| File | Source / Version | License | Purpose |
| --- | --- | --- | --- |
| `vision_bundle.js` | `@mediapipe/tasks-vision@0.10.14` | Apache-2.0 | MediaPipe Tasks Vision Web Worker runtime |
| `vision_wasm_internal.js` | `@mediapipe/tasks-vision@0.10.14` | Apache-2.0 | Emscripten WebAssembly loader |
| `vision_wasm_internal.wasm` | `@mediapipe/tasks-vision@0.10.14` | Apache-2.0 | SIMD WebAssembly inference kernel |
| `efficientdet_lite0.tflite` | Google MediaPipe `efficientdet_lite0/int8/1` | Apache-2.0 | Quantized Int8 person/object detector |
| `LICENSE` / `MANIFEST.json` | Upstream Apache-2.0 notice + SHA-256 pins | Apache-2.0 | Provenance and integrity verification |

### Content Security Policy and MIME types

1. `app.py` registers `.wasm` (`application/wasm`), `.js` (`text/javascript`), and `.tflite` (`application/octet-stream`) explicitly in Python's `mimetypes` registry so `python:3.12-slim` containers serve all vendored assets with strict `X-Content-Type-Options: nosniff` compliance.
2. `app.py` sets the default Content Security Policy to:
   ```http
   default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'
   ```
   `'wasm-unsafe-eval'` permits WebAssembly compilation inside the same-origin worker without allowing arbitrary JavaScript evaluation (`'unsafe-eval'`).

## Off-thread bounded inference architecture

Auto focus splits responsibilities across three modules:

1. **`static/autofocus-core.js` (pure math & target selection, shared with Node unit tests)**:
   - Normalizes raw detector bounding boxes into `[0, 1]` video coordinates.
   - Selects the primary player (`selectPrimaryPerson`) using a proximity/size score (`sqrt(area) * 1.6 + bottom * 0.45 + score * 0.1`) that prioritizes larger and lower-in-frame (closer to camera) person detections while applying a bounded temporal continuity hysteresis bonus (`iou` + center-distance affinity when candidate size is comparable) so similar-sized candidates do not flip-flop, while a clearly larger/closer player entering the frame decisively takes focus.
   - Computes target zoom $z \in [1.0, 2.4]$ (`minZoom = 1.0`, allowing neutral `1×` framing when a close player already fills the target frame height/width without cropping head or feet) and pan offsets $(x, y)$ clamped via `panBounds(stageWidth, stageHeight, videoWidth, videoHeight, z)` so the transform never exposes blank edges outside the rendered video rectangle.
   - Implements `AutoFocusTracker`, a stateful smoother with exponential damping, snap-to-target convergence thresholds, short-term occlusion hold ($\le 750\text{ ms}$), and graceful ease-back to neutral framing (`1×`, `0, 0`) on sustained target loss.
2. **`static/autofocus-worker.js` (dedicated Web Worker)**:
   - Lazily created when the user enables Auto focus on desktop.
   - Requires WebGL2 in an `OffscreenCanvas` for MediaPipe image upload, even though model inference uses the CPU delegate. The worker probes and supplies that canvas explicitly. A browser port without this capability reports a permanent unsupported state while ordinary playback remains available.
   - Loads `/static/vendor/mediapipe/vision_bundle.js` and initializes `ObjectDetector` with `modelAssetPath: '/static/vendor/mediapipe/efficientdet_lite0.tflite'`, `delegate: 'CPU'`, `runningMode: 'IMAGE'`, `categoryAllowlist: ['person']`, `scoreThreshold: 0.22`, and `maxResults: 6`.
   - Processes `{type: 'detect', epoch, seq, width, height, buffer}` messages and replies with `{type: 'result', epoch, seq, detections}` or `{type: 'error', message}`.
3. **`static/player.js` integration**:
   - Adds `#video-autofocus` to `.video-view-actions` on desktop (`@media (max-width: 650px), (pointer: coarse)` hides the button, and viewport/media-query changes to mobile/coarse layout automatically disable and tear down any active Auto focus session).
   - Samples `<video id="player">` into an offscreen canvas (`maxEdge = 384`) at most once every `180 ms` (~5.5 Hz) while playing (`!video.paused && !video.ended`), plus a single forced capture on worker `ready`, `loadeddata`, `canplay`, or `seeked`.
   - **Single in-flight gate & sequence ownership**: Tracks `afInFlightSeq` per worker request and never sends overlapping `detect` messages. Source changes (`loadedmetadata`, `emptied`) and `seeking` increment `afEpoch` and reset framing immediately while preserving `afInFlightSeq` busy ownership until the matching `seq` replies (discarding stale-epoch detections and triggering one fresh capture if needed). All worker callbacks verify `worker === afWorker`.
   - **Smooth transform updates**: A `requestAnimationFrame` loop interpolates the current `(zoom, x, y)` state toward the tracker target and writes `video.style.transform = translate(Xpx, Ypx) scale(Z)`.

## Lifecycle, cleanup, and failure states

- **Disable / reset**: Clicking `#video-autofocus` while active immediately sets `aria-pressed="false"`, cancels sampling, watchdog, and animation loops, increments `epoch`, terminates the worker, and resets `video.style.transform = ''`.
- **Clip change, seek, and modal handoff**:
  - `emptied`, `loadedmetadata`, and `seeking` increment `afEpoch` and reset the tracked target state (`tracker.reset()`) so camera framing starts cleanly on the new cut or seek position.
  - Opening `Clip study` (`#clip-study[open]`), switching to a mobile/coarse viewport, navigating away from the review page, or calling `ReviewPlayer.controller.destroy()` disables/terminates Auto focus and restores neutral framing.
- **Explicit unsupported / recoverable failure state**:
  - If `Worker` or `WebAssembly` is unsupported, `#video-autofocus` sets `aria-pressed="false"`, `disabled = true`, and `data-autofocus-state="unavailable"`.
  - If worker/model initialization or inference fails or exceeds bounded initialization/inference watchdogs, Auto focus terminates the worker, resets `video.style.transform = ''`, transitions `#video-autofocus` to `aria-pressed="false"` and `data-autofocus-state="unavailable"`, and surfaces a visible error via `onError('Auto focus is unavailable in this browser.')` while keeping the control recoverable on subsequent user retry.
  - Initialization allows up to 30 seconds for a cold runtime/model download and compilation. The user can disable Auto focus while it loads; inference has its own shorter watchdog.

## Verification boundaries

Deterministic detections test framing and player lifecycle behavior. A separate browser smoke test loads the shipped runtime/model under the real CSP and processes local fixture pixels. Recognition of hand-drawn figures is not treated as a portable ground-truth benchmark. Real-tennis validation uses private local playback outside the public fixtures; no user footage is committed.

The reference Chromium smoke must complete real inference. WebKit ports that cannot create worker WebGL2 must report the specific unsupported capability and retain working playback; arbitrary runtime errors still fail the test. See the [upstream vision task runner](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/web/vision/core/vision_task_runner.ts) for its canvas and GPU-buffer image-input contract.
