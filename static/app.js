'use strict';
const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const model = {sessions:[], practiceGroups:[], courts:[], players:[], homePlayer:'', clipPlayer:'', selectingClips:false, selectedClips:new Set(), session:null, status:null, sort:'original', likedOnly:false, mode:'full', current:null, autoplay:false, query:'', homeSort:'latest', homeView:'sessions', busy:new Set()};
let toastTimer;
function toast(message){ $('#toast').textContent=message; $('#toast').classList.add('visible'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),6000); }
function time(seconds, precise=false){ const n=Math.max(0, Number(seconds)||0); const ms=Math.round(n*1000); const whole=precise?Math.floor(ms/1000):Math.round(n); const h=Math.floor(whole/3600), m=Math.floor(whole%3600/60),s=whole%60; return (h?h+':'+String(m).padStart(2,'0'):String(m))+':'+String(s).padStart(2,'0')+(precise?'.'+String(ms%1000).padStart(3,'0'):''); }
const preciseTime=value=>time(value,true).replace(/0+$/,'').replace(/\.$/,'');
function dateText(s){ const value=s.recorded_at_iso||s.source.recorded_at; if(!value)return 'Date not recorded';const d=new Date(value); if(Number.isNaN(d.valueOf()))return 'Date not recorded'; const match=value.match(/^(\d{4})-(\d{2})-(\d{2})/); if(match){const local=new Date(+match[1],+match[2]-1,+match[3]);return local.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'});} return d.toLocaleDateString(); }
function locationText(s){ if(s.court?.name)return s.court.name;if(s.court_status==='conflict')return 'Multiple courts';if(s.location_label)return s.location_label;const p=s.source.location;return p&&Number.isFinite(p.latitude)&&Number.isFinite(p.longitude)?`${p.latitude.toFixed(4)}°, ${p.longitude.toFixed(4)}°`:'Location not recorded'; }
const mediaURL=(s,file)=>`/media/${s.id}/${file}?v=${encodeURIComponent(s.published_at)}`;
const clipPosterURL=(s,c)=>`/api/sessions/${s.id}/clips/${c.id}/poster?v=${encodeURIComponent(s.published_at)}`;
const posterURL=s=>`/api/sessions/${s.id}/poster?v=${encodeURIComponent(s.published_at)}`;
function likeButton(s,clip=null,extra=''){ const like=clip?clip.like:s.like; return `<button class="like-button ${extra}" data-like-session="${s.id}" data-like-item="${clip?clip.id:'video'}" aria-pressed="${!!like.value}" aria-label="${like.value?'Unlike':'Like'} ${clip?'clip '+clip.index:'video'}" title="${like.value?'Unlike':'Like'}">${ReviewPlayer.icon('thumbup')}</button>`; }
async function api(path, options={}){ const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Tennis-Request':'1',...options.headers}});const data=await response.json(); if(!response.ok){ const error=new Error(data.error||`Request failed (${response.status})`);error.status=response.status;error.current=data.current;throw error;}return data; }
function processingStatusInfo(s=model.status){
 const files=(s&&Array.isArray(s.files))?s.files:[];
 const fileError=files.some(r=>r&&(r.error||r.status==='needs_attention'));
 const hasRetry=files.some(r=>r&&r.status==='retry');
 if(s&&(s.has_error||s.error||fileError||s.service==='Error'||s.service==='Failed'))return {color:'red',label:'Processing attention needed'};
 if(!s||!s.available||s.stale||s.service==='Stopped'||hasRetry)return {color:'yellow',label:!s?'Checking processing status':!s.available?'Processing status unavailable':s.stale?'Processing status is stale':s.service==='Stopped'?'Processing is paused':'Processing waiting to retry'};
 return {color:'green',label:s.stage&&s.stage!=='Idle'?`Processing: ${s.stage}`:'Processing ready'};
}
function updateProcessingDot(){
 const dot=$('#nav-processing-dot'),link=$('#nav-processing'),info=processingStatusInfo();
 if(dot){dot.className=`status-dot status-${info.color}`;dot.dataset.status=info.color;dot.setAttribute('aria-label',info.label);dot.title=info.label;}
 if(link)link.title=info.label;
}
function nav(){
 $('#nav-library')?.classList.toggle('active',!model.session&&location.pathname==='/');
 $('#nav-learning')?.classList.toggle('active',location.pathname==='/learning');
 $('#nav-processing')?.classList.toggle('active',location.pathname==='/processing');
 updateProcessingDot();
}
function queueLabel(row){return ({done:'Finished',waiting:'Waiting for upload to settle',processing:'Processing',retry:'Waiting to retry',needs_attention:'Needs attention'})[row.status]||row.status;}
function statusHTML(){const s=model.status;if(!s)return '<div class="status-panel"><div class="status-copy">Checking processing status…</div></div>'; const info=processingStatusInfo(s);const warning=info.color!=='green';const running=info.color==='green'&&s.service==='Running';let title=info.color==='red'?'Processing attention needed':warning?(s.stale&&s.available?'Processing status is stale':!s.available?'Processing status unavailable':s.service==='Stopped'?'Processing is paused':'Processing waiting to retry'):(s.stage==='Idle'?'Ready':s.stage||'Processing');let detail=s.current_video?`${esc(s.current_video)} · `:'';if(s.progress&&s.progress.total){detail+=`${esc(s.progress.current)} / ${esc(s.progress.total)} ${esc(s.progress.unit)}`;}else detail+=warning?'Check the processing service.':'';const heartbeat=s.heartbeat?new Date(s.heartbeat*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'Unavailable';const pending=(s.files||[]).filter(r=>r.status!=='done');const errors=pending.filter(r=>r.error);const percent=s.progress?.total?Math.min(100,100*s.progress.current/s.progress.total):0;return `<div class="status-panel"><div class="status-orb status-${info.color}${warning?' warning':''}${info.color==='red'?' danger':''}"><span class="dot"></span></div><div class="status-copy"><div class="status-title">${esc(title)}</div><div class="status-subtitle">${detail}</div>${pending.length?`<details class="queue"><summary>${pending.length} video${pending.length===1?'':'s'} in the queue${errors.length?' · attention needed':''}</summary><ul>${pending.map(r=>`<li>${esc(r.name)} — ${esc(queueLabel(r))}${r.error?' · see processing log for details':''}</li>`).join('')}</ul></details>`:''}</div><div class="status-aside">${running?'PROCESSOR ONLINE':warning?'CHECK PROCESSOR':'PROCESSOR PAUSED'}<br>Heartbeat ${esc(heartbeat)}${percent?`<div class="progress-rail"><div class="progress-fill" data-progress="${percent}"></div></div>`:''}</div></div>`;}
function workerStatusHTML(){
 const s=model.status, info=processingStatusInfo(s), warning=info.color!=='green';
 const title=!s?'Checking worker status…':info.color==='red'?'Worker attention needed':warning?(s.stale&&s.available?'Worker status is stale':'Worker status unavailable'):s.service==='Stopped'?'Worker paused':s.stage==='Idle'?'Worker ready':s.stage||'Worker online';
 const detail=s?.current_video||(!warning&&s?.progress?.total?`${s.progress.current} / ${s.progress.total} ${s.progress.unit||''}`:'');
 return `<a class="worker-status${warning?' warning':''}" href="${esc($('#main').dataset.processingUrl)}" aria-label="${esc(title)} — Open Processing Room"><span class="status-orb status-${info.color}${warning?' warning':''}${info.color==='red'?' danger':''}" aria-hidden="true"><span class="dot"></span></span><span class="worker-status-copy"><strong>${esc(title)}</strong>${detail?`<span>${esc(detail)}</span>`:''}</span><span class="worker-status-link">Processing Room ↗</span></a>`;
}
function processingRoom(){
 model.session=null;nav();document.title='Processing Room — Courtside';
 $('#main').innerHTML=`<div class="review-breadcrumb"><a href="/">Library</a><span>/</span>Processing Room</div><h1>Processing Room</h1><section id="processing-status" aria-label="Processing status">${statusHTML()}</section>`;applyBars();
}

function applyBars(){ $$('.progress-fill').forEach(el=>el.style.width=`${el.dataset.progress}%`); }
function isClipHighlighted(c){return Boolean(c&&(c.highlighted||Number(c.highlight_count)>0||(Array.isArray(c.highlights)&&c.highlights.length>0)));}
function allLikesAndHighlights(sessions=model.sessions,playerFilter=''){
 const seen=new Set(),out=[];
 for(const s of sessions){
  for(const c of (s.clips||[])){
   const liked=Boolean(c.like?.value),highlighted=isClipHighlighted(c);
   if((!liked&&!highlighted)||(playerFilter&&!PlayerUI.matches(c,playerFilter)))continue;
   const key=`clip:${s.id}:${c.id}`;if(seen.has(key))continue;seen.add(key);
   out.push({kind:'clip',key,s,c,liked,highlighted});
  }
  if(s.like?.value&&(!playerFilter||PlayerUI.matches(s,playerFilter))){
   const key=`video:${s.id}`;if(!seen.has(key)){seen.add(key);out.push({kind:'video',key,s,c:null,liked:true,highlighted:false});}
  }
 }
 return out;
}
function homeViewFromLocation(){
 const h=location.hash;
 if(h==='#clips')return 'clips';
 if(h==='#practice'||h==='#videos')return 'practice';
 if(h==='#likes'||h.startsWith('#liked'))return 'likes';
 return 'sessions';
}
function setHomeView(view,push=true){
 model.homeView=view;
 const targetUrl=view==='sessions'?'/':`/#${view}`;
 if(push&&(location.pathname+location.hash)!==targetUrl)history.pushState({homeView:view},'',targetUrl);
 if($('#session-grid'))homeResults();else home();
}
function card(s,clip=null,opts={}){
 const isLiked=Boolean(clip?clip.like?.value:s.like?.value),isHighlighted=Boolean(clip&&isClipHighlighted(clip));
 const href=`/review/${s.id}${clip?'?clip='+clip.id+(opts.likedQuery?'&liked=1':''):''}`;
 const reasonTags=[];if(opts.showReason||isHighlighted){if(isLiked)reasonTags.push('LIKED');if(isHighlighted)reasonTags.push('★ HIGHLIGHT');}
 const reasonAttr=isLiked&&isHighlighted?'liked-highlight':isHighlighted?'highlight':isLiked?'liked':'';
 return `<article class="session-card" data-card-kind="${clip?'clip':'video'}" data-session-id="${s.id}"${clip?` data-clip-id="${clip.id}"`:''}${reasonAttr?` data-card-reason="${reasonAttr}"`:''}><a class="card-visual" href="${href}" aria-label="Review ${esc(s.title)}${clip?' clip '+clip.index:''}"><img class="${clip?'clip-poster':''}" src="${clip?clipPosterURL(s,clip):posterURL(s)}" alt="Tennis court from ${esc(s.title)}" loading="lazy"><div class="card-badges"><span>${clip?'CLIP '+String(clip.index).padStart(2,'0'):s.clips.length+' CLIPS'}${s.video.hdr?' · HDR':''}${reasonTags.length?' · '+reasonTags.join(' · '):''}</span><span>${time(clip?clip.duration_seconds:s.video.duration_seconds)}</span></div></a><div class="card-body"><div class="card-date">${esc(dateText(s))}</div><div class="card-title-row"><h3><a href="${href}">${esc(s.title)}${clip?' · '+String(clip.index).padStart(2,'0'):''}</a></h3>${likeButton(s,clip)}</div><div class="card-location">⌾ <span>${esc(locationText(s))}</span></div><div class="player-chips">${PlayerUI.avatars(clip?clip.players:s.players)}</div><div class="card-footer"><span>${clip?'Original '+time(clip.original_start_seconds)+'–'+time(clip.original_end_seconds):time(s.source.duration_seconds)+' recorded · '+time(s.removed_seconds)+' removed'}</span><a href="${href}">${clip?'Play clip':'Review video'} ↗</a></div></div></article>`;
}
function recordingClock(s){const match=(s.recorded_at_iso||'').match(/T(\d{2}):(\d{2})/);if(!match)return 'Time not recorded';const hour=Number(match[1]);return `${hour%12||12}:${match[2]} ${hour>=12?'pm':'am'}`;}
function originalOrder(a,b,direction=1){const ta=a.recorded_at_epoch,tb=b.recorded_at_epoch;if(ta==null||tb==null)return ta==null?(tb==null?a.id.localeCompare(b.id):1):-1;return direction*(ta-tb)||a.id.localeCompare(b.id);}
function practiceEntries(){const byId=new Map(model.sessions.map(s=>[s.id,s]));return model.practiceGroups.map(g=>({...g,recordings:g.recording_ids.map(id=>byId.get(id)).filter(Boolean)})).filter(g=>g.recordings.length);}
function practiceCard(g){const recordings=g.recordings,first=recordings[0],duration=recordings.reduce((n,s)=>n+s.video.duration_seconds,0),clips=recordings.reduce((n,s)=>n+s.clips.length,0);return `<article class="session-card practice-card" data-practice="${g.id}"><div class="practice-visual"><a class="card-visual" href="/review/${first.id}" aria-label="Review first recording from ${esc(g.title)}"><img src="${posterURL(first)}" alt="Tennis court from ${esc(first.title)}" loading="lazy"><div class="card-badges"><span>${recordings.length} ${recordings.length===1?'RECORDING':'RECORDINGS'} · ${clips} CLIPS</span><span>${time(duration)}</span></div></a><details class="practice-options"><summary aria-label="Options for ${esc(g.title)}" title="Session options"><span aria-hidden="true">⋯</span></summary><div class="practice-options-list"><button type="button" data-edit-practice="${g.id}">Session details</button><button type="button" data-practice-players="${g.id}">Players</button><button type="button" data-choose-court="${g.id}">${g.court?'Change court':'Name or choose court'}</button></div></details></div><div class="card-body"><div class="card-title-row"><h3>${esc(g.title)}</h3></div><div class="card-location">⌾ <span>${esc(g.court?.name||CourtUI.gpsText(g.location))}</span></div><div class="player-chips">${PlayerUI.avatars(g.players)}</div>${g.metadata?.note?`<p class="session-note">${esc(g.metadata.note)}</p>`:''}<div class="practice-recordings" aria-label="Recordings in this practice">${recordings.map(v=>`<div class="practice-recording"><a href="/review/${v.id}" aria-label="Review ${esc(v.title)}"><strong>${esc(v.title)}</strong><span>${esc(recordingClock(v))} · ${time(v.video.duration_seconds)} of play${v.video.hdr?' · HDR':''}</span></a>${likeButton(v)}</div>`).join('')}</div></div></article>`;}
async function editPractice(identity){
 const group=model.practiceGroups.find(g=>g.id===identity);if(!group)return;
 let dialog=$('#practice-editor');if(!dialog){
  document.body.insertAdjacentHTML('beforeend',`<dialog id="practice-editor" aria-labelledby="practice-editor-title"><form id="practice-form"><div class="dialog-heading"><h2 id="practice-editor-title">Session details</h2><button type="button" class="icon-button" id="practice-close" aria-label="Close session details">×</button></div><label>Title<input id="practice-title" maxlength="160"></label><label>Note<textarea id="practice-note" rows="3" maxlength="2000"></textarea></label><p id="practice-error" class="error-message" role="alert"></p><button type="submit" class="button primary" id="practice-save">Save</button></form></dialog>`);dialog=$('#practice-editor');$('#practice-close').onclick=()=>dialog.close();
 }
 $('#practice-title').value=group.metadata?.title||'';$('#practice-title').placeholder=group.title;$('#practice-note').value=group.metadata?.note||'';$('#practice-error').textContent='';
 $('#practice-form').onsubmit=async event=>{event.preventDefault();$('#practice-save').disabled=true;
  try{await api(`/api/practices/${identity}/metadata`,{method:'PUT',body:JSON.stringify({version:group.metadata_version,title:$('#practice-title').value,note:$('#practice-note').value})});dialog.close();await refreshShared();}
  catch(error){$('#practice-error').textContent=error.message;}finally{$('#practice-save').disabled=false;}
 };dialog.showModal();
}
document.addEventListener('click',event=>{const button=event.target.closest('[data-edit-practice]');if(button){button.closest('details').open=false;editPractice(button.dataset.editPractice);}});
function home(){
 model.session=null;model.homeView=homeViewFromLocation();nav();
 const totalClips=model.sessions.reduce((n,s)=>n+s.clips.length,0);
 const totalVideos=model.sessions.length;
 const minutes=Math.round(model.sessions.reduce((n,s)=>n+s.video.duration_seconds,0)/60);
 const totalLikes=allLikesAndHighlights(model.sessions).length;
 const playerCount=model.players.length,courtCount=model.courts.length;
 $('#main').innerHTML=`<h1 class="sr-only" id="home-page-title">Your tennis library</h1><section class="library-overview" aria-label="Library overview"><div class="intro-stats" role="group" aria-label="Library views"><button type="button" class="summary-item" id="summary-sessions" data-home-view="sessions"><strong id="stat-sessions">${model.practiceGroups.length}</strong><span>Sessions</span></button><button type="button" class="summary-item" id="summary-clips" data-home-view="clips"><strong id="stat-clips">${totalClips}</strong><span>Clips</span></button><button type="button" class="summary-item" id="summary-practice" data-home-view="practice"><span class="summary-value-row"><strong id="stat-minutes">${minutes}<small>m</small></strong><small class="summary-subcount" id="stat-videos">${totalVideos} ${totalVideos===1?'video':'videos'}</small></span><span>Practice</span></button><button type="button" class="summary-item" id="summary-likes" data-home-view="likes"><strong id="stat-likes">${totalLikes}</strong><span>Likes &amp; highlights</span></button></div><div class="summary-entities" role="group" aria-label="Library entities"><button type="button" class="summary-item summary-entity" id="manage-players"><span class="summary-value-row" id="stat-players-row"><strong id="stat-players">${playerCount}</strong>${PlayerUI.avatarStack(model.players)}</span><span id="label-players">${playerCount===1?'Player':'Players'}</span></button><button type="button" class="summary-item summary-entity" id="manage-courts"><strong id="stat-courts">${courtCount}</strong><span id="label-courts">${courtCount===1?'Court':'Courts'}</span></button></div></section>${model.libraryAvailable===false?'<div class="banner">The published library is not connected. Configure its storage location to display completed sessions.</div>':''}${model.invalidSessions?'<div class="banner">Some session metadata could not be loaded. Valid sessions are still shown.</div>':''}<section><div class="section-heading"><h2 id="home-section-title">Sessions</h2><span class="count" id="result-count"></span><div class="toolbar"><label class="search-box"><span class="search-icon" aria-hidden="true">⌕</span><input type="search" id="search" placeholder="Find a session, clip, or location" aria-label="Search sessions" value="${esc(model.query)}"></label>${PlayerUI.filter('home-player-filter',model.homePlayer)}<label><span class="hidden">Sort sessions</span><select id="home-sort" aria-label="Sort sessions"><option value="latest">Newest first</option><option value="oldest">Oldest first</option><option value="longest">Longest first</option><option value="shortest">Shortest first</option></select></label></div></div><div class="sessions-grid" id="session-grid"></div></section>`;
 $$('[data-home-view]').forEach(b=>b.addEventListener('click',()=>setHomeView(b.dataset.homeView,true)));
 $('#search').addEventListener('input',e=>{model.query=e.target.value;homeResults();});
 $('#home-sort').value=model.homeSort;$('#home-sort').addEventListener('change',e=>{model.homeSort=e.target.value;homeResults();});
 $('#home-player-filter').onchange=e=>{model.homePlayer=e.target.value;homeResults();};
 $('#manage-players').onclick=()=>PlayerUI.openSaved();
 $('#manage-courts').addEventListener('click',()=>CourtUI.openSaved());
 homeResults();applyBars();
}
function homeResults(){
 const view=homeViewFromLocation();model.homeView=view;
 const totalClips=model.sessions.reduce((n,s)=>n+s.clips.length,0);
 const totalVideos=model.sessions.length;
 const minutes=Math.round(model.sessions.reduce((n,s)=>n+s.video.duration_seconds,0)/60);
 const totalLikes=allLikesAndHighlights(model.sessions).length;
 const playerCount=model.players.length,courtCount=model.courts.length;
 if($('#stat-sessions')){
  $('#stat-sessions').textContent=model.practiceGroups.length;
  $('#stat-clips').textContent=totalClips;
  $('#stat-minutes').innerHTML=minutes+'<small>m</small>';
  if($('#stat-videos'))$('#stat-videos').textContent=`${totalVideos} ${totalVideos===1?'video':'videos'}`;
  if($('#stat-likes'))$('#stat-likes').textContent=totalLikes;
  if($('#stat-players-row'))$('#stat-players-row').innerHTML=`<strong id="stat-players">${playerCount}</strong>${PlayerUI.avatarStack(model.players)}`;
  if($('#label-players'))$('#label-players').textContent=playerCount===1?'Player':'Players';
  if($('#stat-courts'))$('#stat-courts').textContent=courtCount;
  if($('#label-courts'))$('#label-courts').textContent=courtCount===1?'Court':'Courts';
 }
 $$('[data-home-view]').forEach(b=>{
  const active=b.dataset.homeView===view;
  b.classList.toggle('active',active);
  if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');
 });
 const titles={sessions:'Sessions',clips:'Clips',practice:'Practice',likes:'Likes & highlights'};
 if($('#home-section-title'))$('#home-section-title').textContent=titles[view]||'Sessions';
 const practiceNames=new Map(model.practiceGroups.flatMap(g=>g.recording_ids.map(id=>[id,`${g.title} ${g.court_name||''} ${g.court?.address||''}`])));
 const q=model.query.trim().toLocaleLowerCase();
 const matchesSession=s=>!q||`${practiceNames.get(s.id)||''} ${s.title} ${locationText(s)} ${dateText(s)} ${(s.players||[]).map(p=>p.name).join(' ')}`.toLocaleLowerCase().includes(q);
 const matchesClip=(s,c)=>!q||matchesSession(s)||`${c.title||''} clip ${c.index} clip ${String(c.index).padStart(2,'0')} ${(c.players||[]).map(p=>p.name).join(' ')} ${(c.highlights||[]).map(h=>h.text||'').join(' ')}`.toLocaleLowerCase().includes(q);
 const sortEntries=(a,b)=>{
  const dur=e=>e.c?e.c.duration_seconds:e.s.video.duration_seconds;
  if(model.homeSort==='longest'||model.homeSort==='shortest')return (model.homeSort==='longest'?-1:1)*(dur(a)-dur(b))||originalOrder(a.s,b.s)||((a.c?.index||0)-(b.c?.index||0));
  return originalOrder(a.s,b.s,model.homeSort==='oldest'?1:-1)||(Boolean(a.c)===Boolean(b.c)?((a.c?.index||0)-(b.c?.index||0)):(a.c?-1:1));
 };
 let count=0,markup='',noun='session',emptyTitle='No sessions yet',emptyBody='Processed recordings appear here.';
 if(view==='sessions'){
  let groups=practiceEntries().filter(g=>g.recordings.some(matchesSession)&&PlayerUI.matches(g,model.homePlayer));
  groups.sort((a,b)=>{if(model.homeSort==='longest'||model.homeSort==='shortest'){const duration=g=>g.recordings.reduce((n,s)=>n+s.video.duration_seconds,0);return (model.homeSort==='longest'?-1:1)*(duration(a)-duration(b))||originalOrder(a.recordings[0],b.recordings[0]);}return originalOrder(a.recordings[0],b.recordings[0],model.homeSort==='oldest'?1:-1);});
  count=groups.length;markup=groups.map(practiceCard).join('');noun='session';
 }else if(view==='clips'){
  const entries=model.sessions.flatMap(s=>s.clips.filter(c=>matchesClip(s,c)&&PlayerUI.matches(c,model.homePlayer)).map(c=>({s,c})));
  entries.sort(sortEntries);count=entries.length;markup=entries.map(({s,c})=>card(s,c)).join('');
  noun='clip';emptyTitle='No clips yet';emptyBody='Processed clips appear here.';
 }else if(view==='practice'){
  const list=model.sessions.filter(s=>matchesSession(s)&&PlayerUI.matches(s,model.homePlayer));
  list.sort((a,b)=>{if(model.homeSort==='longest'||model.homeSort==='shortest')return (model.homeSort==='longest'?-1:1)*(a.video.duration_seconds-b.video.duration_seconds)||originalOrder(a,b);return originalOrder(a,b,model.homeSort==='oldest'?1:-1);});
  count=list.length;markup=list.map(s=>card(s,null)).join('');
  noun='video';emptyTitle='No practice videos yet';emptyBody='Processed practice videos appear here.';
 }else{
  let entries=allLikesAndHighlights(model.sessions,model.homePlayer).filter(e=>e.c?matchesClip(e.s,e.c):matchesSession(e.s));
  if(location.hash==='#liked-sessions')entries=entries.filter(e=>!e.c);
  else if(location.hash==='#liked')entries=entries.filter(e=>Boolean(e.c));
  entries.sort(sortEntries);count=entries.length;
  markup=entries.map(e=>card(e.s,e.c,{showReason:true,likedQuery:Boolean(e.c&&e.liked&&!e.highlighted)})).join('');
  noun='item';emptyTitle='No likes or highlights yet';emptyBody='Like a video or clip, or highlight a moment during review.';
 }
 const filtered=Boolean(q||model.homePlayer);
 $('#result-count').textContent=`${count} ${noun}${count===1?'':'s'}`;
 $('#session-grid').innerHTML=count?markup:`<div class="empty"><h3>${filtered?'No matches':emptyTitle}</h3><p>${filtered?'Try another title, location, player, or date.':emptyBody}</p></div>`;
 bindLikes($('#session-grid'));CourtUI.bindCards();
}
async function changeLike(button){const s=model.session?.id===button.dataset.likeSession?model.session:model.sessions.find(s=>s.id===button.dataset.likeSession);if(!s)return;const clip=['session','video'].includes(button.dataset.likeItem)?null:s.clips.find(c=>c.id===button.dataset.likeItem);const target=clip||s;const key=s.id+':'+button.dataset.likeItem;if(model.busy.has(key))return;model.busy.add(key);button.disabled=true;try{target.like=await api(`/api/videos/${s.id}/likes/${button.dataset.likeItem}`,{method:'PUT',body:JSON.stringify({liked:!target.like.value,version:target.like.version,...(clip?{publication:s.published_at}:{})})});}catch(error){if(error.status===409&&error.current)target.like=error.current;toast(error.message);}finally{model.busy.delete(key);button.disabled=false;const homeSession=model.sessions.find(v=>v.id===s.id);if(homeSession&&homeSession!==s){if(clip){const c=homeSession.clips.find(c=>c.id===clip.id);if(c)c.like=target.like;}else homeSession.like=target.like;}refreshLikeButtons();nav();if(model.session){await NoteUI.refresh();renderClipList();}else homeResults();}}
function bindLikes(root=document){$$('[data-like-item]',root).forEach(b=>b.addEventListener('click',()=>changeLike(b)));}
function refreshLikeButtons(){ $$('[data-like-item]').forEach(b=>{const s=model.session?.id===b.dataset.likeSession?model.session:model.sessions.find(s=>s.id===b.dataset.likeSession);const t=['session','video'].includes(b.dataset.likeItem)?s:s?.clips.find(c=>c.id===b.dataset.likeItem);if(t){b.disabled=model.busy.has(s.id+':'+b.dataset.likeItem);b.setAttribute('aria-pressed',String(!!t.like.value));b.innerHTML=ReviewPlayer.icon('thumbup');b.title=t.like.value?'Unlike':'Like';b.setAttribute('aria-label',`${t.like.value?'Unlike':'Like'} ${['session','video'].includes(b.dataset.likeItem)?'video':'clip '+t.index}`);}});}
function filteredClips(){if(!model.session)return [];const list=model.session.clips.filter(c=>NoteUI.filterClip(c)&&PlayerUI.matches(c,model.clipPlayer));return list.sort((a,b)=>model.sort==='original'?a.index-b.index:model.sort==='smart'?(Number(!!b.like?.value)-Number(!!a.like?.value)||Number(!!NoteUI.hasMoments?.(b))-Number(!!NoteUI.hasMoments?.(a))||b.duration_seconds-a.duration_seconds||a.index-b.index):((model.sort==='shortest'?1:-1)*(a.duration_seconds-b.duration_seconds)||a.index-b.index));}
function review(s){model.session=s;nav();const query=new URLSearchParams(location.search);model.likedOnly=query.get('liked')==='1';model.noteFilter=model.likedOnly?'liked':'all';$('#main').innerHTML=`<div class="review-breadcrumb"><a href="/">Library</a><span>/</span><span id="review-kind">Video review</span></div><section class="review-heading"><div><h1 id="review-title">${esc(s.title)}</h1><p id="review-video-context" hidden></p><div class="review-meta"><span>${esc(dateText(s))}</span><span id="review-location">⌾ ${esc(locationText(s))}</span><span id="recording-player-badges" class="player-chips"></span><span>${s.clips.length} clips · ${time(s.video.duration_seconds)} of practice${s.video.hdr?' · HDR':''}</span></div></div><div class="review-actions"><button class="button" id="edit-session">Edit video</button><span id="review-like">${likeButton(s)}</span></div></section><section class="player-shell" aria-label="Practice video player"><div class="player-title"><div><p class="eyebrow" id="player-eyebrow">FULL PRACTICE</p><div id="player-name">${s.clips.length} clips</div></div><button class="button small hidden" id="full-practice">Full practice ↗</button></div>${ReviewPlayer.markup()}<div class="player-footer"><div class="player-navigation"><span class="player-position" id="player-position"></span><div id="clip-player-row" class="clip-player-row" hidden><button type="button" class="player-action" id="clip-main-player"></button><span id="clip-player-default" class="player-inherited"></span></div><div class="player-options"><div class="player-reactions" role="group" aria-label="Video feedback"><span id="player-like"></span><button type="button" class="like-button" id="note-demote" aria-label="Dislike video" aria-pressed="false" title="Dislike">${ReviewPlayer.icon('thumbdown')}</button></div><button type="button" class="player-action hidden" id="study-clip" aria-label="Study clip" title="Study clip">${ReviewPlayer.icon('study')}</button><a class="player-action" id="player-download" download aria-label="Download full practice" title="Download full practice">${ReviewPlayer.icon('download')}</a><details class="player-more${s.video.nas_url?'':' hidden'}"><summary class="player-action" aria-label="More video actions" title="More video actions">${ReviewPlayer.icon('more')}</summary><div class="player-more-list"><button type="button" class="${s.video.nas_url?'':'hidden'}" id="player-nas">NAS file address</button></div></details></div></div></div></section><section><div class="section-heading clip-toolbar"><h2>Clips</h2><span class="count" id="clip-count"></span><div class="toolbar">${PlayerUI.filter('clip-player-filter',model.clipPlayer,'Filter clips by player')}<select id="clip-sort" aria-label="Sort clips"><option value="original">Court order</option><option value="smart">Smart</option><option value="shortest">Shortest first</option><option value="longest">Longest first</option></select></div><details class="clip-actions-menu"><summary class="button" aria-label="Clip list actions" title="Clip list actions">⋯</summary><div><button type="button" id="select-clips" aria-label="Select clips" aria-pressed="false">Select clips</button></div></details></div><div id="clip-selection" class="clip-selection" role="group" aria-label="Selected clips" hidden><label class="clip-select-all"><input id="select-all-clips" type="checkbox" aria-label="Select all shown clips">All</label><span id="selected-clip-count" role="status" aria-live="polite"></span><button type="button" class="button small" id="assign-selected-player" disabled>Assign main player</button></div><div class="clip-list" id="clip-list"></div></section><details class="timeline-section"><summary>Timeline <span aria-hidden="true">+</span></summary><div class="table-scroll"><table class="timeline-table"><thead><tr><th>Decision</th><th>Original start</th><th>Original end</th><th>Duration</th><th>Clip</th></tr></thead><tbody id="timeline-body"></tbody></table></div><div class="artifact-links"><a href="/api/sessions/${s.id}/artifacts/edit-timeline.csv">Download timeline CSV</a><a href="/api/sessions/${s.id}/artifacts/edit-timeline.json">Timeline JSON</a><a href="/review/${s.id}/original">Original generated review ↗</a></div></details>`;
 $('#select-clips').onclick=()=>{$('.clip-actions-menu').open=false;model.selectingClips=!model.selectingClips;if(!model.selectingClips)model.selectedClips.clear();renderClipList();};
 $('#select-all-clips').onchange=e=>{for(const c of filteredClips()){if(e.target.checked)model.selectedClips.add(c.id);else model.selectedClips.delete(c.id);}renderClipList();};
 PlayerUI.bindReview();const player=$('#player');model.mode='full';model.current=s.clips[0]?.id;try{model.autoplay=localStorage.getItem('courtside.autoplay')==='true';}catch(error){model.autoplay=false;}$('#autoplay-next').checked=model.autoplay;player.poster=posterURL(s);player.src=mediaURL(s,s.video.file);model.playerControls=ReviewPlayer.mount({time,onError:toast});updatePlayerDownload();
 player.addEventListener('timeupdate',onTime);player.addEventListener('ended',()=>{if(player.ended&&model.autoplay&&model.mode==='clip')stepClip(1,true);});
 player.addEventListener('error',()=>toast('This video could not be played. Check storage access and whether your browser supports its video format.'));
 $('#previous').addEventListener('click',()=>stepClip(-1));$('#next').addEventListener('click',()=>stepClip(1));$('#autoplay-next').addEventListener('change',e=>{model.autoplay=e.target.checked;try{localStorage.setItem('courtside.autoplay',String(model.autoplay));}catch(error){/* Playback still works when storage is unavailable. */}});
 $('#full-practice').addEventListener('click',()=>{const c=s.clips.find(c=>c.id===model.current);playFull(c?c.playback_start_seconds:0,false);});
 $('#clip-sort').value=model.sort;$('#clip-sort').addEventListener('change',e=>{model.sort=e.target.value;renderClipList();});
 $('#player-nas').addEventListener('click',()=>{const clip=model.mode==='clip'?s.clips.find(c=>c.id===model.current):null;$('.player-more').open=false;showNasAddress(s,clip);});
 $('#edit-session').addEventListener('click',openEdit);bindLikes($('.review-heading'));renderClipList();renderTimeline();
 const requested=s.clips.find(c=>c.id===query.get('clip'));if(requested)playClip(requested.id,false);NoteUI.mount(s);if(requested&&query.has('coach_job')){$('#study-clip').click();$('#study-mode-coach')?.click();$('#coach-step-4')?.click();}
}
function updatePlayerDownload(){
 const s=model.session, clip=model.mode==='clip'?s.clips.find(c=>c.id===model.current):null;
ClipStudy.updateEntry({button:$('#study-clip'),session:s,clip,player:$('#player'),url:clip?mediaURL(s,clip.file):null});
 window.Coach?.watch({session:s,clip,player:$('#player')});
 const link=$('#player-download');link.href=mediaURL(s,clip?clip.file:s.video.file)+'&download=1';
 const label=clip?'Download clip '+String(clip.index).padStart(2,'0'):'Download full practice';
 link.setAttribute('aria-label',label);link.title=label;
 $('#player-nas').classList.toggle('hidden',!(clip?clip.nas_url:s.video.nas_url));
}
function setSource(url, at=0, play=false){
 updatePlayerDownload();
 const player=$('#player');
 if(model.loadHandler)player.removeEventListener('loadedmetadata',model.loadHandler);
 const intent=(model.playerIntent||0)+1;model.playerIntent=intent;
 const apply=()=>{if(model.playerIntent!==intent)return;model.loadHandler=null;player.currentTime=Math.min(at,Number.isFinite(player.duration)?player.duration:at);if(play)player.play().catch(()=>{});};
 if(player.getAttribute('src')===url&&player.readyState>=1){apply();return;}
 model.loadHandler=apply;player.addEventListener('loadedmetadata',apply,{once:true});
 if(player.getAttribute('src')!==url){player.pause();player.src=url;player.load();}
}

function playClip(id,play=true){const s=model.session,c=s.clips.find(c=>c.id===id);if(!c)return;$('#player').poster=clipPosterURL(s,c);model.mode='clip';model.current=id;$('#player-eyebrow').textContent=`CLIP ${String(c.index).padStart(2,'0')}`;$('#player-name').textContent=`${time(c.duration_seconds)} · Original ${preciseTime(c.original_start_seconds)}–${preciseTime(c.original_end_seconds)}`;$('#full-practice').classList.remove('hidden');setSource(mediaURL(s,c.file),0,play);renderClipList();if(play){$('#video-stage').focus({preventScroll:true});$('.player-shell').scrollIntoView({behavior:'smooth',block:'start'});}}
function playFull(at=0,play=true){const s=model.session;$('#player').poster=posterURL(s);model.mode='full';$('#player-eyebrow').textContent='FULL PRACTICE';$('#player-name').textContent=`${s.clips.length} clips`;$('#full-practice').classList.add('hidden');setSource(mediaURL(s,s.video.file),at,play);updatePlayerState();onTime();}
function stepClip(direction,ended=false){const list=filteredClips();const index=list.findIndex(c=>c.id===model.current);let next;if(index<0){next=direction>0?list[0]:list.at(-1);}else next=list[index+direction];if(!next){if(ended)toast('You reached the end of this clip list.');return;}if(model.mode==='full')playFull(next.playback_start_seconds,true);else playClip(next.id,true);}
function onTime(){const player=$('#player');if(!player||player.readyState===0||model.mode!=='full')return;const t=player.currentTime;const c=model.session.clips.find(c=>t>=c.playback_start_seconds-.001&&t<c.playback_end_seconds-.001)||model.session.clips.at(-1);if(c&&c.id!==model.current){model.current=c.id;updatePlayerState();}}
function updatePlayerState(){const s=model.session,list=filteredClips(),index=list.findIndex(c=>c.id===model.current),current=s.clips.find(c=>c.id===model.current);$$('.clip-row').forEach(row=>{const active=row.dataset.clip===model.current;row.classList.toggle('playing',active);if(active)row.setAttribute('aria-current','true');else row.removeAttribute('aria-current');const button=row.querySelector('[data-play]');if(active)button?.setAttribute('aria-current','true');else button?.removeAttribute('aria-current');});$('#video-context').textContent=(model.mode==='clip'?'Clip ':'Full practice · Clip ')+(current?String(current.index).padStart(2,'0'):'—');$('#player-position').textContent=index<0?(current?'Outside this filter':'No clips'):`${index+1} of ${list.length}`;$('#previous').disabled=!list.length||(index===0);$('#next').disabled=!list.length||(index===list.length-1);PlayerUI.current(current);NoteUI.current();const activeClip=model.mode==='clip'?current:null;$('#player-like').innerHTML=likeButton(s,activeClip);bindLikes($('#player-like'));updateReviewHeading();model.playerControls?.updateMediaSession({title:(model.mode==='clip'?'Clip ':'Full practice · Clip ')+(current?String(current.index).padStart(2,'0'):'—'),album:s.title});}
function renderClipList(){
 const active=document.activeElement,focusId=active?.dataset.play||active?.dataset.selectClip,focusKind=active?.dataset.play?'play':'selectClip';
 const list=filteredClips();$('#clip-count').textContent=`${list.length} ${model.noteFilter==='liked'?'liked ':''}clip${list.length===1?'':'s'}`;
 const shown=new Set(list.map(c=>c.id));for(const id of model.selectedClips)if(!shown.has(id))model.selectedClips.delete(id);
 $('#clip-list').innerHTML=list.length?list.map(c=>`<div class="clip-row ${c.id===model.current?'playing':''}${model.selectedClips.has(c.id)?' selected':''}" data-clip="${c.id}"><label class="clip-select" ${model.selectingClips?'':'hidden'}><input type="checkbox" data-select-clip="${c.id}" aria-label="Select clip ${c.index}" ${model.selectedClips.has(c.id)?'checked':''}></label><button type="button" class="clip-play" data-play="${c.id}" aria-label="Play clip ${c.index}, ${time(c.duration_seconds)}${c.main_player?' · '+esc(c.main_player.name):''}" ${c.id===model.current?'aria-current="true"':''}><span class="clip-thumbnail"><img class="clip-poster" src="${clipPosterURL(model.session,c)}" alt="" loading="lazy" decoding="async" width="80" height="45"></span><span class="clip-details"><span class="clip-title-line"><span class="clip-name">${esc(c.title||'Clip '+String(c.index).padStart(2,'0'))}</span>${NoteUI.clipBadge(c)}${PlayerUI.avatars(c.players)}${c.player_assignment?.needs_review?'<span class="player-inherited" title="Cut changed · confirm player">!</span>':''}</span><span class="clip-times"><span title="In full practice">${time(c.playback_start_seconds)}</span><span title="Original ${preciseTime(c.original_start_seconds)}–${preciseTime(c.original_end_seconds)}">Original ${preciseTime(c.original_start_seconds)}–${preciseTime(c.original_end_seconds)}</span></span></span><span class="duration" title="Clip duration">${time(c.duration_seconds)}</span></button></div>`).join(''):'<div class="empty"><h3>No matching clips</h3><p>Change the player or liked filter to see more clips.</p></div>';
 $$('[data-play]').forEach(b=>b.addEventListener('click',()=>playClip(b.dataset.play)));
 $$('[data-select-clip]').forEach(box=>box.onchange=()=>{if(box.checked)model.selectedClips.add(box.dataset.selectClip);else model.selectedClips.delete(box.dataset.selectClip);box.closest('.clip-row').classList.toggle('selected',box.checked);updateClipSelection();});
 updateClipSelection();updatePlayerState();
 if(focusId){const control=$$('[data-play],[data-select-clip]').find(el=>el.dataset[focusKind]===focusId);control?.focus({preventScroll:true});}
}
function updateClipSelection(){
 const active=model.selectingClips,visible=filteredClips(),count=model.selectedClips.size;
 $('#clip-selection').hidden=!active;$('#select-clips').textContent=active?'Finish selection':'Select clips';$('#select-clips').setAttribute('aria-pressed',String(active));
 $('#selected-clip-count').textContent=`${count} selected`;$('#assign-selected-player').disabled=!count;
 const all=$('#select-all-clips');all.checked=!!visible.length&&count===visible.length;all.indeterminate=count>0&&count<visible.length;all.disabled=!visible.length;
}

function renderTimeline(){const s=model.session,rows=[];let cursor=0;function row(action,a,b,clip=null){return `<tr><td><span class="decision ${action==='Preserved'?'kept':'cut'}">${action}</span></td><td>${preciseTime(a)}</td><td>${preciseTime(b)}</td><td>${preciseTime(b-a)}</td><td>${clip?String(clip.index).padStart(2,'0'):'—'}</td></tr>`;}for(const c of s.clips){if(c.original_start_seconds>cursor)rows.push(row('Cut',cursor,c.original_start_seconds));rows.push(row('Preserved',c.original_start_seconds,c.original_end_seconds,c));cursor=c.original_end_seconds;}if(cursor<s.source.duration_seconds)rows.push(row('Cut',cursor,s.source.duration_seconds));$('#timeline-body').innerHTML=rows.join('');}
function showNasAddress(s,clip=null){const address=clip?clip.nas_url:s.video.nas_url;if(!address)return;$('#nas-file-name').textContent=`${s.title} · ${clip?'Clip '+String(clip.index).padStart(2,'0'):'Full practice'}`;$('#nas-address').value=address;$('#nas-copy-status').textContent='';$('#nas-dialog').showModal();}
$('#close-nas').addEventListener('click',()=>$('#nas-dialog').close());
$('#copy-nas').addEventListener('click',async()=>{const field=$('#nas-address');try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(field.value);}else{field.focus();field.select();if(!document.execCommand('copy'))throw new Error('Manual copy needed');}$('#nas-copy-status').textContent='Address copied.';}catch(error){field.focus();field.select();$('#nas-copy-status').textContent='Select and copy the address above.';}});
function updateReviewHeading(){
 const s=model.session;if(!s||!$('#review-title'))return;const clip=model.mode==='clip'?s.clips.find(c=>c.id===model.current):null;
 $('#review-title').textContent=clip?.title||s.title;$('#review-kind').textContent=clip?'Clip review':'Video review';
 $('#review-video-context').hidden=!clip;$('#review-video-context').textContent=clip?'From '+s.title:'';
 $('#edit-session').textContent=clip?'Edit clip':'Edit video';$('#review-like').innerHTML=likeButton(s,clip);bindLikes($('#review-like'));
 document.title=`${clip?clip.title+' · ':''}${s.title} — Courtside`;
}
function openEdit(){
 if(model.editSaving)return;
 const s=model.session,clip=model.mode==='clip'?s.clips.find(c=>c.id===model.current):null;
 model.editGen=(model.editGen||0)+1;
 model.editTarget={video_id:s.id,clip_id:clip?.id||null,publication:s.published_at};
 model.editVersion=clip?clip.details.version:s.overrides.version;
 model.editInitialTitle=(clip?clip.title:s.title).trim();
 model.editInitialLocation=(s.location_label||'').trim();
 $('#edit-dialog-title').textContent=clip?'Clip details':'Video details';$('#edit-context').textContent=clip?s.title+' · Original '+preciseTime(clip.original_start_seconds)+'–'+preciseTime(clip.original_end_seconds):s.title;
 $('#edit-title').value=clip?clip.title:s.title;$('#edit-location').value=s.location_label||'';$('#edit-location-row').hidden=!!clip;$('#edit-video-players').hidden=!!clip;$('#edit-clip-players').hidden=!clip;
 $('#edit-player-badges').innerHTML=PlayerUI.avatars(s.recording_players,true);$('#edit-clip-player-badges').innerHTML=PlayerUI.avatars(clip?.players||[],true);
 $('#clip-detail-player-edit').onclick=()=>PlayerUI.openClip(model.editTarget.clip_id);
 NoteUI.openEntityNotes(model.editTarget);
 $('#edit-error').textContent='';$('#edit-load-latest-note').hidden=true;$('#edit-dialog').showModal();
}
$('#close-edit').addEventListener('click',()=>{if(!model.editSaving)$('#edit-dialog').close();});
$('#edit-dialog').addEventListener('cancel',event=>{if(model.editSaving)event.preventDefault();});
$('#edit-form').addEventListener('submit',async event=>{
 event.preventDefault();if(model.editSaving)return;
 const s=model.session,editGen=model.editGen,target={...model.editTarget},editVersion=model.editVersion;
 const title=$('#edit-title').value.trim(),location=$('#edit-location').value.trim();
 const clip=target.clip_id?s.clips.find(c=>c.id===target.clip_id):null;
 const detailsChanged=title!==model.editInitialTitle||(!clip&&location!==model.editInitialLocation);
 const noteSnapshot=NoteUI.captureEditNote(target);
 const noteChanged=noteSnapshot.changed;
 const button=$('button[type=submit]',$('#edit-form'));
 model.editSaving=true;button.disabled=true;$('#close-edit').disabled=true;
 let detailsSaved=false;
 try{
  if(s.id!==target.video_id||(target.clip_id&&(!clip||s.published_at!==target.publication))||noteSnapshot.mismatch)throw new Error('This clip changed. Close the editor and refresh before saving.');
  if(detailsChanged||!noteChanged){
   const path=target.clip_id?`/api/videos/${target.video_id}/clips/${target.clip_id}/details`:`/api/videos/${target.video_id}/details`;
   const body={title,version:editVersion,...(clip?{publication:target.publication}:{location})};
   const result=await api(path,{method:'PUT',body:JSON.stringify(body)});
   if(clip){clip.details=result;clip.title=result.value.title;}else{s.overrides=result;s.title=result.value.title;s.location_label=result.value.location;}
   if(model.editGen===editGen){model.editVersion=result.version;model.editInitialTitle=result.value.title;if(!clip)model.editInitialLocation=result.value.location||'';}
   updateReviewHeading();$('#review-location').textContent='⌾ '+locationText(s);renderClipList();
   detailsSaved=detailsChanged;
  }
  if(noteChanged)await NoteUI.saveEditNote(noteSnapshot);
  if(model.editGen===editGen)$('#edit-dialog').close();
  toast(clip?'Clip details saved.':'Video details saved.');
 }catch(error){if(model.editGen===editGen)$('#edit-error').textContent=detailsSaved?`Details saved, but note could not be saved: ${error.message}`:error.message;/* Keep the captured entity and draft on conflict. */}
 finally{model.editSaving=false;button.disabled=false;$('#close-edit').disabled=false;}
});
async function refreshStatus(){try{model.status=await api('/api/status');updateProcessingDot();const panel=$('#processing-status');if(panel){panel.innerHTML=location.pathname==='/processing'?statusHTML():workerStatusHTML();applyBars();}}catch(error){model.status={available:false,error:true};updateProcessingDot();const panel=$('#processing-status');if(panel){panel.innerHTML=location.pathname==='/processing'?statusHTML():workerStatusHTML();}}}
async function refreshShared(){if(document.hidden||['/processing','/learning'].includes(location.pathname))return;try{if(model.session){const fresh=await api(`/api/sessions/${model.session.id}`);const playersChanged=JSON.stringify([fresh.players,fresh.player_assignment,fresh.clips.map(c=>[c.main_player,c.player_assignment])])!==JSON.stringify([model.session.players,model.session.player_assignment,model.session.clips.map(c=>[c.main_player,c.player_assignment])]);const changed=playersChanged||fresh.clips.some((c,i)=>c.like.version!==model.session.clips[i]?.like.version||c.details.version!==model.session.clips[i]?.details.version||c.highlighted!==model.session.clips[i]?.highlighted||c.highlight_count!==model.session.clips[i]?.highlight_count)||fresh.like.version!==model.session.like.version;model.session.players=fresh.players;model.session.recording_players=fresh.recording_players;model.session.player_assignment=fresh.player_assignment;model.session.like=fresh.like;model.session.overrides=fresh.overrides;model.session.title=fresh.title;model.session.location_label=fresh.location_label;model.session.court=fresh.court;model.session.court_status=fresh.court_status;model.session.court_name=fresh.court_name;fresh.clips.forEach(c=>{const current=model.session.clips.find(v=>v.id===c.id);if(current){current.like=c.like;current.highlighted=c.highlighted;current.highlight_count=c.highlight_count;current.highlights=c.highlights;current.title=c.title;current.details=c.details;current.main_player=c.main_player;current.players=c.players;current.player_assignment=c.player_assignment;}});updateReviewHeading();$('#review-location').textContent='⌾ '+locationText(fresh);if(changed){if(playersChanged){model.players=(await api('/api/players')).players;PlayerUI.syncReview();updatePlayerDownload();}refreshLikeButtons();renderClipList();}}else{const data=await api('/api/sessions');const fingerprint=JSON.stringify([data.players,data.courts,data.practice_groups,data.sessions.map(s=>[s.id,s.published_at,s.recorded_at_iso,s.like,s.overrides,s.players,s.player_assignment,s.clips.map(c=>[c.like,c.highlighted,c.highlight_count,c.main_player,c.player_assignment])])]);if(fingerprint!==model.fingerprint){model.fingerprint=fingerprint;model.sessions=data.sessions;model.practiceGroups=data.practice_groups||[];model.courts=data.courts||[];model.players=data.players||[];model.libraryAvailable=data.library_available;model.invalidSessions=data.invalid_sessions;PlayerUI.syncHome();homeResults();nav();CourtUI.renderSaved();}}}catch(error){/* Keep the current player and last loaded library during a transient outage. */}}
window.addEventListener('hashchange',()=>{if(!model.session&&location.pathname==='/'){model.homeView=homeViewFromLocation();if($('#session-grid'))homeResults();else home();}});
window.addEventListener('popstate',()=>{if(!model.session&&location.pathname==='/'){model.homeView=homeViewFromLocation();if($('#session-grid'))homeResults();else home();}});
window.addEventListener('focus',()=>{refreshStatus();refreshShared();});
async function boot(){try{const [data,status]=await Promise.all([api('/api/sessions'),api('/api/status').catch(()=>({available:false,error:true}))]);model.sessions=data.sessions;model.practiceGroups=data.practice_groups||[];model.courts=data.courts||[];model.players=data.players||[];model.libraryAvailable=data.library_available;model.invalidSessions=data.invalid_sessions;model.status=status;updateProcessingDot();const match=location.pathname.match(/^\/review\/([a-f0-9]{64})(\/cuts)?$/);if(match){const s=await api(`/api/sessions/${match[1]}`);if(match[2])NoteUI.cuts(s);else review(s);if(match[2])document.title=`${s.title} — Courtside`;}else if(location.pathname==='/processing')processingRoom();else if(location.pathname==='/learning')LearningUI.mount();else home();setInterval(refreshStatus,15000);setInterval(refreshShared,10000);}catch(error){$('#main').innerHTML=`<div class="empty"><p class="eyebrow">COURTSIDE</p><h1>We couldn’t open the library.</h1><p>${esc(error.message)}</p><a href="/" class="button primary">Try again</a></div>`;}}
document.addEventListener('error',event=>{if(event.target.matches?.('img.clip-poster')){event.target.hidden=true;event.target.parentElement.classList.add('thumbnail-unavailable');}},true);
CourtUI.init();
PlayerUI.init();
NoteUI.init();
boot();
