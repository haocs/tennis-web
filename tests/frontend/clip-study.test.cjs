const test=require('node:test');
const assert=require('node:assert/strict');
const C=require('../../static/clip-study-core.js');
const clip={index:2,original_start_seconds:60,original_end_seconds:70};
test('clock carries millisecond rounding correctly',()=>{
  assert.equal(C.clock(59.9996),'1:00.000');assert.equal(C.clock(3600.005),'1:00:00.005');assert.equal(C.clock(-1),'0:00.000');assert.equal(C.clock(Infinity),'0:00.000');
});
test('sampling stays inside short and long clips with bounded frame count',()=>{
  for(const d of [.04,6,1800]){const times=C.sampleTimes(d);assert.equal(times.length,6);assert(times.every(t=>t>0&&t<d));assert(times.every((t,i)=>!i||t>times[i-1]));}
  for(const d of [0,NaN,Infinity,-1])assert.deepEqual(C.sampleTimes(d),[]);
  assert.deepEqual(C.sampleTimes(10,100),[]);
});
test('source offsets clamp to declared cut and remain approximate in prompt',()=>{
  assert.equal(C.sourceTime(clip,-2),60);assert.equal(C.sourceTime(clip,2),62);assert.equal(C.sourceTime(clip,11),70);
  const prompt=C.coachingPrompt(clip,[{time:3},{time:1}]);assert(prompt.indexOf('0:01.000')<prompt.indexOf('0:03.000'));assert.match(prompt,/approximate/);assert.match(prompt,/sparse stills/);assert.match(C.coachingPrompt(clip),/original-quality clip/);
});
test('pan bounds account for letterboxing and prevent losing the image',()=>{
  assert.deepEqual(C.panBounds(800,450,1920,1080,1),{x:0,y:0});assert.deepEqual(C.panBounds(800,450,1920,1080,2),{x:400,y:225});
  assert.equal(C.panBounds(800,450,1080,1920,2).x,0);assert.deepEqual(C.panBounds(0,0,0,0,4),{x:0,y:0});
});
test('ZIP CRC and entries are real and bounded, with no unsafe names',async()=>{
  const enc=new TextEncoder();assert.equal(C.crc32(enc.encode('123456789')),0xcbf43926);
  const blob=C.zipFiles([{name:'frame-01.jpg',data:new Uint8Array([1,2,3])},{name:'review-prompt.txt',data:'tennis'}]);
  const bytes=new Uint8Array(await blob.arrayBuffer()),view=new DataView(bytes.buffer);
  assert.equal(view.getUint32(0,true),0x04034b50);assert.equal(view.getUint32(bytes.length-22,true),0x06054b50);assert.equal(view.getUint16(bytes.length-14,true),2);
  assert.throws(()=>C.zipFiles([{name:'../secret',data:'x'}]),/filename/);
  assert.throws(()=>C.zipFiles(Array.from({length:11},()=>({name:'x',data:'x'}))),/Too many/);
  assert.throws(()=>C.zipFiles([{name:'large.jpg',data:new Uint8Array(20*1024*1024+1)}]),/20 MB/);
});
test('copy policy supports secure API, LAN HTTP and denied permissions',async()=>{
  const calls=[];
  assert.equal(await C.copyText('prompt',{secure:true,writeText:async text=>calls.push(text),legacyCopy:()=>{throw Error('not needed');}}),true);
  assert.deepEqual(calls,['prompt']);
  assert.equal(await C.copyText('lan',{secure:false,writeText:()=>{throw Error('must not use secure API');},legacyCopy:text=>text==='lan'}),true);
  assert.equal(await C.copyText('denied',{secure:true,writeText:async()=>{throw Error('denied');},legacyCopy:()=>false}),false);
  assert.equal(await C.copyText('fallback',{secure:true,writeText:async()=>{throw Error('denied');},legacyCopy:()=>true}),true);
});

test('manual coaching prompt includes anonymous profile without name or player ID',()=>{
  const prompt=C.coachingPrompt({...clip,main_player:{id:'private-player-id',name:'PrivateName',height_cm:181,level:'experienced'}});
  assert.match(prompt,/181/);assert.match(prompt,/experienced/);assert.doesNotMatch(prompt,/PrivateName|private-player-id|I am a beginner/);
});

test('interval bounds clamp start and end pins and enforce strict interval playback and loop',()=>{
  assert.deepEqual(C.clampInterval(0,2,2),{start:0,end:2});
  assert.deepEqual(C.clampInterval(1.95,2,2,'start'),{start:1.9,end:2});
  assert.deepEqual(C.clampInterval(0.5,0.4,2,'end'),{start:0.5,end:0.6});
  assert.deepEqual(C.clampInterval(-1,5,2),{start:0,end:2});
  assert.equal(C.clampToInterval(0.1,0.4,1.2,2),0.4);
  assert.equal(C.clampToInterval(1.8,0.4,1.2,2),1.2);
  assert.equal(C.clampToInterval(0.8,0.4,1.2,2),0.8);
  assert.deepEqual(C.resolveIntervalPlayback(0.2,0.4,1.2,false),{action:'seek',time:0.4});
  assert.deepEqual(C.resolveIntervalPlayback(0.8,0.4,1.2,false),{action:'continue',time:0.8});
  assert.deepEqual(C.resolveIntervalPlayback(1.2,0.4,1.2,false),{action:'pause',time:1.2});
  assert.deepEqual(C.resolveIntervalPlayback(1.2,0.4,1.2,true),{action:'loop',time:0.4});
});

test('coaching subject options order main player, recording players, saved players, and no-profile cleanly',()=>{
  const alex={id:'a'.repeat(32),name:'Alex',version:1,height_cm:172,level:'beginner'};
  const blair={id:'b'.repeat(32),name:'Blair',version:1,height_cm:180,level:'experienced'};
  const casey={id:'c'.repeat(32),name:'Casey',version:2,height_cm:168,level:'intermediate'};
  const alexFresh={...alex,version:3,level:'intermediate'};
  const assignedClip={...clip,main_player:alex};
  const session={recording_players:[alex,blair],players:[alex,blair]};

  const cat=C.coachingSubjectOptions({clip:assignedClip,session,players:[casey,alexFresh,blair]});
  assert.equal(cat.defaultValue,alex.id);
  assert.deepEqual(cat.options.map(o=>[o.value,o.label,o.role]),[
    [alex.id,'Alex (clip main player)','main_player'],
    [blair.id,'Blair (in recording)','recording_player'],
    [casey.id,'Casey','saved_player'],
    ['','No player profile','none'],
  ]);
  assert.equal(cat.byId[alex.id].version,3,'Higher profile version from refreshed players list supersedes stale clip snapshot');

  // Authoritative list omits soft-deleted profiles.
  const catRemoved=C.coachingSubjectOptions({clip:assignedClip,session,players:[alexFresh],authoritativePlayers:true});
  assert.deepEqual(catRemoved.options.map(o=>o.value),[alex.id,'']);

  // Unassigned clip defaults to empty no-profile option.
  const unassignedCat=C.coachingSubjectOptions({clip:{...clip,main_player:null},session:{recording_players:[blair],players:[blair]},players:[blair,casey]});
  assert.equal(unassignedCat.defaultValue,'');
  assert.equal(unassignedCat.options.at(-1).label,'No player profile (unassigned)');
});

test('resolveCoachingSubject, normalizeSubjectHint, and describeCoachingSubject enforce request-local semantics',()=>{
  const alex={id:'a'.repeat(32),name:'Alex',version:2,height_cm:172,level:'beginner'};
  const blair={id:'b'.repeat(32),name:'Blair',version:4,height_cm:180,level:'experienced'};
  const assignedClip={...clip,main_player:alex};
  const players=[alex,blair];

  assert.equal(C.normalizeSubjectHint('  far court\n\tin   blue  '),'far court in blue');
  assert.equal(C.normalizeSubjectHint('x'.repeat(200)).length,160);
  assert.equal(C.normalizeSubjectHint(null),'');

  const mainRes=C.resolveCoachingSubject({selectedPlayerId:alex.id,clip:assignedClip,players,subjectHint:'   '});
  assert.equal(mainRes.subjectMode,'main_player');
  assert.equal(mainRes.playerId,alex.id);
  assert.equal(mainRes.playerProfileVersion,2);
  assert.equal(mainRes.subjectHint,null);
  assert.match(C.describeCoachingSubject({selectedPlayerId:alex.id,clip:assignedClip,players}),/For Alex \(clip main player\)/);

  const otherRes=C.resolveCoachingSubject({selectedPlayerId:blair.id,clip:assignedClip,players,subjectHint:'far court dark shirt'});
  assert.equal(otherRes.subjectMode,'selected_player');
  assert.equal(otherRes.playerId,blair.id);
  assert.equal(otherRes.playerProfileVersion,4);
  assert.equal(otherRes.subjectHint,'far court dark shirt');
  const otherDesc=C.describeCoachingSubject({selectedPlayerId:blair.id,clip:assignedClip,players});
  assert.match(otherDesc,/For Blair \(this request only; clip player unchanged\)/);
  assert.match(otherDesc,/Auto-frame motion scans foreground motion, not player identity/);

  const noneRes=C.resolveCoachingSubject({selectedPlayerId:'',clip:assignedClip,players});
  assert.deepEqual(noneRes,{subjectMode:'none',playerId:null,playerProfileVersion:null,player:null,subjectHint:null});
  assert.match(C.describeCoachingSubject({selectedPlayerId:'',clip:assignedClip,players}),/clip player stays Alex/);

  assert.throws(()=>C.resolveCoachingSubject({selectedPlayerId:'f'.repeat(32),clip:assignedClip,players}),/Choose a saved player/);
});

test('evidence crop to clip and stage letterbox/zoom/pan projections are exact across aspect ratios', () => {
  const ptUncropped = C.evidenceToClipPoint(0.5, 0.25, [0, 0, 1, 1]);
  assert.deepEqual(ptUncropped, {x: 0.5, y: 0.25});

  const crop = [0.25, 0.2, 0.5, 0.5];
  const ptCropped = C.evidenceToClipPoint(0.5, 0.4, crop);
  assert.ok(Math.abs(ptCropped.x - 0.5) < 1e-9);
  assert.ok(Math.abs(ptCropped.y - 0.4) < 1e-9);

  const boxCropped = C.evidenceToClipBox([0.1, 0.2, 0.4, 0.5], crop);
  assert.ok(Math.abs(boxCropped.x - 0.3) < 1e-9);
  assert.ok(Math.abs(boxCropped.y - 0.3) < 1e-9);
  assert.ok(Math.abs(boxCropped.width - 0.2) < 1e-9);
  assert.ok(Math.abs(boxCropped.height - 0.25) < 1e-9);

  // Landscape 16:9 inside 800x500 stage -> fits width 800, height 450, centered vertically (top 25).
  const lbLandscape = C.letterboxRect(800, 500, 1920, 1080);
  assert.equal(lbLandscape.left, 0);
  assert.equal(lbLandscape.top, 25);
  assert.equal(lbLandscape.width, 800);
  assert.equal(lbLandscape.height, 450);

  // Portrait 9:16 inside 800x500 stage -> fits height 500, width 281.25, pillarboxed horizontally.
  const lbPortrait = C.letterboxRect(800, 500, 1080, 1920);
  assert.ok(Math.abs(lbPortrait.top) < 1e-9);
  assert.ok(Math.abs(lbPortrait.height - 500) < 1e-9);
  assert.ok(Math.abs(lbPortrait.width - 281.25) < 1e-9);
  assert.ok(Math.abs(lbPortrait.left - (800 - 281.25) / 2) < 1e-9);

  // Stage projection under zoom = 2, panX = 30, panY = -20.
  const pStage = C.projectPointToStage(0.75, 0.5, 800, 500, 1920, 1080, 2, 30, -20);
  assert.ok(Math.abs(pStage.x - (400 + 0.25 * 800 * 2 + 30)) < 1e-9);
  assert.ok(Math.abs(pStage.y - (250 + 0 - 20)) < 1e-9);

  const sBox = C.projectBoxToStage({x: 0.25, y: 0.25, width: 0.5, height: 0.5}, 800, 500, 1920, 1080, 1, 0, 0);
  assert.ok(Math.abs(sBox.left - 200) < 1e-9);
  assert.ok(Math.abs(sBox.top - 137.5) < 1e-9);
  assert.ok(Math.abs(sBox.width - 400) < 1e-9);
  assert.ok(Math.abs(sBox.height - 225) < 1e-9);
});

test('disjoint applies_to ranges remain independent and pick the nearest matching range', () => {
  const ranges = C.selectSuggestionRanges(
    [
      {start_seconds: 10.2, end_seconds: 10.7},
      {start_seconds: 13.4, end_seconds: 13.9},
      {start_seconds: 20.0, end_seconds: 21.0}, // outside clip [10, 15]
    ],
    10.0,
    15.0,
    5.0
  );
  assert.equal(ranges.length, 2);
  assert.deepEqual({start: ranges[0].start, end: ranges[0].end}, {start: 0.2, end: 0.7});
  assert.deepEqual({start: ranges[1].start, end: ranges[1].end}, {start: 3.4, end: 3.9});
  assert.equal(C.pickSuggestionRangeIndex(ranges, 13.6), 1);
  assert.equal(C.pickSuggestionRangeIndex(ranges, 10.1), 0);
});

test('resolveSparseOverlay enforces paused-frame, confidence, edge, and identity gates', () => {
  const frame = {frame_id: 'f_1', order: 1, original_seconds: 11.0, clip_seconds: 1.0};
  const suggestion = {
    body_areas: ['hitting_arm', 'head_eyes'],
    focus_region: {x: 0.2, y: 0.2, width: 0.3, height: 0.3},
  };
  const poseFrame = {
    person_count: 1,
    subject_ambiguous: false,
    joints: {
      nose: {x: 0.4, y: 0.25, confidence: 0.9},
      left_shoulder: {x: 0.35, y: 0.35, confidence: 0.85},
      left_elbow: {x: 0.28, y: 0.45, confidence: 0.82},
      left_wrist: {x: 0.25, y: 0.52, confidence: 0.4}, // below 0.65 threshold
      right_shoulder: {x: 0.48, y: 0.35, confidence: 0.88},
      right_elbow: {x: 0.56, y: 0.42, confidence: 0.88},
      right_wrist: {x: 0.99, y: 0.45, confidence: 0.95}, // edge-clipped > 0.98
      left_ankle: {x: 0.42, y: 0.85, confidence: 0.92}, // omitted because body_areas excludes legs
    },
  };

  // Hidden during playback or off-frame or historical or disabled toggle.
  assert.equal(C.resolveSparseOverlay({suggestion, frame, poseFrame, paused: false, currentTime: 1.0, overlayEnabled: true}).visible, false);
  assert.equal(C.resolveSparseOverlay({suggestion, frame, poseFrame, paused: true, currentTime: 1.3, overlayEnabled: true}).visible, false);
  assert.equal(C.resolveSparseOverlay({suggestion, frame, poseFrame, paused: true, currentTime: 1.0, historical: true, overlayEnabled: true}).visible, false);
  assert.equal(C.resolveSparseOverlay({suggestion, frame, poseFrame, paused: true, currentTime: 1.0, overlayEnabled: false}).visible, false);

  // Paused on matching frame passes symmetric arm segments, filters low-conf & edge joints, avoids handedness guesses.
  const ok = C.resolveSparseOverlay({suggestion, frame, poseFrame, paused: true, currentTime: 1.0, overlayEnabled: true});
  assert.equal(ok.visible, true);
  assert.equal(ok.label, 'Approximate body guide');
  assert.ok(ok.focusBox);
  assert.ok(ok.joints.nose && ok.joints.left_shoulder && ok.joints.left_elbow && ok.joints.right_shoulder && ok.joints.right_elbow);
  assert.equal(ok.joints.left_wrist, undefined);
  assert.equal(ok.joints.right_wrist, undefined);
  assert.equal(ok.joints.left_ankle, undefined);

  // Ambiguous multi-person suppresses joints while preserving non-fabricated focus box fallback only when provided.
  const ambig = C.resolveSparseOverlay({
    suggestion: {...suggestion, focus_region: null},
    frame,
    poseFrame: {...poseFrame, person_count: 2, subject_ambiguous: true, ambiguity_reason: 'Multiple players visible'},
    paused: true,
    currentTime: 1.0,
    overlayEnabled: true,
  });
  assert.equal(ambig.visible, false);
  assert.equal(ambig.reason, 'subject_ambiguous');
  assert.deepEqual(ambig.joints, {});
});

test('formatNowTry formats v3 summary cleanly and falls back gracefully on v2 observations', () => {
  const v3 = C.formatNowTry({
    now_summary: 'Racket drops late behind the body.',
    cue: 'Prepare earlier as the ball bounces.',
    stroke_phase: 'unit_turn',
    body_areas: ['hitting_arm', 'shoulders_chest'],
  });
  assert.equal(v3.nowText, 'Racket drops late behind the body.');
  assert.equal(v3.tryText, 'Prepare earlier as the ball bounces.');
  assert.equal(v3.phaseLabel, 'Unit turn');
  assert.deepEqual(v3.bodyAreaLabels, ['Hitting arm', 'Shoulders / chest']);

  const v2 = C.formatNowTry({
    observation: 'Backswing stays compact before contact. Recovery takes two extra steps.',
    cue: 'Turn shoulders earlier.',
  });
  assert.equal(v2.nowText, 'Backswing stays compact before contact.');
  assert.equal(v2.tryText, 'Turn shoulders earlier.');
  assert.equal(v2.phaseLabel, '');
  assert.deepEqual(v2.bodyAreaLabels, []);
});

test('shortClock and formatExcerptSummary present compact human-readable selection summaries', () => {
  assert.equal(C.shortClock(0), '0:00');
  assert.equal(C.shortClock(12), '0:12');
  assert.equal(C.shortClock(4.2), '0:04.2');
  assert.equal(C.shortClock(0.25), '0:00.25');
  assert.equal(C.shortClock(59.9996), '1:00');
  assert.equal(C.shortClock(3600.5), '1:00:00.5');

  assert.equal(
    C.formatExcerptSummary({start: 0, end: 12, speed: 0.5, zoom: 1}, 12),
    'Full clip (0:00–0:12) · 0.5× · 1× zoom'
  );
  assert.equal(
    C.formatExcerptSummary({start: 0.2, end: 1.4, speed: 0.25, zoom: 1.5}, 12),
    '0:00.2–0:01.4 · 0.25× · 1.5× zoom'
  );
});

test('deriveActiveRequestState distinguishes waiting, working, reused prep, and non-cancellable calling', () => {
  const inFlightPost = C.deriveActiveRequestState({pendingOp: {clip_id: 'c1', cancelled: false}});
  assert.equal(inFlightPost.stage, 'waiting_prepare');
  assert.equal(inFlightPost.headline, 'Waiting to prepare');
  assert.equal(inFlightPost.prepState, 'waiting');
  assert.equal(inFlightPost.canCancel, true);
  assert.equal(inFlightPost.badgeText, 'Preparing…');

  const reusedPrep = C.deriveActiveRequestState({pendingOp: {clip_id: 'c1', exportId: 'e1', prepReused: true}});
  assert.equal(reusedPrep.stage, 'waiting_ai');
  assert.equal(reusedPrep.prepState, 'done');
  assert.equal(reusedPrep.headline, 'Waiting for AI');

  const renderLeased = C.deriveActiveRequestState({renderJob: {id: 'r1', role: 'render', state: 'leased'}});
  assert.equal(renderLeased.stage, 'preparing');
  assert.equal(renderLeased.headline, 'Preparing clip');
  assert.equal(renderLeased.prepState, 'active');
  assert.equal(renderLeased.canCancel, true);

  const aiQueued = C.deriveActiveRequestState({aiJob: {id: 'a1', role: 'ai', state: 'queued'}});
  assert.equal(aiQueued.stage, 'waiting_ai');
  assert.equal(aiQueued.headline, 'Waiting for AI');
  assert.equal(aiQueued.prepState, 'done');
  assert.equal(aiQueued.canCancel, true);

  const aiCalling = C.deriveActiveRequestState({aiJob: {id: 'a1', role: 'ai', state: 'calling'}});
  assert.equal(aiCalling.stage, 'analyzing');
  assert.equal(aiCalling.headline, 'Analyzing');
  assert.equal(aiCalling.canCancel, false);
  assert.match(aiCalling.nonCancellableNote, /Analysis has started/);

  const needsReview = C.deriveActiveRequestState({aiJob: {id: 'a1', role: 'ai', state: 'needs_review'}});
  assert.equal(needsReview.stage, 'needs_attention');
  assert.equal(needsReview.headline, 'Needs attention');
  assert.equal(needsReview.badgeText, 'Needs attention');

  assert.equal(C.deriveActiveRequestState({aiJob: {id: 'a1', role: 'ai', state: 'completed'}}), null);
});

test('deriveClipCoachBadge prioritizes active work and attention, then counts non-dismissed tips only', () => {
  const c1 = {id: 'clip1', original_start_seconds: 0, original_end_seconds: 12};
  const jobDone = {id: 'j1', role: 'ai', state: 'completed', asked_at: 100, body: {clip_id: 'clip1', original_start: 1, original_end: 5}};
  const annotations = [
    {id: 'a1', job_id: 'j1', status: 'proposed'},
    {id: 'a2', job_id: 'j1', status: 'accepted'},
    {id: 'a3', job_id: 'j1', status: 'dismissed'},
  ];

  // Excludes dismissed annotations from tip count, ignores share exports and unowned render jobs.
  const shareJob = {id: 's1', role: 'render', state: 'leased', body: {clip_id: 'clip1', intent: 'share', publication: 1}};
  const unownedRender = {id: 'r0', role: 'render', state: 'queued', body: {clip_id: 'clip1', publication: 1}};
  assert.deepEqual(
    C.deriveClipCoachBadge({jobs: [jobDone, shareJob, unownedRender], annotations, clip: c1, publication: 1}),
    {kind: 'tips', count: 2, text: '2 tips', stage: 'ready'}
  );

  // Pending operation takes precedence over existing tips.
  assert.deepEqual(
    C.deriveClipCoachBadge({
      jobs: [jobDone],
      annotations,
      pendingOp: {clip_id: 'clip1', publication: 1, cancelled: false},
      clip: c1,
      publication: 1,
    }),
    {kind: 'active', count: 0, text: 'Preparing…', stage: 'waiting_prepare'}
  );

  // Calling AI shows Analyzing…
  const callingJob = {id: 'j2', role: 'ai', state: 'calling', asked_at: 200, body: {clip_id: 'clip1'}};
  assert.deepEqual(
    C.deriveClipCoachBadge({jobs: [jobDone, callingJob], annotations, clip: c1, publication: 1}),
    {kind: 'active', count: 0, text: 'Analyzing…', stage: 'analyzing'}
  );

  // Failed latest job shows Needs attention.
  const failedJob = {id: 'j3', role: 'ai', state: 'failed', asked_at: 300, body: {clip_id: 'clip1'}};
  assert.deepEqual(
    C.deriveClipCoachBadge({jobs: [jobDone, failedJob], annotations, clip: c1, publication: 1}),
    {kind: 'attention', count: 0, text: 'Needs attention', stage: 'failed'}
  );

  // Failed follow-on question reusing a completed render must not be masked by an earlier completed AI job on that export.
  const reusedRender = {id: 'r1', role: 'render', state: 'completed', body: {clip_id: 'clip1', publication: 1}};
  const doneJobOnR1 = {id: 'j1', role: 'ai', state: 'completed', asked_at: 100, body: {clip_id: 'clip1', export_id: 'r1', original_start: 1, original_end: 5}};
  const failedReusedOp = {clip_id: 'clip1', publication: 1, exportId: 'r1', prepReused: true, aiJobId: null, askRequestId: 'req-2', phase: 'failed', knownAiIds: ['j1'], cancelled: false};
  assert.equal(C.matchPendingAiJob(failedReusedOp, [reusedRender, doneJobOnR1]), null);
  assert.equal(
    C.deriveActiveRequestState({pendingOp: failedReusedOp, renderJob: reusedRender, aiJob: C.matchPendingAiJob(failedReusedOp, [reusedRender, doneJobOnR1])}).stage,
    'failed'
  );
  assert.deepEqual(
    C.deriveClipCoachBadge({jobs: [reusedRender, doneJobOnR1], annotations, pendingOp: failedReusedOp, clip: c1, publication: 1}),
    {kind: 'attention', count: 0, text: 'Needs attention', stage: 'failed'}
  );

  // Player options disclosure stays collapsed unless review needed, override selected, or hint entered.
  assert.equal(C.shouldAutoOpenPlayerOptions({clip: c1}), false);
  assert.equal(C.shouldAutoOpenPlayerOptions({clip: {...c1, player_assignment: {needs_review: true}}}), true);
  assert.equal(C.shouldAutoOpenPlayerOptions({clip: c1, subjectCustomized: true}), true);
  assert.equal(C.shouldAutoOpenPlayerOptions({clip: c1, subjectHint: '  far court  '}), true);
});
