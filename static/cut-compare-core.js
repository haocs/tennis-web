(function(root,factory){const api=factory(typeof module==='object'&&module.exports?require('./cut-review-core.js'):root.CutReviewCore);if(typeof module==='object'&&module.exports)module.exports=api;else root.CutCompareCore=api;})(typeof globalThis!=='undefined'?globalThis:this,Core=>{
 'use strict';
 function decompose(a,b){
  if(a.id!==b.id||a.source.sha256!==b.source.sha256||a.source.duration_seconds!==b.source.duration_seconds)throw new Error('Versions must refer to the same original recording and duration');
  const A=Core.buildTimeline(a),B=Core.buildTimeline(b),points=[...new Set([0,A.totalDurationMs,...A.clips.flatMap(c=>[c.startMs,c.endMs]),...B.clips.flatMap(c=>[c.startMs,c.endMs])])].sort((x,y)=>x-y),segments=[];
  for(let i=0;i<points.length-1;i++){
   const startMs=points[i],endMs=points[i+1];if(endMs<=startMs)continue;
   const left=Core.projectOriginalToPlayback(A,startMs),right=Core.projectOriginalToPlayback(B,startMs),keptA=left.kind==='clip',keptB=right.kind==='clip';
   segments.push({startMs,endMs,state:keptA?(keptB?'BOTH_RETAINED':'ONLY_A_RETAINED'):(keptB?'ONLY_B_RETAINED':'BOTH_REMOVED'),a:left,b:right});
  }
  return {a:A,b:B,segments,totalDurationMs:A.totalDurationMs};
 }
 class SeekGate{
  constructor(){this.epoch=0;this.disposers=[];}
  cancel(){this.epoch++;for(const dispose of this.disposers.splice(0))dispose();return this.epoch;}
  async ready(video,url,time,epoch=this.epoch){
   if(epoch!==this.epoch)return false;
   const wait=events=>new Promise(resolve=>{
    let settled=false,timer;
    const finish=ok=>{if(settled)return;settled=true;clearTimeout(timer);for(const e of events)video.removeEventListener(e,onEvent);video.removeEventListener('error',onError);resolve(ok&&epoch===this.epoch);};
    const onEvent=()=>finish(true),onError=()=>finish(false);
    for(const e of events)video.addEventListener(e,onEvent);video.addEventListener('error',onError);timer=setTimeout(()=>finish(false),8000);this.disposers.push(()=>finish(false));
   });
   video.pause();
   if(video.getAttribute('src')!==url){const loaded=wait(['loadedmetadata']);video.src=url;video.load();if(!await loaded)return false;}
   else if(video.readyState<1&&!await wait(['loadedmetadata']))return false;
   if(epoch!==this.epoch||!Number.isFinite(video.duration))return false;
   const target=Math.max(0,Math.min(time,video.duration));
   if(Math.abs(video.currentTime-target)>0.025){const sought=wait(['seeked']);video.currentTime=target;if(!await sought)return false;}
   if(video.readyState<2&&!await wait(['loadeddata','canplay']))return false;
   return epoch===this.epoch;
  }
 }
 return {decompose,SeekGate};
});
