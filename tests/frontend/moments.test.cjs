const test=require('node:test'),assert=require('node:assert/strict');
const {timeline,nextHighlight,CueClock}=require('../../static/moments-core.js');
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
test('point highlight dismisses at one wall-clock second even when paused',()=>{
 const events=timeline([moment('h',2)]),clock=new CueClock();
 assert.equal(clock.tick(events,2,0).length,1);
 assert.equal(clock.tick(events,2,999).length,1);
 assert.equal(clock.tick(events,2,1000).length,0);
 assert.equal(clock.tick(events,2,2000).length,0); // no repeated trigger at a paused playhead
});
test('point coaching dismisses at three seconds without inventing an end timestamp',()=>{
 const a=moment('coach',1,{kind:'coach'}),clock=new CueClock(),events=timeline([a]);
 assert.equal(clock.tick(events,1,0).length,1);
 assert.equal(clock.tick(events,1,2999).length,1);
 assert.equal(clock.tick(events,1,3000).length,0);
 assert.equal(a.target.end_seconds,undefined);
});
test('explicit ends use playback time and remain visible while paused',()=>{
 const events=timeline([moment('range',2,{end:5})]),clock=new CueClock();
 assert.equal(clock.tick(events,2,0).length,1);
 assert.equal(clock.tick(events,2,100000).length,1);
 assert.equal(clock.tick(events,4.99,100001).length,1);
 assert.equal(clock.tick(events,5,100002).length,0);
});
test('seeks reconcile the landing position rather than firing every skipped moment',()=>{
 const events=timeline([moment('one',1),moment('eight',8,{end:10})]),clock=new CueClock();
 clock.tick(events,0,0);
 assert.deepEqual(clock.tick(events,9,10,{discontinuity:true}).map(e=>e.id),['eight']);
 assert.deepEqual(clock.tick(events,1,20,{discontinuity:true}).map(e=>e.id),['one']);
 assert.equal(clock.tick(events,1,1021).length,0);
 assert.equal(clock.tick(events,1,2000,{discontinuity:true}).length,1);
});
test('replaying and changing media resets point cues',()=>{
 const events=timeline([moment('one',1)]),clock=new CueClock();
 clock.tick(events,1,0);assert.equal(clock.tick(events,1.2,1001).length,0);
 assert.equal(clock.tick(events,1,1002).length,1);
 clock.reset();assert.equal(clock.tick(events,1,2000).length,1);
});
test('supports overlapping cues, fast playback crossings and half-open ends',()=>{
 const events=timeline([moment('h',1),moment('coach',1,{kind:'coach',end:2})]),clock=new CueClock();
 clock.tick(events,.9,0);
 assert.deepEqual(clock.tick(events,1.4,100,{playing:true,rate:2}).map(e=>e.id).sort(),['coach','h']);
 assert.equal(clock.tick(events,2,2000).length,0);
});
test('next highlight is strictly later in the video and can be in another clip',()=>{
 const items=[moment('first',1),moment('second',8,{clip:'c2',local:1,playback:4}),moment('coach',3,{kind:'coach'})];
 assert.equal(nextHighlight(items,1).id,'second');
 assert.equal(nextHighlight(items,4),null);
});
test('a split range is one moment, not another next-highlight target',()=>{
 const a=moment('range',1,{end:4});a.visible_intervals.push({...a.visible_intervals[0],clip_id:'c2',playback_start:4,playback_end:6});
 assert.equal(nextHighlight([a],2),null);
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
 assert.equal(clock.tick(events,5,1000,{playing:true,rate:4}).length,0);
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
