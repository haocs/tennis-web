# Moment panel

Agreed revision, 2026-11-05. Implemented locally; not released or deployed.
This supersedes the previous watch-page Moments controls and teaser behavior.
The serving entity definitions in [ENTITY-MODEL.md](ENTITY-MODEL.md) still apply.

## Scope and current defects

The watch page has a **moment panel**, directly below playback. It contains only
Moments relevant to the currently watched Clip or Video. It is not a general
metadata browser.

Current implementation problems:

- The “All moments” view bypasses clip filtering and shows moments elsewhere in
  the recording. Remove this view entirely, not just rename it.
- “Notes & details” mixes entity metadata into the moment panel. Remove this view
  from the panel as well. Untimed entity notes and usefulness stay with the entity.
- Clip matching alone is not the scope definition. Use original-time intersection
  and current published playback mapping consistently for rows, markers and Jump.
- The controls disappear when collapsed; highlights currently create empty teasers.

## Always-visible bar and expansion

Compact mobile and desktop bar:

`Moments ⌄       Highlight       Jump       + Moment`

- **Moments** is the disclosure control. Initially collapsed; expanding reveals
  the list and its filters below the bar. The panel surface/heading can expand it;
  clicks on an action or filter must not toggle the disclosure accidentally.
- **Highlight** immediately saves a highlight at the current original timestamp.
  Keep existing same-timestamp deduplication and retry/concurrency protections.
- **Jump** seeks to the next active moment in the current playback scope, regardless
  of type. Do not jump into another clip while watching a single clip.
- **+ Moment** (accessible name “Add moment”) opens the moment editor at the
  current timestamp. All three actions remain available when collapsed.
- Keep keyboard access, visible focus and accessible expanded state. Do not put
  action buttons inside another button or competing disclosure activation area.
- Avoid explanatory paragraphs in the interface. Use concise empty/error states.

Jump visibility and end behavior:

- Hide Jump if there are no active, seekable moments in the current Clip/Video.
  Archived-only moments do not make it visible.
- Select the first moment strictly after the playhead. At the last moment, wrap
  to the first in-scope moment. This keeps Jump useful when the scope has moments;
  it never advances into a different Clip or recording.
- Coincident moments share one jump destination; do not get stuck on the same
  timestamp. An interval already underway is not a later destination.
- Seek without automatically starting or pausing playback; preserve playing state.
  No unexpected focus or page-scroll changes.

## One current-range scope; no “All moments” view

There is no clip/all/notes scope dropdown or recording-wide escape from Clip mode.
Type and archive filters narrow the current scope; they never broaden it.

Canonical timing is integer milliseconds relative to the Original recording.
Player-relative time is a projection, never a saved anchor.

- **Clip mode:** scope is the selected Clip's original half-open range `[start,end)`
  in its current Video version. A point is included when `start <= t < end`.
  An optional-end Moment is included when its range intersects the Clip with
  positive duration: `moment.start < clip.end && moment.end > clip.start`.
- **Full Video mode:** scope is the union of original ranges retained by the
  current published Video. Do not use a bounding envelope that includes removed
  gaps. The selected chapter does not shrink full Video scope.
- A Moment spanning a clip boundary is relevant to both overlapping Clips. Display
  it once per panel; preserve its original start/end and show partial coverage
  concisely when necessary. Jump/markers use the first visible in-scope fragment.
- A point exactly at a boundary belongs to the following Clip, not both. A point
  at the final exclusive endpoint has no playable position; creation clamps to
  the last valid millisecond rather than saving an unreachable endpoint.
- Exclude moments from other recordings and moments wholly outside retained
  footage. Reprocessing never deletes them or moves them to a nearby timestamp.
  Historical management/export remains available separately from this panel.
- Recompute immediately after a Clip/Video switch or publication change. Clear
  old rows/teasers while loading; ignore stale responses for the previous context.
- Preserve existing subject/player eligibility and coaching evidence rules. A
  range match alone must not misattribute one player's coaching to another.

The same range projection drives list membership, timeline markers, navigation
and teaser eligibility. Archived moments may appear in the filtered list only;
never in timeline markers, Jump destinations or automatic teasers.

## Expanded list and filters

Expanding shows chronological moments within the current scope, below the bar.
Rows provide time, type, optional text and the relevant edit/review/archive actions.
Keep coaching's structured response and pinned evidence accessible.

- **Type:** All types (default), Highlight, Coaching, and other supported Moment
  types present in the data. “All types” is a type filter, not “All moments”.
- **Archived:** hidden by default; an explicit “Show archived” toggle includes
  archived rows without enabling their playback behavior.
- Filters affect only the list. They do not remove valid playback markers or
  change Jump's navigation through active moments in the current scope.
- No untimed Session/Video/Clip notes, usefulness records or cut reports in this
  list. No “Notes & details” or “All moments” view.
- Empty filtered list: “No moments”. Keep creation actions available.

## Add and edit a moment

- Capture recording ID, publication, playback scope and original timestamp when
  opening the editor. Subsequent playback/context changes cannot redirect a save.
- Required start time defaults to the current playhead; allow editing it.
- Optional fields: end time, note text and relevant existing subject/player fields.
  End time must be later than start and within the Original recording duration.
- A note is text in a Moment's payload, not a new Note entity or a separate
  note-versus-interval creation flow. A Moment with an end remains a Moment.
- The simple user-created Moment can reuse the existing `comment` wire value for
  compatibility; the interface calls it “Moment”. Highlight remains its quick
  action/type; coaching retains its typed payload and evidence workflow.
- Existing entity notes remain entity metadata and editable outside this panel.
  Do not automatically convert untimed notes or usefulness into Moments.
- A new Moment must intersect the captured scope. An optional end may span other
  Clips in the same Original recording; this does not change its canonical anchor.
- Keep validation, optimistic concurrency, idempotent creation, safe text rendering,
  archive/restore and revision history. A stale publication requires refresh/review,
  not a silent retarget. No automatic training-label export.

## Teasers below playback

A **moment teaser** is the temporary text cue shown when playback reaches a Moment.
It is separate from the expandable list and can appear while the panel is collapsed.

- Only show a teaser when the eligible Moment has meaningful, non-whitespace text
  after payload formatting. Examples: a coaching excerpt or a Moment's note.
- A textless highlight produces **no teaser**, empty card, “Highlight” banner or
  announcement. Its progress-bar marker and list entry remain visible.
- A highlight with an explicitly added note can show that note; the gate is text
  content, not type. A formatter fallback alone is not meaningful coaching text.
- Point text defaults to 3 seconds of wall-clock display, including when paused
  or using a different playback speed. An explicit end follows playback time,
  bounded by the current visible fragment. Leaving the scope clears the teaser.
- Seek shows only eligible content at the landing position, never all skipped
  Moments. Replaying can trigger it again. Overlapping text Moments stay compact,
  do not overlay the footage, and never pause playback or scroll/focus the page.
- Keep readable structured coaching formatting and existing reviewed-suggestion
  precedence. A whole-review excerpt is selection context, not fabricated precise
  technique timing. Preserve raw responses, snapshot mappings and evidence.

## Entity metadata stays separate

**Less useful** is metadata of a specific Clip or Video version, never a Moment.
It must not appear in this panel, type filters, timeline, Jump or teasers. Keep the
existing entity-level action outside the panel, with the correct watched entity
as its target. Do not copy the value into replacement Clips/versions on reprocess.
Untimed entity notes belong in entity editing. Bad-cut reports remain processing
annotations in the processing-feedback workflow.

## Implementation sequence and acceptance checks

1. Introduce one shared current-range selector/projection. Test points, spanning
   Moments, adjacent boundaries, removed gaps, archived rows, player constraints,
   reprocess and context changes. Remove both old scope views.
2. Rebuild the persistent bar and expanded list/type/archive filters. Verify
   independent action/disclosure behavior on mobile and desktop.
3. Add the unified Moment editor, retaining existing records/history and coaching
   editing contracts. Verify optional-end validation and captured save context.
4. Replace Next highlight with scoped Jump, unique destinations and wrap behavior.
5. Gate teasers on meaningful text; retain markers for textless highlights. Test
   paused playback, speed changes, seeking, overlap and default expiry.
6. Run backend/helper/browser checks. Verify entity notes/usefulness and processing
   feedback remain functional outside the panel; no media or metadata loss.

This change does not require a new Note or Interval entity, a new database solely
for the panel, worker changes, NAS permission changes or an automatic migration
of entity metadata into Moments. Confirm existing contracts support optional-end
editing; make any necessary API adaptation additive. Deployment is a separate step
after implementation and verification, not part of this design-only change.


## Local implementation and verification

- `static/moments-core.js` supplies the shared current-range projection, scoped
  Jump selection and meaningful-text check. Saved original timestamps are unchanged.
- `static/notes.js` renders the persistent bar, current-range list and unified
  optional-end editor. Entity notes use a separate Clip/Video notes dialog opened
  from the player's menu; usefulness remains an independent entity action.
- The existing Moment API now accepts an empty note for timed Moments. Untimed
  entity notes still require text. Point targets use half-open recording bounds.
  No database migration or copying of historical records is required.
- Verification: 110 backend tests and 25 frontend helper tests passed. The complete
  Chromium/WebKit suite passed 76 tests; two additional keyboard Jump tests passed
  for playing-state, focus and scroll preservation (78 browser tests in total).
- Mobile layouts were checked at 320/390 px and desktop at 1280 px. Test media and
  isolated website state were used. No production records, NAS configuration,
  release tags or published images were changed.


Follow-up #64/#65 verification: 114 backend, 25 helper and 84 Chromium/WebKit
browser tests passed with the combined local changes. After the requested removal
of the reaction pill/divider, eight targeted mobile/button tests passed again.
Like/Dislike are transparent icon buttons matching Study/Download; selected thumbs
use a filled icon. They remain outside the moment panel. Nothing was deployed.
