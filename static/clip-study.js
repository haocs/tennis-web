/* Precision playback stays local; export jobs use the independent media worker. */
(function () {
  'use strict';
  const C = window.ClipStudyCore;
  const $ = id => document.getElementById(id);
  const dialog = $('clip-study'), video = $('study-video'), stage = $('study-stage');
  const PLAY_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>';
  const PAUSE_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>';
  let context = null, generation = 0;
  const pointers = new Map();
  let drag = null, pinch = null, pinDrag = null, playbackTimer = null;
  function status(message) { if (context) $('study-status').textContent = message; }
  function duration() { return C.finite(video.duration) && video.duration > 0 ? video.duration : 0; }
  function fallbackDuration() { return duration() || (context?.clip?.duration_seconds > 0 ? context.clip.duration_seconds : 1); }
  function ready() { return !!context && video.readyState >= 1 && duration() > 0; }
  function cryptoId() {
    return 'share-' + Array.from(crypto.getRandomValues(new Uint8Array(16)), v => v.toString(16).padStart(2, '0')).join('');
  }
  function invalidateShare(owner) {
    owner.shareEpoch = (owner.shareEpoch || 0) + 1;
    clearInterval(owner.shareTimer);
    owner.shareTimer = null;
    owner.shareAbort?.abort();
    owner.shareAbort = null;
    owner.shareJobId = null;
    owner.downloading = false;
  }
  function notifySelectionChanged() {
    if (!context) return;
    const selection = window.ClipStudy?.coachingSelection?.();
    const key = JSON.stringify(selection && [selection.start, selection.end, selection.speed, selection.crop]);
    if (key !== context.shareSelectionKey) {
      const hadShare = context.downloading || context.shareJobId;
      invalidateShare(context);
      context.shareSelectionKey = key;
      context.shareRequestId = null;
      const link = $('study-download-ready');
      if (link) { link.hidden = true; link.removeAttribute('href'); }
      if (hadShare) status('');
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
  function setControls() {
    const enabled = ready() && !context.capturing;
    for (const id of ['study-seek', 'study-pin-start', 'study-pin-end', 'study-back', 'study-play', 'study-forward', 'study-download-interval']) {
      if ($(id)) $(id).disabled = !enabled || (id === 'study-download-interval' && !!context?.downloading);
    }
    const frameReady = enabled && video.readyState >= 2 && !context?.capturing;
    for (const id of ['study-capture', 'study-sample']) $(id).disabled = !frameReady;
    $('study-copy').disabled = !!context?.capturing;
    for (const button of document.querySelectorAll('#study-frames button')) button.disabled = !!context?.capturing;
  }
  function enforceIntervalPlayback() {
    if (!context || !ready() || context.capturing || video.paused) return;
    const decision = C.resolveIntervalPlayback(video.currentTime, context.intervalStart, context.intervalEnd, context.loop);
    if (decision.action === 'seek') {
      video.currentTime = decision.time;
      syncTime();
    } else if (decision.action === 'loop') {
      video.currentTime = decision.time;
      syncTime();
      if (video.paused) video.play().catch(() => {});
    } else if (decision.action === 'pause') {
      video.pause();
      video.currentTime = decision.time;
      syncTime();
    }
  }
  function seek(at) {
    if (!ready() || context.capturing) return;
    video.pause();
    video.currentTime = C.clampToInterval(at, context.intervalStart, context.intervalEnd, duration());
    syncTime();
  }
  async function togglePlay() {
    if (!ready() || context.capturing) return;
    if (!video.paused) { video.pause(); return; }
    if (video.currentTime < context.intervalStart - 0.005 || video.currentTime >= context.intervalEnd - 0.02) {
      video.currentTime = context.intervalStart;
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
    if (selectionChanged) notifySelectionChanged();
  }
  function resetView() { if (context) { context.zoom = 1; context.x = 0; context.y = 0; applyView(); } }
  function stopCapture() { if (context?.captureController) context.captureController.abort(); }
  function clearFrames(owner) { for (const frame of owner.frames) URL.revokeObjectURL(frame.url); owner.frames = []; }
  function close() { if (dialog.open) dialog.close(); }
  dialog.addEventListener('close', () => {
    const old = context; if (!old) return;
    window.Coach?.close();
    clearInterval(playbackTimer); playbackTimer = null;
    invalidateShare(old);
    context = null; generation++;
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
  });
  video.addEventListener('seeked', () => { if (context) context.lastTime = video.currentTime; syncTime(); });
  video.addEventListener('play', () => {
    syncTime();
    clearInterval(playbackTimer);
    playbackTimer = setInterval(enforceIntervalPlayback, 30);
  });
  video.addEventListener('pause', () => {
    clearInterval(playbackTimer);
    playbackTimer = null;
    syncTime();
  });
  video.addEventListener('ended', () => {
    clearInterval(playbackTimer);
    playbackTimer = null;
    if (context && context.loop && ready()) {
      video.currentTime = context.intervalStart;
      video.play().catch(() => {});
      return;
    }
    if (context && ready()) video.currentTime = context.intervalEnd;
    syncTime();
  });
  function mediaError() {
    if (!context) return;
    video.pause();
    $('study-media-error').hidden = false;
    $('study-media-error').textContent = 'This browser could not play the clip. Download it to review in a compatible player. HDR/HEVC support varies by device.';
    for (const id of ['study-seek','study-pin-start','study-pin-end','study-back','study-play','study-forward','study-capture','study-sample','study-download-interval']) {
      if ($(id)) $(id).disabled = true;
    }
  }
  video.addEventListener('error', mediaError);
  $('study-play').addEventListener('click', togglePlay);
  $('study-back').addEventListener('click', () => seek(video.currentTime - .1));
  $('study-forward').addEventListener('click', () => seek(video.currentTime + .1));
  $('study-seek').addEventListener('input', event => seek(Number(event.target.value)));
  $('study-speed').addEventListener('change', event => {
    try { video.playbackRate = Number(event.target.value); }
    catch (_) { video.playbackRate = .5; event.target.value = '0.5'; status('That speed is unavailable in this browser. Using 0.5×.'); }
    notifySelectionChanged();
  });
  $('study-loop').addEventListener('click', () => {
    if (!context) return;
    context.loop = !context.loop;
    $('study-loop').setAttribute('aria-pressed', String(context.loop));
  });
  $('study-zoom').addEventListener('input', event => { if (context) { context.zoom = Number(event.target.value); applyView(); } });
  $('study-download-interval').addEventListener('click', async () => {
    if (!context || !ready() || context.downloading) return;
    notifySelectionChanged();
    const owner = context;
    const sel = window.ClipStudy.coachingSelection();
    if (!sel) return;
    const epoch = owner.shareEpoch || 0;
    const abort = new AbortController();
    owner.shareAbort = abort;
    const isCurrent = () => context === owner && (owner.shareEpoch || 0) === epoch && owner.shareAbort === abort;
    owner.downloading = true;
    clearInterval(owner.shareTimer);
    setControls();
    status('Preparing high-quality download…');
    try {
      owner.shareRequestId = owner.shareRequestId || cryptoId();
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
          request_id: owner.shareRequestId
        })
      });
      const job = await response.json();
      if (!response.ok) throw new Error(job.error || 'Could not queue interval download');
      if (!isCurrent()) return;
      owner.shareJobId = job.id;
      const jobId = job.id;
      let polling = false;
      const checkShare = async () => {
        if (!isCurrent() || polling) return;
        polling = true;
        try {
          const r = await fetch('/api/coaching/jobs/' + jobId, {signal: abort.signal});
          if (!r.ok) throw new Error('Could not check download status. Retrying…');
          const currentJob = await r.json();
          if (!isCurrent() || currentJob.id !== jobId) return;
          if (currentJob.state === 'completed') {
            clearInterval(owner.shareTimer);
            owner.shareTimer = null;
            owner.downloading = false;
            owner.shareAbort = null;
            setControls();
            const href = `/api/coaching/exports/${jobId}/excerpt.mp4?download=1`;
            const readyLink = $('study-download-ready');
            readyLink.href = href;
            readyLink.hidden = false;
            const isHdr = String(currentJob.result?.color_output || '').startsWith('hdr10-');
            status(isHdr ? 'High-quality HDR (10-bit HEVC) download ready.' : 'High-quality download ready.');
            readyLink.click();
          } else if (currentJob.state === 'failed' || currentJob.state === 'cancelled') {
            clearInterval(owner.shareTimer);
            owner.shareTimer = null;
            owner.downloading = false;
            owner.shareAbort = null;
            owner.shareRequestId = null;
            setControls();
            status(currentJob.result?.error || 'Interval download failed.');
          }
        } catch (error) {
          if (isCurrent()) status(error.message || 'Waiting for download status…');
        } finally { polling = false; }
      };
      if (job.state === 'completed') {
        await checkShare();
      } else {
        owner.shareTimer = setInterval(checkShare, 1500);
        await checkShare();
      }
    } catch (error) {
      if (isCurrent()) {
        owner.downloading = false;
        owner.shareAbort = null;
        setControls();
        status(error.message);
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
    const dur = fallbackDuration();
    const bounds = C.clampInterval(context.intervalStart, context.intervalEnd, dur);
    if (!ready()) {
      return {start: bounds.start, end: bounds.end, speed: Number($('study-speed').value) || 0.5, zoom: context.zoom, crop: [0, 0, 1, 1]};
    }
    const sw=stage.clientWidth,sh=stage.clientHeight,w=video.videoWidth,h=video.videoHeight;
    const fit=Math.min(sw/w,sh/h)*context.zoom;
    const x0=C.clamp(((-sw/2-context.x)/fit+w/2)/w,0,1),y0=C.clamp(((-sh/2-context.y)/fit+h/2)/h,0,1);
    const x1=C.clamp(((sw/2-context.x)/fit+w/2)/w,0,1),y1=C.clamp(((sh/2-context.y)/fit+h/2)/h,0,1);
    return {start: bounds.start, end: bounds.end, speed: video.playbackRate, zoom: context.zoom, crop:[x0,y0,x1-x0,y1-y0]};
  },
  setInterval(start, end, changed = 'end') {
    setIntervalBounds(start, end, changed);
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
  },
  open(options) {
    if (context || !options.clip || !options.player) return;
    const {session,clip,player,url,returnFocus}=options;
    const initialDur = C.finite(clip.duration_seconds) && clip.duration_seconds > 0 ? clip.duration_seconds : 1;
    context={session,clip,player,url,returnFocus,parentRate:player.playbackRate,startTime:player.currentTime||0,lastTime:player.currentTime||0,intervalStart:0,intervalEnd:initialDur,intervalDefaultEnd:true,loop:false,zoom:1,x:0,y:0,frames:[],fetchController:new AbortController(),capturing:false,downloading:false,shareJobId:null,shareRequestId:null,shareTimer:null,generation:++generation};
    player.pause();
    $('study-heading').textContent='Clip study · '+String(clip.index).padStart(2,'0');
    $('study-description').textContent=`${session.title} · Original ${C.clock(clip.original_start_seconds)}–${C.clock(clip.original_end_seconds)}`;
    $('study-status').textContent='';
    $('study-current').textContent=C.clock(context.startTime);$('study-duration').textContent='Loading clip…';
    $('study-media-error').hidden=true;$('study-seek').value=0;$('study-seek').max=initialDur;
    $('study-speed').value='0.5';
    $('study-loop').setAttribute('aria-pressed','false');
    video.loop=false;video.muted=player.muted;video.volume=player.volume;
    $('study-download-ready').hidden=true;$('study-download-ready').removeAttribute('href');
    $('study-download').href=url+'&download=1';$('study-download').textContent='Download clip '+String(clip.index).padStart(2,'0');
    $('study-frames').replaceChildren();$('study-export').disabled=true;$('study-export').textContent='Download frame package';$('study-cancel-capture').hidden=true;
    $('study-prompt').value=C.coachingPrompt(clip);
    $('study-facts').replaceChildren(fact('Original interval',C.clock(clip.original_start_seconds)+'–'+C.clock(clip.original_end_seconds)),fact('Detector',typeof session.algorithm==='string'?session.algorithm:'Not published'));
    $('study-observations').textContent='No observations.';
    document.body.classList.add('study-open');dialog.showModal();
    dialog.scrollTop=0;document.querySelector('.study-sidebar').scrollTop=0;
    syncIntervalUI();
    video.src=url;video.load();setControls();resetView();
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
