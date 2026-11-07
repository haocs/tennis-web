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
