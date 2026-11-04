/* Coaching proposals retain their original evidence; reviewed cues are separate. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id), C=window.ClipStudyCore;
  const oldDefault='Please suggest one observable improvement and a simple drill for this beginner tennis sequence.';
  const defaults='Review the full sequence of supplied snapshots, from first to last. I am a beginner. Assess all clearly visible aspects of my technique: ready position, footwork, spacing, balance, racket preparation, swing positions and recovery. Cover the supported observations across the whole sequence, group repeated issues and prioritise up to six useful improvements. For each, cite the relevant frames and original timestamps, give an actionable cue and a simple drill, and state what the stills cannot establish. Do not limit the review to a single issue or invent unseen movement.';
  const states={queued:'Queued',leased:'Working',calling:'ChatGPT is reviewing frames',needs_review:'Interrupted after sending. Check the worker result before asking again.',failed:'Failed',completed:'Ready',cancelled:'Cancelled'};
  let owner=null,timer=null,generation=0,selectedExport=null,selectedAsk=null,busy=false,revision=0,requestId=null,askId=null,askFingerprint=null,step=1,lastData=null,historyKey='',selectionPlaying=false,followUpId=null,mobileMode='watch';
  let stopWatch=()=>{};
  function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
  async function api(path,body,method='POST'){
    const r=await fetch('/api/coaching/'+path,body===undefined?{}:{method,headers:{'Content-Type':'application/json','X-Tennis-Request':'1'},body:JSON.stringify(body)});
    const d=await r.json();if(!r.ok)throw new Error(d.error||'Request unavailable');return d;
  }
  function message(text){$('coach-status').textContent=text;}
  function cryptoId(){return 'coach-'+Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');}
  function draftKey(){return `tennis-coach:${owner.session.id}:${owner.session.published_at}:${owner.clip.id}`;}
  function saveDraft(){if(!owner)return;try{sessionStorage.setItem(draftKey(),JSON.stringify({...times(),quality:$('coach-quality').value,question:$('coach-question').value,selectedExport,selectedAsk,followUpId,step}));}catch(_){/* Storage may be disabled. */}}
  function mobile(){return matchMedia('(max-width:760px)').matches;}
  function mobileOffsets(){
    const dialog=$('clip-study'),header=document.querySelector('.study-header'),bar=$('study-mobile-tabs'),workspace=document.querySelector('.study-workspace');
    const chrome=header.offsetHeight+(mobile()?bar.offsetHeight:0);
    dialog.style.setProperty('--study-header-height',header.offsetHeight+'px');
    dialog.style.setProperty('--study-chrome-height',chrome+'px');
    dialog.style.setProperty('--coach-anchor-height',(chrome+(mobileMode==='coach'?workspace.offsetHeight:0)+12)+'px');
  }
  function showMode(mode,scroll=true){
    if(mode==='watch')$('coach-preview').pause();
    mobileMode=mode;$('clip-study').dataset.mobileMode=mode;
    for(const name of ['watch','coach'])$('study-mode-'+name).setAttribute('aria-pressed',String(name===mode));
    requestAnimationFrame(()=>{mobileOffsets();if(scroll&&mobile())(mode==='coach'?$('coaching-panel'):document.querySelector('.study-workspace')).scrollIntoView({block:'start',behavior:'smooth'});});
  }
  function navigate(value,scroll=true){
    if(step===2&&value!==2)$('coach-preview').pause();
    step=value;document.querySelector('.study-layout').dataset.coachStep=value;
    for(let i=1;i<=4;i++){$('coach-step-'+i).setAttribute('aria-current',i===step?'step':'false');$('coach-phase-'+i).hidden=i!==step;}
    $('coach-back').hidden=step===1;
    saveDraft();if(lastData)updateProgress(lastData);
    requestAnimationFrame(()=>{mobileOffsets();if(scroll&&mobile()&&mobileMode==='coach')$('coaching-panel').scrollIntoView({block:'start',behavior:'smooth'});});
  }
  function times(){return {start:Number($('coach-in').value),end:Number($('coach-out').value)};}
  function showRange(){const t=times();$('coach-range').textContent=`Clip ${C.clock(t.start)}–${C.clock(t.end)} · original ${C.clock(owner.clip.original_start_seconds+t.start)}–${C.clock(owner.clip.original_start_seconds+t.end)}`;}
  function clearPreview(){const v=$('coach-preview');v.pause();v.removeAttribute('src');v.hidden=true;delete v.dataset.job;$('coach-export-links').replaceChildren();$('coach-ask').disabled=true;$('coach-preview-next').disabled=true;}
  function selection(){if(!owner)return;selectionPlaying=false;followUpId=null;showRange();selectedExport=null;selectedAsk=null;revision++;requestId=null;clearPreview();saveDraft();}
  function setBusy(value){busy=value;$('coach-prepare').disabled=value||!owner||lastData?.enabled===false;$('coach-question').disabled=value;if(lastData)askAvailability(lastData);else $('coach-ask').disabled=true;}
  function button(text,action,cls='study-button'){const b=el('button',text,cls);b.type='button';b.onclick=action;return b;}
  function makePanel(){
    const panel=el('section',undefined,'study-panel coaching-panel');panel.id='coaching-panel';
    panel.innerHTML=`<h3>Ask a coach</h3>
      <nav class="coach-steps" aria-label="Coaching steps">${['Choose moment','Preview','Ask coach','Review suggestions'].map((name,i)=>`<button type="button" id="coach-step-${i+1}"><span>${i+1}</span>${name}</button>`).join('')}</nav>
      <section id="coach-phase-1" aria-labelledby="coach-step-1"><p class="study-muted">Up to 30 seconds · includes zoom and speed</p><p id="coach-range"></p>
      <div class="coach-boundary"><label>Start<input id="coach-in" type="range" min="0" max="1" step="0.01" value="0" aria-label="Coaching excerpt start"></label><button class="study-button" id="coach-set-in" type="button">Set start here</button></div>
      <div class="coach-boundary"><label>End<input id="coach-out" type="range" min="0" max="1" step="0.01" value="1" aria-label="Coaching excerpt end"></label><button class="study-button" id="coach-set-out" type="button">Set end here</button></div><button class="study-button" id="coach-play-selection" type="button">Play selection</button>
      <details id="coach-question-options"><summary>Review focus (optional)</summary><label>What would you like help with?<textarea id="coach-question" rows="3" maxlength="4000"></textarea></label></details>
      <details><summary>Export options</summary><label>Export size<select id="coach-quality"><option value="balanced">Balanced · up to 960px</option><option value="small">Smaller · up to 640px</option></select></label></details>
      <button class="study-button study-primary" id="coach-prepare" type="button">Prepare coaching clip</button></section>
      <section id="coach-phase-2" aria-labelledby="coach-step-2" hidden><p id="coach-preview-detail" class="study-muted">Prepare a clip to preview.</p><video id="coach-preview" controls playsinline preload="metadata" hidden aria-label="Prepared coaching excerpt"></video><div id="coach-export-links"></div><button class="study-button study-primary" id="coach-preview-next" type="button">Looks good · continue</button></section>
      <section id="coach-phase-3" aria-labelledby="coach-step-3" hidden><p class="study-hint">Sends your question and up to 32 frames to OpenAI. Reviews stills, without audio.</p><p id="coach-follow-up-context" class="study-hint" hidden></p><label>Your question<textarea id="coach-send-question" rows="5" maxlength="4000"></textarea></label><button type="button" id="coach-use-default" class="study-button">Review the whole sequence</button><p id="coach-send-detail" class="study-muted">Preview a clip first.</p><button class="study-button study-primary" id="coach-ask" type="button" disabled>Ask coach · send frames to OpenAI</button><button class="study-button" id="coach-to-results" type="button">View saved suggestions</button></section>
      <section id="coach-phase-4" aria-labelledby="coach-step-4" hidden><p class="study-muted">Accept suggestions to show timed cues during playback.</p><div id="coach-history"></div></section>
      <button type="button" id="coach-back" class="study-button" hidden>← Previous step</button><p id="coach-status" role="status" aria-live="polite"></p>`;
    const sidebar=document.querySelector('.study-sidebar');
    const extras=el('details',undefined,'study-extra-tools');extras.append(el('summary','Recording & frame tools'));extras.open=!mobile();
    while(sidebar.firstChild)extras.append(sidebar.firstChild);sidebar.append(panel,extras);
    const tabs=el('nav',undefined,'study-mobile-tabs');tabs.id='study-mobile-tabs';tabs.setAttribute('aria-label','Clip study views');
    tabs.append(button('Watch',()=>showMode('watch')),button('Coach',()=>showMode('coach')));tabs.children[0].id='study-mode-watch';tabs.children[1].id='study-mode-coach';document.querySelector('.study-header').after(tabs);$('coach-question-options').open=!mobile();
    const start=button('Get AI coaching →',()=>showMode('coach'),'study-button study-primary study-coach-entry');document.querySelector('.study-transport').append(start);
    const compact=el('div',undefined,'study-mobile-transport'),play=button('Play clip',()=>{$('study-play').click();});play.id='study-mobile-play';const collapse=button('Hide player',()=>{const hidden=$('clip-study').dataset.playerCollapsed==='true';$('clip-study').dataset.playerCollapsed=String(!hidden);collapse.textContent=hidden?'Hide player':'Show player';requestAnimationFrame(mobileOffsets);});collapse.id='study-mobile-collapse';compact.append(play,collapse,button('Zoom & playback',()=>showMode('watch')));document.querySelector('.study-workspace').append(compact);
    const syncPlay=()=>{play.textContent=$('study-video').paused?'Play clip':'Pause clip';};
    for(const event of ['play','pause','ended'])$('study-video').addEventListener(event,syncPlay);
    if(window.ResizeObserver)new ResizeObserver(()=>{if(owner)mobileOffsets();}).observe(document.querySelector('.study-workspace'));
    window.addEventListener('resize',()=>{if(owner)mobileOffsets();});
    $('coach-back').onclick=()=>navigate(Math.max(1,step-1));
    $('coach-use-default').onclick=()=>{$('coach-send-question').value=defaults;$('coach-send-question').dispatchEvent(new Event('input'));};
    for(let i=1;i<=4;i++)$('coach-step-'+i).onclick=()=>navigate(i);
    for(const id of ['coach-in','coach-out','coach-quality'])$(id).addEventListener('input',selection);
    $('coach-set-in').onclick=()=>{$('coach-in').value=$('study-video').currentTime;selection();};
    $('coach-set-out').onclick=()=>{$('coach-out').value=$('study-video').currentTime;selection();};
    $('coach-play-selection').onclick=()=>{const t=times(),v=$('study-video');if(t.start>=t.end){message('End must be after start.');return;}selectionPlaying=true;v.currentTime=t.start;v.play().catch(()=>message('Tap Play to preview.'));};
    $('study-video').addEventListener('timeupdate',()=>{if(!owner||!selectionPlaying)return;const t=times(),v=$('study-video');if(v.currentTime>=t.end&&!v.paused){selectionPlaying=false;v.pause();message('Selection finished.');}});
    for(const id of ['study-play','study-seek','study-back','study-forward'])$(id).addEventListener(id==='study-seek'?'input':'click',()=>{selectionPlaying=false;});
    $('coach-preview').addEventListener('play',()=>{selectionPlaying=false;$('study-video').pause();});
    $('study-video').addEventListener('play',()=>{$('coach-preview').pause();});
    $('study-speed').addEventListener('change',()=>{selection();navigate(1);});
    $('coach-question').addEventListener('input',()=>{requestId=null;saveDraft();$('coach-send-question').value=$('coach-question').value;if(lastData)askAvailability(lastData);});
    $('coach-send-question').addEventListener('input',()=>{$('coach-question').value=$('coach-send-question').value;askFingerprint=null;askId=null;saveDraft();if(lastData)askAvailability(lastData);});
    $('coach-prepare').onclick=prepare;$('coach-ask').onclick=ask;
    $('coach-preview-next').onclick=()=>{if(!$('coach-preview').dataset.job){message('Wait for the prepared excerpt.');return;}navigate(3);};
    $('coach-to-results').onclick=()=>navigate(4);
  }
  function recipe(){
    const transform=window.ClipStudy.coachingSelection();if(!transform)throw new Error('Wait for the clip to load');
    const {start,end}=times();if(start>=end||end-start>30||end-start<.1)throw new Error('Select between 0.1 and 30 seconds');
    return {source_id:owner.session.id,clip_id:owner.clip.id,publication:owner.session.published_at,start,end,...transform,quality:$('coach-quality').value,question:$('coach-question').value};
  }
  async function prepare(){
    if(busy||!owner)return;const current=owner,rev=revision;
    try{const body=recipe();selectionPlaying=false;$('study-video').pause();setBusy(true);message('Clip queued…');requestId=requestId||cryptoId();const j=await api('exports',{...body,request_id:requestId});if(owner!==current||rev!==revision)return;selectedExport=j.id;selectedAsk=null;followUpId=null;navigate(2);await refresh();}
    catch(e){if(owner===current)message(e.message);}finally{if(owner===current){setBusy(false);if(rev!==revision)message('Selection changed. Prepare it again.');}}
  }
  async function ask(){
    if(busy||!selectedExport)return;const current=owner;
    try{setBusy(true);message('Review queued…');const fingerprint=JSON.stringify([selectedExport,$('coach-question').value.trim(),followUpId]);if(fingerprint!==askFingerprint){askFingerprint=fingerprint;askId=cryptoId();}const j=await api('requests',{export_id:selectedExport,prompt:$('coach-question').value,consent:true,request_id:askId,...(followUpId?{follow_up_of:followUpId}:{})});if(owner!==current)return;selectedAsk=j.id;navigate(4);await refresh();}
    catch(e){if(owner===current)message(e.message);}finally{if(owner===current){setBusy(false);refresh();}}
  }
  function exportLinks(job){
    if($('coach-preview').dataset.job===job.id)return;$('coach-preview').dataset.job=job.id;
    const base='/api/coaching/exports/'+job.id+'/';$('coach-preview').src=base+'excerpt.mp4';$('coach-preview').hidden=false;
    const r=job.result;$('coach-preview-detail').textContent=`${(r.bytes/1024/1024).toFixed(1)} MB · ${C.clock(r.output_seconds)} · ${job.body.speed}×`;
    $('coach-send-detail').textContent=`${r.frames.length} frames · original ${C.clock(job.body.original_start)}–${C.clock(job.body.original_end)}`;
    $('coach-send-question').value=$('coach-question').value;
    const box=$('coach-export-links');box.replaceChildren();const manual=el('details');manual.append(el('summary','Use ChatGPT manually instead'));
    for(const [name,file] of [['Download coaching video','excerpt.mp4'],['Timing metadata','recipe.json'],['Prepared prompt','prompt.txt']]){const a=el('a',name,'study-button');a.href=base+file+'?download=1';a.setAttribute('download','');manual.append(a);}
    const chat=el('a','Open ChatGPT ↗','study-button');chat.href='https://chatgpt.com/';chat.target='_blank';chat.rel='noopener noreferrer';manual.append(chat,el('p','Attach the video and paste your question. Replies stay in that chat.','study-hint'));box.append(manual);
    $('coach-ask').disabled=busy;$('coach-preview-next').disabled=false;
  }
  function jump(original,annotation){
    const clip=owner.clip;
    if(original>=clip.original_start_seconds&&original<=clip.original_end_seconds){selectionPlaying=false;$('clip-study').dataset.playerCollapsed='false';$('study-mobile-collapse').textContent='Hide player';requestAnimationFrame(mobileOffsets);$('study-video').pause();$('study-video').currentTime=original-clip.original_start_seconds;message(`Original ${C.clock(original)}`);if(mobile())document.querySelector('.study-workspace').scrollIntoView({block:'start',behavior:'smooth'});return;}
    const target=owner.session.clips.find(c=>c.original_start_seconds<=original&&c.original_end_seconds>=original);
    if(target){location.href=`/review/${owner.session.id}?clip=${target.id}&coach_time=${original}`;}
    else message('This moment was cut from the current video. See the saved frame.');
  }
  function evidenceStrip(job,annotation){
    const strip=el('div',undefined,'coach-evidence');const refs=annotation.current.evidence_frame_ids;
    const frames=(job.body.evidence_frames||[]).filter(f=>refs.includes(f.frame_id));
    if(!frames.length){strip.append(el('p','No verified frame references.','study-muted'));return strip;}
    for(const f of frames){const item=el('figure'),image=el('img');image.src=`/api/coaching/evidence/${job.id}/${f.frame_id}.jpg`;image.loading='lazy';image.alt=`Submitted frame ${f.order}, original ${C.clock(f.original_seconds)}`;const go=button(`Frame ${f.order} · ${C.clock(f.original_seconds)}`,()=>jump(f.original_seconds,annotation),'coach-frame-link');go.append(image);item.append(go,el('figcaption',`Clip ${C.clock(f.clip_seconds)} · excerpt ${C.clock(f.excerpt_seconds)}`));strip.append(item);}
    return strip;
  }
  async function review(a,status,content){
    try{await api('annotations/'+a.id,{version:a.version,publication:owner.session.published_at,status,...(content?{content}:{})},'PUT');await refresh(true);window.dispatchEvent(new Event('coach-notes-changed'));message(status==='accepted'?'Suggestion accepted.':'Review saved.');}
    catch(e){message(e.message);/* Keep the unsaved edit visible after a conflict. */}
  }
  function editor(a,job,manual=false){
    const details=el('details',undefined,'coach-editor');details.append(el('summary',manual?'Add your own suggestion':'Edit suggestion or timing'));
    const form=el('form'),fields={};
    const names={title:'Title',topic:'Topic',observation:'Observation',cue:'Playback cue',drill:'Drill',uncertainty:'Uncertainty'};
    const limits={title:120,topic:80,observation:1600,cue:400,drill:1200,uncertainty:1200};
    for(const [key,name] of Object.entries(names)){const label=el('label',name),input=el('textarea');input.rows=key==='cue'?2:1;input.value=a.current[key];input.maxLength=limits[key];label.append(input);form.append(label);fields[key]=input;}
    form.append(el('p','Original recording times · optional','study-hint'));
    const ranges=el('div'),rows=[];
    function addRange(start,end){const row=el('div',undefined,'coach-time-row'),inputs=[];for(const [name,value] of [['Start',start],['End',end]]){const label=el('label',name+' seconds'),input=el('input');input.type='number';input.min=job.body.original_start;input.max=job.body.original_end;input.step='.001';input.required=true;input.value=value;label.append(input);row.append(label);inputs.push(input);}row.append(button('Remove',()=>{row.remove();rows.splice(rows.indexOf(inputs),1);}));rows.push(inputs);ranges.append(row);}
    for(const i of a.current.applies_to)addRange(i.start_seconds,i.end_seconds);
    form.append(ranges,button('Add excerpt interval',()=>{if(rows.length<12)addRange(job.body.original_start,job.body.original_end);}));
    const refs=el('fieldset');refs.append(el('legend','Evidence frames'));const checks=[];
    for(const f of job.body.evidence_frames||[]){const label=el('label',`Frame ${f.order} · original ${C.clock(f.original_seconds)}`),input=el('input');input.type='checkbox';input.checked=a.current.evidence_frame_ids.includes(f.frame_id);label.prepend(input);refs.append(label);checks.push([input,f.frame_id]);}form.append(refs);
    const submit=el('button',manual?'Save reviewed suggestion':'Save edits and accept','study-button study-primary');submit.type='submit';form.append(submit);
    form.onsubmit=event=>{event.preventDefault();const content=Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.value]));content.evidence_frame_ids=checks.filter(([check])=>check.checked).map(([,id])=>id);content.applies_to=rows.map(([s,e])=>({start_seconds:Number(s.value),end_seconds:Number(e.value)}));if(manual){const requestId=details.dataset.requestId||(details.dataset.requestId=cryptoId());api('annotations',{job_id:job.id,publication:owner.session.published_at,content,request_id:requestId}).then(()=>{refresh(true);window.dispatchEvent(new Event('coach-notes-changed'));message('Suggestion saved.');}).catch(e=>message(e.message));}else review(a,'accepted',content);};details.append(form);return details;
  }
  function suggestionCard(a,job){
    const card=el('article',undefined,'coach-suggestion');card.dataset.annotation=a.id;
    card.append(el('p',`${a.status==='accepted'?'Accepted':a.status==='dismissed'?'Dismissed':'Needs your review'}${a.historical?' · Previous publication':''}`,'coach-review-state'),el('h5',a.current.title));
    if(a.historical)card.append(el('p',a.playback_eligible?'Reviewed for this video.':'Older video version. Review again to show cues.','study-hint'));
    for(const [key,title] of [['observation','Observed'],['cue','Try'],['drill','Practice'],['uncertainty','Uncertainty']])if(a.current[key]){const p=el('p');p.append(el('strong',title+': '),document.createTextNode(a.current[key]));card.append(p);}
    const timing=el('div',undefined,'coach-timing');if(a.current.applies_to.length)for(const i of a.current.applies_to)timing.append(button(`Original ${C.clock(i.start_seconds)}–${C.clock(i.end_seconds)}`,()=>jump(i.start_seconds,a)));else timing.append(el('p','Add timing to show a playback cue.','study-muted'));card.append(timing,evidenceStrip(job,a));
    for(const warning of a.current.validation_warnings||[])card.append(el('p',warning+'. Edit references before accepting.','coach-warning'));
    card.append(button(a.historical?'Accept for current video':'Accept suggestion',()=>review(a,'accepted')),button('Dismiss',()=>review(a,'dismissed')),editor(a,job));
    return card;
  }
  function legacyCard(card,j){
    card.append(el('p','Older response · timing unverified','study-hint'));
    const label=el('label','Your playback cue (review before saving)'),input=el('textarea');input.maxLength=400;input.rows=2;input.value=j.result.cue||'';label.append(input);card.append(label,button('Save reviewed cue',async()=>{try{await api('jobs/'+j.id,{action:'cue',cue:input.value,version:j.version},'PUT');await refresh(true);window.dispatchEvent(new Event('coach-notes-changed'));}catch(e){message(e.message);}}));
  }
  function followUp(job){
    if(job.body.publication!==owner.session.published_at){message('Older video version. Prepare a new clip to ask about this video.');return;}
    const exported=lastData.jobs.find(j=>j.id===job.body.export_id&&j.role==='render'&&j.state==='completed');
    if(!exported){message('Clip unavailable. Prepare a new selection.');navigate(1);return;}
    selectedExport=exported.id;selectedAsk=null;followUpId=job.id;askId=null;askFingerprint=null;
    exportLinks(exported);$('coach-question').value='';$('coach-send-question').value='';
    $('coach-follow-up-context').textContent='Includes the previous question and response.';$('coach-follow-up-context').hidden=false;
    showMode('coach',false);navigate(3);$('coach-send-question').focus({preventScroll:true});askAvailability(lastData);
  }
  function history(data){
    const box=$('coach-history');box.replaceChildren();
    const jobs=data.jobs.filter(j=>j.role==='ai'&&(j.body.clip_id===owner.clip.id||j.body.original_start<owner.clip.original_end_seconds&&j.body.original_end>owner.clip.original_start_seconds));
    if(!jobs.length)box.append(el('p','No suggestions yet.','study-muted'));
    for(const j of jobs){
      const card=el('article',undefined,'coach-note');card.append(el('strong',`${C.clock(j.body.original_start)}–${C.clock(j.body.original_end)} · ${states[j.state]||j.state}${j.body.follow_up_of?' · Follow-up':''}`));
      const question=el('details');question.append(el('summary','Question asked'),el('p',j.body.prompt));card.append(question);
      if(j.state==='completed'){
        if(j.body.publication===owner.session.published_at)card.append(button('Ask a follow-up',()=>followUp(j),'study-button study-primary'));
        if(j.result.structured){const result=j.result.structured;if(result.summary)card.append(el('p',result.summary,'coach-summary'));for(const warning of result.validation_warnings)card.append(el('p',warning,'coach-warning'));if(result.parse_status==='unstructured')card.append(el('p','Timing unavailable. See the original response and frames.','study-hint'));
          for(const a of data.annotations.filter(a=>a.job_id===j.id))card.append(suggestionCard(a,j));
          const all=el('details');all.append(el('summary','All submitted snapshots'));const fake={current:{evidence_frame_ids:(j.body.evidence_frames||[]).map(f=>f.frame_id)}};all.append(evidenceStrip(j,fake));card.append(all);
          const blank={current:{title:'',topic:'',observation:'',cue:'',drill:'',uncertainty:'',evidence_frame_ids:[],applies_to:[]}};card.append(editor(blank,j,true));
        }else legacyCard(card,j);
        const raw=el('details');raw.append(el('summary','Original AI response'),el('p',j.result.answer,'coach-answer'));card.append(raw);
      }
      if(j.result?.error)card.append(el('p',j.result.error,'coach-warning'));
      if(['queued','leased'].includes(j.state))card.append(button('Cancel request',async()=>{try{await api('jobs/'+j.id,{action:'cancel',version:j.version},'PUT');await refresh();}catch(e){message(e.message);}}));
      box.append(card);
    }
    const exports=data.jobs.filter(j=>j.role==='render'&&j.body.clip_id===owner.clip.id&&j.body.publication===owner.session.published_at);
    if(exports.length){const past=el('details');past.append(el('summary','Earlier prepared excerpts'));for(const j of exports){const p=el('p',`${C.clock(j.body.start)}–${C.clock(j.body.end)} · ${j.body.speed}× · ${states[j.state]||j.state}`);if(j.state==='completed')p.append(button('View this export',()=>{selectedExport=j.id;exportLinks(j);navigate(2);}));past.append(p);}box.append(past);}
    box.append(button('Export annotations and revision history',async()=>{
      const current=owner,merged={schema_version:1,annotations:[],responses:[]};let after='';
      try{do{const page=await api('annotations/export/'+current.session.id+'?after='+encodeURIComponent(after));merged.annotations.push(...page.annotations);merged.responses.push(...page.responses);after=page.next_after;}while(after);
        const url=URL.createObjectURL(new Blob([JSON.stringify(merged,null,2)],{type:'application/json'})),link=el('a');link.href=url;link.download='coaching-annotations.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }catch(e){if(owner===current)message(e.message);}
    }));
  }
  function askAvailability(data){
      const existing=data.jobs.find(a=>a.role==='ai'&&a.body.export_id===selectedExport&&a.body.prompt===$('coach-question').value.trim()&&(a.body.follow_up_of||null)===followUpId&&!['failed','cancelled'].includes(a.state));
      $('coach-ask').disabled=busy||data.jobs.find(j=>j.id===selectedExport)?.state!=='completed'||!!existing||!$('coach-question').value.trim();
      $('coach-ask').textContent=existing?(existing.state==='completed'?'Response saved · ask a follow-up':'Request in progress · view suggestions'):followUpId?'Send follow-up · frames to OpenAI':'Ask coach · send frames to OpenAI';
  }
  function updateProgress(data){
    const exported=data.jobs.find(j=>j.id===selectedExport),asked=data.jobs.find(j=>j.id===selectedAsk);
    const completed=[!!exported,exported?.state==='completed',asked?.state==='completed',!!asked&&data.annotations.some(a=>a.job_id===asked.id&&a.status!=='proposed')];
    for(let i=1;i<=4;i++){$('coach-step-'+i).dataset.complete=completed[i-1]?'true':'false';$('coach-step-'+i).title=completed[i-1]?'Completed':'';}
    if(step===2&&exported?.state==='completed')message('Clip ready.');
  }
  async function refresh(force=false){
    if(!owner)return;const current=owner,gen=generation;
    try{const data=await api('sessions/'+current.session.id);if(owner!==current||gen!==generation)return;lastData=data;
      const key=JSON.stringify([data.jobs.map(j=>[j.id,j.version]),data.annotations.map(a=>[a.id,a.version])]);
      if(force||key!==historyKey&&!document.activeElement?.closest('#coach-history')&&!$('coach-history').querySelector('.coach-editor[open]')){history(data);historyKey=key;}
      const j=data.jobs.find(j=>j.id===selectedExport),ai=data.jobs.find(j=>j.id===selectedAsk);
      if(j?.state==='completed')exportLinks(j);
      $('coach-prepare').disabled=busy||!data.enabled;
      askAvailability(data);
      const online=data.workers.filter(w=>w.online),roles=online.map(w=>w.role);
      if(!data.enabled)message('Coaching workers are not configured yet.');
      else if(ai&&ai.state!=='completed')message(ai.state==='queued'&&!online.some(w=>w.role==='ai'&&JSON.parse(w.capabilities||'[]').includes('coaching-annotations-v1'))?'Queued · waiting for AI worker update.':states[ai.state]+(ai.result?.error?': '+ai.result.error:''));
      else if(ai?.state==='completed')message('Response saved.');
      else if(j?.state==='completed')message('Clip ready.');
      else if(j)message(j.state==='queued'&&!roles.includes('render')?'Queued · waiting for media worker.':states[j.state]+(j.result?.error?': '+j.result.error:''));
      updateProgress(data);
    }catch(e){if(owner===current)message(e.message);}
  }
  window.Coach={
    open(session,clip){if(!$('coaching-panel'))makePanel();generation++;owner={session,clip};selectedExport=null;selectedAsk=null;requestId=null;busy=false;historyKey='';lastData=null;clearPreview();$('clip-study').dataset.playerCollapsed='false';$('study-mobile-collapse').textContent='Hide player';selectionPlaying=false;followUpId=null;$('coach-follow-up-context').hidden=true;
      for(const id of ['coach-in','coach-out'])$(id).max=clip.duration_seconds;
      let d={};try{d=JSON.parse(sessionStorage.getItem(draftKey())||'{}');}catch(_){}
      $('coach-in').value=Number.isFinite(d.start)?d.start:0;$('coach-out').value=Number.isFinite(d.end)?d.end:Math.min(30,clip.duration_seconds);$('coach-question').value=typeof d.question==='string'&&d.question!==oldDefault?d.question:defaults;$('coach-quality').value=d.quality==='small'?'small':'balanced';
      selectedExport=d.selectedExport||null;selectedAsk=d.selectedAsk||null;followUpId=d.followUpId||null;if(followUpId){$('coach-follow-up-context').hidden=false;$('coach-follow-up-context').textContent='Includes the previous question and response.';}$('coach-send-question').value=$('coach-question').value;showRange();setBusy(false);showMode('watch',false);navigate([1,2,3,4].includes(d.step)?d.step:1,false);refresh();clearInterval(timer);timer=setInterval(()=>refresh(),4000);
    },
    close(){saveDraft();selectionPlaying=false;generation++;owner=null;clearInterval(timer);if($('coach-preview'))clearPreview();},
    watch({session,clip,player}){
      stopWatch();let active=true,notes=[],legacy=[],pendingSeek=null;const badge=el('button','Coach cue','coach-playback-cue');badge.hidden=true;player.parentElement.after(badge);
      const originalTime=()=>{if(clip)return clip.original_start_seconds+player.currentTime;const c=session.clips.find(c=>player.currentTime>=c.playback_start_seconds&&player.currentTime<=c.playback_end_seconds);return c?c.original_start_seconds+player.currentTime-c.playback_start_seconds:null;};
      const sync=()=>{if(!badge.isConnected)return;const original=originalTime();const cues=notes.filter(a=>original!==null&&a.current.applies_to.some(i=>original>=i.start_seconds&&original<=i.end_seconds)).map(a=>a.current.cue);for(const j of legacy)if(original!==null&&original>=j.body.original_start&&original<=j.body.original_end)cues.push(j.result.cue);badge.hidden=!cues.length;badge.textContent=cues.join(' · ');};
      const load=()=>api('sessions/'+session.id).then(data=>{if(active){notes=data.annotations.filter(a=>a.playback_eligible);legacy=data.jobs.filter(j=>j.role==='ai'&&j.state==='completed'&&!j.result.structured&&j.result.cue_reviewed&&j.body.publication===session.published_at);sync();}}).catch(()=>{});
      const requested=new URL(location.href).searchParams.get('coach_time');if(requested!==null&&clip){const t=Number(requested);if(Number.isFinite(t)&&t>=clip.original_start_seconds&&t<=clip.original_end_seconds){const seek=()=>{player.currentTime=t-clip.original_start_seconds;pendingSeek=null;};if(player.readyState)seek();else{pendingSeek=seek;player.addEventListener('loadedmetadata',seek,{once:true});}}}
      badge.onclick=()=>{player.pause();badge.classList.toggle('coach-expanded');};
      stopWatch=()=>{active=false;if(pendingSeek)player.removeEventListener('loadedmetadata',pendingSeek);window.removeEventListener('coach-notes-changed',load);player.removeEventListener('timeupdate',sync);badge.remove();};window.addEventListener('coach-notes-changed',load);player.addEventListener('timeupdate',sync);load();
    }
  };
})();
