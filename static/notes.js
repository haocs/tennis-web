'use strict';
const NoteUI=(()=>{
 let source=null,items=[],itemsLoaded=false,itemsLoadedSourceId=null,itemsLoadedPub=null,loadError='',loadPromise=null,generation=0,timer=null,editing=null,editingKind='note',saveRequest=null,saving=false,markerKey='',refreshSequence=0,lastCurrent=null,pendingHighlight=null,saveFingerprint=null;
 let editorContext=null,entityNotesTarget=null,entityNotesGen=0,editNoteRecord={id:null,version:0,initialText:'',mode:'new',player_id:null,unloaded:true},editNoteDirty=false,editNoteSaveRequest=null,editNoteFingerprint=null;
 let cueTimer=null,cueKey='',mediaKey='',cueClock=new MomentPlayback.CueClock();
 let cutState={active:false,versions:[],currentVersionId:null,currentPublication:null,staleServingVersionId:null,versionId:null,manifest:null,timeline:null,allowHistorical:false,allVersions:false,mode:'clip',clipId:null,gapId:null,draftDirty:false,saveRequest:null,saveFingerprint:null};
 const names={comment:'Moment',note:'Moment',highlight:'Highlight',usefulness:'Less useful',cut_feedback:'Cut review',coach:'Coach suggestion'};
 const verdicts={good_cut:'Good cut',needs_adjustment:'Needs adjustment',poor_cut:'Poor cut',missed_play:'Missed play'};
 const reasons={poor_cut:'Poor cut',no_play:'No tennis play',downtime:'Too much downtime',too_short:'Too short',wrong_player:'Wrong player',start_early:'Start too early',start_late:'Start too late',end_early:'End too early',end_late:'End too late',lost_player:'Lost player',split_rally:'Split rally',missed_play:'Missed play'};
 const active=a=>a.status==='active';
 const msRound=MomentPlayback.msRound;
 const msMin=MomentPlayback.msMin;
 const msMaxEnd=MomentPlayback.msMaxEnd;
 const related=(a,c)=>c&&(a.matched_clip_ids||a.related_clip_ids||[]).includes(c.id);
 const currentClip=()=>source?.clips.find(c=>c.id===model.current);
 const requestId=()=>globalThis.crypto?.randomUUID?.()||'note-'+Date.now()+'-'+Math.random().toString(36).slice(2);
 function lessUseful(c){return items.find(a=>a.kind==='usefulness'&&active(a)&&a.playback_eligible&&related(a,c));}
 function clipMoments(c){return c?MomentPlayback.inScope(items,{mode:'clip',clipId:c.id,clips:source?.clips||[],sourceId:source?.id}).map(e=>e.annotation).filter(a=>active(a)&&MomentPlayback.canPlay(a,items)):[];}
 function hasMoments(c){return clipMoments(c).length>0;}
 function highlights(c){return clipMoments(c).filter(a=>a.kind==='highlight');}
 function moments(c){return clipMoments(c).filter(a=>a.kind!=='highlight');}
 function clipBadge(c){return `${highlights(c).length?'<span class="note-clip-badge" title="Highlighted moment">★</span>':''}${moments(c).length?'<span class="note-clip-badge note-moment" title="Moment">⚡</span>':''}${lessUseful(c)?'<span class="note-clip-badge note-less" title="Disliked clip">↓</span>':''}`;}
 function filterClip(c){return model.noteFilter==='liked'?!!c.like.value:model.noteFilter==='highlights'?highlights(c).length>0:(model.noteFilter==='recommended'||model.noteFilter==='useful')?!lessUseful(c)&&(Number(c?.duration_seconds)>=10||hasMoments(c)):true;}
 function pointAtPlayer(){
  const v=$('#player'),c=currentClip();if(!v||!c||v.readyState<1)throw new Error('Wait for the clip to load');
  const match=model.mode==='clip'?c:source.clips.find(c=>v.currentTime>=c.playback_start_seconds&&v.currentTime<c.playback_end_seconds)||source.clips.at(-1);
  const raw=match.original_start_seconds+v.currentTime-(model.mode==='clip'?0:match.playback_start_seconds);
  return MomentPlayback.clampPointToClip(raw,match);
 }
 function clipTarget(c=currentClip()){if(!c)throw new Error('Choose a clip first');return {scope:'clip',clip_id:c.id,start_seconds:c.original_start_seconds,end_seconds:c.original_end_seconds};}
 async function refresh(force=false){
  if(!source||(!force&&document.hidden))return;const owner=source,rev=generation,seq=++refreshSequence;let after='',all=[];
  try{
   if(cutState.active&&$('#cut-report-list')){
    const vData=await api(`/api/processing/videos/${owner.id}/versions`);
    if(owner!==source||rev!==generation||seq!==refreshSequence)return;
    cutState.versions=vData.versions||[];
    if(cutState.currentVersionId&&vData.current_version_id!==cutState.currentVersionId){
     cutState.staleServingVersionId=vData.current_version_id;
     cutState.currentVersionId=vData.current_version_id;
     cutState.currentPublication=vData.current_publication;
     renderCutVersionSelect();
     updateCutVersionBanner();
    }else if(!cutState.currentVersionId){
     cutState.currentVersionId=vData.current_version_id;
     cutState.currentPublication=vData.current_publication;
    }
    const vidParam=!cutState.allVersions&&cutState.versionId?`video_version_id=${encodeURIComponent(cutState.versionId)}`:'';
    do{
     const q=[vidParam,after?`after=${encodeURIComponent(after)}`:''].filter(Boolean).join('&');
     const data=await api(`/api/processing-annotations/sources/${owner.id}${q?'?'+q:''}`);
     all.push(...data.annotations);after=data.next_after;
    }while(after&&rev===generation);
    if(owner!==source||rev!==generation||seq!==refreshSequence)return;
    if($('#notes-status'))$('#notes-status').textContent='';
    loadError='';items=all;itemsLoaded=true;itemsLoadedSourceId=owner.id;itemsLoadedPub=cutState.manifest?.published_at??owner.published_at;
    if(cutState.versionId!==cutState.currentVersionId)ensureHistoricalCutLease(cutState);
    render();renderCutTimeline();loadCutHints();CutReprocess.update({sourceId:source.id,version:cutState.versionId,manifest:cutState.manifest,reviews:items});
    return;
   }
   do{const data=await api(`/api/recordings/${owner.id}/metadata${after?'?after='+encodeURIComponent(after):''}`);all.push(...data.annotations);after=data.next_after;}while(after&&rev===generation);
   if(owner!==source||rev!==generation||seq!==refreshSequence)return;
   if($('#notes-status'))$('#notes-status').textContent='';
   loadError='';
   const wasUnloaded=!itemsLoaded||itemsLoadedSourceId!==owner.id||itemsLoadedPub!==owner.published_at;
   const changed=JSON.stringify(all)!==JSON.stringify(items);items=all;
   itemsLoaded=true;itemsLoadedSourceId=owner.id;itemsLoadedPub=owner.published_at;
   for(const c of source.clips||[]){const hl=highlights(c);c.highlighted=hl.length>0;c.highlight_count=hl.length;}
   const homeSession=model.sessions.find(v=>v.id===source.id);
   if(homeSession&&homeSession!==source){for(const c of homeSession.clips||[]){const sc=source.clips.find(x=>x.id===c.id);if(sc){c.highlighted=sc.highlighted;c.highlight_count=sc.highlight_count;}}}
   if(changed||wasUnloaded||editNoteRecord.unloaded){if($('#clip-list'))renderClipList();render();renderEntityNotes();markers();}
   current();
  }catch(e){
   if(owner!==source||rev!==generation||seq!==refreshSequence)return;
   loadError=e.message||'Could not load notes.';
   const status=$('#notes-status');if(status)status.textContent='Notes unavailable. '+e.message;
   renderEntityNotes();
  }
 }
 async function create(kind,target,body,rid=requestId(),player_id=currentClip()?.main_player?.id||null,context={source_id:source.id,publication:source.published_at}){
  if(kind==='cut_feedback'||kind==='processing_label'){
   return api('/api/processing-annotations',{method:'POST',body:JSON.stringify({source_id:context.source_id,publication:context.publication,...(context.reviewed_video_version_id?{reviewed_video_version_id:context.reviewed_video_version_id}:{}),...(context.allow_historical?{allow_historical:true}:{}),...(context.require_current_serving!==undefined?{require_current_serving:context.require_current_serving}:{}),kind,target,body,request_id:rid,player_id})});
  }
  if(kind==='highlight'||target.scope==='point'||target.scope==='interval')return api('/api/moments',{method:'POST',body:JSON.stringify({original_recording_id:context.source_id,publication:context.publication,type:kind==='note'?'comment':kind,start_ms:Math.round(target.start_seconds*1000),...(target.end_seconds===undefined?{}:{end_ms:Math.round(target.end_seconds*1000)}),payload:body,request_id:rid,player_id})});
  return api('/api/metadata',{method:'POST',body:JSON.stringify({source_id:context.source_id,publication:context.publication,kind,target,body,request_id:rid,player_id})});
 }
 async function update(a,changes,context={publication:source.published_at}){
  if(a.record_class==='moment'&&!a.read_only){
   const {body,target,...rest}=changes;
   return api('/api/moments/'+a.id,{method:'PUT',body:JSON.stringify({publication:context.publication,version:a.version,...rest,...(body?{payload:body}:{}),...(target?{start_ms:Math.round(target.start_seconds*1000),...(target.end_seconds===undefined?{}:{end_ms:Math.round(target.end_seconds*1000)})}:{})})});
  }
  return api((a.kind==='cut_feedback'||a.kind==='processing_label'?'/api/processing-annotations/':'/api/metadata/')+a.id,{method:'PUT',body:JSON.stringify({publication:context.publication,...(context.reviewed_video_version_id?{reviewed_video_version_id:context.reviewed_video_version_id}:{}),...(context.allow_historical?{allow_historical:true}:{}),version:a.version,...changes})});
 }
 async function highlight(){
  if(saving)return;try{if(!pendingHighlight)pendingHighlight={target:{scope:'point',start_seconds:pointAtPlayer()},id:requestId()};saving=true;const result=await create('highlight',pendingHighlight.target,{text:''},pendingHighlight.id);pendingHighlight=null;await refresh(true);toast(result.already_highlighted?'Moment already highlighted.':'Moment highlighted.');}catch(e){if(e.status)pendingHighlight=null;toast(e.status?e.message:'Could not confirm the save. Tap Highlight again to retry the same moment.');}finally{saving=false;}
 }
 async function demote(){
  const c=model.mode==='clip'?currentClip():null,identity=c?.id||'video',key=source.id+':'+identity;
  if(saving||model.busy.has(key))return;
  try{saving=true;model.busy.add(key);$('#note-demote').disabled=true;
   const existing=items.find(a=>a.kind==='usefulness'&&a.context?.publication===source.published_at&&(c?a.target.scope==='clip'&&a.target.clip_id===c.id:a.target.scope==='video'));
   const value=!downvoted(c);
   await api(`/api/entities/videos/${source.id}/metadata`,{method:'PUT',body:JSON.stringify({publication:source.published_at,subject_type:c?'clip':'video_version',...(c?{clip_id:c.id}:{}),field:'less_useful',value,version:existing?.version||0,...(existing?{key:existing.id}:{})})});
   await refreshShared();await refresh(true);
  }catch(e){toast(e.message);if(e.status===409){await refreshShared();await refresh(true);}}
  finally{saving=false;model.busy.delete(key);refreshLikeButtons();current();}
 }
 function downvoted(c){return items.some(a=>a.kind==='usefulness'&&active(a)&&a.context?.publication===source.published_at&&(c?a.target.scope==='clip'&&a.target.clip_id===c.id:a.target.scope==='video'));}
 function current(){
  const b=$('#note-demote');if(!b||!source)return;const c=currentClip(),target=model.mode==='clip'?c:null,down=downvoted(target);b.disabled=model.busy.has(source.id+':'+(target?.id||'video'));b.setAttribute('aria-pressed',String(down));b.setAttribute('aria-label',(down?'Remove dislike from ':'Dislike ')+(target?'clip '+target.index:'video'));b.title=down?'Remove dislike':'Dislike';$('.player-reactions')?.setAttribute('aria-label',target?'Clip feedback':'Video feedback');
  const h=$('#note-highlight');if(h)h.disabled=!c;
  const cutLink=$('.note-cut-link');if(cutLink&&source)cutLink.href=`/review/${source.id}/cuts${model.mode==='clip'&&model.current?'?clip='+encodeURIComponent(model.current):''}`;
  const scopeKey=model.mode+':'+model.current;if(lastCurrent!==scopeKey){lastCurrent=scopeKey;render();}markers();cueTick();
 }
 function jump(a,selectedFragment=null,preservePlayback=false){
  if(cutState.active&&a.kind==='cut_feedback'&&!sameCutVersion(a)){toast('Open the reviewed version first.');return;}
  if(cutState.active&&($('#cut-video')||$('#cut-gap-slate'))){
   if(a.kind==='cut_feedback'&&a.target.scope==='interval'){
    const startMs=Math.round(a.target.start_seconds*1000),endMs=Math.round(a.target.end_seconds*1000);
    const gap=(cutState.timeline?.gaps||[]).find(g=>g.startMs<=startMs&&endMs<=g.endMs)||(cutState.timeline?.gaps||[])[0];
    if(gap)selectCutGap(gap.id,{startSeconds:a.target.start_seconds,endSeconds:a.target.end_seconds});
    return;
   }
   if(a.target.scope==='clip'&&a.target.clip_id){
    selectCutClip(a.target.clip_id);
    const video=$('#cut-video');
    if(video&&!video.hidden){
     const localTarget=a.body?.suggested_start_ms!=null?Math.max(0,(a.body.suggested_start_ms/1000)-a.target.start_seconds):0;
     const seek=()=>{video.currentTime=Math.min(localTarget,Number.isFinite(video.duration)?video.duration:localTarget);video.pause();updateCutPlayheadReadout();};
     if(video.readyState>=1)seek();else video.addEventListener('loadedmetadata',seek,{once:true});
    }
    return;
   }
  }
  const wasPlaying=preservePlayback&&!$('#player').paused;
  if(!MomentPlayback.canPlay(a,items)||!a.visible_intervals.length)return toast('This moment is outside the current cuts. Its original timestamp is retained.');
  const fragment=selectedFragment||a.visible_intervals.find(f=>f.clip_id===model.current)||a.visible_intervals[0];
  if($('#cut-video')){const video=$('#cut-video'),choice=$('#cut-clip');const seek=()=>{video.currentTime=fragment.local_start;video.pause();};if(choice.value===fragment.clip_id&&video.readyState>=1)seek();else{video.addEventListener('loadedmetadata',seek,{once:true});choice.value=fragment.clip_id;choice.dispatchEvent(new Event('change'));}return;}
  if(model.mode==='full'){playFull(fragment.playback_start,wasPlaying);}else{if(!preservePlayback)playClip(fragment.clip_id,false);setSource(mediaURL(source,source.clips.find(c=>c.id===fragment.clip_id).file),fragment.local_start,wasPlaying);}
  if(!preservePlayback){$('#player').pause();model.playerControls?.reveal();$('.player-shell')?.scrollIntoView({behavior:'smooth',block:'start'});}
 }
 function scopeOptions(){return {mode:model.mode,clipId:model.current,clips:source?.clips||[],sourceId:source?.id};}
 function momentEvents(){return MomentPlayback.timeline(items,scopeOptions());}
 function cueTick(discontinuity=false){
  clearTimeout(cueTimer);const v=$('#player'),box=$('#moment-cues');if(!v||!box||!source)return;
  const key=source.id+':'+source.published_at+':'+model.mode+':'+(model.mode==='clip'?model.current:'');
  if(key!==mediaKey){mediaKey=key;cueClock.reset();cueKey='';box.replaceChildren();}
  if(document.hidden||v.readyState<1||v.seeking)return;
  const active=cueClock.tick(momentEvents().filter(e=>MomentPlayback.teaserText(e.annotation)),v.currentTime,performance.now(),{discontinuity,playing:!v.paused,rate:v.playbackRate});
  const fingerprint=JSON.stringify(active.map(e=>[e.id,e.annotation.version]));
  if(fingerprint!==cueKey){
   cueKey=fingerprint;
   box.innerHTML=active.map(e=>{const a=e.annotation,text=MomentPlayback.teaserText(a);return `<article class="moment-cue ${a.kind==='coach'?'moment-coach':''}" data-active-moment="${esc(a.id)}"><div><strong>${esc(a.kind==='coach'?(a.body.response?'Coach review · excerpt':'Coach'):a.kind==='highlight'?'★ Highlight':'Moment')}</strong><span>Original ${esc(preciseTime(a.target.start_seconds))}</span></div>${text?`<p>${esc(text)}</p>`:''}</article>`;}).join('');
  }
  const expiries=active.filter(e=>e.expiresAt).map(e=>e.expiresAt-performance.now());
  if(expiries.length)cueTimer=setTimeout(()=>cueTick(),Math.max(1,Math.min(...expiries)+10));
  const next=$('#moment-jump');if(next)next.hidden=!momentEvents().length;
 }
 function nextMoment(){
  const v=$('#player');if(!v||!source)return;
  const next=MomentPlayback.nextMoment(items,v.currentTime,scopeOptions());
  if(next)jump(next.annotation,next.fragment,true);
 }
 function markers(){
  const box=$('#note-markers'),v=$('#player');if(!box||!v)return;
  const duration=v.duration;if(!Number.isFinite(duration)||duration<=0){box.replaceChildren();markerKey='';return;}
  const key=JSON.stringify([model.mode,model.current,duration,items.map(a=>[a.id,a.version,a.status,a.playback_eligible,a.visible_intervals])]);if(key===markerKey)return;markerKey=key;
  const events=momentEvents(),groups=new Map();
  events.forEach(e=>{const tick=Math.round(e.start*1000);if(!groups.has(tick))groups.set(tick,[]);groups.get(tick).push(e);});
  box.innerHTML=[...groups.values()].map(group=>{const e=group[0],a=e.annotation,kind=group.some(e=>e.kind==='coach')?'coach':a.kind;return `<button type="button" class="note-marker ${kind==='coach'?'coach-marker':''}" data-note-jump="${esc(e.key)}" data-note-position="${Math.max(0,Math.min(100,100*e.start/duration))}" aria-label="${esc(group.length>1?group.length+' moments':a.kind==='coach'?(a.body.response?'Coach review':'Coach suggestion'):a.kind==='highlight'?'Highlight':'Moment')} at original ${esc(preciseTime(e.fragment.original_start))}" title="${esc(group.length>1?group.length+' moments':names[a.kind]||'Moment')} · Original ${esc(preciseTime(e.fragment.original_start))}"></button>`;}).join('');
  $$('[data-note-jump]',box).forEach(b=>{b.style.setProperty('--note-position',b.dataset.notePosition+'%');b.onclick=()=>{const e=events.find(e=>e.key===b.dataset.noteJump);if(e)jump(e.annotation,e.fragment);};});
 }
 function targetLabel(a){
  const t=a.target;if(t.scope==='video')return 'Whole recording';
  if(a.kind==='cut_feedback'&&t.scope==='interval')return 'Removed gap · Original '+preciseTime(t.start_seconds)+'–'+preciseTime(t.end_seconds);
  if(a.kind==='cut_feedback'&&t.scope==='clip'){
   const clips=(cutState.active&&cutState.manifest?cutState.manifest.clips:source?.clips)||[];
   const clip=clips.find(c=>c.id===t.clip_id);
   return (clip?'Clip '+String(clip.index).padStart(2,'0')+' · ':'')+'Original '+preciseTime(t.start_seconds)+'–'+preciseTime(t.end_seconds);
  }
  return 'Original '+preciseTime(t.start_seconds)+(t.scope==='point'?'':'–'+preciseTime(t.end_seconds));
 }
 function relationLabel(a){if(a.archive_reason==='duplicate_highlight'&&a.status==='archived')return 'Duplicate highlight · history retained';return ({needs_review:'Cut changed · review target',unavailable:'Outside current cuts',partly_retained:'Partly outside current cuts',historical_run:'Earlier processed version',removed_gap:'Removed gap'})[a.relation]||'';}
 function cutItemVersion(a){return a?.subject?.video_version_id||a?.context?.video_version_id||cutState.versions.find(v=>v.manifest.published_at===a?.context?.publication)?.id;}
 function sameCutVersion(a){return a?.context?.publication===cutState.manifest?.published_at&&(!cutItemVersion(a)||cutItemVersion(a)===cutState.versionId);}
 function cutRecordManifest(a){return cutState.versions.find(v=>v.id===cutItemVersion(a))?.manifest||null;}
 function canJumpCutItem(a){
  if(a.status!=='active'||(cutState.active&&!sameCutVersion(a)))return false;
  if(a.kind==='cut_feedback'&&a.target.scope==='interval'&&cutState.active){
   const sMs=Math.round(a.target.start_seconds*1000),eMs=Math.round(a.target.end_seconds*1000);
   return Boolean((cutState.timeline?.gaps||[]).some(g=>g.startMs<=sMs&&eMs<=g.endMs));
  }
  if(a.kind==='cut_feedback'&&a.target.scope==='clip'&&cutState.active){
   return Boolean((cutState.manifest?.clips||[]).some(c=>c.id===a.target.clip_id));
  }
  return MomentPlayback.canPlay(a,items)&&a.visible_intervals.length>0;
 }
 function itemActions(a,cuts){
  if(cuts&&!sameCutVersion(a)){const version=cutItemVersion(a);return (version?'<button class="button small" type="button" data-note-action="select-version">Open reviewed version</button>':'')+'<button class="button small" type="button" data-note-action="history">History</button>';}
  const jumpButton=(cuts||a.record_class==='moment')&&(cuts?canJumpCutItem(a):(a.status==='active'&&MomentPlayback.canPlay(a,items)&&a.visible_intervals.length))?'<button class="button small" type="button" data-note-action="jump" aria-label="Jump to moment">Jump</button>':'';
  const editButtons=(a.read_only||(cuts&&cutState.versionId!==cutState.currentVersionId&&!cutState.allowHistorical))?'':'<button type="button" data-note-action="edit">Edit</button><button type="button" data-note-action="archive">'+(a.status==='active'?'Archive':'Restore')+'</button>';
  const history='<button type="button" data-note-action="history">History</button>';
  if(cuts)return jumpButton+(editButtons+history).replaceAll('<button type=','<button class="button small" type=');
  const coach=a.read_only?'<button class="button small" type="button" data-note-action="coach" aria-label="Review coaching">Review</button>':'';
  return jumpButton+coach+`<details class="moment-more note-actions-menu"><summary class="button small" aria-label="${a.record_class==='entity_metadata'?'Note actions':'Moment actions'}">⋯</summary><div>${editButtons}${history}</div></details>`;
 }
 function cutDetailsHTML(a){
  if(a.kind!=='cut_feedback')return '';
  const parts=[];
  if(a.body?.verdict)parts.push(`<span class="cut-badge cut-verdict-${esc(a.body.verdict)}">${esc(verdicts[a.body.verdict]||a.body.verdict)}</span>`);
  for(const r of (a.body?.reasons||[]))parts.push(`<span class="cut-badge">${esc(reasons[r]||r)}</span>`);
  if(a.body?.suggested_start_ms!=null||a.body?.suggested_end_ms!=null){
   const sSec=a.body.suggested_start_ms!=null?a.body.suggested_start_ms/1000:a.target.start_seconds;
   const eSec=a.body.suggested_end_ms!=null?a.body.suggested_end_ms/1000:a.target.end_seconds;
   parts.push(`<span class="cut-badge cut-bounds-badge">Suggested ${esc(preciseTime(sSec))}–${esc(preciseTime(eSec))}</span>`);
  }
  const vid=a.subject?.video_version_id||a.context?.video_version_id;
  if(vid&&cutState.allVersions)parts.push(`<span class="cut-badge cut-version-badge">${esc(vid.slice(0,10))}</span>`);
  return parts.length?`<div class="cut-item-badges">${parts.join(' ')}</div>`:'';
 }
 function cutContextForRecord(a=null){
  if(!cutState.active)return {source_id:source.id,publication:source.published_at};
  const recVid=a?.subject?.video_version_id||a?.context?.video_version_id||cutState.versionId;
  const recPub=a?.context?.publication??cutState.manifest?.published_at??source.published_at;
  if(a&&!sameCutVersion(a))throw new Error('Open the reviewed version before changing this review.');
  const isHist=recVid!==cutState.currentVersionId;
  return {
   source_id:source.id,
   publication:recPub,
   reviewed_video_version_id:recVid,
   require_current_serving:!isHist,
   ...(isHist&&recVid===cutState.versionId&&cutState.allowHistorical?{allow_historical:true}:{})
  };
 }
 function render(){
  const box=$('#notes-list')||$('#cut-report-list');if(!box)return;
  const cuts=!!$('#cut-report-list');
  let rows=cuts?items.filter(a=>a.kind==='cut_feedback'):MomentPlayback.inScope(items,scopeOptions()).map(e=>e.annotation);
  const type=$('#moment-type')?.value||'all';
  if(!cuts&&type!=='all')rows=rows.filter(a=>momentType(a)===type);
  const archived=$('#notes-archived')?.checked||false;rows=rows.filter(a=>archived||active(a));
  renderRows(box,rows,cuts);
 }
 function momentType(a){return a.kind==='coach'?'coaching':a.kind==='highlight'?'highlight':'comment';}
 function renderRows(box,rows,cuts=false){
  rows.sort((a,b)=>(a.target.start_seconds??-1)-(b.target.start_seconds??-1)||a.id.localeCompare(b.id));
  box.innerHTML=rows.length?rows.map(a=>{const text=MomentPlayback.displayText(a,true).trim();return `<article class="note-item${a.status==='archived'?' archived':''}" data-note="${esc(a.id)}"><div class="note-item-heading"><strong>${esc(a.body?.response?'Coach review':a.record_class==='entity_metadata'?'Note':names[a.kind]||a.kind)}${a.status==='archived'?' · Archived':''}</strong><span>${esc(targetLabel(a))}</span></div>${cutDetailsHTML(a)}${text?(a.body?.response?`<details class="note-response"><summary>Read saved response</summary><p>${esc(text)}</p></details>`:`<p class="note-text">${esc(text)}</p>`):''}${relationLabel(a)?`<p class="note-relation">${esc(relationLabel(a))}</p>`:''}<div class="note-item-actions">${itemActions(a,cuts)}</div><div class="note-history" hidden></div></article>`;}).join(''):'<p class="note-empty">'+(cuts?'No cut reviews yet.':box.id==='entity-notes-list'?'No notes.':'No moments.')+'</p>';
  $$('[data-note-action]',box).forEach(b=>b.onclick=async()=>{
   const card=b.closest('[data-note]'),a=items.find(a=>a.id===card.dataset.note);if(b.closest('.note-actions-menu'))b.closest('.note-actions-menu').open=false;b.disabled=true;
   try{
    if(b.dataset.noteAction==='select-version')await selectCutVersion(cutItemVersion(a));
    if(b.dataset.noteAction==='jump')jump(a);
    if(b.dataset.noteAction==='edit')openEditor(a.kind,a);
    if(b.dataset.noteAction==='archive'){if(box.id==='entity-notes-list')editNoteDirty=false;await update(a,{status:a.status==='active'?'archived':'active'},cuts?cutContextForRecord(a):{publication:source.published_at});await refresh(true);toast(a.status==='active'?'Archived.':'Restored.');}
    if(b.dataset.noteAction==='history'){const h=$('.note-history',card);let after=0,versions=[];do{const r=await api('/api/annotations/'+a.id+'/revisions'+(after?'?after='+after:''));versions.push(...r.revisions);after=r.next_after;}while(after);h.textContent=versions.map(r=>'v'+r.version+' · '+(r.status||'')+(r.body?.verdict?' · '+(verdicts[r.body.verdict]||r.body.verdict):'')+(r.target?.scope==='interval'&&r.kind==='cut_feedback'?' · '+preciseTime(r.target.start_seconds)+'–'+preciseTime(r.target.end_seconds):'')+' · '+(r.body?.text||r.content?.cue||'')).join('\n');h.hidden=!h.hidden;}
    if(b.dataset.noteAction==='coach'){
     const scopedFrag=MomentPlayback.inScope([a],scopeOptions())[0]?.fragments[0];
     const fallbackFrag=(a.visible_intervals||[])[0];
     const fragment=scopedFrag||fallbackFrag;
     const cur=currentClip();
     const tStart=a.target?.start_seconds,tEnd=a.target?.end_seconds;
     const overlapsCur=cur&&Number.isFinite(tStart)&&(Number.isFinite(tEnd)&&tEnd>tStart?Math.max(cur.original_start_seconds,tStart)<Math.min(cur.original_end_seconds,tEnd):(cur.original_start_seconds<=tStart&&tStart<cur.original_end_seconds));
     const clipId=model.mode==='clip'?(scopedFrag?.clip_id||(overlapsCur?cur.id:fallbackFrag?.clip_id)):fragment?.clip_id;
     const clip=clipId?source.clips.find(c=>c.id===clipId):null;
     if(!clip)throw new Error('No current clip contains this suggestion. Its history is retained in the export.');
     const annotationId=a.id.startsWith('coach:')?a.id.slice(6):(a.origin&&!a.body?.response?a.origin.reference:null);
     const jobId=a.origin?.job_id||(a.id.startsWith('coach-response:')?a.id.slice(15):null);
     const startSeconds=fragment?.original_start??a.target?.start_seconds;
     const endSeconds=fragment?.original_end??a.target?.end_seconds??startSeconds;
     playClip(clip.id,false);
     window.Coach?.focusTarget?.({clipId:clip.id,jobId,annotationId,startSeconds,endSeconds});
     $('#study-clip').click();
    }
   }catch(e){toast(e.message);if(e.status===409)await refresh(true);}finally{b.disabled=false;}
  });
 }
 function isMetadataLoadedFor(target=entityNotesTarget){
  return Boolean(target&&itemsLoaded&&itemsLoadedSourceId===target.source_id&&itemsLoadedPub===target.publication);
 }
 function targetEntityNotesFor(target=entityNotesTarget,includeArchived=false){
  if(!target||!source||!isMetadataLoadedFor(target))return [];
  return items.filter(a=>a.record_class==='entity_metadata'&&a.kind==='note'&&a.relation!=='historical_run'&&(target.clip_id?a.target.scope==='clip'&&a.target.clip_id===target.clip_id:a.target.scope==='video')&&(includeArchived||active(a)));
 }
 function targetEntityNotes(includeArchived=false){
  return targetEntityNotesFor(entityNotesTarget,includeArchived);
 }
 function syncEditNoteFromItems(preserveDraft=false){
  const field=$('#edit-note');if(!field||!entityNotesTarget)return;
  const loaded=isMetadataLoadedFor(entityNotesTarget);
  const activeNotes=targetEntityNotes(false);
  if(loaded&&activeNotes.length===1){
   const a=activeNotes[0],text=(a.body?.text||'').trim();
   editNoteRecord={id:a.id,version:a.version,initialText:text,mode:'single',player_id:a.player_id||null,unloaded:false};
   field.placeholder='';
   if(!preserveDraft||!editNoteDirty)field.value=a.body?.text||'';
  }else if(!loaded||activeNotes.length===0){
   editNoteRecord={id:null,version:0,initialText:'',mode:'new',player_id:null,unloaded:!loaded};
   field.placeholder='Add a note…';
   if(!preserveDraft||!editNoteDirty)field.value='';
  }else{
   editNoteRecord={id:null,version:0,initialText:'',mode:'additional',player_id:null,unloaded:false};
   field.placeholder='Add another note…';
   if(!preserveDraft||!editNoteDirty)field.value='';
  }
 }
 function renderEntityNotes(){
  const box=$('#entity-notes-list');if(!box||!entityNotesTarget||!source)return;
  const target=entityNotesTarget;
  if(target.source_id!==source.id||target.publication!==source.published_at){box.textContent='This video changed. Close the editor and refresh.';return;}
  if(!isMetadataLoadedFor(target)){
   box.innerHTML=loadError?`<p class="note-empty error-message">Notes unavailable. ${esc(loadError)}</p>`:'<p class="note-empty">Loading notes…</p>';
   return;
  }
  if(!editNoteDirty||editNoteRecord.unloaded)syncEditNoteFromItems(true);
  const rows=targetEntityNotes($('#entity-notes-archived')?.checked);
  renderRows(box,rows);
 }
 function openEntityNotes(target=null){
  const sourceId=target?.video_id||target?.source_id||source?.id;
  const publication=target?.publication??source?.published_at;
  const clipId=target?target.clip_id:(model.mode==='clip'?model.current:null);
  entityNotesGen++;
  entityNotesTarget={source_id:sourceId,publication,clip_id:clipId||null,gen:entityNotesGen};
  editNoteDirty=false;editNoteSaveRequest=requestId();editNoteFingerprint=null;
  if($('#entity-notes-archived'))$('#entity-notes-archived').checked=false;
  if($('#edit-load-latest-note'))$('#edit-load-latest-note').hidden=true;
  syncEditNoteFromItems(false);renderEntityNotes();
  loadPromise=refresh(true);
 }
 function captureEditNote(target=null){
  const field=$('#edit-note');
  if(!field||!entityNotesTarget)return {changed:false};
  const context={...entityNotesTarget};
  if(target&&(context.source_id!==target.video_id||context.clip_id!==(target.clip_id||null)||context.publication!==target.publication)){
   return {changed:false,mismatch:true};
  }
  const text=field.value.trim();
  const record={...editNoteRecord};
  const changed=record.unloaded?Boolean(text):text!==record.initialText;
  const selected=context.clip_id?source?.clips.find(c=>c.id===context.clip_id):null;
  const clipTargetData=context.clip_id?(selected?clipTarget(selected):null):{scope:'video'};
  const player_id=selected?.main_player?.id||null;
  const fingerprint=JSON.stringify([context.source_id,context.publication,context.clip_id,record.id,record.version,text,clipTargetData,player_id]);
  if(fingerprint!==editNoteFingerprint){editNoteFingerprint=fingerprint;editNoteSaveRequest=requestId();}
  return {changed,text,context,record,gen:context.gen,clipTargetData,player_id,saveRequest:editNoteSaveRequest};
 }
 function hasEditNoteChange(){
  return captureEditNote().changed;
 }
 function loadLatestEditNote(){
  editNoteDirty=false;
  syncEditNoteFromItems(false);
  if($('#edit-load-latest-note'))$('#edit-load-latest-note').hidden=true;
  if($('#edit-error'))$('#edit-error').textContent='';
 }
 async function saveEditNote(snapshot=captureEditNote()){
  if(!snapshot||snapshot.mismatch)throw new Error('This clip changed. Close the editor and refresh before saving.');
  if(!snapshot.changed)return;
  const context=snapshot.context;
  if(!source||source.id!==context.source_id||source.published_at!==context.publication)throw new Error('This video changed. Close the editor and refresh before saving.');
  if(!isMetadataLoadedFor(context)){
   if(loadPromise)await loadPromise;
   if(!isMetadataLoadedFor(context)){loadPromise=refresh(true);await loadPromise;}
   if(!source||source.id!==context.source_id||source.published_at!==context.publication)throw new Error('This video changed. Close the editor and refresh before saving.');
   if(!isMetadataLoadedFor(context))throw new Error('Notes unavailable. '+(loadError||'Try saving again once notes load.'));
  }
  let record=snapshot.record;
  if(record.unloaded){
   const activeNotes=targetEntityNotesFor(context,false);
   if(activeNotes.length>0){
    if(entityNotesGen===snapshot.gen){
     syncEditNoteFromItems(true);
     if($('#edit-load-latest-note'))$('#edit-load-latest-note').hidden=false;
    }
    throw new Error('Existing notes loaded. Load the latest saved note before saving.');
   }
   record={id:null,version:0,initialText:'',mode:'new',player_id:null,unloaded:false};
   if(entityNotesGen===snapshot.gen)syncEditNoteFromItems(true);
  }
  const text=snapshot.text;
  if(text===record.initialText)return;
  try{
   if(record.mode==='single'&&record.id){
    const currentRecord={id:record.id,version:record.version,kind:'note',record_class:'entity_metadata'};
    if(!text){
     await update(currentRecord,{status:'archived'},context);
    }else{
     await update(currentRecord,{body:{text},player_id:record.player_id},context);
    }
   }else if(text){
    if(context.clip_id&&!snapshot.clipTargetData)throw new Error('Choose a current clip for this target');
    await create('note',snapshot.clipTargetData,{text},snapshot.saveRequest,snapshot.player_id,context);
   }
   if(entityNotesGen===snapshot.gen){
    editNoteDirty=false;
    if($('#edit-load-latest-note'))$('#edit-load-latest-note').hidden=true;
   }
   await refresh(true);
  }catch(e){
   if(e.status===409){await refresh(true);if(entityNotesGen===snapshot.gen&&$('#edit-load-latest-note'))$('#edit-load-latest-note').hidden=false;}
   throw e;
  }
 }
 function editorScope(){
  const scope=$('#note-scope').value,timed=scope==='point'||scope==='interval';
  const isGapCutEdit=editingKind==='cut_feedback'&&editing&&editing.target?.scope==='interval';
  $('#note-start-row').hidden=!timed;$('#note-end-row').hidden=scope!=='interval';
  $('#note-clip-row').hidden=scope!=='clip'||editingKind==='note';
  const enabled=!editing||$('#note-retarget').checked||isGapCutEdit;
  $('#note-target-fields').disabled=!enabled;
  $('#note-scope').disabled=Boolean(editing&&!$('#note-retarget').checked);
  $('#note-target-clip').disabled=Boolean(editing&&!$('#note-retarget').checked);
  $('#note-start').disabled=!enabled||!timed;$('#note-start').required=timed;
  $('#note-end').disabled=!enabled||scope!=='interval';$('#note-end').required=scope==='interval';
  if($('#note-suggested-bounds-row'))$('#note-suggested-bounds-row').hidden=editingKind!=='cut_feedback'||scope!=='clip';
 }
 function openEditor(kind='note',a=null){
  editing=a;editingKind=kind;
  if(kind==='cut_feedback'&&a&&cutState.active&&!sameCutVersion(a))throw new Error('Open the reviewed version first.');
  const activeManifest=kind==='cut_feedback'&&a&&cutState.active?cutRecordManifest(a):(cutState.active&&cutState.manifest?cutState.manifest:source);
  const activeClips=activeManifest?.clips||source?.clips||[];
  editorContext=kind==='cut_feedback'&&cutState.active?{...cutContextForRecord(a),mode:'clip',clip_id:a?.target?.clip_id||cutState.clipId||model.current}:{source_id:source.id,publication:source.published_at,mode:kind==='cut_feedback'?'clip':model.mode,clip_id:model.mode==='clip'||kind==='cut_feedback'?model.current:null};
  if(kind==='note'&&a?.record_class!=='moment'&&entityNotesTarget)editorContext={...entityNotesTarget,mode:entityNotesTarget.clip_id?'clip':'full'};saveRequest=requestId();saveFingerprint=null;$('#note-editor-title').textContent=(a?'Edit ':'Add ')+(kind==='cut_feedback'?'cut review':kind==='note'&&a?.record_class!=='moment'?'note':kind==='highlight'?'highlight':'moment');
  const timed=kind==='highlight'||kind==='comment'||a?.record_class==='moment';
  const allowed=timed?['point','interval']:kind==='note'?[editorContext.clip_id?'clip':'video']:kind==='cut_feedback'&&a?.target?.scope==='interval'?['interval']:['clip'];
  $('#note-scope').innerHTML=allowed.map(s=>`<option value="${s}">${({point:'Moment',interval:kind==='cut_feedback'?'Removed gap':'Moment with end',clip:'Current clip',video:'Whole video'})[s]}</option>`).join('');
  $('#note-target-clip').innerHTML=activeClips.map(c=>`<option value="${c.id}">Clip ${c.index} · Original ${preciseTime(c.original_start_seconds)}–${preciseTime(c.original_end_seconds)}</option>`).join('');
  let at=msMin(currentClip()?.original_start_seconds||0);try{at=pointAtPlayer();}catch(_){}
  const t=a?.target||{scope:timed?'point':allowed[0],start_seconds:at};$('#note-scope').value=t.scope;
  const maxDur=activeManifest?.source?.duration_seconds??source.source.duration_seconds;
  const start=msRound(t.start_seconds??0),end=msRound(t.end_seconds??Math.min(msMaxEnd(maxDur),start+1));
  $('#note-start').value=start;$('#note-end').value=end;
  $('#note-scope-row').hidden=true;$('#note-optional-end-row').hidden=!timed;$('#note-optional-end').checked=t.scope==='interval';$('#note-optional-end').disabled=!!a;$('#note-start').required=timed;
  if(!a&&kind==='highlight')try{$('#note-start').value=pointAtPlayer();}catch(_){}
  $('#note-target-clip').value=t.clip_id||editorContext.clip_id||activeClips[0]?.id||'';
  $('#note-save').textContent=kind==='cut_feedback'?'Save cut review':timed?'Save moment':'Save note';$('#note-text-label').textContent=kind==='cut_feedback'?'Comment':'Note';$('#note-text').value=a?.body.text||'';$('#note-text').required=kind==='note'&&!timed;
  $('#note-reason-row').hidden=kind!=='usefulness';$('#note-usefulness-reason').value=a?.body.reason||'';
  if($('#note-verdict-row')){$('#note-verdict-row').hidden=kind!=='cut_feedback';$('#note-verdict').value=a?.body?.verdict||'';}
  if($('#note-suggested-start'))$('#note-suggested-start').value=a?.body?.suggested_start_ms!=null?msRound(a.body.suggested_start_ms/1000):'';
  if($('#note-suggested-end'))$('#note-suggested-end').value=a?.body?.suggested_end_ms!=null?msRound(a.body.suggested_end_ms/1000):'';
  $('#note-cut-reasons').hidden=kind!=='cut_feedback';$$('#note-cut-reasons input').forEach(e=>e.checked=(a?.body.reasons||[]).includes(e.value));
  $('#note-retarget-row').hidden=!a||!timed;$('#note-retarget').checked=false;
  $('#note-editor-context').textContent=a?targetLabel(a)+(relationLabel(a)?' · '+relationLabel(a):''):'';
  const targetClip=editorContext.clip_id?activeClips.find(c=>c.id===editorContext.clip_id):null;
  $('#note-player').innerHTML='<option value="">No player specified</option>'+model.players.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('');$('#note-player').value=a?.player_id||(editorContext.clip_id?targetClip?.main_player?.id:currentClip()?.main_player?.id&&timed?currentClip()?.main_player?.id:'')||'';
  $('#note-editor-error').textContent='';$('#note-load-latest').hidden=true;editorScope();if($('#edit-dialog')?.open)$('#edit-dialog').inert=true;$('#note-editor').showModal();
 }
 function init(){
  const node=document.createElement('div');node.innerHTML=`<dialog id="note-editor" aria-labelledby="note-editor-title"><form id="note-form" novalidate><div class="dialog-heading"><h2 id="note-editor-title">Add note</h2><button type="button" class="icon-button" id="note-close" aria-label="Close note editor">×</button></div><p id="note-editor-context"></p><label id="note-retarget-row" class="check-label"><input type="checkbox" id="note-retarget">Change timing or target</label><fieldset id="note-target-fields"><label id="note-scope-row">Applies to<select id="note-scope" aria-label="Note target"></select></label><label id="note-clip-row">Clip<select id="note-target-clip" aria-label="Note clip"></select></label><label id="note-start-row">Original start (seconds)<input type="number" id="note-start" min="0" step="0.001"></label><label class="check-label" id="note-optional-end-row"><input type="checkbox" id="note-optional-end">End time</label><label id="note-end-row">Original end (seconds)<input type="number" id="note-end" min="0" step="0.001"></label></fieldset><label id="note-verdict-row" hidden>Verdict<select id="note-verdict" aria-label="Cut verdict"><option value="">Unspecified</option><option value="good_cut">Good cut</option><option value="needs_adjustment">Needs adjustment</option><option value="poor_cut">Poor cut</option><option value="missed_play">Missed play</option></select></label><div id="note-suggested-bounds-row" class="cut-bounds-grid" hidden><label>Suggested start (seconds)<input type="number" id="note-suggested-start" min="0" step="0.001" placeholder="Keep current"></label><label>Suggested end (seconds)<input type="number" id="note-suggested-end" min="0" step="0.001" placeholder="Keep current"></label></div><label><span id="note-text-label">Note</span><textarea id="note-text" rows="3" maxlength="2000"></textarea></label><label id="note-reason-row">Reason<select id="note-usefulness-reason" aria-label="Usefulness reason"><option value="">Not specified</option><option value="low_information">Low information</option><option value="repetitive">Repetitive</option><option value="poor_view">Poor view</option><option value="other">Other</option></select></label><fieldset id="note-cut-reasons"><legend>Cut issues</legend>${Object.entries(reasons).map(([k,v])=>`<label class="check-label"><input type="checkbox" value="${k}">${v}</label>`).join('')}</fieldset><label>Player (optional)<select id="note-player" aria-label="Note player"></select></label><p id="note-editor-error" class="error-message" role="alert"></p><button type="button" class="button" id="note-load-latest" hidden>Load latest saved version</button><button type="submit" class="button primary" id="note-save">Save note</button></form></dialog>`;document.body.append(node);
  $('#note-editor').addEventListener('close',()=>{if($('#edit-dialog'))$('#edit-dialog').inert=false;});
  if($('#entity-note-add'))$('#entity-note-add').onclick=()=>openEditor('note');
  if($('#entity-notes-archived'))$('#entity-notes-archived').onchange=renderEntityNotes;
  if($('#edit-note'))$('#edit-note').addEventListener('input',()=>{editNoteDirty=true;});
  if($('#edit-load-latest-note'))$('#edit-load-latest-note').onclick=loadLatestEditNote;
  $('#note-load-latest').onclick=()=>{const latest=items.find(a=>a.id===editing?.id);if(latest)openEditor(latest.kind,latest);else location.reload();};
  $('#note-close').onclick=()=>$('#note-editor').close();$('#note-scope').onchange=editorScope;$('#note-retarget').onchange=()=>{editorScope();$('#note-optional-end').disabled=!$('#note-retarget').checked;};$('#note-optional-end').onchange=()=>{if($('#note-optional-end').checked){const s=msRound($('#note-start').value),e=Number($('#note-end').value);if(!Number.isFinite(e)||e<=s)$('#note-end').value=msRound(Math.min(msMaxEnd(source.source.duration_seconds),(Number.isFinite(s)?s:0)+1));else $('#note-end').value=msRound(e);}$('#note-scope').value=$('#note-optional-end').checked?'interval':'point';editorScope();};
  $('#note-form').onsubmit=async e=>{
   e.preventDefault();if(saving)return;saving=true;$('#note-save').disabled=true;
   try{
    const context=editorContext;
    if(source.id!==context.source_id||(editingKind!=='cut_feedback'&&source.published_at!==context.publication))throw new Error('This video changed. Close the editor and refresh before saving.');
    const body={text:$('#note-text').value.trim()};if(editingKind==='usefulness')Object.assign(body,{value:'less_useful',reason:$('#note-usefulness-reason').value});
    const parseSec=el=>{const raw=el.value.trim();return raw===''?NaN:msRound(Number(raw));};
    if(editingKind==='cut_feedback'){
     const vVal=$('#note-verdict')?.value||'';
     if(vVal)body.verdict=vVal;
     body.reasons=$$('#note-cut-reasons input:checked').map(e=>e.value);
     if($('#note-scope').value==='clip'){
      const sStartRaw=$('#note-suggested-start')?.value.trim()||'';
      const sEndRaw=$('#note-suggested-end')?.value.trim()||'';
      if(sStartRaw!==''){const n=Number(sStartRaw);if(!Number.isFinite(n)||n<0)throw new Error('Suggested start must be a non-negative number of seconds.');body.suggested_start_ms=Math.round(n*1000);}
      if(sEndRaw!==''){const n=Number(sEndRaw);if(!Number.isFinite(n)||n<=0)throw new Error('Suggested end must be a positive number of seconds.');body.suggested_end_ms=Math.round(n*1000);}
     }
    }
    const activeManifest=editingKind==='cut_feedback'&&cutState.active?cutState.versions.find(v=>v.id===context.reviewed_video_version_id)?.manifest:source;
    if(!activeManifest)throw new Error('Reviewed version is unavailable.');
    const activeClips=activeManifest?.clips||source?.clips||[];
    const isGapCutEdit=editingKind==='cut_feedback'&&editing&&editing.target?.scope==='interval';
    let target;if(!editing||$('#note-retarget').checked||isGapCutEdit){const scope=$('#note-scope').value;target={scope};if(scope==='clip'){const selected=activeClips.find(c=>c.id===(editingKind==='note'?context.clip_id:$('#note-target-clip').value));if(!selected)throw new Error('Choose a current clip for this target');target=clipTarget(selected);}else if(scope!=='video'){target.start_seconds=parseSec($('#note-start'));if(scope==='interval')target.end_seconds=parseSec($('#note-end'));}}
    const timing=target||editing?.target;
    const timed=editingKind!=='cut_feedback'&&timing&&['point','interval'].includes(timing.scope);
    if(editingKind==='note'&&!timed&&!body.text)throw new Error('Enter a note.');
    if(editingKind==='note'&&!timed&&!editing&&!isMetadataLoadedFor(context)){
     if(loadPromise)await loadPromise;
     if(!isMetadataLoadedFor(context)){loadPromise=refresh(true);await loadPromise;}
     if(!isMetadataLoadedFor(context))throw new Error('Notes unavailable. '+(loadError||'Try saving again once notes load.'));
    }
    if(timed){
     const lo=timing.start_seconds,hi=timing.end_seconds;
     if(!Number.isFinite(lo)||lo<0||lo>=source.source.duration_seconds||hi!==undefined&&(!Number.isFinite(hi)||hi<=lo||hi>source.source.duration_seconds))throw new Error('Choose a start within the recording and an optional later end.');
     const clips=context.mode==='clip'?source.clips.filter(c=>c.id===context.clip_id):source.clips;
     if(!clips.some(c=>{const a=c.original_start_seconds,b=c.original_end_seconds;return hi===undefined?lo>=a&&lo<b:lo<b&&hi>a;}))throw new Error('The moment must overlap the clip or video you were editing.');
    }
    const player_id=$('#note-player').value||null;
    const fingerprint=JSON.stringify([body,target,player_id]);if(fingerprint!==saveFingerprint){saveFingerprint=fingerprint;saveRequest=requestId();}
    if(editing)await update(editing,{body,player_id,...(target?{target}:{})},context);
    else await create(editingKind,target,body,saveRequest,player_id,context);
    if(editingKind==='note'&&!timed)editNoteDirty=false;
    await refresh(true);$('#note-editor').close();
    if(editingKind==='comment'||editing?.record_class==='moment'){
     const savedType=editing?momentType(editing):editingKind==='highlight'?'highlight':'comment';
     if($('#moment-type')&&$('#moment-type').value!=='all'&&$('#moment-type').value!==savedType)$('#moment-type').value='all';
     setExpanded(true);
    }
    toast('Saved.');
   }catch(e){$('#note-editor-error').textContent=e.message;if(e.status===409){await refresh(true);$('#note-load-latest').hidden=false;}}finally{saving=false;$('#note-save').disabled=false;}
  };
 }
 function setExpanded(open){
  const toggle=$('#moment-toggle');if(!toggle)return;toggle.setAttribute('aria-expanded',String(open));$('#moment-panel-content').hidden=!open;
  $('#notes-panel')?.classList.toggle('expanded',open);
  if(open)render();
 }
 function mount(s){
  releaseCutPreviewLease(cutState);cutState.active=false;
  source=s;items=[];itemsLoaded=false;itemsLoadedSourceId=null;itemsLoadedPub=null;loadError='';markerKey='';lastCurrent=null;generation++;clearInterval(timer);clearTimeout(cueTimer);cueClock.reset();mediaKey='';cueKey='';
  const markers=document.createElement('div');markers.id='note-markers';markers.className='note-markers';markers.setAttribute('aria-label','Moments on playback timeline');$('#video-seek').after(markers);
  const player=$('#player');player.addEventListener('loadedmetadata',()=>current());player.addEventListener('timeupdate',()=>current());
  player.addEventListener('seeked',()=>cueTick(true));player.addEventListener('play',()=>cueTick());player.addEventListener('pause',()=>cueTick());player.addEventListener('ended',()=>cueTick());
  player.addEventListener('emptied',()=>{clearTimeout(cueTimer);cueClock.reset();cueKey='';$('#moment-cues')?.replaceChildren();});
  window.addEventListener('coach-notes-changed',refresh);document.addEventListener('visibilitychange',()=>cueTick(true));
  $('.player-shell').insertAdjacentHTML('afterend',`<section id="moments-area" aria-label="Moment panel"><div id="notes-panel" class="notes-panel"><div class="moment-bar"><button type="button" id="moment-toggle" aria-expanded="false" aria-controls="moment-panel-content">Moments <span aria-hidden="true">⌄</span></button><button type="button" class="button small" id="moment-jump" aria-label="Jump to next moment" hidden>Jump</button><button type="button" class="button small" id="note-highlight" aria-label="Highlight this moment">Highlight</button><button type="button" class="button small" id="add-moment" aria-label="Add moment">+ Moment</button></div><div id="moment-cues" class="moment-cues" role="status" aria-live="polite" aria-atomic="true"></div><div id="moment-panel-content" hidden><div class="notes-toolbar moment-filters"><select id="moment-type" aria-label="Moment type"><option value="all">All types</option><option value="highlight">Highlight</option><option value="coaching">Coaching</option><option value="comment">Moment</option></select><label class="check-label"><input id="notes-archived" type="checkbox">Show archived</label></div><p id="notes-status" role="status"></p><div id="notes-list"></div></div></div></section>`);
  $('#note-highlight').onclick=highlight;$('#note-demote').onclick=demote;$('#moment-jump').onclick=nextMoment;$('#add-moment').onclick=()=>openEditor('comment');
  $('#moment-toggle').onclick=()=>setExpanded($('#moment-toggle').getAttribute('aria-expanded')!=='true');
  $('#notes-panel').addEventListener('click',e=>{
   if(e.target.closest('button,select,input,label,a,summary,details,#moment-panel-content'))return;
   if(e.target.closest('#moment-cues,.moment-cue')||$('#moment-toggle')?.getAttribute('aria-expanded')!=='true')setExpanded(true);
  });
  $('#moment-type').onchange=render;$('#notes-archived').onchange=render;
  const more=$('.player-more');more.classList.remove('hidden');$('.player-more-list').insertAdjacentHTML('beforeend',`<a class="note-cut-link" href="/review/${s.id}/cuts">Review cut</a>`);
  const toolbar=$('.clip-toolbar .toolbar');toolbar.insertAdjacentHTML('beforeend','<select id="note-clip-filter" aria-label="Clip focus"><option value="all">All clips</option><option value="recommended">Recommended</option><option value="liked">Liked only</option><option value="highlights">Highlights</option></select>');$('#note-clip-filter').value=model.noteFilter||'all';$('#note-clip-filter').onchange=e=>{model.noteFilter=e.target.value;renderClipList();};
  loadPromise=refresh(true);timer=setInterval(refresh,10000);
 }
 function renderCutVersionSelect(){
  const sel=$('#cut-version');if(!sel)return;
  sel.innerHTML=cutState.versions.map(v=>{
   const label=`${v.is_current_serving?'Current serving':'Historical'} · ${v.id.slice(0,10)} (${v.clip_count} ${v.clip_count===1?'clip':'clips'})`;
   return `<option value="${esc(v.id)}">${esc(label)}</option>`;
  }).join('');
  if(cutState.versionId)sel.value=cutState.versionId;
 }
 function updateCutVersionBanner(){
  const banner=$('#cut-version-banner');if(!banner)return;
  const isHist=cutState.versionId&&cutState.currentVersionId&&cutState.versionId!==cutState.currentVersionId;
  if(!isHist){
   banner.hidden=true;banner.replaceChildren();
   updateCutFormLock();
   return;
  }
  banner.hidden=false;ensureHistoricalCutLease(cutState);
  const staleNotice=cutState.staleServingVersionId&&cutState.staleServingVersionId===cutState.currentVersionId?'Recording was reprocessed to a newer serving version. ':'';
  if(cutState.allowHistorical){
   banner.innerHTML=`<div class="cut-banner-copy"><strong>Scoped to historical version ${esc(cutState.versionId.slice(0,10))}</strong><span>${esc(staleNotice)}Reviews will be recorded against this historical version.</span></div><div class="cut-banner-actions"><button type="button" class="button small" id="cut-switch-serving">Switch to current serving version</button></div>`;
  }else{
   banner.innerHTML=`<div class="cut-banner-copy"><strong>Historical version ${esc(cutState.versionId.slice(0,10))} (read-only)</strong><span>${esc(staleNotice)}Switch to the current serving version or explicitly scope your review to this historical version.</span></div><div class="cut-banner-actions"><button type="button" class="button small primary" id="cut-scope-historical">Scope reviews to this version</button><button type="button" class="button small" id="cut-switch-serving">Switch to current serving version</button></div>`;
  }
  const scopeBtn=$('#cut-scope-historical');
  if(scopeBtn)scopeBtn.onclick=()=>{cutState.allowHistorical=true;if($('#cut-form-error'))$('#cut-form-error').textContent='';if($('#cut-conflict-actions'))$('#cut-conflict-actions').hidden=true;updateCutVersionBanner();render();};
  const switchBtn=$('#cut-switch-serving');
  if(switchBtn)switchBtn.onclick=()=>selectCutVersion(cutState.currentVersionId,true);
  updateCutFormLock();
 }
 function updateCutFormLock(){
  const readOnly=Boolean(cutState.versionId&&cutState.currentVersionId&&cutState.versionId!==cutState.currentVersionId&&!cutState.allowHistorical);
  $$('#cut-review-form input,#cut-review-form select,#cut-review-form textarea,#cut-save').forEach(el=>{el.disabled=saving||readOnly;});
  if($('#cut-clear'))$('#cut-clear').disabled=saving;
  $$('#cut-conflict-actions button').forEach(el=>{el.disabled=saving;});
  for(const id of ['cut-version','cut-recording','cut-clip','cut-gap'])if($('#'+id))$('#'+id).disabled=saving;
  const gaps=cutState.timeline?.gaps||[],idx=gaps.findIndex(g=>g.id===cutState.gapId);
  if($('#cut-prev-gap'))$('#cut-prev-gap').disabled=saving||!gaps.length||(cutState.mode==='gap'&&idx<=0);
  if($('#cut-next-gap'))$('#cut-next-gap').disabled=saving||!gaps.length||(cutState.mode==='gap'&&idx>=gaps.length-1);
 }
 async function loadCutHints(){
  const owner=cutState,version=owner.versionId;
  if(!owner.active||owner.hintsVersion===version)return;
  owner.hintsVersion=version;owner.hints=[];owner.hintsMessage='Loading…';renderCutHints();
  try{
   let after='',all=[],page;
   do{
    page=await api(`/api/processing/videos/${source.id}/heuristics?version=${encodeURIComponent(version)}${after?'&after='+encodeURIComponent(after):''}`);
    if(cutState!==owner||owner.versionId!==version)return;
    all.push(...(page.items||[]));after=page.next_after;
   }while(after);
   owner.hints=all;owner.hintsMessage=page.available?'':page.reason;
  }catch(e){if(cutState!==owner||owner.versionId!==version)return;owner.hintsMessage='Diagnostics unavailable. You can still review the cut.';}
  renderCutHints();
 }
 function renderCutHints(){
  const list=$('#cut-hints');if(!list)return;
  const gap=cutState.timeline?.gaps.find(g=>g.id===cutState.gapId);
  const entry=(cutState.hints||[]).find(e=>cutState.mode==='clip'?e.target.scope==='clip'&&e.target.clip_id===cutState.clipId:e.target.scope==='gap'&&e.target.gap_index===gap?.gapIndex);
  if(cutState.hintsMessage){list.textContent=cutState.hintsMessage;return;}
  if(!entry){list.textContent='No diagnostics for this range.';return;}
  const status=entry.heuristic_status==='unknown'?'Insufficient observations':entry.heuristic_status==='no_detector_warnings'?'No movement warnings':entry.heuristic_status.replaceAll('_',' ');
  list.innerHTML=`<p class="cut-hint">${esc(status)} · Movement hints, not verified tennis play.</p>`+(entry.hints||[]).map((h,i)=>`<div class="cut-hint-row"><p>${esc(h.summary)}</p><button type="button" class="button small" data-cut-hint="${i}">Prefill review</button></div>`).join('');
  $$('[data-cut-hint]',list).forEach(button=>{button.disabled=saving||($('#cut-save')?.disabled??true);button.onclick=()=>{
   if(saving||$('#cut-save').disabled)return;
   if(cutState.draftDirty){$('#cut-form-error').textContent='Save or clear this review before prefilling a hint.';return;}
   const h=entry.hints[Number(button.dataset.cutHint)];if(!h)return;
   $$('#cut-form-reasons input').forEach(cb=>cb.checked=(h.mapped_reasons||[]).includes(cb.value));
   if(h.candidate_start_ms!=null)$('#cut-suggested-start').value=h.candidate_start_ms/1000;
   if(h.candidate_end_ms!=null)$('#cut-suggested-end').value=h.candidate_end_ms/1000;
   if(cutState.mode==='gap'){$('#cut-gap-start').value=h.original_start_ms/1000;$('#cut-gap-end').value=h.original_end_ms/1000;}
   cutState.heuristicRef=h.hint_id;cutState.draftDirty=true;$('#cut-comment').focus();
  };});
 }
 function renderCutTimeline(){
  const bar=$('#cut-timeline-bar'),summary=$('#cut-timeline-summary');
  if(!bar||!cutState.timeline)return;
  const tl=cutState.timeline;
  if(summary)summary.textContent=`Original ${time(tl.totalDurationMs/1000)} · ${tl.clips.length} kept ${tl.clips.length===1?'clip':'clips'} · ${tl.gaps.length} removed ${tl.gaps.length===1?'gap':'gaps'}`;
  const total=Math.max(1,tl.totalDurationMs);
  bar.innerHTML=tl.segments.map(seg=>{
   const pct=100*(seg.endMs-seg.startMs)/total;
   if(seg.kind==='clip'){
    const isSelected=cutState.mode==='clip'&&cutState.clipId===seg.clipId;
    const hasReview=items.some(a=>active(a)&&sameCutVersion(a)&&a.kind==='cut_feedback'&&a.target.scope==='clip'&&a.target.clip_id===seg.clipId);
    return `<button type="button" class="cut-seg cut-seg-clip${isSelected?' active':''}${hasReview?' reviewed':''}" data-seg-kind="kept_clip" data-clip-id="${esc(seg.clipId)}" data-start-percent="${100*seg.startMs/total}" data-width-percent="${pct}" aria-pressed="${isSelected}" aria-label="Kept clip ${seg.clipIndex}, Original ${preciseTime(seg.startSeconds)} to ${preciseTime(seg.endSeconds)}" title="Clip ${String(seg.clipIndex).padStart(2,'0')} · Original ${preciseTime(seg.startSeconds)}–${preciseTime(seg.endSeconds)}"><span>C${seg.clipIndex}</span></button>`;
   }
   const isSelected=cutState.mode==='gap'&&cutState.gapId===seg.id;
   const hasReview=items.some(a=>active(a)&&sameCutVersion(a)&&a.kind==='cut_feedback'&&a.target.scope==='interval'&&Math.round(a.target.start_seconds*1000)>=seg.startMs&&Math.round(a.target.end_seconds*1000)<=seg.endMs);
   return `<button type="button" class="cut-seg cut-seg-gap${isSelected?' active':''}${hasReview?' reviewed':''}" data-seg-kind="removed_gap" data-gap-id="${esc(seg.id)}" data-start-percent="${100*seg.startMs/total}" data-width-percent="${pct}" aria-pressed="${isSelected}" aria-label="Removed gap ${seg.gapIndex}, Original ${preciseTime(seg.startSeconds)} to ${preciseTime(seg.endSeconds)}" title="Removed gap ${seg.gapIndex} · Original ${preciseTime(seg.startSeconds)}–${preciseTime(seg.endSeconds)}"><span>Gap ${seg.gapIndex}</span></button>`;
  }).join('');
  $$('[data-seg-kind]',bar).forEach(b=>{
   b.style.left=Number(b.dataset.startPercent)+'%';b.style.width=Number(b.dataset.widthPercent)+'%';
   b.onclick=()=>{
    if(b.dataset.segKind==='kept_clip')selectCutClip(b.dataset.clipId);
    else selectCutGap(b.dataset.gapId);
   };
  });
 }
 function updateCutPlayheadReadout(){
  const out=$('#cut-original');if(!out||!cutState.timeline)return;
  if(cutState.mode==='gap'){
   const gap=cutState.timeline.gaps.find(g=>g.id===cutState.gapId);
   out.textContent=gap?`Removed gap ${gap.gapIndex} · Original ${preciseTime(gap.startSeconds)}–${preciseTime(gap.endSeconds)} (${preciseTime(gap.durationSeconds)})`:'Removed gap';
   return;
  }
  const clip=(cutState.manifest?.clips||[]).find(c=>c.id===cutState.clipId);
  if(!clip){out.textContent='No kept clips in this version';return;}
  const video=$('#cut-video');
  const localSec=video&&Number.isFinite(video.currentTime)?video.currentTime:0;
  const proj=CutReviewCore.projectLocalToOriginal(clip,localSec,{runtimeDurationSeconds:video?.duration});
  const playheadOrig=proj?proj.originalSeconds:clip.original_start_seconds;
  out.textContent=`Clip ${String(clip.index).padStart(2,'0')} · Original ${preciseTime(clip.original_start_seconds)}–${preciseTime(clip.original_end_seconds)} · Playhead ${preciseTime(playheadOrig)}`;
 }
 function currentPlayheadOriginalSeconds(){
  const clip=(cutState.manifest?.clips||[]).find(c=>c.id===cutState.clipId);
  if(!clip)return null;
  const video=$('#cut-video');
  const localSec=video&&Number.isFinite(video.currentTime)?video.currentTime:0;
  const proj=CutReviewCore.projectLocalToOriginal(clip,localSec,{runtimeDurationSeconds:video?.duration});
  return proj?msRound(proj.originalSeconds):msRound(clip.original_start_seconds);
 }
 function populateCutVerdictOptions(mode){
  const sel=$('#cut-verdict');if(!sel)return;
  const currentVal=sel.value;
  const opts=mode==='gap'
   ?[['missed_play','Missed play'],['poor_cut','Poor cut'],['needs_adjustment','Needs adjustment'],['','Unspecified']]
   :[['','Unspecified'],['good_cut','Good cut'],['needs_adjustment','Needs adjustment'],['poor_cut','Poor cut']];
  sel.innerHTML=opts.map(([k,v])=>`<option value="${k}">${v}</option>`).join('');
  if(opts.some(([k])=>k===currentVal))sel.value=currentVal;
  else sel.value=mode==='gap'?'missed_play':'';
 }
 function resetCutFormForTarget(){
  cutState.heuristicRef=null;
  if(cutState.draftDirty)return;
  populateCutVerdictOptions(cutState.mode);
  $$('#cut-form-reasons input').forEach(cb=>{cb.checked=cutState.mode==='gap'&&cb.value==='missed_play';});
  if($('#cut-suggested-start'))$('#cut-suggested-start').value='';
  if($('#cut-suggested-end'))$('#cut-suggested-end').value='';
  if(cutState.mode==='gap'){
   const gap=(cutState.timeline?.gaps||[]).find(g=>g.id===cutState.gapId);
   if(gap){
    if($('#cut-gap-start'))$('#cut-gap-start').value=msRound(gap.startSeconds);
    if($('#cut-gap-end'))$('#cut-gap-end').value=msRound(gap.endSeconds);
   }
  }
  if($('#cut-comment'))$('#cut-comment').value='';
  if($('#cut-form-error'))$('#cut-form-error').textContent='';
  if($('#cut-conflict-actions'))$('#cut-conflict-actions').hidden=true;
 }
 function updateCutStageAndForm(){
  const clipStage=$('#cut-clip-stage'),gapSlate=$('#cut-gap-slate');
  const boundsFields=$('#cut-bounds-fields'),gapFields=$('#cut-gap-fields');
  const targetHint=$('#cut-form-target');
  const isServing=cutState.versionId===cutState.currentVersionId;
  if(cutState.mode==='clip'){
   if(clipStage)clipStage.hidden=false;
   if(gapSlate)gapSlate.hidden=true;
   if(boundsFields)boundsFields.hidden=false;
   if(gapFields)gapFields.hidden=true;
   const clip=(cutState.manifest?.clips||[]).find(c=>c.id===cutState.clipId);
   const video=$('#cut-video'),histNote=$('#cut-historical-media-note');
   if(clip){
    model.current=clip.id;
    if($('#cut-clip'))$('#cut-clip').value=clip.id;
    if($('#cut-gap'))$('#cut-gap').value='';
    if(targetHint)targetHint.textContent=`Clip ${String(clip.index).padStart(2,'0')} · Original ${preciseTime(clip.original_start_seconds)}–${preciseTime(clip.original_end_seconds)}`;
    if(video){
     if(isServing){
      cutState.historicalMediaKey=null;video.hidden=false;
      const desired=mediaURL(source,clip.file);
      if(video.getAttribute('src')!==desired)video.src=desired;
      if(histNote)histNote.hidden=true;
     }else{
      const key=cutState.versionId+':'+clip.id;
      if(cutState.historicalMediaKey!==key){
       cutState.historicalMediaKey=key;video.pause();video.removeAttribute('src');video.load();video.hidden=true;
       if(histNote)histNote.hidden=false;
       loadHistoricalCutPreview(key,clip);
      }
     }
    }
   }else{
    if(video){video.pause();video.hidden=true;}
    if(targetHint)targetHint.textContent='No kept clips';
   }
  }else{
   cutState.historicalMediaKey=null;
   const video=$('#cut-video');if(video)video.pause();
   if(clipStage)clipStage.hidden=true;
   if(gapSlate)gapSlate.hidden=false;
   if(boundsFields)boundsFields.hidden=true;
   if(gapFields)gapFields.hidden=false;
   const gap=(cutState.timeline?.gaps||[]).find(g=>g.id===cutState.gapId);
   if($('#cut-gap')&&gap)$('#cut-gap').value=gap.id;
   if($('#cut-clip'))$('#cut-clip').value='';
   if(gap){
    if(targetHint)targetHint.textContent=`Removed gap ${gap.gapIndex} · Original ${preciseTime(gap.startSeconds)}–${preciseTime(gap.endSeconds)}`;
    const gapSummary=$('#cut-gap-summary');
    if(gapSummary)gapSummary.textContent=`Original ${preciseTime(gap.startSeconds)}–${preciseTime(gap.endSeconds)} (${preciseTime(gap.durationSeconds)} removed)`;
    const prevBtn=$('#cut-gap-prev-clip'),nextBtn=$('#cut-gap-next-clip');
    if(prevBtn){
     prevBtn.disabled=!gap.prevClipId;
     prevBtn.onclick=()=>{if(!gap.prevClipId)return;if(!selectCutClip(gap.prevClipId))return;const v=$('#cut-video'),c=(cutState.manifest?.clips||[]).find(x=>x.id===gap.prevClipId);if(v&&c&&!v.hidden){const expected=cutState,version=cutState.versionId;const seek=()=>{if(cutState!==expected||cutState.versionId!==version||cutState.mode!=='clip'||cutState.clipId!==gap.prevClipId)return;const d=CutReviewCore.effectiveClipDurationSeconds(c,v.duration);v.currentTime=Math.max(0,d-Math.min(1.5,d*0.5));v.pause();updateCutPlayheadReadout();};if(v.readyState>=1)seek();else v.addEventListener('loadedmetadata',seek,{once:true});}};
    }
    if(nextBtn){
     nextBtn.disabled=!gap.nextClipId;
     nextBtn.onclick=()=>{if(!gap.nextClipId)return;if(!selectCutClip(gap.nextClipId))return;const v=$('#cut-video');if(v&&!v.hidden){const expected=cutState,version=cutState.versionId;const seek=()=>{if(cutState!==expected||cutState.versionId!==version||cutState.mode!=='clip'||cutState.clipId!==gap.nextClipId)return;v.currentTime=0;v.pause();updateCutPlayheadReadout();};if(v.readyState>=1)seek();else v.addEventListener('loadedmetadata',seek,{once:true});}};
    }
   }
  }
  const gaps=cutState.timeline?.gaps||[];
  const gapIdx=gaps.findIndex(g=>g.id===cutState.gapId);
  if($('#cut-prev-gap'))$('#cut-prev-gap').disabled=!gaps.length||(cutState.mode==='gap'&&gapIdx<=0);
  if($('#cut-next-gap'))$('#cut-next-gap').disabled=!gaps.length||(cutState.mode==='gap'&&gapIdx>=gaps.length-1);
  updateCutPlayheadReadout();
  renderCutTimeline();
  updateCutFormLock();renderCutHints();
 }
 function releaseCutPreviewLease(owner){
  const ref=owner.previewLeaseRef;if(!ref)return;owner.previewLeaseRef=null;
  fetch(`/api/processing/videos/${ref.sourceId}/comparison-lease`,{method:'DELETE',headers:{'Content-Type':'application/json','X-Tennis-Request':'1'},body:JSON.stringify({video_version_id:ref.version,holder_id:ref.holder}),keepalive:true}).catch(()=>{});
 }
 async function ensureHistoricalCutLease(owner){
  if(!owner.active||owner.versionId===owner.currentVersionId)return null;
  if(owner.previewLeaseRef?.version!==owner.versionId){releaseCutPreviewLease(owner);owner.previewLeaseRef={sourceId:source.id,version:owner.versionId,holder:Array.from({length:32},()=>Math.floor(Math.random()*16).toString(16)).join(''),renewed:0,promise:null};}
  const ref=owner.previewLeaseRef;if(ref.promise)return ref.promise;
  if(Date.now()-ref.renewed<300000)return ref;
  ref.promise=(async()=>{
   try{
    const ack=await api(`/api/processing/videos/${ref.sourceId}/comparison-lease`,{method:'POST',body:JSON.stringify({video_version_id:ref.version,holder_id:ref.holder})});
    if(cutState!==owner||!owner.active||owner.previewLeaseRef!==ref){fetch(`/api/processing/videos/${ref.sourceId}/comparison-lease`,{method:'DELETE',headers:{'Content-Type':'application/json','X-Tennis-Request':'1'},body:JSON.stringify({video_version_id:ref.version,holder_id:ref.holder}),keepalive:true}).catch(()=>{});return null;}
    if(!ack.acknowledged)return null;ref.renewed=Date.now();return ref;
   }catch(e){return null;}
  })();try{return await ref.promise;}finally{ref.promise=null;}
 }
 async function loadHistoricalCutPreview(key,clip){
  const owner=cutState,version=owner.versionId,ref=await ensureHistoricalCutLease(owner);
  if(!ref||cutState!==owner||owner.versionId!==version||owner.historicalMediaKey!==key||owner.mode!=='clip'||owner.clipId!==clip.id)return;
  const video=$('#cut-video'),desired=`/media/versions/${version}/${clip.file}?lease_id=${ref.holder}`;
  if(video.getAttribute('src')!==desired)video.src=desired;
  video.hidden=false;$('#cut-historical-media-note').hidden=true;
 }
 function canChangeCutTarget(){
  if(!saving&&!cutState.draftDirty)return true;
  const error=$('#cut-form-error');if(error)error.textContent=saving?'Saving this review…':'Save or clear this review before switching.';
  if($('#cut-version'))$('#cut-version').value=cutState.versionId;
  if($('#cut-recording'))$('#cut-recording').value=source.id;
  if($('#cut-clip'))$('#cut-clip').value=cutState.mode==='clip'?cutState.clipId:'';
  if($('#cut-gap'))$('#cut-gap').value=cutState.mode==='gap'?cutState.gapId:'';
  return false;
 }
 function selectCutClip(clipId,forceReset=false){
  const clip=(cutState.manifest?.clips||[]).find(c=>c.id===clipId)||(cutState.manifest?.clips||[])[0];
  if(!clip)return;
  const changed=cutState.mode!=='clip'||cutState.clipId!==clip.id;
  if(changed&&!canChangeCutTarget())return false;
  cutState.mode='clip';cutState.clipId=clip.id;
  if(changed&&forceReset)cutState.draftDirty=false;
  if(changed)resetCutFormForTarget();
  updateCutStageAndForm();
  return true;
 }
 function selectCutGap(gapId,customInterval=null){
  const gap=(cutState.timeline?.gaps||[]).find(g=>g.id===gapId)||(cutState.timeline?.gaps||[])[0];
  if(!gap)return;
  const changed=cutState.mode!=='gap'||cutState.gapId!==gap.id;
  if(changed&&!canChangeCutTarget())return false;
  cutState.mode='gap';cutState.gapId=gap.id;
  if(changed)resetCutFormForTarget();
  if(customInterval&&!cutState.draftDirty){
   if($('#cut-gap-start'))$('#cut-gap-start').value=msRound(customInterval.startSeconds);
   if($('#cut-gap-end'))$('#cut-gap-end').value=msRound(customInterval.endSeconds);
  }
  updateCutStageAndForm();
 }
 async function selectCutVersion(versionId,resetDraft=false){
  const entry=cutState.versions.find(v=>v.id===versionId);
  if(!entry)return;
  if(entry.id===cutState.versionId)return true;
  if(entry.id!==cutState.versionId&&!canChangeCutTarget())return false;
  generation++;
  releaseCutPreviewLease(cutState);
  cutState.historicalMediaKey=null;
  cutState.versionId=entry.id;
  cutState.manifest=entry.manifest;
  cutState.timeline=CutReviewCore.buildTimeline(entry.manifest);
  cutState.allowHistorical=false;
  if(resetDraft)cutState.draftDirty=false;
  renderCutVersionSelect();
  const clipSel=$('#cut-clip'),gapSel=$('#cut-gap');
  if(clipSel){
   const clips=entry.manifest.clips||[];
   clipSel.innerHTML=clips.length?'<option value="">Select clip…</option>'+clips.map(c=>`<option value="${esc(c.id)}">Clip ${c.index} (${preciseTime(c.original_start_seconds)}–${preciseTime(c.original_end_seconds)})</option>`).join(''):'<option value="">No kept clips</option>';
  }
  if(gapSel){
   const gaps=cutState.timeline.gaps||[];
   gapSel.innerHTML='<option value="">Select removed gap…</option>'+gaps.map(g=>`<option value="${esc(g.id)}">Gap ${g.gapIndex} (${preciseTime(g.startSeconds)}–${preciseTime(g.endSeconds)})</option>`).join('');
  }
  if((entry.manifest.clips||[]).length){
   const keepClip=(entry.manifest.clips||[]).find(c=>c.id===cutState.clipId)||entry.manifest.clips[0];
   cutState.mode='clip';cutState.clipId=keepClip.id;cutState.gapId=cutState.timeline.gaps[0]?.id||null;
  }else if(cutState.timeline.gaps.length){
   cutState.mode='gap';cutState.gapId=cutState.timeline.gaps[0].id;cutState.clipId=null;
  }
  if(!cutState.draftDirty)resetCutFormForTarget();
  updateCutVersionBanner();
  updateCutStageAndForm();
  await refresh(true);
 }
 async function cuts(s){
  releaseCutPreviewLease(cutState);
  source=s;model.session=s;items=[];itemsLoaded=false;itemsLoadedSourceId=null;itemsLoadedPub=null;loadError='';generation++;clearInterval(timer);nav();
  const vData=await api(`/api/processing/videos/${s.id}/versions`);
  const query=new URLSearchParams(location.search);
  const reqVer=query.get('version');
  const initialVer=vData.versions.find(v=>v.id===reqVer)||vData.versions.find(v=>v.id===vData.current_version_id)||vData.versions[0];
  cutState={
   active:true,
   versions:vData.versions||[],
   currentVersionId:vData.current_version_id,
   currentPublication:vData.current_publication,
   staleServingVersionId:null,
   versionId:initialVer.id,
   manifest:initialVer.manifest,
   timeline:CutReviewCore.buildTimeline(initialVer.manifest),
   allowHistorical:false,
   allVersions:false,
   mode:'clip',
   clipId:initialVer.manifest.clips[0]?.id||null,
   gapId:null,
   draftDirty:false,
   saveRequest:requestId(),
   saveFingerprint:null
  };
  const leaseOwner=cutState;window.addEventListener('pagehide',()=>releaseCutPreviewLease(leaseOwner),{once:true});
  cutState.gapId=cutState.timeline.gaps[0]?.id||null;
  if(!cutState.clipId&&cutState.gapId)cutState.mode='gap';
  const reqClip=query.get('clip'),reqGap=query.get('gap');
  if(reqClip&&(initialVer.manifest.clips||[]).some(c=>c.id===reqClip)){cutState.mode='clip';cutState.clipId=reqClip;}
  else if(reqGap){
   const [gStart,gEnd]=reqGap.split('-').map(Number);
   const matchedGap=cutState.timeline.gaps.find(g=>Math.abs(g.startMs-gStart)<=5&&Math.abs(g.endMs-gEnd)<=5)||cutState.timeline.gaps[0];
   if(matchedGap){cutState.mode='gap';cutState.gapId=matchedGap.id;}
  }
  const portalUrl=$('#main')?.dataset.processingUrl||'/processing';
  const portalLink=portalUrl!=='/processing'?`<span>·</span><a href="${esc(portalUrl)}" id="cut-portal-handoff">Processing Server Portal ↗</a>`:'';
  const recOptions=(model.sessions.length?model.sessions:[s]).map(r=>`<option value="${esc(r.id)}"${r.id===s.id?' selected':''}>${esc(r.title)}</option>`).join('');
  $('#main').innerHTML=`<div class="review-breadcrumb"><a href="/review/${s.id}" id="cut-back-review">Back to review</a><span>/</span><a href="/processing">Processing Room</a>${portalLink}<span>/</span><span>Review cut</span><a class="button small" href="/review/${s.id}/compare">Compare versions</a></div>
  <header class="cut-header"><div><h1>Review cut</h1></div><div class="cut-context-selectors"><label>Recording<select id="cut-recording" aria-label="Recording">${recOptions}</select></label><label>Reviewed version<select id="cut-version" aria-label="Reviewed version"></select></label></div></header>
  <div id="cut-version-banner" class="cut-version-banner" role="status" hidden></div>
  <section class="cut-timeline-section" id="cut-timeline-section" aria-label="Original recording timeline"><div class="cut-timeline-header"><span id="cut-timeline-summary" class="cut-timeline-summary"></span><div class="cut-target-toolbar"><label>Clip<select id="cut-clip" aria-label="Review clip"></select></label><label>Removed gap<select id="cut-gap" aria-label="Removed gap"></select></label><div class="cut-gap-nav"><button type="button" class="button small" id="cut-prev-gap">Prev gap</button><button type="button" class="button small" id="cut-next-gap">Next gap</button></div></div></div><div id="cut-timeline-bar" class="cut-timeline-bar" role="group" aria-label="Original recording timeline"></div></section>
  <div class="cut-workspace">
   <section class="cut-stage-col" aria-label="Cut preview">
    <div id="cut-clip-stage">
     <video class="cut-review-player" id="cut-video" controls playsinline preload="metadata"></video>
     <p id="cut-historical-media-note" class="cut-hint" hidden>Video preview is unavailable for this historical version. Its cut boundaries are retained.</p>
     <div class="cut-transport-bar">
      <p id="cut-original" class="cut-time-readout"></p>
      <div class="cut-step-controls">
       <button type="button" class="button small" id="cut-step-back" aria-label="Step back 0.5 seconds">-0.5s</button>
       <button type="button" class="button small" id="cut-step-forward" aria-label="Step forward 0.5 seconds">+0.5s</button>
       <button type="button" class="button small" id="cut-set-start">Use playhead as start</button>
       <button type="button" class="button small" id="cut-set-end">Use playhead as end</button>
      </div>
     </div>
    </div>
    <div id="cut-gap-slate" class="cut-gap-slate" hidden>
     <div class="cut-gap-badge">Removed gap</div>
     <p id="cut-gap-summary" class="cut-gap-summary"></p>
     <p class="cut-hint">This range was removed from the processed video. Use the neighboring clips for context.</p>
     <div class="cut-gap-actions">
      <button type="button" class="button small" id="cut-gap-prev-clip">Play preceding clip end</button>
      <button type="button" class="button small" id="cut-gap-next-clip">Play following clip start</button>
     </div>
    </div>
   </section>
   <section class="cut-form-col notes-panel" aria-label="Review cut form">
    <form id="cut-review-form" novalidate>
     <div class="cut-form-header"><h2>Review cut</h2><span id="cut-form-target" class="cut-hint"></span></div>
     <label>Verdict<select id="cut-verdict" aria-label="Verdict"></select></label>
     <fieldset id="cut-form-reasons" class="cut-reasons-grid"><legend>Cut issues</legend>${Object.entries(reasons).map(([k,v])=>`<label class="check-label"><input type="checkbox" value="${k}">${v}</label>`).join('')}</fieldset>
     <div id="cut-bounds-fields" class="cut-bounds-grid">
      <label>Suggested start (s)<div class="cut-bound-row"><input type="number" id="cut-suggested-start" aria-label="Suggested start (seconds)" min="0" step="0.001" placeholder="Keep current"><button type="button" class="button small" id="cut-nudge-start-minus" aria-label="Nudge suggested start earlier">-0.5s</button><button type="button" class="button small" id="cut-nudge-start-plus" aria-label="Nudge suggested start later">+0.5s</button></div></label>
      <label>Suggested end (s)<div class="cut-bound-row"><input type="number" id="cut-suggested-end" aria-label="Suggested end (seconds)" min="0" step="0.001" placeholder="Keep current"><button type="button" class="button small" id="cut-nudge-end-minus" aria-label="Nudge suggested end earlier">-0.5s</button><button type="button" class="button small" id="cut-nudge-end-plus" aria-label="Nudge suggested end later">+0.5s</button></div></label>
     </div>
     <div id="cut-gap-fields" class="cut-bounds-grid" hidden>
      <label>Interval start (s)<input type="number" id="cut-gap-start" aria-label="Interval start (seconds)" min="0" step="0.001"></label>
      <label>Interval end (s)<input type="number" id="cut-gap-end" aria-label="Interval end (seconds)" min="0" step="0.001"></label>
     </div>
     <label>Comment<textarea id="cut-comment" aria-label="Comment" rows="2" maxlength="2000"></textarea></label>
     <p id="cut-form-error" class="error-message" role="alert"></p>
     <div id="cut-conflict-actions" class="cut-conflict-actions" hidden>
      <button type="button" class="button small" id="cut-conflict-scope-historical">Scope review to historical version</button>
      <button type="button" class="button small" id="cut-conflict-switch-serving">Switch to current serving version</button>
     </div>
     <div class="cut-form-actions">
      <button type="submit" class="button primary" id="cut-save">Save cut review</button>
      <button type="button" class="button" id="cut-clear">Clear form</button>
     </div>
    </form>
   </section>
  </div>
  <section class="notes-panel cut-rerun-panel" id="cut-rerun-panel" aria-label="Reprocess with cut feedback"></section>
  <details class="notes-panel" id="cut-hints-panel"><summary>Detector hints</summary><div id="cut-hints" role="status"></div></details>
  <section class="notes-panel" id="cut-reviews-panel">
   <div class="cut-reviews-header"><h2>Cut reviews</h2><div class="notes-toolbar"><label class="check-label"><input id="notes-archived" type="checkbox">Show archived</label><label class="check-label"><input id="cut-all-versions" type="checkbox">All versions</label></div></div>
   <p id="notes-status" role="status"></p>
   <div id="cut-report-list"></div>
  </section>`;
  renderCutVersionSelect();
  const clips=cutState.manifest.clips||[];
  $('#cut-clip').innerHTML=clips.length?'<option value="">Select clip…</option>'+clips.map(c=>`<option value="${esc(c.id)}">Clip ${c.index} (${preciseTime(c.original_start_seconds)}–${preciseTime(c.original_end_seconds)})</option>`).join(''):'<option value="">No kept clips</option>';
  const gaps=cutState.timeline.gaps||[];
  $('#cut-gap').innerHTML='<option value="">Select removed gap…</option>'+gaps.map(g=>`<option value="${esc(g.id)}">Gap ${g.gapIndex} (${preciseTime(g.startSeconds)}–${preciseTime(g.endSeconds)})</option>`).join('');
  $('#cut-recording').onchange=e=>{if(e.target.value&&e.target.value!==source.id&&canChangeCutTarget())location.href=`/review/${e.target.value}/cuts`;};
  $('#cut-version').onchange=e=>selectCutVersion(e.target.value,false);
  $('#cut-clip').onchange=e=>{if(e.target.value)selectCutClip(e.target.value,false);};
  $('#cut-gap').onchange=e=>{if(e.target.value)selectCutGap(e.target.value);};
  $('#cut-prev-gap').onclick=()=>{
   const gList=cutState.timeline?.gaps||[];if(!gList.length)return;
   if(cutState.mode!=='gap'){
    const clip=(cutState.manifest?.clips||[]).find(c=>c.id===cutState.clipId);
    const adj=clip?CutReviewCore.adjacentGapsForClip(cutState.timeline,clip.id):null;
    selectCutGap((adj?.prevGap||gList[0]).id);
   }else{
    const idx=gList.findIndex(g=>g.id===cutState.gapId);
    if(idx>0)selectCutGap(gList[idx-1].id);
   }
  };
  $('#cut-next-gap').onclick=()=>{
   const gList=cutState.timeline?.gaps||[];if(!gList.length)return;
   if(cutState.mode!=='gap'){
    const clip=(cutState.manifest?.clips||[]).find(c=>c.id===cutState.clipId);
    const adj=clip?CutReviewCore.adjacentGapsForClip(cutState.timeline,clip.id):null;
    selectCutGap((adj?.nextGap||gList[0]).id);
   }else{
    const idx=gList.findIndex(g=>g.id===cutState.gapId);
    if(idx>=0&&idx<gList.length-1)selectCutGap(gList[idx+1].id);
   }
  };
  const video=$('#cut-video');
  video.addEventListener('timeupdate',updateCutPlayheadReadout);
  video.addEventListener('loadedmetadata',updateCutPlayheadReadout);
  video.addEventListener('seeked',updateCutPlayheadReadout);
  const stepVideo=delta=>{
   const c=(cutState.manifest?.clips||[]).find(x=>x.id===cutState.clipId);if(!c||video.hidden)return;
   const dur=CutReviewCore.effectiveClipDurationSeconds(c,video.duration);
   video.pause();
   video.currentTime=Math.max(0,Math.min(dur,(Number.isFinite(video.currentTime)?video.currentTime:0)+delta));
   updateCutPlayheadReadout();
  };
  $('#cut-step-back').onclick=()=>stepVideo(-0.5);
  $('#cut-step-forward').onclick=()=>stepVideo(0.5);
  $('#cut-set-start').onclick=()=>{
   if(saving||$('#cut-suggested-start').disabled)return;
   const orig=currentPlayheadOriginalSeconds();
   if(orig!=null){$('#cut-suggested-start').value=orig;cutState.draftDirty=true;if(!$('#cut-verdict').value)$('#cut-verdict').value='needs_adjustment';}
  };
  $('#cut-set-end').onclick=()=>{
   if(saving||$('#cut-suggested-end').disabled)return;
   const orig=currentPlayheadOriginalSeconds();
   if(orig!=null){$('#cut-suggested-end').value=orig;cutState.draftDirty=true;if(!$('#cut-verdict').value)$('#cut-verdict').value='needs_adjustment';}
  };
  const nudgeBound=(inputEl,fallbackSec,delta)=>{
   if(saving||inputEl.disabled)return;
   const dur=cutState.manifest?.source?.duration_seconds??source.source.duration_seconds;
   const cur=inputEl.value.trim()!==''?Number(inputEl.value):fallbackSec;
   if(!Number.isFinite(cur))return;
   inputEl.value=msRound(Math.max(0,Math.min(dur,cur+delta)));
   cutState.draftDirty=true;
   if(!$('#cut-verdict').value)$('#cut-verdict').value='needs_adjustment';
  };
  $('#cut-nudge-start-minus').onclick=()=>{const c=(cutState.manifest?.clips||[]).find(x=>x.id===cutState.clipId);if(c)nudgeBound($('#cut-suggested-start'),c.original_start_seconds,-0.5);};
  $('#cut-nudge-start-plus').onclick=()=>{const c=(cutState.manifest?.clips||[]).find(x=>x.id===cutState.clipId);if(c)nudgeBound($('#cut-suggested-start'),c.original_start_seconds,0.5);};
  $('#cut-nudge-end-minus').onclick=()=>{const c=(cutState.manifest?.clips||[]).find(x=>x.id===cutState.clipId);if(c)nudgeBound($('#cut-suggested-end'),c.original_end_seconds,-0.5);};
  $('#cut-nudge-end-plus').onclick=()=>{const c=(cutState.manifest?.clips||[]).find(x=>x.id===cutState.clipId);if(c)nudgeBound($('#cut-suggested-end'),c.original_end_seconds,0.5);};
  $('#cut-review-form').addEventListener('input',()=>{cutState.draftDirty=true;});
  $('#cut-clear').onclick=()=>{if(saving)return;cutState.draftDirty=false;cutState.saveFingerprint=null;cutState.saveRequest=null;resetCutFormForTarget();};
  $('#notes-archived').onchange=render;
  $('#cut-all-versions').onchange=e=>{cutState.allVersions=e.target.checked;refresh(true);};
  $('#cut-conflict-scope-historical').onclick=()=>{
   cutState.allowHistorical=true;
   $('#cut-conflict-actions').hidden=true;
   $('#cut-form-error').textContent='';
   updateCutVersionBanner();
  };
  $('#cut-conflict-switch-serving').onclick=()=>{
   $('#cut-conflict-actions').hidden=true;
   $('#cut-form-error').textContent='';
   selectCutVersion(cutState.currentVersionId,false);
  };
  $('#cut-review-form').onsubmit=async e=>{
   e.preventDefault();if(saving)return;
   $('#cut-form-error').textContent='';$('#cut-conflict-actions').hidden=true;
   const owner=cutState,rev=generation;
   saving=true;updateCutFormLock();
   try{
    const vVal=$('#cut-verdict').value||'';
    const checkedReasons=$$('#cut-form-reasons input:checked').map(cb=>cb.value);
    const comment=$('#cut-comment').value.trim();
    const body={text:comment,reasons:checkedReasons};
    if(cutState.heuristicRef)body.heuristic_ref=cutState.heuristicRef;
    if(vVal)body.verdict=vVal;
    let target,player_id=null;
    const durMs=Math.round((cutState.manifest?.source?.duration_seconds??source.source.duration_seconds)*1000);
    if(cutState.mode==='clip'){
     const clip=(cutState.manifest?.clips||[]).find(c=>c.id===cutState.clipId);
     if(!clip)throw new Error('Choose a kept clip to review.');
     target=clipTarget(clip);
     player_id=clip.main_player?.id||(cutState.manifest.published_at===source.published_at?source.clips.find(c=>c.id===clip.id)?.main_player?.id:null)||null;
     const sRaw=$('#cut-suggested-start').value.trim(),eRaw=$('#cut-suggested-end').value.trim();
     const sMs=sRaw!==''?Math.round(Number(sRaw)*1000):null;
     const eMs=eRaw!==''?Math.round(Number(eRaw)*1000):null;
     const check=CutReviewCore.validateSuggestedBounds(clip,sMs,eMs,durMs);
     if(!check.valid)throw new Error(check.error);
     if(sMs!=null)body.suggested_start_ms=sMs;
     if(eMs!=null)body.suggested_end_ms=eMs;
    }else{
     const gStartRaw=$('#cut-gap-start').value.trim(),gEndRaw=$('#cut-gap-end').value.trim();
     const gStartSec=gStartRaw!==''?msRound(Number(gStartRaw)):NaN;
     const gEndSec=gEndRaw!==''?msRound(Number(gEndRaw)):NaN;
     const check=CutReviewCore.validateGapInterval(cutState.timeline,Math.round(gStartSec*1000),Math.round(gEndSec*1000));
     if(!check.valid)throw new Error(check.error);
     target={scope:'interval',start_seconds:gStartSec,end_seconds:gEndSec};
    }
    const ctx=cutContextForRecord();
    const fp=JSON.stringify([ctx.reviewed_video_version_id,ctx.publication,target,body,player_id]);
    if(fp!==cutState.saveFingerprint){cutState.saveFingerprint=fp;cutState.saveRequest=requestId();}
    await create('cut_feedback',target,body,cutState.saveRequest,player_id,ctx);
    if(cutState!==owner||generation!==rev)return;
    cutState.saveFingerprint=null;cutState.saveRequest=null;
    cutState.draftDirty=false;
    resetCutFormForTarget();
    await refresh(true);
    toast('Cut review saved.');
   }catch(err){
    if(cutState!==owner||generation!==rev)return;
    $('#cut-form-error').textContent=err.message;
    if(err.status===409){
     await refresh(true);
     $('#cut-conflict-actions').hidden=false;
    }
   }finally{
    saving=false;updateCutFormLock();
   }
  };
  resetCutFormForTarget();
  updateCutVersionBanner();
  updateCutStageAndForm();
  loadPromise=refresh(true);
  await loadPromise;
  timer=setInterval(refresh,10000);
 }
 return {init,mount,cuts,current,clipBadge,filterClip,hasMoments,refresh,openEntityNotes,captureEditNote,hasEditNoteChange,saveEditNote,loadLatestEditNote};
})();
