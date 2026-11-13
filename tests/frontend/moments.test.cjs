const test=require('node:test'),assert=require('node:assert/strict');
const {timeline,inScope,nextMoment,teaserText,CueClock}=require('../../static/moments-core.js');
function moment(id,start,{kind='highlight',end,clip='c1',local=start,playback=start,status='active',eligible=true}={}){
 return {id,kind,record_class:'moment',status,playback_eligible:eligible,target:{start_seconds:start,...(end===undefined?{}:{end_seconds:end})},visible_intervals:[{clip_id:clip,original_start:start,original_end:end??start,local_start:local,local_end:local+(end===undefined?0:end-start),playback_start:playback,playback_end:playback+(end===undefined?0:end-start)}]};
}
test('projects original-time moments onto full or clip time and excludes metadata',()=>{
 const a=moment('h',100,{clip:'c2',local:2,playback:12});
 assert.equal(timeline([a])[0].start,12);
 assert.equal(timeline([a],{mode:'clip',clipId:'c2'})[0].start,2);
 assert.equal(timeline([a],{mode:'clip',clipId:'c1'}).length,0);
 assert.equal(timeline([moment('archived',1,{status:'archived'}),moment('legacy',1,{eligible:false}),{...a,record_class:'entity_metadata',kind:'usefulness'}]).length,0);
});
test('point text follows playback time rather than wall clock and stays visible while paused',()=>{
 const events=timeline([moment('h',2)]),clock=new CueClock();
 assert.equal(events[0].end,5);
 assert.equal(clock.tick(events,2,0).length,1);
 assert.equal(clock.tick(events,2,100000).length,1);
 assert.equal(clock.tick(events,4.99,100001).length,1);
 assert.equal(clock.tick(events,5,100002).length,0);
});
test('point coaching ends after five seconds of playback time without inventing an end timestamp',()=>{
 const a=moment('coach',1,{kind:'coach'}),clock=new CueClock(),events=timeline([a]);
 assert.equal(events[0].end,6);
 assert.equal(clock.tick(events,1,0).length,1);
 assert.equal(clock.tick(events,5.99,100000).length,1);
 assert.equal(clock.tick(events,6,100001).length,0);
 assert.equal(a.target.end_seconds,undefined);
});
test('point end clamps to clip boundary and playback timeline without bleeding into next clip',()=>{
 const a=moment('h-end',18,{clip:'c1',local:8,playback:8});
 const events=timeline([a],{clips:[{id:'c1',original_start_seconds:10,original_end_seconds:20,playback_start_seconds:0}]});
 assert.equal(events[0].start,8);
 assert.equal(events[0].end,10);
});
test('explicit ends use playback time and remain visible while paused',()=>{
 const events=timeline([moment('range',2,{end:5})]),clock=new CueClock();
 assert.equal(clock.tick(events,2,0).length,1);
 assert.equal(clock.tick(events,2,100000).length,1);
 assert.equal(clock.tick(events,4.99,100001).length,1);
 assert.equal(clock.tick(events,5,300002).length,0);
});
test('seeks reconcile the landing position rather than firing every skipped moment',()=>{
 const events=timeline([moment('one',1),moment('eight',8,{end:10})]),clock=new CueClock();
 clock.tick(events,0,0);
 assert.deepEqual(clock.tick(events,9,10,{discontinuity:true}).map(e=>e.id),['eight']);
 assert.deepEqual(clock.tick(events,1,20,{discontinuity:true}).map(e=>e.id),['one']);
 assert.equal(clock.tick(events,4,3021).length,0);
 assert.equal(clock.tick(events,1,4000,{discontinuity:true}).length,1);
});
test('seeking back to a point cue shows it again at that playback position',()=>{
 const events=timeline([moment('one',1)]),clock=new CueClock();
 assert.equal(clock.tick(events,1,0).length,1);
 assert.equal(clock.tick(events,4,100).length,0);
 assert.equal(clock.tick(events,1,200).length,1);
 clock.reset();assert.equal(clock.tick(events,1,4000).length,1);
});
test('supports overlapping cues and half-open playback ends without wall-clock extensions',()=>{
 const events=timeline([moment('h',1),moment('coach',1,{kind:'coach',end:2}),moment('note-range',1,{kind:'note',end:2})]),clock=new CueClock();
 clock.tick(events,.9,0);
 assert.deepEqual(clock.tick(events,1.4,100,{playing:true,rate:2}).map(e=>e.id).sort(),['coach','h','note-range']);
 // At playback position 2, interval cues with end=2 expire even when wall-clock time barely moved, while point h (end=4) stays active.
 assert.deepEqual(clock.tick(events,2,150).map(e=>e.id),['h']);
 assert.equal(clock.tick(events,4,200).length,0);
});
test('Jump is current-scope, includes all types, wraps, and groups coincident destinations',()=>{
 const items=[moment('first',1),moment('comment',2,{kind:'note'}),moment('same',2),moment('later',8,{clip:'c2',local:1,playback:4})];
 assert.equal(nextMoment(items,1,{mode:'clip',clipId:'c1'}).id,'comment');
 assert.equal(nextMoment(items,2,{mode:'clip',clipId:'c1'}).id,'first');
 assert.equal(nextMoment(items,2,{mode:'full'}).id,'later');
 assert.equal(nextMoment(items,10,{mode:'full'}).id,'first');
 assert.equal(nextMoment(items,0,{mode:'clip',clipId:'empty'}),null);
});
test('a split range is one jump destination in a full video',()=>{
 const a=moment('range',1,{end:4});a.visible_intervals.push({...a.visible_intervals[0],clip_id:'c2',playback_start:4,playback_end:6});
 assert.equal(nextMoment([a],2).start,1);
});
test('coaching for another player never becomes an automatic cue',()=>{
 const a={...moment('coach',1,{kind:'coach',end:2}),player_id:'alice'};
 assert.equal(timeline([a],{clips:[{id:'c1',main_player:{id:'bob'}}]}).length,0);
 assert.equal(timeline([a],{clips:[{id:'c1',main_player:{id:'alice'}}]}).length,1);
});
test('fast playback does not shorten the wall-clock duration of a point cue',()=>{
 const clock=new CueClock(),events=timeline([moment('h',1)]);
 clock.tick(events,1,0);
 assert.equal(clock.tick(events,3,500,{playing:true,rate:4}).length,1);
 assert.equal(clock.tick(events,5,3000,{playing:true,rate:4}).length,0);
});
test('saved coaching response is excerpt context, superseded by verified suggestions from that job',()=>{
 const response={...moment('response',1,{kind:'coach',end:4,eligible:false}),body:{response:true,text:'Advice'},origin:{job_id:'job'}};
 assert.equal(timeline([response]).length,1);
 const suggestion={...moment('specific',2,{kind:'coach',end:3}),body:{text:'Cue'},origin:{job_id:'job'}};
 assert.deepEqual(timeline([response,suggestion]).map(e=>e.id),['specific']);
});
test('structured saved coaching is readable text in compact cues and full review',()=>{
 const {displayText}=require('../../static/moments-core.js');
 const a={kind:'coach',body:{response:true,text:JSON.stringify({schema_version:2,summary:'Recover after each shot.',suggestions:[{title:'Ready position',observation:'Knees straighten.',cue:'Bend knees.',drill:'Reset ten times.',uncertainty:'Contact timing unclear.'}]})}};
 assert.equal(displayText(a),'Recover after each shot.');
 assert.match(displayText(a,true),/Cue: Bend knees\./);assert.match(displayText(a,true),/Drill: Reset ten times\./);
 assert.ok(!displayText(a,true).includes('schema_version'));
 a.body.text=JSON.stringify({schema_version:2,suggestions:[{cue:'Recover balance.'}]});assert.equal(displayText(a),'Recover balance.');
});
test('malformed or truncated structured responses never leak JSON into playback cues',()=>{
 const {displayText}=require('../../static/moments-core.js');
 assert.equal(displayText({kind:'coach',body:{response:true,text:'{"suggestions":['}}),'Saved coaching review');
 assert.equal(displayText({kind:'coach',body:{response:true,text:'Keep your knees soft.'}}),'Keep your knees soft.');
 assert.equal(displayText({kind:'note',body:{text:'{a user note}'}}),'{a user note}');
});

const clips=[{id:'c1',original_start_seconds:10,original_end_seconds:20,playback_start_seconds:0},{id:'c2',original_start_seconds:30,original_end_seconds:40,playback_start_seconds:10}];
function at(id,start,end,clip='c1'){
 const base=clips.find(c=>c.id===clip),a=moment(id,start,{kind:'note',end,clip,local:start-base.original_start_seconds,playback:start-base.original_start_seconds+base.playback_start_seconds});
 return a;
}
test('shared range selector ignores stale clip matches, metadata, other recordings and removed footage',()=>{
 const good=at('inside',12),outside=at('stale',25),boundary=at('end',20),wrongSource={...good,id:'other',source_id:'other'};
 const metadata={...good,id:'entity',record_class:'entity_metadata',kind:'usefulness'};
 assert.deepEqual(inScope([good,outside,boundary,wrongSource,metadata],{clips,mode:'clip',clipId:'c1',sourceId:'current'}).map(e=>e.annotation.id),['inside']);
 assert.equal(timeline([at('gap',25)],{clips}).length,0);
 assert.equal(timeline([at('final-end',40,undefined,'c2')],{clips}).length,0);
});
test('spanning moments are clipped to visible scope, stay original anchored, and appear once in the list',()=>{
 const a=at('spanning',18,32);
 a.visible_intervals.push({...a.visible_intervals[0],clip_id:'c2',original_start:30,original_end:32,local_start:0,local_end:2,playback_start:10,playback_end:12});
 const events=timeline([a],{clips});assert.deepEqual(events.map(e=>[e.start,e.end]),[[8,10],[10,12]]);
 assert.equal(inScope([a],{clips}).length,1);
 assert.deepEqual(timeline([a],{clips,mode:'clip',clipId:'c2'}).map(e=>[e.start,e.end]),[[0,2]]);
 assert.equal(a.target.start_seconds,18);assert.equal(a.target.end_seconds,32);
});
test('archived moments remain listable in range but cannot produce markers, Jump or teasers',()=>{
 const a={...at('archived',12),status:'archived',playback_eligible:false};
 assert.equal(inScope([a],{clips}).length,1);assert.equal(timeline([a],{clips}).length,0);assert.equal(nextMoment([a],0,{clips}),null);
});
test('only meaningful text produces teaser content; empty highlights and structured fallbacks do not',()=>{
 assert.equal(teaserText({kind:'highlight',body:{text:'  '}}),'');
 assert.equal(teaserText({kind:'highlight',body:{text:' Good recovery '}}),'Good recovery');
 assert.equal(teaserText({kind:'note',body:{text:'A note'}}),'A note');
 assert.equal(teaserText({kind:'comment',payload:{text:'Payload note'}}),'Payload note');
 assert.equal(teaserText({kind:'coach',body:{response:true,text:'{"schema_version":2}'}}),'');
 assert.equal(teaserText({kind:'coach',body:{response:true,text:'{"suggestions":['}}),'');
});
test('fractional clip boundaries preserve exact in-scope projection and quantize captured points inside [start, end)',()=>{
 const {clampPointToClip,msMin,msMaxPoint}=require('../../static/moments-core.js');
 const subClips=[{id:'c1',original_start_seconds:1547.516842,original_end_seconds:1563.516842,playback_start_seconds:0}];
 assert.equal(msMin(1547.516842),1547.517);
 assert.equal(msMaxPoint(1563.516842),1563.516);
 assert.equal(clampPointToClip(1547.516842,subClips[0]),1547.517);
 assert.equal(clampPointToClip(1547.5161,subClips[0]),1547.517);
 assert.equal(clampPointToClip(1563.516842,subClips[0]),1563.516);
 const beforeStart=moment('before-start',1547.516,{kind:'note',clip:'c1',local:0,playback:0});
 const atMin=moment('at-min',1547.517,{kind:'note',clip:'c1',local:0.000158,playback:0.000158});
 const atMax=moment('at-max',1563.516,{kind:'note',clip:'c1',local:15.999158,playback:15.999158});
 const afterEnd=moment('after-end',1563.517,{kind:'note',clip:'c1',local:16,playback:16});
 assert.deepEqual(inScope([beforeStart,atMin,atMax,afterEnd],{clips:subClips,mode:'clip',clipId:'c1'}).map(e=>e.annotation.id),['at-min','at-max']);

 const adjClips=[
  {id:'a',original_start_seconds:0,original_end_seconds:1.0004,playback_start_seconds:0},
  {id:'b',original_start_seconds:1.0004,original_end_seconds:2,playback_start_seconds:1.0004},
 ];
 assert.equal(clampPointToClip(1.0004,adjClips[0]),1.000);
 assert.equal(clampPointToClip(1.0004,adjClips[1]),1.001);
 const p1000A=moment('p1000-a',1.000,{kind:'note',clip:'a',local:1.000,playback:1.000});
 const p1000B=moment('p1000-b',1.000,{kind:'note',clip:'b',local:0,playback:1.000});
 const p1001A=moment('p1001-a',1.001,{kind:'note',clip:'a',local:1.001,playback:1.001});
 const p1001B=moment('p1001-b',1.001,{kind:'note',clip:'b',local:0.0006,playback:1.001});
 assert.deepEqual(inScope([p1000A,p1001A],{clips:adjClips,mode:'clip',clipId:'a'}).map(e=>e.annotation.id),['p1000-a']);
 assert.deepEqual(inScope([p1000B,p1001B],{clips:adjClips,mode:'clip',clipId:'b'}).map(e=>e.annotation.id),['p1001-b']);
});
