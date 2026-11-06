/* Dedicated Web Worker for browser-local desktop player Auto focus person detection. */
'use strict';

let detector = null;
let initPromise = null;

async function ensureDetector(config = {}) {
  if (detector) return detector;
  if (initPromise) return initPromise;
  initPromise = (async () => {
    const bundleUrl = config.bundleUrl || '/static/vendor/mediapipe/vision_bundle.js';
    const wasmLoaderPath = config.wasmLoaderPath || '/static/vendor/mediapipe/vision_wasm_internal.js';
    const wasmBinaryPath = config.wasmBinaryPath || '/static/vendor/mediapipe/vision_wasm_internal.wasm';
    const modelAssetPath = config.modelAssetPath || '/static/vendor/mediapipe/efficientdet_lite0.tflite';
    self.exports = {};
    self.module = {exports: self.exports};
    importScripts(bundleUrl);
    const {ObjectDetector} = self.module.exports || {};
    if (!ObjectDetector || typeof ObjectDetector.createFromOptions !== 'function') {
      throw new Error('Person detector runtime is unavailable');
    }
    detector = await ObjectDetector.createFromOptions(
      {wasmLoaderPath, wasmBinaryPath},
      {
        baseOptions: {modelAssetPath, delegate: 'CPU'},
        runningMode: 'IMAGE',
        scoreThreshold: 0.22,
        categoryAllowlist: ['person'],
        maxResults: 6,
      }
    );
    return detector;
  })();
  try {
    return await initPromise;
  } catch (err) {
    initPromise = null;
    throw err;
  }
}

self.onmessage = async event => {
  const data = event.data || {};
  try {
    if (data.type === 'init') {
      await ensureDetector(data.config || {});
      self.postMessage({type: 'ready'});
      return;
    }
    if (data.type === 'detect') {
      const instance = await ensureDetector(data.config || {});
      const width = Number(data.width) || 0;
      const height = Number(data.height) || 0;
      if (!data.buffer || width <= 0 || height <= 0) {
        self.postMessage({type: 'result', epoch: data.epoch, seq: data.seq, detections: []});
        return;
      }
      const image = new ImageData(new Uint8ClampedArray(data.buffer), width, height);
      const result = instance.detect(image);
      const detections = (result?.detections || []).map(d => ({
        boundingBox: d.boundingBox
          ? {
              originX: d.boundingBox.originX,
              originY: d.boundingBox.originY,
              width: d.boundingBox.width,
              height: d.boundingBox.height,
            }
          : null,
        categories: (d.categories || []).map(c => ({
          score: c.score,
          categoryName: c.categoryName || c.displayName || 'person',
        })),
      }));
      self.postMessage({type: 'result', epoch: data.epoch, seq: data.seq, detections});
      return;
    }
    if (data.type === 'close') {
      try {
        detector?.close?.();
      } catch (_) {}
      detector = null;
      initPromise = null;
    }
  } catch (error) {
    self.postMessage({
      type: 'error',
      epoch: data.epoch,
      seq: data.seq,
      message: error?.message || 'Auto focus detector failed',
    });
  }
};
