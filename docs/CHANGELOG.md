# Changelog

## v0.15.0 — 2026-11-05

- #64: Match playback speed control size to fullscreen, use a transparent
  background, and remove the persistent fullscreen instruction.
- #65: Pair thumbs-up/down in the player for Clips and full practice. Selecting
  one clears the other atomically, retaining downvote revisions and legacy likes.
  Down remains version-bound entity metadata, outside the Moment panel.

- Keep the moment panel, playback markers and Jump within the current Clip or
  Video's retained timeline; remove the All moments and Notes & details views.
- Keep Highlight, Jump and Add moment available while the panel is collapsed.
  Add a current-time Moment with optional end and note; filter the list by type
  and archived status. Jump includes all active Moment types and wraps in scope.
- Show playback teasers only for Moments containing text. Textless highlights
  remain timeline markers without displaying empty cards.
- Keep entity notes in a separate Clip/Video notes dialog and usefulness outside
  the moment panel. Preserve original-time anchors, evidence and revision history.

## v0.8.0 — 2026-11-02

- Guide coaching through Choose moment, Preview, Ask coach and Review suggestions.
- Save structured observations, cues, drills and uncertainty with original-recording intervals and exact submitted snapshots.
- Accept, edit or dismiss individual suggestions with conflict checks and revision history; retain provenance after reprocessing.
- Deduplicate retained evidence separately from temporary previews, and export annotations, raw responses and revisions.
- Add coaching worker 0.2.0 with capability-gated structured requests and evidence verification before provider calls.
- Preserve legacy notes and the latest expanded player/native-navigation fixes.


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
