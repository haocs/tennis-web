'use strict';
const CutCompare=(()=>{
 let state=null;
 const uid=()=>Array.from({length:32},()=>Math.floor(Math.random()*16).toString(16)).join('');
 const labels={BOTH_RETAINED:'Both kept',ONLY_A_RETAINED:'Only A kept',ONLY_B_RETAINED:'Only B kept',BOTH_REMOVED:'Both removed'};
 const status=text=>{const el=document.querySelector('#compare-status');if(el)el.textContent=text;};
 async function mount(source){
  if(state)dispose(state);
  const owner=state={source,versions:[],a:null,b:null,position:0,deck:'a',gate:new CutCompareCore.SeekGate(),lease:uid(),refs:new Map(),allRefs:new Set(),ready:[],preparing:false,comparisonId:'cmp_'+uid(),revision:0};
  model.session=null;nav();
  $('#main').innerHTML=`<div class="breadcrumb"><a href="/review/${source.id}/cuts">Review cut</a><span>/</span><span>Compare versions</span></div><header class="cut-header"><h1>Compare versions</h1><button type="button" class="button small" id="compare-refresh">Refresh candidates</button></header><div class="compare-versions"><label>A<select id="compare-a" aria-label="Version A"></select></label><label>B<select id="compare-b" aria-label="Version B"></select></label></div><p id="compare-status" role="status"></p><div id="compare-workspace"></div><section class="notes-panel"><h2>Save comparison</h2><label>Preferred version<select id="compare-preferred"><option value="">Undecided</option><option value="a">A</option><option value="b">B</option></select></label><label>Note<textarea id="compare-note" rows="2" maxlength="2000"></textarea></label><label class="check-label"><input type="checkbox" id="compare-retain">Keep both videos for this saved comparison</label><button type="button" class="button primary" id="compare-save">Save comparison</button><p id="compare-save-status" role="status"></p><div id="compare-saved"></div></section>`;
  $('#compare-refresh').onclick=async()=>{try{status('Loading candidate versions…');await api(`/api/processing/videos/${source.id}/versions/import`,{method:'POST',body:'{}'});await loadVersions(owner);status('Candidate versions refreshed.');}catch(e){status(e.message);}};
  $('#compare-a').onchange=()=>choose(owner);$('#compare-b').onchange=()=>choose(owner);
  $('#compare-save').onclick=()=>save(owner);
  await loadVersions(owner);await saved(owner);
  owner.mediaQuery=matchMedia('(max-width:650px)');owner.onResize=()=>{if(state!==owner||!owner.a||!owner.b)return;const preferred=$('#compare-preferred').value;choose(owner,{id:owner.comparisonId,version:owner.revision,note:$('#compare-note').value,preferred_version_id:preferred?owner[preferred].id:null,retain_versions:$('#compare-retain').checked?[owner.a.id,owner.b.id]:[]});};owner.mediaQuery.addEventListener('change',owner.onResize);
  owner.renew=setInterval(()=>{if(state===owner&&!document.hidden)retainOpen(owner);},300000);
  window.addEventListener('pagehide',()=>dispose(owner),{once:true});
 }
 async function loadVersions(owner){
  const data=await api(`/api/processing/videos/${owner.source.id}/versions`);if(state!==owner)return;
  owner.versions=data.versions;owner.current=data.current_version_id;
  for(const deck of ['a','b'])$('#compare-'+deck).innerHTML=owner.versions.map(v=>`<option value="${esc(v.id)}">${v.is_current_serving?'Serving':'Version'} · ${esc(v.id.slice(-8))} · ${v.clip_count} clips</option>`).join('');
  $('#compare-a').value=owner.a?.id||owner.current;$('#compare-b').value=owner.b?.id||owner.versions.find(v=>v.id!==owner.current)?.id||owner.current;
  choose(owner);
 }
 function choose(owner,existing=null){
  if(state!==owner)return;owner.gate.cancel();owner.ready=[];
  document.querySelectorAll('#compare-workspace video').forEach(v=>{v.pause();v.removeAttribute('src');v.load();});
  owner.a=owner.versions.find(v=>v.id===$('#compare-a').value);owner.b=owner.versions.find(v=>v.id===$('#compare-b').value);
  reconcileRefs(owner);
  owner.comparisonId=existing?.id||'cmp_'+uid();owner.revision=existing?.version||0;$('#compare-note').value=existing?.note||'';$('#compare-preferred').value=existing?.preferred_version_id===owner.a?.id?'a':existing?.preferred_version_id===owner.b?.id?'b':'';$('#compare-retain').checked=!!existing?.retain_versions?.length;$('#compare-save-status').textContent='';
  if(!owner.a||!owner.b||owner.a.id===owner.b.id){$('#compare-workspace').innerHTML='<p class="empty">Choose two different versions. Refresh candidates after staging a rerun in the processing portal.</p>';$('#compare-save').disabled=true;return;}
  try{owner.map=CutCompareCore.decompose(owner.a.manifest,owner.b.manifest);}catch(e){status(e.message);$('#compare-workspace').innerHTML='';$('#compare-save').disabled=true;return;}
  $('#compare-save').disabled=false;owner.mobile=matchMedia('(max-width:650px)').matches;
  const decks=owner.mobile?['mobile']:['a','b'];
  $('#compare-workspace').innerHTML=`<div class="compare-transport"><button type="button" class="button small" id="compare-play">Play</button><button type="button" class="button small" id="compare-pause">Pause</button><label>Speed<select id="compare-speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label><button type="button" class="button small" id="compare-deck-a">A · sound</button><button type="button" class="button small" id="compare-deck-b">B · sound</button></div><label>Original recording · <span id="compare-clock"></span><input id="compare-scrub" aria-label="Original recording position" type="range" min="0" max="${owner.map.totalDurationMs}" step="1" value="0"></label><div class="compare-decks">${decks.map(deck=>`<section><h2 id="compare-label-${deck}">${deck==='mobile'?'A':deck.toUpperCase()}</h2><video id="compare-video-${deck}" playsinline preload="auto"></video><p id="compare-slate-${deck}" class="compare-slate"></p></section>`).join('')}</div><div class="compare-deltas"><h2>Original-time ranges</h2>${owner.map.segments.map((s,i)=>`<button type="button" class="button small" data-compare-segment="${i}">${time(s.startMs/1000)}–${time(s.endMs/1000)} · ${labels[s.state]}</button>`).join('')}</div>`;
  $('#compare-scrub').oninput=e=>seek(owner,Number(e.target.value));
  document.querySelectorAll('[data-compare-segment]').forEach(button=>button.onclick=()=>seek(owner,owner.map.segments[Number(button.dataset.compareSegment)].startMs));
  for(const deck of ['a','b'])$('#compare-deck-'+deck).onclick=()=>{owner.deck=deck;seek(owner,owner.position);};
  $('#compare-play').onclick=()=>play(owner);
  $('#compare-pause').onclick=()=>pause(owner);$('#compare-speed').onchange=()=>owner.ready.forEach(x=>x.video.playbackRate=Number($('#compare-speed').value));
  decks.forEach(deck=>{const video=$('#compare-video-'+deck);video.addEventListener('timeupdate',()=>tick(owner,deck));video.addEventListener('ended',()=>{if(video.ended)finishRange(owner);});});
  seek(owner,Math.min(owner.position,owner.map.totalDurationMs));
 }
 function pause(owner){owner.ready.forEach(x=>x.video.pause());}
 function releaseRef(owner,ref,keepalive=false){
  ref.retired=true;
  return fetch(`/api/processing/videos/${owner.source.id}/comparison-lease`,{method:'DELETE',headers:{'Content-Type':'application/json','X-Tennis-Request':'1'},body:JSON.stringify({video_version_id:ref.version,holder_id:ref.holder}),keepalive}).then(response=>{if(response.ok)owner.allRefs.delete(ref);}).catch(()=>{});
 }
 function dispose(owner){
  owner.disposed=true;owner.gate.cancel();pause(owner);clearInterval(owner.renew);owner.mediaQuery?.removeEventListener('change',owner.onResize);
  for(const ref of owner.allRefs)releaseRef(owner,ref,true);
 }
 function reconcileRefs(owner){
  const selected=new Set([owner.a?.id,owner.b?.id].filter(Boolean));
  for(const [version,ref] of owner.refs){if(!selected.has(version)){owner.refs.delete(version);releaseRef(owner,ref);}}
  for(const version of selected){if(!owner.refs.has(version)){const ref={version,holder:uid(),inflight:null,retired:false};owner.refs.set(version,ref);owner.allRefs.add(ref);}}
  retainOpen(owner);  // Both versions, immediately, including gaps and the inactive mobile deck.
 }
 async function acquireRef(owner,ref){
  if(ref.inflight)return ref.inflight;
  ref.inflight=(async()=>{
   try{
    const result=await api(`/api/processing/videos/${owner.source.id}/comparison-lease`,{method:'POST',body:JSON.stringify({video_version_id:ref.version,holder_id:ref.holder})});
    if(state!==owner||owner.disposed||owner.refs.get(ref.version)!==ref){releaseRef(owner,ref,true);return false;}
    return !!result.acknowledged;
   }catch(e){return false;}
  })();
  try{return await ref.inflight;}finally{ref.inflight=null;}
 }
 async function lease(owner,version){
  if(version===owner.current)return true; // Serving has its own durable server protection.
  const ref=owner.refs.get(version);return ref?acquireRef(owner,ref):false;
 }
 async function retainOpen(owner){await Promise.all([...owner.refs.values()].map(ref=>acquireRef(owner,ref)));}
 async function play(owner){
  if(owner.preparing||state!==owner)return;
  let ready=owner.ready.filter(x=>x.ok);if(!ready.length)return;
  let epoch=owner.gate.epoch;const position=owner.position;
  try{
   if(ready.some(x=>x.video.readyState<2)){
    owner.preparing=true;status('Buffering…');
    // A user gesture primes metadata-only iOS decoders, silently and without advancing our clock.
    await Promise.all(ready.map(async x=>{x.video.muted=true;await x.video.play();if(state===owner&&owner.gate.epoch===epoch)x.video.pause();}));
    if(state!==owner||owner.gate.epoch!==epoch)return;
    if(!await seek(owner,position))return;epoch=owner.gate.epoch;
    ready=owner.ready.filter(x=>x.ok);
   }
   for(const item of ready){item.video.muted=item.deck!==owner.deck;item.video.playbackRate=Number($('#compare-speed').value);}
   await Promise.all(ready.map(x=>x.video.play()));
  }catch(e){if(state===owner&&owner.gate.epoch===epoch){owner.preparing=false;pause(owner);status('Playback paused. Tap Play to try again.');}}
 }
 async function finishRange(owner){
  if(state!==owner||owner.preparing)return;pause(owner);
  if(owner.segmentEndMs<owner.map.totalDurationMs){if(await seek(owner,owner.segmentEndMs))status('Range boundary reached. Tap Play to continue.');}
  else status('End of recording.');
 }
 async function seek(owner,position){
  if(state!==owner||!owner.map)return;const epoch=owner.gate.cancel();pause(owner);owner.ready=[];owner.preparing=true;owner.position=Math.max(0,Math.min(position,owner.map.totalDurationMs));
  owner.segmentEndMs=owner.map.segments.find(s=>s.startMs<=owner.position&&owner.position<s.endMs)?.endMs??owner.map.totalDurationMs;
  for(const deck of ['a','b']){$('#compare-deck-'+deck).textContent=deck.toUpperCase()+(deck===owner.deck?' · sound':' · muted');$('#compare-deck-'+deck).setAttribute('aria-pressed',String(deck===owner.deck));}
  $('#compare-clock').textContent=time(owner.position/1000);$('#compare-scrub').value=owner.position;
  status('Preparing playback…');
  const visible=owner.mobile?[owner.deck]:['a','b'];
  await Promise.all(visible.map(async deck=>{
   const entry=owner[deck],projection=CutReviewCore.projectOriginalToPlayback(entry.manifest,owner.position),slot=owner.mobile?'mobile':deck,video=$('#compare-video-'+slot),slate=$('#compare-slate-'+slot);
   $('#compare-label-'+slot).textContent=deck.toUpperCase()+(deck===owner.deck?' · sound':'');video.pause();video.muted=true;
   if(projection.kind!=='clip'){video.hidden=true;video.removeAttribute('src');video.load();slate.textContent='Removed in '+deck.toUpperCase();slate.hidden=false;return;}
   const playable=await lease(owner,entry.id);if(state!==owner||epoch!==owner.gate.epoch)return;
   if(!playable){video.hidden=true;slate.textContent='Kept in '+deck.toUpperCase()+' · retained media unavailable';slate.hidden=false;return;}
   const url=`/media/versions/${entry.id}/${projection.clip.file}?lease_id=${owner.refs.get(entry.id)?.holder||owner.lease}`;
   video.hidden=false;slate.hidden=true;
   const ok=await owner.gate.ready(video,url,projection.localSeconds,epoch);
   if(state!==owner||epoch!==owner.gate.epoch)return;
   if(!ok){video.hidden=true;slate.textContent='Kept in '+deck.toUpperCase()+' · video unavailable or still buffering';slate.hidden=false;}
   owner.ready.push({deck,video,projection,ok});
  }));
  if(state!==owner||epoch!==owner.gate.epoch)return;owner.preparing=false;status('');return true;
 }
 function tick(owner,slot){
  if(state!==owner||owner.preparing)return;
  const ready=owner.ready.filter(x=>x.ok),master=ready.find(x=>x.deck===owner.deck)||ready[0];if(!master||master.video!==$('#compare-video-'+slot)||master.video.paused)return;
  const p=CutReviewCore.projectLocalToOriginal(master.projection.clip,master.video.currentTime,{runtimeDurationSeconds:master.video.duration});if(!p)return;
  owner.position=p.originalMs;$('#compare-clock').textContent=time(p.originalSeconds);$('#compare-scrub').value=p.originalMs;
  if(p.originalMs>=owner.segmentEndMs){finishRange(owner);return;}
  const next=owner.map.segments.find(s=>s.startMs<=p.originalMs&&p.originalMs<s.endMs);
  if(!next||next.state==='BOTH_REMOVED'){pause(owner);return;}
  for(const other of ready){if(other===master)continue;const q=CutReviewCore.projectOriginalToPlayback(owner[other.deck].manifest,p.originalMs);if(q.kind!=='clip'||q.clipId!==other.projection.clipId){pause(owner);status('Range boundary reached. Choose the next range.');return;}if(Math.abs(other.video.currentTime-q.localSeconds)>0.25){pause(owner);seek(owner,p.originalMs);return;}}
 }
 async function save(owner){
  if(state!==owner||!owner.a||!owner.b)return;const button=$('#compare-save'),message=$('#compare-save-status');button.disabled=true;
  const preferred=$('#compare-preferred').value;
  try{
   const result=await api(`/api/processing/videos/${owner.source.id}/comparisons/${owner.comparisonId}`,{method:'PUT',body:JSON.stringify({version:owner.revision,version_a_id:owner.a.id,version_b_id:owner.b.id,preferred_version_id:preferred?owner[preferred].id:null,note:$('#compare-note').value,retain_versions:$('#compare-retain').checked?[owner.a.id,owner.b.id]:[]})});
   if(state!==owner)return;owner.revision=result.version;message.textContent=result.release_pending?.length?'Saved. Some previous media pins still need release in the processing portal.':'Comparison saved. Serving version unchanged.';await saved(owner);
  }catch(e){if(state===owner)message.textContent=e.message;}finally{if(state===owner)button.disabled=false;}
 }
 async function saved(owner){
  const result=await api(`/api/processing/videos/${owner.source.id}/comparisons`);if(state!==owner)return;
  $('#compare-saved').innerHTML=result.items.map((r,i)=>`<button type="button" class="button small" data-saved-comparison="${i}">${esc(r.note.slice(0,70)||'Saved comparison')} · ${r.preferred_version_id?'preference saved':'undecided'}</button>`).join('');
  document.querySelectorAll('[data-saved-comparison]').forEach(button=>button.onclick=()=>{const record=result.items[Number(button.dataset.savedComparison)];$('#compare-a').value=record.version_a_id;$('#compare-b').value=record.version_b_id;choose(owner,record);});
 }
 return {mount};
})();
