'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');

const {
  sampleDimensions,
  normalizeDetections,
  iou,
  selectPrimaryPerson,
  panBounds,
  targetFraming,
  projectBoxToStage,
  diagnosticsVerdict,
  AutoFocusTracker,
  planAutoCoachSampleTimes,
  scoreAutoCoachSamples,
  selectAutoCoachWindows,
} = require('../../static/autofocus-core.js');

test('sampleDimensions bounds largest dimension and preserves even pixel sizes', () => {
  assert.deepEqual(sampleDimensions(1920, 1080, 384), {width: 384, height: 216});
  assert.deepEqual(sampleDimensions(160, 90, 384), {width: 320, height: 180});
  assert.deepEqual(sampleDimensions(0, -10, 384), {width: 320, height: 180});
});

test('normalizeDetections filters non-person, low-score, and invalid bounding boxes', () => {
  const raw = [
    {
      boundingBox: {originX: 160, originY: 100, width: 48, height: 72},
      categories: [{categoryName: 'person', score: 0.72}],
    },
    {
      boundingBox: {originX: 40, originY: 40, width: 40, height: 50},
      categories: [{categoryName: 'sports ball', score: 0.91}],
    },
    {
      boundingBox: {originX: 80, originY: 80, width: 32, height: 48},
      categories: [{categoryName: 'person', score: 0.12}],
    },
    {
      boundingBox: {originX: 0, originY: 0, width: 318, height: 178},
      categories: [{categoryName: 'person', score: 0.85}],
    },
  ];
  const out = normalizeDetections(raw, 320, 180);
  assert.equal(out.length, 1);
  assert.equal(out[0].x, 0.5);
  assert.ok(Math.abs(out[0].width - 0.15) < 1e-9);
  assert.ok(Math.abs(out[0].height - 0.4) < 1e-9);
  assert.equal(out[0].score, 0.72);
});

test('selectPrimaryPerson prefers nearest/largest player and maintains hysteresis lock', () => {
  const candidates = normalizeDetections(
    [
      {
        boundingBox: {originX: 140, originY: 28, width: 16, height: 28},
        categories: [{categoryName: 'person', score: 0.65}],
      },
      {
        boundingBox: {originX: 180, originY: 96, width: 36, height: 64},
        categories: [{categoryName: 'person', score: 0.52}],
      },
    ],
    320,
    180
  );
  const chosen = selectPrimaryPerson(candidates, null);
  assert.ok(chosen);
  assert.ok(chosen.bottom > 0.8, 'Near-court larger player should win over far-court player');

  // With two closely ranked players, temporal continuity keeps the previously tracked target stable.
  const closePair = normalizeDetections(
    [
      {
        boundingBox: {originX: 70, originY: 90, width: 34, height: 62},
        categories: [{categoryName: 'person', score: 0.54}],
      },
      {
        boundingBox: {originX: 200, originY: 92, width: 35, height: 63},
        categories: [{categoryName: 'person', score: 0.55}],
      },
    ],
    320,
    180
  );
  const lockedLeft = selectPrimaryPerson(closePair, closePair[0]);
  assert.equal(lockedLeft.x, closePair[0].x);
  assert.ok(iou(closePair[0], closePair[0]) > 0.99);

  // When a clearly larger/closer player enters the frame, continuity must not stay stuck on a far player.
  const [far, near] = normalizeDetections(
    [
      {x: 0.45, y: 0.3, width: 0.08, height: 0.15, score: 0.9},
      {x: 0.1, y: 0.2, width: 0.25, height: 0.6, score: 0.9},
    ],
    1,
    1
  );
  const switched = selectPrimaryPerson([far, near], far);
  assert.ok(switched);
  assert.equal(switched.x, near.x);
  assert.equal(switched.height, near.height);
});

test('targetFraming clamps pan offsets within panBounds and keeps neutral 1x for close players', () => {
  const edgePlayer = {
    x: 0.88,
    y: 0.68,
    width: 0.1,
    height: 0.28,
    cx: 0.93,
    cy: 0.82,
    bottom: 0.96,
    area: 0.028,
    score: 0.8,
  };
  const framing = targetFraming(edgePlayer, 960, 540, 1920, 1080);
  const bounds = panBounds(960, 540, 1920, 1080, framing.zoom);
  assert.ok(framing.zoom > 1.4 && framing.zoom <= 2.4);
  assert.ok(Math.abs(framing.x) <= bounds.x + 1e-6);
  assert.ok(Math.abs(framing.y) <= bounds.y + 1e-6);

  // Close/large player already filling most of the frame height stays at neutral 1x without cropping.
  const closePlayer = {
    x: 0.35,
    y: 0.1,
    width: 0.3,
    height: 0.72,
    cx: 0.5,
    cy: 0.46,
    bottom: 0.82,
    area: 0.216,
    score: 0.92,
  };
  assert.deepEqual(targetFraming(closePlayer, 960, 540, 1920, 1080), {zoom: 1, x: 0, y: 0});
});

test('AutoFocusTracker smooths toward target, holds briefly on occlusion, and eases back on loss', () => {
  const tracker = new AutoFocusTracker({holdMs: 700});
  const detection = [
    {
      boundingBox: {originX: 200, originY: 96, width: 32, height: 54},
      categories: [{categoryName: 'person', score: 0.78}],
    },
  ];
  const first = tracker.updateDetection(detection, 320, 180, 960, 540, 1920, 1080, 1000);
  assert.equal(first.state, 'tracking');
  assert.ok(first.target.zoom > 1.3);

  let stepFrame = tracker.step(0.1, 960, 540, 1920, 1080);
  assert.ok(stepFrame.zoom > 1.05 && stepFrame.zoom < first.target.zoom);
  assert.ok(stepFrame.x < 0);

  for (let i = 0; i < 20; i += 1) {
    stepFrame = tracker.step(0.1, 960, 540, 1920, 1080);
  }
  assert.ok(Math.abs(stepFrame.zoom - first.target.zoom) < 0.02);

  // Brief occlusion within holdMs holds the target framing.
  const held = tracker.updateDetection([], 320, 180, 960, 540, 1920, 1080, 1400);
  assert.equal(held.state, 'holding');
  assert.equal(held.target.zoom, first.target.zoom);

  // Sustained loss beyond holdMs eases back toward neutral 1x framing.
  const lost = tracker.updateDetection([], 320, 180, 960, 540, 1920, 1080, 1900);
  assert.equal(lost.state, 'searching');
  assert.deepEqual(lost.target, {zoom: 1, x: 0, y: 0});
  for (let i = 0; i < 30; i += 1) {
    stepFrame = tracker.step(0.1, 960, 540, 1920, 1080);
  }
  assert.equal(stepFrame.zoom, 1);
  assert.equal(stepFrame.x, 0);
  assert.equal(stepFrame.y, 0);
});

test('projectBoxToStage maps detection boxes through letterboxing and zoom/pan transforms', () => {
  // 4:3 video (1440x1080) inside 16:9 stage (960x540) has 120px left/right pillarbox bars (rendered 720x540).
  const fullBox = {x: 0, y: 0, width: 1, height: 1};
  const letterboxed = projectBoxToStage(fullBox, 960, 540, 1440, 1080, {zoom: 1, x: 0, y: 0});
  assert.deepEqual(letterboxed, {
    left: 120,
    top: 0,
    width: 720,
    height: 540,
    rawLeft: 120,
    rawTop: 0,
    rawWidth: 720,
    rawHeight: 540,
    visible: true,
  });

  // When zoomed and panned toward a player within panBounds, the focus point lands at the stage center (480, 270).
  const playerBox = {
    x: 0.45,
    y: 0.34,
    width: 0.14,
    height: 0.32,
    cx: 0.52,
    cy: 0.5,
    bottom: 0.66,
    area: 0.0448,
    score: 0.84,
  };
  const framing = targetFraming(playerBox, 960, 540, 1440, 1080);
  const projected = projectBoxToStage(playerBox, 960, 540, 1440, 1080, framing);
  assert.ok(projected && projected.visible);
  const focusStageX = projected.rawLeft + 0.5 * projected.rawWidth;
  const focusStageY = projected.rawTop + 0.48 * projected.rawHeight;
  assert.ok(Math.abs(focusStageX - 480) < 0.5);
  assert.ok(Math.abs(focusStageY - 270) < 0.5);

  // When pan is clamped by letterbox panBounds, the zoomed video's right edge stays flush with stageWidth (960).
  const edgeBox = {...playerBox, x: 0.78, cx: 0.85};
  const clampedFraming = targetFraming(edgeBox, 960, 540, 1440, 1080);
  const zoomedVideo = projectBoxToStage(fullBox, 960, 540, 1440, 1080, clampedFraming);
  assert.ok(Math.abs(zoomedVideo.rawLeft + zoomedVideo.rawWidth - 960) < 0.5);
});

test('diagnosticsVerdict reports motion and loss truthfully while keeping play and swing not measured', () => {
  const tracker = new AutoFocusTracker({holdMs: 700});
  assert.deepEqual(diagnosticsVerdict(tracker, false, 'off'), {
    trackingState: 'off',
    playerStatus: 'off',
    motion: 'not active',
    playVerdict: 'not measured',
    swingVerdict: 'not measured',
  });

  const detA = [{boundingBox: {originX: 140, originY: 80, width: 32, height: 60}, categories: [{categoryName: 'person', score: 0.8}]}];
  tracker.updateDetection(detA, 320, 180, 960, 540, 1920, 1080, 1000);
  const steady = diagnosticsVerdict(tracker, true, 'tracking');
  assert.equal(steady.playerStatus, 'tracked · steady');
  assert.equal(steady.playVerdict, 'not measured');
  assert.equal(steady.swingVerdict, 'not measured');

  const detB = [{boundingBox: {originX: 185, originY: 82, width: 32, height: 60}, categories: [{categoryName: 'person', score: 0.82}]}];
  tracker.updateDetection(detB, 320, 180, 960, 540, 1920, 1080, 1180);
  const moving = diagnosticsVerdict(tracker, true, 'tracking');
  assert.equal(moving.playerStatus, 'tracked · moving');
  assert.equal(moving.motion, 'moving');

  tracker.updateDetection([], 320, 180, 960, 540, 1920, 1080, 1400);
  assert.equal(diagnosticsVerdict(tracker, true, 'holding').playerStatus, 'lost briefly · holding');

  tracker.updateDetection([], 320, 180, 960, 540, 1920, 1080, 2000);
  assert.equal(diagnosticsVerdict(tracker, true, 'searching').playerStatus, 'player lost · searching');
});

test('planAutoCoachSampleTimes bounds sample count to [2, 16] and stays strictly interior', () => {
  assert.deepEqual(planAutoCoachSampleTimes(0), []);
  assert.deepEqual(planAutoCoachSampleTimes(-5), []);
  const shortTimes = planAutoCoachSampleTimes(0.8);
  assert.equal(shortTimes.length, 4);
  assert.ok(shortTimes.every(t => t > 0 && t < 0.8));

  const mediumTimes = planAutoCoachSampleTimes(12, 16);
  assert.equal(mediumTimes.length, 8);
  assert.ok(mediumTimes.every((t, i) => t > 0 && t < 12 && (i === 0 || t > mediumTimes[i - 1])));

  const longTimes = planAutoCoachSampleTimes(90, 24);
  assert.equal(longTimes.length, 16, 'Never exceeds 16 samples per scan');
  assert.ok(longTimes[0] > 0 && longTimes[longTimes.length - 1] < 90);
});

test('selectAutoCoachWindows returns 0 windows on empty court or static person, 1 window on single burst, and 2 non-overlapping windows on two bursts', () => {
  const times12 = planAutoCoachSampleTimes(12);
  // 1. Empty court -> 0 windows
  const emptySamples = times12.map(time => ({time, detections: []}));
  assert.deepEqual(selectAutoCoachWindows(emptySamples, 12, {originalStartSeconds: 100, originalEndSeconds: 112}), []);

  // 2. Completely static person across all samples -> 0 windows (motion gate prevents false positive on frozen figure)
  const staticSamples = times12.map(time => ({
    time,
    detections: [{x: 0.42, y: 0.45, width: 0.14, height: 0.36, score: 0.88, categoryName: 'person'}],
  }));
  assert.deepEqual(selectAutoCoachWindows(staticSamples, 12, {originalStartSeconds: 100, originalEndSeconds: 112}), []);

  // 3. Single active burst near start of 12s clip -> 1 window only (never fabricates a second window)
  const singleBurst = times12.map((time, idx) => {
    if (idx <= 2) {
      return {
        time,
        detections: [
          {
            x: 0.3 + idx * 0.11,
            y: 0.42 - idx * 0.03,
            width: 0.16,
            height: 0.38,
            score: 0.9,
            categoryName: 'person',
          },
        ],
      };
    }
    return {time, detections: []};
  });
  const oneWin = selectAutoCoachWindows(singleBurst, 12, {
    maxWindows: 2,
    originalStartSeconds: 50,
    originalEndSeconds: 62,
  });
  assert.equal(oneWin.length, 1);
  assert.ok(oneWin[0].start >= 0 && oneWin[0].end <= 6.0);
  assert.equal(oneWin[0].original_start, Number((50 + oneWin[0].start).toFixed(3)));
  assert.equal(oneWin[0].original_end, Number((50 + oneWin[0].end).toFixed(3)));

  // 4. Two distinct active bursts (early and late) in a 15s clip -> 2 chronological non-overlapping windows
  const times15 = planAutoCoachSampleTimes(15);
  const twoBursts = times15.map((time, idx) => {
    // Far-court tiny background figure present everywhere (should not dominate primary near-court player)
    const farPerson = {x: 0.48, y: 0.18, width: 0.04, height: 0.08, score: 0.55, categoryName: 'person'};
    if (time >= 0.5 && time <= 4.2) {
      const step = (time - 0.5) / 3.7;
      return {
        time,
        detections: [
          farPerson,
          {x: 0.22 + step * 0.28, y: 0.44, width: 0.16, height: 0.38, score: 0.91, categoryName: 'person'},
        ],
      };
    }
    if (time >= 9.5 && time <= 14.2) {
      const step = (time - 9.5) / 4.7;
      return {
        time,
        detections: [
          farPerson,
          {x: 0.64 - step * 0.32, y: 0.42, width: 0.17, height: 0.4, score: 0.93, categoryName: 'person'},
        ],
      };
    }
    return {time, detections: [farPerson]};
  });

  const twoWins = selectAutoCoachWindows(twoBursts, 15, {
    maxWindows: 2,
    originalStartSeconds: 200,
    originalEndSeconds: 215,
  });
  assert.equal(twoWins.length, 2);
  assert.ok(twoWins[0].start < twoWins[0].end);
  assert.ok(twoWins[1].start < twoWins[1].end);
  assert.ok(twoWins[0].end <= twoWins[1].start, `Expected non-overlapping windows, got ${JSON.stringify(twoWins)}`);
  assert.ok(twoWins[0].original_start >= 200 && twoWins[1].original_end <= 215);

  // Respects remaining slot cap (maxWindows = 1 returns only the top window, maxWindows = 0 returns [])
  const cappedOne = selectAutoCoachWindows(twoBursts, 15, {
    maxWindows: 1,
    originalStartSeconds: 200,
    originalEndSeconds: 215,
  });
  assert.equal(cappedOne.length, 1);
  assert.deepEqual(selectAutoCoachWindows(twoBursts, 15, {maxWindows: 0}), []);

  // Short clip (< 5s) never returns more than 1 window even with active motion throughout
  const shortActive = planAutoCoachSampleTimes(3.2).map((time, idx) => ({
    time,
    detections: [{x: 0.25 + idx * 0.12, y: 0.4, width: 0.18, height: 0.42, score: 0.9, categoryName: 'person'}],
  }));
  const shortWin = selectAutoCoachWindows(shortActive, 3.2, {maxWindows: 2, originalStartSeconds: 10, originalEndSeconds: 13.2});
  assert.equal(shortWin.length, 1);
  assert.ok(shortWin[0].start >= 0 && shortWin[0].end <= 3.2 && shortWin[0].end - shortWin[0].start === 2.5);

  // Ultra-short clip (< 2.5s) spans the full clip [0, duration]
  const ultraShortActive = planAutoCoachSampleTimes(2.0).map((time, idx) => ({
    time,
    detections: [{x: 0.3 + idx * 0.1, y: 0.4, width: 0.18, height: 0.42, score: 0.9, categoryName: 'person'}],
  }));
  const ultraShortWin = selectAutoCoachWindows(ultraShortActive, 2.0, {maxWindows: 2, originalStartSeconds: 5, originalEndSeconds: 7});
  assert.equal(ultraShortWin.length, 1);
  assert.equal(ultraShortWin[0].start, 0);
  assert.equal(ultraShortWin[0].end, 2.0);
});

test('long clips (41s, 60s, 90s) retain bounded paired motion sampling, reject static/empty scenes, and detect late activity', () => {
  // Moving foreground player across 12s, 30s, 40s, 41s, 60s, and 90s clips
  for (const dur of [12, 30, 40, 41, 60, 90]) {
    const times = planAutoCoachSampleTimes(dur, 16);
    assert.ok(times.length <= 16, `Expected <= 16 samples for ${dur}s, got ${times.length}`);
    assert.ok(times.every((t, i) => t > 0 && t < dur && (i === 0 || t > times[i - 1])));

    const movingSamples = times.map((time, idx) => ({
      time,
      detections: [
        {
          x: 0.25 + ((idx % 4) * 0.11),
          y: 0.36 + ((idx % 2) * 0.04),
          width: 0.16,
          height: 0.4,
          score: 0.92,
          categoryName: 'person',
        },
      ],
    }));
    const windows = selectAutoCoachWindows(movingSamples, dur, {
      maxWindows: 2,
      originalStartSeconds: 100,
      originalEndSeconds: 100 + dur,
    });
    assert.equal(windows.length, 2, `Expected 2 non-overlapping windows on moving ${dur}s clip, got ${JSON.stringify(windows)}`);
    assert.ok(windows[0].end <= windows[1].start, `Expected non-overlapping windows on ${dur}s clip`);
    assert.ok(windows[0].peakMotion > 0 && windows[1].peakMotion > 0, `Expected positive motion on ${dur}s clip`);

    // Static foreground person on long clip -> 0 windows
    const staticSamples = times.map(time => ({
      time,
      detections: [{x: 0.42, y: 0.38, width: 0.16, height: 0.4, score: 0.92, categoryName: 'person'}],
    }));
    assert.deepEqual(
      selectAutoCoachWindows(staticSamples, dur, {maxWindows: 2}),
      [],
      `Expected 0 windows on static ${dur}s clip`
    );

    // Empty court on long clip -> 0 windows
    const emptySamples = times.map(time => ({time, detections: []}));
    assert.deepEqual(
      selectAutoCoachWindows(emptySamples, dur, {maxWindows: 2}),
      [],
      `Expected 0 windows on empty ${dur}s clip`
    );
  }

  // Late-only activity on 60s and 90s clips selects the late window
  for (const dur of [60, 90]) {
    const times = planAutoCoachSampleTimes(dur, 16);
    const lateStart = dur * 0.78;
    const lateSamples = times.map((time, idx) => {
      if (time >= lateStart) {
        return {
          time,
          detections: [
            {
              x: 0.3 + ((idx % 2) * 0.16),
              y: 0.36,
              width: 0.17,
              height: 0.42,
              score: 0.93,
              categoryName: 'person',
            },
          ],
        };
      }
      // Static player earlier in the clip
      return {
        time,
        detections: [{x: 0.45, y: 0.36, width: 0.16, height: 0.4, score: 0.88, categoryName: 'person'}],
      };
    });
    const lateWin = selectAutoCoachWindows(lateSamples, dur, {
      maxWindows: 2,
      originalStartSeconds: 300,
      originalEndSeconds: 300 + dur,
    });
    assert.ok(lateWin.length >= 1, `Expected late activity window on ${dur}s clip`);
    assert.ok(
      lateWin[lateWin.length - 1].start >= dur * 0.65,
      `Expected late window near end of ${dur}s clip, got ${JSON.stringify(lateWin)}`
    );
  }
});

test('selectAutoCoachWindows excludes consumed or in-flight intervals on retry while allowing pre-call failed/cancelled intervals', () => {
  const times15 = planAutoCoachSampleTimes(15, 16);
  const twoBursts = times15.map(time => {
    if (time >= 1.2 && time <= 4.8) {
      const step = (time - 1.2) / 3.6;
      return {
        time,
        detections: [{x: 0.22 + step * 0.36, y: 0.36, width: 0.18, height: 0.44, score: 0.96, categoryName: 'person'}],
      };
    }
    if (time >= 9.6 && time <= 13.6) {
      const step = (time - 9.6) / 4.0;
      return {
        time,
        detections: [{x: 0.62 - step * 0.28, y: 0.4, width: 0.16, height: 0.4, score: 0.91, categoryName: 'person'}],
      };
    }
    return {
      time,
      detections: [{x: 0.45, y: 0.38, width: 0.16, height: 0.4, score: 0.9, categoryName: 'person'}],
    };
  });

  const initialTwo = selectAutoCoachWindows(twoBursts, 15, {maxWindows: 2});
  assert.equal(initialTwo.length, 2);
  const [firstBurst, secondBurst] = initialTwo;
  assert.ok(firstBurst.end <= secondBurst.start);

  // Without exclusions, maxWindows = 1 picks the strongest first burst.
  const unexcludedTop = selectAutoCoachWindows(twoBursts, 15, {maxWindows: 1});
  assert.equal(unexcludedTop.length, 1);
  assert.equal(unexcludedTop[0].start, firstBurst.start);

  // Partial failure retry: first burst succeeded (excluded), second failed -> maxWindows = 1 selects second burst, never duplicating first.
  const retryAfterFirstSucceeded = selectAutoCoachWindows(twoBursts, 15, {
    maxWindows: 1,
    excludedIntervals: [{start: firstBurst.start, end: firstBurst.end}],
  });
  assert.equal(retryAfterFirstSucceeded.length, 1);
  assert.ok(retryAfterFirstSucceeded[0].start >= firstBurst.end, `Expected non-overlapping second burst, got ${JSON.stringify(retryAfterFirstSucceeded)}`);
  assert.equal(retryAfterFirstSucceeded[0].start, secondBurst.start);
  assert.equal(retryAfterFirstSucceeded[0].end, secondBurst.end);

  // Reverse partial failure: second burst succeeded (excluded), first failed -> maxWindows = 1 selects first burst.
  const retryAfterSecondSucceeded = selectAutoCoachWindows(twoBursts, 15, {
    maxWindows: 1,
    excludedIntervals: [{start: secondBurst.start, end: secondBurst.end}],
  });
  assert.equal(retryAfterSecondSucceeded.length, 1);
  assert.ok(retryAfterSecondSucceeded[0].end <= secondBurst.start);
  assert.equal(retryAfterSecondSucceeded[0].start, firstBurst.start);

  // Pre-call failed/cancelled intervals are omitted from excludedIntervals -> strongest burst remains eligible on retry.
  const retryAfterPreCallFail = selectAutoCoachWindows(twoBursts, 15, {
    maxWindows: 1,
    excludedIntervals: [],
  });
  assert.equal(retryAfterPreCallFail.length, 1);
  assert.equal(retryAfterPreCallFail[0].start, firstBurst.start);

  // Single-burst clip: after the sole burst is consumed/in-flight, retry with maxWindows = 1 returns 0 windows ([]), never duplicating it.
  const singleBurst = times15.map(time => {
    if (time >= 2.0 && time <= 5.5) {
      const step = (time - 2.0) / 3.5;
      return {
        time,
        detections: [{x: 0.26 + step * 0.32, y: 0.38, width: 0.17, height: 0.42, score: 0.94, categoryName: 'person'}],
      };
    }
    return {time, detections: []};
  });
  const onlyInitial = selectAutoCoachWindows(singleBurst, 15, {maxWindows: 2});
  assert.equal(onlyInitial.length, 1);
  const retrySingleConsumed = selectAutoCoachWindows(singleBurst, 15, {
    maxWindows: 1,
    excludedIntervals: [{start: onlyInitial[0].start, end: onlyInitial[0].end}],
  });
  assert.deepEqual(retrySingleConsumed, []);

  // Short clip (< 5s): once 1 interval is consumed, retry returns 0 windows.
  const shortSamples = planAutoCoachSampleTimes(3.4).map((time, idx) => ({
    time,
    detections: [{x: 0.25 + idx * 0.12, y: 0.4, width: 0.18, height: 0.42, score: 0.9, categoryName: 'person'}],
  }));
  const shortInitial = selectAutoCoachWindows(shortSamples, 3.4, {maxWindows: 2});
  assert.equal(shortInitial.length, 1);
  assert.deepEqual(
    selectAutoCoachWindows(shortSamples, 3.4, {
      maxWindows: 1,
      excludedIntervals: [{start: shortInitial[0].start, end: shortInitial[0].end}],
    }),
    []
  );

  // Long 60s clip: excluding first burst selects the second burst on retry.
  const times60 = planAutoCoachSampleTimes(60, 16);
  const longTwoBursts = times60.map((time, idx) => {
    if ((time >= 8 && time <= 18) || (time >= 42 && time <= 54)) {
      return {
        time,
        detections: [{x: 0.28 + ((idx % 2) * 0.18), y: 0.36, width: 0.17, height: 0.42, score: 0.93, categoryName: 'person'}],
      };
    }
    return {
      time,
      detections: [{x: 0.45, y: 0.36, width: 0.16, height: 0.4, score: 0.88, categoryName: 'person'}],
    };
  });
  const longInitial = selectAutoCoachWindows(longTwoBursts, 60, {maxWindows: 2});
  assert.equal(longInitial.length, 2);
  const longRetry = selectAutoCoachWindows(longTwoBursts, 60, {
    maxWindows: 1,
    excludedIntervals: [{start: longInitial[0].start, end: longInitial[0].end}],
  });
  assert.equal(longRetry.length, 1);
  assert.equal(longRetry[0].start, longInitial[1].start);
});
