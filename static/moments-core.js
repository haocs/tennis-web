/* Original-time projections are supplied by the server; this module uses only
   current playback coordinates and never changes a saved moment's target. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.MomentPlayback=api;})(typeof globalThis!=='undefined'?globalThis:this,()=>{
 'use strict';
 const defaults={highlight:1,coach:3,note:3};
 const finite=x=>typeof x==='number'&&Number.isFinite(x);
 function displayText(a,full=false){
  const text=typeof a.body?.text==='string'?a.body.text:'';
  if(a.kind!=='coach'||!a.body?.response)return text;
  const raw=text.trim().replace(/^```(?:json)?\s*/i,'').replace(/\s*```$/,'');
  if(!raw.startsWith('{')&&!raw.startsWith('['))return text;
  try{
   const parsed=JSON.parse(raw),suggestions=Array.isArray(parsed.suggestions)?parsed.suggestions:[];
   const summary=typeof parsed.summary==='string'?parsed.summary.trim():'';
   if(!full)return summary||suggestions.map(s=>typeof s.cue==='string'?s.cue:'').filter(Boolean).join(' · ')||'Saved coaching review';
   const sections=suggestions.map(s=>[s.title,s.observation,s.cue&&'Cue: '+s.cue,s.drill&&'Drill: '+s.drill,s.uncertainty&&'Uncertainty: '+s.uncertainty].filter(v=>typeof v==='string'&&v.trim()).join('\n'));
   return [summary,...sections].filter(Boolean).join('\n\n')||'Saved coaching review';
  }catch(_){return 'Saved coaching review';}
 }
 function canPlay(a,items=[]){
  if(a.playback_eligible)return true;
  // A response is anchored to the selected excerpt, not a verified technique
  // timestamp. Prefer reviewed timed suggestions when the same job has them.
  return a.kind==='coach'&&a.body?.response&&a.visible_intervals?.length>0&&!items.some(b=>b.kind==='coach'&&!b.body?.response&&b.playback_eligible&&b.origin?.job_id&&b.origin.job_id===a.origin?.job_id);
 }
 function timeline(items,{mode='full',clipId=null,clips=[]}={}){
  return items.filter(a=>a.status==='active'&&canPlay(a,items)&&(a.record_class==='moment'||['highlight','coach'].includes(a.kind)||a.kind==='note'&&['point','interval'].includes(a.target?.scope))).flatMap(a=>{
   const point=!finite(a.target.end_seconds);const duration=defaults[a.kind]||3;
   return (a.visible_intervals||[]).flatMap((f,index)=>{
    if(mode==='clip'&&f.clip_id!==clipId)return [];
    // Reviewed coaching remains tied to the player it describes.
    if(a.kind==='coach'&&a.player_id&&clips.find(c=>c.id===f.clip_id)?.main_player?.id!==a.player_id)return [];
    const start=mode==='clip'?f.local_start:f.playback_start,end=mode==='clip'?f.local_end:f.playback_end;
    if(!finite(start)||!finite(end)||start<0||(!point&&end<=start))return [];
    return [{key:a.id+':'+index,id:a.id,kind:a.kind,start,end,point,duration,fragment:f,annotation:a}];
   });
  }).sort((a,b)=>a.start-b.start||a.key.localeCompare(b.key));
 }
 function nextHighlight(items,position,options={}){
  const seen=new Set();return timeline(items,{...options,mode:'full'}).filter(e=>{
   if(e.kind!=='highlight'||seen.has(e.id))return false;seen.add(e.id);return e.start>position+.001;
  })[0]||null;
 }
 class CueClock{
  constructor(){this.reset();}
  reset(){this.previous=null;this.points=new Map();}
  tick(events,position,now,{discontinuity=false,playing=false,rate=1}={}){
   if(!finite(position)||!finite(now))return [];
   const backward=this.previous!==null&&position<this.previous-.01;
   if(discontinuity||backward)this.points.clear();
   const result=[],ids=new Set();
   for(const e of events){
    if(ids.has(e.id))continue;
    if(!e.point){if(position>=e.start&&position<e.end){result.push(e);ids.add(e.id);}continue;}
    const near=position>=e.start&&position<e.start+e.duration;
    const crossed=playing&&!discontinuity&&!backward&&this.previous!==null&&this.previous<e.start&&position>=e.start&&position-this.previous<=Math.max(1,rate);
    if((near||crossed)&&!this.points.has(e.key))this.points.set(e.key,now+e.duration*1000);
    const expiresAt=this.points.get(e.key);
    if(expiresAt>now){result.push({...e,expiresAt});ids.add(e.id);}
   }
   this.previous=position;return result;
  }
 }
 return {defaults,displayText,canPlay,timeline,nextHighlight,CueClock};
});
