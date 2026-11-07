/* Pure desktop player Auto focus math and smoothing state machine, shared with Node tests. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AutoFocusCore = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const finite = v => typeof v === 'number' && Number.isFinite(v);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  function sampleDimensions(videoWidth, videoHeight, maxEdge = 384) {
    if (!finite(videoWidth) || !finite(videoHeight) || videoWidth <= 0 || videoHeight <= 0) {
      return {width: 320, height: 180};
    }
    const longest = Math.max(videoWidth, videoHeight);
    const targetEdge = clamp(longest, 320, maxEdge);
    const scale = targetEdge / longest;
    const width = Math.max(160, Math.round((videoWidth * scale) / 2) * 2);
    const height = Math.max(120, Math.round((videoHeight * scale) / 2) * 2);
    return {width, height};
  }

  function normalizeDetections(rawDetections, frameWidth = 1, frameHeight = 1, minScore = 0.22) {
    if (!Array.isArray(rawDetections) || !finite(frameWidth) || !finite(frameHeight) || frameWidth <= 0 || frameHeight <= 0) {
      return [];
    }
    const out = [];
    for (const item of rawDetections) {
      if (!item || typeof item !== 'object') continue;
      const cat = Array.isArray(item.categories) ? item.categories[0] : null;
      const label = String(cat?.categoryName ?? cat?.displayName ?? item.label ?? 'person').toLowerCase();
      if (label && label !== 'person') continue;
      const score = finite(cat?.score) ? cat.score : finite(item.score) ? item.score : 0;
      if (score < minScore) continue;
      const b = item.boundingBox || item.box || item;
      const rawX = finite(b.originX) ? b.originX : b.x;
      const rawY = finite(b.originY) ? b.originY : b.y;
      const rawW = b.width;
      const rawH = b.height;
      if (![rawX, rawY, rawW, rawH].every(finite) || rawW <= 0 || rawH <= 0) continue;
      const normalized = frameWidth > 1 || frameHeight > 1 || rawX > 1 || rawY > 1 || rawW > 1 || rawH > 1;
      const invW = normalized ? 1 / frameWidth : 1;
      const invH = normalized ? 1 / frameHeight : 1;
      const x0 = clamp(rawX * invW, 0, 1);
      const y0 = clamp(rawY * invH, 0, 1);
      const x1 = clamp((rawX + rawW) * invW, 0, 1);
      const y1 = clamp((rawY + rawH) * invH, 0, 1);
      const width = x1 - x0;
      const height = y1 - y0;
      const area = width * height;
      if (width < 0.02 || height < 0.04 || area < 0.0015 || area > 0.92) continue;
      out.push({
        x: x0,
        y: y0,
        width,
        height,
        cx: x0 + width / 2,
        cy: y0 + height / 2,
        bottom: y1,
        area,
        score: clamp(score, 0, 1),
      });
    }
    return out;
  }

  function iou(a, b) {
    if (!a || !b) return 0;
    const x0 = Math.max(a.x, b.x);
    const y0 = Math.max(a.y, b.y);
    const x1 = Math.min(a.x + a.width, b.x + b.width);
    const y1 = Math.min(a.y + a.height, b.y + b.height);
    if (x1 <= x0 || y1 <= y0) return 0;
    const inter = (x1 - x0) * (y1 - y0);
    const union = a.area + b.area - inter;
    return union > 0 ? inter / union : 0;
  }

  function selectPrimaryPerson(candidates, previousBox = null) {
    if (!Array.isArray(candidates) || !candidates.length) return null;
    let best = null;
    let bestRank = -Infinity;
    for (const c of candidates) {
      let rank = Math.sqrt(c.area) * 1.6 + c.bottom * 0.45 + c.score * 0.1;
      if (previousBox && (!finite(previousBox.area) || c.area >= previousBox.area * 0.55)) {
        const overlap = iou(c, previousBox);
        const dist = Math.hypot(c.cx - previousBox.cx, c.cy - previousBox.cy);
        if (overlap > 0.05 || dist < 0.24) {
          const affinity = overlap * 0.6 + Math.max(0, 1 - dist / 0.24) * 0.4;
          rank += affinity * 0.09;
        }
      }
      if (
        rank > bestRank + 1e-6 ||
        (Math.abs(rank - bestRank) <= 1e-6 &&
          best &&
          (c.area > best.area + 1e-6 ||
            (Math.abs(c.area - best.area) <= 1e-6 && (c.bottom > best.bottom + 1e-6 || c.cx < best.cx))))
      ) {
        best = c;
        bestRank = rank;
      }
    }
    return best ? {...best, rank: bestRank} : null;
  }

  function panBounds(stageWidth, stageHeight, videoWidth, videoHeight, zoom) {
    if (
      ![stageWidth, stageHeight, videoWidth, videoHeight, zoom].every(finite) ||
      Math.min(stageWidth, stageHeight, videoWidth, videoHeight) <= 0
    ) {
      return {x: 0, y: 0, renderedWidth: 0, renderedHeight: 0};
    }
    const fit = Math.min(stageWidth / videoWidth, stageHeight / videoHeight);
    const renderedWidth = videoWidth * fit;
    const renderedHeight = videoHeight * fit;
    return {
      x: Math.max(0, (renderedWidth * zoom - stageWidth) / 2),
      y: Math.max(0, (renderedHeight * zoom - stageHeight) / 2),
      renderedWidth,
      renderedHeight,
    };
  }

  function targetFraming(box, stageWidth, stageHeight, videoWidth, videoHeight, options = {}) {
    if (!box) return {zoom: 1, x: 0, y: 0};
    const minZoom = finite(options.minZoom) ? options.minZoom : 1;
    const maxZoom = finite(options.maxZoom) ? options.maxZoom : 2.4;
    const targetHeightRatio = finite(options.targetHeightRatio) ? options.targetHeightRatio : 0.56;
    const targetWidthRatio = finite(options.targetWidthRatio) ? options.targetWidthRatio : 0.48;
    const paddedHeight = Math.max(0.12, box.height * 1.08);
    const paddedWidth = Math.max(0.08, box.width * 1.08);
    const rawZoom = clamp(
      Math.min(targetHeightRatio / paddedHeight, targetWidthRatio / paddedWidth),
      minZoom,
      maxZoom
    );
    const zoom = Number(rawZoom.toFixed(4));
    if (zoom <= 1.001) return {zoom: 1, x: 0, y: 0};
    const bounds = panBounds(stageWidth, stageHeight, videoWidth, videoHeight, zoom);
    const focusX = clamp(box.cx, 0, 1);
    const focusY = clamp(box.y + box.height * 0.48, 0, 1);
    const rawX = -(focusX - 0.5) * bounds.renderedWidth * zoom;
    const rawY = -(focusY - 0.5) * bounds.renderedHeight * zoom;
    return {
      zoom,
      x: clamp(rawX, -bounds.x, bounds.x),
      y: clamp(rawY, -bounds.y, bounds.y),
    };
  }

  function projectBoxToStage(box, stageWidth, stageHeight, videoWidth, videoHeight, framing = null) {
    if (
      !box ||
      ![box.x, box.y, box.width, box.height, stageWidth, stageHeight, videoWidth, videoHeight].every(finite) ||
      Math.min(stageWidth, stageHeight, videoWidth, videoHeight) <= 0 ||
      box.width <= 0 ||
      box.height <= 0
    ) {
      return null;
    }
    const active = Boolean(framing && finite(framing.zoom) && framing.zoom > 1.01);
    const zoom = active ? framing.zoom : 1;
    const tx = active && finite(framing.x) ? framing.x : 0;
    const ty = active && finite(framing.y) ? framing.y : 0;
    const bounds = panBounds(stageWidth, stageHeight, videoWidth, videoHeight, zoom);
    const rw = bounds.renderedWidth * zoom;
    const rh = bounds.renderedHeight * zoom;
    const rawLeft = stageWidth / 2 + (box.x - 0.5) * rw + tx;
    const rawTop = stageHeight / 2 + (box.y - 0.5) * rh + ty;
    const rawRight = rawLeft + box.width * rw;
    const rawBottom = rawTop + box.height * rh;
    const left = clamp(rawLeft, 0, stageWidth);
    const top = clamp(rawTop, 0, stageHeight);
    const right = clamp(rawRight, 0, stageWidth);
    const bottom = clamp(rawBottom, 0, stageHeight);
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    return {
      left: Number(left.toFixed(2)),
      top: Number(top.toFixed(2)),
      width: Number(width.toFixed(2)),
      height: Number(height.toFixed(2)),
      rawLeft: Number(rawLeft.toFixed(2)),
      rawTop: Number(rawTop.toFixed(2)),
      rawWidth: Number((box.width * rw).toFixed(2)),
      rawHeight: Number((box.height * rh).toFixed(2)),
      visible: width >= 2 && height >= 2,
    };
  }

  function diagnosticsVerdict(tracker, afEnabled = false, afState = 'off') {
    if (!afEnabled || !tracker) {
      return {
        trackingState: afState || 'off',
        playerStatus: afState === 'unavailable' ? 'unavailable' : 'off',
        motion: 'not active',
        playVerdict: 'not measured',
        swingVerdict: 'not measured',
      };
    }
    const state = afState === 'loading' || afState === 'unavailable' ? afState : tracker.state || 'idle';
    let playerStatus = 'searching';
    if (state === 'loading') playerStatus = 'loading detector';
    else if (state === 'unavailable') playerStatus = 'unavailable';
    else if (state === 'tracking') playerStatus = tracker.motion === 'moving' ? 'tracked · moving' : 'tracked · steady';
    else if (state === 'holding') playerStatus = 'lost briefly · holding';
    else if (state === 'searching') playerStatus = tracker.lostCount > 0 ? 'player lost · searching' : 'searching';
    return {
      trackingState: state,
      playerStatus,
      motion: state === 'tracking' ? tracker.motion || 'steady' : state === 'holding' ? 'holding' : 'lost',
      playVerdict: 'not measured',
      swingVerdict: 'not measured',
    };
  }

  class AutoFocusTracker {
    constructor(options = {}) {
      this.holdMs = finite(options.holdMs) ? options.holdMs : 750;
      this.trackRate = finite(options.trackRate) ? options.trackRate : 5.0;
      this.releaseRate = finite(options.releaseRate) ? options.releaseRate : 2.8;
      this.reset();
    }

    reset() {
      this.current = {zoom: 1, x: 0, y: 0};
      this.target = {zoom: 1, x: 0, y: 0};
      this.trackedBox = null;
      this.lastRawBox = null;
      this.lastCandidates = [];
      this.lastDetectedAt = null;
      this.lostCount = 0;
      this.motion = 'idle';
      this.motionSpeed = 0;
      this.state = 'idle';
    }

    updateDetection(rawDetections, frameWidth, frameHeight, stageWidth, stageHeight, videoWidth, videoHeight, nowMs = 0) {
      const candidates = normalizeDetections(rawDetections, frameWidth, frameHeight);
      const chosen = selectPrimaryPerson(candidates, this.trackedBox);
      this.lastCandidates = candidates.map(c => ({
        ...c,
        primary: Boolean(
          chosen &&
            Math.abs(c.x - chosen.x) <= 1e-6 &&
            Math.abs(c.y - chosen.y) <= 1e-6 &&
            Math.abs(c.width - chosen.width) <= 1e-6 &&
            Math.abs(c.height - chosen.height) <= 1e-6
        ),
      }));
      if (chosen) {
        const prevBox = this.lastRawBox || this.trackedBox;
        if (prevBox && this.lastDetectedAt !== null && finite(nowMs) && nowMs >= this.lastDetectedAt) {
          const dtSec = Math.max(0.05, (nowMs - this.lastDetectedAt) / 1000);
          const delta = Math.hypot(chosen.cx - prevBox.cx, chosen.cy - prevBox.cy);
          this.motionSpeed = Number((delta / dtSec).toFixed(3));
          this.motion = delta >= 0.012 || this.motionSpeed >= 0.04 ? 'moving' : 'steady';
        } else {
          this.motionSpeed = 0;
          this.motion = 'steady';
        }
        this.lastRawBox = {...chosen};
        if (this.trackedBox && Math.hypot(chosen.cx - this.trackedBox.cx, chosen.cy - this.trackedBox.cy) < 0.25) {
          const b = 0.55;
          const x = this.trackedBox.x * (1 - b) + chosen.x * b;
          const y = this.trackedBox.y * (1 - b) + chosen.y * b;
          const width = this.trackedBox.width * (1 - b) + chosen.width * b;
          const height = this.trackedBox.height * (1 - b) + chosen.height * b;
          this.trackedBox = {
            x,
            y,
            width,
            height,
            cx: x + width / 2,
            cy: y + height / 2,
            bottom: y + height,
            area: width * height,
            score: chosen.score,
          };
        } else {
          this.trackedBox = chosen;
        }
        this.target = targetFraming(this.trackedBox, stageWidth, stageHeight, videoWidth, videoHeight);
        this.lastDetectedAt = finite(nowMs) ? nowMs : 0;
        this.lostCount = 0;
        this.state = 'tracking';
      } else {
        this.lostCount += 1;
        this.motionSpeed = 0;
        if (this.lastDetectedAt !== null && finite(nowMs) && nowMs - this.lastDetectedAt <= this.holdMs) {
          this.state = 'holding';
          this.motion = 'holding';
        } else {
          this.trackedBox = null;
          this.lastRawBox = null;
          this.target = {zoom: 1, x: 0, y: 0};
          this.state = 'searching';
          this.motion = 'lost';
        }
      }
      return {state: this.state, target: {...this.target}, trackedBox: this.trackedBox ? {...this.trackedBox} : null};
    }

    step(dtSeconds, stageWidth, stageHeight, videoWidth, videoHeight) {
      const dt = clamp(finite(dtSeconds) ? dtSeconds : 1 / 60, 0.001, 0.25);
      const rate = this.state === 'tracking' ? this.trackRate : this.releaseRate;
      const alpha = 1 - Math.exp(-dt * rate);
      let nextZoom = this.current.zoom + (this.target.zoom - this.current.zoom) * alpha;
      let nextX = this.current.x + (this.target.x - this.current.x) * alpha;
      let nextY = this.current.y + (this.target.y - this.current.y) * alpha;
      if (Math.abs(nextZoom - this.target.zoom) < 0.003) nextZoom = this.target.zoom;
      if (Math.abs(nextX - this.target.x) < 0.25) nextX = this.target.x;
      if (Math.abs(nextY - this.target.y) < 0.25) nextY = this.target.y;
      const bounds = panBounds(stageWidth, stageHeight, videoWidth, videoHeight, nextZoom);
      this.current = {
        zoom: clamp(nextZoom, 1, 3),
        x: clamp(nextX, -bounds.x, bounds.x),
        y: clamp(nextY, -bounds.y, bounds.y),
      };
      return {...this.current};
    }
  }

  return {
    finite,
    clamp,
    sampleDimensions,
    normalizeDetections,
    iou,
    selectPrimaryPerson,
    panBounds,
    targetFraming,
    projectBoxToStage,
    diagnosticsVerdict,
    AutoFocusTracker,
  };
});
