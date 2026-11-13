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

test('manual coaching prompt includes anonymous profile without name or player ID',()=>{
  const prompt=C.coachingPrompt({...clip,main_player:{id:'private-player-id',name:'PrivateName',height_cm:181,level:'experienced'}});
  assert.match(prompt,/181/);assert.match(prompt,/experienced/);assert.doesNotMatch(prompt,/PrivateName|private-player-id|I am a beginner/);
});

test('interval bounds clamp start and end pins and enforce strict interval playback and loop',()=>{
  assert.deepEqual(C.clampInterval(0,2,2),{start:0,end:2});
  assert.deepEqual(C.clampInterval(1.95,2,2,'start'),{start:1.9,end:2});
  assert.deepEqual(C.clampInterval(0.5,0.4,2,'end'),{start:0.5,end:0.6});
  assert.deepEqual(C.clampInterval(-1,5,2),{start:0,end:2});
  assert.equal(C.clampToInterval(0.1,0.4,1.2,2),0.4);
  assert.equal(C.clampToInterval(1.8,0.4,1.2,2),1.2);
  assert.equal(C.clampToInterval(0.8,0.4,1.2,2),0.8);
  assert.deepEqual(C.resolveIntervalPlayback(0.2,0.4,1.2,false),{action:'seek',time:0.4});
  assert.deepEqual(C.resolveIntervalPlayback(0.8,0.4,1.2,false),{action:'continue',time:0.8});
  assert.deepEqual(C.resolveIntervalPlayback(1.2,0.4,1.2,false),{action:'pause',time:1.2});
  assert.deepEqual(C.resolveIntervalPlayback(1.2,0.4,1.2,true),{action:'loop',time:0.4});
});

test('coaching subject options order main player, recording players, saved players, and no-profile cleanly',()=>{
  const alex={id:'a'.repeat(32),name:'Alex',version:1,height_cm:172,level:'beginner'};
  const blair={id:'b'.repeat(32),name:'Blair',version:1,height_cm:180,level:'experienced'};
  const casey={id:'c'.repeat(32),name:'Casey',version:2,height_cm:168,level:'intermediate'};
  const alexFresh={...alex,version:3,level:'intermediate'};
  const assignedClip={...clip,main_player:alex};
  const session={recording_players:[alex,blair],players:[alex,blair]};

  const cat=C.coachingSubjectOptions({clip:assignedClip,session,players:[casey,alexFresh,blair]});
  assert.equal(cat.defaultValue,alex.id);
  assert.deepEqual(cat.options.map(o=>[o.value,o.label,o.role]),[
    [alex.id,'Alex (clip main player)','main_player'],
    [blair.id,'Blair (in recording)','recording_player'],
    [casey.id,'Casey','saved_player'],
    ['','No player profile','none'],
  ]);
  assert.equal(cat.byId[alex.id].version,3,'Higher profile version from refreshed players list supersedes stale clip snapshot');

  // Authoritative list omits soft-deleted profiles.
  const catRemoved=C.coachingSubjectOptions({clip:assignedClip,session,players:[alexFresh],authoritativePlayers:true});
  assert.deepEqual(catRemoved.options.map(o=>o.value),[alex.id,'']);

  // Unassigned clip defaults to empty no-profile option.
  const unassignedCat=C.coachingSubjectOptions({clip:{...clip,main_player:null},session:{recording_players:[blair],players:[blair]},players:[blair,casey]});
  assert.equal(unassignedCat.defaultValue,'');
  assert.equal(unassignedCat.options.at(-1).label,'No player profile (unassigned)');
});

test('resolveCoachingSubject, normalizeSubjectHint, and describeCoachingSubject enforce request-local semantics',()=>{
  const alex={id:'a'.repeat(32),name:'Alex',version:2,height_cm:172,level:'beginner'};
  const blair={id:'b'.repeat(32),name:'Blair',version:4,height_cm:180,level:'experienced'};
  const assignedClip={...clip,main_player:alex};
  const players=[alex,blair];

  assert.equal(C.normalizeSubjectHint('  far court\n\tin   blue  '),'far court in blue');
  assert.equal(C.normalizeSubjectHint('x'.repeat(200)).length,160);
  assert.equal(C.normalizeSubjectHint(null),'');

  const mainRes=C.resolveCoachingSubject({selectedPlayerId:alex.id,clip:assignedClip,players,subjectHint:'   '});
  assert.equal(mainRes.subjectMode,'main_player');
  assert.equal(mainRes.playerId,alex.id);
  assert.equal(mainRes.playerProfileVersion,2);
  assert.equal(mainRes.subjectHint,null);
  assert.match(C.describeCoachingSubject({selectedPlayerId:alex.id,clip:assignedClip,players}),/For Alex \(clip main player\)/);

  const otherRes=C.resolveCoachingSubject({selectedPlayerId:blair.id,clip:assignedClip,players,subjectHint:'far court dark shirt'});
  assert.equal(otherRes.subjectMode,'selected_player');
  assert.equal(otherRes.playerId,blair.id);
  assert.equal(otherRes.playerProfileVersion,4);
  assert.equal(otherRes.subjectHint,'far court dark shirt');
  const otherDesc=C.describeCoachingSubject({selectedPlayerId:blair.id,clip:assignedClip,players});
  assert.match(otherDesc,/For Blair \(this request only; clip player unchanged\)/);
  assert.match(otherDesc,/Auto detect scans foreground motion, not player identity/);

  const noneRes=C.resolveCoachingSubject({selectedPlayerId:'',clip:assignedClip,players});
  assert.deepEqual(noneRes,{subjectMode:'none',playerId:null,playerProfileVersion:null,player:null,subjectHint:null});
  assert.match(C.describeCoachingSubject({selectedPlayerId:'',clip:assignedClip,players}),/clip player stays Alex/);

  assert.throws(()=>C.resolveCoachingSubject({selectedPlayerId:'f'.repeat(32),clip:assignedClip,players}),/Choose a saved player/);
});
