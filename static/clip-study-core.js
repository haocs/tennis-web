/* Pure Clip study helpers, shared with Node's built-in tests. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ClipStudyCore = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  function clock(seconds) {
    const input = Number(seconds);
    const ms = Math.round(Math.max(0, Number.isFinite(input) ? input : 0) * 1000);
    const whole = Math.floor(ms / 1000), s = whole % 60, m = Math.floor(whole / 60) % 60, h = Math.floor(whole / 3600);
    return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(s).padStart(2, '0') + '.' + String(ms % 1000).padStart(3, '0');
  }
  function sampleTimes(duration, count = 6) {
    if (!finite(duration) || duration <= 0 || !Number.isInteger(count) || count < 1 || count > 6) return [];
    // Interior samples avoid the undecodable end timestamp and title/black edge frames.
    return Array.from({length: count}, (_, i) => duration * (i + .5) / count);
  }
  function sourceTime(clip, local) {
    return clamp(clip.original_start_seconds + Math.max(0, local), clip.original_start_seconds, clip.original_end_seconds);
  }
  function panBounds(stageWidth, stageHeight, videoWidth, videoHeight, zoom) {
    if (![stageWidth, stageHeight, videoWidth, videoHeight, zoom].every(finite) || Math.min(stageWidth, stageHeight, videoWidth, videoHeight) <= 0) return {x: 0, y: 0};
    const fit = Math.min(stageWidth / videoWidth, stageHeight / videoHeight);
    return {x: Math.max(0, (videoWidth * fit * zoom - stageWidth) / 2), y: Math.max(0, (videoHeight * fit * zoom - stageHeight) / 2)};
  }
  function clampInterval(start, end, duration, changed = 'end', minSpan = 0.1) {
    if (!finite(duration) || duration <= 0) return {start: 0, end: 0};
    const span = Math.min(Math.max(0.01, finite(minSpan) ? minSpan : 0.1), duration);
    let s = clamp(finite(start) ? start : 0, 0, duration);
    let e = clamp(finite(end) ? end : duration, 0, duration);
    if (changed === 'start') {
      s = clamp(s, 0, Math.max(0, e - span));
      e = clamp(e, Math.min(duration, s + span), duration);
    } else {
      e = clamp(e, Math.min(duration, s + span), duration);
      s = clamp(s, 0, Math.max(0, e - span));
    }
    return {
      start: Math.round(s * 1000) / 1000,
      end: Math.round(e * 1000) / 1000
    };
  }
  function clampToInterval(time, intervalStart, intervalEnd, duration) {
    const bounds = clampInterval(intervalStart, intervalEnd, duration);
    return clamp(finite(time) ? time : bounds.start, bounds.start, bounds.end);
  }
  function resolveIntervalPlayback(currentTime, intervalStart, intervalEnd, loop = false) {
    const start = finite(intervalStart) ? Math.max(0, intervalStart) : 0;
    const end = finite(intervalEnd) ? Math.max(start, intervalEnd) : start;
    const now = finite(currentTime) ? currentTime : start;
    if (now < start - 0.005) return {action: 'seek', time: start};
    if (now >= end - 0.01) return loop ? {action: 'loop', time: start} : {action: 'pause', time: end};
    return {action: 'continue', time: now};
  }
  function coachingPrompt(clip, frames = []) {
    const points = frames.length ? '\nSelected stills, in chronological order:\n' + [...frames].sort((a,b) => a.time-b.time).map((f,i) => `${i+1}. Clip ${clock(f.time)}; approximate original ${clock(sourceTime(clip, f.time))}`).join('\n') : '';
    const p=clip.main_player;const profile=p?'\nMain-player profile (anonymous, user supplied): '+JSON.stringify({height_cm:p.height_cm,level:p.level})+'. If the intended player is ambiguous in the images, state uncertainty.':'';
    return `Please review this tennis practice clip and help me improve.${p?'':' I am a beginner.'}${profile}\n\nClip ${String(clip.index).padStart(2,'0')}; original recording interval ${clock(clip.original_start_seconds)}–${clock(clip.original_end_seconds)}. Clip-local time starts at zero. Original offsets are approximate because export timing can round.${points}\n\nReview the entire attached clip or every supplied snapshot in order. Assess all clearly visible aspects of technique across the sequence, group recurring issues and prioritise up to six practical improvements with a cue and drill for each supported issue. Refer to clip-local timestamps or still numbers. Separate observations from guesses; do not infer ball contact, full motion or stroke timing from sparse stills alone. Ask for the full clip if needed. Avoid judging an action you cannot see clearly. The stills are unzoomed browser exports; color may differ for HDR.\n\n${frames.length ? 'I will attach the selected still images. They are context samples, not a full motion sequence.' : 'I will attach the original-quality clip.'}`;
  }
  async function copyText(text, {secure, writeText, legacyCopy}) {
    if (secure && typeof writeText === 'function') {
      try { await writeText(text); return true; } catch (_) { /* Try the LAN/manual path. */ }
    }
    try { return !!legacyCopy(text); } catch (_) { return false; }
  }
  // A small ZIP STORE writer: no remote library, compression service or upload.
  const table = Array.from({length: 256}, (_, n) => {let c=n; for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;return c>>>0;});
  function crc32(bytes) {let c=0xffffffff;for(const b of bytes)c=table[(c^b)&255]^(c>>>8);return (c^0xffffffff)>>>0;}
  function zipFiles(files) {
    if (!Array.isArray(files) || files.length > 10) throw new Error('Too many export files');
    const encoder=new TextEncoder(), chunks=[], directory=[];let offset=0, size=0;
    for(const file of files) {
      if (!/^[a-z0-9][a-z0-9._-]{0,100}$/i.test(file.name)) throw new Error('Invalid export filename');
      const bytes=typeof file.data==='string'?encoder.encode(file.data):file.data;
      if(!(bytes instanceof Uint8Array))throw new Error('Invalid export data');
      size+=bytes.length;if(size>20*1024*1024)throw new Error('Frame package exceeds 20 MB');
      const name=encoder.encode(file.name), crc=crc32(bytes), local=new Uint8Array(30), l=new DataView(local.buffer);
      l.setUint32(0,0x04034b50,true);l.setUint16(4,20,true);l.setUint16(10,0,true);l.setUint16(12,33,true);l.setUint32(14,crc,true);l.setUint32(18,bytes.length,true);l.setUint32(22,bytes.length,true);l.setUint16(26,name.length,true);
      const central=new Uint8Array(46), c=new DataView(central.buffer);
      c.setUint32(0,0x02014b50,true);c.setUint16(4,20,true);c.setUint16(6,20,true);c.setUint16(12,0,true);c.setUint16(14,33,true);c.setUint32(16,crc,true);c.setUint32(20,bytes.length,true);c.setUint32(24,bytes.length,true);c.setUint16(28,name.length,true);c.setUint32(42,offset,true);
      chunks.push(local,name,bytes);directory.push(central,name);offset+=local.length+name.length+bytes.length;
    }
    const directorySize=directory.reduce((sum,b)=>sum+b.length,0), end=new Uint8Array(22), e=new DataView(end.buffer);
    e.setUint32(0,0x06054b50,true);e.setUint16(8,files.length,true);e.setUint16(10,files.length,true);e.setUint32(12,directorySize,true);e.setUint32(16,offset,true);
    return new Blob([...chunks,...directory,end],{type:'application/zip'});
  }
  function normalizeSubjectHint(value) {
    if (typeof value !== 'string') return '';
    return value.replace(/\s+/g, ' ').trim().slice(0, 160);
  }
  function coachingSubjectOptions({clip, session, players, authoritativePlayers = false} = {}) {
    const byId = new Map();
    const upsert = p => {
      if (!p || typeof p.id !== 'string' || !p.id || typeof p.name !== 'string') return;
      const prev = byId.get(p.id);
      if (!prev || (Number(p.version) || 0) >= (Number(prev.version) || 0)) byId.set(p.id, p);
    };
    if (Array.isArray(players) && (authoritativePlayers || players.length > 0)) {
      for (const p of players) upsert(p);
      if (!authoritativePlayers) {
        for (const p of (session?.recording_players || [])) upsert(p);
        for (const p of (session?.players || [])) upsert(p);
        if (clip?.main_player) upsert(clip.main_player);
      }
    } else if (!authoritativePlayers) {
      for (const p of (session?.recording_players || [])) upsert(p);
      for (const p of (session?.players || [])) upsert(p);
      if (clip?.main_player) upsert(clip.main_player);
    }
    const mainId = clip?.main_player?.id && byId.has(clip.main_player.id) ? clip.main_player.id : '';
    const recordingIds = new Set([...(session?.recording_players || []), ...(session?.players || [])].map(p => p?.id).filter(Boolean));
    const sortByName = (a, b) => a.name.localeCompare(b.name, undefined, {sensitivity: 'base'}) || String(a.id).localeCompare(String(b.id));
    const options = [];
    if (mainId) {
      const mp = byId.get(mainId);
      options.push({value: mp.id, label: `${mp.name} (clip main player)`, role: 'main_player', player: mp});
    }
    const inRecording = [...byId.values()].filter(p => p.id !== mainId && recordingIds.has(p.id)).sort(sortByName);
    for (const p of inRecording) {
      options.push({value: p.id, label: `${p.name} (in recording)`, role: 'recording_player', player: p});
    }
    const others = [...byId.values()].filter(p => p.id !== mainId && !recordingIds.has(p.id)).sort(sortByName);
    for (const p of others) {
      options.push({value: p.id, label: p.name, role: 'saved_player', player: p});
    }
    options.push({value: '', label: mainId ? 'No player profile' : 'No player profile (unassigned)', role: 'none', player: null});
    return {options, defaultValue: mainId, byId: Object.fromEntries(byId)};
  }
  function resolveCoachingSubject({selectedPlayerId, clip, session, players, authoritativePlayers = false, subjectHint = ''} = {}) {
    const {byId, defaultValue} = coachingSubjectOptions({clip, session, players, authoritativePlayers});
    const hint = normalizeSubjectHint(subjectHint);
    const pid = typeof selectedPlayerId === 'string' ? selectedPlayerId.trim() : '';
    if (!pid) {
      return {subjectMode: 'none', playerId: null, playerProfileVersion: null, player: null, subjectHint: hint || null};
    }
    const player = byId[pid] || null;
    if (!player) throw new Error('Selected player is unavailable. Choose a saved player.');
    const isMain = Boolean(defaultValue && pid === defaultValue);
    return {
      subjectMode: isMain ? 'main_player' : 'selected_player',
      playerId: player.id,
      playerProfileVersion: player.version,
      player,
      subjectHint: hint || null
    };
  }
  function describeCoachingSubject({selectedPlayerId, clip, session, players, authoritativePlayers = false} = {}) {
    const {byId, defaultValue} = coachingSubjectOptions({clip, session, players, authoritativePlayers});
    const pid = typeof selectedPlayerId === 'string' ? selectedPlayerId.trim() : '';
    const player = pid ? byId[pid] : null;
    if (player && defaultValue && pid === defaultValue) {
      return `For ${player.name} (clip main player) · height and level are included; name stays in this library.`;
    }
    if (player) {
      return `For ${player.name} (this request only; clip player unchanged) · height and level included; name stays local. Auto-frame motion scans foreground motion, not player identity.`;
    }
    if (defaultValue && byId[defaultValue]) {
      return `No player profile for this request (clip player stays ${byId[defaultValue].name}) · advice stays on this clip only.`;
    }
    return 'No main player assigned · choose a player above or use Saved players… to add one without changing clip assignment.';
  }
  const STROKE_PHASE_LABELS = {
    split_step: 'Split step',
    unit_turn: 'Unit turn',
    backswing: 'Backswing',
    forward_swing: 'Forward swing',
    contact_window: 'Contact window',
    follow_through: 'Follow-through',
    recovery: 'Recovery',
    serve_toss: 'Serve toss',
    trophy_position: 'Trophy position',
    other: 'Other phase',
  };
  const BODY_AREA_LABELS = {
    hitting_arm: 'Hitting arm',
    off_arm: 'Non-hitting arm',
    shoulders_chest: 'Shoulders / chest',
    hips_trunk: 'Hips / trunk',
    knees_legs: 'Knees / legs',
    feet_stance: 'Feet / stance',
    head_eyes: 'Head / eyes',
  };
  const BODY_AREA_SEGMENTS = {
    hitting_arm: [['left_shoulder', 'left_elbow'], ['left_elbow', 'left_wrist'], ['right_shoulder', 'right_elbow'], ['right_elbow', 'right_wrist']],
    off_arm: [['left_shoulder', 'left_elbow'], ['left_elbow', 'left_wrist'], ['right_shoulder', 'right_elbow'], ['right_elbow', 'right_wrist']],
    shoulders_chest: [['left_shoulder', 'right_shoulder'], ['left_shoulder', 'left_hip'], ['right_shoulder', 'right_hip']],
    hips_trunk: [['left_shoulder', 'right_shoulder'], ['left_hip', 'right_hip'], ['left_shoulder', 'left_hip'], ['right_shoulder', 'right_hip']],
    knees_legs: [['left_hip', 'left_knee'], ['left_knee', 'left_ankle'], ['right_hip', 'right_knee'], ['right_knee', 'right_ankle']],
    feet_stance: [['left_knee', 'left_ankle'], ['right_knee', 'right_ankle']],
    head_eyes: [['left_shoulder', 'nose'], ['right_shoulder', 'nose']],
  };
  const DEFAULT_BODY_SEGMENTS = [
    ['left_shoulder', 'right_shoulder'],
    ['left_hip', 'right_hip'],
    ['left_shoulder', 'left_hip'],
    ['right_shoulder', 'right_hip'],
    ['left_shoulder', 'left_elbow'], ['left_elbow', 'left_wrist'],
    ['right_shoulder', 'right_elbow'], ['right_elbow', 'right_wrist'],
    ['left_hip', 'left_knee'], ['left_knee', 'left_ankle'],
    ['right_hip', 'right_knee'], ['right_knee', 'right_ankle'],
  ];

  function normalizeCrop(crop) {
    if (!Array.isArray(crop) || crop.length !== 4 || !crop.every(finite)) return [0, 0, 1, 1];
    const cx = clamp(crop[0], 0, 1);
    const cy = clamp(crop[1], 0, 1);
    const cw = clamp(crop[2], 0, 1 - cx);
    const ch = clamp(crop[3], 0, 1 - cy);
    if (cw <= 0 || ch <= 0) return [0, 0, 1, 1];
    return [cx, cy, cw, ch];
  }

  function evidenceToClipPoint(ex, ey, crop = [0, 0, 1, 1]) {
    if (!finite(ex) || !finite(ey)) return null;
    const [cx, cy, cw, ch] = normalizeCrop(crop);
    return {
      x: clamp(cx + clamp(ex, 0, 1) * cw, 0, 1),
      y: clamp(cy + clamp(ey, 0, 1) * ch, 0, 1),
    };
  }

  function evidenceToClipBox(box, crop = [0, 0, 1, 1]) {
    let bx, by, bw, bh;
    if (Array.isArray(box) && box.length === 4) {
      [bx, by, bw, bh] = box;
    } else if (box && typeof box === 'object') {
      bx = box.x; by = box.y; bw = box.width; bh = box.height;
    } else {
      return null;
    }
    if (![bx, by, bw, bh].every(finite) || bw <= 0 || bh <= 0) return null;
    if (bx < 0 || by < 0 || bx > 1 || by > 1 || bx + bw > 1.001 || by + bh > 1.001) return null;
    const [cx, cy, cw, ch] = normalizeCrop(crop);
    const nx = cx + bx * cw;
    const ny = cy + by * ch;
    return {
      x: clamp(nx, 0, 1),
      y: clamp(ny, 0, 1),
      width: clamp(bw * cw, 0, Math.max(0, 1 - nx)),
      height: clamp(bh * ch, 0, Math.max(0, 1 - ny)),
    };
  }

  function letterboxRect(containerWidth, containerHeight, mediaWidth, mediaHeight) {
    if (![containerWidth, containerHeight, mediaWidth, mediaHeight].every(v => finite(v) && v > 0)) {
      return {left: 0, top: 0, width: 0, height: 0, scale: 0};
    }
    const scale = Math.min(containerWidth / mediaWidth, containerHeight / mediaHeight);
    const width = mediaWidth * scale;
    const height = mediaHeight * scale;
    return {
      left: (containerWidth - width) / 2,
      top: (containerHeight - height) / 2,
      width,
      height,
      scale,
    };
  }

  function projectPointToStage(clipX, clipY, stageWidth, stageHeight, videoWidth, videoHeight, zoom = 1, panX = 0, panY = 0) {
    if (!finite(clipX) || !finite(clipY)) return null;
    const box = letterboxRect(stageWidth, stageHeight, videoWidth, videoHeight);
    if (box.width <= 0 || box.height <= 0) return null;
    const z = finite(zoom) && zoom > 0 ? zoom : 1;
    const tx = finite(panX) ? panX : 0;
    const ty = finite(panY) ? panY : 0;
    return {
      x: stageWidth / 2 + (clipX - 0.5) * box.width * z + tx,
      y: stageHeight / 2 + (clipY - 0.5) * box.height * z + ty,
    };
  }

  function projectBoxToStage(clipBox, stageWidth, stageHeight, videoWidth, videoHeight, zoom = 1, panX = 0, panY = 0) {
    if (!clipBox || ![clipBox.x, clipBox.y, clipBox.width, clipBox.height].every(finite)) return null;
    const tl = projectPointToStage(clipBox.x, clipBox.y, stageWidth, stageHeight, videoWidth, videoHeight, zoom, panX, panY);
    const br = projectPointToStage(clipBox.x + clipBox.width, clipBox.y + clipBox.height, stageWidth, stageHeight, videoWidth, videoHeight, zoom, panX, panY);
    if (!tl || !br) return null;
    return {
      left: tl.x,
      top: tl.y,
      width: Math.max(0, br.x - tl.x),
      height: Math.max(0, br.y - tl.y),
    };
  }

  function selectSuggestionRanges(appliesTo, clipOriginalStart, clipOriginalEnd, clipDuration) {
    if (!Array.isArray(appliesTo) || !finite(clipOriginalStart)) return [];
    const origEnd = finite(clipOriginalEnd) ? clipOriginalEnd : clipOriginalStart + (finite(clipDuration) ? clipDuration : 0);
    const dur = finite(clipDuration) && clipDuration > 0 ? clipDuration : Math.max(0.1, origEnd - clipOriginalStart);
    const out = [];
    for (let i = 0; i < appliesTo.length; i++) {
      const item = appliesTo[i];
      if (!item || !finite(item.start_seconds) || !finite(item.end_seconds) || item.end_seconds < item.start_seconds) continue;
      if (item.start_seconds > origEnd + 1e-6 || item.end_seconds < clipOriginalStart - 1e-6) continue;
      const rawStart = clamp(item.start_seconds - clipOriginalStart, 0, dur);
      const rawEnd = clamp(item.end_seconds - clipOriginalStart, 0, dur);
      const bounds = clampInterval(rawStart, Math.max(rawStart, rawEnd), dur, 'end');
      out.push({
        index: out.length,
        sourceIndex: i,
        originalStart: item.start_seconds,
        originalEnd: item.end_seconds,
        start: bounds.start,
        end: bounds.end,
      });
    }
    return out;
  }

  function pickSuggestionRangeIndex(ranges, targetOriginalSeconds) {
    if (!Array.isArray(ranges) || !ranges.length) return -1;
    if (!finite(targetOriginalSeconds)) return 0;
    const containing = ranges.findIndex(r => targetOriginalSeconds >= r.originalStart - 1e-6 && targetOriginalSeconds <= r.originalEnd + 1e-6);
    if (containing >= 0) return containing;
    let bestIdx = 0;
    let bestDist = Infinity;
    for (let i = 0; i < ranges.length; i++) {
      const r = ranges[i];
      const dist = Math.min(Math.abs(targetOriginalSeconds - r.originalStart), Math.abs(targetOriginalSeconds - r.originalEnd));
      if (dist < bestDist) {
        bestDist = dist;
        bestIdx = i;
      }
    }
    return bestIdx;
  }

  function resolveSparseOverlay({
    suggestion = null,
    frame = null,
    poseFrame = null,
    crop = [0, 0, 1, 1],
    paused = false,
    currentTime = 0,
    historical = false,
    overlayEnabled = true,
    frameTolerance = 0.055,
    minConfidence = 0.65,
  } = {}) {
    if (!overlayEnabled) return {visible: false, reason: 'disabled', focusBox: null, joints: {}, segments: [], label: 'Approximate body guide'};
    if (historical) return {visible: false, reason: 'historical', focusBox: null, joints: {}, segments: [], label: 'Approximate body guide'};
    if (!paused) return {visible: false, reason: 'playing', focusBox: null, joints: {}, segments: [], label: 'Approximate body guide'};
    if (!frame || !finite(frame.clip_seconds) || !finite(currentTime)) {
      return {visible: false, reason: 'no_frame', focusBox: null, joints: {}, segments: [], label: 'Approximate body guide'};
    }
    if (Math.abs(currentTime - frame.clip_seconds) > frameTolerance) {
      return {visible: false, reason: 'off_frame', focusBox: null, joints: {}, segments: [], label: 'Approximate body guide'};
    }

    const focusBox = suggestion?.focus_region ? evidenceToClipBox(suggestion.focus_region, crop) : null;
    let suppressedReason = '';
    const acceptedRaw = {};
    if (!poseFrame) {
      suppressedReason = 'pose_unavailable';
    } else if (poseFrame.subject_ambiguous) {
      suppressedReason = 'subject_ambiguous';
    } else if (!(poseFrame.person_count >= 1)) {
      suppressedReason = 'no_person';
    } else if (poseFrame.joints && typeof poseFrame.joints === 'object') {
      for (const [name, pt] of Object.entries(poseFrame.joints)) {
        if (
          pt &&
          finite(pt.x) && finite(pt.y) && finite(pt.confidence) &&
          pt.confidence >= minConfidence &&
          pt.x >= 0.02 && pt.x <= 0.98 &&
          pt.y >= 0.02 && pt.y <= 0.98
        ) {
          const projected = evidenceToClipPoint(pt.x, pt.y, crop);
          if (projected) acceptedRaw[name] = {x: projected.x, y: projected.y, confidence: pt.confidence};
        }
      }
    }

    const areas = Array.isArray(suggestion?.body_areas)
      ? suggestion.body_areas.filter(a => Object.prototype.hasOwnProperty.call(BODY_AREA_SEGMENTS, a))
      : [];
    const candidatePairs = areas.length
      ? areas.flatMap(a => BODY_AREA_SEGMENTS[a])
      : DEFAULT_BODY_SEGMENTS;
    const seenPairs = new Set();
    const segments = [];
    const activeNames = new Set();
    for (const [a, b] of candidatePairs) {
      const key = a < b ? `${a}|${b}` : `${b}|${a}`;
      if (seenPairs.has(key)) continue;
      seenPairs.add(key);
      if (acceptedRaw[a] && acceptedRaw[b]) {
        segments.push([a, b]);
        activeNames.add(a);
        activeNames.add(b);
      }
    }
    if (areas.includes('head_eyes') && acceptedRaw.nose) activeNames.add('nose');

    const joints = {};
    for (const name of activeNames) {
      if (acceptedRaw[name]) joints[name] = acceptedRaw[name];
    }
    if (!suppressedReason && !Object.keys(joints).length) suppressedReason = 'low_confidence';
    const visible = Boolean(focusBox || Object.keys(joints).length);
    return {
      visible,
      reason: visible ? 'ok' : (suppressedReason || 'uncertain'),
      suppressedReason: Object.keys(joints).length ? '' : suppressedReason,
      ambiguityReason: typeof poseFrame?.ambiguity_reason === 'string' ? poseFrame.ambiguity_reason : '',
      label: 'Approximate body guide',
      focusBox,
      joints,
      segments,
    };
  }

  function formatNowTry(suggestion = {}) {
    const rawNow = typeof suggestion?.now_summary === 'string' ? suggestion.now_summary.trim() : '';
    let nowText = rawNow;
    if (!nowText && typeof suggestion?.observation === 'string') {
      const obs = suggestion.observation.trim();
      if (obs) {
        const sentence = obs.split(/(?<=[.!?])\s+/)[0] || obs;
        nowText = sentence.length > 160 ? sentence.slice(0, 157).trimEnd() + '…' : sentence;
      }
    }
    const tryText = typeof suggestion?.cue === 'string' ? suggestion.cue.trim() : '';
    const phaseLabel = STROKE_PHASE_LABELS[suggestion?.stroke_phase] || '';
    const bodyAreaLabels = Array.isArray(suggestion?.body_areas)
      ? suggestion.body_areas.map(k => BODY_AREA_LABELS[k]).filter(Boolean)
      : [];
    return {nowText, tryText, phaseLabel, bodyAreaLabels};
  }

  function shortClock(seconds) {
    const input = Number(seconds);
    const ms = Math.round(Math.max(0, Number.isFinite(input) ? input : 0) * 1000);
    const whole = Math.floor(ms / 1000);
    const s = whole % 60;
    const m = Math.floor(whole / 60) % 60;
    const h = Math.floor(whole / 3600);
    const base = (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(s).padStart(2, '0');
    const frac = ms % 1000;
    return frac ? base + '.' + String(frac).padStart(3, '0').replace(/0+$/, '') : base;
  }

  function formatExcerptSummary(selection = {}, clipDuration = 0) {
    const dur = finite(clipDuration) && clipDuration > 0 ? clipDuration : null;
    const rawStart = finite(selection?.start) ? Math.max(0, selection.start) : 0;
    const rawEnd = finite(selection?.end) ? Math.max(rawStart, selection.end) : (dur ?? rawStart);
    const start = dur !== null ? clamp(rawStart, 0, dur) : rawStart;
    const end = dur !== null ? clamp(rawEnd, start, dur) : rawEnd;
    const speed = finite(selection?.speed) && selection.speed > 0 ? Number(selection.speed.toFixed(2)) : 0.5;
    const zoom = finite(selection?.zoom) && selection.zoom >= 1 ? Number(selection.zoom.toFixed(2)) : 1;
    const isFull = dur !== null && Math.abs(start) < 0.02 && Math.abs(end - dur) < 0.02;
    const rangeLabel = isFull
      ? `Full clip (${shortClock(start)}–${shortClock(end)})`
      : `${shortClock(start)}–${shortClock(end)}`;
    return `${rangeLabel} · ${speed}× · ${zoom}× zoom`;
  }

  function deriveActiveRequestState({pendingOp = null, renderJob = null, aiJob = null} = {}) {
    if (aiJob) {
      if (aiJob.state === 'calling') {
        return {
          stage: 'analyzing',
          headline: 'Analyzing',
          prepState: 'done',
          aiState: 'active',
          canCancel: false,
          nonCancellableNote: 'Analysis has started and can’t be cancelled.',
          badgeText: 'Analyzing…',
        };
      }
      if (aiJob.state === 'leased') {
        return {
          stage: 'analyzing',
          headline: 'Analyzing',
          prepState: 'done',
          aiState: 'active',
          canCancel: true,
          nonCancellableNote: '',
          badgeText: 'Analyzing…',
        };
      }
      if (aiJob.state === 'queued') {
        return {
          stage: 'waiting_ai',
          headline: 'Waiting for AI',
          prepState: 'done',
          aiState: 'waiting',
          canCancel: true,
          nonCancellableNote: '',
          badgeText: 'Waiting for AI…',
        };
      }
      if (aiJob.state === 'needs_review') {
        return {
          stage: 'needs_attention',
          headline: 'Needs attention',
          prepState: 'done',
          aiState: 'attention',
          canCancel: false,
          nonCancellableNote: '',
          badgeText: 'Needs attention',
        };
      }
      if (aiJob.state === 'failed') {
        return {
          stage: 'failed',
          headline: 'Failed',
          prepState: 'done',
          aiState: 'failed',
          canCancel: false,
          nonCancellableNote: '',
          badgeText: 'Needs attention',
        };
      }
      if (aiJob.state === 'completed' || aiJob.state === 'cancelled') return null;
    }

    if (pendingOp && !pendingOp.cancelled) {
      const reused = Boolean(pendingOp.prepReused || (renderJob && renderJob.state === 'completed'));
      if (pendingOp.phase === 'failed') {
        return {
          stage: 'failed',
          headline: 'Failed',
          prepState: reused ? 'done' : 'failed',
          aiState: reused ? 'failed' : 'pending',
          canCancel: false,
          nonCancellableNote: '',
          badgeText: 'Needs attention',
        };
      }
      if (renderJob) {
        if (renderJob.state === 'completed') {
          return {
            stage: 'waiting_ai',
            headline: 'Waiting for AI',
            prepState: 'done',
            aiState: 'waiting',
            canCancel: true,
            nonCancellableNote: '',
            badgeText: 'Waiting for AI…',
          };
        }
        if (renderJob.state === 'leased') {
          return {
            stage: 'preparing',
            headline: 'Preparing clip',
            prepState: 'active',
            aiState: 'pending',
            canCancel: true,
            nonCancellableNote: '',
            badgeText: 'Preparing…',
          };
        }
        if (renderJob.state === 'queued') {
          return {
            stage: 'waiting_prepare',
            headline: 'Waiting to prepare',
            prepState: 'waiting',
            aiState: 'pending',
            canCancel: true,
            nonCancellableNote: '',
            badgeText: 'Preparing…',
          };
        }
        if (renderJob.state === 'failed') {
          return {
            stage: 'failed',
            headline: 'Failed',
            prepState: 'failed',
            aiState: 'pending',
            canCancel: false,
            nonCancellableNote: '',
            badgeText: 'Needs attention',
          };
        }
        if (renderJob.state === 'cancelled') return null;
      }
      if (reused) {
        return {
          stage: 'waiting_ai',
          headline: 'Waiting for AI',
          prepState: 'done',
          aiState: 'waiting',
          canCancel: true,
          nonCancellableNote: '',
          badgeText: 'Waiting for AI…',
        };
      }
      return {
        stage: 'waiting_prepare',
        headline: 'Waiting to prepare',
        prepState: 'waiting',
        aiState: 'pending',
        canCancel: true,
        nonCancellableNote: '',
        badgeText: 'Preparing…',
      };
    }

    if (renderJob) {
      if (renderJob.state === 'leased') {
        return {
          stage: 'preparing',
          headline: 'Preparing clip',
          prepState: 'active',
          aiState: 'pending',
          canCancel: true,
          nonCancellableNote: '',
          badgeText: 'Preparing…',
        };
      }
      if (renderJob.state === 'queued') {
        return {
          stage: 'waiting_prepare',
          headline: 'Waiting to prepare',
          prepState: 'waiting',
          aiState: 'pending',
          canCancel: true,
          nonCancellableNote: '',
          badgeText: 'Preparing…',
        };
      }
      if (renderJob.state === 'failed') {
        return {
          stage: 'failed',
          headline: 'Failed',
          prepState: 'failed',
          aiState: 'pending',
          canCancel: false,
          nonCancellableNote: '',
          badgeText: 'Needs attention',
        };
      }
    }

    return null;
  }

  function matchPendingAiJob(pendingOp, jobs = []) {
    if (!pendingOp || !Array.isArray(jobs) || !jobs.length) return null;
    if (pendingOp.aiJobId) {
      return jobs.find(j => j && j.id === pendingOp.aiJobId) || null;
    }
    if (pendingOp.phase === 'failed') return null;
    if (pendingOp.askRequestId) {
      const byReq = jobs.find(j => j && j.role === 'ai' && (j.request_id === pendingOp.askRequestId || j.body?.request_id === pendingOp.askRequestId));
      if (byReq) return byReq;
    }
    if (!pendingOp.exportId) return null;
    const known = new Set(Array.isArray(pendingOp.knownAiIds) ? pendingOp.knownAiIds : []);
    return jobs.find(j =>
      j &&
      j.role === 'ai' &&
      j.body?.export_id === pendingOp.exportId &&
      !known.has(j.id) &&
      ['queued', 'leased', 'calling'].includes(j.state)
    ) || null;
  }

  function deriveClipCoachBadge({jobs = [], annotations = [], pendingOp = null, clip = null, publication = null} = {}) {
    if (!clip || !clip.id) return {kind: 'idle', count: 0, text: '', stage: 'idle'};
    const listJobs = Array.isArray(jobs) ? jobs : [];
    const listAnns = Array.isArray(annotations) ? annotations : [];
    const jobMatchesClip = j => {
      const b = j?.body;
      if (!b || b.intent === 'share' || b.purpose === 'learning_plan') return false;
      if (b.clip_id === clip.id) return true;
      return finite(b.original_start) && finite(b.original_end) &&
        finite(clip.original_start_seconds) && finite(clip.original_end_seconds) &&
        b.original_start < clip.original_end_seconds && b.original_end > clip.original_start_seconds;
    };
    const sortTime = j => (finite(j?.asked_at) ? j.asked_at : (finite(j?.updated) ? j.updated : 0));
    const clipAiJobs = listJobs.filter(j => j?.role === 'ai' && jobMatchesClip(j))
      .sort((a, b) => (sortTime(b) - sortTime(a)) || String(b.id || '').localeCompare(String(a.id || '')));

    const opMatches = pendingOp && !pendingOp.cancelled && pendingOp.clip_id === clip.id &&
      (publication == null || pendingOp.publication === publication);
    if (opMatches) {
      const opAi = matchPendingAiJob(pendingOp, listJobs);
      const opRender = pendingOp.exportId ? listJobs.find(j => j.id === pendingOp.exportId) : null;
      const derived = deriveActiveRequestState({pendingOp, renderJob: opRender, aiJob: opAi});
      if (derived && derived.stage !== 'failed') {
        return {
          kind: derived.stage === 'needs_attention' ? 'attention' : 'active',
          count: 0,
          text: derived.badgeText,
          stage: derived.stage,
        };
      }
      if (derived && derived.stage === 'failed' && !pendingOp.dismissedError) {
        return {kind: 'attention', count: 0, text: 'Needs attention', stage: 'failed'};
      }
    }

    const activeAi = clipAiJobs.find(j => ['queued', 'leased', 'calling'].includes(j.state));
    if (activeAi) {
      const derived = deriveActiveRequestState({aiJob: activeAi});
      if (derived) return {kind: 'active', count: 0, text: derived.badgeText, stage: derived.stage};
    }

    if (clipAiJobs.some(j => j.state === 'needs_review')) {
      return {kind: 'attention', count: 0, text: 'Needs attention', stage: 'needs_attention'};
    }
    const latestAi = clipAiJobs[0];
    if (latestAi && latestAi.state === 'failed') {
      return {kind: 'attention', count: 0, text: 'Needs attention', stage: 'failed'};
    }

    const clipAiIds = new Set(clipAiJobs.map(j => j.id));
    const tipCount = listAnns.filter(a => {
      if (!a || a.status === 'dismissed') return false;
      if (clipAiIds.size) return clipAiIds.has(a.job_id);
      return a.provenance?.clip_id === clip.id || (Array.isArray(a.related_clip_ids) && a.related_clip_ids.includes(clip.id));
    }).length;
    if (tipCount > 0) {
      return {kind: 'tips', count: tipCount, text: tipCount === 1 ? '1 tip' : `${tipCount} tips`, stage: 'ready'};
    }
    return {kind: 'idle', count: 0, text: '', stage: 'idle'};
  }

  function shouldAutoOpenPlayerOptions({clip = null, subjectCustomized = false, subjectHint = ''} = {}) {
    return Boolean(clip?.player_assignment?.needs_review || subjectCustomized || normalizeSubjectHint(subjectHint));
  }

  return {
    finite, clamp, clock, shortClock, sampleTimes, sourceTime, panBounds, clampInterval, clampToInterval,
    resolveIntervalPlayback, coachingPrompt, copyText, crc32, zipFiles, normalizeSubjectHint,
    coachingSubjectOptions, resolveCoachingSubject, describeCoachingSubject,
    STROKE_PHASE_LABELS, BODY_AREA_LABELS, normalizeCrop, evidenceToClipPoint, evidenceToClipBox,
    letterboxRect, projectPointToStage, projectBoxToStage, selectSuggestionRanges, pickSuggestionRangeIndex,
    resolveSparseOverlay, formatNowTry, formatExcerptSummary, deriveActiveRequestState,
    matchPendingAiJob, deriveClipCoachBadge, shouldAutoOpenPlayerOptions,
  };
});
