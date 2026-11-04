# Annotation metadata: design and first implementation

Status: implementation design, 2026-11-04. No deployment until all linked tasks pass.

## Goals and product language

An annotation is editable metadata attached to an immutable original recording.
The UI uses **Highlight moment**, **Less useful**, **Note**, and **Report a cut**,
not a generic "annotation" button. Existing hearts stay personal shared-library
favorites. Less useful does not mean poor technique; a poor stroke may be useful
for learning. Cut feedback does not mean poor performance.

Watch supports quick highlights at the playhead, a reversible less-useful clip
flag, and a compact moments/notes list with edit/archive and jump actions. A note
editor supports moment, interval, clip and recording targets. Current clip rows
show highlights/usefulness and a Highlights filter. Cut-report editing lives on
a separate Review cuts page reached from More actions, with source timestamps,
original clip bounds, reason flags and comments. No new ML training or detector
is part of this release.

## Research and architecture

The W3C Web Annotation Data Model separates body, target, selector and provenance.
We adopt that conceptual separation with a small versioned JSON contract; this
is not a claim of full JSON-LD/W3C protocol conformance. Temporal selectors use
original recording seconds, inspired by Media Fragments. Point and interval are
explicitly different; interval overlap uses half-open [start,end) semantics.

- https://www.w3.org/TR/annotation-model/
- https://www.w3.org/TR/media-frags/

Website → metadata repository/API → local NAS application-data database.
Media stays on the read-only library storage. Processing sidecars and prediction
logs remain processor-owned, separate from human metadata and future released
training/evaluation datasets. Mac/Windows workers use APIs; never open databases
through SMB. SQLite WAL requires a single host and does not make transactions
across attached databases atomic, so we do not introduce cross-database writes.
https://www.sqlite.org/wal.html

New metadata lives in `annotations.sqlite3` in the existing /data volume, with
current rows and append-only revisions. Existing player profiles/preferences
remain in website.sqlite3. Existing coaching annotations, reviewed revisions and
pinned evidence remain authoritative in coaching.sqlite3 and are exposed through
a read adapter to the common contract (`coach:<existing-id>`). Their existing edit
API and evidence views remain the editor, avoiding duplicate copies and fragile
dual writes. Export includes stable origin references and original evidence links.
A future store migration can replace the adapter without changing common IDs.

## Contract

Version 1 envelope:

- id, schema_version, source_id (immutable original hash), kind, body
- target: scope = point | interval | clip | video; start_seconds/end_seconds in
  ORIGINAL video time. Video has no selector. Point has one time. Clip stores its
  historical original interval, clip_id and publication; those are provenance,
  never the durable primary identity.
- origin: human | ai | processor and versioned external references. Normal browser
  creates can only claim human origin. Optional player_id links a saved subject;
  profile names are not sent to AI automatically.
- context: publication, original source duration and processing identity if known
- status = active | archived, version, created_at, updated_at; edits retain revisions
- training_label_status = not_released (read-only); never auto-promote to truth

Known kinds validate versioned bodies: note(text), highlight(text optional),
usefulness(value=less_useful, optional reason), cut_feedback(reason flags/comment).
Registry-based schemas let future kinds add UI renderers and validation. Existing
coach suggestions map to kind=coach with interval targets, possibly multiple
applies_to selectors; untimed responses remain selection-level context, not
invented precise timestamps. Their source/player/job/evidence references persist.

GET /api/annotations/types
GET /api/annotations/sources/<source> (common human + coaching view, current relations)
POST /api/annotations (request_id idempotency, current publication precondition)
PUT /api/annotations/<id> (expected version, current publication, full desired body/target/status)
GET /api/annotations/<id>/revisions
GET /api/annotations/sources/<source>/export (historical records/revisions, including archived)

Conflicts return 409 with current state. Mutations inherit same-origin/JSON/header
requirements. Bounded text, payloads and list pages prevent accidental unbounded
writes. Usefulness is one shared assessment per original clip interval, not a
multi-account vote count; concurrent creates cannot produce duplicate flags.
No auto deletion or media writes occur. Archive is reversible and keeps history.

## Reprocessing and projection

Never rewrite saved selectors when clip IDs or cuts change. API adds derived
related_clip_ids, visible original/local intervals and relation:

- Point: current containing clip, [start,end); end-of-original can be shown at the
  final retained endpoint. A removed moment remains saved, visibly unavailable.
- Interval: overlap may yield multiple visible fragments; uncovered original
  portions remain explicit. Display/jump only to retained fragments, without
  relabeling the original interval or pretending the whole span was reviewed.
- Clip-scoped note/usefulness: exact original bounds may match a new clip ID;
  changed bounds show historical/needs-review context, never silently downvote
  newly expanded/split/merged clips. An explicit review can acknowledge a new
  interval via a new revision, retaining the previous target and publication.
- Cut feedback: always pinned to the reviewed publication/run, even if the next
  run happens to produce identical bounds. Old reports remain visible as history.
- Video: attaches to original hash, survives all publications.
- Coaching: retain existing reviewed-publication/player-matching requirements;
  old suggestions cannot become timed cues for a different cut/player silently.

No overlap-to-truth conversion. User can see retained/historical/unavailable items,
edit text without moving their target, or explicitly retarget after reviewing.
All three player clocks (clip, combined practice, original) are converted centrally.
Player markers and list jumps use the current published timeline only. Out-of-cut
source times are described as unavailable instead of seeking unrelated footage.

## Training and retention boundary

Application feedback, model predictions, human ground truth and released datasets
are separate domains. Training/evaluation manifests may reference an annotation ID
AND immutable revision and source hash. Cut feedback is a proposed review signal,
not a frame label; low usefulness and highlights are not detector ground truth.
Experiments/TTL apply to derivative media, never metadata/revisions/evidence pins.
No training export is released implicitly. Existing algorithm-lab dataset curation
and processing-server run records remain authoritative for their domains.

## Milestones / acceptance

1. Metadata store/API, original-clock projections, concurrency/revisions/export,
   existing coaching adapter, schema/storage documentation and backend tests.
2. Watch highlight/usefulness/note editing, markers and clip decorations/filter;
   mobile/desktop, preserved active playback, undo/archive and conflict recovery.
3. Separate cut-review page for reasons/comments and original selectors; historical
   reports survive reprocess, no false training labels or processing side effects.
4. Cross-feature browser tests, container checks, data-preserving release/rollout.

Keep existing #15/#16/#17/#18 and #39 open for their additional processing-server,
exact missed-play correction, adjudication, multi-run evaluation and recommendation
work. This release adds the shared metadata foundation and lightweight cut reports;
it does not claim to implement those entire research milestones.
