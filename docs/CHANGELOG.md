# Changelog

## v0.3.0 — 2026-10-30

- Home practice titles use the saved court name plus original recording date and time of day.
- Saved courts support original GPS/optional altitude, adjustable matching ranges, unique GPS recognition, ambiguous-match suggestions, and explicit practice selections.
- Court edits and multi-recording selections protect concurrent visitors with optimistic versions and atomic writes.
- Single-recording and multi-recording practices use the same card component, title format, recording count, clip count, and individual recording links. This fixes [issue #1](https://github.com/haocs/tennis-web/issues/1), implemented in source commit `1624509` and verified on the deployed image for both card types.
- Individual video-review names, existing likes/edits, and video files are preserved.

Validation: 27 application tests, GitHub container/image checks, and desktop/mobile browser checks. Saved courts and recording coordinates remain private runtime data.

## v0.2.0 — 2026-10-30

- Sort and group home practices using original recording timestamps and a configurable gap after the previous recording ends (default 30 minutes).
- Show and copy configured NAS addresses for clips and the playing file.
