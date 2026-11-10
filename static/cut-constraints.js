'use strict';
/* Pure preview helpers. Review evidence never becomes an executable command implicitly. */
const CutConstraints=(()=>{
 const overlap=(a,b)=>Math.max(a.start_ms,b.start_ms)<Math.min(a.end_ms,b.end_ms);
 function draft(reviews,manifest){
  const clips=new Map(manifest.clips.map(c=>[c.id,c])),rows=[],advisory=[];
  for(const r of reviews){
   if(r.kind!=='cut_feedback'||r.status!=='active')continue;
   const evidence={source_annotation_id:r.id,source_revision:r.version};
   if(r.target.scope==='interval')rows.push({type:'keep_intervals_ms',confirmed:false,start_ms:Math.round(r.target.start_seconds*1000),end_ms:Math.round(r.target.end_seconds*1000),...evidence});
   else{
    const c=clips.get(r.target.clip_id),b=r.body||{};
    if(c&&(Number.isInteger(b.suggested_start_ms)||Number.isInteger(b.suggested_end_ms)))rows.push({type:'override_clip_bounds_ms',confirmed:false,clip_id:c.id,original_start_ms:b.suggested_start_ms??Math.round(c.original_start_seconds*1000),original_end_ms:b.suggested_end_ms??Math.round(c.original_end_seconds*1000),...evidence});
    else advisory.push({annotation_id:r.id,version:r.version,text:b.text||'',reasons:b.reasons||[],verdict:b.verdict??null});
   }
  }
  return {rows,advisory};
 }
 function validate(commands,manifest){
  const duration=Math.round(manifest.source.duration_seconds*1000),clips=new Map(manifest.clips.map(c=>[c.id,c])),errors=[];
  const positive=[],negative=[],overrides=[];
  for(const type of ['keep_intervals_ms','exclude_intervals_ms','override_clip_bounds_ms']){
   if(!Array.isArray(commands[type])||commands[type].length>50){errors.push('Invalid or oversized command list');continue;}
   for(const row of commands[type]){
    const start=type==='override_clip_bounds_ms'?row.original_start_ms:row.start_ms,end=type==='override_clip_bounds_ms'?row.original_end_ms:row.end_ms;
    if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||start>=end||end>duration){errors.push('Use positive intervals inside the original recording');continue;}
    const range={start_ms:start,end_ms:end};
    if(type==='override_clip_bounds_ms'){
     if(!clips.has(row.clip_id))errors.push('Override refers to another output');
     else{const c=clips.get(row.clip_id),base={start_ms:Math.round(c.original_start_seconds*1000),end_ms:Math.round(c.original_end_seconds*1000)};if(!overlap(range,base)||[...clips.values()].some(other=>other.id!==row.clip_id&&overlap(range,{start_ms:Math.round(other.original_start_seconds*1000),end_ms:Math.round(other.original_end_seconds*1000)})))errors.push('Boundary overrides must stay with their base clip; use keep/exclude commands across clips');}
     if(overrides.some(x=>x.clip_id===row.clip_id||overlap(x,range)))errors.push('Conflicting clip overrides');
     overrides.push({...range,clip_id:row.clip_id});
    }
    (type==='exclude_intervals_ms'?negative:positive).push(range);
   }
  }
  if(positive.some(a=>negative.some(b=>overlap(a,b))))errors.push('Keep and exclude commands overlap');
  return [...new Set(errors)];
 }
 function commands(rows){
  const result={keep_intervals_ms:[],exclude_intervals_ms:[],override_clip_bounds_ms:[]};
  for(const row of rows){if(!row.confirmed||!result[row.type])continue;const {type,confirmed,...command}=row;result[type].push(command);}
  return result;
 }
 return {draft,validate,commands};
})();
if(typeof module!=='undefined')module.exports=CutConstraints;
