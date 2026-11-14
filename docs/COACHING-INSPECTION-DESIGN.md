# Clip Study AI Coach Inspection Design & Acceptance Checklist

First-release design approved for **Clip study** review clarity across desktop and mobile (`haocs/tennis-web`).

## 1. Release Scope & Explicit Deferrals

### Included in First Release
1. **Evidence-backed primary-frame & body-area inspection**:
   - Versioned optional suggestion fields (`schema_version: 3` while preserving `schema_version: 2` compatibility): concise `now_summary`, `primary_frame_id`, controlled `stroke_phase`, side-agnostic `body_areas`, and optional normalized `focus_region`.
   - Optional, fault-isolated worker-side pose extraction (`pose.json`, `pose_status`) on exported coaching stills during `render`, stored in a separate `evidence_enrichment` table without altering immutable `evidence` manifests or failing normal coaching exports.
2. **Compact `Now` / `Try` cards, navigation, and timeline markers**:
   - Compact **Now** (`now_summary` or concise fallback) + **Try** (`cue`) summary at the top of each suggestion card, keeping full `Observed`, `Practice`, and `Uncertainty` accessible without crowding small mobile viewports.
   - `← Prev` / `Suggestion k of N` / `Next →` navigation bar + **Body guide** toggle (`aria-pressed`) + **Exit inspect** restore control.
   - Non-blocking timeline markers inside `#study-interval-bar` for current-clip suggestion ranges and primary evidence stills.
3. **Explicit opt-in movement loop & roll-freeze-roll walkthrough**:
   - **Loop moment** loops a single selected `applies_to[rangeIndex]` range.
   - **Walkthrough** plays a single selected range in slow motion (`0.35×` with `0.5×` fallback), briefly pauses (`1.2s`) on `primary_frame` to show the gated body guide + `Now` / `Try` HUD, then rolls through the end of that single range and loops until cancelled.
   - Disjoint `applies_to` ranges remain strictly independent selectable moments (never merged or looped across `[min(start), max(end)]`).
   - Inspection never mutates the saved export crop, interval `[start, end]`, or speed for the next **Ask coach** or **Download interval** request.

### Explicitly Deferred (Not in This Release)
- Target/"ideal" green ghost skeletons, comparative reference overlays, numeric degree targets, or 3D biomechanical angles.
- 3D animated mannequin lessons or personal-reference side-by-side clip comparison.
- Dense video-rate skeleton playback overlays or linear trail interpolation across un-sampled frames.
- Inferred center of mass, racket trajectory from wrist landmarks alone, or exact ball-contact timing claims from sparse stills.
- Automatic promotion of coaching reviews/annotations to processing/benchmark training truth (`training_label_status` stays `"not_released"`).

---

## 2. Coordinate & Clock Contracts

### Time Mappings
- **Original recording clock** (`original_seconds`) is canonical across reprocessing (`annotations.py`, `coaching_annotations.py`).
- **Clip-local clock** (`local_seconds`): `clamp(original_seconds - clip.original_start_seconds, 0, clip.duration_seconds)`.
- **Disjoint `applies_to` ranges**: Each element `{start_seconds, end_seconds}` in `applies_to` is projected independently against `[clip.original_start_seconds, clip.original_end_seconds]`. Focusing or walking through a suggestion operates on one explicit `rangeIndex`, never a bounding union across disjoint gaps.

### Spatial Coordinate Spaces
All spatial metadata uses normalized `[0, 1]` coordinates with origin at the top-left of the **display-oriented** frame (respecting container rotation metadata):

1. **Evidence-frame space (`[ex, ey]`)** → **Display-oriented clip space (`[clipX, clipY]`)**:
   Given frozen render recipe `crop = [cx, cy, cw, ch]` (`default [0, 0, 1, 1]`):
   $$\text{clipX} = c_x + e_x \cdot c_w, \qquad \text{clipY} = c_y + e_y \cdot c_h$$
   A cropped bounding box `[bx, by, bw, bh]` maps to `[cx + bx * cw, cy + by * ch, bw * cw, bh * ch]`.
2. **Letterbox content box (`object-fit: contain`)**:
   Given rendered container `(CW, CH)` and display-oriented intrinsic media dimensions `(MW, MH)`:
   $$\text{scale} = \min(CW / MW, CH / MH), \qquad W = MW \cdot \text{scale}, \qquad H = MH \cdot \text{scale}$$
   $$\text{left} = (CW - W) / 2, \qquad \text{top} = (CH - H) / 2$$
   Evidence `<img>` thumbnails show the already-cropped evidence still directly, so `[ex, ey]` projects directly into the image's `letterboxRect`.
3. **Stage zoom & pan transform (`#study-stage` + `#study-video`)**:
   `#study-video` uses `transform-origin: 50% 50%` and `transform: translate(tx, ty) scale(zoom)` (bounded by `ClipStudyCore.panBounds`). A full-clip normalized point `(clipX, clipY)` maps to stage pixel coordinates:
   $$x_{\text{stage}} = \frac{CW}{2} + (\text{clipX} - 0.5) \cdot W \cdot \text{zoom} + t_x$$
   $$y_{\text{stage}} = \frac{CH}{2} + (\text{clipY} - 0.5) \cdot H \cdot \text{zoom} + t_y$$
   Overlay elements sit inside `#study-stage` (`overflow: hidden`) so pan/zoom stays synchronized and never leaks outside the stage.

---

## 3. Honest Biomechanics, Confidence & Identity Gating

1. **No over-claimed precision**:
   - Pose landmarks are **approximate 2D monocular still-frame estimates**, labelled clearly as `"Approximate body guide"`.
   - Prompts and UI explicitly avoid claiming pixel-exact joint angles, 3D kinematics, center of mass, racket path from wrist alone, or contact timing from sparse stills.
2. **Multi-stage gating (`ClipStudyCore.resolveSparseOverlay`)**:
   - **Sparse-frame temporal lock**: Overlays render **only** on their matching evidence thumbnail OR while `<video>` is **paused**, `!historical`, and `|video.currentTime - frame.clip_seconds| <= frameTolerance` (`0.045s` or half frame step). Starting playback immediately clears the stage overlay so no stale skeleton ever floats over moving video.
   - **Intended-player ambiguity suppression**: In worker pose extraction, if `person_count > 1` and either `subject_hint` is non-empty (pose geometry cannot verify shirt color/appearance cues) OR the second-most prominent detected person scores `>= 0.75 * best_score`, `subject_ambiguous` is set to `true` and joints are omitted/suppressed rather than highlighting the wrong player.
   - **Per-joint confidence & visibility bounds**: Joints require `confidence >= 0.65` and normalized coordinates strictly inside `[0.02, 0.98]` (suppressing offscreen or edge-clipped joints).
   - **Handedness unknown**: Side-agnostic `body_areas` (`hitting_arm`, `off_arm`, `shoulders_chest`, `hips_trunk`, `knees_legs`, `feet_stance`, `head_eyes`) avoid guessing left vs right when handedness is unverified. For `hitting_arm` / `off_arm`, the overlay highlights symmetric upper-arm cues only when confident or falls back to `focus_region` / unadorned image + text cues.
   - **Graceful fallback**: When pose is unavailable, disabled, ambiguous, or low-confidence, the UI displays the original evidence frame and text without fabricating geometry or posture lines.

---

## 4. Schema Backward Compatibility & Server Validation

- `worker/tennis_coach/annotations.py` emits `PROMPT_VERSION = 'tennis-coach-evidence-v3'` and `SCHEMA_VERSION = 3` for new AI requests while accepting both `schema_version: 2` and `schema_version: 3` in `parse_response()` and `normalize_suggestion()`.
- New v3 suggestion fields use concrete JSON Schema types compatible with OpenAI strict mode (`additionalProperties: false`, all keys in `required`) AND Gemini/Antigravity OpenAPI subset (`gemini_compatible_schema()`, no `anyOf`/`null` unions): empty string `""` and empty array `[]` act as omitted/uncertain sentinels.
- Server validates every timestamp, finite number, `primary_frame_id` membership in retained `evidence_frame_ids` / `manifest['frames']`, controlled vocabularies (`stroke_phase`, `body_areas`), and bounded `focus_region` geometry. Invalid references are stripped with visible warnings on AI proposals (`partial` status) and rejected with HTTP `400` when accepting (`strict=True`) or creating manual annotations.
- `evidence_manifest()` and historical rows in `evidence`, `annotations`, `annotation_revisions`, and `jobs` remain **immutable**. Accepted suggestions continue projecting into playback Moments (`kind: 'coach'`) with `training_label_status: 'not_released'` and explicit re-acceptance required after reprocessing (`historical: true`, `playback_eligible: false`).

---

## 5. Worker-Side Optional Pose & Non-Mutating Inspection Rules

- Render worker `0.6.0` runs optional, fault-isolated pose inference (`worker/tennis_coach/pose.py`) on extracted `frame-*.jpg` stills only when `intent != 'share'`, `pose_extraction` is enabled, and `mediapipe` (`Apache-2.0`) + verified `pose_landmarker_lite.task` (`Apache-2.0`, SHA-256 checked against `MANIFEST.json`) are present.
- All pose exceptions, missing runtime, or timeouts (`8.0s` budget) degrade cleanly to `pose_status: 'unavailable'` without failing any render/coaching export.
- `Dockerfile` copies only `annotations.py` — zero extra dependencies or CPU inference run on the NAS web container.
- Polling `/api/coaching/sessions/<source>` strips bulky `pose_artifact` blobs; the browser lazy-loads pose data via `GET /api/coaching/evidence/<job_id>/pose` only when the user inspects a frame.

---

## 6. Acceptance Checklist

### Contracts & Worker
- [x] `schema_version` 2 & 3 both parse, validate, edit, accept, dismiss, restore, and export cleanly; unstructured legacy notes retain text/frame fallback.
- [x] `gemini_compatible_schema(response_schema())` emits no `additionalProperties`, `anyOf`, or `null` types.
- [x] Invalid `primary_frame_id`, non-finite/out-of-bounds/undersized `focus_region`, unknown `stroke_phase`, and unknown `body_areas` strip cleanly with warnings and block acceptance until corrected.
- [x] `validate_pose_artifact()` rejects out-of-bounds/NaN coords, unknown files, wrong SHA-256, or oversized payloads (`400`); `/api/coaching/sessions/<id>` omits raw pose payload while `/api/coaching/evidence/<id>/pose` lazy-loads it.
- [x] Pose unavailability/error in `render()` never fails coaching export (`pose_status: 'unavailable'`).
- [x] Ambiguous multi-person scenes / `subject_hint` ambiguity suppress joint skeletons cleanly.
- [x] Historical `evidence` manifests remain immutable; reprocessed clips keep `historical: true`, `playback_eligible: false` until explicitly accepted for the new publication; accepted Moments keep `training_label_status: 'not_released'`.

### Coordinate & Time Geometry
- [x] Unit tests verify `evidenceToClipPoint` / `evidenceToClipBox` with cropped and uncropped exports (`[0,0,1,1]` and zoomed sub-crops).
- [x] Unit tests verify `letterboxRect` and `projectPointToStage` across 16:9, 9:16 portrait, zoom (`1×`–`4×`), and pan transforms.
- [x] Disjoint `applies_to` ranges remain independent (never merged/unioned in `applyPendingFocus`, timeline markers, Loop range, or Walkthrough).

### Playback, Mobile/Desktop UX & Accessibility
- [x] Inspecting a suggestion, looping a range, or running Walkthrough never mutates saved export crop, `[start, end]`, or speed for `Ask coach` / `Download interval`; exiting inspection restores user state.
- [x] Sparse-frame overlay appears **only** when paused on a matching cited frame and passing confidence + identity gates; clears immediately on `play`.
- [x] Toggleable `Body guide` (`aria-pressed`), zero hover-only controls, `>= 40px` (`44px` mobile) touch targets, zero horizontal overflow (`scrollWidth <= clientWidth`) on `320px`, `390px`, `844px`, and `1280px`.
- [x] Ordinary `Play` never auto-pauses; Walkthrough is explicit opt-in, handles rejected `play()` Promises, pauses/cancels on `visibilitychange`, dialog close, manual seek/play/step/pin/zoom, or suggestion switch, and honors `prefers-reduced-motion: reduce`.
- [x] Mobile `Coach` view auto-reveals collapsed player when user taps `Inspect frame`, `Loop moment`, or `Walkthrough`.

### Privacy, Release & Operations
- [x] Zero private footage, real AI calls, IPs, credentials, or local paths in diffs, tests, commits, or public docs.
- [ ] Node tests, backend `pytest`, Playwright Chromium/WebKit desktop & mobile tests, Compose configs, and `scripts/container-smoke.sh` all pass.
- [ ] Fresh immutable release tag + multi-arch GHCR digest verified; `haocs/courtside` docs updated; Mac coaching workers (`render` & `ai`) upgraded to `0.6.0` cleanly without touching `local.tennis-api-worker` or NAS originals; deployment status or exact blocked NAS step reported truthfully.
