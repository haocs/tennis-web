# Clip study

Local implementation of [issue 11](https://github.com/haocs/tennis-web/issues/11).
Entry button: **Study clip**. Focused view: **Clip study**. Release and NAS deployment are separate operator-controlled steps.

## Interaction design

Select an individual clip, then open **More video actions → Study clip**. This
action is hidden while the combined practice is loaded. The normal player keeps
its lightweight overlays and compact action layout from PR 24. It opens a modal focused on that clip. Desktop uses a large
video/control column and an independently scrolling context/AI column. Mobile stacks
those sections with a sticky return header. Native dialog focus trapping and Escape
are retained; returning restores the studied clip position, paused, and the parent's
original playback speed. Study never auto-advances to a different clip.

Playback starts paused at the parent's position. Controls provide 0.1-second steps,
0.01-second seek-slider increments, 0.1–1× playback, optional clip looping and mute.
Arrow keys seek 0.1 seconds; Shift+arrow seeks one second, outside form controls.
These are browser time seeks, not a guarantee of frame-exact decoding. Zoom is 1–4×
with bounded drag-to-pan, touch pinch and Reset view; zoom does not alter media.

## Watch player integration

The watch page retains its existing markup, player.js and watch styles. One
`ClipStudy.updateEntry` call in the current-video update function supplies the
menu element, session, selected clip, player and versioned media URL. The study
module creates/hides its own menu entry and owns all modal behavior. This keeps
layout work in the normal player independent of the study view.

## Processor context contract

`GET /api/sessions/<source-id>/clips/<clip-id>/study?publication=<published_at>`
returns bounded read-only public metadata. A changed publication returns 409; versioned media URLs also reject a changed
publication so a new cut cannot silently replace the studied footage. An
unknown clip returns 404. Legacy publications return an empty observation list
and truthful empty state. No raw private session files/logs or original files are read.

Published clips may optionally contain `processor_observations`, a list of records:

```json
{
  "label": "Candidate movement",
  "detail": "Wrist motion exceeded the configured threshold",
  "original_start_seconds": 12.4,
  "original_end_seconds": 14.6,
  "score": 2.6,
  "score_kind": "margin"
}
```

Times must be finite numeric values strictly ordered within that clip's original
interval. Labels are at most 120 characters, details 600. At most 200 inputs are
examined and 20 valid records returned. Optional scores must be finite, bounded by
1,000,000 in magnitude, with kind `score`, `margin` or `probability`; probability
values must be within 0–1. Invalid optional records/scores are ignored. Text is
rendered with textContent, not inserted as HTML. The endpoint returns approximate
clip-local offsets for navigation and never invents observations or confidence.
A published probability is not a claim that calibration has been verified.

Current processing publications normally provide the detector name and boundaries,
not clip-specific observations. Adding producer annotations is a separate processing
change. This feature consumes the optional contract without changing that pipeline.

## AI review preparation

The user can download the existing original-quality clip, add the current frame,
or replace their selection with six evenly spaced interior samples. Frames are
unzoomed, at most 960px on the longest edge, JPEG quality 0.82, maximum six frames.
Selection is chronological and stays in browser memory only. Sampling is cancellable,
transactional and restores playback position; closing discards frames and releases
object URLs. Failed sampling leaves the prior selection intact.

The frame ZIP contains JPEGs, review-prompt.txt and context.json. Context records
source/clip/publication identity, browser clip times and approximate original offsets;
no GPS, NAS path or private logs. ZIP generation is client-only, uncompressed,
limited to 20 MB, with no remote dependency or transcoding on the NAS. Sparse stills
are a compact first look, not sufficient evidence of complete motion or ball contact.
Use the clip when timing/footwork/contact is important. Browser canvas output of HDR
video may have different colors; the original-quality download remains available.

Copy prompt handles secure Clipboard API and LAN HTTP fallback/manual selection.
Open ChatGPT and optional Claude/Gemini links open a normal provider homepage. The
user unzips/attaches images or uploads the clip and pastes the prompt. No provider
credentials, automatic upload, automatic prompt submission or AI inference is wired
into this application. AI responses are not persisted as training annotations.

## Local validation and handoff

Run the existing Python tests plus the standalone JavaScript helper tests:

```sh
python -m pytest -q
node --test tests/frontend/*.test.cjs
```

A loopback-only preview with synthetic video is available using an installed ffmpeg:

```sh
python tools/preview_clip_study.py --root /tmp/clip-study-preview --ffmpeg /path/to/ffmpeg
```

It uses isolated library/state files and never reads or writes NAS media. Check
clip-only entry, stepping/speed/loop, zoom/pan, close/reopen/focus, no-observation and
malformed-publication behavior, capture cancellation, ZIP contents, desktop/mobile
layout and console errors. Physical iPhone/Safari/HEVC-HDR behavior still needs device
verification; desktop viewport testing alone does not establish that compatibility.

Implementation subtasks: #19 playback, #20 context, #21 AI handoff, #22 verification.
Close the parent and subtasks after integration and deployed verification; local
completion alone must not be represented as a deployed feature.

See [Local validation results](CLIP-STUDY-VALIDATION.md) for executed checks and device limits.
