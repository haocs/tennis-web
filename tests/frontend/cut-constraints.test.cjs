const test=require('node:test'),assert=require('node:assert/strict'),C=require('../../static/cut-constraints.js');
const manifest={source:{duration_seconds:30},clips:[{id:'one',original_start_seconds:5,original_end_seconds:10}]};
const review=(body,target={scope:'clip',clip_id:'one'})=>({id:'r',version:2,kind:'cut_feedback',status:'active',body,target});
test('judgments and direction-only evidence do not create cut commands',()=>{
 for(const body of [{verdict:'good_cut'},{reasons:['wrong_player']},{reasons:['too_short']},{reasons:['start_early']}])assert.equal(C.draft([review(body)],manifest).rows.length,0);
});
test('exact bounds create unconfirmed preview rows; only confirmation yields commands',()=>{
 const d=C.draft([review({suggested_end_ms:12000})],manifest);assert.equal(d.rows[0].original_start_ms,5000);assert.equal(C.commands(d.rows).override_clip_bounds_ms.length,0);d.rows[0].confirmed=true;assert.equal(C.commands(d.rows).override_clip_bounds_ms[0].source_revision,2);
});
test('missed gaps require confirmation and keep/exclude conflicts cannot be hidden',()=>{
 const d=C.draft([review({}, {scope:'interval',start_seconds:15,end_seconds:18})],manifest);d.rows[0].confirmed=true;const cmd=C.commands(d.rows);assert.deepEqual(C.validate(cmd,manifest),[]);cmd.exclude_intervals_ms.push({start_ms:17000,end_ms:19000});assert.ok(C.validate(cmd,manifest).length);
});
test('bound validation rejects NaN, fractional milliseconds, unknown clips and overlapping overrides',()=>{
 const base=()=>({keep_intervals_ms:[],exclude_intervals_ms:[],override_clip_bounds_ms:[]});
 for(const row of [{start_ms:NaN,end_ms:1000},{start_ms:0.5,end_ms:1000},{start_ms:0,end_ms:31000}]){const c=base();c.keep_intervals_ms.push(row);assert.ok(C.validate(c,manifest).length);}
 const c=base();c.override_clip_bounds_ms.push({clip_id:'elsewhere',original_start_ms:0,original_end_ms:1000});assert.ok(C.validate(c,manifest).length);
});
