const test=require('node:test');
const assert=require('node:assert/strict');
const C=require('../../static/clip-study-core.js');
const clip={index:2,original_start_seconds:60,original_end_seconds:70};
test('clock carries millisecond rounding correctly',()=>{
  assert.equal(C.clock(59.9996),'1:00.000');assert.equal(C.clock(3600.005),'1:00:00.005');assert.equal(C.clock(-1),'0:00.000');assert.equal(C.clock(Infinity),'0:00.000');
});
test('sampling stays inside short and long clips with bounded frame count',()=>{
  for(const d of [.04,6,1800]){const times=C.sampleTimes(d);assert.equal(times.length,6);assert(times.every(t=>t>0&&t<d));assert(times.every((t,i)=>!i||t>times[i-1]));}
  for(const d of [0,NaN,Infinity,-1])assert.deepEqual(C.sampleTimes(d),[]);
  assert.deepEqual(C.sampleTimes(10,100),[]);
});
test('source offsets clamp to declared cut and remain approximate in prompt',()=>{
  assert.equal(C.sourceTime(clip,-2),60);assert.equal(C.sourceTime(clip,2),62);assert.equal(C.sourceTime(clip,11),70);
  const prompt=C.coachingPrompt(clip,[{time:3},{time:1}]);assert(prompt.indexOf('0:01.000')<prompt.indexOf('0:03.000'));assert.match(prompt,/approximate/);assert.match(prompt,/sparse stills/);assert.match(C.coachingPrompt(clip),/original-quality clip/);
});
test('pan bounds account for letterboxing and prevent losing the image',()=>{
  assert.deepEqual(C.panBounds(800,450,1920,1080,1),{x:0,y:0});assert.deepEqual(C.panBounds(800,450,1920,1080,2),{x:400,y:225});
  assert.equal(C.panBounds(800,450,1080,1920,2).x,0);assert.deepEqual(C.panBounds(0,0,0,0,4),{x:0,y:0});
});
test('ZIP CRC and entries are real and bounded, with no unsafe names',async()=>{
  const enc=new TextEncoder();assert.equal(C.crc32(enc.encode('123456789')),0xcbf43926);
  const blob=C.zipFiles([{name:'frame-01.jpg',data:new Uint8Array([1,2,3])},{name:'review-prompt.txt',data:'tennis'}]);
  const bytes=new Uint8Array(await blob.arrayBuffer()),view=new DataView(bytes.buffer);
  assert.equal(view.getUint32(0,true),0x04034b50);assert.equal(view.getUint32(bytes.length-22,true),0x06054b50);assert.equal(view.getUint16(bytes.length-14,true),2);
  assert.throws(()=>C.zipFiles([{name:'../secret',data:'x'}]),/filename/);
  assert.throws(()=>C.zipFiles(Array.from({length:11},()=>({name:'x',data:'x'}))),/Too many/);
  assert.throws(()=>C.zipFiles([{name:'large.jpg',data:new Uint8Array(20*1024*1024+1)}]),/20 MB/);
});
test('copy policy supports secure API, LAN HTTP and denied permissions',async()=>{
  const calls=[];
  assert.equal(await C.copyText('prompt',{secure:true,writeText:async text=>calls.push(text),legacyCopy:()=>{throw Error('not needed');}}),true);
  assert.deepEqual(calls,['prompt']);
  assert.equal(await C.copyText('lan',{secure:false,writeText:()=>{throw Error('must not use secure API');},legacyCopy:text=>text==='lan'}),true);
  assert.equal(await C.copyText('denied',{secure:true,writeText:async()=>{throw Error('denied');},legacyCopy:()=>false}),false);
  assert.equal(await C.copyText('fallback',{secure:true,writeText:async()=>{throw Error('denied');},legacyCopy:()=>true}),true);
});
