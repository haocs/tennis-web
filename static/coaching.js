/* Coaching proposals retain their original evidence; reviewed cues are separate. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id), C=window.ClipStudyCore;
  const oldDefault='Please suggest one observable improvement and a simple drill for this beginner tennis sequence.';
  const defaults='Review the full sequence of supplied snapshots, from first to last. I am a beginner. Assess all clearly visible aspects of my technique: ready position, footwork, spacing, balance, racket preparation, swing positions and recovery. Cover the supported observations across the whole sequence, group repeated issues and prioritise up to six useful improvements. For each, cite the relevant frames and original timestamps, give an actionable cue and a simple drill, and state what the stills cannot establish. Do not limit the review to a single issue or invent unseen movement.';
  const presetPrompts={
    full:defaults,
    footwork:'Review my footwork, split-step timing, spacing to the ball, and balance throughout this sequence. I am a beginner. Cite the relevant frames and original timestamps, give an actionable cue and a simple drill, and state what the stills cannot establish.',
    preparation:'Review my ready position, unit turn, and racket preparation across this sequence. I am a beginner. Cite the relevant frames and original timestamps, give an actionable cue and a simple drill, and state what the stills cannot establish.',
    contact:'Review my spacing, posture near contact, and follow-through across these snapshots. I am a beginner. Cite the relevant frames and original timestamps, give an actionable cue and a simple drill, and state what the stills cannot establish.',
    recovery:'Review how I recover after the shot and my court positioning across this sequence. I am a beginner. Cite the relevant frames and original timestamps, give an actionable cue and a simple drill, and state what the stills cannot establish.'
  };
  const states={queued:'Queued',leased:'Working',calling:'Getting response…',needs_review:'Interrupted after sending. Check the worker result before asking again.',failed:'Failed',completed:'Ready',cancelled:'Cancelled'};
  function presetQuestion(key='full'){
    const base=presetPrompts[key]||defaults;
    return owner?.clip?.main_player?base.replace(' I am a beginner.',''):base;
  }
  function defaultQuestion(){return presetQuestion('full');}
  let owner=null,timer=null,generation=0,selectedExport=null,selectedAsk=null,pendingAsk=null,busy=false,revision=0,requestId=null,askId=null,askFingerprint=null,lastData=null,historyKey='',followUpId=null,mobileMode='watch';
  let stopWatch=()=>{};
  function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
  async function api(path,body,method='POST'){
    const r=await fetch('/api/coaching/'+path,body===undefined?{}:{method,headers:{'Content-Type':'application/json','X-Tennis-Request':'1'},body:JSON.stringify(body)});
    const d=await r.json();if(!r.ok)throw new Error(d.error||'Request unavailable');return d;
  }
  function message(text){if($('coach-status'))$('coach-status').textContent=text;}
  function cryptoId(){return 'coach-'+Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');}
  function draftKey(){return `tennis-coach:${owner.session.id}:${owner.session.published_at}:${owner.clip.id}`;}
  function currentSelection(){
    return window.ClipStudy?.coachingSelection?.()||{start:0,end:Math.min(30,owner?.clip?.duration_seconds||1),speed:0.5,zoom:1,crop:[0,0,1,1]};
  }
  function saveDraft(){
    if(!owner)return;
    try{
      const sel=currentSelection();
      sessionStorage.setItem(draftKey(),JSON.stringify({start:sel.start,end:sel.end,preset:$('coach-preset')?.value||'full',question:$('coach-question')?.value||'',selectedExport,selectedAsk,followUpId}));
    }catch(_){/* Storage may be disabled. */}
  }
  function mobile(){return matchMedia('(max-width:760px)').matches;}
  function mobileOffsets(){
    const dialog=$('clip-study'),header=document.querySelector('.study-header'),bar=$('study-mobile-tabs'),workspace=document.querySelector('.study-workspace');
    if(!dialog||!header||!bar||!workspace)return;
    const chrome=header.offsetHeight+(mobile()?bar.offsetHeight:0);
    dialog.style.setProperty('--study-header-height',header.offsetHeight+'px');
    dialog.style.setProperty('--study-chrome-height',chrome+'px');
    dialog.style.setProperty('--coach-anchor-height',(chrome+(mobileMode==='coach'?workspace.offsetHeight:0)+12)+'px');
  }
  function showMode(mode,scroll=true){
    mobileMode=mode;$('clip-study').dataset.mobileMode=mode;
    for(const name of ['watch','coach'])if($('study-mode-'+name))$('study-mode-'+name).setAttribute('aria-pressed',String(name===mode));
    requestAnimationFrame(()=>{mobileOffsets();if(scroll&&mobile())(mode==='coach'?$('coaching-panel'):document.querySelector('.study-workspace')).scrollIntoView({block:'start',behavior:'smooth'});});
  }
  function showRange(){
    if(!owner||!$('coach-range'))return;
    const sel=currentSelection();
    const zoomText=Number((sel.zoom||1).toFixed(2))+'× zoom';
    $('coach-range').textContent=`Selected ${C.clock(sel.start)}–${C.clock(sel.end)} · ${sel.speed}× · ${zoomText} · original ${C.clock(owner.clip.original_start_seconds+sel.start)}–${C.clock(owner.clip.original_start_seconds+sel.end)}`;
  }
  function sameCrop(a,b){
    return Array.isArray(a)&&Array.isArray(b)&&a.length===4&&b.length===4&&a.every((v,i)=>Math.abs(v-b[i])<0.002);
  }
  function matchingCompletedExport(data,spec){
    if(!data?.jobs)return null;
    return data.jobs.find(j=>
      j.role==='render'&&
      j.state==='completed'&&
      j.body.intent!=='share'&&
      j.body.clip_id===spec.clip_id&&
      j.body.publication===spec.publication&&
      Math.abs(j.body.start-spec.start)<0.005&&
      Math.abs(j.body.end-spec.end)<0.005&&
      Math.abs(j.body.speed-spec.speed)<0.005&&
      sameCrop(j.body.crop,spec.crop)
    )||null;
  }
  function selectionChanged(){
    if(!owner)return;
    showRange();
    if(!pendingAsk&&!busy){
      revision++;
      requestId=null;
    }
    saveDraft();
    if(lastData)askAvailability(lastData);
  }
  function setBusy(value){
    busy=value;
    if($('coach-question'))$('coach-question').disabled=value;
    if($('coach-preset'))$('coach-preset').disabled=value;
    if(lastData)askAvailability(lastData);
    else if($('coach-ask'))$('coach-ask').disabled=value||!owner;
  }
  function button(text,action,cls='study-button'){const b=el('button',text,cls);b.type='button';b.onclick=action;return b;}
  function makePanel(){
    const panel=el('section',undefined,'study-panel coaching-panel');panel.id='coaching-panel';
    panel.innerHTML=`<h3>Ask a coach</h3>
      <p class="study-muted">Uses your selected interval (0.1–30s), zoom framing, and playback speed. Sends up to 32 stills and your question to OpenAI.</p>
      <p id="coach-range"></p>
      <p id="coach-player-context" class="study-hint"></p>
      <p id="coach-follow-up-context" class="study-hint" hidden></p>
      <label for="coach-preset">Preset<select id="coach-preset" aria-label="Preset">
        <option value="full">Full technique review (up to 6 improvements)</option>
        <option value="footwork">Footwork, split step, and balance</option>
        <option value="preparation">Racket preparation and unit turn</option>
        <option value="contact">Spacing, contact, and follow-through</option>
        <option value="recovery">Recovery and court positioning</option>
        <option value="custom">Custom question</option>
      </select></label>
      <label for="coach-question">Question for coach<textarea id="coach-question" rows="4" maxlength="4000"></textarea></label>
      <div class="coach-actions">
        <button class="study-button study-primary" id="coach-ask" type="button">Ask coach</button>
      </div>
      <p id="coach-status" role="status" aria-live="polite"></p>
      <div id="coach-history"></div>`;
    const sidebar=document.querySelector('.study-sidebar');
    const extras=el('details',undefined,'study-extra-tools');extras.append(el('summary','Recording & frame tools'));
    while(sidebar.firstChild)extras.append(sidebar.firstChild);sidebar.append(panel,extras);
    const tabs=el('nav',undefined,'study-mobile-tabs');tabs.id='study-mobile-tabs';tabs.setAttribute('aria-label','Clip study views');
    tabs.append(button('Watch',()=>showMode('watch')),button('Coach',()=>showMode('coach')));tabs.children[0].id='study-mode-watch';tabs.children[1].id='study-mode-coach';document.querySelector('.study-header').after(tabs);
    const start=button('Get AI coaching →',()=>showMode('coach'),'study-button study-primary study-coach-entry');document.querySelector('.study-transport').append(start);
    const compact=el('div',undefined,'study-mobile-transport'),play=button('Play clip',()=>{$('study-play').click();});play.id='study-mobile-play';const collapse=button('Hide player',()=>{const hidden=$('clip-study').dataset.playerCollapsed==='true';$('clip-study').dataset.playerCollapsed=String(!hidden);collapse.textContent=hidden?'Hide player':'Show player';requestAnimationFrame(mobileOffsets);});collapse.id='study-mobile-collapse';compact.append(play,collapse,button('Zoom & playback',()=>showMode('watch')));document.querySelector('.study-workspace').append(compact);
    const syncPlay=()=>{play.textContent=$('study-video').paused?'Play clip':'Pause clip';};
    for(const event of ['play','pause','ended'])$('study-video').addEventListener(event,syncPlay);
    if(window.ResizeObserver)new ResizeObserver(()=>{if(owner)mobileOffsets();}).observe(document.querySelector('.study-workspace'));
    window.addEventListener('resize',()=>{if(owner)mobileOffsets();});
    window.addEventListener('study-selection-changed',selectionChanged);
    $('coach-preset').addEventListener('change',()=>{
      const key=$('coach-preset').value;
      if(key!=='custom'){
        $('coach-question').value=presetQuestion(key);
        $('coach-question').dispatchEvent(new Event('input'));
      }
    });
    $('coach-question').addEventListener('input',()=>{
      const val=$('coach-question').value;
      const matchingKey=Object.keys(presetPrompts).find(k=>presetQuestion(k)===val);
      $('coach-preset').value=matchingKey||'custom';
      requestId=null;askFingerprint=null;askId=null;
      saveDraft();
      if(lastData)askAvailability(lastData);
    });
    $('coach-ask').onclick=askCoach;
  }
  function captureRecipe(){
    const transform=window.ClipStudy.coachingSelection();if(!transform)throw new Error('Wait for the clip to load');
    const {start,end,speed,crop}=transform;
    if(start>=end||end-start>30||end-start<.1)throw new Error('Select between 0.1 and 30 seconds');
    const question=$('coach-question').value.trim();
    if(!question)throw new Error('Enter a question up to 4000 characters');
    return {
      source_id:owner.session.id,
      clip_id:owner.clip.id,
      publication:owner.session.published_at,
      start,
      end,
      speed,
      crop:[...crop],
      quality:'balanced',
      question,
      followUpOf:followUpId,
      playerId:owner.clip.main_player?.id||null,
      playerProfileVersion:owner.clip.main_player?.version||null
    };
  }
  async function submitAiRequest(exportId,captured){
    const current=owner;
    const fingerprint=JSON.stringify([exportId,captured.question,captured.followUpOf]);
    if(fingerprint!==askFingerprint){askFingerprint=fingerprint;askId=cryptoId();}
    message('Getting response…');
    const j=await api('requests',{
      player_id:captured.playerId,
      player_profile_version:captured.playerProfileVersion,
      export_id:exportId,
      prompt:captured.question,
      consent:true,
      request_id:askId,
      ...(captured.followUpOf?{follow_up_of:captured.followUpOf}:{})
    });
    if(owner!==current)return;
    selectedAsk=j.id;
    followUpId=null;
    $('coach-follow-up-context').hidden=true;
    saveDraft();
  }
  async function askCoach(){
    if(busy||!owner)return;
    const current=owner;
    try{
      const captured=captureRecipe();
      $('study-video').pause();
      setBusy(true);
      // If following up on the same completed export or if an identical export is already completed, go straight to AI request.
      let existingExport=null;
      if(captured.followUpOf&&selectedExport){
        const candidate=lastData?.jobs?.find(j=>j.id===selectedExport&&j.role==='render'&&j.state==='completed'&&j.body.intent!=='share');
        if(candidate&&Math.abs(candidate.body.start-captured.start)<0.005&&Math.abs(candidate.body.end-captured.end)<0.005&&Math.abs(candidate.body.speed-captured.speed)<0.005&&sameCrop(candidate.body.crop,captured.crop)){
          existingExport=candidate;
        }
      }
      if(!existingExport){
        existingExport=matchingCompletedExport(lastData,captured);
      }
      if(existingExport){
        selectedExport=existingExport.id;
        pendingAsk=null;
        await submitAiRequest(existingExport.id,captured);
        await refresh(true);
        return;
      }
      message('Preparing your clip…');
      requestId=requestId||cryptoId();
      const exportPayload={
        source_id:captured.source_id,
        clip_id:captured.clip_id,
        publication:captured.publication,
        start:captured.start,
        end:captured.end,
        speed:captured.speed,
        crop:captured.crop,
        quality:captured.quality,
        question:captured.question,
        request_id:requestId
      };
      const renderJob=await api('exports',exportPayload);
      if(owner!==current)return;
      selectedExport=renderJob.id;
      selectedAsk=null;
      pendingAsk={...captured,exportId:renderJob.id};
      saveDraft();
      if(renderJob.state==='completed'){
        const nextAsk=pendingAsk;
        pendingAsk=null;
        await submitAiRequest(renderJob.id,nextAsk);
      }
      await refresh(true);
    }catch(e){
      if(owner===current){pendingAsk=null;message(e.message);}
    }finally{
      if(owner===current){
        setBusy(false);
        if(lastData)askAvailability(lastData);
      }
    }
  }
  function jump(original){
    const clip=owner.clip;
    if(original>=clip.original_start_seconds&&original<=clip.original_end_seconds){
      $('clip-study').dataset.playerCollapsed='false';
      if($('study-mobile-collapse'))$('study-mobile-collapse').textContent='Hide player';
      requestAnimationFrame(mobileOffsets);
      const localTime=original-clip.original_start_seconds;
      const sel=currentSelection();
      if(localTime<sel.start||localTime>sel.end){
        window.ClipStudy?.setInterval?.(Math.min(sel.start,localTime),Math.max(sel.end,localTime));
      }
      $('study-video').pause();
      $('study-video').currentTime=localTime;
      message(`Original ${C.clock(original)}`);
      if(mobile())document.querySelector('.study-workspace').scrollIntoView({block:'start',behavior:'smooth'});
      return;
    }
    const target=owner.session.clips.find(c=>c.original_start_seconds<=original&&c.original_end_seconds>=original);
    if(target){location.href=`/review/${owner.session.id}?clip=${target.id}&coach_time=${original}`;}
    else message('This moment was cut from the current video. See the saved frame.');
  }
  function evidenceStrip(job,annotation){
    const strip=el('div',undefined,'coach-evidence');const refs=annotation.current.evidence_frame_ids;
    const frames=(job.body.evidence_frames||[]).filter(f=>refs.includes(f.frame_id));
    if(!frames.length){strip.append(el('p','No verified frame references.','study-muted'));return strip;}
    for(const f of frames){const item=el('figure'),image=el('img');image.src=`/api/coaching/evidence/${job.id}/${f.frame_id}.jpg`;image.loading='lazy';image.alt=`Submitted frame ${f.order}, original ${C.clock(f.original_seconds)}`;const go=button(`Frame ${f.order} · ${C.clock(f.original_seconds)}`,()=>jump(f.original_seconds),'coach-frame-link');go.append(image);item.append(go,el('figcaption',`Clip ${C.clock(f.clip_seconds)} · excerpt ${C.clock(f.excerpt_seconds)}`));strip.append(item);}
    return strip;
  }
  async function review(a,status,content){
    try{
      await api('annotations/'+a.id,{version:a.version,publication:owner.session.published_at,status,...(content?{content}:{})},'PUT');
      await refresh(true);
      window.dispatchEvent(new Event('coach-notes-changed'));
      message(status==='accepted'?'Suggestion saved.':'Suggestion discarded.');
    }catch(e){message(e.message);/* Keep the unsaved edit visible after a conflict. */}
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
    form.onsubmit=event=>{event.preventDefault();const content=Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.value]));content.evidence_frame_ids=checks.filter(([check])=>check.checked).map(([,id])=>id);content.applies_to=rows.map(([s,e])=>({start_seconds:Number(s.value),end_seconds:Number(e.value)}));if(manual){const reqId=details.dataset.requestId||(details.dataset.requestId=cryptoId());api('annotations',{job_id:job.id,publication:owner.session.published_at,content,request_id:reqId}).then(()=>{refresh(true);window.dispatchEvent(new Event('coach-notes-changed'));message('Suggestion saved.');}).catch(e=>message(e.message));}else review(a,'accepted',content);};details.append(form);return details;
  }
  function suggestionCard(a,job){
    const card=el('article',undefined,'coach-suggestion');card.dataset.annotation=a.id;
    const stateLabel=a.status==='accepted'?'Saved':a.status==='dismissed'?'Discarded':'Needs your review';
    card.append(el('p',`${stateLabel}${a.historical?' · Previous publication':''}`,'coach-review-state'),el('h5',a.current.title));
    if(a.historical)card.append(el('p',a.playback_eligible?'Reviewed for this video.':'Older video version. Review again to show cues.','study-hint'));
    for(const [key,title] of [['observation','Observed'],['cue','Try'],['drill','Practice'],['uncertainty','Uncertainty']])if(a.current[key]){const p=el('p');p.append(el('strong',title+': '),document.createTextNode(a.current[key]));card.append(p);}
    const timing=el('div',undefined,'coach-timing');if(a.current.applies_to.length)for(const i of a.current.applies_to)timing.append(button(`Original ${C.clock(i.start_seconds)}–${C.clock(i.end_seconds)}`,()=>jump(i.start_seconds)));else timing.append(el('p','Add timing to show a playback cue.','study-muted'));card.append(timing,evidenceStrip(job,a));
    for(const warning of a.current.validation_warnings||[])card.append(el('p',warning+'. Edit references before accepting.','coach-warning'));
    card.append(
      button(a.historical?'Save for current video':'Save',()=>review(a,'accepted'),'study-button study-primary'),
      button('Discard',()=>review(a,'dismissed')),
      editor(a,job)
    );
    return card;
  }
  function legacyCard(card,j){
    card.append(el('p','Older response · timing unverified','study-hint'));
    const label=el('label','Your playback cue (review before saving)'),input=el('textarea');input.maxLength=400;input.rows=2;input.value=j.result.cue||'';label.append(input);card.append(label,button('Save reviewed cue',async()=>{try{await api('jobs/'+j.id,{action:'cue',cue:input.value,version:j.version},'PUT');await refresh(true);window.dispatchEvent(new Event('coach-notes-changed'));}catch(e){message(e.message);}}));
  }
  function followUp(job){
    if(job.body.publication!==owner.session.published_at){message('Older video version. Ask about the current video.');return;}
    const exported=lastData?.jobs?.find(j=>j.id===job.body.export_id&&j.role==='render'&&j.state==='completed');
    if(!exported){message('Prepared clip unavailable. Ask coach to prepare a fresh selection.');return;}
    selectedExport=exported.id;selectedAsk=null;pendingAsk=null;followUpId=job.id;askId=null;askFingerprint=null;
    if(Number.isFinite(exported.body.start)&&Number.isFinite(exported.body.end)){
      window.ClipStudy?.setInterval?.(exported.body.start,exported.body.end);
    }
    $('coach-preset').value='custom';
    $('coach-question').value='';
    $('coach-follow-up-context').textContent='Re-asking with previous question and response context.';
    $('coach-follow-up-context').hidden=false;
    showMode('coach',false);
    $('coach-question').focus({preventScroll:true});
    if(lastData)askAvailability(lastData);
  }
  function history(data){
    const box=$('coach-history');box.replaceChildren();
    const jobs=data.jobs.filter(j=>j.role==='ai'&&(j.body.clip_id===owner.clip.id||j.body.original_start<owner.clip.original_end_seconds&&j.body.original_end>owner.clip.original_start_seconds));
    if(!jobs.length){box.append(el('p','No coach responses yet.','study-muted'));return;}
    for(const j of jobs){
      const card=el('article',undefined,'coach-note');card.append(el('strong',`${C.clock(j.body.original_start)}–${C.clock(j.body.original_end)} · ${states[j.state]||j.state}${j.body.follow_up_of?' · Follow-up':''}`));
      if(j.body.player_context&&typeof model==='object'&&model?.players){const p=model.players.find(p=>p.id===j.body.player_context.player_id);card.append(el('p','For '+(p?.name||'saved player'),'study-hint'));}
      const question=el('details');question.append(el('summary','Question asked'),el('p',j.body.prompt));card.append(question);
      if(j.state==='completed'){
        if(j.body.publication===owner.session.published_at)card.append(button('Re-ask',()=>followUp(j),'study-button'));
        if(j.result.structured){const result=j.result.structured;if(result.summary)card.append(el('p',result.summary,'coach-summary'));for(const warning of result.validation_warnings)card.append(el('p',warning,'coach-warning'));if(result.parse_status==='unstructured')card.append(el('p','Timing unavailable. See the original response and frames.','study-hint'));
          for(const a of data.annotations.filter(a=>a.job_id===j.id))card.append(suggestionCard(a,j));
          const all=el('details');all.append(el('summary','All submitted snapshots'));const fake={current:{evidence_frame_ids:(j.body.evidence_frames||[]).map(f=>f.frame_id)}};all.append(evidenceStrip(j,fake));card.append(all);
          const blank={current:{title:'',topic:'',observation:'',cue:'',drill:'',uncertainty:'',evidence_frame_ids:[],applies_to:[]}};card.append(editor(blank,j,true));
        }else legacyCard(card,j);
        const raw=el('details');raw.append(el('summary','Original AI response'),el('p',j.result.answer,'coach-answer'));card.append(raw);
      }
      if(j.result?.error)card.append(el('p',j.result.error,'coach-warning'));
      if(['queued','leased'].includes(j.state))card.append(button('Cancel request',async()=>{try{await api('jobs/'+j.id,{action:'cancel',version:j.version},'PUT');await refresh(true);}catch(e){message(e.message);}}));
      box.append(card);
    }
    box.append(button('Export annotations and revision history',async()=>{
      const current=owner,merged={schema_version:1,annotations:[],responses:[]};let after='';
      try{do{const page=await api('annotations/export/'+current.session.id+'?after='+encodeURIComponent(after));merged.annotations.push(...page.annotations);merged.responses.push(...page.responses);after=page.next_after;}while(after);
        const url=URL.createObjectURL(new Blob([JSON.stringify(merged,null,2)],{type:'application/json'})),link=el('a');link.href=url;link.download='coaching-annotations.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }catch(e){if(owner===current)message(e.message);}
    }));
  }
  function askAvailability(data){
    if(!$('coach-ask'))return;
    const renderJob=data?.jobs?.find(j=>j.id===selectedExport);
    const aiJob=data?.jobs?.find(j=>j.id===selectedAsk);
    const exportInProgress=!!pendingAsk&&renderJob&&['queued','leased'].includes(renderJob.state);
    const aiInProgress=aiJob&&['queued','leased','calling'].includes(aiJob.state);
    $('coach-ask').disabled=busy||exportInProgress||aiInProgress||data?.enabled===false||!$('coach-question').value.trim();
    $('coach-ask').textContent='Ask coach';
  }
  async function refresh(force=false){
    if(!owner)return;const current=owner,gen=generation;
    try{const data=await api('sessions/'+current.session.id);if(owner!==current||gen!==generation)return;lastData=data;
      const j=data.jobs.find(j=>j.id===selectedExport),ai=data.jobs.find(j=>j.id===selectedAsk);
      if(pendingAsk&&j?.state==='completed'&&!selectedAsk){
        const nextAsk=pendingAsk;
        pendingAsk=null;
        try{
          await submitAiRequest(j.id,nextAsk);
          return await refresh(true);
        }catch(e){
          if(owner===current)message(e.message);
        }
      }
      const key=JSON.stringify([data.jobs.map(j=>[j.id,j.version,j.state]),data.annotations.map(a=>[a.id,a.version,a.status])]);
      if(force||key!==historyKey&&!document.activeElement?.closest('#coach-history')&&!$('coach-history').querySelector('.coach-editor[open]')){history(data);historyKey=key;}
      askAvailability(data);
      if(!data.enabled)message('Coaching workers are not configured yet.');
      else if(ai&&['queued','leased','calling'].includes(ai.state))message('Getting response…');
      else if(ai&&ai.state==='failed')message(ai.result?.error||'Failed to get coach response.');
      else if(ai&&ai.state==='needs_review')message(states.needs_review);
      else if(j&&['queued','leased'].includes(j.state))message('Preparing your clip…');
      else if(j&&j.state==='failed'){pendingAsk=null;message(j.result?.error||'Failed to prepare clip.');}
    }catch(e){if(owner===current)message(e.message);}
  }
  window.Coach={
    open(session,clip){
      if(!$('coaching-panel'))makePanel();
      generation++;owner={session,clip};selectedExport=null;selectedAsk=null;pendingAsk=null;requestId=null;busy=false;historyKey='';lastData=null;
      $('clip-study').dataset.playerCollapsed='false';
      if($('study-mobile-collapse'))$('study-mobile-collapse').textContent='Hide player';
      followUpId=null;$('coach-follow-up-context').hidden=true;$('coach-status').textContent='';
      $('coach-player-context').textContent=clip.main_player?'For '+clip.main_player.name+' · height and level are included; name stays in this library.':'No main player assigned.';
      let d={};try{d=JSON.parse(sessionStorage.getItem(draftKey())||'{}');}catch(_){}
      if(Number.isFinite(d.start)&&Number.isFinite(d.end)&&d.start<d.end){
        window.ClipStudy?.setInterval?.(d.start,d.end);
      }
      const savedQuestion=typeof d.question==='string'&&d.question!==oldDefault&&d.question!==defaults?d.question:defaultQuestion();
      $('coach-question').value=savedQuestion;
      const matchingKey=Object.keys(presetPrompts).find(k=>presetQuestion(k)===savedQuestion);
      $('coach-preset').value=matchingKey||'custom';
      selectedExport=d.selectedExport||null;selectedAsk=d.selectedAsk||null;followUpId=d.followUpId||null;
      if(followUpId){$('coach-follow-up-context').hidden=false;$('coach-follow-up-context').textContent='Re-asking with previous question and response context.';}
      showRange();setBusy(false);showMode('watch',false);refresh(true);clearInterval(timer);timer=setInterval(()=>refresh(),4000);
    },
    close(){saveDraft();pendingAsk=null;generation++;owner=null;clearInterval(timer);},
    watch({session,clip,player}){
      stopWatch();let pendingSeek=null;
      const requested=new URL(location.href).searchParams.get('coach_time');if(requested!==null&&clip){const t=Number(requested);if(Number.isFinite(t)&&t>=clip.original_start_seconds&&t<=clip.original_end_seconds){const seek=()=>{player.currentTime=t-clip.original_start_seconds;pendingSeek=null;};if(player.readyState)seek();else{pendingSeek=seek;player.addEventListener('loadedmetadata',seek,{once:true});}}}
      stopWatch=()=>{if(pendingSeek)player.removeEventListener('loadedmetadata',pendingSeek);};
    }
  };
})();
