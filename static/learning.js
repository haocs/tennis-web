'use strict';
const LearningUI=(()=>{
 let generation=0,selected='';
 function mount(){
  model.session=null;nav();document.title='Learning — Courtside';selected=new URLSearchParams(location.search).get('player')||'';
  $('#main').innerHTML=`<h1>Learning</h1><div class="learning-toolbar"><label>Player<select id="learning-player" aria-label="Learning player"><option value="">Choose a player</option>${model.players.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label></div><p id="learning-status" role="status"></p><div id="learning-clips" class="learning-clips"></div>`;
  if(!model.players.some(p=>p.id===selected))selected='';$('#learning-player').value=selected;
  $('#learning-player').onchange=e=>{selected=e.target.value;const url=new URL(location.href);if(selected)url.searchParams.set('player',selected);else url.searchParams.delete('player');history.replaceState(null,'',url);load();};load();
 }
 async function load(){
  const rev=++generation,pid=selected,box=$('#learning-clips'),status=$('#learning-status');box.replaceChildren();
  if(!pid){status.textContent=model.players.length?'Choose a player to see clips with coaching reviews.':'Add a player from the library to get started.';return;}
  status.textContent='Loading coaching reviews…';const entries=new Map();let after='';
  try{
   do{const data=await api('/api/learning?player_id='+encodeURIComponent(pid)+(after?'&after='+encodeURIComponent(after):''));if(rev!==generation)return;
    for(const item of data.entries){const existing=entries.get(item.id);if(existing){const ids=new Set(existing.reviews.map(r=>r.id));existing.reviews.push(...item.reviews.filter(r=>!ids.has(r.id)));}else entries.set(item.id,item);}after=data.next_after;
   }while(after);
   const rows=[...entries.values()].sort((a,b)=>(Date.parse(b.recorded_at)||0)-(Date.parse(a.recorded_at)||0)||a.source_id.localeCompare(b.source_id)||a.original_start-b.original_start);
   status.textContent=rows.length?`${rows.length} clip${rows.length===1?'':'s'} with coaching reviews`:'No coaching reviews for this player yet.';
   box.innerHTML=rows.map(card).join('');
  }catch(e){if(rev===generation)status.textContent='Could not load coaching reviews. '+e.message;}
 }
 function card(c){
  return `<article class="learning-card"><div class="learning-card-media">${c.media_available?`<a href="/review/${c.source_id}?clip=${c.clip_id}" aria-label="Watch ${esc(c.title)} clip ${c.clip_index}"><img src="${esc(c.poster_url)}" alt="" loading="lazy"><span>${time(c.duration_seconds)}</span></a>`:'<div class="learning-unavailable">Clip unavailable · review retained</div>'}</div><div class="learning-card-body"><h2>${esc(c.title)}${c.clip_index?' · Clip '+c.clip_index:''}</h2><p class="learning-time">${esc(c.recorded_at?dateText({source:{recorded_at:c.recorded_at}}):'Date not recorded')} · Original ${preciseTime(c.original_start)}–${preciseTime(c.original_end)}</p>${c.reviews.sort((a,b)=>b.updated_at-a.updated_at).map(r=>`<section class="learning-review"><h3>Coach review · ${preciseTime(r.original_start)}–${preciseTime(r.original_end)}</h3>${r.historical?'<p class="learning-context">Earlier processed version</p>':''}${r.player_mismatch?'<p class="learning-context">Current clip has a different or unassigned player. Confirm the focus before using this advice.</p>':''}${r.association==='current_clip'?'<p class="learning-context">Player from current clip assignment</p>':''}<p>${esc(r.summary)}</p><details><summary>Read saved response</summary><p class="learning-answer">${esc(r.answer)}</p>${r.answer_truncated?`<a href="${esc(r.response_url)}">Full saved response</a>`:''}</details>${c.media_available?`<a class="button small" href="/review/${c.source_id}?clip=${c.clip_id}&coach_time=${Math.max(c.original_start,Math.min(c.original_end,r.original_start))}&coach_job=${encodeURIComponent(r.id)}">Study & review coaching</a>`:''}</section>`).join('')}</div></article>`;
 }
 return {mount};
})();
