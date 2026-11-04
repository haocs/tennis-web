'use strict';
const PlayerUI = (() => {
 let target=null,editing=null,selected=new Set(),openedVersion=null;
 const levels={kids:'Kids',beginner:'Beginner',intermediate:'Intermediate',experienced:'Experienced',pro:'Pro'};
 const initials=p=>p.name.trim().split(/\s+/).slice(0,2).map(s=>[...s][0]).join('').toLocaleUpperCase();
 function avatars(players=[]){return players.map(p=>`<span class="player-chip" title="${esc(p.name)}"><span class="player-avatar" aria-hidden="true">${esc(initials(p))}</span><span>${esc(p.name)}</span></span>`).join('');}
 function options(value){return `<option value="">All players</option>${model.players.map(p=>`<option value="${p.id}"${value===p.id?' selected':''}>${esc(p.name)}</option>`).join('')}`;}
 function filter(id,value,label='Filter by player'){return `<select id="${id}" aria-label="${label}">${options(value)}</select>`;}
 function syncHome(){const field=$('#home-player-filter');if(field)field.innerHTML=options(model.homePlayer);}
 function matches(item,id){return !id||(item.players||[]).some(p=>p.id===id);}
 async function reload(){
  const data=await api('/api/sessions');model.players=data.players||[];model.sessions=data.sessions;model.practiceGroups=data.practice_groups||[];
  if(model.session){
   const fresh=await api(`/api/sessions/${model.session.id}`);
   Object.assign(model.session,fresh);syncReview();renderClipList();updatePlayerDownload();
  }else homeResults();
  const homeFilter=$('#home-player-filter');if(homeFilter)homeFilter.innerHTML=options(model.homePlayer);renderSaved();
 }
 function renderSaved(){
  $('#saved-player-list').innerHTML=model.players.length?model.players.map(p=>`<div class="saved-player-row"><div>${avatars([p])}<p>${esc([p.height_cm==null?'':p.height_cm+' cm',levels[p.level]||''].filter(Boolean).join(' · '))}</p></div><button class="button small" type="button" data-edit-player="${p.id}">Edit</button></div>`).join(''):'<p>No players saved yet.</p>';
 }
 async function openSaved(){try{model.players=(await api('/api/players')).players;renderSaved();$('#players-dialog').showModal();}catch(e){toast(e.message);}}
 function renderChoices(){
  $('#player-choices').innerHTML=model.players.length?model.players.map(p=>`<label class="player-choice"><input type="${target.kind==='clip'?'radio':'checkbox'}" name="chosen-player" value="${p.id}"${selected.has(p.id)?' checked':''}>${avatars([p])}</label>`).join(''):'<p>Add a player to get started.</p>';
  if(target.kind==='clip')$('#player-choices').insertAdjacentHTML('afterbegin',`<label class="player-choice"><input type="radio" name="chosen-player" value=""${!selected.size?' checked':''}>Unassigned</label>`);
 }
 async function openPicker(kind,id){
  if(model.session)$('#player')?.pause();
  try{
   await reload();target={kind,id};let ids=[];
   if(kind==='practice'){
    const g=model.practiceGroups.find(g=>g.id===id);if(!g)throw new Error('Session changed. Please select it again.');
    target.versions={...g.player_versions};ids=g.players.map(p=>p.id);
   }else if(kind==='recording'){
    target.version=model.session.player_assignment.version;ids=model.session.recording_players.map(p=>p.id);
   }else{
    const c=model.session.clips.find(c=>c.id===id);if(!c)throw new Error('Clip changed. Please select it again.');
    target.version=c.player_assignment.version;target.publication=model.session.published_at;ids=c.main_player?[c.main_player.id]:[];
   }
   selected=new Set(ids);$('#player-picker-title').textContent=kind==='clip'?'Main player for this clip':kind==='practice'?'Session players':'Recording players';
   $('#player-picker-help').textContent=kind==='clip'?'Choose the player the camera focuses on.':(kind==='practice'?'Applies to every recording in this session. ':'')+'A sole recording player is the default for unassigned clips. Clip-specific selections stay unchanged.';
   $('#player-picker-error').textContent='';renderChoices();$('#player-picker').showModal();
  }catch(e){toast(e.message);}
 }
 function openEditor(player=null){
  editing=player;openedVersion=player?.version;$('#player-editor-title').textContent=player?'Edit player':'Add player';
  $('#player-profile-name').value=player?.name||'';$('#player-height').value=player?.height_cm??'';$('#player-level').value=player?.level||'';$('#player-editor-error').textContent='';$('#player-editor').showModal();
 }
 function syncReview(){
  if(!model.session)return;
  const s=model.session;
  $('#recording-player-badges').innerHTML=avatars(s.players);
  const filterEl=$('#clip-player-filter');if(filterEl){filterEl.innerHTML=options(model.clipPlayer);filterEl.value=model.clipPlayer||'';}
 }
 function current(clip){
  const row=$('#clip-player-row');if(!row)return;
  row.hidden=model.mode!=='clip'||!clip;
  $('#clip-main-player').innerHTML=clip?.main_player?avatars([clip.main_player]):'<span>Assign main player</span>';
  $('#clip-main-player').setAttribute('aria-label',`Main player for clip ${clip?.index||''}: ${clip?.main_player?.name||'unassigned'}`);
  $('#clip-player-default').textContent=clip?.player_assignment?.needs_review?'Cut changed · confirm player':clip?.player_assignment?.inherited?'From recording':'';
 }
 function bindReview(){
  $('#recording-player-edit').onclick=()=>openPicker('recording',model.session.id);
  $('#clip-main-player').onclick=()=>openPicker('clip',model.current);
  $('#clip-player-filter').onchange=e=>{model.clipPlayer=e.target.value;renderClipList();};syncReview();
 }
 function init(){
  const box=document.createElement('div');box.innerHTML=`
  <dialog id="players-dialog" aria-labelledby="players-title"><div class="dialog-heading"><h2 id="players-title">Saved players</h2><button type="button" class="icon-button" data-player-close="players-dialog" aria-label="Close saved players">×</button></div><div id="saved-player-list"></div><button type="button" class="button primary" id="player-add">Add player</button></dialog>
  <dialog id="player-picker" aria-labelledby="player-picker-title"><form id="player-picker-form"><div class="dialog-heading"><h2 id="player-picker-title">Players</h2><button type="button" class="icon-button" data-player-close="player-picker" aria-label="Close player selection">×</button></div><p id="player-picker-help"></p><fieldset id="player-choices"><legend class="sr-only">Select players</legend></fieldset><p id="player-picker-error" class="error-message" role="alert"></p><div class="court-form-actions"><button type="button" class="button" id="player-picker-add">Add player</button><button type="submit" class="button primary">Save players</button></div></form></dialog>
  <dialog id="player-editor" aria-labelledby="player-editor-title"><form id="player-editor-form"><div class="dialog-heading"><h2 id="player-editor-title">Add player</h2><button type="button" class="icon-button" data-player-close="player-editor" aria-label="Close player editor">×</button></div><label>Name<input id="player-profile-name" maxlength="100" required></label><label>Height (cm, optional)<input id="player-height" type="number" min="30" max="260" step="0.1"></label><label>Level<select id="player-level" aria-label="Level"><option value="">Not specified</option>${Object.entries(levels).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label><p id="player-editor-error" class="error-message" role="alert"></p><button type="submit" class="button primary">Save player</button></form></dialog>`;
  document.body.append(box);
  document.addEventListener('click',e=>{
   const b=e.target.closest('[data-player-close],[data-edit-player],[data-practice-players]');if(!b)return;
   if(b.dataset.playerClose)$('#'+b.dataset.playerClose).close();
   else if(b.dataset.editPlayer)openEditor(model.players.find(p=>p.id===b.dataset.editPlayer));
   else {b.closest('details').open=false;openPicker('practice',b.dataset.practicePlayers);}
  });
  $('#player-choices').onchange=e=>{if(e.target.name!=='chosen-player')return;if(target.kind==='clip')selected.clear();if(e.target.checked&&e.target.value)selected.add(e.target.value);else selected.delete(e.target.value);};
  $('#player-add').onclick=()=>openEditor();$('#player-picker-add').onclick=()=>openEditor();
  $('#player-editor-form').onsubmit=async e=>{
   e.preventDefault();const b=$('button[type=submit]',e.target);b.disabled=true;
   try{
    const value={name:$('#player-profile-name').value.trim(),height_cm:$('#player-height').value===''?null:Number($('#player-height').value),level:$('#player-level').value||null,...(editing?{version:openedVersion}:{})};
    const p=await api('/api/players'+(editing?'/'+editing.id:''),{method:editing?'PUT':'POST',body:JSON.stringify(value)});
    await reload();$('#player-editor').close();if($('#player-picker').open){if(!editing){if(target.kind==='clip')selected.clear();selected.add(p.id);}renderChoices();}
   }catch(error){$('#player-editor-error').textContent=error.message;}finally{b.disabled=false;}
  };
  $('#player-picker-form').onsubmit=async e=>{
   e.preventDefault();const b=$('button[type=submit]',e.target);b.disabled=true;
   try{
    let url,body;
    if(target.kind==='clip'){url=`/api/sessions/${model.session.id}/clips/${target.id}/player`;body={player_id:[...selected][0]||null,version:target.version,publication:target.publication};}
    else if(target.kind==='practice'){url=`/api/practices/${target.id}/players`;body={player_ids:[...selected],versions:target.versions};}
    else {url=`/api/sessions/${target.id}/players`;body={player_ids:[...selected],version:target.version};}
    await api(url,{method:'PUT',body:JSON.stringify(body)});await reload();$('#player-picker').close();
   }catch(error){$('#player-picker-error').textContent=error.message;}finally{b.disabled=false;}
  };
 }
 return {init,avatars,filter,syncHome,matches,openSaved,bindReview,syncReview,current};
})();
