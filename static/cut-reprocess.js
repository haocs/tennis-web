'use strict';
const CutReprocess=(()=>{
 let state=null;
 const refs=()=>[...document.querySelectorAll('#cut-rerun-evidence input:checked')].map(input=>JSON.parse(input.value));
 function update(context){
  const host=document.querySelector('#cut-rerun-panel');if(!host)return;
  const key=context.sourceId+':'+context.version;
  if(!state||state.key!==key){
   state={...context,key,preview:null,rows:[],requestId:null,busy:false};
   host.innerHTML='<summary>Prepare reprocess task</summary><div id="cut-rerun-evidence"></div><button type="button" class="button small" id="cut-rerun-preview">Preview commands</button><div id="cut-rerun-commands"></div><p id="cut-rerun-status" role="status"></p>';
   host.querySelector('#cut-rerun-preview').onclick=prepare;
  }
  state.context=context;
  if(state.preview||state.busy)return;
  const selected=new Set(refs().map(r=>r.annotation_id+':'+r.version));
  const reviews=context.reviews.filter(r=>r.kind==='cut_feedback'&&r.status==='active'&&r.context.publication===context.manifest.published_at);
  document.querySelector('#cut-rerun-evidence').innerHTML=reviews.length?reviews.map(r=>`<label class="check-label"><input type="checkbox" value="${esc(JSON.stringify({annotation_id:r.id,version:r.version}))}"${selected.has(r.id+':'+r.version)?' checked':''}>${esc((r.body.verdict||'Review').replaceAll('_',' '))} · ${time(r.target.start_seconds)}–${time(r.target.end_seconds)}${r.body.text?' · '+esc(r.body.text.slice(0,80)):''}</label>`).join(''):'<p class="cut-hint">Save a cut review to prepare a feedback-guided rerun.</p>';
  document.querySelector('#cut-rerun-preview').disabled=!reviews.length;
 }
 async function prepare(){
  const owner=state;if(owner.busy)return;
  const selected=refs(),status=document.querySelector('#cut-rerun-status');
  if(!selected.length){status.textContent='Select at least one review.';return;}
  owner.busy=true;status.textContent='Loading selected revisions…';
  try{
   const preview=await api(`/api/processing/videos/${owner.sourceId}/reprocess-preview`,{method:'POST',body:JSON.stringify({video_version_id:owner.version,evidence_revisions:selected})});
   if(state!==owner)return;
   owner.preview=preview;owner.selected=selected;
   const reviews=preview.evidence.map(e=>({...e,id:e.annotation_id,status:'active'}));
   const draft=CutConstraints.draft(reviews,preview.manifest);owner.rows=draft.rows;
   renderPreview(draft.advisory);status.textContent=preview.server_configured?'Confirm exact intervals. This creates a candidate; serving stays unchanged.':'Processing server is not configured. The preview is available; task creation is disabled.';
  }catch(e){if(state===owner)status.textContent=e.message;}
  finally{if(state===owner)owner.busy=false;}
 }
 function renderPreview(advisory){
  const owner=state,host=document.querySelector('#cut-rerun-commands');
  host.innerHTML='<h3>Confirm commands</h3>'+owner.rows.map((row,i)=>{
   const override=row.type==='override_clip_bounds_ms',a=override?row.original_start_ms:row.start_ms,b=override?row.original_end_ms:row.end_ms;
   return `<div class="cut-command-row" data-command="${i}"><label class="check-label"><input type="checkbox" aria-label="Confirm command ${i+1}">Confirm</label><select aria-label="Command ${i+1} action">${override?'<option value="override_clip_bounds_ms">Replace clip bounds</option>':'<option value="keep_intervals_ms">Keep interval</option><option value="exclude_intervals_ms">Exclude interval</option>'}</select><label>Start (s)<input type="number" step="0.001" min="0" data-start value="${a/1000}"></label><label>End (s)<input type="number" step="0.001" min="0" data-end value="${b/1000}"></label></div>`;
  }).join('')+(advisory.length?'<p class="cut-hint">Advisory only: '+advisory.map(a=>esc(a.text||a.verdict||(a.reasons||[]).join(', '))).join(' · ')+'</p>':'')+
  '<button type="button" class="button small" id="cut-command-add">Add interval</button> <button type="button" class="button small" id="cut-rerun-back">Change selection</button> <button type="button" class="button primary" id="cut-rerun-submit">Create candidate task</button>';
  host.querySelector('#cut-rerun-submit').disabled=!owner.preview.server_configured;
  host.querySelector('#cut-rerun-back').onclick=()=>{if(owner.busy)return;owner.preview=null;owner.requestId=null;host.innerHTML='';update(owner.context);};
  host.querySelector('#cut-command-add').onclick=()=>{
   if(owner.busy)return;capture();const e=owner.preview.evidence[0],t=e.target;
   owner.rows.push({type:'keep_intervals_ms',confirmed:false,start_ms:Math.round(t.start_seconds*1000),end_ms:Math.round(t.end_seconds*1000),source_annotation_id:e.annotation_id,source_revision:e.version});renderPreview(advisory);
  };
  host.querySelectorAll('[data-command]').forEach((node,i)=>{node.querySelector('input[type=checkbox]').checked=owner.rows[i].confirmed;node.querySelector('select').value=owner.rows[i].type;});
  host.oninput=()=>{owner.requestId=null;};host.onchange=()=>{owner.requestId=null;};
  host.querySelector('#cut-rerun-submit').onclick=submit;
 }
 function capture(){
  document.querySelectorAll('#cut-rerun-commands [data-command]').forEach((node,i)=>{
   const row=state.rows[i];row.confirmed=node.querySelector('input[type=checkbox]').checked;row.type=node.querySelector('select').value;
   const a=Math.round((node.querySelector('[data-start]').value.trim()===''?NaN:Number(node.querySelector('[data-start]').value))*1000),b=Math.round((node.querySelector('[data-end]').value.trim()===''?NaN:Number(node.querySelector('[data-end]').value))*1000);
   if(row.type==='override_clip_bounds_ms'){row.original_start_ms=a;row.original_end_ms=b;}else{row.start_ms=a;row.end_ms=b;}
  });
 }
 async function submit(){
  const owner=state,status=document.querySelector('#cut-rerun-status');if(owner.busy)return;
  capture();const commands=CutConstraints.commands(owner.rows),errors=CutConstraints.validate(commands,owner.preview.manifest);
  if(errors.length||!Object.values(commands).some(rows=>rows.length)){status.textContent=errors.join('. ')||'Confirm at least one exact command.';return;}
  owner.requestId??=globalThis.crypto?.randomUUID?.()||'rerun-'+Date.now()+'-'+Math.random().toString(36).slice(2);
  owner.busy=true;document.querySelectorAll('#cut-rerun-panel button,#cut-rerun-panel input,#cut-rerun-panel select').forEach(e=>e.disabled=true);status.textContent='Creating candidate task…';
  try{
   const result=await api(`/api/processing/videos/${owner.sourceId}/reprocess`,{method:'POST',body:JSON.stringify({video_version_id:owner.version,evidence_revisions:owner.selected,confirmed_commands:commands,request_id:owner.requestId})});
   if(state!==owner)return;
   owner.submitted=true;status.textContent='Candidate task created · '+result.task.task_id;
  }catch(e){if(state===owner)status.textContent=e.message+' Retry uses the same request ID.';}
  finally{if(state===owner){owner.busy=false;if(!owner.submitted)document.querySelectorAll('#cut-rerun-panel button,#cut-rerun-panel input,#cut-rerun-panel select').forEach(e=>e.disabled=false);}}
 }
 return {update};
})();
