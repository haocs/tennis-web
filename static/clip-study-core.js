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
  function coachingPrompt(clip, frames = []) {
    const points = frames.length ? '\nSelected stills, in chronological order:\n' + [...frames].sort((a,b) => a.time-b.time).map((f,i) => `${i+1}. Clip ${clock(f.time)}; approximate original ${clock(sourceTime(clip, f.time))}`).join('\n') : '';
    return `Please review this tennis practice clip and help me improve. I am a beginner.\n\nClip ${String(clip.index).padStart(2,'0')}; original recording interval ${clock(clip.original_start_seconds)}–${clock(clip.original_end_seconds)}. Clip-local time starts at zero. Original offsets are approximate because export timing can round.${points}\n\nReview the entire attached clip or every supplied snapshot in order. Assess all clearly visible aspects of technique across the sequence, group recurring issues and prioritise up to six practical improvements with a cue and drill for each supported issue. Refer to clip-local timestamps or still numbers. Separate observations from guesses; do not infer ball contact, full motion or stroke timing from sparse stills alone. Ask for the full clip if needed. Avoid judging an action you cannot see clearly. The stills are unzoomed browser exports; color may differ for HDR.\n\n${frames.length ? 'I will attach the selected still images. They are context samples, not a full motion sequence.' : 'I will attach the original-quality clip.'}`;
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
  return {finite, clamp, clock, sampleTimes, sourceTime, panBounds, coachingPrompt, copyText, crc32, zipFiles};
});
