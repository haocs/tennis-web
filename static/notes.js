'use strict';
const NoteUI=(()=>{
 let source=null,items=[],itemsLoaded=false,itemsLoadedSourceId=null,itemsLoadedPub=null,loadError='',loadPromise=null,generation=0,timer=null,editing=null,editingKind='note',saveRequest=null,saving=false,markerKey='',lastCurrent=null,pendingHighlight=null,saveFingerprint=null;
 let editorContext=null,entityNotesTarget=null,entityNotesGen=0,editNoteRecord={id:null,version:0,initialText:'',mode:'new',player_id:null,unloaded:true},editNoteDirty=false,editNoteSaveRequest=null,editNoteFingerprint=null;
 let cueTimer=null,cueKey='',mediaKey='',cueClock=new MomentPlayback.CueClock();
 const names={comment:'Moment',note:'Moment',highlight:'Highlight',usefulness:'Less useful',cut_feedback:'Cut report',coach:'Coach suggestion'};
 const reasons={poor_cut:'Poor cut',no_play:'No tennis play',downtime:'Too much downtime',too_short:'Too short',wrong_player:'Wrong player',start_early:'Start too early',start_late:'Start too late',end_early:'End too early',end_late:'End too late'};
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
  if(!source||(!force&&document.hidden))return;const owner=source,rev=generation;let after='',all=[];
  try{
   do{const data=await api(`/api/recordings/${owner.id}/metadata${after?'?after='+encodeURIComponent(after):''}`);all.push(...data.annotations);after=data.next_after;}while(after&&rev===generation);
   if(owner!==source||rev!==generation)return;
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
   loadError=e.message||'Could not load notes.';
   const status=$('#notes-status');if(status)status.textContent='Notes unavailable. '+e.message;
   renderEntityNotes();
  }
 }
 async function create(kind,target,body,rid=requestId(),player_id=currentClip()?.main_player?.id||null,context={source_id:source.id,publication:source.published_at}){
  if(kind==='highlight'||target.scope==='point'||target.scope==='interval')return api('/api/moments',{method:'POST',body:JSON.stringify({original_recording_id:context.source_id,publication:context.publication,type:kind==='note'?'comment':kind,start_ms:Math.round(target.start_seconds*1000),...(target.end_seconds===undefined?{}:{end_ms:Math.round(target.end_seconds*1000)}),payload:body,request_id:rid,player_id})});
  return api(kind==='cut_feedback'?'/api/processing-annotations':'/api/metadata',{method:'POST',body:JSON.stringify({source_id:context.source_id,publication:context.publication,kind,target,body,request_id:rid,player_id})});
 }
 async function update(a,changes,context={publication:source.published_at}){
  if(a.record_class==='moment'&&!a.read_only){
   const {body,target,...rest}=changes;
   return api('/api/moments/'+a.id,{method:'PUT',body:JSON.stringify({publication:context.publication,version:a.version,...rest,...(body?{payload:body}:{}),...(target?{start_ms:Math.round(target.start_seconds*1000),...(target.end_seconds===undefined?{}:{end_ms:Math.round(target.end_seconds*1000)})}:{})})});
  }
  return api((a.kind==='cut_feedback'?'/api/processing-annotations/':'/api/metadata/')+a.id,{method:'PUT',body:JSON.stringify({publication:context.publication,version:a.version,...changes})});
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
  const scopeKey=model.mode+':'+model.current;if(lastCurrent!==scopeKey){lastCurrent=scopeKey;render();}markers();cueTick();
 }
 function jump(a,selectedFragment=null,preservePlayback=false){
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
 function targetLabel(a){const t=a.target;if(t.scope==='video')return 'Whole recording';return 'Original '+preciseTime(t.start_seconds)+(t.scope==='point'?'':'–'+preciseTime(t.end_seconds));}
 function relationLabel(a){if(a.archive_reason==='duplicate_highlight'&&a.status==='archived')return 'Duplicate highlight · history retained';return ({needs_review:'Cut changed · review target',unavailable:'Outside current cuts',partly_retained:'Partly outside current cuts',historical_run:'Earlier processed version'})[a.relation]||'';}
 function itemActions(a,cuts){
  const jumpButton=(cuts||a.record_class==='moment')&&a.status==='active'&&MomentPlayback.canPlay(a,items)&&a.visible_intervals.length?'<button class="button small" type="button" data-note-action="jump" aria-label="Jump to moment">Jump</button>':'';
  const editButtons=a.read_only?'':'<button type="button" data-note-action="edit">Edit</button><button type="button" data-note-action="archive">'+(a.status==='active'?'Archive':'Restore')+'</button>';
  const history='<button type="button" data-note-action="history">History</button>';
  if(cuts)return jumpButton+(editButtons+history).replaceAll('<button type=','<button class="button small" type=');
  const coach=a.read_only?'<button class="button small" type="button" data-note-action="coach" aria-label="Review coaching">Review</button>':'';
  return jumpButton+coach+`<details class="moment-more note-actions-menu"><summary class="button small" aria-label="${a.record_class==='entity_metadata'?'Note actions':'Moment actions'}">⋯</summary><div>${editButtons}${history}</div></details>`;
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
  box.innerHTML=rows.length?rows.map(a=>{const text=MomentPlayback.displayText(a,true).trim();return `<article class="note-item${a.status==='archived'?' archived':''}" data-note="${esc(a.id)}"><div class="note-item-heading"><strong>${esc(a.body?.response?'Coach review':a.record_class==='entity_metadata'?'Note':names[a.kind]||a.kind)}${a.status==='archived'?' · Archived':''}</strong><span>${esc(targetLabel(a))}</span></div>${text?(a.body?.response?`<details class="note-response"><summary>Read saved response</summary><p>${esc(text)}</p></details>`:`<p class="note-text">${esc(text)}</p>`):''}${a.kind==='cut_feedback'?`<p>${(a.body?.reasons||[]).map(r=>esc(reasons[r]||r)).join(' · ')}</p>`:''}${relationLabel(a)?`<p class="note-relation">${esc(relationLabel(a))}</p>`:''}<div class="note-item-actions">${itemActions(a,cuts)}</div><div class="note-history" hidden></div></article>`;}).join(''):'<p class="note-empty">'+(cuts?'No cut reports yet.':box.id==='entity-notes-list'?'No notes.':'No moments.')+'</p>';
  $$('[data-note-action]',box).forEach(b=>b.onclick=async()=>{
   const card=b.closest('[data-note]'),a=items.find(a=>a.id===card.dataset.note);if(b.closest('.note-actions-menu'))b.closest('.note-actions-menu').open=false;b.disabled=true;
   try{
    if(b.dataset.noteAction==='jump')jump(a);
    if(b.dataset.noteAction==='edit')openEditor(a.kind,a);
    if(b.dataset.noteAction==='archive'){if(box.id==='entity-notes-list')editNoteDirty=false;await update(a,{status:a.status==='active'?'archived':'active'});await refresh(true);toast(a.status==='active'?'Archived.':'Restored.');}
    if(b.dataset.noteAction==='history'){const h=$('.note-history',card);let after=0,versions=[];do{const r=await api('/api/annotations/'+a.id+'/revisions'+(after?'?after='+after:''));versions.push(...r.revisions);after=r.next_after;}while(after);h.textContent=versions.map(r=>'v'+r.version+' · '+(r.status||'')+' · '+(r.body?.text||r.content?.cue||'')).join('\n');h.hidden=!h.hidden;}
    if(b.dataset.noteAction==='coach'){
     const fragment=MomentPlayback.inScope([a],scopeOptions())[0]?.fragments[0];const clip=source.clips.find(c=>c.id===(model.mode==='clip'?model.current:fragment?.clip_id));if(!clip)throw new Error('No current clip contains this suggestion. Its history is retained in the export.');
     playClip(clip.id,false);$('#study-clip').click();$('#study-mode-coach').click();$('#coach-step-4').click();
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
  $('#note-start-row').hidden=!timed;$('#note-end-row').hidden=scope!=='interval';
  $('#note-clip-row').hidden=scope!=='clip'||editingKind==='note';
  const enabled=!editing||$('#note-retarget').checked;$('#note-target-fields').disabled=!enabled;
  $('#note-start').disabled=!enabled||!timed;$('#note-start').required=timed;
  $('#note-end').disabled=!enabled||scope!=='interval';$('#note-end').required=scope==='interval';
 }
 function openEditor(kind='note',a=null){
  editing=a;editingKind=kind;editorContext={source_id:source.id,publication:source.published_at,mode:kind==='cut_feedback'?'clip':model.mode,clip_id:model.mode==='clip'||kind==='cut_feedback'?model.current:null};
  if(kind==='note'&&a?.record_class!=='moment'&&entityNotesTarget)editorContext={...entityNotesTarget,mode:entityNotesTarget.clip_id?'clip':'full'};saveRequest=requestId();saveFingerprint=null;$('#note-editor-title').textContent=(a?'Edit ':'Add ')+(kind==='cut_feedback'?'cut report':kind==='note'&&a?.record_class!=='moment'?'note':kind==='highlight'?'highlight':'moment');
  const timed=kind==='highlight'||kind==='comment'||a?.record_class==='moment';
  const allowed=timed?['point','interval']:kind==='note'?[editorContext.clip_id?'clip':'video']:['clip'];
  $('#note-scope').innerHTML=allowed.map(s=>`<option value="${s}">${({point:'Moment',interval:'Moment with end',clip:'Current clip',video:'Whole video'})[s]}</option>`).join('');
  $('#note-target-clip').innerHTML=source.clips.map(c=>`<option value="${c.id}">Clip ${c.index} · Original ${preciseTime(c.original_start_seconds)}–${preciseTime(c.original_end_seconds)}</option>`).join('');
  let at=msMin(currentClip()?.original_start_seconds||0);try{at=pointAtPlayer();}catch(_){}
  const t=a?.target||{scope:timed?'point':allowed[0],start_seconds:at};$('#note-scope').value=t.scope;
  const start=msRound(t.start_seconds??0),end=msRound(t.end_seconds??Math.min(msMaxEnd(source.source.duration_seconds),start+1));
  $('#note-start').value=start;$('#note-end').value=end;
  $('#note-scope-row').hidden=true;$('#note-optional-end-row').hidden=!timed;$('#note-optional-end').checked=t.scope==='interval';$('#note-optional-end').disabled=!!a;$('#note-start').required=timed;
  if(!a&&kind==='highlight')try{$('#note-start').value=pointAtPlayer();}catch(_){}
  $('#note-target-clip').value=t.clip_id||editorContext.clip_id||source.clips[0]?.id||'';
  $('#note-save').textContent=kind==='cut_feedback'?'Save report':timed?'Save moment':'Save note';$('#note-text-label').textContent=kind==='cut_feedback'?'Comment':'Note';$('#note-text').value=a?.body.text||'';$('#note-text').required=kind==='note'&&!timed;
  $('#note-reason-row').hidden=kind!=='usefulness';$('#note-usefulness-reason').value=a?.body.reason||'';
  $('#note-cut-reasons').hidden=kind!=='cut_feedback';$$('#note-cut-reasons input').forEach(e=>e.checked=(a?.body.reasons||[]).includes(e.value));
  $('#note-retarget-row').hidden=!a||!timed;$('#note-retarget').checked=false;
  $('#note-editor-context').textContent=a?targetLabel(a)+(relationLabel(a)?' · '+relationLabel(a):''):'';
  const targetClip=editorContext.clip_id?source.clips.find(c=>c.id===editorContext.clip_id):null;
  $('#note-player').innerHTML='<option value="">No player specified</option>'+model.players.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('');$('#note-player').value=a?.player_id||(editorContext.clip_id?targetClip?.main_player?.id:currentClip()?.main_player?.id&&timed?currentClip()?.main_player?.id:'')||'';
  $('#note-editor-error').textContent='';$('#note-load-latest').hidden=true;editorScope();if($('#edit-dialog')?.open)$('#edit-dialog').inert=true;$('#note-editor').showModal();
 }
 function init(){
  const node=document.createElement('div');node.innerHTML=`<dialog id="note-editor" aria-labelledby="note-editor-title"><form id="note-form" novalidate><div class="dialog-heading"><h2 id="note-editor-title">Add note</h2><button type="button" class="icon-button" id="note-close" aria-label="Close note editor">×</button></div><p id="note-editor-context"></p><label id="note-retarget-row" class="check-label"><input type="checkbox" id="note-retarget">Change timing or target</label><fieldset id="note-target-fields"><label id="note-scope-row">Applies to<select id="note-scope" aria-label="Note target"></select></label><label id="note-clip-row">Clip<select id="note-target-clip" aria-label="Note clip"></select></label><label id="note-start-row">Original start (seconds)<input type="number" id="note-start" min="0" step="0.001"></label><label class="check-label" id="note-optional-end-row"><input type="checkbox" id="note-optional-end">End time</label><label id="note-end-row">Original end (seconds)<input type="number" id="note-end" min="0" step="0.001"></label></fieldset><label><span id="note-text-label">Note</span><textarea id="note-text" rows="3" maxlength="2000"></textarea></label><label id="note-reason-row">Reason<select id="note-usefulness-reason" aria-label="Usefulness reason"><option value="">Not specified</option><option value="low_information">Low information</option><option value="repetitive">Repetitive</option><option value="poor_view">Poor view</option><option value="other">Other</option></select></label><fieldset id="note-cut-reasons"><legend>Cut issues</legend>${Object.entries(reasons).map(([k,v])=>`<label class="check-label"><input type="checkbox" value="${k}">${v}</label>`).join('')}</fieldset><label>Player (optional)<select id="note-player" aria-label="Note player"></select></label><p id="note-editor-error" class="error-message" role="alert"></p><button type="button" class="button" id="note-load-latest" hidden>Load latest saved version</button><button type="submit" class="button primary" id="note-save">Save note</button></form></dialog>`;document.body.append(node);
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
    if(source.id!==context.source_id||source.published_at!==context.publication)throw new Error('This video changed. Close the editor and refresh before saving.');
    const body={text:$('#note-text').value.trim()};if(editingKind==='usefulness')Object.assign(body,{value:'less_useful',reason:$('#note-usefulness-reason').value});
    if(editingKind==='cut_feedback')body.reasons=$$('#note-cut-reasons input:checked').map(e=>e.value);
    const parseSec=el=>{const raw=el.value.trim();return raw===''?NaN:msRound(Number(raw));};
    let target;if(!editing||$('#note-retarget').checked){const scope=$('#note-scope').value;target={scope};if(scope==='clip'){const selected=source.clips.find(c=>c.id===(editingKind==='note'?context.clip_id:$('#note-target-clip').value));if(!selected)throw new Error('Choose a current clip for this target');target=clipTarget(selected);}else if(scope!=='video'){target.start_seconds=parseSec($('#note-start'));if(scope==='interval')target.end_seconds=parseSec($('#note-end'));}}
    const timing=target||editing?.target;
    const timed=timing&&['point','interval'].includes(timing.scope);
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
  const more=$('.player-more');more.classList.remove('hidden');$('.player-more-list').insertAdjacentHTML('beforeend',`<a class="note-cut-link" href="/review/${s.id}/cuts">Review cuts</a>`);
  const toolbar=$('.clip-toolbar .toolbar');toolbar.insertAdjacentHTML('beforeend','<select id="note-clip-filter" aria-label="Clip focus"><option value="all">All clips</option><option value="recommended">Recommended</option><option value="liked">Liked only</option><option value="highlights">Highlights</option></select>');$('#note-clip-filter').value=model.noteFilter||'all';$('#note-clip-filter').onchange=e=>{model.noteFilter=e.target.value;renderClipList();};
  loadPromise=refresh(true);timer=setInterval(refresh,10000);
 }
 function cuts(s){
  source=s;model.session=s;items=[];itemsLoaded=false;itemsLoadedSourceId=null;itemsLoadedPub=null;loadError='';generation++;clearInterval(timer);nav();
  $('#main').innerHTML=`<div class="review-breadcrumb"><a href="/review/${s.id}">Back to review</a><span>/</span>Review cuts</div><h1>Review cuts</h1><p>Reports refer to the processed version you reviewed. They do not change your videos.</p><label>Clip<select id="cut-clip" aria-label="Review clip">${s.clips.map(c=>`<option value="${c.id}">Clip ${c.index}</option>`).join('')}</select></label><video class="cut-review-player" id="cut-video" controls playsinline preload="metadata"></video><p id="cut-original"></p><button type="button" class="button primary" id="cut-report">Report this cut</button><section class="notes-panel"><h2>Cut reports</h2><label class="check-label"><input id="notes-archived" type="checkbox">Archived</label><p id="notes-status" role="status"></p><div id="cut-report-list"></div></section>`;
  const choose=()=>{const c=s.clips.find(c=>c.id===$('#cut-clip').value);model.current=c.id;$('#cut-video').src=mediaURL(s,c.file);$('#cut-original').textContent='Original '+preciseTime(c.original_start_seconds)+'–'+preciseTime(c.original_end_seconds);};
  $('#cut-clip').onchange=choose;$('#cut-report').onclick=()=>{$('#cut-video').pause();openEditor('cut_feedback');};$('#notes-archived').onchange=render;choose();loadPromise=refresh(true);timer=setInterval(refresh,10000);
 }
 return {init,mount,cuts,current,clipBadge,filterClip,hasMoments,refresh,openEntityNotes,captureEditNote,hasEditNoteChange,saveEditNote,loadLatestEditNote};
})();
