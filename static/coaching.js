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
  let owner=null,timer=null,generation=0,selectedExport=null,selectedAsk=null,pendingAsk=null,busy=false,scanningAuto=false,autoState='ready',stickyNotice='',revision=0,requestId=null,askId=null,askFingerprint=null,lastData=null,historyKey='',followUpId=null,mobileMode='watch',pendingFocus=null,highlightedKey=null;
  let stopWatch=()=>{},stopIntervalSeek=()=>{};
  function isAutoDetectorSupported(){
    return !window.__TENNIS_AUTOFOCUS_FORCE_UNSUPPORTED&&typeof Worker==='function'&&typeof WebAssembly==='object'&&!!window.AutoFocusCore;
  }
  function autoCoachStats(data){
    if(!owner||!Array.isArray(data?.jobs))return {used:0,remaining:2,inFlightRenders:[],completedUnsubmittedRenders:[],inFlightAi:[],failedRenders:[],excludedIntervals:[]};
    const clipJobs=data.jobs.filter(j=>
      j.body?.auto_detect===true&&
      j.body?.source_id===owner.session.id&&
      j.body?.publication===owner.session.published_at&&
      j.body?.clip_id===owner.clip.id
    );
    const aiJobs=clipJobs.filter(j=>j.role==='ai');
    const activeOrConsumedAi=aiJobs.filter(j=>
      ['queued','leased','calling','completed','needs_review'].includes(j.state)||
      (j.state==='failed'&&j.result?.provider_called===true)
    );
    const untransitionedRenders=clipJobs.filter(r=>
      r.role==='render'&&
      ['queued','leased','completed'].includes(r.state)&&
      !aiJobs.some(a=>a.body?.export_id===r.id)
    );
    const inFlightRenders=untransitionedRenders.filter(r=>['queued','leased'].includes(r.state));
    const completedUnsubmittedRenders=untransitionedRenders.filter(r=>r.state==='completed');
    const failedRenders=clipJobs.filter(r=>r.role==='render'&&r.state==='failed'&&!aiJobs.some(a=>a.body?.export_id===r.id));
    const inFlightAi=activeOrConsumedAi.filter(a=>['queued','leased','calling'].includes(a.state));
    const origBase=Number(owner.clip.original_start_seconds)||0;
    const jobLocalInterval=j=>{
      const b=j?.body;
      if(C.finite(b?.start)&&C.finite(b?.end)&&b.end>b.start){
        return {start:Number(b.start),end:Number(b.end)};
      }
      if(C.finite(b?.original_start)&&C.finite(b?.original_end)&&b.original_end>b.original_start){
        return {start:Math.max(0,Number(b.original_start)-origBase),end:Math.max(0,Number(b.original_end)-origBase)};
      }
      if(j?.role==='ai'&&b?.export_id){
        const parentRender=clipJobs.find(r=>r.role==='render'&&r.id===b.export_id);
        if(parentRender)return jobLocalInterval(parentRender);
      }
      return null;
    };
    const excludedIntervals=[...activeOrConsumedAi,...untransitionedRenders].map(jobLocalInterval).filter(Boolean);
    const used=Math.min(2,activeOrConsumedAi.length+untransitionedRenders.length);
    return {
      used,
      remaining:Math.max(0,2-used),
      inFlightRenders,
      completedUnsubmittedRenders,
      inFlightAi,
      failedRenders,
      excludedIntervals
    };
  }
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
      !j.body.auto_detect&&
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
    else{
      if($('coach-ask'))$('coach-ask').disabled=value||!owner;
      if($('coach-auto-ask'))$('coach-auto-ask').disabled=value||!owner||autoState==='unavailable';
    }
  }
  function button(text,action,cls='study-button'){const b=el('button',text,cls);b.type='button';b.onclick=action;return b;}
  function makePanel(){
    const panel=el('section',undefined,'study-panel coaching-panel');panel.id='coaching-panel';
    panel.innerHTML=`<h3>Ask a coach</h3>
      <p class="study-muted">Ask coach uses your selected interval (0.1–30s); Auto detect &amp; ask scans the clip locally for up to 2 player-motion intervals. Applies your zoom and playback speed, sending up to 32 stills per review and your question to OpenAI.</p>
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
        <button class="study-button study-secondary" id="coach-auto-ask" type="button" data-auto-coach-state="ready" data-auto-coach-remaining="2">Auto detect &amp; ask</button>
        <button class="study-button" id="coach-auto-cancel" type="button" hidden>Cancel scan</button>
      </div>
      <p id="coach-auto-quota" class="study-hint"></p>
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
    window.addEventListener('study-capture-state',()=>{if(owner)askAvailability(lastData);});
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
    $('coach-auto-ask').onclick=autoCoach;
    $('coach-auto-cancel').onclick=()=>{window.ClipStudy?.cancelScan?.();};
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
    if(busy||scanningAuto||!owner)return;
    const current=owner;
    try{
      const captured=captureRecipe();
      stickyNotice='';
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
  async function autoCoach(){
    if(busy||scanningAuto||!owner)return;
    if(!isAutoDetectorSupported()){
      autoState='unavailable';
      stickyNotice='Automatic detection is unavailable in this browser. Select an interval and use Ask coach.';
      message(stickyNotice);
      askAvailability(lastData);
      return;
    }
    const current=owner,gen=generation;
    if(!lastData){
      await refresh(true);
      if(owner!==current||gen!==generation)return;
    }
    const stats=autoCoachStats(lastData);
    if(stats.remaining<=0){
      stickyNotice='Automatic coach review limit reached for this clip (2 of 2 used). Use Ask coach for manual selections.';
      message(stickyNotice);
      askAvailability(lastData);
      return;
    }
    const transform=window.ClipStudy?.coachingSelection?.();
    if(!transform){
      message('Wait for the clip to load');
      return;
    }
    const question=$('coach-question').value.trim();
    if(!question){
      message('Enter a question up to 4000 characters');
      return;
    }
    const captured={
      source_id:current.session.id,
      clip_id:current.clip.id,
      publication:current.session.published_at,
      speed:transform.speed||0.5,
      crop:[...(transform.crop||[0,0,1,1])],
      quality:'balanced',
      question,
      playerId:current.clip.main_player?.id||null,
      playerProfileVersion:current.clip.main_player?.version||null,
      batchId:cryptoId()
    };
    stickyNotice='';
    scanningAuto=true;
    autoState='scanning';
    $('study-video')?.pause?.();
    setBusy(true);
    try{
      message('Scanning clip for player motion…');
      const scanResult=await window.ClipStudy.scanPlayerMoments({
        maxWindows:()=>autoCoachStats(lastData).remaining,
        excludedIntervals:()=>autoCoachStats(lastData).excludedIntervals,
        onProgress:({index,total})=>{
          if(owner===current&&gen===generation){
            message(`Scanning clip for player motion (${index}/${total})…`);
          }
        }
      });
      if(owner!==current||gen!==generation)return;
      scanningAuto=false;
      const windows=scanResult?.windows||[];
      if(!windows.length){
        const latestStats=autoCoachStats(lastData);
        autoState='ready';
        stickyNotice=latestStats.excludedIntervals.length
          ?'No additional player-motion moments detected in this clip. Adjust the interval and use Ask coach.'
          :'No clear player motion detected in this clip. Adjust the interval and use Ask coach.';
        message(stickyNotice);
        return;
      }
      const origBase=current.clip.original_start_seconds||0;
      const rangeLabels=windows.map((w,i)=>`#${i+1} ${C.clock(origBase+w.start)}–${C.clock(origBase+w.end)}`);
      message(`Found ${windows.length} player-motion moment${windows.length===1?'':'s'} (${rangeLabels.join(', ')}) · preparing snapshot${windows.length===1?'':'s'}…`);
      const createdRenders=[];
      let partialConflictError='';
      for(let i=0;i<windows.length;i++){
        if(owner!==current||gen!==generation)return;
        const w=windows[i];
        try{
          const renderJob=await api('exports',{
            source_id:captured.source_id,
            clip_id:captured.clip_id,
            publication:captured.publication,
            start:w.start,
            end:w.end,
            speed:captured.speed,
            crop:captured.crop,
            quality:captured.quality,
            question:captured.question,
            player_id:captured.playerId,
            player_profile_version:captured.playerProfileVersion,
            consent:true,
            auto_detect:true,
            auto_slot:i+1,
            request_id:`${captured.batchId}-exp-${i+1}`
          });
          if(owner!==current||gen!==generation)return;
          createdRenders.push(renderJob);
        }catch(err){
          if(owner!==current||gen!==generation)return;
          if(createdRenders.length>0){
            partialConflictError=err?.message||'Second moment skipped due to quota limit.';
            break;
          }
          throw err;
        }
      }
      if(createdRenders.length){
        followUpId=null;
        $('coach-follow-up-context').hidden=true;
        saveDraft();
      }
      if(partialConflictError){
        stickyNotice=`Submitted 1 automatic review (${rangeLabels[0]}); ${partialConflictError}`;
        message(stickyNotice);
      }else{
        stickyNotice=`Auto-detected ${createdRenders.length} moment${createdRenders.length===1?'':'s'} (${rangeLabels.slice(0,createdRenders.length).join(', ')}) · preparing coaching review${createdRenders.length===1?'':'s'}…`;
        message(stickyNotice);
      }
      await refresh(true);
    }catch(e){
      if(owner===current&&gen===generation){
        scanningAuto=false;
        const unavailableCodes=['unavailable','unsupported','worker_graphics_unavailable'];
        if(e?.name==='AbortError'){
          autoState='ready';
          stickyNotice='Auto-detect scan cancelled.';
          message(stickyNotice);
        }else if(unavailableCodes.includes(e?.code)){
          autoState='unavailable';
          stickyNotice='Automatic detection is unavailable in this browser. Select an interval and use Ask coach.';
          message(stickyNotice);
        }else{
          autoState='ready';
          stickyNotice=e?.message||'Auto-detect failed.';
          message(stickyNotice);
        }
      }
    }finally{
      if(owner===current&&gen===generation){
        scanningAuto=false;
        setBusy(false);
        if(lastData)askAvailability(lastData);
      }
    }
  }
  async function advancePendingAutoExports(data,current,gen){
    if(owner!==current||gen!==generation||!current)return false;
    const stats=autoCoachStats(data);
    if(!stats.completedUnsubmittedRenders.length)return false;
    let advanced=false;
    for(const renderJob of stats.completedUnsubmittedRenders){
      if(owner!==current||gen!==generation)return false;
      if(current.failedAutoExports.has(renderJob.id)||current.submittingAutoExports.has(renderJob.id))continue;
      current.submittingAutoExports.add(renderJob.id);
      try{
        const prompt=(renderJob.body.question||$('coach-question')?.value||defaultQuestion()).trim();
        const playerCtx=renderJob.body.player_context||null;
        const hasSavedPlayerCtx=Object.prototype.hasOwnProperty.call(renderJob.body,'player_context');
        await api('requests',{
          player_id:hasSavedPlayerCtx?(playerCtx?playerCtx.player_id:null):(current.clip.main_player?.id||null),
          player_profile_version:hasSavedPlayerCtx?(playerCtx?playerCtx.profile_version:null):(current.clip.main_player?.version||null),
          export_id:renderJob.id,
          prompt,
          consent:true,
          auto_detect:true,
          ...(Number.isInteger(renderJob.body.auto_slot)?{auto_slot:renderJob.body.auto_slot}:{}),
          request_id:'auto-ai-'+renderJob.id
        });
        if(owner!==current||gen!==generation)return false;
        advanced=true;
      }catch(e){
        if(owner!==current||gen!==generation)return false;
        current.failedAutoExports.add(renderJob.id);
        stickyNotice=e?.message||'Failed to submit automatic coaching request.';
        message(stickyNotice);
      }finally{
        current.submittingAutoExports.delete(renderJob.id);
      }
    }
    return advanced;
  }
  function jump(original){
    const clip=owner.clip;
    if(original>=clip.original_start_seconds&&original<clip.original_end_seconds){
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
    const target=owner.session.clips.find(c=>c.original_start_seconds<=original&&original<c.original_end_seconds);
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
    if(!owner)return false;
    const current=owner,gen=generation;
    try{
      await api('annotations/'+a.id,{version:a.version,publication:current.session.published_at,status,...(content?{content}:{})},'PUT');
      if(owner!==current||gen!==generation)return false;
      await refresh(true);
      if(owner!==current||gen!==generation)return true;
      window.dispatchEvent(new Event('coach-notes-changed'));
      message(status==='accepted'?'Suggestion saved.':'Suggestion discarded.');
      return true;
    }catch(e){
      if(owner===current&&gen===generation)message(e.message);
      /* Keep the unsaved edit visible after a conflict. */
      return false;
    }
  }
  function editor(a,job,manual=false){
    const details=el('details',undefined,'coach-editor');details.append(el('summary',manual?'Add your own suggestion':'Edit suggestion or timing'));
    const form=el('form'),fields={};
    const names={title:'Title',topic:'Topic',observation:'Observation',cue:'Playback cue',drill:'Drill',uncertainty:'Uncertainty'};
    const limits={title:120,topic:80,observation:1600,cue:400,drill:1200,uncertainty:1200};
    for(const [key,name] of Object.entries(names)){const label=el('label',name),input=el('textarea');input.rows=key==='cue'?2:1;input.value=a.current[key];input.maxLength=limits[key];label.append(input);form.append(label);fields[key]=input;}
    form.append(el('p','Original recording times · optional','study-hint'));
    const ranges=el('div'),rows=[];
    function addRange(start,end){const row=el('div',undefined,'coach-time-row'),inputs=[];for(const [name,value] of [['Start',start],['End',end]]){const label=el('label',name+' seconds'),input=el('input');input.type='number';input.min=job.body.original_start;input.max=job.body.original_end;input.step='.001';input.required=true;input.value=value;label.append(input);row.append(label);inputs.push(input);}row.append(button('Remove',()=>{row.remove();rows.splice(rows.indexOf(inputs),1);form.dataset.dirty='true';}));rows.push(inputs);ranges.append(row);}
    for(const i of a.current.applies_to)addRange(i.start_seconds,i.end_seconds);
    form.append(ranges,button('Add excerpt interval',()=>{if(rows.length<12){addRange(job.body.original_start,job.body.original_end);form.dataset.dirty='true';}}));
    const refs=el('fieldset');refs.append(el('legend','Evidence frames'));const checks=[];
    for(const f of job.body.evidence_frames||[]){const label=el('label',`Frame ${f.order} · original ${C.clock(f.original_seconds)}`),input=el('input');input.type='checkbox';input.checked=a.current.evidence_frame_ids.includes(f.frame_id);label.prepend(input);refs.append(label);checks.push([input,f.frame_id]);}form.append(refs);
    form.addEventListener('input',()=>{form.dataset.dirty='true';});
    form.addEventListener('change',()=>{form.dataset.dirty='true';});
    function resetEditor(){
      for(const [key,input] of Object.entries(fields))input.value=a.current[key]||'';
      ranges.replaceChildren();rows.length=0;
      for(const i of a.current.applies_to)addRange(i.start_seconds,i.end_seconds);
      for(const [input,frameId] of checks)input.checked=a.current.evidence_frame_ids.includes(frameId);
      form.dataset.dirty='false';
    }
    const actionsRow=el('div',undefined,'coach-actions');
    const submit=el('button',manual?'Save reviewed suggestion':'Save edits and accept','study-button study-primary');submit.type='submit';
    const resetBtn=button('Reset edits',()=>{resetEditor();details.open=false;message('Edits reset.');},'study-button');
    actionsRow.append(submit,resetBtn);
    form.append(actionsRow);
    form.onsubmit=async event=>{
      event.preventDefault();
      if(!owner||submit.disabled)return;
      const current=owner,gen=generation;
      const content=Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.value]));
      content.evidence_frame_ids=checks.filter(([check])=>check.checked).map(([,id])=>id);
      content.applies_to=rows.map(([s,e])=>({start_seconds:Number(s.value),end_seconds:Number(e.value)}));
      submit.disabled=true;
      try{
        if(manual){
          const reqId=details.dataset.requestId||(details.dataset.requestId=cryptoId());
          await api('annotations',{job_id:job.id,publication:current.session.published_at,content,request_id:reqId});
          if(owner!==current||gen!==generation)return;
          form.dataset.dirty='false';
          await refresh(true);
          if(owner!==current||gen!==generation)return;
          window.dispatchEvent(new Event('coach-notes-changed'));
          message('Suggestion saved.');
        }else{
          const ok=await review(a,'accepted',content);
          if(ok&&owner===current&&gen===generation)form.dataset.dirty='false';
        }
      }catch(e){
        if(owner===current&&gen===generation)message(e.message);
      }finally{
        submit.disabled=false;
      }
    };
    details.append(form);return details;
  }
  function suggestionCard(a,job){
    const card=el('article',undefined,'coach-suggestion');card.dataset.annotation=a.id;
    if(highlightedKey==='a:'+a.id)card.classList.add('is-highlighted');
    const stateLabel=a.status==='accepted'?(a.playback_eligible?'Saved · Active in playback':'Saved'):a.status==='dismissed'?'Discarded':'Needs your review';
    const header=el('div',undefined,'coach-suggestion-header');
    const heading=el('div',undefined,'coach-suggestion-heading');
    heading.append(el('p',`${stateLabel}${a.historical?' · Previous publication':''}`,'coach-review-state'),el('h5',a.current.title));
    header.append(heading);
    if(a.status!=='dismissed'){
      const discard=button('×',()=>{
        const form=card.querySelector('.coach-editor form');
        if(form&&form.dataset.dirty==='true'){message('Save or reset your edits before discarding this suggestion.');return;}
        review(a,'dismissed');
      },'coach-discard-btn');
      discard.setAttribute('aria-label','Discard suggestion');
      discard.title='Discard suggestion';
      header.append(discard);
    }
    card.append(header);
    if(a.historical)card.append(el('p',a.playback_eligible?'Reviewed for this video.':'Older video version. Review again to show cues.','study-hint'));
    for(const [key,title] of [['observation','Observed'],['cue','Try'],['drill','Practice'],['uncertainty','Uncertainty']])if(a.current[key]){const p=el('p');p.append(el('strong',title+': '),document.createTextNode(a.current[key]));card.append(p);}
    const timing=el('div',undefined,'coach-timing');if(a.current.applies_to.length)for(const i of a.current.applies_to)timing.append(button(`Original ${C.clock(i.start_seconds)}–${C.clock(i.end_seconds)}`,()=>jump(i.start_seconds)));else timing.append(el('p','Add timing to show a playback cue.','study-muted'));card.append(timing,evidenceStrip(job,a));
    for(const warning of a.current.validation_warnings||[])card.append(el('p',warning+'. Edit references before accepting.','coach-warning'));
    if(a.historical&&!a.playback_eligible&&a.status!=='dismissed'){
      card.append(button('Use for current video',()=>review(a,'accepted'),'study-button study-primary'));
    }else if(a.status==='dismissed'){
      card.append(button('Restore',()=>review(a,'accepted'),'study-button'));
    }else if(a.status==='proposed'&&!a.historical&&!(a.current.validation_warnings&&a.current.validation_warnings.length)){
      card.append(button('Use in playback',()=>review(a,'accepted'),'study-button study-primary'));
    }
    card.append(editor(a,job));
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
  function formatAskedAt(job){
    const ts=Number.isFinite(job?.asked_at)?job.asked_at:null;
    if(ts!==null){
      const d=new Date(ts*1000);
      if(!Number.isNaN(d.valueOf()))return 'Asked '+d.toLocaleString(undefined,{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'});
    }
    if(Number.isFinite(job?.updated)){
      const d=new Date(job.updated*1000);
      if(!Number.isNaN(d.valueOf()))return 'Ask time not recorded · updated '+d.toLocaleString(undefined,{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'});
    }
    return 'Ask time not recorded';
  }
  function history(data){
    const box=$('coach-history');box.replaceChildren();
    const aiJobs=data.jobs
      .filter(j=>j.role==='ai'&&(j.body.clip_id===owner.clip.id||j.body.original_start<owner.clip.original_end_seconds&&j.body.original_end>owner.clip.original_start_seconds));
    const pendingAutoRenders=data.jobs
      .filter(j=>j.role==='render'&&j.body?.auto_detect===true&&j.body?.clip_id===owner.clip.id&&j.body?.publication===owner.session.published_at&&(['queued','leased','failed'].includes(j.state)||(j.state==='completed'&&owner.failedAutoExports.has(j.id)))&&!data.jobs.some(a=>a.role==='ai'&&a.body?.export_id===j.id));
    const jobs=[...aiJobs,...pendingAutoRenders]
      .sort((a,b)=>((Number.isFinite(b.asked_at)?b.asked_at:(b.updated||0))-(Number.isFinite(a.asked_at)?a.asked_at:(a.updated||0)))||String(b.id||'').localeCompare(String(a.id||'')));
    if(!jobs.length){box.append(el('p','No coach responses yet.','study-muted'));return;}
    for(const j of jobs){
      if(j.role==='render'){
        const origStart=(owner.clip.original_start_seconds||0)+(j.body.start||0);
        const origEnd=(owner.clip.original_start_seconds||0)+(j.body.end||0);
        const stateText=j.state==='failed'?'Failed':'Preparing clip…';
        const card=el('article',undefined,'coach-note');card.dataset.job=j.id;card.dataset.role='render';
        card.append(el('strong',`${C.clock(origStart)}–${C.clock(origEnd)} · ${stateText} · Auto-detected`),el('p',formatAskedAt(j),'study-hint coach-asked-at'));
        if(j.body.question){const question=el('details');question.append(el('summary','Question asked'),el('p',j.body.question));card.append(question);}
        if(j.result?.error)card.append(el('p',j.result.error,'coach-warning'));
        if(['queued','leased'].includes(j.state)||(j.state==='completed'&&owner.failedAutoExports.has(j.id))){
          const current=owner,gen=generation;
          card.append(button('Cancel request',async()=>{try{await api('jobs/'+j.id,{action:'cancel',version:j.version},'PUT');if(owner===current&&gen===generation){current.failedAutoExports.delete(j.id);await refresh(true);}}catch(e){if(owner===current&&gen===generation)message(e.message);}}));
        }
        box.append(card);
        continue;
      }
      const card=el('article',undefined,'coach-note');card.dataset.job=j.id;card.append(el('strong',`${C.clock(j.body.original_start)}–${C.clock(j.body.original_end)} · ${states[j.state]||j.state}${j.body.auto_detect?' · Auto-detected':''}${j.body.follow_up_of?' · Follow-up':''}`),el('p',formatAskedAt(j),'study-hint coach-asked-at'));
      if(j.body.player_context&&typeof model==='object'&&model?.players){const p=model.players.find(p=>p.id===j.body.player_context.player_id);card.append(el('p','For '+(p?.name||'saved player'),'study-hint'));}
      const question=el('details');question.append(el('summary','Question asked'),el('p',j.body.prompt));card.append(question);
      if(j.state==='completed'){
        if(j.body.publication===owner.session.published_at)card.append(button('Re-ask',()=>followUp(j),'study-button'));
        if(highlightedKey==='j:'+j.id)card.classList.add('is-highlighted');
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
    const capturing=!!window.ClipStudy?.isCapturing?.();
    const workersEnabled=data?.enabled!==false;
    const hasQuestion=!!$('coach-question')?.value.trim();
    $('coach-ask').disabled=busy||scanningAuto||capturing||exportInProgress||aiInProgress||!workersEnabled||!hasQuestion;
    $('coach-ask').textContent='Ask coach';
    const autoBtn=$('coach-auto-ask'),cancelBtn=$('coach-auto-cancel'),quotaEl=$('coach-auto-quota');
    if(!autoBtn)return;
    const stats=autoCoachStats(data);
    const failedSet=owner?.failedAutoExports;
    const submittingSet=owner?.submittingAutoExports;
    const pendingAutoExports=stats.completedUnsubmittedRenders.filter(r=>!failedSet?.has(r.id));
    let state='ready';
    if(!isAutoDetectorSupported()||autoState==='unavailable'){
      state='unavailable';
    }else if(scanningAuto){
      state='scanning';
    }else if(stats.inFlightRenders.length>0||pendingAutoExports.length>0||stats.inFlightAi.length>0||(submittingSet?.size||0)>0){
      state='submitting';
    }else if(stats.remaining<=0){
      state='capped';
    }
    autoBtn.dataset.autoCoachState=state;
    autoBtn.dataset.autoCoachRemaining=String(stats.remaining);
    autoBtn.disabled=state!=='ready'||busy||capturing||exportInProgress||aiInProgress||!workersEnabled||!hasQuestion;
    autoBtn.textContent=state==='scanning'?'Scanning clip…':state==='submitting'?'Preparing auto review…':state==='capped'?'Auto detect & ask (2/2 used)':state==='unavailable'?'Auto detect unavailable':'Auto detect & ask';
    if(cancelBtn)cancelBtn.hidden=!scanningAuto;
    if(quotaEl){
      if(state==='unavailable'){
        quotaEl.textContent='Automatic detection unavailable in this browser · use Ask coach.';
      }else if(state==='capped'){
        quotaEl.textContent='0 of 2 automatic reviews remaining · Ask coach stays available.';
      }else{
        quotaEl.textContent=`${stats.remaining} of 2 automatic review${stats.remaining===1?'':'s'} remaining`;
      }
    }
  }
  function applyClipInterval(origStart,origEnd){
    stopIntervalSeek();
    if(!owner||!Number.isFinite(origStart))return;
    const current=owner,gen=generation,clip=owner.clip,dur=clip.duration_seconds||1;
    const localStart=C.clamp(origStart-clip.original_start_seconds,0,dur);
    const localEnd=Number.isFinite(origEnd)&&origEnd>origStart?C.clamp(origEnd-clip.original_start_seconds,Math.min(dur,localStart+0.1),dur):dur;
    window.ClipStudy?.setInterval?.(localStart,localEnd);
    const v=$('study-video');
    if(v){
      const expectedSrc=v.getAttribute('src');
      const seek=()=>{
        stopIntervalSeek=()=>{};
        if(owner!==current||gen!==generation||!$('clip-study')?.open||v.getAttribute('src')!==expectedSrc)return;
        v.pause();
        v.currentTime=localStart;
      };
      if(v.readyState>=1)seek();
      else{
        v.addEventListener('loadedmetadata',seek,{once:true});
        stopIntervalSeek=()=>{v.removeEventListener('loadedmetadata',seek);};
      }
    }
  }
  function applyPendingFocus(data){
    if(!pendingFocus||!owner)return {handled:false,preserveMessage:false};
    if(pendingFocus.clipId&&pendingFocus.clipId!==owner.clip.id)return {handled:false,preserveMessage:false};
    const target=pendingFocus;pendingFocus=null;
    const box=$('coach-history');
    for(const el of box.querySelectorAll('.is-highlighted'))el.classList.remove('is-highlighted');
    let card=null,origStart=target.startSeconds,origEnd=target.endSeconds,preserveMessage=false;
    if(target.annotationId){
      const ann=data.annotations.find(a=>a.id===target.annotationId);
      card=[...box.querySelectorAll('.coach-suggestion')].find(n=>n.dataset.annotation===target.annotationId)||null;
      if(ann&&ann.current?.applies_to?.length){
        origStart=Math.min(...ann.current.applies_to.map(i=>i.start_seconds));
        origEnd=Math.max(...ann.current.applies_to.map(i=>i.end_seconds));
      }
      if(card){
        highlightedKey='a:'+target.annotationId;
        if(ann?.status==='dismissed'){
          message('This coaching suggestion was discarded. Use Restore below to include it again.');
          preserveMessage=true;
        }
      }else{
        highlightedKey=null;
        const parentJob=target.jobId?data.jobs.find(j=>j.id===target.jobId):null;
        const parentCard=target.jobId?[...box.querySelectorAll('.coach-note')].find(n=>n.dataset.job===target.jobId)||null:null;
        if(parentJob&&!Number.isFinite(origEnd)){
          if(!Number.isFinite(origStart))origStart=parentJob.body?.original_start;
          origEnd=parentJob.body?.original_end;
        }
        if(Number.isFinite(origStart))applyClipInterval(origStart,origEnd);
        showMode('coach',false);
        if(parentCard){
          parentCard.tabIndex=-1;
          requestAnimationFrame(()=>{
            parentCard.scrollIntoView({block:'center',behavior:'smooth'});
            parentCard.focus({preventScroll:true});
          });
          message('The selected coaching suggestion is unavailable; showing its parent coaching review.');
        }else{
          message('The selected coaching suggestion is unavailable for this clip.');
        }
        return {handled:true,preserveMessage:true};
      }
    }else if(target.jobId){
      const job=data.jobs.find(j=>j.id===target.jobId);
      card=[...box.querySelectorAll('.coach-note')].find(n=>n.dataset.job===target.jobId)||null;
      if(job&&!Number.isFinite(origEnd)){
        if(!Number.isFinite(origStart))origStart=job.body?.original_start;
        origEnd=job.body?.original_end;
      }
      if(card)highlightedKey='j:'+target.jobId;
    }
    if(Number.isFinite(origStart))applyClipInterval(origStart,origEnd);
    if(!card){
      highlightedKey=null;
      showMode('coach',false);
      message('The selected coaching review is unavailable for this clip.');
      return {handled:true,preserveMessage:true};
    }
    card.classList.add('is-highlighted');
    card.tabIndex=-1;
    showMode('coach',false);
    requestAnimationFrame(()=>{
      card.scrollIntoView({block:'center',behavior:'smooth'});
      card.focus({preventScroll:true});
    });
    return {handled:true,preserveMessage};
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
      const advancedAuto=await advancePendingAutoExports(data,current,gen);
      if(owner!==current||gen!==generation)return;
      if(advancedAuto)return await refresh(true);
      const key=JSON.stringify([data.jobs.map(j=>[j.id,j.version,j.state]),data.annotations.map(a=>[a.id,a.version,a.status]),[...current.failedAutoExports]]);
      if(force||key!==historyKey&&!document.activeElement?.closest('#coach-history')&&!$('coach-history').querySelector('.coach-editor[open]')){history(data);historyKey=key;}
      const focusOutcome=applyPendingFocus(data);
      askAvailability(data);
      if(focusOutcome.preserveMessage||scanningAuto)return;
      const stats=autoCoachStats(data);
      const pendingAutoExports=stats.completedUnsubmittedRenders.filter(r=>!current.failedAutoExports.has(r.id));
      if(stickyNotice.startsWith('Auto-detected ')&&!stats.inFlightRenders.length&&!pendingAutoExports.length&&!stats.inFlightAi.length){
        const clipAutoAi=data.jobs.filter(x=>x.role==='ai'&&x.body?.auto_detect===true&&x.body?.clip_id===current.clip.id&&x.body?.publication===current.session.published_at);
        const failedAutoAi=clipAutoAi.find(x=>x.state==='failed');
        if(failedAutoAi){
          stickyNotice=failedAutoAi.result?.error||'Failed to get coach response.';
        }else if(stats.failedRenders.length){
          stickyNotice=stats.failedRenders[0].result?.error||'Failed to prepare clip.';
        }else if(clipAutoAi.some(x=>x.state==='completed')){
          stickyNotice='Auto-detected coach response ready.';
        }
      }
      if(!data.enabled)message('Coaching workers are not configured yet.');
      else if(stickyNotice)message(stickyNotice);
      else if(ai&&['queued','leased','calling'].includes(ai.state))message('Getting response…');
      else if(ai&&ai.state==='failed')message(ai.result?.error||'Failed to get coach response.');
      else if(ai&&ai.state==='needs_review')message(states.needs_review);
      else if(j&&['queued','leased'].includes(j.state))message('Preparing your clip…');
      else if(j&&j.state==='failed'){pendingAsk=null;message(j.result?.error||'Failed to prepare clip.');}
    }catch(e){if(owner===current)message(e.message);}
  }
  window.Coach={
    focusTarget(target){
      pendingFocus=target?{...target}:null;
      if(owner&&lastData)applyPendingFocus(lastData);
    },
    open(session,clip){
      if(!$('coaching-panel'))makePanel();
      stopIntervalSeek();
      generation++;owner={session,clip,failedAutoExports:new Set(),submittingAutoExports:new Set()};selectedExport=null;selectedAsk=null;pendingAsk=null;requestId=null;busy=false;scanningAuto=false;autoState=isAutoDetectorSupported()?'ready':'unavailable';stickyNotice='';historyKey='';lastData=null;highlightedKey=null;
      $('clip-study').dataset.playerCollapsed='false';
      if($('study-mobile-collapse'))$('study-mobile-collapse').textContent='Hide player';
      followUpId=null;$('coach-follow-up-context').hidden=true;$('coach-status').textContent='';
      $('coach-player-context').textContent=clip.main_player?'For '+clip.main_player.name+' · height and level are included; name stays in this library.':'No main player assigned.';
      let d={};try{d=JSON.parse(sessionStorage.getItem(draftKey())||'{}');}catch(_){}
      const focusing=pendingFocus&&(!pendingFocus.clipId||pendingFocus.clipId===clip.id);
      if(focusing&&Number.isFinite(pendingFocus.startSeconds)){
        applyClipInterval(pendingFocus.startSeconds,pendingFocus.endSeconds);
      }else if(Number.isFinite(d.start)&&Number.isFinite(d.end)&&d.start<d.end){
        window.ClipStudy?.setInterval?.(d.start,d.end);
      }
      const savedQuestion=typeof d.question==='string'&&d.question!==oldDefault&&d.question!==defaults?d.question:defaultQuestion();
      $('coach-question').value=savedQuestion;
      const matchingKey=Object.keys(presetPrompts).find(k=>presetQuestion(k)===savedQuestion);
      $('coach-preset').value=matchingKey||'custom';
      selectedExport=d.selectedExport||null;selectedAsk=d.selectedAsk||null;followUpId=d.followUpId||null;
      if(followUpId){$('coach-follow-up-context').hidden=false;$('coach-follow-up-context').textContent='Re-asking with previous question and response context.';}
      showRange();setBusy(false);showMode(focusing?'coach':'watch',false);refresh(true);clearInterval(timer);timer=setInterval(()=>refresh(),4000);
    },
    close(){window.ClipStudy?.cancelScan?.();stopIntervalSeek();saveDraft();pendingAsk=null;pendingFocus=null;highlightedKey=null;scanningAuto=false;stickyNotice='';generation++;owner=null;clearInterval(timer);},
    watch({session,clip,player}){
      stopWatch();let pendingSeek=null;
      const requested=new URL(location.href).searchParams.get('coach_time');
      if(requested!==null&&clip&&player){
        const t=Number(requested);
        if(Number.isFinite(t)&&t>=clip.original_start_seconds&&t<=clip.original_end_seconds){
          const expectedSrc=player.getAttribute('src'),expectedClipId=clip.id,expectedSessionId=session?.id;
          const seek=()=>{
            pendingSeek=null;
            if(typeof model==='object'&&model&&(model.session?.id!==expectedSessionId||model.current!==expectedClipId))return;
            if(player.getAttribute('src')!==expectedSrc)return;
            player.currentTime=t-clip.original_start_seconds;
          };
          if(player.readyState>=1)seek();else{pendingSeek=seek;player.addEventListener('loadedmetadata',seek,{once:true});}
        }
      }
      stopWatch=()=>{if(pendingSeek){player.removeEventListener('loadedmetadata',pendingSeek);pendingSeek=null;}};
    }
  };
})();
