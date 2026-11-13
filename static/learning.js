'use strict';
const LearningUI=(()=>{
 let playerEpoch=0,loadSeq=0,selected='',activeTab='reviews',pollTimer=null;
 let cachedEntries=[],cachedPlanState=null,editingLogId='',editDraft=null,addingManual=false,manualDraft=null,planError='';
 const planSubmittingByPlayer=new Map();

 const STATUS_LABELS={
  not_started:'Not started',
  ongoing:'Ongoing',
  practiced:'Practiced',
  working_well:'Working well',
  archived:'Archived',
 };

 function stopPolling(){
  if(pollTimer){clearTimeout(pollTimer);pollTimer=null;}
 }

 function isPlanSubmitting(pid=selected){
  return Boolean(pid&&planSubmittingByPlayer.has(pid));
 }

 function newManualDraft(pid=selected){
  return {
   request_id:'prac-'+(pid||'player').slice(0,8)+'-'+Date.now()+'-'+Math.random().toString(36).slice(2,8),
   title:'',
   topic:'',
   status:'ongoing',
   cue:'',
   drill:'',
   note:'',
   error:'',
   submitting:false,
  };
 }

 function newEditDraft(item){
  return {
   logId:item.id,
   version:item.version,
   title:item.title||'',
   topic:item.topic||'',
   status:item.status||'ongoing',
   cue:item.cue||'',
   drill:item.drill||'',
   note:item.note||'',
   error:'',
   submitting:false,
  };
 }

 function defaultPlanState(){
  return {
   enabled:false,
   active_plan:null,
   revisions:[],
   practice_logs:[],
   eligibility:{
    can_generate:false,
    reason:'no_reviews',
    expected_parent_revision:null,
    input_digest:'',
    valid_reviews_count:0,
    new_or_revised_reviews_count:0,
    removed_reviews_count:0,
    practice_log_changes_count:0,
    backlog_reviews_count:0,
    backlog_practice_logs_count:0,
    days_since_last_plan:null,
    recommended_cadence_days:14,
    requires_ambiguous_retry:false,
    last_attempt_invalid:false,
    last_invalid_warnings:[],
   },
   pending_job:null,
  };
 }

 function syncUrl(){
  const url=new URL(location.href);
  if(selected)url.searchParams.set('player',selected);
  else url.searchParams.delete('player');
  if(activeTab==='plan')url.searchParams.set('tab','plan');
  else url.searchParams.delete('tab');
  history.replaceState(null,'',url);
 }

 function mount(){
  stopPolling();
  playerEpoch++;
  model.session=null;nav();document.title='Learning — Courtside';
  const params=new URLSearchParams(location.search);
  selected=params.get('player')||'';
  activeTab=params.get('tab')==='plan'?'plan':'reviews';
  editingLogId='';editDraft=null;addingManual=false;manualDraft=null;planError='';
  if(!model.players.some(p=>p.id===selected))selected='';
  $('#main').innerHTML=`<h1>Learning</h1>
   <div class="learning-toolbar">
    <label>Player<select id="learning-player" aria-label="Learning player"><option value="">Choose a player</option>${model.players.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label>
    <div id="learning-tabs" class="learning-tabs" role="tablist" aria-label="Learning sections" ${selected?'':'hidden'}>
     <button type="button" role="tab" id="learning-tab-reviews" class="learning-tab" aria-selected="${activeTab==='reviews'?'true':'false'}" aria-controls="learning-panel-reviews">Reviews</button>
     <button type="button" role="tab" id="learning-tab-plan" class="learning-tab" aria-selected="${activeTab==='plan'?'true':'false'}" aria-controls="learning-panel-plan">Plan</button>
    </div>
   </div>
   <p id="learning-status" role="status"></p>
   <section id="learning-panel-reviews" role="tabpanel" aria-labelledby="learning-tab-reviews" ${activeTab==='reviews'?'':'hidden'}>
    <div id="learning-clips" class="learning-clips"></div>
   </section>
   <section id="learning-panel-plan" role="tabpanel" aria-labelledby="learning-tab-plan" ${activeTab==='plan'?'':'hidden'}>
    <div id="learning-plan-root" class="learning-plan-root"></div>
   </section>`;
  $('#learning-player').value=selected;
  $('#learning-player').onchange=e=>{
   playerEpoch++;
   selected=e.target.value;
   editingLogId='';editDraft=null;addingManual=false;manualDraft=null;planError='';
   syncUrl();
   updateTabsUI();
   load();
  };
  $('#learning-tab-reviews').onclick=()=>switchTab('reviews');
  $('#learning-tab-plan').onclick=()=>switchTab('plan');
  load();
 }

 function switchTab(tab){
  if(tab!=='reviews'&&tab!=='plan')return;
  activeTab=tab;
  syncUrl();
  updateTabsUI();
  updateStatusText();
 }

 function updateTabsUI(){
  const tabs=$('#learning-tabs'),revBtn=$('#learning-tab-reviews'),planBtn=$('#learning-tab-plan');
  const revPanel=$('#learning-panel-reviews'),planPanel=$('#learning-panel-plan');
  if(!tabs||!revBtn||!planBtn||!revPanel||!planPanel)return;
  tabs.hidden=!selected;
  revBtn.setAttribute('aria-selected',activeTab==='reviews'?'true':'false');
  planBtn.setAttribute('aria-selected',activeTab==='plan'?'true':'false');
  revPanel.hidden=activeTab!=='reviews';
  planPanel.hidden=activeTab!=='plan';
 }

 function updateStatusText(){
  const status=$('#learning-status');
  if(!status)return;
  if(!selected){
   status.hidden=false;
   status.textContent=model.players.length?'Choose a player to see clips with coaching reviews.':'Add a player from the library to get started.';
   return;
  }
  if(activeTab==='plan'){
   status.hidden=true;
   status.textContent='';
   return;
  }
  status.hidden=false;
  const count=cachedEntries.length;
  status.textContent=count?`${count} clip${count===1?'':'s'} with coaching reviews`:'No coaching reviews for this player yet.';
 }

 async function load(opts={}){
  const isPoll=Boolean(opts.isPoll);
  stopPolling();
  const epoch=playerEpoch,seq=++loadSeq,pid=selected;
  const box=$('#learning-clips'),planRoot=$('#learning-plan-root'),status=$('#learning-status');
  if(!isPoll){
   if(box)box.replaceChildren();
   if(planRoot)planRoot.replaceChildren();
   cachedEntries=[];cachedPlanState=null;
  }
  updateTabsUI();
  if(!pid){
   updateStatusText();
   return;
  }
  if(!isPoll&&status&&activeTab==='reviews'){
   status.hidden=false;
   status.textContent='Loading coaching reviews…';
  }
  const entries=new Map();
  let after='',firstPlanState=null;
  try{
   do{
    const data=await api('/api/learning?player_id='+encodeURIComponent(pid)+(after?'&after='+encodeURIComponent(after):''));
    if(epoch!==playerEpoch||seq!==loadSeq||pid!==selected)return;
    if(!after&&data.plan_state)firstPlanState=data.plan_state;
    for(const item of (data.entries||[])){
     const existing=entries.get(item.id);
     if(existing){
      const ids=new Set(existing.reviews.map(r=>r.id));
      existing.reviews.push(...item.reviews.filter(r=>!ids.has(r.id)));
     }else entries.set(item.id,item);
    }
    after=data.next_after;
   }while(after);
   const rows=[...entries.values()].sort((a,b)=>newestAskedAt(b)-newestAskedAt(a)||(Date.parse(b.recorded_at)||0)-(Date.parse(a.recorded_at)||0)||a.source_id.localeCompare(b.source_id)||a.original_start-b.original_start||String(a.id||'').localeCompare(String(b.id||'')));
   cachedEntries=rows;
   cachedPlanState=firstPlanState||defaultPlanState();
   const revTab=$('#learning-tab-reviews'),planTab=$('#learning-tab-plan');
   const totalReviews=rows.reduce((sum,c)=>sum+(c.reviews||[]).length,0);
   if(revTab)revTab.textContent=totalReviews?`Reviews (${totalReviews})`:'Reviews';
   if(planTab)planTab.textContent='Plan';
   if(box)box.innerHTML=rows.map(card).join('');
   renderPlanTab({preservePracticeDom:isPoll&&(addingManual||Boolean(editingLogId))});
   updateStatusText();
   const pending=cachedPlanState.pending_job;
   if(pending&&['queued','leased','calling'].includes(pending.state)){
    pollTimer=setTimeout(()=>{if(epoch===playerEpoch&&pid===selected)load({isPoll:true});},3000);
   }
  }catch(e){
   if(epoch===playerEpoch&&seq===loadSeq&&!isPoll&&status){
    status.hidden=false;
    status.textContent='Could not load coaching reviews. '+e.message;
   }
  }
 }

 function reviewSortTime(r){
  return Number.isFinite(r?.asked_at)?r.asked_at:(Number.isFinite(r?.updated_at)?r.updated_at:0);
 }
 function newestAskedAt(c){
  return (c?.reviews||[]).reduce((max,r)=>Math.max(max,reviewSortTime(r)),-Infinity);
 }
 function fmtTimestamp(ts){
  if(Number.isFinite(ts)){
   const d=new Date(ts*1000);
   if(!Number.isNaN(d.valueOf()))return d.toLocaleString(undefined,{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'});
  }
  return '';
 }
 function askedLabel(r){
  const text=fmtTimestamp(r?.asked_at);
  return text?'Asked '+text:'Ask time not recorded';
 }

 function card(c){
  return `<article class="learning-card"><div class="learning-card-media">${c.media_available?`<a href="/review/${c.source_id}?clip=${c.clip_id}" aria-label="Watch ${esc(c.title)} clip ${c.clip_index}"><img src="${esc(c.poster_url)}" alt="" loading="lazy"><span>${time(c.duration_seconds)}</span></a>`:'<div class="learning-unavailable">Clip unavailable · review retained</div>'}</div><div class="learning-card-body"><h2>${esc(c.title)}${c.clip_index?' · Clip '+c.clip_index:''}</h2><p class="learning-time">${esc(c.recorded_at?dateText({source:{recorded_at:c.recorded_at}}):'Date not recorded')} · Original ${preciseTime(c.original_start)}–${preciseTime(c.original_end)}</p>${c.reviews.sort((a,b)=>(reviewSortTime(b)-reviewSortTime(a))||String(a.id||'').localeCompare(String(b.id||''))).map(r=>reviewSection(c,r)).join('')}</div></article>`;
 }

 function reviewSection(c,r){
  const suggestions=(r.suggestions||[]).filter(s=>s.status!=='dismissed');
  const chips=suggestions.length?`<div class="learning-cue-chips">${suggestions.slice(0,3).map(s=>`<span class="learning-cue-chip learning-cue-${esc(s.status)}"><strong>${esc(s.title||s.topic||'Cue')}:</strong> ${esc(s.cue)} <em>(${s.status==='accepted'?'Accepted':'Proposed'})</em></span>`).join('')}</div>`:'';
  return `<section class="learning-review"><h3>Coach review · ${preciseTime(r.original_start)}–${preciseTime(r.original_end)}</h3><p class="learning-context learning-asked-at">${esc(askedLabel(r))}</p>${r.historical?'<p class="learning-context">Earlier processed version</p>':''}${r.player_mismatch?'<p class="learning-context">Current clip has a different or unassigned player. Confirm the focus before using this advice.</p>':''}${r.selected_override?`<p class="learning-context">Selected player for this review${r.subject_hint?' · Target: '+esc(r.subject_hint):''}</p>`:(r.subject_hint?`<p class="learning-context">Target: ${esc(r.subject_hint)}</p>`:'')}${r.association==='current_clip'?'<p class="learning-context">Player from current clip assignment</p>':''}${chips}<p>${esc(r.summary)}</p>${r.answer!==r.summary?`<details><summary>Read saved response</summary><p class="learning-answer">${esc(r.answer)}</p>${r.answer_truncated?`<a href="${esc(r.response_url)}">Full saved response</a>`:''}</details>`:''}${c.media_available?`<a class="button small" href="/review/${c.source_id}?clip=${c.clip_id}&coach_time=${Math.max(c.original_start,Math.min(c.original_end,r.original_start))}&coach_end=${Math.max(c.original_start,Math.min(c.original_end,r.original_end))}&coach_job=${encodeURIComponent(r.id)}">Study & review coaching</a>`:''}</section>`;
 }

 function renderCitationPill(eid,citations){
  const c=(citations||{})[eid];
  if(!c){
   return `<span class="learning-citation learning-citation-invalid" title="Unresolved citation">${esc(eid)}</span>`;
  }
  const trustLabel=c.provenance_trust==='user_confirmed'?'User confirmed':(c.provenance_trust==='unconfirmed_ai_proposal'?'Unconfirmed AI proposal':'Saved response');
  const timeText=Number.isFinite(c.original_start)&&Number.isFinite(c.original_end)?`${preciseTime(c.original_start)}–${preciseTime(c.original_end)}`:'';
  const label=`${c.title||c.recording_title||'Review'}${timeText?' · '+timeText:''}`;
  if(!c.currently_valid){
   const reasonText=c.revision_changed?'Updated review':(c.invalid_reason||'Outdated');
   return `<span class="learning-citation learning-citation-invalid" title="${esc(reasonText)}">${esc(label)} · ${esc(reasonText)}</span>`;
  }
  if(c.study_url){
   return `<a class="learning-citation learning-citation-${esc(c.review_status)}" href="${esc(c.study_url)}" title="${esc(trustLabel)}">${esc(label)} · ${esc(trustLabel)} ↗</a>`;
  }
  return `<span class="learning-citation learning-citation-${esc(c.review_status)}" title="${esc(trustLabel)}">${esc(label)} · ${esc(trustLabel)}</span>`;
 }

 function renderBanner(ps){
  const el=ps.eligibility||defaultPlanState().eligibility;
  const ap=ps.active_plan;
  const pending=ps.pending_job;
  const submitting=isPlanSubmitting(selected);
  const isInFlight=Boolean(pending&&['queued','leased','calling'].includes(pending.state));
  const isNeedsReview=Boolean(pending&&pending.state==='needs_review');
  const isFailed=Boolean(pending&&pending.state==='failed');
  const isInvalidLast=Boolean(el.last_attempt_invalid);
  const showActions=Boolean(ps.enabled&&(el.can_generate||isNeedsReview||isFailed||submitting));

  if(!isInFlight&&!isNeedsReview&&!isFailed&&!isInvalidLast&&!showActions){
   let statusLine='';
   if(!ps.enabled){
    statusLine='Coaching AI worker is offline · Saved plan and manual practice tracking remain available.';
   }else if(el.reason==='no_reviews'){
    statusLine='Complete a coaching review on a clip to generate an AI learning plan, or track practice items below.';
   }else{
    statusLine=`Plan up to date · Updated ${fmtTimestamp(ap?.updated_at)||'recently'}`;
   }
   return `<section id="learning-plan-banner" class="learning-plan-banner learning-plan-banner-compact">
    <p class="learning-context" id="learning-banner-headline">${esc(statusLine)}</p>
    ${planError?`<p class="error-message" id="learning-plan-error" role="alert">${esc(planError)}</p>`:''}
   </section>`;
  }

  let headline='',subtext='';
  if(isInFlight){
   headline=`Updating learning plan (${pending.state})…`;
   subtext='Progress updates automatically while the request is active.';
  }else if(isNeedsReview){
   headline='Previous plan request expired after calling the provider and needs review.';
   subtext='Check the worker journal before retrying so you do not repeat a completed call.';
  }else if(isFailed){
   headline='Previous learning plan request failed.';
   subtext=(pending.result&&pending.result.error)||'Check the coaching AI worker and try again.';
  }else if(isInvalidLast){
   headline='Last plan update returned an unreadable response and was not applied.';
   subtext=ap?'Your active plan and practice progress were preserved. You can retry the update now.':'Your practice progress was preserved. You can retry generating the plan now.';
  }else if(ap){
   headline='New coaching reviews or practice notes are ready to fold into your plan.';
   subtext=`Last updated ${fmtTimestamp(ap.updated_at)||'earlier'}.`;
  }else{
   headline='Ready to generate a learning plan from your saved coaching reviews.';
   subtext='Synthesizes recurring focus areas, drills, and next clips to record.';
  }

  const buttonLabel=isNeedsReview?'Confirm & retry plan':((isFailed||isInvalidLast)?'Retry learning plan':(ap?'Update learning plan':'Generate learning plan'));
  const canClick=Boolean(ps.enabled&&!submitting&&!isInFlight&&(el.can_generate||isNeedsReview||isFailed));
  const canCancel=Boolean(pending&&['queued','leased'].includes(pending.state)&&!submitting);

  return `<section id="learning-plan-banner" class="learning-plan-banner">
   <div class="learning-banner-info">
    <p class="learning-banner-headline" id="learning-banner-headline">${esc(headline)}</p>
    ${subtext?`<p class="learning-context">${esc(subtext)}</p>`:''}
    ${planError?`<p class="error-message" id="learning-plan-error" role="alert">${esc(planError)}</p>`:''}
   </div>
   <div class="learning-banner-actions">
    ${isInFlight?`
     ${canCancel?`<button type="button" class="button small" id="learning-plan-cancel">Cancel request</button>`:''}
    `:`
     <label class="check-label learning-consent-label"><input type="checkbox" id="learning-plan-consent" ${canClick?'':'disabled'}>Send selected review summaries and practice progress to OpenAI</label>
     <button type="button" class="button primary small" id="learning-plan-generate" ${canClick?'':'disabled'}>${esc(submitting?'Submitting…':buttonLabel)}</button>
    `}
   </div>
  </section>`;
 }

 function renderInlinePracticeForm(item){
  const draft=(editDraft&&editDraft.logId===item.id)?editDraft:newEditDraft(item);
  return `<form class="learning-practice-form" data-log-id="${esc(item.id)}" data-version="${draft.version}">
   <div class="learning-form-grid">
    <label>Title<input type="text" name="title" maxlength="120" required value="${esc(draft.title)}"></label>
    <label>Topic<input type="text" name="topic" maxlength="80" value="${esc(draft.topic)}"></label>
    <label>Status<select name="status" aria-label="Practice status">
     ${Object.entries(STATUS_LABELS).map(([val,label])=>`<option value="${val}" ${draft.status===val?'selected':''}>${esc(label)}</option>`).join('')}
    </select></label>
   </div>
   <label>Cue<input type="text" name="cue" maxlength="400" value="${esc(draft.cue)}"></label>
   <label>Drill<textarea name="drill" rows="2" maxlength="1200">${esc(draft.drill)}</textarea></label>
   <label>Practice note<textarea name="note" rows="2" maxlength="1000" placeholder="How did this feel in practice?">${esc(draft.note)}</textarea></label>
   <p class="error-message learning-practice-item-error" role="alert" ${draft.error?'':'hidden'}>${esc(draft.error||'')}</p>
   <div class="learning-form-actions">
    <button type="submit" class="button primary small" ${draft.submitting?'disabled':''}>${draft.submitting?'Saving…':'Save progress'}</button>
    <button type="button" class="button small learning-cancel-practice-btn" data-log-id="${esc(item.id)}" ${draft.submitting?'disabled':''}>Cancel</button>
   </div>
  </form>`;
 }

 function renderFocusCard(f,citations,matchedLog){
  const item=matchedLog||{
   id:f.log_id||'',
   item_key:f.item_key||'',
   origin:'ai_plan',
   title:f.title,
   topic:f.topic||'',
   cue:f.cue||'',
   drill:f.drill||'',
   status:'not_started',
   note:'',
   version:1,
   revisions:[],
  };
  const hasLog=Boolean(matchedLog&&matchedLog.id);
  const isEditing=hasLog&&editingLogId===item.id;
  const statusLabel=STATUS_LABELS[item.status]||item.status;
  const revs=item.revisions||[];
  const detailLines=[];
  if(item.drill||f.drill)detailLines.push(`<p><strong>Drill:</strong> ${esc(item.drill||f.drill)}</p>`);
  if(f.observed_issue)detailLines.push(`<p><strong>Observed:</strong> ${esc(f.observed_issue)}</p>`);
  if(f.uncertainty)detailLines.push(`<p class="learning-context"><strong>Uncertainty:</strong> ${esc(f.uncertainty)}</p>`);

  return `<article class="learning-focus-card ${hasLog?'learning-practice-item':''}" ${hasLog?`data-log-id="${esc(item.id)}"`:''} data-item-key="${esc(f.item_key||item.item_key||'')}">
   <div class="learning-practice-head">
    <div>
     <span class="learning-topic-badge">${esc(item.topic||f.topic||'Focus')}</span>
     ${hasLog?`<span class="learning-status-badge learning-status-${esc(item.status)}">${esc(statusLabel)}</span>`:''}
     <h4>${esc(item.title||f.title)}</h4>
    </div>
    ${hasLog&&!isEditing?`<button type="button" class="button small learning-edit-practice-btn" data-log-id="${esc(item.id)}">Edit progress</button>`:''}
   </div>
   ${isEditing?renderInlinePracticeForm(item):`
    <p><strong>Cue:</strong> ${esc(item.cue||f.cue)}</p>
    ${item.note?`<p class="learning-practice-note"><strong>Note:</strong> ${esc(item.note)}</p>`:''}
    ${(f.evidence_ids||[]).length?`<div class="learning-citations-row">${f.evidence_ids.map(id=>renderCitationPill(id,citations)).join('')}</div>`:''}
   `}
   ${(detailLines.length||revs.length)?`
    <details class="learning-practice-history">
     <summary>Drill &amp; history${revs.length?` (${revs.length})`:''}</summary>
     <div class="learning-focus-details-body">
      ${detailLines.join('')}
      ${revs.length?`<ul class="learning-history-list">${revs.map(r=>`<li><strong>v${r.version}</strong> · ${r.actor==='human'?'Player':'Plan'} · <span class="learning-status-badge learning-status-${esc(r.status)}">${esc(STATUS_LABELS[r.status]||r.status)}</span>${r.note?` — “${esc(r.note)}”`:''} <span class="learning-context">(${esc(fmtTimestamp(r.created_at)||'recorded')})</span></li>`).join('')}</ul>`:''}
     </div>
    </details>
   `:''}
  </article>`;
 }

 function renderActivePlan(ps){
  const ap=ps.active_plan;
  if(!ap||!ap.plan){
   return `<section class="learning-plan-empty"><p>No AI learning plan has been generated for this player yet.</p></section>`;
  }
  const p=ap.plan;
  const citations=ap.citations||{};
  const logs=ps.practice_logs||[];
  const logsById=new Map(logs.map(l=>[l.id,l]));
  const logsByKey=new Map(logs.filter(l=>l.item_key).map(l=>[l.item_key,l]));
  const warnings=p.validation_warnings||[];
  const focusList=p.focus_areas||[];
  const focusCitationIds=new Set(focusList.flatMap(f=>f.evidence_ids||[]));
  const summaryEids=(p.summary_evidence_ids||[]).filter(id=>!focusList.length||!focusCitationIds.has(id));
  const summaryCitations=summaryEids.map(id=>renderCitationPill(id,citations)).join('');
  const uncertainties=p.uncertainties||[];
  const unassessed=p.unassessed_areas||[];
  const nextReqs=p.next_evidence_requests||[];

  return `<section id="learning-active-plan" class="learning-active-plan">
   ${warnings.length?`<div class="learning-plan-warnings" role="status"><strong>Note:</strong> ${esc(warnings.join(' · '))}</div>`:''}
   <div class="learning-plan-section learning-plan-summary-section">
    <h3>Current focus &amp; summary</h3>
    <p class="learning-plan-prose">${esc(p.observed_summary||'No summary text available.')}</p>
    ${p.progress_notes?`<p class="learning-context learning-progress-notes">${esc(p.progress_notes)}</p>`:''}
    ${summaryCitations?`<div class="learning-citations-row">${summaryCitations}</div>`:''}
   </div>
   ${focusList.length?`
    <div class="learning-plan-section">
     <div class="learning-focus-grid">
      ${focusList.map(f=>{
       const matched=(f.log_id&&logsById.get(f.log_id))||(f.item_key&&logsByKey.get(f.item_key))||null;
       return renderFocusCard(f,citations,matched);
      }).join('')}
     </div>
    </div>
   `:''}
   ${nextReqs.length?`
    <div class="learning-plan-section">
     <h4>Next clips to record</h4>
     <ul class="learning-plan-list">${nextReqs.map(r=>`<li>${esc(r)}</li>`).join('')}</ul>
    </div>
   `:''}
   ${(uncertainties.length||unassessed.length)?`
    <details class="learning-plan-limits">
     <summary>Sampling limits &amp; unassessed skills</summary>
     <div class="learning-focus-details-body">
      ${uncertainties.length?`<p class="learning-context"><strong>Uncertainties:</strong> ${esc(uncertainties.join(' '))}</p>`:''}
      ${unassessed.length?`<p class="learning-context"><strong>Not yet observed in reviewed clips:</strong> ${esc(unassessed.join(' · '))}</p>`:''}
     </div>
    </details>
   `:''}
  </section>`;
 }

 function renderPracticeItem(item){
  const isEditing=editingLogId===item.id;
  const statusLabel=STATUS_LABELS[item.status]||item.status;
  const revs=item.revisions||[];
  return `<article class="learning-practice-item" data-log-id="${esc(item.id)}" data-item-key="${esc(item.item_key)}">
   <div class="learning-practice-head">
    <div>
     <span class="learning-topic-badge">${esc(item.topic||(item.origin==='manual'?'Manual focus':'Plan focus'))}</span>
     <span class="learning-status-badge learning-status-${esc(item.status)}">${esc(statusLabel)}</span>
     <h4>${esc(item.title)}</h4>
    </div>
    ${isEditing?'':`<button type="button" class="button small learning-edit-practice-btn" data-log-id="${esc(item.id)}">Edit progress</button>`}
   </div>
   ${isEditing?renderInlinePracticeForm(item):`
    ${item.cue?`<p><strong>Cue:</strong> ${esc(item.cue)}</p>`:''}
    ${item.note?`<p class="learning-practice-note"><strong>Note:</strong> ${esc(item.note)}</p>`:''}
   `}
   <details class="learning-practice-history">
    <summary>History (${revs.length} revision${revs.length===1?'':'s'})</summary>
    <div class="learning-focus-details-body">
     ${item.drill?`<p><strong>Drill:</strong> ${esc(item.drill)}</p>`:''}
     <ul class="learning-history-list">
      ${revs.map(r=>`<li><strong>v${r.version}</strong> · ${r.actor==='human'?'Player':'Plan'} · <span class="learning-status-badge learning-status-${esc(r.status)}">${esc(STATUS_LABELS[r.status]||r.status)}</span>${r.note?` — “${esc(r.note)}”`:''} <span class="learning-context">(${esc(fmtTimestamp(r.created_at)||'recorded')})</span></li>`).join('')}
     </ul>
    </div>
   </details>
  </article>`;
 }

 function renderPracticeSection(ps){
  const logs=ps.practice_logs||[];
  const focusList=ps.active_plan?.plan?.focus_areas||[];
  const activeLogIds=new Set(focusList.map(f=>f.log_id).filter(Boolean));
  const activeKeys=new Set(focusList.map(f=>f.item_key).filter(Boolean));
  const extraLogs=logs.filter(l=>!activeLogIds.has(l.id)&&!activeKeys.has(l.item_key));
  const draft=manualDraft||newManualDraft(selected);

  return `<section id="learning-practice-section" class="learning-practice-section ${!extraLogs.length&&!addingManual?'learning-practice-section-compact':''}">
   <div class="learning-practice-header">
    <div>
     <h3>${ps.active_plan?'Additional practice items':'Practice log'}${extraLogs.length?` (${extraLogs.length})`:''}</h3>
     ${!ps.active_plan||extraLogs.length?`<p class="learning-context">Track custom drills and session notes across plan revisions.</p>`:''}
    </div>
    ${addingManual?'':`<button type="button" class="button small" id="learning-add-practice-btn">Add practice item</button>`}
   </div>
   ${addingManual?`
    <form id="learning-add-practice-form" class="learning-practice-form">
     <h4>New manual practice focus</h4>
     <div class="learning-form-grid">
      <label>Title<input type="text" id="manual-practice-title" name="title" maxlength="120" required placeholder="e.g. Early split step on return" value="${esc(draft.title)}"></label>
      <label>Topic<input type="text" id="manual-practice-topic" name="topic" maxlength="80" placeholder="e.g. Footwork" value="${esc(draft.topic)}"></label>
      <label>Status<select id="manual-practice-status" name="status" aria-label="New practice status">
       ${Object.entries(STATUS_LABELS).map(([val,label])=>`<option value="${val}" ${val===draft.status?'selected':''}>${esc(label)}</option>`).join('')}
      </select></label>
     </div>
     <label>Cue<input type="text" id="manual-practice-cue" name="cue" maxlength="400" placeholder="Short actionable reminder" value="${esc(draft.cue)}"></label>
     <label>Drill<textarea id="manual-practice-drill" name="drill" rows="2" maxlength="1200" placeholder="Practice drill description">${esc(draft.drill)}</textarea></label>
     <label>Practice note<textarea id="manual-practice-note" name="note" rows="2" maxlength="1000" placeholder="Session notes or observations">${esc(draft.note)}</textarea></label>
     <p class="error-message" id="learning-add-practice-error" role="alert" ${draft.error?'':'hidden'}>${esc(draft.error||'')}</p>
     <div class="learning-form-actions">
      <button type="submit" class="button primary small" ${draft.submitting?'disabled':''}>${draft.submitting?'Saving…':'Save practice item'}</button>
      <button type="button" class="button small" id="learning-cancel-add-practice" ${draft.submitting?'disabled':''}>Cancel</button>
     </div>
    </form>
   `:''}
   ${extraLogs.length?`<div class="learning-practice-list">${extraLogs.map(renderPracticeItem).join('')}</div>`:(!ps.active_plan&&!addingManual?'<p class="learning-context">No practice items yet.</p>':'')}
  </section>`;
 }

 function renderRevisionHistory(ps){
  const revs=ps.revisions||[];
  const ap=ps.active_plan;
  if(!revs.length&&!ap)return '';
  const covCount=ap?.coverage?.covered_reviews_count||0;
  const summaryLabel=ap?`Plan history · Revision ${ap.revision}${covCount?` (${covCount} review${covCount===1?'':'s'} covered)`:''}`:`Plan history (${revs.length})`;
  return `<details id="learning-plan-history" class="learning-plan-history">
   <summary>${esc(summaryLabel)}</summary>
   ${revs.length?`<ul class="learning-history-list">
    ${revs.map(r=>`<li><strong>Revision ${r.revision}</strong>${r.status&&r.status!=='active'?` (${esc(r.status)})`:''} · ${esc(fmtTimestamp(r.created_at)||'recorded')} — ${esc((r.observed_summary||'Not applied.').slice(0,160))}</li>`).join('')}
   </ul>`:''}
  </details>`;
 }

 function ensurePlanSlots(root){
  let bannerSlot=root.querySelector('#learning-plan-banner-slot');
  let activeSlot=root.querySelector('#learning-active-plan-slot');
  let practiceSlot=root.querySelector('#learning-practice-slot');
  let historySlot=root.querySelector('#learning-history-slot');
  if(!bannerSlot||!activeSlot||!practiceSlot||!historySlot){
   root.innerHTML=`<div id="learning-plan-banner-slot"></div><div id="learning-active-plan-slot"></div><div id="learning-practice-slot"></div><div id="learning-history-slot"></div>`;
   bannerSlot=root.querySelector('#learning-plan-banner-slot');
   activeSlot=root.querySelector('#learning-active-plan-slot');
   practiceSlot=root.querySelector('#learning-practice-slot');
   historySlot=root.querySelector('#learning-history-slot');
  }
  return {bannerSlot,activeSlot,practiceSlot,historySlot};
 }

 function renderPlanTab(opts={}){
  const root=$('#learning-plan-root');
  if(!root||!selected)return;
  const ps=cachedPlanState||defaultPlanState();
  const consentChecked=Boolean(root.querySelector('#learning-plan-consent')?.checked);
  const {bannerSlot,activeSlot,practiceSlot,historySlot}=ensurePlanSlots(root);
  bannerSlot.innerHTML=renderBanner(ps);
  const newConsent=bannerSlot.querySelector('#learning-plan-consent');
  if(newConsent&&consentChecked&&!newConsent.disabled)newConsent.checked=true;
  if(!opts.preservePracticeDom||!activeSlot.children.length||!practiceSlot.children.length){
   activeSlot.innerHTML=renderActivePlan(ps);
   practiceSlot.innerHTML=renderPracticeSection(ps);
  }
  historySlot.innerHTML=renderRevisionHistory(ps);
  bindPlanEvents();
 }

 function syncFormToDraft(form,draft){
  if(!form||!draft)return;
  const fd=new FormData(form);
  draft.title=String(fd.get('title')??'');
  draft.topic=String(fd.get('topic')??'');
  draft.status=String(fd.get('status')||'ongoing');
  draft.cue=String(fd.get('cue')??'');
  draft.drill=String(fd.get('drill')??'');
  draft.note=String(fd.get('note')??'');
 }

 function bindPlanEvents(){
  const ps=cachedPlanState||defaultPlanState();
  const genBtn=$('#learning-plan-generate');
  const consentBox=$('#learning-plan-consent');
  if(genBtn){
   genBtn.onclick=async()=>{
    if(!consentBox||!consentBox.checked){
     planError='Confirm sending review summaries and practice progress to OpenAI before generating a plan.';
     renderPlanTab({preservePracticeDom:true});
     return;
    }
    await submitPlanRequest();
   };
  }
  const cancelJobBtn=$('#learning-plan-cancel');
  if(cancelJobBtn&&ps.pending_job){
   cancelJobBtn.onclick=async()=>{
    const pid=selected,epoch=playerEpoch;
    try{
     await api('/api/coaching/jobs/'+encodeURIComponent(ps.pending_job.id),{
      method:'PUT',
      body:JSON.stringify({version:ps.pending_job.version,action:'cancel'}),
     });
     if(epoch===playerEpoch&&pid===selected)await load();
    }catch(e){
     if(epoch===playerEpoch&&pid===selected){planError=e.message;renderPlanTab({preservePracticeDom:true});}
    }
   };
  }
  const addBtn=$('#learning-add-practice-btn');
  if(addBtn){
   addBtn.onclick=()=>{
    addingManual=true;
    manualDraft=newManualDraft(selected);
    editingLogId='';
    editDraft=null;
    renderPlanTab();
   };
  }
  const cancelAddBtn=$('#learning-cancel-add-practice');
  if(cancelAddBtn){
   cancelAddBtn.onclick=()=>{
    addingManual=false;
    manualDraft=null;
    renderPlanTab();
   };
  }
  const addForm=$('#learning-add-practice-form');
  if(addForm){
   if(!manualDraft)manualDraft=newManualDraft(selected);
   addForm.oninput=()=>syncFormToDraft(addForm,manualDraft);
   addForm.onchange=()=>syncFormToDraft(addForm,manualDraft);
   addForm.onsubmit=async e=>{
    e.preventDefault();
    await submitManualPractice(addForm);
   };
  }
  for(const btn of $$('.learning-edit-practice-btn')){
   btn.onclick=()=>{
    const logId=btn.dataset.logId||'';
    const item=(ps.practice_logs||[]).find(l=>l.id===logId);
    editingLogId=logId;
    editDraft=item?newEditDraft(item):null;
    addingManual=false;
    manualDraft=null;
    renderPlanTab();
   };
  }
  for(const btn of $$('.learning-cancel-practice-btn')){
   btn.onclick=()=>{
    editingLogId='';
    editDraft=null;
    renderPlanTab();
   };
  }
  for(const form of $$('.learning-practice-form[data-log-id]')){
   const logId=form.dataset.logId||'';
   if(!editDraft||editDraft.logId!==logId){
    const item=(ps.practice_logs||[]).find(l=>l.id===logId);
    if(item)editDraft=newEditDraft(item);
   }
   form.oninput=()=>syncFormToDraft(form,editDraft);
   form.onchange=()=>syncFormToDraft(form,editDraft);
   form.onsubmit=async e=>{
    e.preventDefault();
    await submitPracticeUpdate(form);
   };
  }
 }

 async function submitPlanRequest(){
  const pid=selected,epoch=playerEpoch,ps=cachedPlanState||defaultPlanState();
  if(!pid||isPlanSubmitting(pid))return;
  const el=ps.eligibility||{};
  const pending=ps.pending_job;
  const token=Symbol('plan-submit');
  planSubmittingByPlayer.set(pid,token);
  planError='';
  renderPlanTab({preservePracticeDom:true});
  try{
   const reqBody={
    player_id:pid,
    consent:true,
    expected_parent_revision:el.expected_parent_revision??null,
    input_digest:el.input_digest,
    request_id:'plan-'+pid.slice(0,8)+'-'+Date.now()+'-'+Math.random().toString(36).slice(2,8),
   };
   if(pending&&pending.state==='needs_review')reqBody.retry_ambiguous=true;
   await api('/api/learning/plan',{method:'POST',body:JSON.stringify(reqBody)});
   if(planSubmittingByPlayer.get(pid)===token)planSubmittingByPlayer.delete(pid);
   if(epoch!==playerEpoch||pid!==selected)return;
   await load({isPoll:true});
  }catch(e){
   if(planSubmittingByPlayer.get(pid)===token)planSubmittingByPlayer.delete(pid);
   if(epoch!==playerEpoch||pid!==selected)return;
   planError=e.message;
   renderPlanTab({preservePracticeDom:true});
  }
 }

 async function submitManualPractice(form){
  const pid=selected,epoch=playerEpoch;
  if(!manualDraft)manualDraft=newManualDraft(pid);
  if(manualDraft.submitting)return;
  syncFormToDraft(form,manualDraft);
  manualDraft.submitting=true;
  manualDraft.error='';
  renderPlanTab();
  const payload={
   player_id:pid,
   title:manualDraft.title.trim(),
   topic:manualDraft.topic.trim(),
   status:manualDraft.status||'ongoing',
   cue:manualDraft.cue.trim(),
   drill:manualDraft.drill.trim(),
   note:manualDraft.note.trim(),
   request_id:manualDraft.request_id,
  };
  try{
   await api('/api/learning/practice-logs',{method:'POST',body:JSON.stringify(payload)});
   if(epoch!==playerEpoch||pid!==selected)return;
   addingManual=false;
   manualDraft=null;
   await load();
  }catch(e){
   if(epoch!==playerEpoch||pid!==selected)return;
   if(manualDraft){
    manualDraft.submitting=false;
    manualDraft.error=e.message;
   }
   renderPlanTab();
  }
 }

 async function submitPracticeUpdate(form){
  const pid=selected,epoch=playerEpoch;
  const logId=form.dataset.logId;
  const version=Number.parseInt(form.dataset.version,10);
  if(!editDraft||editDraft.logId!==logId){
   editDraft={logId,version,title:'',topic:'',status:'ongoing',cue:'',drill:'',note:'',error:'',submitting:false};
  }
  if(editDraft.submitting)return;
  syncFormToDraft(form,editDraft);
  editDraft.submitting=true;
  editDraft.error='';
  renderPlanTab();
  const payload={
   player_id:pid,
   version:editDraft.version,
   title:editDraft.title.trim(),
   topic:editDraft.topic.trim(),
   status:editDraft.status||'ongoing',
   cue:editDraft.cue.trim(),
   drill:editDraft.drill.trim(),
   note:editDraft.note.trim(),
  };
  try{
   await api('/api/learning/practice-logs/'+encodeURIComponent(logId),{method:'PUT',body:JSON.stringify(payload)});
   if(epoch!==playerEpoch||pid!==selected)return;
   editingLogId='';
   editDraft=null;
   await load();
  }catch(e){
   if(epoch!==playerEpoch||pid!==selected)return;
   if(editDraft&&editDraft.logId===logId){
    editDraft.submitting=false;
    editDraft.error=e.message;
   }
   renderPlanTab();
  }
 }

 return {mount};
})();
