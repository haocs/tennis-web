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
