const test = require('node:test');
const assert = require('node:assert/strict');
const {
  frameToleranceMs,
  withinFrameTolerance,
  effectiveClipDurationSeconds,
  buildTimeline,
  projectOriginalToPlayback,
  projectLocalToOriginal,
  adjacentGapsForClip,
  validateSuggestedBounds,
  validateGapInterval,
} = require('../../static/cut-review-core.js');

const walkthroughVersionA = {
  id: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
  published_at: 1700000000,
  source: { duration_seconds: 600 },
  video: { duration_seconds: 225.434 },
  clips: [
    {
      id: 'a1'.repeat(12),
      index: 1,
      file: 'Clips/clip-001.mp4',
      original_start_seconds: 55,
      original_end_seconds: 130.4,
      duration_seconds: 75.433,
      playback_start_seconds: 0,
      playback_end_seconds: 75.433,
    },
    {
      id: 'a2'.repeat(12),
      index: 2,
      file: 'Clips/clip-002.mp4',
      original_start_seconds: 145,
      original_end_seconds: 208.9,
      duration_seconds: 63.867,
      playback_start_seconds: 75.433,
      playback_end_seconds: 139.3,
    },
    {
      id: 'a3'.repeat(12),
      index: 3,
      file: 'Clips/clip-003.mp4',
      original_start_seconds: 333.9,
      original_end_seconds: 420,
      duration_seconds: 86.134,
      playback_start_seconds: 139.3,
      playback_end_seconds: 225.434,
    },
  ],
};

test('builds authoritative kept clips and removed gaps with leading, internal, and trailing spans', () => {
  const tl = buildTimeline(walkthroughVersionA);
  assert.equal(tl.totalDurationMs, 600000);
  assert.equal(tl.keptDurationMs, 75400 + 63900 + 86100);
  assert.equal(tl.removedDurationMs, 600000 - tl.keptDurationMs);
  assert.equal(tl.clips.length, 3);
  assert.equal(tl.gaps.length, 4);
  assert.deepEqual(
    tl.gaps.map(g => [g.gapIndex, g.startMs, g.endMs, g.position]),
    [
      [1, 0, 55000, 'leading'],
      [2, 130400, 145000, 'internal'],
      [3, 208900, 333900, 'internal'],
      [4, 420000, 600000, 'trailing'],
    ]
  );
  const adj = adjacentGapsForClip(tl, 'a2'.repeat(12));
  assert.equal(adj.prevGap.gapIndex, 2);
  assert.equal(adj.nextGap.gapIndex, 3);
});

test('uses authoritative playback_start_seconds, playback_end_seconds, and clip.duration_seconds rather than summed original spans', () => {
  const tl = buildTimeline(walkthroughVersionA);
  // Midpoint of Clip A2: [145000, 208900) -> 176950 ms (alpha = 0.5)
  // Authoritative playback_start_seconds is 75.433 (not 75.400) and clip.duration_seconds is 63.867 (not 63.900).
  const mid = projectOriginalToPlayback(tl, 176950);
  assert.equal(mid.kind, 'clip');
  assert.equal(mid.clipIndex, 2);
  assert.ok(Math.abs(mid.alpha - 0.5) < 1e-9);
  assert.ok(Math.abs(mid.localSeconds - 0.5 * 63.867) < 1e-6);
  assert.ok(Math.abs(mid.joinedSeconds - (75.433 + 0.5 * (139.3 - 75.433))) < 1e-6);

  // Inverse projection restores original timestamp within 1ms
  const inv = projectLocalToOriginal(tl.clips[1], mid.localSeconds);
  assert.equal(inv.originalMs, 176950);
});

test('handles frame quantization tolerance at 29.97fps and 59.94fps and respects runtime videoElement.duration', () => {
  assert.equal(frameToleranceMs(29.97), 68);
  assert.equal(frameToleranceMs(30), 68);
  assert.equal(frameToleranceMs(59.94), 45);
  assert.equal(withinFrameTolerance(198000, 198055, 29.97), true);
  assert.equal(withinFrameTolerance(198000, 198090, 29.97), false);

  const clip = walkthroughVersionA.clips[1];
  // Within two-frame quantization tolerance (68ms at 29.97fps) -> uses runtime duration
  assert.equal(effectiveClipDurationSeconds(clip, 63.91, 29.97), 63.91);
  // Outside two-frame quantization tolerance (e.g. +120ms) -> falls back to manifest duration_seconds
  assert.equal(effectiveClipDurationSeconds(clip, 63.99, 29.97), 63.867);
  // Wildly mismatched runtime duration -> falls back to manifest duration_seconds
  assert.equal(effectiveClipDurationSeconds(clip, 12.0, 29.97), 63.867);
});

test('keeps original source duration canonical: clamps one-frame end rounding and rejects clips exceeding source tolerance', () => {
  const oneFrameOver = {
    source: { duration_seconds: 20, fps: 29.97 },
    clips: [
      {
        id: 'c1',
        index: 1,
        original_start_seconds: 5,
        original_end_seconds: 20.033,
        duration_seconds: 15.033,
        playback_start_seconds: 0,
        playback_end_seconds: 15.033,
      },
    ],
  };
  const tl = buildTimeline(oneFrameOver);
  assert.equal(tl.totalDurationMs, 20000);
  assert.equal(tl.clips[0].endMs, 20000);
  assert.equal(tl.gaps.length, 1); // only leading gap [0, 5000); trailing clamped to 20000

  const beyondTolerance = {
    source: { duration_seconds: 20, fps: 29.97 },
    clips: [
      {
        id: 'c1',
        index: 1,
        original_start_seconds: 5,
        original_end_seconds: 20.25,
        duration_seconds: 15.25,
        playback_start_seconds: 0,
        playback_end_seconds: 15.25,
      },
    ],
  };
  assert.throws(() => buildTimeline(beyondTolerance), /exceeds source duration beyond frame tolerance/);
});

test('handles touching clips (e_i == s_{i+1}), no leading/trailing gap when flush, and exact right endpoint t_orig == D', () => {
  const flushAndTouching = {
    source: { duration_seconds: 20 },
    clips: [
      {
        id: 'c1',
        index: 1,
        original_start_seconds: 0,
        original_end_seconds: 10,
        duration_seconds: 10,
        playback_start_seconds: 0,
        playback_end_seconds: 10,
      },
      {
        id: 'c2',
        index: 2,
        original_start_seconds: 10,
        original_end_seconds: 20,
        duration_seconds: 10,
        playback_start_seconds: 10,
        playback_end_seconds: 20,
      },
    ],
  };
  const tl = buildTimeline(flushAndTouching);
  assert.equal(tl.gaps.length, 0);
  assert.equal(tl.touchingBoundaries.length, 1);
  assert.equal(tl.touchingBoundaries[0].atMs, 10000);

  // Half-open boundary at 10000ms belongs to Clip 2 local 0s
  const atTouch = projectOriginalToPlayback(tl, 10000);
  assert.equal(atTouch.kind, 'clip');
  assert.equal(atTouch.clipId, 'c2');
  assert.equal(atTouch.localSeconds, 0);

  // Endpoint t_orig == D when e_N == D maps to the end of final clip c_N
  const atEndFlush = projectOriginalToPlayback(tl, 20000);
  assert.equal(atEndFlush.kind, 'clip');
  assert.equal(atEndFlush.clipId, 'c2');
  assert.equal(atEndFlush.localSeconds, 10);
  assert.equal(atEndFlush.joinedSeconds, 20);
  assert.equal(atEndFlush.atEndOfRecording, true);

  // Endpoint t_orig == D when e_N < D maps to the end of trailing gap
  const atEndTrailingGap = projectOriginalToPlayback(walkthroughVersionA, 600000);
  assert.equal(atEndTrailingGap.kind, 'gap');
  assert.equal(atEndTrailingGap.gapIndex, 4);
  assert.equal(atEndTrailingGap.gapOffsetMs, 180000);
  assert.equal(atEndTrailingGap.atEndOfRecording, true);
});

test('handles empty 0-clip output as a single full-recording removed gap', () => {
  const emptyRun = {
    source: { duration_seconds: 120 },
    clips: [],
  };
  const tl = buildTimeline(emptyRun);
  assert.equal(tl.clips.length, 0);
  assert.equal(tl.gaps.length, 1);
  assert.equal(tl.gaps[0].startMs, 0);
  assert.equal(tl.gaps[0].endMs, 120000);
  assert.equal(tl.gaps[0].position, 'full');

  const pMid = projectOriginalToPlayback(tl, 45000);
  assert.equal(pMid.kind, 'gap');
  assert.equal(pMid.gapIndex, 1);
  assert.equal(pMid.gapOffsetMs, 45000);

  const pEnd = projectOriginalToPlayback(tl, 120000);
  assert.equal(pEnd.kind, 'gap');
  assert.equal(pEnd.gapIndex, 1);
  assert.equal(pEnd.gapOffsetMs, 120000);
  assert.equal(pEnd.atEndOfRecording, true);
});

test('validates boundary suggestions with omitted side defaulting to original clip bound and rejects invalid gap intervals', () => {
  const tl = buildTimeline(walkthroughVersionA);
  const clip2 = tl.clips[1]; // [145000, 208900)

  // Only suggested_end_ms provided -> defaults start to 145000
  const trimTail = validateSuggestedBounds(clip2, null, 198000, 600000);
  assert.equal(trimTail.valid, true);
  assert.equal(trimTail.effective_start_ms, 145000);
  assert.equal(trimTail.effective_end_ms, 198000);

  // Suggesting an end <= original start without lowering start is rejected
  const badEndOnly = validateSuggestedBounds(clip2, null, 140000, 600000);
  assert.equal(badEndOnly.valid, false);

  // Suggesting a start >= original end without raising end is rejected
  const badStartOnly = validateSuggestedBounds(clip2, 210000, null, 600000);
  assert.equal(badStartOnly.valid, false);

  // Gap interval inside Gap 3 [208900, 333900)
  const validGap = validateGapInterval(tl, 240000, 265000);
  assert.equal(validGap.valid, true);
  assert.equal(validGap.gap.gapIndex, 3);

  // Gap interval overlapping Clip 2 or extending past Gap 3 [208900, 333900) is rejected
  assert.equal(validateGapInterval(tl, 200000, 265000).valid, false);
  assert.equal(validateGapInterval(tl, 340000, 365000).valid, false);
});
