/* Original-time projections are supplied by the server; this module uses only
   current playback coordinates and never changes a saved moment's target. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.MomentPlayback=api;})(typeof globalThis!=='undefined'?globalThis:this,()=>{
 'use strict';
 const defaults={highlight:3,coach:3,note:3};
 const finite=x=>typeof x==='number'&&Number.isFinite(x);
 const msRound=x=>Math.round(Number(x)*1000)/1000;
 const msMin=x=>Math.ceil(Number(x)*1000-1e-6)/1000;
 const msMaxPoint=x=>(Math.ceil(Number(x)*1000-1e-6)-1)/1000;
 const msMaxEnd=x=>Math.floor(Number(x)*1000+1e-6)/1000;
 function clampPointToClip(rawSeconds,clip){
  const lo=msMin(clip.original_start_seconds),hi=Math.max(lo,msMaxPoint(clip.original_end_seconds));
  return Math.max(lo,Math.min(hi,msRound(rawSeconds)));
 }
 function displayText(a,full=false){
  const text=typeof a.body?.text==='string'?a.body.text:typeof a.payload?.text==='string'?a.payload.text:'';
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
 function isMoment(a){
  return a.record_class?a.record_class==='moment':['highlight','coach','comment'].includes(a.kind)||a.kind==='note'&&['point','interval'].includes(a.target?.scope);
 }
 function inScope(items,{mode='full',clipId=null,clips=[],sourceId=null}={}){
  return items.filter(a=>isMoment(a)&&(!sourceId||!a.source_id||a.source_id===sourceId)).flatMap(a=>{
   const point=!finite(a.target?.end_seconds),start=a.target?.start_seconds,end=a.target?.end_seconds;
   if(!finite(start))return [];
   const fragments=(a.visible_intervals||[]).flatMap(f=>{
    if(mode==='clip'&&f.clip_id!==clipId)return [];
    const clip=clips.find(c=>c.id===f.clip_id);
    if(clips.length&&!clip)return [];
    if(a.kind==='coach'&&a.player_id&&clip?.main_player?.id!==a.player_id)return [];
    const lo=clip?.original_start_seconds??f.original_start,hi=clip?.original_end_seconds??f.original_end;
    if(!finite(lo)||!finite(hi))return [];
    if(point&&(start<lo||(clip&&start>=hi)||Math.abs(start-f.original_start)>1e-6))return [];
    const x=point?start:Math.max(start,lo,f.original_start),y=point?start:Math.min(end,hi,f.original_end);
    if(!finite(x)||!finite(y)||!point&&y<=x)return [];
    const local=x-(clip?.original_start_seconds??(f.original_start-f.local_start));
    const playback=clip?clip.playback_start_seconds+local:f.playback_start+x-f.original_start;
    return [{...f,original_start:x,original_end:y,local_start:local,local_end:local+y-x,playback_start:playback,playback_end:playback+y-x}];
   });
   return fragments.length?[{annotation:a,fragments}]:[];
  });
 }
 function timeline(items,options={}){
  const mode=options.mode||'full';
  return inScope(items,options).filter(({annotation:a})=>a.status==='active'&&canPlay(a,items)).flatMap(({annotation:a,fragments})=>{
   const point=!finite(a.target.end_seconds),duration=defaults[a.kind]||3;
   return fragments.map((f,index)=>({key:a.id+':'+index,id:a.id,kind:a.kind,start:mode==='clip'?f.local_start:f.playback_start,end:mode==='clip'?f.local_end:f.playback_end,point,duration,fragment:f,annotation:a}));
  }).sort((a,b)=>a.start-b.start||a.key.localeCompare(b.key));
 }
 function nextMoment(items,position,options={}){
  const seen=new Set(),events=timeline(items,options).filter(e=>{if(seen.has(e.id))return false;seen.add(e.id);return true;});
  return events.find(e=>e.start>position+.001)||events[0]||null;
 }
 function teaserText(a){
  const text=displayText(a).trim();
  if(a.kind==='coach'&&a.body?.response&&text==='Saved coaching review'&&/^[{[\x60]/.test((a.body?.text||'').trim()))return '';
  return text;
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
 return {defaults,msRound,msMin,msMaxPoint,msMaxEnd,clampPointToClip,displayText,teaserText,canPlay,inScope,timeline,nextMoment,nextHighlight,CueClock};
});
