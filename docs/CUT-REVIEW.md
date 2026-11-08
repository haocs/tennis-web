# Cut review

The Processing Room links to `/review/<source-sha>/cuts` (also `/processing/cuts/<source-sha>`). Reviews are processing evidence, separate from Moments and output usefulness. They never change a cut or become training labels automatically.

## Current-serving review

The timeline uses original recording time and distinguishes kept clips from removed gaps. Removed footage is not published; neighboring retained clips provide context. The form supports an optional verdict, reasons, suggested original start/end boundaries and a comment. Save or clear a draft before changing its clip, gap, recording or version.

`GET /api/processing/videos/<source-sha>/versions` returns immutable, retained version descriptors and the current serving version. Version IDs hash the source SHA and frozen publication token; they are not processing-server source IDs. A manifest collision is rejected. Historical descriptors alone do not imply playable historical media.

`GET /api/processing-annotations/sources/<source-sha>?video_version_id=<version>` filters before pagination. Writes accept `reviewed_video_version_id`, the exact `publication` and optionally `allow_historical: true`. Historical writes require explicit scope; `require_current_serving` fences a serving change during save. Updates retain their original target version. Normal revision conflicts use optimistic concurrency.

Cut-feedback verdicts: `good_cut`, `needs_adjustment`, `poor_cut`, `missed_play`, or null. Suggested bounds are integer original milliseconds, bounded by source duration and a positive effective interval. Missed-play intervals must fit wholly inside one removed gap; resizing stays in that gap and version. Direction-only feedback is evidence, not an executable constraint.

No processing-server, worker, NAS permissions or deployment changes are needed for the first review phase. Legacy versions without verified server execution mapping remain reviewable.

## Worker hints and rerun preview

Worker diagnostics are read only from declared, checksummed artifacts. The reader rejects hints with a wrong version, source, parent range, provenance hash, truth status or size. Pages contain at most 50 summaries. Prefilling leaves the review unsaved and records the hint reference only after human confirmation. Historical descriptors without diagnostic artifacts remain reviewable.

Set the private `TENNIS_PROCESSING_API_URL` to the processing-server base URL to enable metadata-only rerun submission; `TENNIS_PROCESSING_ROOM_URL` separately controls the portal link. No private-originals mount is added to the website. Disabled or offline integration fails visibly without a background queue.

Select exact active review revisions, preview proposed commands, and confirm/edit their original-time intervals. Good-cut, wrong-player, too-short and direction-only judgments remain advisory. Only confirmed commands are submitted as an immutable server snapshot. The bridge verifies server attempt/manifest mapping, exports full selected evidence revisions and rechecks revisions on submission. Requests use stable idempotency keys and always request `publication_policy: never`. Candidate activation remains a separate operator action in the processing portal.

## Version comparison and retention

`/review/<source-sha>/compare` uses original recording time to distinguish both-kept, only-A-kept, only-B-kept and both-removed ranges. A kept range with missing, buffering or evicted media is shown as unavailable rather than removed. Desktop renders two decks with one audible deck; mobile uses one video element and switches decks at the same original time. Each seek cancels older metadata/seek readiness waits.

`POST /api/processing/videos/<source-sha>/versions/import` imports verified server descriptors without changing the serving pointer. The operator must first stage a candidate for sharing in the processing portal. Historical cut review and diagnostics can then use the staged version; legacy descriptors without a verified attempt remain metadata-only.

`POST/DELETE /api/processing/videos/<source-sha>/comparison-lease` acquire/release a tab's own short-lived server pin. `/media/versions/<version>/<filename>` serves only declared published files; historical requests require an acknowledged server lease. The website never mounts processing scratch storage or originals.

`GET /api/processing/videos/<source-sha>/comparisons` lists saved comparisons. `PUT .../comparisons/<cmp-id>` saves a preference/note with an expected revision. Saving a preference never activates a version or creates a training label. Full-media retention is opt-in; the website claims retention only after server acknowledgement. Each saved revision owns independent pins so a losing concurrent update cannot remove the winner's protection. If releasing an old pin fails, the response reports pending release rather than pretending cleanup succeeded.

Media lifecycle remains server-owned. Automatic cleanup is disabled by default; serving and superseded serving outputs remain permanent by default. Small retained manifests and comparison/review evidence survive media expiry. Existing serving metadata, Moments, likes and court/player records keep their own identities and storage.

## Deployment compatibility

Upgrade the server and standalone worker before enabling the website bridge. The website itself still runs with an empty API setting; serving playback and ordinary cut review remain available. New constraint jobs require `cut-constraints-v1`; an old `process-v2`-only worker cannot claim them. The worker refreshes capabilities through the authenticated profile endpoint without creating a new installed identity. No migration here launches a container, installs a worker, changes NAS permissions or activates a candidate automatically.
