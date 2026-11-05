'use strict';
const NoteUI=(()=>{
 let source=null,items=[],generation=0,timer=null,editing=null,editingKind='note',saveRequest=null,saving=false,markerKey='',lastCurrent=null,pendingHighlight=null,saveFingerprint=null;
 const names={comment:'Comment',note:'Note',highlight:'Highlight',usefulness:'Less useful',cut_feedback:'Cut report',coach:'Coach suggestion'};
 const reasons={poor_cut:'Poor cut',no_play:'No tennis play',downtime:'Too much downtime',too_short:'Too short',wrong_player:'Wrong player',start_early:'Start too early',start_late:'Start too late',end_early:'End too early',end_late:'End too late'};
 const active=a=>a.status==='active';
 const related=(a,c)=>c&&(a.matched_clip_ids||a.related_clip_ids||[]).includes(c.id);
 const currentClip=()=>source?.clips.find(c=>c.id===model.current);
 const requestId=()=>globalThis.crypto?.randomUUID?.()||'note-'+Date.now()+'-'+Math.random().toString(36).slice(2);
 function lessUseful(c){return items.find(a=>a.kind==='usefulness'&&active(a)&&a.playback_eligible&&related(a,c));}
 function highlights(c){return items.filter(a=>a.kind==='highlight'&&active(a)&&a.playback_eligible&&related(a,c));}
 function clipBadge(c){const n=highlights(c).length;return `${n?`<span class="note-clip-badge" title="${n} highlighted moment${n===1?'':'s'}">★${n>1?n:''}</span>`:''}${lessUseful(c)?'<span class="note-clip-badge note-less" title="Marked less useful">↓</span>':''}`;}
 function filterClip(c){return model.noteFilter==='liked'?!!c.like.value:model.noteFilter==='highlights'?highlights(c).length>0:model.noteFilter==='useful'?!lessUseful(c):true;}
 function pointAtPlayer(){
  const v=$('#player'),c=currentClip();if(!v||!c||v.readyState<1)throw new Error('Wait for the clip to load');
  if(model.mode==='clip')return Math.min(Math.max(c.original_start_seconds,c.original_end_seconds-.001),c.original_start_seconds+v.currentTime);
  const match=source.clips.find(c=>v.currentTime>=c.playback_start_seconds&&v.currentTime<c.playback_end_seconds)||source.clips.at(-1);
  return Math.min(Math.max(match.original_start_seconds,match.original_end_seconds-.001),match.original_start_seconds+v.currentTime-match.playback_start_seconds);
 }
 function clipTarget(c=currentClip()){if(!c)throw new Error('Choose a clip first');return {scope:'clip',clip_id:c.id,start_seconds:c.original_start_seconds,end_seconds:c.original_end_seconds};}
 async function refresh(){
  if(!source||document.hidden)return;const owner=source,rev=generation;let after='',all=[];
  try{
   do{const data=await api(`/api/recordings/${owner.id}/metadata${after?'?after='+encodeURIComponent(after):''}`);all.push(...data.annotations);after=data.next_after;}while(after&&rev===generation);
   if(owner!==source||rev!==generation)return;
   if($('#notes-status'))$('#notes-status').textContent='';
   const changed=JSON.stringify(all)!==JSON.stringify(items);items=all;
   if(changed){if($('#clip-list'))renderClipList();render();markers();}
   current();
  }catch(e){const status=$('#notes-status');if(status)status.textContent='Notes unavailable. '+e.message;}
 }
 async function create(kind,target,body,rid=requestId(),player_id=currentClip()?.main_player?.id||null){
  if(kind==='highlight'||target.scope==='point'||target.scope==='interval')return api('/api/moments',{method:'POST',body:JSON.stringify({original_recording_id:source.id,publication:source.published_at,type:kind==='note'?'comment':kind,start_ms:Math.round(target.start_seconds*1000),...(target.end_seconds===undefined?{}:{end_ms:Math.round(target.end_seconds*1000)}),payload:body,request_id:rid,player_id})});
  return api(kind==='cut_feedback'?'/api/processing-annotations':'/api/metadata',{method:'POST',body:JSON.stringify({source_id:source.id,publication:source.published_at,kind,target,body,request_id:rid,player_id})});
 }
 async function update(a,changes){return api((a.kind==='cut_feedback'?'/api/processing-annotations/':'/api/metadata/')+a.id,{method:'PUT',body:JSON.stringify({publication:source.published_at,version:a.version,...changes})});}
 async function highlight(){
  if(saving)return;try{if(!pendingHighlight)pendingHighlight={target:{scope:'point',start_seconds:pointAtPlayer()},id:requestId()};saving=true;const result=await create('highlight',pendingHighlight.target,{text:''},pendingHighlight.id);pendingHighlight=null;await refresh();toast(result.already_highlighted?'Moment already highlighted.':'Moment highlighted.');}catch(e){if(e.status)pendingHighlight=null;toast(e.status?e.message:'Could not confirm the save. Tap Highlight again to retry the same moment.');}finally{saving=false;}
 }
 async function demote(){
  if(saving)return;try{saving=true;const c=currentClip();const existing=items.find(a=>a.kind==='usefulness'&&related(a,c)&&a.relation==='exact');
   await api(`/api/entities/videos/${source.id}/metadata`,{method:'PUT',body:JSON.stringify({publication:source.published_at,subject_type:'clip',clip_id:c.id,field:'less_useful',value:!lessUseful(c),version:existing?.version||0,...(existing?{key:existing.id}:{})})});
   await refresh();toast(lessUseful(c)?'Clip marked less useful.':'Less useful mark removed.');
  }catch(e){toast(e.message);if(e.status===409)await refresh();}finally{saving=false;}
 }
 function current(){
  const b=$('#note-demote');if(!b)return;const c=currentClip();b.disabled=!c;b.hidden=model.mode!=='clip';b.setAttribute('aria-pressed',String(!!lessUseful(c)));b.title=lessUseful(c)?'Remove less useful mark':'Mark clip as less useful';
  const h=$('#note-highlight');if(h)h.disabled=!c;
  if(lastCurrent!==model.current){lastCurrent=model.current;render();}markers();
 }
 function jump(a){
  if(!a.playback_eligible||!a.visible_intervals.length)return toast('This moment is outside the current cuts. Its original timestamp is retained.');
  const fragment=a.visible_intervals.find(f=>f.clip_id===model.current)||a.visible_intervals[0];
  if($('#cut-video')){const video=$('#cut-video'),choice=$('#cut-clip');const seek=()=>{video.currentTime=fragment.local_start;video.pause();};if(choice.value===fragment.clip_id&&video.readyState>=1)seek();else{video.addEventListener('loadedmetadata',seek,{once:true});choice.value=fragment.clip_id;choice.dispatchEvent(new Event('change'));}return;}
  if(model.mode==='full'){playFull(fragment.playback_start,false);}else{playClip(fragment.clip_id,false);setSource(mediaURL(source,source.clips.find(c=>c.id===fragment.clip_id).file),fragment.local_start,false);}
  $('#player').pause();model.playerControls?.reveal();$('.player-shell')?.scrollIntoView({behavior:'smooth',block:'start'});
 }
 function markers(){
  const box=$('#note-markers'),v=$('#player');if(!box||!v)return;
  const duration=v.duration;if(!Number.isFinite(duration)||duration<=0){box.replaceChildren();return;}
  const selected=model.mode==='clip'?model.current:null;const key=JSON.stringify([selected,duration,items.map(a=>[a.id,a.version,a.status,a.playback_eligible,a.visible_intervals])]);if(key===markerKey)return;markerKey=key;
  const visible=items.filter(a=>active(a)&&a.playback_eligible&&['highlight','coach'].includes(a.kind));
  box.innerHTML=visible.flatMap(a=>a.visible_intervals.filter(f=>!selected||f.clip_id===selected).map(f=>{
   const at=selected?f.local_start:f.playback_start;return `<button type="button" class="note-marker ${a.kind==='coach'?'coach-marker':''}" data-note-jump="${esc(a.id)}" data-note-position="${Math.max(0,Math.min(100,100*at/duration))}" aria-label="${a.kind==='coach'?'Coach suggestion':'Highlight'} at original ${esc(preciseTime(f.original_start))}" title="${a.kind==='coach'?'Coach':'Highlight'} · Original ${esc(preciseTime(f.original_start))}"></button>`;
  })).join('');
  $$('[data-note-jump]',box).forEach(b=>{b.style.setProperty('--note-position',b.dataset.notePosition+'%');b.onclick=()=>jump(items.find(a=>a.id===b.dataset.noteJump));});
 }
 function targetLabel(a){const t=a.target;if(t.scope==='video')return 'Whole recording';return 'Original '+preciseTime(t.start_seconds)+(t.scope==='point'?'':'–'+preciseTime(t.end_seconds));}
 function relationLabel(a){if(a.archive_reason==='duplicate_highlight'&&a.status==='archived')return 'Duplicate highlight · history retained';return ({needs_review:'Cut changed · review target',unavailable:'Outside current cuts',partly_retained:'Partly outside current cuts',historical_run:'Earlier processed version'})[a.relation]||'';}
 function render(){
  const box=$('#notes-list')||$('#cut-report-list');if(!box)return;
  const cuts=!!$('#cut-report-list'),view=$('#notes-view')?.value||'clip';const c=currentClip();
  let rows=items.filter(a=>cuts?a.kind==='cut_feedback':a.record_class!=='processing_annotation');
  if(!cuts&&view==='clip')rows=rows.filter(a=>a.target.scope==='video'||related(a,c));
  const archived=$('#notes-archived')?.checked||false;rows=rows.filter(a=>archived||active(a));
  rows.sort((a,b)=>(a.target.start_seconds??-1)-(b.target.start_seconds??-1)||a.id.localeCompare(b.id));
  box.innerHTML=rows.length?rows.map(a=>`<article class="note-item${a.status==='archived'?' archived':''}" data-note="${esc(a.id)}"><div class="note-item-heading"><strong>${esc(a.body.response?'Coach review':names[a.kind]||a.kind)}${a.status==='archived'?' · Archived':''}</strong><span>${esc(targetLabel(a))}</span></div>${a.body.text?(a.body.response?`<details class="note-response"><summary>Read saved response</summary><p>${esc(a.body.text)}</p></details>`:`<p>${esc(a.body.text)}</p>`):''}${a.kind==='cut_feedback'?`<p>${a.body.reasons.map(r=>esc(reasons[r]||r)).join(' · ')}</p>`:''}${relationLabel(a)?`<p class="note-relation">${esc(relationLabel(a))}</p>`:''}<div class="note-item-actions">${a.playback_eligible&&a.visible_intervals.length?`<button class="button small" type="button" data-note-action="jump">Jump to moment</button>`:''}${a.read_only?'<button class="button small" type="button" data-note-action="coach">Review coaching</button>':'<button class="button small" type="button" data-note-action="edit">Edit</button><button class="button small" type="button" data-note-action="archive">'+(a.status==='active'?'Archive':'Restore')+'</button>'}<button type="button" class="button small" data-note-action="history">History</button></div><div class="note-history" hidden></div></article>`).join(''):'<p class="note-empty">'+(cuts?'No cut reports yet.':'No moments or notes in this view.')+'</p>';
  $$('[data-note-action]',box).forEach(b=>b.onclick=async()=>{
   const card=b.closest('[data-note]'),a=items.find(a=>a.id===card.dataset.note);b.disabled=true;
   try{
    if(b.dataset.noteAction==='jump')jump(a);
    if(b.dataset.noteAction==='edit')openEditor(a.kind,a);
    if(b.dataset.noteAction==='archive'){await update(a,{status:a.status==='active'?'archived':'active'});await refresh();toast(a.status==='active'?'Archived.':'Restored.');}
    if(b.dataset.noteAction==='history'){const h=$('.note-history',card);let after=0,versions=[];do{const r=await api('/api/annotations/'+a.id+'/revisions'+(after?'?after='+after:''));versions.push(...r.revisions);after=r.next_after;}while(after);h.textContent=versions.map(r=>'v'+r.version+' · '+(r.status||'')+' · '+(r.body?.text||r.content?.cue||'')).join('\n');h.hidden=!h.hidden;}
    if(b.dataset.noteAction==='coach'){
     const clip=source.clips.find(c=>a.related_clip_ids.includes(c.id));if(!clip)throw new Error('No current clip contains this suggestion. Its history is retained in the export.');
     playClip(clip.id,false);$('#study-clip').click();$('#study-mode-coach').click();$('#coach-step-4').click();
    }
   }catch(e){toast(e.message);if(e.status===409)await refresh();}finally{b.disabled=false;}
  });
 }
 function editorScope(){
  const scope=$('#note-scope').value;$('#note-start-row').hidden=scope==='video'||scope==='clip';$('#note-end-row').hidden=scope!=='interval';
  $('#note-clip-row').hidden=scope!=='clip';
  const enabled=!editing||$('#note-retarget').checked;$('#note-target-fields').disabled=!enabled;
 }
 function openEditor(kind='note',a=null){
  editing=a;editingKind=kind;saveRequest=requestId();saveFingerprint=null;$('#note-editor-title').textContent=(a?'Edit ':'Add ')+(names[kind]||'note').toLowerCase();
  const allowed=kind==='highlight'||kind==='comment'||a?.record_class==='moment'?['point','interval']:kind==='note'?['clip','video']:['clip'];
  $('#note-scope').innerHTML=allowed.map(s=>`<option value="${s}">${({point:'Moment',interval:'Moment with end',clip:'Current clip',video:'Whole video'})[s]}</option>`).join('');
  $('#note-target-clip').innerHTML=source.clips.map(c=>`<option value="${c.id}">Clip ${c.index} · Original ${preciseTime(c.original_start_seconds)}–${preciseTime(c.original_end_seconds)}</option>`).join('');
  let at=currentClip()?.original_start_seconds||0;try{at=pointAtPlayer();}catch(_){}
  const t=a?.target||{scope:kind==='highlight'||kind==='comment'?'point':'clip',start_seconds:at};$('#note-scope').value=t.scope;
  $('#note-start').value=t.start_seconds??0;$('#note-end').value=t.end_seconds??Math.min(source.source.duration_seconds,(t.start_seconds||0)+1);
  if(!a&&kind==='highlight')try{$('#note-start').value=pointAtPlayer();}catch(_){}
  $('#note-target-clip').value=t.clip_id||currentClip()?.id||source.clips[0]?.id||'';
  $('#note-save').textContent=kind==='cut_feedback'?'Save report':'Save note';$('#note-text-label').textContent=kind==='cut_feedback'?'Comment':'Note';$('#note-text').value=a?.body.text||'';$('#note-text').required=kind==='note'||kind==='comment';
  $('#note-reason-row').hidden=kind!=='usefulness';$('#note-usefulness-reason').value=a?.body.reason||'';
  $('#note-cut-reasons').hidden=kind!=='cut_feedback';$$('#note-cut-reasons input').forEach(e=>e.checked=(a?.body.reasons||[]).includes(e.value));
  $('#note-retarget-row').hidden=!a||kind==='cut_feedback'||kind==='usefulness';$('#note-retarget').checked=false;
  $('#note-editor-context').textContent=a?targetLabel(a)+(relationLabel(a)?' · '+relationLabel(a):''):'';
  $('#note-player').innerHTML='<option value="">No player specified</option>'+model.players.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('');$('#note-player').value=a?.player_id||currentClip()?.main_player?.id||'';
  $('#note-editor-error').textContent='';$('#note-load-latest').hidden=true;editorScope();$('#note-editor').showModal();
 }
 function init(){
  const node=document.createElement('div');node.innerHTML=`<dialog id="note-editor" aria-labelledby="note-editor-title"><form id="note-form"><div class="dialog-heading"><h2 id="note-editor-title">Add note</h2><button type="button" class="icon-button" id="note-close" aria-label="Close note editor">×</button></div><p id="note-editor-context"></p><label id="note-retarget-row" class="check-label"><input type="checkbox" id="note-retarget">Change timing or target</label><fieldset id="note-target-fields"><label>Applies to<select id="note-scope" aria-label="Note target"></select></label><label id="note-clip-row">Clip<select id="note-target-clip" aria-label="Note clip"></select></label><label id="note-start-row">Original start (seconds)<input type="number" id="note-start" min="0" step="0.001"></label><label id="note-end-row">Original end (seconds)<input type="number" id="note-end" min="0" step="0.001"></label></fieldset><label><span id="note-text-label">Note</span><textarea id="note-text" rows="3" maxlength="2000"></textarea></label><label id="note-reason-row">Reason<select id="note-usefulness-reason" aria-label="Usefulness reason"><option value="">Not specified</option><option value="low_information">Low information</option><option value="repetitive">Repetitive</option><option value="poor_view">Poor view</option><option value="other">Other</option></select></label><fieldset id="note-cut-reasons"><legend>Cut issues</legend>${Object.entries(reasons).map(([k,v])=>`<label class="check-label"><input type="checkbox" value="${k}">${v}</label>`).join('')}</fieldset><label>Player (optional)<select id="note-player" aria-label="Note player"></select></label><p id="note-editor-error" class="error-message" role="alert"></p><button type="button" class="button" id="note-load-latest" hidden>Load latest saved version</button><button type="submit" class="button primary" id="note-save">Save note</button></form></dialog>`;document.body.append(node);
  $('#note-load-latest').onclick=()=>{const latest=items.find(a=>a.id===editing?.id);if(latest)openEditor(latest.kind,latest);else location.reload();};
  $('#note-close').onclick=()=>$('#note-editor').close();$('#note-scope').onchange=editorScope;$('#note-retarget').onchange=editorScope;
  $('#note-form').onsubmit=async e=>{
   e.preventDefault();if(saving)return;saving=true;$('#note-save').disabled=true;
   try{
    const body={text:$('#note-text').value.trim()};if(editingKind==='usefulness')Object.assign(body,{value:'less_useful',reason:$('#note-usefulness-reason').value});
    if(editingKind==='cut_feedback')body.reasons=$$('#note-cut-reasons input:checked').map(e=>e.value);
    let target;if(!editing||$('#note-retarget').checked){const scope=$('#note-scope').value;target={scope};if(scope==='clip'){const selected=source.clips.find(c=>c.id===$('#note-target-clip').value);if(!selected)throw new Error('Choose a current clip for this target');target=clipTarget(selected);}else if(scope!=='video'){target.start_seconds=Number($('#note-start').value);if(scope==='interval')target.end_seconds=Number($('#note-end').value);}}
    const player_id=$('#note-player').value||null;
    const fingerprint=JSON.stringify([body,target,player_id]);if(fingerprint!==saveFingerprint){saveFingerprint=fingerprint;saveRequest=requestId();}
    if(editing)await update(editing,{body,player_id,...(target?{target}:{})});
    else await create(editingKind,target,body,saveRequest,player_id);
    await refresh();$('#note-editor').close();$('#notes-panel')?.setAttribute('open','');toast('Saved.');
   }catch(e){$('#note-editor-error').textContent=e.message;if(e.status===409){await refresh();$('#note-load-latest').hidden=false;}}finally{saving=false;$('#note-save').disabled=false;}
  };
 }
 function mount(s){
  source=s;items=[];markerKey='';lastCurrent=null;generation++;clearInterval(timer);
  const markers=document.createElement('div');markers.id='note-markers';markers.className='note-markers';$('#video-stage').append(markers);
  $('#player').addEventListener('loadedmetadata',()=>{current();});$('#player').addEventListener('timeupdate',()=>{current();});
  $('.player-shell').insertAdjacentHTML('afterend',`<div class="note-quick-actions"><button type="button" class="button small" id="note-highlight" aria-label="Highlight this moment" title="Highlight this moment">★ Highlight moment</button><button type="button" class="button small" id="note-demote" aria-label="Less useful clip" aria-pressed="false">↓ Less useful</button></div><details id="notes-panel" class="notes-panel"><summary>Moments & notes</summary><div class="notes-toolbar"><button type="button" class="button small" id="add-note">Add note</button><button type="button" class="button small" id="add-timed-comment">Timed comment</button><select id="notes-view" aria-label="Notes view"><option value="clip">This clip</option><option value="all">Whole recording</option></select><label class="check-label"><input id="notes-archived" type="checkbox">Archived</label><a class="button small" href="/api/annotations/sources/${s.id}/export">Export metadata</a></div><p id="notes-status" role="status"></p><div id="notes-list"></div></details>`);
  $('#note-highlight').onclick=highlight;$('#note-demote').onclick=demote;
  $('#add-note').onclick=()=>openEditor();$('#add-timed-comment').onclick=()=>openEditor('comment');$('#notes-view').onchange=render;$('#notes-archived').onchange=render;
  $('#notes-panel').addEventListener('toggle',()=>{if($('#notes-panel').open)render();});
  const more=$('.player-more');more.classList.remove('hidden');$('.player-more-list').insertAdjacentHTML('beforeend',`<a class="note-cut-link" href="/review/${s.id}/cuts">Review cuts</a>`);
  const toolbar=$('.clip-toolbar .toolbar');toolbar.insertAdjacentHTML('beforeend','<select id="note-clip-filter" aria-label="Clip focus"><option value="all">All clips</option><option value="liked">Liked only</option><option value="highlights">Highlights</option><option value="useful">Hide less useful</option></select>');$('#note-clip-filter').value=model.noteFilter||'all';$('#note-clip-filter').onchange=e=>{model.noteFilter=e.target.value;renderClipList();};
  refresh();timer=setInterval(refresh,10000);
 }
 function cuts(s){
  source=s;model.session=s;items=[];generation++;clearInterval(timer);nav();
  $('#main').innerHTML=`<div class="review-breadcrumb"><a href="/review/${s.id}">Back to review</a><span>/</span>Review cuts</div><h1>Review cuts</h1><p>Reports refer to the processed version you reviewed. They do not change your videos.</p><label>Clip<select id="cut-clip" aria-label="Review clip">${s.clips.map(c=>`<option value="${c.id}">Clip ${c.index}</option>`).join('')}</select></label><video class="cut-review-player" id="cut-video" controls playsinline preload="metadata"></video><p id="cut-original"></p><button type="button" class="button primary" id="cut-report">Report this cut</button><section class="notes-panel"><h2>Cut reports</h2><label class="check-label"><input id="notes-archived" type="checkbox">Archived</label><p id="notes-status" role="status"></p><div id="cut-report-list"></div></section>`;
  const choose=()=>{const c=s.clips.find(c=>c.id===$('#cut-clip').value);model.current=c.id;$('#cut-video').src=mediaURL(s,c.file);$('#cut-original').textContent='Original '+preciseTime(c.original_start_seconds)+'–'+preciseTime(c.original_end_seconds);};
  $('#cut-clip').onchange=choose;$('#cut-report').onclick=()=>{$('#cut-video').pause();openEditor('cut_feedback');};$('#notes-archived').onchange=render;choose();refresh();timer=setInterval(refresh,10000);
 }
 return {init,mount,cuts,current,clipBadge,filterClip,refresh};
})();
