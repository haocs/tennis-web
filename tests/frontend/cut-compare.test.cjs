const test=require('node:test'),assert=require('node:assert/strict'),C=require('../../static/cut-compare-core.js');
const manifest=ranges=>({id:'a'.repeat(64),source:{sha256:'a'.repeat(64),duration_seconds:20},video:{duration_seconds:20},clips:ranges.map(([a,b],i)=>({id:String(i),index:i+1,original_start_seconds:a,original_end_seconds:b,duration_seconds:b-a,playback_start_seconds:a,playback_end_seconds:b}))});
test('elementary original-time decomposition exposes every retained/removed state',()=>{
 const result=C.decompose(manifest([[2,8]]),manifest([[5,12]]));assert.deepEqual(result.segments.map(s=>s.state),['BOTH_REMOVED','ONLY_A_RETAINED','BOTH_RETAINED','ONLY_B_RETAINED','BOTH_REMOVED']);assert.equal(result.segments[2].startMs,5000);
});
test('empty candidate remains comparable; mismatched originals and duration reject',()=>{
 assert.ok(C.decompose(manifest([[2,8]]),manifest([])).segments.some(s=>s.state==='ONLY_A_RETAINED'));
 for(const change of [{id:'b'.repeat(64)},{source:{sha256:'a'.repeat(64),duration_seconds:21}}])assert.throws(()=>C.decompose(manifest([]),{...manifest([]),...change}));
});
test('seek gate cancels stale metadata waits before a newer seek can play',async()=>{
 class Video extends EventTarget{constructor(){super();this.readyState=0;this.duration=20;this.currentTime=0;this.src='';}pause(){}load(){}getAttribute(){return this.src;}}
 const video=new Video(),gate=new C.SeekGate(),epoch=gate.cancel(),old=gate.ready(video,'old.mp4',5,epoch);gate.cancel();assert.equal(await old,false);assert.equal(await gate.ready(video,'obsolete.mp4',0,epoch),false);assert.equal(video.src,'old.mp4');
});
test('metadata-only decoder at clip start stays playable until the Play gesture',async()=>{
 class Video extends EventTarget{constructor(){super();this.readyState=0;this.duration=2;this.currentTime=0;this.src='';}pause(){}getAttribute(){return this.src;}load(){queueMicrotask(()=>{this.readyState=1;this.dispatchEvent(new Event('loadedmetadata'));});}}
 const video=new Video(),gate=new C.SeekGate(),epoch=gate.cancel();
 assert.equal(await Promise.race([gate.ready(video,'zero.mp4',0,epoch),new Promise(resolve=>setTimeout(()=>resolve('stalled'),100))]),true);
 assert.equal(video.preload,'auto');
});
