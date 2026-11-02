# Clip study local validation

Validated 2026-11-02 on local branch `feature/clip-deep-review`, based on main
`54e256f2c19d25df5f632dcd2c0f60e424fa2221`. This includes the new overlay player from PR 24; its player.js and watch CSS are
retained unchanged. Study clip is a clip-only action in More video actions. The results below describe the local validation performed before publication;
release CI and deployment verification are tracked separately.

## Automated checks

- Python application suite: 50 passed. Includes bounded public observations,
  malformed inputs, legacy manifests, unknown clips, publication conflicts and
  versioned media/downloads refusing a silently replaced cut.
- JavaScript helper suite: 6 passed under Node 24.22.1. Includes time rounding,
  short/long sampling, approximate source offsets, pan bounds, ZIP CRC/limits and
  secure/LAN HTTP/denied-permission clipboard policies.
- Syntax checks passed for app.js, clip-study.js and clip-study-core.js.
- Git whitespace checks passed. Dockerfile and build-context allowlist include the
  new runtime module/assets. Frontend tests were added to the existing verification
  job with a pinned setup-node action. Two study integration tests were added to
  the existing Chromium/WebKit CI suite (four parameterized cases); their syntax
  was checked locally, with equivalent flows exercised through the local browser.
  At local validation time the automated Chromium/WebKit suite, remote CI and
  container build had not run; the release workflow gates publication on them.

## Browser checks

Used a loopback-only server with an isolated synthetic three-clip library and
separate temporary state. Also checked a short existing local HDR tennis export
from worker-local storage, with no NAS writes. No media was sent to an AI provider.

- Study clip appears for an individual clip and is hidden for full-practice mode.
- Fine stepping, speed selection, loop-at-end and zoom/drag work. The parent stays
  paused and study never advances to another clip.
- Sampled six chronological unzoomed 960px frames; sampling restored position.
  The downloaded ZIP passed CRC verification and contained six JPEGs, prompt and
  timestamp context with correct source-offset estimates.
- Capture cancellation and closing during capture preserved the parent position.
  Reopening clears frames, zoom and loop, resets scroll and restores default speed.
- Copy prompt worked in the loopback browser. HTTP/denied-permission fallback
  branches are covered by helper tests; LAN HTTP clipboard behavior still varies
  by browser and offers manual selection.
- Legacy context shows an empty observation state. Synthetic published notes are
  displayed as data with score semantics. A stale publication shows a refresh
  message; unsupported video disables capture/playback and retains the download.
- Return and Escape close the modal and restore focus to More video actions.
- Desktop keeps playback controls visible while the sidebar scrolls independently.
  At 390×844, the view stacks with no horizontal overflow and frame capture works.
- Final real-media browser check had no console warnings/errors.

## Device and integration review

Physical iPhone/Safari, physical pinch gestures and cross-device HDR color handling
remain manual device checks. Viewport testing is not a claim of physical-device
coverage. Seeking is time-based, not guaranteed exact frame stepping. Canvas JPEG
colors can differ from HDR playback; the original-quality clip remains downloadable.
Processor observations only appear when published; this feature does not add pose
inference to the NAS. AI assistance is an explicit manual export/copy/open handoff,
not automatic inference, upload or saved coaching feedback.

Run the synthetic preview using the command in [Clip study](CLIP-STUDY.md).
Publish and deploy only after release CI passes and the operator authorizes the upgrade.

After rebasing, local browser checks repeated the new watch player play/pause,
clip-row activation, full-practice hiding, menu entry, precision seek, slow rate,
frame sampling, mobile layout and focus restoration. No console errors appeared.
