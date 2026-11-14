'use strict';
const CutReprocess=(()=>{
 let state=null;
 const reviewKey=r=>r.id+':'+r.version;
 const selectedRefs=()=>[...document.querySelectorAll('#cut-rerun-evidence input:checked')].map(input=>JSON.parse(input.value));
 function shellHtml(){
  return '<div class="cut-rerun-head"><h2>Reprocess with cut feedback</h2><details class="cut-how-it-works"><summary>How cut feedback is used</summary><p>Exact intervals you confirm below become hard rules for a new candidate cut task (the serving video stays unchanged until you promote the candidate). All saved reviews also remain stored as immutable evaluation evidence for detector tuning.</p></details></div>'+
   '<div id="cut-rerun-body" hidden><details id="cut-rerun-evidence-details"><summary id="cut-rerun-evidence-summary"></summary><div id="cut-rerun-evidence"></div></details><p id="cut-rerun-status" role="status"></p><div id="cut-rerun-commands"></div></div>'+
   '<p id="cut-rerun-empty" class="cut-hint" hidden>Save at least one cut review on this version to draft reprocess rules and build a candidate task.</p>';
 }
 function updateSummary(selectedCount,totalCount){
  const summary=document.querySelector('#cut-rerun-evidence-summary');
  if(summary)summary.textContent=`Included cut reviews (${selectedCount} of ${totalCount})`;
 }
 function update(context){
  const host=document.querySelector('#cut-rerun-panel');if(!host)return;
  const key=context.sourceId+':'+context.version;
  if(!state||state.key!==key||state.host!==host){
   state={...context,key,host,preview:null,advisory:[],rows:[],selected:[],selectedKeys:new Set(),knownKeys:new Set(),reviewSig:'',requestId:null,previewSeq:0,busy:false,submitted:false};
   host.innerHTML=shellHtml();
   host.querySelector('#cut-rerun-evidence').onchange=()=>{
    if(!state||state.submitted||state.busy)return;
    const owner=state;
    owner.selectedKeys=new Set([...host.querySelectorAll('#cut-rerun-evidence input:checked')].map(i=>i.dataset.key));
    owner.requestId=null;
    updateSummary(owner.selectedKeys.size,owner.totalReviews);
    void prepare();
   };
  }
  state.context=context;
  if(state.busy||state.submitted)return;
  const reviews=context.reviews.filter(r=>r.kind==='cut_feedback'&&r.status==='active'&&r.context.publication===context.manifest.published_at);
  const sig=reviews.map(reviewKey).join(',');
  state.totalReviews=reviews.length;
  const empty=host.querySelector('#cut-rerun-empty'),body=host.querySelector('#cut-rerun-body'),list=host.querySelector('#cut-rerun-evidence');
  if(!reviews.length){
   state.reviewSig=sig;state.knownKeys=new Set();state.selectedKeys=new Set();state.preview=null;state.rows=[];state.advisory=[];
   empty.hidden=false;body.hidden=true;list.innerHTML='';host.querySelector('#cut-rerun-commands').innerHTML='';host.querySelector('#cut-rerun-status').textContent='';
   return;
  }
  empty.hidden=true;body.hidden=false;
  if(sig!==state.reviewSig){
   const nextKnown=new Set(reviews.map(reviewKey)),nextSelected=new Set();
   for(const r of reviews){const k=reviewKey(r);if(!state.knownKeys.size||!state.knownKeys.has(k)||state.selectedKeys.has(k))nextSelected.add(k);}
   state.reviewSig=sig;state.knownKeys=nextKnown;state.selectedKeys=nextSelected;
   list.innerHTML=reviews.map(r=>{
    const k=reviewKey(r),checked=nextSelected.has(k)?' checked':'';
    const label=(r.body.verdict||'Review').replaceAll('_',' '),text=r.body.text?' · '+esc(r.body.text.slice(0,80)):'';
    return `<label class="check-label"><input type="checkbox" data-key="${esc(k)}" value="${esc(JSON.stringify({annotation_id:r.id,version:r.version}))}"${checked}>${esc(label)} · ${time(r.target.start_seconds)}–${time(r.target.end_seconds)}${text}</label>`;
   }).join('');
   updateSummary(nextSelected.size,reviews.length);
   void prepare();
  }else{
   updateSummary(state.selectedKeys.size,reviews.length);
  }
 }
 async function prepare(){
  const owner=state;if(!owner||owner.submitted)return;
  const selected=selectedRefs(),status=document.querySelector('#cut-rerun-status'),commandsHost=document.querySelector('#cut-rerun-commands');
  const seq=++owner.previewSeq;
  owner.selected=selected;owner.requestId=null;
  if(!selected.length){
   owner.preview=null;owner.rows=[];owner.advisory=[];
   commandsHost.innerHTML='';
   status.textContent='Select at least one review in "Included cut reviews" to draft reprocess rules.';
   return;
  }
  status.textContent='Drafting rules from selected reviews…';
  try{
   const preview=await api(`/api/processing/videos/${owner.sourceId}/reprocess-preview`,{method:'POST',body:JSON.stringify({video_version_id:owner.version,evidence_revisions:selected})});
   if(state!==owner||seq!==owner.previewSeq||owner.submitted)return;
   owner.preview=preview;
   const reviews=preview.evidence.map(e=>({...e,id:e.annotation_id,status:'active'}));
   const draft=CutConstraints.draft(reviews,preview.manifest);
   owner.rows=draft.rows.map(row=>({...row,confirmed:true}));
   owner.advisory=draft.advisory;
   renderPreview(owner.advisory);
   status.textContent=preview.server_configured?(owner.rows.length?'Review the drafted rules below, then create a candidate cut task. Serving stays unchanged until you promote it.':'No exact rules drafted from this selection — add an interval rule below or include more reviews.'):'Processing server is not configured. Rule preview is available; candidate task creation is disabled.';
  }catch(e){if(state===owner&&seq===owner.previewSeq)status.textContent=e.message;}
 }
 function rowActionForEvidence(evidence){
  const b=evidence.body||{};
  if(b.verdict==='poor_cut')return 'exclude_intervals_ms';
  if(b.verdict!=='good_cut'&&b.verdict!=='needs_adjustment'&&Array.isArray(b.reasons)&&(b.reasons.includes('no_play')||b.reasons.includes('downtime')))return 'exclude_intervals_ms';
  return 'keep_intervals_ms';
 }
 function renderPreview(advisory){
  const owner=state,host=document.querySelector('#cut-rerun-commands');
  const rowsHtml=owner.rows.length?owner.rows.map((row,i)=>{
   const override=row.type==='override_clip_bounds_ms',a=override?row.original_start_ms:row.start_ms,b=override?row.original_end_ms:row.end_ms;
   return `<div class="cut-command-row" data-command="${i}"><label class="check-label"><input type="checkbox" aria-label="Confirm command ${i+1}">Include rule</label><select aria-label="Command ${i+1} action">${override?'<option value="override_clip_bounds_ms">Replace clip bounds</option>':'<option value="keep_intervals_ms">Keep interval</option><option value="exclude_intervals_ms">Exclude interval</option>'}</select><label>Start (s)<input type="number" step="0.001" min="0" data-start value="${a/1000}"></label><label>End (s)<input type="number" step="0.001" min="0" data-end value="${b/1000}"></label></div>`;
  }).join(''):'<p class="cut-hint">No exact interval rules drafted yet — pick a review below to add a keep or exclude rule.</p>';
  const advisoryHtml=advisory.length?'<p class="cut-hint">Saved as tuning notes only (no hard interval): '+advisory.map(a=>esc(a.text||((a.verdict||'').replaceAll('_',' '))||(a.reasons||[]).join(', '))).join(' · ')+'</p>':'';
  const compareHref=`/review/${encodeURIComponent(owner.sourceId)}/compare?left=${encodeURIComponent(owner.version)}`;
  host.innerHTML='<h3>Confirm reprocess rules</h3>'+rowsHtml+advisoryHtml+
   '<div class="cut-rerun-add-row"><label>Add rule from review<select id="cut-command-evidence">'+owner.preview.evidence.map((e,i)=>`<option value="${i}">${esc(e.body.text?.slice(0,60)||((e.body.reasons||[]).join(', ')||(e.body.verdict||'Review').replaceAll('_',' ')))} · ${time(e.target.start_seconds)}–${time(e.target.end_seconds)}</option>`).join('')+'</select></label><button type="button" class="button small" id="cut-command-add">Add interval</button></div>'+
   '<div class="cut-rerun-actions"><button type="button" class="button primary" id="cut-rerun-submit">Create candidate task</button></div>'+
   `<div id="cut-rerun-next" class="cut-rerun-next" hidden><a class="button small" href="/review">Open Review</a><a class="button small" href="${compareHref}">Compare versions</a><button type="button" class="button small" id="cut-rerun-reset">Prepare another task</button></div>`;
  host.querySelector('#cut-rerun-submit').disabled=!owner.preview.server_configured||owner.busy||owner.submitted;
  host.querySelector('#cut-command-add').onclick=()=>{
   if(owner.busy||owner.submitted)return;
   capture();owner.requestId=null;
   const e=owner.preview.evidence[Number(host.querySelector('#cut-command-evidence').value)],t=e.target;
   owner.rows.push({type:rowActionForEvidence(e),confirmed:true,start_ms:Math.round(t.start_seconds*1000),end_ms:Math.round(t.end_seconds*1000),source_annotation_id:e.annotation_id,source_revision:e.version});
   renderPreview(owner.advisory);
  };
  host.querySelector('#cut-rerun-reset').onclick=resetTask;
  host.querySelectorAll('[data-command]').forEach((node,i)=>{
   node.querySelector('input[type=checkbox]').checked=Boolean(owner.rows[i].confirmed);
   node.querySelector('select').value=owner.rows[i].type;
  });
  host.oninput=()=>{owner.requestId=null;};host.onchange=()=>{owner.requestId=null;};
  host.querySelector('#cut-rerun-submit').onclick=submit;
 }
 function resetTask(){
  const owner=state;if(!owner||owner.busy)return;
  owner.submitted=false;owner.requestId=null;owner.reviewSig='';
  document.querySelectorAll('#cut-rerun-panel input,#cut-rerun-panel select,#cut-rerun-panel button').forEach(el=>el.disabled=false);
  update(owner.context);
 }
 function capture(){
  document.querySelectorAll('#cut-rerun-commands [data-command]').forEach((node,i)=>{
   const row=state.rows[i];row.confirmed=node.querySelector('input[type=checkbox]').checked;row.type=node.querySelector('select').value;
   const a=Math.round((node.querySelector('[data-start]').value.trim()===''?NaN:Number(node.querySelector('[data-start]').value))*1000),b=Math.round((node.querySelector('[data-end]').value.trim()===''?NaN:Number(node.querySelector('[data-end]').value))*1000);
   if(row.type==='override_clip_bounds_ms'){row.original_start_ms=a;row.original_end_ms=b;}else{row.start_ms=a;row.end_ms=b;}
  });
 }
 async function submit(){
  const owner=state,status=document.querySelector('#cut-rerun-status');if(owner.busy||owner.submitted||!owner.preview)return;
  capture();const commands=CutConstraints.commands(owner.rows),errors=CutConstraints.validate(commands,owner.preview.manifest);
  if(errors.length||!Object.values(commands).some(rows=>rows.length)){status.textContent=errors.join('. ')||'Confirm at least one exact command.';return;}
  owner.requestId??=globalThis.crypto?.randomUUID?.()||'rerun-'+Date.now()+'-'+Math.random().toString(36).slice(2);
  owner.busy=true;document.querySelectorAll('#cut-rerun-panel button,#cut-rerun-panel input,#cut-rerun-panel select').forEach(el=>el.disabled=true);
  status.textContent='Creating candidate task…';
  try{
   const result=await api(`/api/processing/videos/${owner.sourceId}/reprocess`,{method:'POST',body:JSON.stringify({video_version_id:owner.version,evidence_revisions:owner.selected,confirmed_commands:commands,request_id:owner.requestId})});
   if(state!==owner)return;
   owner.submitted=true;
   status.textContent=`Candidate task created (${result.task.task_id}) — serving remains unchanged until promoted.`;
   const next=document.querySelector('#cut-rerun-next');
   if(next){next.hidden=false;next.querySelectorAll('a,button').forEach(el=>el.disabled=false);}
  }catch(e){
   if(state===owner){
    status.textContent=e.message+' Retry uses the same request ID.';
    document.querySelectorAll('#cut-rerun-panel button,#cut-rerun-panel input,#cut-rerun-panel select').forEach(el=>el.disabled=false);
    const submitBtn=document.querySelector('#cut-rerun-submit');if(submitBtn&&!owner.preview.server_configured)submitBtn.disabled=true;
   }
  }finally{if(state===owner)owner.busy=false;}
 }
 return {update};
})();
