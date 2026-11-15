/* Precision playback stays local; export jobs use the independent media worker. */
(function () {
  'use strict';
  const C = window.ClipStudyCore;
  const $ = id => document.getElementById(id);
  const dialog = $('clip-study'), video = $('study-video'), stage = $('study-stage');
  const overlaySvg = $('study-coach-overlay'), overlayBadge = $('study-overlay-badge'), walkthroughHud = $('study-walkthrough-hud'), markersBox = $('study-coach-markers');
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const PLAY_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>';
  const PAUSE_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>';
  const SHARE_STORAGE_KEY = 'tennis-share-downloads:v1';
  const shareTimers = new Map();
  function shareKeyFor(session, clip) {
    return session?.id && session?.published_at !== undefined && clip?.id
      ? `${session.id}:${session.published_at}:${clip.id}`
      : '';
  }
  function loadShareDownloads() {
    const map = new Map();
    try {
      const raw = JSON.parse(sessionStorage.getItem(SHARE_STORAGE_KEY) || '{}');
      if (raw && typeof raw === 'object') {
        const entries = Object.entries(raw)
          .filter(([, v]) => v && typeof v === 'object' && !v.cancelled && !v.superseded)
          .sort((a, b) => (b[1].updatedAt || 0) - (a[1].updatedAt || 0))
          .slice(0, 20);
        for (const [k, v] of entries) {
          const item = {...v, abort: null, autoDownload: false};
          if (item.state === 'submitting') {
            item.state = 'failed';
            item.error = 'Interrupted before starting. Prepare video download again.';
          }
          map.set(k, item);
        }
      }
    } catch (_) {}
    return map;
  }
  const shareDownloads = loadShareDownloads();
  function saveShareDownloads() {
    try {
      const entries = [...shareDownloads.entries()]
        .filter(([, v]) => v && !v.cancelled && !v.superseded)
        .sort((a, b) => (b[1].updatedAt || 0) - (a[1].updatedAt || 0))
        .slice(0, 20);
      const out = {};
      for (const [k, v] of entries) {
        out[k] = {
          key: v.key, requestId: v.requestId, jobId: v.jobId || null, version: v.version ?? null,
          state: v.state, selectionKey: v.selectionKey || '', summary: v.summary || '',
          href: v.href || '', isHdr: !!v.isHdr, error: v.error || '', epoch: v.epoch || 0,
          updatedAt: v.updatedAt || Date.now(),
        };
      }
      sessionStorage.setItem(SHARE_STORAGE_KEY, JSON.stringify(out));
    } catch (_) {}
  }
  function clearShareTimer(key) {
    const id = shareTimers.get(key);
    if (id) { clearTimeout(id); shareTimers.delete(key); }
  }
  function renderDownloadCard(owner = context) {
    const card = $('study-download-card'), heading = $('study-download-heading'), summary = $('study-download-summary');
    const cancelBtn = $('study-download-cancel'), readyLink = $('study-download-ready');
    if (!card || !heading || !summary || !cancelBtn || !readyLink) return;
    if (!owner || context !== owner) { card.hidden = true; return; }
    const key = shareKeyFor(owner.session, owner.clip);
    const entry = key ? shareDownloads.get(key) : null;
    if (!entry || entry.cancelled || entry.superseded) {
      card.hidden = true;
      cancelBtn.hidden = true;
      readyLink.hidden = true;
      readyLink.removeAttribute('href');
      return;
    }
    card.hidden = false;
    summary.textContent = entry.error || entry.summary || '';
    if (entry.state === 'submitting' || entry.state === 'queued' || entry.state === 'leased') {
      heading.textContent = 'Preparing video download…';
      summary.textContent = entry.summary || '';
      cancelBtn.hidden = false;
      cancelBtn.disabled = !!entry.cancelling;
      readyLink.hidden = true;
      readyLink.removeAttribute('href');
    } else if (entry.state === 'completed') {
      heading.textContent = entry.isHdr ? 'Prepared HDR video download' : 'Prepared video download';
      summary.textContent = entry.summary || '';
      cancelBtn.hidden = true;
      readyLink.href = entry.href;
      readyLink.hidden = false;
    } else {
      heading.textContent = 'Video download failed';
      cancelBtn.hidden = true;
      readyLink.hidden = true;
      readyLink.removeAttribute('href');
    }
  }
  async function cancelShareJobRemote(jobId, knownVersion = null) {
    if (!jobId) return;
    let version = knownVersion;
    if (version == null) {
      try {
        const cur = await fetch('/api/coaching/jobs/' + encodeURIComponent(jobId));
        if (cur.ok) version = (await cur.json())?.version;
      } catch (_) {}
    }
    try {
      const r = await fetch('/api/coaching/jobs/' + encodeURIComponent(jobId), {
        method: 'PUT',
        headers: {'Content-Type': 'application/json', 'X-Tennis-Request': '1'},
        body: JSON.stringify(version != null ? {action: 'cancel', version} : {action: 'cancel'}),
      });
      if (r.status === 409) {
        const fresh = await fetch('/api/coaching/jobs/' + encodeURIComponent(jobId));
        if (fresh.ok) {
          const data = await fresh.json();
          if (['queued', 'leased'].includes(data?.state)) {
            await fetch('/api/coaching/jobs/' + encodeURIComponent(jobId), {
              method: 'PUT',
              headers: {'Content-Type': 'application/json', 'X-Tennis-Request': '1'},
              body: JSON.stringify({action: 'cancel', version: data.version}),
            });
          }
        }
      }
    } catch (_) {}
  }
  async function pollShareJob(key, entry) {
    clearShareTimer(key);
    const isAlive = () => !entry.cancelled && !entry.superseded && shareDownloads.get(key) === entry;
    if (!isAlive() || !entry.jobId) return;
    const abort = new AbortController();
    entry.abort = abort;
    try {
      const r = await fetch('/api/coaching/jobs/' + entry.jobId, {signal: abort.signal});
      if (!r.ok) throw new Error('Could not check download status. Retrying…');
      const currentJob = await r.json();
      if (!isAlive() || currentJob.id !== entry.jobId) return;
      entry.state = currentJob.state;
      if (currentJob.version != null) entry.version = currentJob.version;
      entry.updatedAt = Date.now();
      const activeOpen = Boolean(context && shareKeyFor(context.session, context.clip) === key);
      if (currentJob.state === 'completed') {
        entry.href = `/api/coaching/exports/${entry.jobId}/excerpt.mp4?download=1`;
        entry.isHdr = String(currentJob.result?.color_output || '').startsWith('hdr10-');
        const shouldAuto = Boolean(entry.autoDownload && activeOpen && context.shareEpoch === entry.epoch);
        entry.autoDownload = false;
        entry.abort = null;
        saveShareDownloads();
        if (activeOpen) {
          if (context.shareEpoch === entry.epoch) context.downloading = false;
          setControls();
          renderDownloadCard(context);
          status(entry.isHdr ? 'High-quality HDR (10-bit HEVC) download ready.' : 'High-quality download ready.');
          if (shouldAuto) $('study-download-ready')?.click();
        }
        return;
      }
      if (currentJob.state === 'failed' || currentJob.state === 'cancelled') {
        entry.abort = null;
        if (currentJob.state === 'cancelled') {
          shareDownloads.delete(key);
        } else {
          entry.error = currentJob.result?.error || 'Video download failed.';
        }
        saveShareDownloads();
        if (activeOpen) {
          if (context.shareEpoch === entry.epoch) {
            context.downloading = false;
            context.shareRequestId = null;
          }
          setControls();
          renderDownloadCard(context);
          status(currentJob.state === 'cancelled' ? 'Video download cancelled.' : entry.error);
        }
        return;
      }
      saveShareDownloads();
      if (isAlive()) shareTimers.set(key, setTimeout(() => pollShareJob(key, entry), 1500));
    } catch (error) {
      if (!isAlive()) return;
      if (context && shareKeyFor(context.session, context.clip) === key && context.shareEpoch === entry.epoch) {
        status(error.message || 'Waiting for download status…');
      }
      shareTimers.set(key, setTimeout(() => pollShareJob(key, entry), 2000));
    }
  }
  let context = null, generation = 0, walkthroughToken = 0;
  const pointers = new Map();
  let drag = null, pinch = null, pinDrag = null, playbackTimer = null;
  function status(message) { if (context) $('study-status').textContent = message; }
  function duration() { return C.finite(video.duration) && video.duration > 0 ? video.duration : 0; }
  function fallbackDuration() { return duration() || (context?.clip?.duration_seconds > 0 ? context.clip.duration_seconds : 1); }
  function ready() { return !!context && video.readyState >= 1 && duration() > 0; }
  function hideCoachOverlay() {
    if (overlaySvg) { overlaySvg.replaceChildren(); overlaySvg.setAttribute('hidden', ''); }
    if (overlayBadge) overlayBadge.hidden = true;
  }
  function hideWalkthroughHud() {
    if (walkthroughHud) { walkthroughHud.replaceChildren(); walkthroughHud.hidden = true; }
  }
  function showWalkthroughHud(nowTry) {
    if (!walkthroughHud) return;
    walkthroughHud.replaceChildren();
    const lines = [];
    if (nowTry?.nowText) {
      const p = document.createElement('p');
      p.className = 'study-hud-line';
      const strong = document.createElement('strong');
      strong.textContent = 'Now: ';
      p.append(strong, document.createTextNode(nowTry.nowText));
      lines.push(p);
    }
    if (nowTry?.tryText) {
      const p = document.createElement('p');
      p.className = 'study-hud-line';
      const strong = document.createElement('strong');
      strong.textContent = 'Try: ';
      p.append(strong, document.createTextNode(nowTry.tryText));
      lines.push(p);
    }
    if (!lines.length) { walkthroughHud.hidden = true; return; }
    walkthroughHud.append(...lines);
    walkthroughHud.hidden = false;
  }
  function clearWalkthroughTimer(owner = context) {
    walkthroughToken++;
    if (owner?.inspection?.timerId) {
      clearTimeout(owner.inspection.timerId);
      owner.inspection.timerId = null;
    }
    if (owner?.inspection) {
      owner.inspection.walkthroughFrozen = false;
    }
  }
  function selectionFromState(src) {
    if (!context) return null;
    const dur = fallbackDuration();
    const bounds = C.clampInterval(src.intervalStart, src.intervalEnd, dur);
    const selSpeed = C.finite(src.speed) && src.speed > 0 ? src.speed : (Number($('study-speed').value) || 0.5);
    if (!ready()) {
      return {start: bounds.start, end: bounds.end, speed: selSpeed, zoom: src.zoom, crop: [0, 0, 1, 1]};
    }
    const sw = stage.clientWidth, sh = stage.clientHeight, w = video.videoWidth, h = video.videoHeight;
    if (sw <= 0 || sh <= 0 || w <= 0 || h <= 0) {
      return {start: bounds.start, end: bounds.end, speed: selSpeed, zoom: src.zoom, crop: [0, 0, 1, 1]};
    }
    const fit = Math.min(sw / w, sh / h) * src.zoom;
    const x0 = C.clamp(((-sw / 2 - src.x) / fit + w / 2) / w, 0, 1);
    const y0 = C.clamp(((-sh / 2 - src.y) / fit + h / 2) / h, 0, 1);
    const x1 = C.clamp(((sw / 2 - src.x) / fit + w / 2) / w, 0, 1);
    const y1 = C.clamp(((sh / 2 - src.y) / fit + h / 2) / h, 0, 1);
    return {start: bounds.start, end: bounds.end, speed: selSpeed, zoom: src.zoom, crop: [x0, y0, x1 - x0, y1 - y0]};
  }
  function ensureInspectionBaseline() {
    if (!context || context.inspection) return;
    const currentSpeed = ready() ? (Number($('study-speed').value) || video.playbackRate || 0.5) : (Number($('study-speed').value) || 0.5);
    const baselineSrc = {
      intervalStart: context.intervalStart,
      intervalEnd: context.intervalEnd,
      intervalDefaultEnd: context.intervalDefaultEnd,
      loop: context.loop,
      zoom: context.zoom,
      x: context.x,
      y: context.y,
      speed: currentSpeed,
      statusText: $('study-status')?.textContent || '',
    };
    context.inspection = {
      savedControls: baselineSrc,
      savedSelection: selectionFromState(baselineSrc),
      mode: 'frame',
      suggestionId: null,
      rangeIndex: -1,
      suggestion: null,
      frame: null,
      poseFrame: null,
      crop: [0, 0, 1, 1],
      historical: false,
      range: null,
      freezeTime: null,
      nowTry: null,
      timerId: null,
      walkthroughFrozen: false,
      walkthroughFrozeThisPass: false,
      lastOverlay: null,
    };
  }
  function setSpeedControl(rate) {
    const num = C.finite(rate) && rate > 0 ? rate : 0.5;
    if (ready()) {
      try { video.playbackRate = num; }
      catch (_) { try { video.playbackRate = 0.5; } catch (__) {} }
    }
    const sel = $('study-speed');
    if (sel) {
      const target = String(video.playbackRate || num);
      if ([...sel.options].some(opt => opt.value === target)) sel.value = target;
    }
  }
  function activePlaybackBounds() {
    if (!context) return {start: 0, end: 0, loop: false};
    const insp = context.inspection;
    if (insp && (insp.mode === 'loop' || insp.mode === 'walkthrough') && insp.range) {
      return {start: insp.range.start, end: insp.range.end, loop: true};
    }
    return {start: context.intervalStart, end: context.intervalEnd, loop: context.loop};
  }
  function renderCoachMarkers() {
    if (!markersBox || !context) {
      if (markersBox) markersBox.replaceChildren();
      return;
    }
    markersBox.replaceChildren();
    const dur = fallbackDuration();
    if (!(dur > 0) || !Array.isArray(context.coachMarkers)) return;
    for (const m of context.coachMarkers) {
      if (!m) continue;
      if (m.type === 'range' && C.finite(m.start) && C.finite(m.end)) {
        const s = C.clamp(m.start, 0, dur), e = C.clamp(Math.max(s, m.end), 0, dur);
        const span = document.createElement('div');
        span.className = 'study-coach-marker-range' + (m.active ? ' is-active' : '');
        span.style.left = `${(s / dur) * 100}%`;
        span.style.width = `${Math.max(0.8, ((e - s) / dur) * 100)}%`;
        markersBox.append(span);
      } else if (m.type === 'frame' && C.finite(m.time)) {
        const t = C.clamp(m.time, 0, dur);
        const tick = document.createElement('div');
        tick.className = 'study-coach-marker-tick' + (m.primary ? ' is-primary' : '') + (m.active ? ' is-active' : '');
        tick.style.left = `${(t / dur) * 100}%`;
        markersBox.append(tick);
      }
    }
  }
  function renderCoachOverlay() {
    if (!context || !context.inspection || !ready()) {
      hideCoachOverlay();
      return;
    }
    const insp = context.inspection;
    const res = C.resolveSparseOverlay({
      suggestion: insp.suggestion,
      frame: insp.frame,
      poseFrame: insp.poseFrame,
      crop: insp.crop,
      paused: video.paused,
      currentTime: video.currentTime,
      historical: insp.historical,
      overlayEnabled: context.overlayEnabled !== false,
    });
    insp.lastOverlay = res;
    if (!res.visible || !overlaySvg) {
      hideCoachOverlay();
      return;
    }
    const sw = stage.clientWidth, sh = stage.clientHeight, vw = video.videoWidth, vh = video.videoHeight;
    if (sw <= 0 || sh <= 0 || vw <= 0 || vh <= 0) {
      hideCoachOverlay();
      return;
    }
    overlaySvg.replaceChildren();
    overlaySvg.setAttribute('viewBox', `0 0 ${sw} ${sh}`);
    overlaySvg.setAttribute('width', String(sw));
    overlaySvg.setAttribute('height', String(sh));
    if (res.focusBox) {
      const box = C.projectBoxToStage(res.focusBox, sw, sh, vw, vh, context.zoom, context.x, context.y);
      if (box && box.width > 0 && box.height > 0) {
        const rect = document.createElementNS(SVG_NS, 'rect');
        rect.setAttribute('class', 'study-overlay-focus');
        rect.setAttribute('x', box.left.toFixed(1));
        rect.setAttribute('y', box.top.toFixed(1));
        rect.setAttribute('width', box.width.toFixed(1));
        rect.setAttribute('height', box.height.toFixed(1));
        rect.setAttribute('rx', '8');
        overlaySvg.append(rect);
      }
    }
    const projected = {};
    for (const [name, pt] of Object.entries(res.joints || {})) {
      const pos = C.projectPointToStage(pt.x, pt.y, sw, sh, vw, vh, context.zoom, context.x, context.y);
      if (pos) projected[name] = pos;
    }
    for (const [a, b] of res.segments || []) {
      const pa = projected[a], pb = projected[b];
      if (!pa || !pb) continue;
      const line = document.createElementNS(SVG_NS, 'line');
      line.setAttribute('class', 'study-overlay-segment');
      line.setAttribute('x1', pa.x.toFixed(1));
      line.setAttribute('y1', pa.y.toFixed(1));
      line.setAttribute('x2', pb.x.toFixed(1));
      line.setAttribute('y2', pb.y.toFixed(1));
      overlaySvg.append(line);
    }
    for (const [name, pos] of Object.entries(projected)) {
      const dot = document.createElementNS(SVG_NS, 'circle');
      dot.setAttribute('class', 'study-overlay-joint');
      dot.setAttribute('data-joint', name);
      dot.setAttribute('cx', pos.x.toFixed(1));
      dot.setAttribute('cy', pos.y.toFixed(1));
      dot.setAttribute('r', '4.5');
      overlaySvg.append(dot);
    }
    if (!overlaySvg.childElementCount) {
      hideCoachOverlay();
      return;
    }
    overlaySvg.removeAttribute('hidden');
    if (overlayBadge) {
      overlayBadge.textContent = res.label || 'Approximate body guide';
      overlayBadge.hidden = false;
    }
  }
  function stopWalkthroughMode(reason = 'manual') {
    if (!context?.inspection) return;
    const insp = context.inspection;
    const wasWalkthrough = insp.mode === 'walkthrough' || insp.mode === 'loop';
    clearWalkthroughTimer(context);
    if (wasWalkthrough) {
      insp.mode = 'frame';
      hideWalkthroughHud();
      setSpeedControl(insp.savedControls.speed);
      $('study-loop')?.setAttribute('aria-pressed', String(context.loop));
      window.dispatchEvent(new CustomEvent('study-inspection-changed'));
    }
    if (reason === 'rejected') {
      status('Tap Play or Walkthrough again to start playback.');
    }
  }
  function exitInspection({keepCurrentBounds = false, restoreZoomPan = true, pauseVideo = true} = {}) {
    if (!context?.inspection) return;
    const insp = context.inspection;
    clearWalkthroughTimer(context);
    if (pauseVideo && ready() && !video.paused) video.pause();
    context.inspection = null;
    hideCoachOverlay();
    hideWalkthroughHud();
    const saved = insp.savedControls;
    if (!keepCurrentBounds) {
      context.intervalStart = saved.intervalStart;
      context.intervalEnd = saved.intervalEnd;
      context.intervalDefaultEnd = saved.intervalDefaultEnd;
      syncIntervalUI();
    }
    context.loop = saved.loop;
    $('study-loop')?.setAttribute('aria-pressed', String(context.loop));
    setSpeedControl(saved.speed);
    if ($('study-status')) $('study-status').textContent = saved.statusText || '';
    if (restoreZoomPan) {
      context.zoom = saved.zoom;
      context.x = saved.x;
      context.y = saved.y;
      applyView(false);
    }
    if (ready()) {
      const clamped = C.clampToInterval(video.currentTime, context.intervalStart, context.intervalEnd, duration());
      if (Math.abs(clamped - video.currentTime) > 0.002) video.currentTime = clamped;
    }
    syncTime();
    setControls();
    window.dispatchEvent(new CustomEvent('study-inspection-changed'));
  }
  function cryptoId() {
    return 'share-' + Array.from(crypto.getRandomValues(new Uint8Array(16)), v => v.toString(16).padStart(2, '0')).join('');
  }
  function invalidateShare(owner) {
    owner.shareEpoch = (owner.shareEpoch || 0) + 1;
    owner.downloading = false;
  }
  function notifySelectionChanged() {
    if (!context) return;
    const selection = window.ClipStudy?.coachingSelection?.();
    const selKey = JSON.stringify(selection && [selection.start, selection.end, selection.speed, selection.crop]);
    if (selKey !== context.shareSelectionKey) {
      const hadActiveForeground = context.downloading;
      context.shareEpoch = (context.shareEpoch || 0) + 1;
      context.shareSelectionKey = selKey;
      context.downloading = false;
      context.shareRequestId = null;
      const sk = shareKeyFor(context.session, context.clip);
      const active = sk ? shareDownloads.get(sk) : null;
      if (active) {
        active.autoDownload = false;
        saveShareDownloads();
      }
      renderDownloadCard(context);
      if (hadActiveForeground) status('');
      setControls();
    }
    window.dispatchEvent(new CustomEvent('study-selection-changed'));
  }
  function syncIntervalUI() {
    if (!context) return;
    const dur = fallbackDuration();
    const bounds = C.clampInterval(context.intervalStart, context.intervalEnd, dur);
    context.intervalStart = bounds.start;
    context.intervalEnd = bounds.end;
    const startPct = dur > 0 ? C.clamp((bounds.start / dur) * 100, 0, 100) : 0;
    const endPct = dur > 0 ? C.clamp((bounds.end / dur) * 100, 0, 100) : 100;
    const pinStart = $('study-pin-start'), pinEnd = $('study-pin-end'), fill = $('study-interval-fill');
    pinStart.style.left = `${startPct}%`;
    pinEnd.style.left = `${endPct}%`;
    fill.style.left = `${startPct}%`;
    fill.style.width = `${Math.max(0, endPct - startPct)}%`;
    for (const [pin, val] of [[pinStart, bounds.start], [pinEnd, bounds.end]]) {
      pin.setAttribute('aria-valuemin', '0');
      pin.setAttribute('aria-valuemax', String(dur));
      pin.setAttribute('aria-valuenow', String(val));
      pin.setAttribute('aria-valuetext', C.clock(val));
      pin.dataset.value = String(val);
    }
    if ($('study-interval-readout')) {
      $('study-interval-readout').textContent = `${C.clock(bounds.start)}–${C.clock(bounds.end)}`;
    }
  }
  function setIntervalBounds(nextStart, nextEnd, changed = 'end') {
    if (!context) return;
    context.intervalDefaultEnd = false;
    const dur = fallbackDuration();
    const bounds = C.clampInterval(nextStart, nextEnd, dur, changed);
    context.intervalStart = bounds.start;
    context.intervalEnd = bounds.end;
    syncIntervalUI();
    if (ready() && !context.capturing) {
      const clamped = C.clampToInterval(video.currentTime, bounds.start, bounds.end, dur);
      if (Math.abs(clamped - video.currentTime) > 0.002) {
        video.currentTime = clamped;
      }
    }
    syncTime();
    notifySelectionChanged();
  }
  for (const [id, which] of [['study-pin-start', 'start'], ['study-pin-end', 'end']]) {
    const pin = $(id);
    if (!pin) continue;
    Object.defineProperty(pin, 'value', {
      configurable: true,
      get() { return pin.dataset.value ?? pin.getAttribute('aria-valuenow') ?? '0'; },
      set(v) { pin.dataset.value = String(v); }
    });
    const onValueEvent = () => {
      if (!context) return;
      const num = Number(pin.dataset.value);
      if (!Number.isFinite(num)) return;
      if (which === 'start') setIntervalBounds(num, context.intervalEnd, 'start');
      else setIntervalBounds(context.intervalStart, num, 'end');
    };
    pin.addEventListener('input', onValueEvent);
    pin.addEventListener('change', onValueEvent);
    pin.addEventListener('pointerdown', event => {
      if (!context || pin.disabled) return;
      if (context.inspection) exitInspection({keepCurrentBounds: true, restoreZoomPan: false, pauseVideo: false});
      event.preventDefault();
      event.stopPropagation();
      let chosen = which;
      const bar = $('study-interval-bar');
      if (bar) {
        const rect = bar.getBoundingClientRect();
        const ratio = C.clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
        const pointerTime = ratio * fallbackDuration();
        const distStart = Math.abs(pointerTime - context.intervalStart);
        const distEnd = Math.abs(pointerTime - context.intervalEnd);
        if (Math.abs(distStart - distEnd) > 1e-6) {
          chosen = distStart < distEnd ? 'start' : 'end';
        }
      }
      const activePin = $(chosen === 'start' ? 'study-pin-start' : 'study-pin-end') || pin;
      pinDrag = {which: chosen, pointerId: event.pointerId};
      if (activePin.setPointerCapture) {
        try { activePin.setPointerCapture(event.pointerId); } catch (_) {}
      }
      activePin.focus({preventScroll: true});
    });
    pin.addEventListener('keydown', event => {
      if (!context || pin.disabled) return;
      const dur = fallbackDuration();
      const step = event.shiftKey ? 1 : 0.1;
      let target = which === 'start' ? context.intervalStart : context.intervalEnd;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') target -= step;
      else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') target += step;
      else if (event.key === 'Home') target = 0;
      else if (event.key === 'End') target = dur;
      else return;
      if (context.inspection) exitInspection({keepCurrentBounds: true, restoreZoomPan: false, pauseVideo: false});
      event.preventDefault();
      event.stopPropagation();
      if (which === 'start') setIntervalBounds(target, context.intervalEnd, 'start');
      else setIntervalBounds(context.intervalStart, target, 'end');
    });
  }
  window.addEventListener('pointermove', event => {
    if (!pinDrag || !context) return;
    const bar = $('study-interval-bar');
    if (!bar) return;
    const rect = bar.getBoundingClientRect();
    const ratio = C.clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
    const target = ratio * fallbackDuration();
    if (pinDrag.which === 'start') setIntervalBounds(target, context.intervalEnd, 'start');
    else setIntervalBounds(context.intervalStart, target, 'end');
  });
  for (const name of ['pointerup', 'pointercancel']) {
    window.addEventListener(name, () => { pinDrag = null; });
  }
  function syncTime() {
    if (!context) return;
    const t = C.clamp(video.currentTime || 0, 0, duration() || 0);
    $('study-current').textContent = C.clock(t);
    $('study-seek').value = t;
    $('study-seek').setAttribute('aria-valuetext', C.clock(t) + ' of ' + C.clock(duration()));
    const playBtn = $('study-play');
    const label = video.paused ? 'Play' : 'Pause';
    playBtn.setAttribute('aria-label', label);
    playBtn.setAttribute('title', label);
    const nextIcon = video.paused ? PLAY_ICON : PAUSE_ICON;
    if (playBtn.dataset.iconState !== label) {
      playBtn.innerHTML = nextIcon;
      playBtn.dataset.iconState = label;
    }
  }
  function isAutoDetectSupported() {
    return !window.__TENNIS_AUTOFOCUS_FORCE_UNSUPPORTED && typeof Worker === 'function' && typeof WebAssembly === 'object' && !!window.AutoFocusCore;
  }
  function applyDetectedWindow(owner, win) {
    if (!owner || !win || context !== owner) return;
    const dur = fallbackDuration();
    setIntervalBounds(win.start, win.end, 'end');
    if (win.focusBox && ready() && window.AutoFocusCore?.targetFraming) {
      const initial = window.AutoFocusCore.targetFraming(win.focusBox, stage.clientWidth, stage.clientHeight, video.videoWidth, video.videoHeight, {minZoom: 1, maxZoom: 4});
      const snappedZoom = C.clamp(Math.round((initial?.zoom || 1) * 4) / 4, 1, 4);
      const snapped = window.AutoFocusCore.targetFraming(win.focusBox, stage.clientWidth, stage.clientHeight, video.videoWidth, video.videoHeight, {minZoom: snappedZoom, maxZoom: snappedZoom});
      owner.zoom = snapped?.zoom || 1;
      owner.x = snapped?.x || 0;
      owner.y = snapped?.y || 0;
      applyView(true);
    }
    video.pause();
    if (video.readyState >= 1) video.currentTime = C.clamp(win.start, 0, dur);
    owner.lastTime = C.clamp(win.start, 0, dur);
    syncTime();
  }
  function setControls() {
    const enabled = ready() && !context.capturing;
    for (const id of ['study-seek', 'study-pin-start', 'study-pin-end', 'study-back', 'study-play', 'study-forward', 'study-full-clip', 'study-reset-framing', 'study-download-interval']) {
      if ($(id)) $(id).disabled = !enabled || (id === 'study-download-interval' && !!context?.downloading);
    }
    const autoBtn = $('study-auto-detect'), autoCancelBtn = $('study-auto-cancel');
    const unavailable = !!context?.autoDetectUnavailable || !isAutoDetectSupported();
    const scanning = !!context?.scanningAutoDetect;
    const count = context?.detectedWindows?.length || 0;
    const idx = context?.detectedWindowIndex ?? -1;
    if (autoBtn) {
      autoBtn.disabled = !enabled || unavailable || scanning || !!context?.capturing;
      autoBtn.textContent = scanning ? 'Scanning…' : unavailable ? 'Auto-frame unavailable' : 'Auto-frame motion';
    }
    if (autoCancelBtn) autoCancelBtn.hidden = !scanning;
    const stepper = $('study-auto-stepper'), prevAuto = $('study-auto-prev'), nextAuto = $('study-auto-next'), countAuto = $('study-auto-count');
    if (stepper) {
      const hasWindows = !scanning && Array.isArray(context?.detectedWindows) && count > 0;
      stepper.hidden = !hasWindows;
      if (hasWindows) {
        if (countAuto) countAuto.textContent = `${Math.max(0, idx) + 1} of ${count}`;
        if (prevAuto) prevAuto.disabled = !enabled || count <= 1;
        if (nextAuto) nextAuto.disabled = !enabled || count <= 1;
      }
    }
    const frameReady = enabled && video.readyState >= 2 && !context?.capturing;
    for (const id of ['study-capture', 'study-sample']) $(id).disabled = !frameReady;
    $('study-copy').disabled = !!context?.capturing;
    for (const button of document.querySelectorAll('#study-frames button')) button.disabled = !!context?.capturing;
    window.dispatchEvent(new CustomEvent('study-capture-state', {detail: {capturing: !!context?.capturing}}));
  }
  function enforceIntervalPlayback() {
    if (!context || !ready() || context.capturing || video.paused) return;
    const insp = context.inspection;
    if (insp && insp.mode === 'walkthrough' && !insp.walkthroughFrozen && !insp.walkthroughFrozeThisPass && C.finite(insp.freezeTime)) {
      const boundsNow = activePlaybackBounds();
      if (video.currentTime >= insp.freezeTime - 0.025 && (boundsNow.end - insp.freezeTime > 0.02 || video.currentTime >= boundsNow.end - 0.025)) {
        const freezeOwner = context, gen = generation, tok = ++walkthroughToken;
        insp.walkthroughFrozen = true;
        insp.walkthroughFrozeThisPass = true;
        video.pause();
        video.currentTime = C.clamp(insp.freezeTime, boundsNow.start, boundsNow.end);
        syncTime();
        renderCoachOverlay();
        insp.timerId = setTimeout(async () => {
          if (context !== freezeOwner || generation !== gen || context.inspection !== insp || tok !== walkthroughToken) return;
          insp.timerId = null;
          insp.walkthroughFrozen = false;
          if (video.currentTime >= boundsNow.end - 0.025) {
            video.currentTime = boundsNow.start;
            insp.walkthroughFrozeThisPass = false;
          }
          hideCoachOverlay();
          try { await video.play(); } catch (_) { stopWalkthroughMode('rejected'); }
        }, 1200);
        return;
      }
    }
    const bounds = activePlaybackBounds();
    const decision = C.resolveIntervalPlayback(video.currentTime, bounds.start, bounds.end, bounds.loop);
    if (decision.action === 'seek') {
      video.currentTime = decision.time;
      syncTime();
    } else if (decision.action === 'loop') {
      if (insp) insp.walkthroughFrozeThisPass = false;
      video.currentTime = decision.time;
      syncTime();
      if (video.paused) video.play().catch(() => { if (insp?.mode === 'walkthrough') stopWalkthroughMode('rejected'); });
    } else if (decision.action === 'pause') {
      video.pause();
      video.currentTime = decision.time;
      syncTime();
    }
  }
  function seek(at) {
    if (!ready() || context.capturing) return;
    if (context.inspection?.mode === 'walkthrough' || context.inspection?.mode === 'loop') {
      stopWalkthroughMode('manual');
    }
    video.pause();
    const bounds = activePlaybackBounds();
    video.currentTime = C.clampToInterval(at, bounds.start, bounds.end, duration());
    syncTime();
    renderCoachOverlay();
  }
  async function togglePlay() {
    if (!ready() || context.capturing) return;
    if (context.inspection?.mode === 'walkthrough' && (!video.paused || context.inspection.walkthroughFrozen)) {
      video.pause();
      stopWalkthroughMode('manual');
      syncTime();
      renderCoachOverlay();
      return;
    }
    if (!video.paused) { video.pause(); return; }
    const bounds = activePlaybackBounds();
    if (video.currentTime < bounds.start - 0.005 || video.currentTime >= bounds.end - 0.02) {
      video.currentTime = bounds.start;
    }
    try { await video.play(); } catch (_) { status('Tap Play again, or download the clip if this browser cannot play it.'); }
  }
  function applyView(selectionChanged = true) {
    if (!context) return;
    const bounds = C.panBounds(stage.clientWidth, stage.clientHeight, video.videoWidth, video.videoHeight, context.zoom);
    context.x = C.clamp(context.x, -bounds.x, bounds.x);
    context.y = C.clamp(context.y, -bounds.y, bounds.y);
    video.style.transform = `translate(${context.x}px, ${context.y}px) scale(${context.zoom})`;
    stage.classList.toggle('is-zoomed', context.zoom > 1);
    const value = Number(context.zoom.toFixed(2)) + '×';
    $('study-zoom-value').textContent = value;
    $('study-zoom-badge').textContent = value;
    $('study-zoom').value = context.zoom;
    renderCoachOverlay();
    if (selectionChanged && !context.inspection) notifySelectionChanged();
  }
  function resetView() { if (context) { context.zoom = 1; context.x = 0; context.y = 0; applyView(); } }
  function stopCapture() { if (context?.captureController) context.captureController.abort(); }
  function clearFrames(owner) { for (const frame of owner.frames) URL.revokeObjectURL(frame.url); owner.frames = []; }
  function close() { if (dialog.open) dialog.close(); }
  dialog.addEventListener('close', () => {
    const old = context; if (!old) return;
    clearWalkthroughTimer(old);
    window.Coach?.close();
    clearInterval(playbackTimer); playbackTimer = null;
    const sk = shareKeyFor(old.session, old.clip);
    const activeShare = sk ? shareDownloads.get(sk) : null;
    if (activeShare) { activeShare.autoDownload = false; saveShareDownloads(); }
    invalidateShare(old);
    context = null; generation++;
    hideCoachOverlay(); hideWalkthroughHud();
    if (markersBox) markersBox.replaceChildren();
    old.fetchController.abort(); old.captureController?.abort();
    video.pause(); video.removeAttribute('src'); video.load();
    pointers.clear(); drag = pinch = pinDrag = null; stage.classList.remove('is-dragging', 'is-zoomed');
    document.body.classList.remove('study-open');
    if (old.player.isConnected && old.player.getAttribute('src') === old.url) {
      // Return to the studied position, paused; never surprise the user with autoplay.
      if (old.player.readyState >= 1) old.player.currentTime = C.clamp(old.captureReturnTime ?? old.lastTime ?? 0, 0, old.player.duration || 0);
      old.player.playbackRate = old.parentRate;
    }
    clearFrames(old); $('study-frames').replaceChildren();
    if (old.returnFocus?.isConnected) old.returnFocus.focus({preventScroll:true});
  });
  $('study-close').addEventListener('click', close);
  video.addEventListener('loadedmetadata', () => {
    if (!context) return;
    if (!duration()) { mediaError(); return; }
    $('study-seek').max = duration();
    $('study-duration').textContent = C.clock(duration());
    if (context.intervalDefaultEnd) {
      context.intervalStart = 0;
      context.intervalEnd = duration();
    } else {
      const bounds = C.clampInterval(context.intervalStart, context.intervalEnd, duration());
      context.intervalStart = bounds.start;
      context.intervalEnd = bounds.end;
    }
    syncIntervalUI();
    renderCoachMarkers();
    video.playbackRate = Number($('study-speed').value) || .5;
    video.currentTime = C.clampToInterval(context.startTime, context.intervalStart, context.intervalEnd, duration());
    setControls(); syncTime(); applyView();
  });
  video.addEventListener('loadeddata', setControls);
  video.addEventListener('canplay', setControls);
  video.addEventListener('timeupdate', () => {
    if (!context) return;
    enforceIntervalPlayback();
    context.lastTime = video.currentTime;
    syncTime();
    if (video.paused) renderCoachOverlay();
  });
  video.addEventListener('seeked', () => { if (context) context.lastTime = video.currentTime; syncTime(); renderCoachOverlay(); });
  video.addEventListener('play', () => {
    hideCoachOverlay();
    syncTime();
    clearInterval(playbackTimer);
    playbackTimer = setInterval(enforceIntervalPlayback, 30);
  });
  video.addEventListener('pause', () => {
    clearInterval(playbackTimer);
    playbackTimer = null;
    syncTime();
    renderCoachOverlay();
  });
  video.addEventListener('ended', () => {
    clearInterval(playbackTimer);
    playbackTimer = null;
    const bounds = activePlaybackBounds();
    if (context && bounds.loop && ready()) {
      if (context.inspection) context.inspection.walkthroughFrozeThisPass = false;
      video.currentTime = bounds.start;
      video.play().catch(() => { if (context?.inspection?.mode === 'walkthrough') stopWalkthroughMode('rejected'); });
      return;
    }
    if (context && ready()) video.currentTime = bounds.end;
    syncTime();
    renderCoachOverlay();
  });
  function mediaError() {
    if (!context) return;
    video.pause();
    $('study-media-error').hidden = false;
    $('study-media-error').textContent = 'This browser could not play the clip. Download it to review in a compatible player. HDR/HEVC support varies by device.';
    for (const id of ['study-seek','study-pin-start','study-pin-end','study-back','study-play','study-forward','study-full-clip','study-reset-framing','study-auto-detect','study-auto-prev','study-auto-next','study-capture','study-sample','study-download-interval']) {
      if ($(id)) $(id).disabled = true;
    }
  }
  video.addEventListener('error', mediaError);
  $('study-play').addEventListener('click', togglePlay);
  $('study-back').addEventListener('click', () => seek(video.currentTime - .1));
  $('study-forward').addEventListener('click', () => seek(video.currentTime + .1));
  $('study-seek').addEventListener('input', event => seek(Number(event.target.value)));
  $('study-speed').addEventListener('change', event => {
    if (context?.inspection) stopWalkthroughMode('manual');
    try { video.playbackRate = Number(event.target.value); }
    catch (_) { video.playbackRate = .5; event.target.value = '0.5'; status('That speed is unavailable in this browser. Using 0.5×.'); }
    if (context?.inspection) {
      context.inspection.savedControls.speed = video.playbackRate;
      if (context.inspection.savedSelection) context.inspection.savedSelection.speed = video.playbackRate;
    }
    notifySelectionChanged();
  });
  $('study-loop').addEventListener('click', () => {
    if (!context) return;
    if (context.inspection) exitInspection({keepCurrentBounds: false, restoreZoomPan: false, pauseVideo: false});
    context.loop = !context.loop;
    $('study-loop').setAttribute('aria-pressed', String(context.loop));
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && context?.inspection?.mode === 'walkthrough') {
      video.pause();
      stopWalkthroughMode('hidden');
      renderCoachOverlay();
    }
  });
  $('study-zoom').addEventListener('input', event => { if (context) { context.zoom = Number(event.target.value); applyView(); } });
  $('study-download-cancel')?.addEventListener('click', async () => {
    if (!context) return;
    const key = shareKeyFor(context.session, context.clip);
    const entry = key ? shareDownloads.get(key) : null;
    if (!entry) return;
    entry.cancelled = true;
    entry.cancelling = true;
    clearShareTimer(key);
    entry.abort?.abort();
    entry.abort = null;
    shareDownloads.delete(key);
    saveShareDownloads();
    context.downloading = false;
    context.shareRequestId = null;
    renderDownloadCard(context);
    setControls();
    status('Video download cancelled.');
    if (entry.jobId) await cancelShareJobRemote(entry.jobId, entry.version);
  });
  $('study-download-interval').addEventListener('click', async () => {
    if (!context || !ready() || context.downloading) return;
    notifySelectionChanged();
    const owner = context;
    const sel = window.ClipStudy.coachingSelection();
    if (!sel) return;
    const key = shareKeyFor(owner.session, owner.clip);
    if (!key) return;
    const prev = shareDownloads.get(key);
    if (prev) {
      prev.superseded = true;
      prev.autoDownload = false;
      prev.abort?.abort();
      clearShareTimer(key);
    }
    const epoch = ++owner.shareEpoch;
    const requestId = owner.shareRequestId || cryptoId();
    owner.shareRequestId = requestId;
    const abort = new AbortController();
    const entry = {
      key,
      requestId,
      jobId: null,
      version: null,
      state: 'submitting',
      selectionKey: owner.shareSelectionKey || JSON.stringify([sel.start, sel.end, sel.speed, sel.crop]),
      summary: C.formatExcerptSummary(sel, fallbackDuration()),
      href: '',
      isHdr: false,
      error: '',
      epoch,
      autoDownload: true,
      cancelled: false,
      cancelling: false,
      superseded: false,
      abort,
      updatedAt: Date.now(),
    };
    shareDownloads.set(key, entry);
    saveShareDownloads();
    owner.downloading = true;
    renderDownloadCard(owner);
    setControls();
    status('Preparing video download…');
    const isAlive = () => !entry.cancelled && !entry.superseded && shareDownloads.get(key) === entry;
    try {
      const response = await fetch('/api/coaching/exports', {
        method: 'POST',
        signal: abort.signal,
        headers: {'Content-Type': 'application/json', 'X-Tennis-Request': '1'},
        body: JSON.stringify({
          source_id: owner.session.id,
          clip_id: owner.clip.id,
          publication: owner.session.published_at,
          start: sel.start,
          end: sel.end,
          speed: sel.speed,
          crop: sel.crop,
          intent: 'share',
          quality: 'high',
          request_id: requestId
        })
      });
      const job = await response.json();
      if (entry.cancelled && job?.id) {
        await cancelShareJobRemote(job.id, job.version);
        return;
      }
      if (!response.ok) throw new Error(job.error || 'Could not prepare video download');
      if (!isAlive()) return;
      entry.jobId = job.id;
      entry.version = job.version ?? null;
      entry.state = job.state || 'queued';
      entry.abort = null;
      entry.updatedAt = Date.now();
      if (job.state === 'completed') {
        entry.href = `/api/coaching/exports/${job.id}/excerpt.mp4?download=1`;
        entry.isHdr = String(job.result?.color_output || '').startsWith('hdr10-');
        const shouldAuto = Boolean(entry.autoDownload && context === owner && owner.shareEpoch === epoch);
        entry.autoDownload = false;
        saveShareDownloads();
        if (context === owner) {
          if (owner.shareEpoch === epoch) owner.downloading = false;
          setControls();
          renderDownloadCard(owner);
          status(entry.isHdr ? 'High-quality HDR (10-bit HEVC) download ready.' : 'High-quality download ready.');
          if (shouldAuto) $('study-download-ready')?.click();
        }
        return;
      }
      saveShareDownloads();
      if (context === owner) renderDownloadCard(owner);
      await pollShareJob(key, entry);
    } catch (error) {
      if (entry.cancelled || entry.superseded || shareDownloads.get(key) !== entry) return;
      entry.abort = null;
      entry.state = 'failed';
      entry.error = error.message || 'Could not prepare video download';
      entry.updatedAt = Date.now();
      saveShareDownloads();
      if (context === owner) {
        if (owner.shareEpoch === epoch) owner.downloading = false;
        renderDownloadCard(owner);
        setControls();
        status(entry.error);
      }
    }
  });
  dialog.addEventListener('keydown', event => {
    if (!context || event.ctrlKey || event.metaKey || event.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
    if (event.target.classList?.contains('study-interval-pin')) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault(); seek(video.currentTime + (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 1 : .1));
    } else if (event.code === 'Space' && !event.target.closest('button,a,summary')) { event.preventDefault(); togglePlay(); }
  });
  const distance = values => Math.hypot(values[0].x-values[1].x, values[0].y-values[1].y);
  stage.addEventListener('pointerdown', event => {
    if (!context) return;
    pointers.set(event.pointerId, {x:event.clientX,y:event.clientY});
    if (pointers.size === 2) { pinch = {distance:distance([...pointers.values()]),zoom:context.zoom}; drag = null; }
    else drag = {x:event.clientX,y:event.clientY,panX:context.x,panY:context.y};
    if (context.zoom > 1 || pointers.size > 1) { stage.setPointerCapture(event.pointerId); stage.classList.add('is-dragging'); }
  });
  stage.addEventListener('pointermove', event => {
    if (!context || !pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if (pinch && pointers.size === 2) context.zoom=C.clamp(pinch.zoom*distance([...pointers.values()])/Math.max(1,pinch.distance),1,4);
    else if (drag && context.zoom > 1) { context.x=drag.panX+event.clientX-drag.x;context.y=drag.panY+event.clientY-drag.y; }
    applyView();
  });
  for (const name of ['pointerup','pointercancel','lostpointercapture']) stage.addEventListener(name,event=>{pointers.delete(event.pointerId);drag=pinch=null;stage.classList.remove('is-dragging');});
  stage.addEventListener('pointerleave',event=>{if(!stage.hasPointerCapture(event.pointerId)){pointers.delete(event.pointerId);drag=pinch=null;}});
  // Status text and tool disclosures can resize the viewport during a render.
  // Keep that job's frozen crop; only an explicit selection edit replaces it.
  if (window.ResizeObserver) new ResizeObserver(() => applyView(false)).observe(stage);
  function fact(label, value) {
    const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');
    dt.textContent=label;dd.textContent=value;row.append(dt,dd);return row;
  }
  window.ClipStudy = {
  coachingSelection() {
    if (!context) return null;
    if (context.inspection?.savedSelection) {
      const saved = context.inspection.savedSelection;
      return {start: saved.start, end: saved.end, speed: saved.speed, zoom: saved.zoom, crop: [...saved.crop]};
    }
    return selectionFromState({
      intervalStart: context.intervalStart,
      intervalEnd: context.intervalEnd,
      speed: ready() ? video.playbackRate : (Number($('study-speed').value) || 0.5),
      zoom: context.zoom,
      x: context.x,
      y: context.y,
    });
  },
  setInterval(start, end, changed = 'end') {
    setIntervalBounds(start, end, changed);
  },
  setCoachMarkers(markers = []) {
    if (!context) return;
    context.coachMarkers = Array.isArray(markers) ? markers : [];
    renderCoachMarkers();
  },
  toggleOverlay(explicit) {
    if (!context) return true;
    context.overlayEnabled = typeof explicit === 'boolean' ? explicit : !context.overlayEnabled;
    renderCoachOverlay();
    window.dispatchEvent(new CustomEvent('study-inspection-changed'));
    return context.overlayEnabled;
  },
  getInspectionState() {
    if (!context) return null;
    if (!context.inspection) return null;
    return {
      mode: context.inspection.mode,
      suggestionId: context.inspection.suggestionId,
      rangeIndex: context.inspection.rangeIndex,
      frameId: context.inspection.frame?.frame_id || '',
      overlayEnabled: context.overlayEnabled !== false,
      lastOverlay: context.inspection.lastOverlay || null,
    };
  },
  exitInspection(opts) {
    exitInspection(opts);
  },
  inspectSuggestion({suggestion = null, frame = null, poseFrame = null, crop = [0, 0, 1, 1], range = null, rangeIndex = -1, historical = false} = {}) {
    if (!context) return null;
    ensureInspectionBaseline();
    clearWalkthroughTimer(context);
    const insp = context.inspection;
    insp.mode = 'frame';
    insp.suggestionId = suggestion?.id || suggestion?.response_index || null;
    insp.rangeIndex = rangeIndex;
    insp.suggestion = suggestion;
    insp.frame = frame;
    insp.poseFrame = poseFrame;
    insp.crop = C.normalizeCrop(crop);
    insp.historical = !!historical;
    insp.range = range;
    insp.freezeTime = null;
    insp.nowTry = null;
    insp.walkthroughFrozeThisPass = false;
    hideWalkthroughHud();
    setSpeedControl(insp.savedControls.speed);
    if (ready()) {
      video.pause();
      const target = frame && C.finite(frame.clip_seconds) ? frame.clip_seconds : (range ? range.start : video.currentTime);
      video.currentTime = C.clamp(target, 0, duration());
    } else if (frame && C.finite(frame.clip_seconds)) {
      context.startTime = frame.clip_seconds;
    }
    syncTime();
    renderCoachOverlay();
    window.dispatchEvent(new CustomEvent('study-inspection-changed'));
    return insp.lastOverlay;
  },
  async loopMoment({suggestion = null, frame = null, poseFrame = null, crop = [0, 0, 1, 1], range = null, rangeIndex = -1, historical = false} = {}) {
    if (!context || !range || !C.finite(range.start) || !C.finite(range.end)) return false;
    ensureInspectionBaseline();
    clearWalkthroughTimer(context);
    const insp = context.inspection;
    insp.mode = 'loop';
    insp.suggestionId = suggestion?.id || null;
    insp.rangeIndex = rangeIndex;
    insp.suggestion = suggestion;
    insp.frame = frame;
    insp.poseFrame = poseFrame;
    insp.crop = C.normalizeCrop(crop);
    insp.historical = !!historical;
    insp.range = C.clampInterval(range.start, range.end, fallbackDuration(), 'end');
    insp.freezeTime = null;
    insp.nowTry = null;
    insp.walkthroughFrozeThisPass = false;
    hideWalkthroughHud();
    hideCoachOverlay();
    setSpeedControl(insp.savedControls.speed);
    window.dispatchEvent(new CustomEvent('study-inspection-changed'));
    if (!ready()) return false;
    video.currentTime = insp.range.start;
    syncTime();
    try {
      await video.play();
      return true;
    } catch (_) {
      stopWalkthroughMode('rejected');
      renderCoachOverlay();
      return false;
    }
  },
  async startWalkthrough({suggestion = null, frame = null, poseFrame = null, crop = [0, 0, 1, 1], range = null, rangeIndex = -1, nowTry = null, historical = false} = {}) {
    if (!context) return {started: false};
    ensureInspectionBaseline();
    clearWalkthroughTimer(context);
    const dur = fallbackDuration();
    let targetRange = range && C.finite(range.start) && C.finite(range.end)
      ? C.clampInterval(range.start, range.end, dur, 'end')
      : null;
    if (!targetRange && frame && C.finite(frame.clip_seconds)) {
      targetRange = C.clampInterval(Math.max(0, frame.clip_seconds - 0.45), Math.min(dur, frame.clip_seconds + 0.45), dur, 'end');
    }
    if (!targetRange) targetRange = C.clampInterval(context.intervalStart, context.intervalEnd, dur, 'end');

    const insp = context.inspection;
    insp.mode = 'walkthrough';
    insp.suggestionId = suggestion?.id || null;
    insp.rangeIndex = rangeIndex;
    insp.suggestion = suggestion;
    insp.frame = frame;
    insp.poseFrame = poseFrame;
    insp.crop = C.normalizeCrop(crop);
    insp.historical = !!historical;
    insp.range = targetRange;
    const fTime = frame && C.finite(frame.clip_seconds) ? frame.clip_seconds : (targetRange.start + targetRange.end) / 2;
    insp.freezeTime = C.clamp(fTime, targetRange.start, targetRange.end);
    insp.nowTry = nowTry || C.formatNowTry(suggestion || {});
    insp.walkthroughFrozen = false;
    insp.walkthroughFrozeThisPass = false;
    showWalkthroughHud(insp.nowTry);
    window.dispatchEvent(new CustomEvent('study-inspection-changed'));

    const reducedMotion = Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
    if (reducedMotion) {
      if (ready()) {
        video.pause();
        video.currentTime = insp.freezeTime;
      }
      syncTime();
      renderCoachOverlay();
      status('Reduced motion active: paused at key frame.');
      return {started: true, reducedMotion: true};
    }

    setSpeedControl(0.35);
    hideCoachOverlay();
    if (!ready()) return {started: false, waitingMetadata: true};
    video.currentTime = insp.range.start;
    syncTime();
    try {
      await video.play();
      return {started: true, reducedMotion: false};
    } catch (_) {
      stopWalkthroughMode('rejected');
      renderCoachOverlay();
      return {started: false, rejected: true};
    }
  },
  isCapturing() {
    return !!context?.capturing;
  },
  cancelScan() {
    if (context?.captureController) context.captureController.abort();
  },
  async scanPlayerMoments({maxWindows = 2, excludedIntervals = [], onProgress} = {}) {
    if (!context) throw new Error('Wait for the clip to load before scanning.');
    if (context.capturing) throw new Error('Another frame scan is already in progress.');
    if (
      window.__TENNIS_AUTOFOCUS_FORCE_UNSUPPORTED ||
      typeof Worker !== 'function' ||
      typeof WebAssembly !== 'object' ||
      !window.AutoFocusCore
    ) {
      const err = new Error('Auto detect is unavailable in this browser. Select an interval and click Ask coach.');
      err.code = 'unsupported';
      throw err;
    }
    const owner = context;
    const cfg = window.__TENNIS_AUTOFOCUS_CONFIG || {};
    const initTimeoutMs = Number(cfg.initTimeoutMs) > 0 ? Number(cfg.initTimeoutMs) : 30000;
    const detectTimeoutMs = Number(cfg.detectTimeoutMs) > 0 ? Number(cfg.detectTimeoutMs) : 8000;
    const scanTimeoutMs = Number(cfg.scanTimeoutMs) > 0 ? Number(cfg.scanTimeoutMs) : 45000;
    const controller = new AbortController();
    const signal = controller.signal;
    let scanTimedOut = false;
    const scanWatchdog = setTimeout(() => {
      scanTimedOut = true;
      controller.abort();
    }, scanTimeoutMs);
    owner.capturing = true;
    owner.captureReturnTime = video.currentTime || 0;
    owner.captureController = controller;
    const returnTime = owner.captureReturnTime;
    const returnInterval = {
      start: owner.intervalStart,
      end: owner.intervalEnd,
      defaultEnd: owner.intervalDefaultEnd,
    };
    video.pause();
    setControls();
    let worker = null;
    const canvas = document.createElement('canvas');
    let selectedWindows = null;
    try {
      if (!ready() || video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
        await new Promise((resolve, reject) => {
          if (signal.aborted || context !== owner) {
            reject(new DOMException('Cancelled', 'AbortError'));
            return;
          }
          if (ready() && video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) {
            resolve();
            return;
          }
          let timer;
          const cleanup = () => {
            clearTimeout(timer);
            video.removeEventListener('loadeddata', check);
            video.removeEventListener('canplay', check);
            video.removeEventListener('error', fail);
            signal.removeEventListener('abort', abort);
          };
          const check = () => {
            if (ready() && video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) {
              cleanup();
              resolve();
            }
          };
          const fail = () => {
            cleanup();
            reject(new Error('Wait until the clip can be played before scanning.'));
          };
          const abort = () => {
            cleanup();
            reject(new DOMException('Cancelled', 'AbortError'));
          };
          video.addEventListener('loadeddata', check);
          video.addEventListener('canplay', check);
          video.addEventListener('error', fail, {once: true});
          signal.addEventListener('abort', abort, {once: true});
          timer = setTimeout(fail, 6000);
        });
      }
      if (signal.aborted || context !== owner) throw new DOMException('Cancelled', 'AbortError');
      const dur = duration();
      const A = window.AutoFocusCore;
      const sampleTimes = A.planAutoCoachSampleTimes(dur, 16);
      if (!sampleTimes.length) throw new Error('This clip is too short for automatic detection.');
      const dims = A.sampleDimensions(video.videoWidth, video.videoHeight, 384);
      canvas.width = dims.width;
      canvas.height = dims.height;
      const painter = canvas.getContext('2d', {willReadFrequently: true});
      if (!painter) {
        const err = new Error('Auto detect is unavailable in this browser. Select an interval and click Ask coach.');
        err.code = 'unsupported';
        throw err;
      }

      video.dataset.autoCoachState = 'loading';
      video.dataset.autoCoachCaptures = '0';
      video.dataset.autoCoachDetections = '0';

      worker = new Worker('/static/autofocus-worker.js');
      await new Promise((resolve, reject) => {
        if (signal.aborted || context !== owner) {
          reject(new DOMException('Cancelled', 'AbortError'));
          return;
        }
        let timer;
        const cleanup = () => {
          clearTimeout(timer);
          signal.removeEventListener('abort', onAbort);
        };
        const onAbort = () => {
          cleanup();
          reject(new DOMException('Cancelled', 'AbortError'));
        };
        signal.addEventListener('abort', onAbort, {once: true});
        timer = setTimeout(() => {
          cleanup();
          const err = new Error('Automatic detection timed out while starting. Try again or select an interval and use Ask coach.');
          err.code = 'init_timeout';
          reject(err);
        }, initTimeoutMs);
        worker.onmessage = event => {
          const data = event?.data || {};
          if (data.type === 'ready') {
            cleanup();
            resolve();
          } else if (data.type === 'unsupported') {
            cleanup();
            const err = new Error('Auto detect is unavailable in this browser. Select an interval and click Ask coach.');
            err.code = data.code || 'worker_graphics_unavailable';
            reject(err);
          } else if (data.type === 'error') {
            cleanup();
            const err = new Error('Automatic detection failed on this clip. Try again or select an interval and use Ask coach.');
            err.code = data.code || 'detector_error';
            reject(err);
          }
        };
        worker.onerror = () => {
          cleanup();
          const err = new Error('Automatic detection failed on this clip. Try again or select an interval and use Ask coach.');
          err.code = 'worker_error';
          reject(err);
        };
        worker.postMessage({type: 'init', config: cfg});
      });

      if (signal.aborted || context !== owner) throw new DOMException('Cancelled', 'AbortError');
      video.dataset.autoCoachState = 'scanning';
      const samples = [];
      const epoch = owner.generation;

      for (let i = 0; i < sampleTimes.length; i += 1) {
        if (signal.aborted || context !== owner) throw new DOMException('Cancelled', 'AbortError');
        const at = sampleTimes[i];
        onProgress?.({index: i + 1, total: sampleTimes.length, time: at});
        await seekFrame(owner, at, signal);
        await new Promise(resolve => {
          let settled = false;
          let settleTimer = null;
          const finish = () => {
            if (!settled) {
              settled = true;
              clearTimeout(settleTimer);
              signal.removeEventListener('abort', finish);
              resolve();
            }
          };
          signal.addEventListener('abort', finish, {once: true});
          requestAnimationFrame(() => requestAnimationFrame(finish));
          settleTimer = setTimeout(finish, 36);
        });
        if (signal.aborted || context !== owner) throw new DOMException('Cancelled', 'AbortError');
        painter.drawImage(video, 0, 0, dims.width, dims.height);
        const imageData = painter.getImageData(0, 0, dims.width, dims.height);
        const seq = i + 1;
        video.dataset.autoCoachCaptures = String(seq);

        const detections = await new Promise((resolve, reject) => {
          if (signal.aborted || context !== owner) {
            reject(new DOMException('Cancelled', 'AbortError'));
            return;
          }
          let timer;
          const cleanup = () => {
            clearTimeout(timer);
            signal.removeEventListener('abort', onAbort);
          };
          const onAbort = () => {
            cleanup();
            reject(new DOMException('Cancelled', 'AbortError'));
          };
          signal.addEventListener('abort', onAbort, {once: true});
          timer = setTimeout(() => {
            cleanup();
            const err = new Error('Automatic detection timed out on a video frame. Try again or select an interval and use Ask coach.');
            err.code = 'detect_timeout';
            reject(err);
          }, detectTimeoutMs);
          worker.onmessage = event => {
            const data = event?.data || {};
            if (data.type === 'result' && data.epoch === epoch && data.seq === seq) {
              cleanup();
              resolve(Array.isArray(data.detections) ? data.detections : []);
            } else if (data.type === 'unsupported') {
              cleanup();
              const err = new Error('Auto detect is unavailable in this browser. Select an interval and click Ask coach.');
              err.code = data.code || 'worker_graphics_unavailable';
              reject(err);
            } else if (data.type === 'error') {
              cleanup();
              const err = new Error('Automatic detection failed on this clip. Try again or select an interval and use Ask coach.');
              err.code = data.code || 'detector_error';
              reject(err);
            }
          };
          worker.onerror = () => {
            cleanup();
            const err = new Error('Automatic detection failed on this clip. Try again or select an interval and use Ask coach.');
            err.code = 'worker_error';
            reject(err);
          };
          worker.postMessage(
            {
              type: 'detect',
              epoch,
              seq,
              width: dims.width,
              height: dims.height,
              buffer: imageData.data.buffer,
              config: cfg,
            },
            [imageData.data.buffer]
          );
        });

        video.dataset.autoCoachDetections = String(seq);
        samples.push({
          time: at,
          detections,
          frameWidth: dims.width,
          frameHeight: dims.height,
        });
      }

      if (signal.aborted || context !== owner) throw new DOMException('Cancelled', 'AbortError');
      const resolvedMaxWindows = typeof maxWindows === 'function' ? maxWindows() : maxWindows;
      const resolvedExcluded = typeof excludedIntervals === 'function' ? excludedIntervals() : excludedIntervals;
      selectedWindows = A.selectAutoCoachWindows(samples, dur, {
        maxWindows: resolvedMaxWindows,
        excludedIntervals: resolvedExcluded,
        frameWidth: dims.width,
        frameHeight: dims.height,
        originalStartSeconds: owner.clip.original_start_seconds,
        originalEndSeconds: owner.clip.original_end_seconds,
      });
      video.dataset.autoCoachState = selectedWindows.length ? 'detected' : 'empty';
      return {windows: selectedWindows, sampleCount: samples.length};
    } catch (error) {
      let caught = error;
      if (scanTimedOut && context === owner) {
        caught = new Error('Auto-detect scan timed out. Try again or select an interval and use Ask coach.');
        caught.code = 'scan_timeout';
      }
      if (caught?.code && ['unsupported', 'worker_graphics_unavailable'].includes(caught.code)) {
        video.dataset.autoCoachState = 'unavailable';
      } else if (
        caught?.code &&
        ['init_timeout', 'detect_timeout', 'scan_timeout', 'detector_error', 'worker_error'].includes(caught.code)
      ) {
        video.dataset.autoCoachState = 'error';
      } else if (caught?.name === 'AbortError') {
        video.dataset.autoCoachState = 'cancelled';
      }
      throw caught;
    } finally {
      clearTimeout(scanWatchdog);
      try {
        worker?.postMessage({type: 'close'});
      } catch (_) {}
      try {
        worker?.terminate();
      } catch (_) {}
      canvas.width = canvas.height = 1;
      if (context === owner) {
        owner.capturing = false;
        owner.captureController = null;
        delete owner.captureReturnTime;
        const dur = fallbackDuration();
        if (selectedWindows && selectedWindows.length > 0) {
          applyDetectedWindow(owner, selectedWindows[0]);
        } else {
          owner.intervalStart = returnInterval.start;
          owner.intervalEnd = returnInterval.end;
          owner.intervalDefaultEnd = returnInterval.defaultEnd;
          syncIntervalUI();
          if (video.readyState >= 1) {
            video.currentTime = C.clamp(returnTime, 0, dur);
          }
          owner.lastTime = returnTime;
        }
        setControls();
        syncTime();
      }
    }
  },
  setEntryStatus(badge) {
    const pill = $('study-clip-status');
    if (!pill) return;
    if (!badge || !badge.text) {
      pill.textContent = '';
      pill.hidden = true;
      pill.removeAttribute('data-stage');
      return;
    }
    pill.textContent = badge.text;
    pill.dataset.stage = badge.kind || 'idle';
    pill.hidden = false;
  },
  updateEntry(options) {
    const {button,clip}=options;
    if(!button)return;
    if(!button.studyBound){
      button.addEventListener('click',()=>{
        const current=button.studyOptions;if(!current?.clip)return;
        window.ClipStudy.open({...current,returnFocus:button});
      });
      button.studyBound=true;
    }
    button.studyOptions=options;button.classList.toggle('hidden',!clip);
    if (!clip) window.ClipStudy.setEntryStatus(null);
  },
  open(options) {
    if (context || !options.clip || !options.player) return;
    const {session,clip,player,url,returnFocus}=options;
    const initialDur = C.finite(clip.duration_seconds) && clip.duration_seconds > 0 ? clip.duration_seconds : 1;
    context={session,clip,player,url,returnFocus,parentRate:player.playbackRate,startTime:player.currentTime||0,lastTime:player.currentTime||0,intervalStart:0,intervalEnd:initialDur,intervalDefaultEnd:true,loop:false,zoom:1,x:0,y:0,frames:[],fetchController:new AbortController(),capturing:false,downloading:false,scanningAutoDetect:false,autoDetectUnavailable:!isAutoDetectSupported(),detectedWindows:null,detectedWindowIndex:-1,shareEpoch:0,shareSelectionKey:'',shareRequestId:null,inspection:null,overlayEnabled:true,coachMarkers:[],generation:++generation};
    player.pause();
    $('study-heading').textContent='Clip study · '+String(clip.index).padStart(2,'0');
    $('study-description').textContent=`${session.title} · Original ${C.clock(clip.original_start_seconds)}–${C.clock(clip.original_end_seconds)}`;
    $('study-status').textContent='';
    $('study-current').textContent=C.clock(context.startTime);$('study-duration').textContent='Loading clip…';
    $('study-media-error').hidden=true;$('study-seek').value=0;$('study-seek').max=initialDur;
    $('study-speed').value='0.5';
    $('study-loop').setAttribute('aria-pressed','false');
    video.loop=false;video.muted=player.muted;video.volume=player.volume;
    video.dataset.autoCoachState='idle';video.dataset.autoCoachCaptures='0';video.dataset.autoCoachDetections='0';
    if ($('study-auto-stepper')) $('study-auto-stepper').hidden = true;
    if ($('study-manual-frames')) $('study-manual-frames').open = false;
    if ($('study-recording-details')) $('study-recording-details').open = false;
    $('study-download').href=url+'&download=1';$('study-download').textContent='Download clip '+String(clip.index).padStart(2,'0');
    $('study-frames').replaceChildren();$('study-export').disabled=true;$('study-export').textContent='Download frame package';$('study-cancel-capture').hidden=true;
    $('study-prompt').value=C.coachingPrompt(clip);
    $('study-facts').replaceChildren(fact('Original interval',C.clock(clip.original_start_seconds)+'–'+C.clock(clip.original_end_seconds)),fact('Detector',typeof session.algorithm==='string'?session.algorithm:'Not published'));
    $('study-observations').textContent='No observations.';
    document.body.classList.add('study-open');dialog.showModal();
    dialog.scrollTop=0;document.querySelector('.study-sidebar').scrollTop=0;
    syncIntervalUI();
    video.src=url;video.load();setControls();resetView();
    const sk = shareKeyFor(session, clip);
    const activeShare = sk ? shareDownloads.get(sk) : null;
    if (activeShare && activeShare.jobId && ['queued', 'leased'].includes(activeShare.state) && !shareTimers.has(sk)) {
      activeShare.autoDownload = false;
      pollShareJob(sk, activeShare);
    }
    renderDownloadCard(context);
    loadContext(context);
    window.Coach?.open(session,clip);
  }};
  // Metadata and export adapters are kept separate from precision playback.
  async function loadContext(owner) {
    try {
      const response=await fetch(`/api/sessions/${owner.session.id}/clips/${owner.clip.id}/study?publication=${encodeURIComponent(owner.session.published_at)}`,{signal:owner.fetchController.signal});
      if(!response.ok)throw new Error(response.status===409?'This recording was reprocessed. Close study and refresh before comparing processor context.':'Processor context is unavailable. Playback and downloads still work.');
      const data=await response.json();if(context!==owner)return;
      $('study-facts').replaceChildren(fact('Original interval',C.clock(owner.clip.original_start_seconds)+'–'+C.clock(owner.clip.original_end_seconds)),fact('Detector',data.algorithm),fact('Video',data.hdr?'HDR · original export':'SDR · original export'));
      const container=$('study-observations');container.replaceChildren();
      if(!data.observations.length){const p=document.createElement('p');p.className='study-muted';p.textContent='No observations.';container.append(p);}
      for(const item of data.observations){
        const card=document.createElement('div');card.className='study-observation';const title=document.createElement('strong');title.textContent=item.label;card.append(title);
        if(item.detail){const p=document.createElement('p');p.textContent=item.detail;card.append(p);}
        if(item.score!==undefined){const p=document.createElement('p');p.textContent=(item.score_kind==='probability'?'Published probability: ':'Model score: ')+item.score;card.append(p);}
        const button=document.createElement('button');button.type='button';button.className='study-button';button.textContent='Go to '+C.clock(item.local_start_seconds);button.addEventListener('click',()=>seek(item.local_start_seconds));card.append(button);container.append(card);
      }
    } catch(error) { if(context===owner && error.name!=='AbortError')$('study-observations').textContent=error.message; }
  }

  function renderFrames() {
    if(!context)return;
    const owner=context, container=$('study-frames');container.replaceChildren();
    owner.frames.sort((a,b)=>a.time-b.time);
    for(const frame of owner.frames){
      const figure=document.createElement('figure');figure.className='study-frame';
      const image=document.createElement('img');image.src=frame.url;image.alt='Selected moment at clip '+C.clock(frame.time);
      const caption=document.createElement('figcaption');caption.textContent=C.clock(frame.time);
      const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','Remove frame at '+C.clock(frame.time));
      remove.addEventListener('click',()=>{if(context!==owner||owner.capturing)return;URL.revokeObjectURL(frame.url);owner.frames=owner.frames.filter(f=>f!==frame);renderFrames();$('study-capture').focus();});
      figure.append(image,caption,remove);container.append(figure);
    }
    $('study-export').disabled=!owner.frames.length||owner.capturing;
    $('study-export').textContent=owner.frames.length?'Download '+owner.frames.length+'-frame package':'Download frame package';
    $('study-prompt').value=C.coachingPrompt(owner.clip,owner.frames);
  }
  function seekFrame(owner,at,signal) {
    return new Promise((resolve,reject)=>{
      if(signal.aborted||context!==owner){reject(new DOMException('Cancelled','AbortError'));return;}
      let timer;
      const cleanup=()=>{clearTimeout(timer);video.removeEventListener('seeked',done);video.removeEventListener('error',failed);signal.removeEventListener('abort',abort);};
      const done=()=>{cleanup();resolve();};
      const failed=()=>{cleanup();reject(new Error('This browser cannot capture this video. Try downloading the clip instead.'));};
      const abort=()=>{cleanup();reject(new DOMException('Cancelled','AbortError'));};
      if(Math.abs(video.currentTime-at)<.003&&!video.seeking&&video.readyState>=2){resolve();return;}
      video.addEventListener('seeked',done,{once:true});video.addEventListener('error',failed,{once:true});signal.addEventListener('abort',abort,{once:true});
      timer=setTimeout(()=>{cleanup();reject(new Error('Frame seeking took too long. Download the clip or try again.'));},6000);
      try{video.currentTime=C.clamp(at,0,Math.max(0,duration()-.001));}catch(error){cleanup();reject(error);}
    });
  }
  async function captureFrame(owner,at,signal) {
    await seekFrame(owner,at,signal);
    // Let the decoded frame reach the canvas; do not use the zoomed DOM transform.
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    if(signal.aborted||context!==owner)throw new DOMException('Cancelled','AbortError');
    if(video.readyState<2||!video.videoWidth||!video.videoHeight)throw new Error('Wait until the clip can be played before capturing.');
    const canvas=document.createElement('canvas'), scale=Math.min(1,960/Math.max(video.videoWidth,video.videoHeight));
    canvas.width=Math.max(1,Math.round(video.videoWidth*scale));canvas.height=Math.max(1,Math.round(video.videoHeight*scale));
    const painter=canvas.getContext('2d');if(!painter)throw new Error('Frame export is unavailable in this browser.');
    painter.drawImage(video,0,0,canvas.width,canvas.height);
    const time=video.currentTime;
    const blob=await new Promise((resolve,reject)=>{canvas.toBlob(b=>b?resolve(b):reject(new Error('Frame export failed. Download the clip instead.')),'image/jpeg',.82);});
    canvas.width=canvas.height=1;
    if(signal.aborted||context!==owner)throw new DOMException('Cancelled','AbortError');
    return {time,blob,url:URL.createObjectURL(blob)};
  }
  async function capture(sample) {
    if(!ready()||context.capturing||video.readyState<2)return;
    const owner=context;
    if(!sample&&owner.frames.length>=6){status('Six frames selected. Remove one before adding another.');return;}
    if(!sample&&owner.frames.some(f=>Math.abs(f.time-video.currentTime)<.05)){status('This moment is already selected. Seek to another moment first.');return;}
    owner.capturing=true;owner.captureReturnTime=video.currentTime;owner.captureController=new AbortController();
    const signal=owner.captureController.signal, pending=[];
    video.pause();setControls();$('study-export').disabled=true;$('study-cancel-capture').hidden=false;
    try{
      const times=sample?C.sampleTimes(duration()):[video.currentTime];
      for(const at of times){status('Preparing frame '+(pending.length+1)+' of '+times.length+'…');pending.push(await captureFrame(owner,at,signal));}
      if(context!==owner||signal.aborted)throw new DOMException('Cancelled','AbortError');
      if(sample)clearFrames(owner);
      owner.frames.push(...pending);pending.length=0;
      status(owner.frames.length+' '+(owner.frames.length===1?'frame':'frames')+' selected.');
    }catch(error){if(context===owner)status(error.name==='AbortError'?'Sampling cancelled.':error.message);}
    finally{
      for(const frame of pending)URL.revokeObjectURL(frame.url);
      if(context===owner){
        const restore=owner.captureReturnTime;owner.capturing=false;owner.captureController=null;
        if(video.readyState>=1)video.currentTime=C.clamp(restore,0,duration());owner.lastTime=restore;delete owner.captureReturnTime;
        $('study-cancel-capture').hidden=true;setControls();renderFrames();syncTime();
      }
    }
  }
  $('study-sample').addEventListener('click',()=>capture(true));
  $('study-capture').addEventListener('click',()=>capture(false));
  $('study-cancel-capture').addEventListener('click',stopCapture);
  $('study-auto-cancel')?.addEventListener('click',stopCapture);
  $('study-full-clip')?.addEventListener('click', () => {
    if (!context || !ready() || context.capturing) return;
    if (context.inspection) exitInspection({keepCurrentBounds: false, restoreZoomPan: false, pauseVideo: false});
    const dur = fallbackDuration();
    setIntervalBounds(0, dur, 'end');
    context.intervalDefaultEnd = true;
    syncIntervalUI();
    status(`Full clip selected (0:00–${C.shortClock(dur)}). Zoom and speed preserved.`);
  });
  $('study-reset-framing')?.addEventListener('click', () => {
    if (!context || !ready() || context.capturing) return;
    if (context.inspection) exitInspection({keepCurrentBounds: false, restoreZoomPan: false, pauseVideo: false});
    resetView();
    status('Zoom and pan reset to 1×. Selected interval preserved.');
  });
  function stepDetectedWindow(delta) {
    if (!context || !ready() || context.capturing) return;
    const owner = context;
    if (!Array.isArray(owner.detectedWindows) || owner.detectedWindows.length <= 1) return;
    if (owner.inspection) exitInspection({keepCurrentBounds: false, restoreZoomPan: false, pauseVideo: false});
    owner.detectedWindowIndex = (owner.detectedWindowIndex + delta + owner.detectedWindows.length) % owner.detectedWindows.length;
    const win = owner.detectedWindows[owner.detectedWindowIndex];
    applyDetectedWindow(owner, win);
    setControls();
    status(`Moment ${owner.detectedWindowIndex + 1} of ${owner.detectedWindows.length}: ${C.clock(win.start)}–${C.clock(win.end)} (${Number(owner.zoom.toFixed(2))}× zoom).`);
  }
  $('study-auto-prev')?.addEventListener('click', () => stepDetectedWindow(-1));
  $('study-auto-next')?.addEventListener('click', () => stepDetectedWindow(1));
  $('study-auto-detect')?.addEventListener('click', async () => {
    if (!context || !ready() || context.capturing) return;
    if (context.inspection) exitInspection({keepCurrentBounds: false, restoreZoomPan: false, pauseVideo: false});
    const owner = context;
    if (!isAutoDetectSupported()) {
      owner.autoDetectUnavailable = true;
      setControls();
      status('Auto-frame motion is unavailable in this browser. Select an interval and zoom manually.');
      return;
    }
    owner.scanningAutoDetect = true;
    setControls();
    status('Scanning clip for player motion…');
    try {
      const result = await window.ClipStudy.scanPlayerMoments({
        maxWindows: 5,
        onProgress: ({index, total}) => { if (context === owner) status(`Scanning clip for player motion (${index}/${total})…`); }
      });
      if (context !== owner) return;
      const windows = result?.windows || [];
      if (!windows.length) {
        owner.detectedWindows = [];
        owner.detectedWindowIndex = -1;
        status('No clear player motion detected in this clip. Select an interval and zoom manually.');
        return;
      }
      owner.detectedWindows = windows;
      owner.detectedWindowIndex = 0;
      applyDetectedWindow(owner, windows[0]);
      status(
        windows.length > 1
          ? `Framed 1 of ${windows.length} moments (${C.clock(windows[0].start)}–${C.clock(windows[0].end)} · ${Number(owner.zoom.toFixed(2))}× zoom). Use Prev or Next to compare.`
          : `Framed moment (${C.clock(windows[0].start)}–${C.clock(windows[0].end)} · ${Number(owner.zoom.toFixed(2))}× zoom).`
      );
    } catch (error) {
      if (context !== owner) return;
      if (error?.name === 'AbortError') status('Auto-frame scan cancelled.');
      else if (['unsupported', 'worker_graphics_unavailable'].includes(error?.code)) {
        owner.autoDetectUnavailable = true;
        status('Auto-frame motion is unavailable in this browser. Select an interval and zoom manually.');
      } else {
        status(error?.message || 'Auto-frame motion failed.');
      }
    } finally {
      if (context === owner) { owner.scanningAutoDetect = false; setControls(); }
    }
  });
  $('study-copy').addEventListener('click',async()=>{
    if(!context)return;
    const owner=context,field=$('study-prompt');
    const copied=await C.copyText(field.value,{
      secure:window.isSecureContext,
      writeText:navigator.clipboard?.writeText?text=>navigator.clipboard.writeText(text):null,
      legacyCopy:()=>{if(context!==owner)return false;field.focus();field.select();return document.execCommand('copy');}
    });
    if(context!==owner)return;
    if(copied)status('Prompt copied.');
    else{field.focus();field.select();status('Select and copy the prompt.');}
  });
  $('study-export').addEventListener('click',async()=>{
    if(!context||context.capturing||!context.frames.length)return;
    const owner=context;$('study-export').disabled=true;
    try{
      const frames=[...owner.frames].sort((a,b)=>a.time-b.time),files=[];
      for(let i=0;i<frames.length;i++)files.push({name:'frame-'+String(i+1).padStart(2,'0')+'.jpg',data:new Uint8Array(await frames[i].blob.arrayBuffer())});
      const manifest={schema_version:1,source_sha256:owner.session.id,clip_id:owner.clip.id,publication:owner.session.published_at,original_interval_seconds:[owner.clip.original_start_seconds,owner.clip.original_end_seconds],time_note:'Clip timestamps are browser currentTime estimates of decoded stills; original offsets are approximate. These unzoomed stills do not represent all motion.',export:{maximum_dimension:960,format:'jpeg',quality:.82,hdr:!!owner.session.video.hdr},frames:frames.map((f,i)=>({file:files[i].name,clip_seconds:f.time,approximate_original_seconds:C.sourceTime(owner.clip,f.time)}))};
      files.push({name:'review-prompt.txt',data:C.coachingPrompt(owner.clip,frames)},{name:'context.json',data:JSON.stringify(manifest,null,2)});
      const blob=C.zipFiles(files);if(context!==owner)return;
      const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='clip-study-'+String(owner.clip.index).padStart(2,'0')+'.zip';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
      status('Frame package prepared ('+(blob.size/1024/1024).toFixed(2)+' MB). Extract the JPEGs to attach them in your chat.');
    }catch(error){if(context===owner)status(error.message);}
    finally{if(context===owner)$('study-export').disabled=!owner.frames.length;}
  });
})();
