# Serving entities and processing annotations

Agreed model, 2026-11-04. This supersedes the serving terminology in
ANNOTATION-METADATA.md. It does not change worker, processing-server or job contracts.

## Definitions and identity

| Entity | Definition | Reprocessing rule |
| --- | --- | --- |
| Original recording | Immutable camera input; stable content identity and original timeline. Capture metadata includes date, offset, location and duration. A storage path is a locator, not an ID. | Keep identity and timeline. A trim, time warp or replacement input is a different recording. |
| Session | One practice, with one or more videos. Independent identity, editable title/note and references to players/courts. | Keep metadata and membership. A session is never a video. |
| Video | Logical serving presentation of one original recording. Editable title, location override and notes. | Keep logical ID; select a new processed version. |
| Video version | One published processing output, with original-to-playback mapping, provenance and clips. | New ID for each publication, including identical cuts. |
| Clip | Atomic generated cut belonging to a particular video version. Editable clip notes, usefulness, favorites and player assignment are distinct fields. | New entity identity for the new version. Keep old metadata historically, without automatically copying clip notes or usefulness. |
| Moment | Typed serving knowledge anchored to an original-recording start time, with an optional later end. | Remap to overlapping current clips; never rewrite the saved original time. |
| Player / Court | Independent editable reference entities. | Keep identity. Subject references and historical coaching player context remain explicit. |
| Processing annotation | Human/processor/model evidence about a recording, output, clip or original time/range; not a serving entity. | Retain the evaluated output/run, original selector, provenance and review status. Never silently attach an old cut judgment to a new run. |

The v1 publisher has one logical Video per Original recording, using the same
source hash as their IDs in separate namespaces. Version IDs derive from the
source identity and publication token, never file modification time. Clip entity
IDs combine version ID and the publisher's clip ID. Existing raw clip IDs remain
valid URL parameters within their current recording. Future alternate renditions
will need an explicit logical-video ID in the publisher contract.

The catalog retains **observed** output manifests, not video blobs. A run replaced
before the website observes it is not reconstructed; processing-server owns the
complete task/run history. Retained descriptors do not promise historical media
is still available. Workers continue to write published files/sidecars; the web
application never writes to published media.

## Metadata, moments and processing feedback

- A note is a field of Session/Video/Clip metadata. Multiple old notes are retained
  as entries of the `notes` field, with internal keys for editing/revision history;
  they are not separate domain entities. General Video notes survive reprocessing.
  Clip notes do not transfer to new output versions.
- Usefulness is a field of a **specific Video version or Clip**. It is not a
  Moment, skill rating, or detector label. No automatic transfer, even if a new
  cut has exactly the same original boundaries.
- A bad-cut report is a processing annotation, tied to the evaluated publication,
  cut and original bounds. A bad shot can still be correctly cut and useful.
- A highlight is a Moment. A coaching review/suggestion is a Moment with a typed
  payload, pinned frame evidence, prompt/response and review provenance.
- Old timestamped notes become the `comment` Moment type in the read model. No
  text, ID, selector or revision is deleted. New untimed notes target Clip/Video;
  time-specific comments use the Moment API.
- Favorites and player assignments keep their existing policies and APIs; they
  are not silently redefined as usefulness. Explicit original-range player
  attribution can still be reused under the existing exact-boundary policy.

Moment contract: `id`, `original_recording_id`, `type`, integer `start_ms`, optional
`end_ms > start_ms`, `payload_schema_version`, `payload`, player reference,
provenance, status and revision. There is **no interval entity/type**. A moment
with an end is still a Moment. Current clip links and playback fragments are
computed, not authoritative references. Source time remains canonical; clip-local
and concatenated-playback time are only projections. Half-open ranges prevent a
shared clip boundary from producing two point markers. Active point highlights
are unique per original-recording millisecond, across concurrent requests.

A coaching response may describe several disjoint portions. Preserve its `segments`
and pinned snapshot/time mapping. Its outer start/end is the selection envelope,
not a claim that every frame in that envelope supports the advice. Unverified
legacy coaching timing stays unverified and does not acquire precise jump markers.
A moment can overlap several clips or fall entirely in removed footage; keep it
and report retained/partial/unavailable coverage. Do not move it to a nearby action.

Serving information becomes processing evidence only through an explicit
review/export decision. No automatic promotion into training truth, no automatic
rewrite of prior evaluation labels when a user edits serving metadata.

## Storage and migration

`website.sqlite3` now has `original_recordings`, `videos`, `video_versions`, `clips`,
`practice_sessions` and `session_members`, alongside existing shared-state fields
for titles, favorites, players and courts. Session metadata has its own revision
counter and optimistic concurrency. Initial membership uses the established
30-minute end-to-start grouping. New recordings may join a suggested practice;
existing membership and IDs do not regroup when an earlier recording arrives or
capture metadata changes. Existing independent sessions are not automatically
merged. Manual session membership editing is a future UI; the persistent catalog
provides its boundary. Empty/offline sessions retain their metadata.

`annotations.sqlite3` is a historical filename, **not the domain model**. Its
revision journal now discriminates `moment`, `entity_metadata` and
`processing_annotation`. SQLite views `serving_moments`, `entity_metadata` and
`processing_annotations` expose these distinct categories. A shared journal avoids
copying records or double-writing histories; it does not make a Note a domain
entity. New writes also carry explicit typed subjects or a Moment type. Startup
adds/backfills only the category column; existing IDs, current JSON, immutable
revisions and request-id mappings are preserved. This migration is additive and
idempotent, runs transactionally and never opens worker/published files for writes.

Coaching remains authoritative in `coaching.sqlite3`, including saved responses,
suggestions, evidence and revisions. A read adapter exposes them as serving Moments;
there is no second copy of AI responses and no cross-database write transaction.
Processing annotations support bad-cut reports and `processing_label` records
with a namespaced label, bounded JSON value and optional confidence. They can
target a video, clip or original time/range. Browser-created records can only
claim human origin; model/processor writes will require their own trusted API
contract rather than accepting fabricated model provenance.

Before production migration, stop the web container and copy all website state,
including SQLite WAL/SHM sidecars, to a backup. Never open live SQLite through SMB.
No production migration or deployment is performed by editing this repository.

## APIs and compatibility

- `GET /api/practices`: actual Sessions (not individual recordings).
- `GET /api/entities/sessions/{id}`; `PUT /api/practices/{id}/metadata` edits
  Session `title`/`note` with `version`. Empty title restores the generated label.
- `GET /api/entities/original-recordings/{id}`: retained input metadata.
- `GET /api/entities/videos/{id}`: logical Video/current version/retained versions.
- `GET /api/entities/video-versions/{id}` and `/api/entities/clips/{entity-id}`:
  retained output descriptors, independent of media-file availability.
- `GET /api/entities/videos/{id}/metadata?video_version_id=...`: metadata documents
  grouped by typed subject; logical Video notes plus selected version/clip fields.
- `PUT /api/entities/videos/{id}/metadata`: edit one field. Supply `publication`,
  `subject_type` (`video`, `video_version`, `clip`), `clip_id` for a clip, `field`
  (`note` or `less_useful`), `value`, `version` (0 for new), and optional existing
  notes-entry `key`. Responses return the internal key and new field version.
- `GET /api/recordings/{id}/moments`, `POST /api/moments`,
  `PUT /api/moments/{id}`: normalized optional-end Moment contract. Coaching
  writes use the existing reviewed-coaching API, retaining its evidence checks.
- `GET /api/processing-annotations/sources/{id}`; `POST/PUT
  /api/processing-annotations[/{id}]`: processing feedback only.
- `/api/recordings/{id}/metadata` is the paginated mixed presentation adapter used
  by existing panels; each entry declares its `record_class` and typed subject.
- `/api/metadata[/{id}]` preserves existing metadata editor envelopes.
- Existing `/api/sessions` endpoints and publisher `metadata/sessions/*.json`
  still mean per-recording **Videos**, for compatibility. `practice_groups` are
  actual Sessions. Do not copy the old endpoint names into new domain contracts.
- `/api/annotations` remains a deprecated compatibility/export/revision facade
  for the old UI and retained links. `point`/`interval` in that facade are legacy
  selector shapes, not new domain entity types. Its export is not a training set.

All browser writes retain same-origin checks, explicit desired values, optimistic
concurrency and safe text rendering. No new authentication, NAS permission changes,
worker queue changes, model training or automatic media retention policy is added.

## Review action scope

`/review/{video-id}` reviews a Video, not a practice Session. With an active clip
(including a `?clip=` deep link), the heading, details editor and both hearts target
that Clip. Full-practice playback uses the Video's details and favorite; advancing
through chapters does not change that target. Video capture date/location and
recording-player context remain inherited information, not clip-editable fields.
Clip title edits are stored against the versioned clip entity in shared website
state (`clip-meta:{entity-id}`), with optimistic concurrency and a publication
guard. Notes remain in entity metadata. Session details stay on the home page.

An opened details editor captures its video/clip identity and publication until
closed. Changing playback while it is open never redirects its save. A reprocessed
clip must be reloaded before editing or liking through the new API. Old favorites
are retained as recorded; no inference moves historical clip likes onto videos.
`/api/videos/{id}/details`, `/api/videos/{id}/clips/{clip-id}/details`, and
`/api/videos/{id}/likes/{video|clip-id}` give these actions explicit names while
retaining the old compatibility endpoints.

## Moments during playback

The watch page projects saved original-time moments onto the selected Clip or
full processed Video. Small keyboard-accessible marks sit on the actual seek bar;
overlapping starts share a mark. Playback cues appear in a dedicated area directly
below the player, without pausing playback, moving focus, scrolling the page or
opening the collapsed Moments controls. Those controls offer Highlight, Next
highlight (strictly after the playhead, across clips), and a compact view selector
with All moments. Notes, archived items and export live in a three-dot menu; each
list row keeps Jump/Review primary and puts editing/history in its own menu.
Existing notes, edit/archive/history/export and cut-report flows remain available.

A point highlight displays for one wall-clock second; point coaching/comments use
three seconds. Expiration still works while paused or at a different playback
speed. Explicit-end moments follow playback time until their projected end,
including while paused. Seeking reconciles the landing position; it does not fire
all moments skipped by a seek. Replaying a position can trigger its cue again.
Changing media clears stale cues. Multiple simultaneous cues remain accessible in
a compact scrollable area. A moment outside retained cuts remains in the all-moments
list, with its original timestamp and availability label.

Saved unstructured AI responses can appear as **Coach review · excerpt**, anchored
to the selected excerpt, without claiming verified technique-event timing. Reviewed
timed suggestions supersede that job's response-level automatic cue. The complete
response, evidence and provenance remain authoritative in coaching storage. No AI
request is sent by playback or by opening the Moments area.
