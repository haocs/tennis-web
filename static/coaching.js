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
  function hasSelectedPlayerProfile(){
    const sel=$('coach-subject');
    if(sel)return Boolean(sel.value);
    return Boolean(owner?.clip?.main_player);
  }
  function presetQuestion(key='full',hasProfile=hasSelectedPlayerProfile()){
    const base=presetPrompts[key]||defaults;
    return hasProfile?base.replace(' I am a beginner.',''):base;
  }
  function defaultQuestion(){return presetQuestion('full');}
  let owner=null,timer=null,generation=0,selectedExport=null,selectedAsk=null,pendingAsk=null,busy=false,revision=0,requestId=null,askId=null,askFingerprint=null,lastData=null,historyKey='',followUpId=null,followUpOrigin=null,mobileMode='watch',pendingFocus=null,highlightedKey=null;
  let stopWatch=()=>{},stopIntervalSeek=()=>{},orderedSuggestions=[],activeNavIdx=0;
  const poseCache=new Map(),activeRangeByAnn=new Map();
  function playerUI(){
    return typeof PlayerUI==='object'?PlayerUI:window.PlayerUI;
  }
  function knownPlayers(){
    return typeof model==='object'&&Array.isArray(model?.players)?model.players:[];
  }
  function subjectCatalog(targetOwner=owner){
    if(!targetOwner)return {options:[],defaultValue:'',byId:{}};
    return C.coachingSubjectOptions({
      clip:targetOwner.clip,
      session:targetOwner.session,
      players:knownPlayers(),
      authoritativePlayers:Boolean(targetOwner.authoritativePlayers)
    });
  }
  function updatePlayerContextText(targetOwner=owner){
    const box=$('coach-player-context'),sel=$('coach-subject');
    if(!box||!targetOwner)return;
    box.textContent=C.describeCoachingSubject({
      selectedPlayerId:sel?sel.value:(targetOwner.clip?.main_player?.id||''),
      clip:targetOwner.clip,
      session:targetOwner.session,
      players:knownPlayers(),
      authoritativePlayers:Boolean(targetOwner.authoritativePlayers)
    });
  }
  function syncSubjectOptions(targetOwner=owner,{preferredValue}={}){
    const sel=$('coach-subject');
    if(!sel||!targetOwner)return;
    const {options,defaultValue}=subjectCatalog(targetOwner);
    const currentVal=preferredValue!==undefined?preferredValue:(targetOwner.subjectCustomized?sel.value:defaultValue);
    const resolvedVal=options.some(o=>o.value===currentVal)?currentVal:defaultValue;
    if(resolvedVal!==currentVal)targetOwner.subjectCustomized=false;
    sel.replaceChildren();
    for(const opt of options){
      const node=el('option',opt.label);
      node.value=opt.value;
      sel.append(node);
    }
    sel.value=resolvedVal;
    updatePlayerContextText(targetOwner);
  }
  async function syncPlayersFromServer(targetOwner=owner,gen=generation,{includeSession=false,forceResetIds=false}={}){
    if(!targetOwner)return;
    const prevSel=$('coach-subject')?.value??'';
    const prevById=subjectCatalog(targetOwner).byId;
    const prevVer=prevSel?prevById[prevSel]?.version:null;
    const hadProfile=hasSelectedPlayerProfile();
    try{
      const pResp=await fetch('/api/players');
      if(pResp.ok){
        const pData=await pResp.json();
        if(Array.isArray(pData?.players)){
          if(typeof model==='object'&&model)model.players=pData.players;
          if(owner===targetOwner&&gen===generation)targetOwner.authoritativePlayers=true;
        }
      }
      if(includeSession&&owner===targetOwner&&gen===generation){
        const sResp=await fetch('/api/sessions/'+targetOwner.session.id);
        if(sResp.ok){
          const fresh=await sResp.json();
          if(owner===targetOwner&&gen===generation&&fresh?.published_at===targetOwner.session.published_at){
            Object.assign(targetOwner.session,fresh);
            const freshClip=(fresh.clips||[]).find(c=>c.id===targetOwner.clip.id);
            if(freshClip)Object.assign(targetOwner.clip,freshClip);
            if(typeof model==='object'&&model?.session?.id===targetOwner.session.id){
              Object.assign(model.session,fresh);
              playerUI()?.syncReview?.();
              playerUI()?.current?.(model.session.clips?.find(c=>c.id===model.current));
            }
          }
        }
      }
    }catch(_){/* Keep cached player list on transient error. */}
    if(owner!==targetOwner||gen!==generation)return;
    const prevPreset=$('coach-preset')?.value||'full';
    syncSubjectOptions(targetOwner);
    const nextSel=$('coach-subject')?.value??'';
    const nextVer=nextSel?subjectCatalog(targetOwner).byId[nextSel]?.version:null;
    const subjectChanged=nextSel!==prevSel||nextVer!==prevVer;
    if(hasSelectedPlayerProfile()!==hadProfile&&prevPreset!=='custom'&&$('coach-question')){
      $('coach-question').value=presetQuestion(prevPreset);
    }
    if(followUpId&&followUpOrigin){
      try{
        const snap=C.resolveCoachingSubject({
          selectedPlayerId:nextSel,
          clip:targetOwner.clip,
          session:targetOwner.session,
          players:knownPlayers(),
          authoritativePlayers:Boolean(targetOwner.authoritativePlayers),
          subjectHint:$('coach-subject-hint')?.value||''
        });
        if(snap.subjectMode!==followUpOrigin.subjectMode||snap.playerId!==followUpOrigin.playerId||snap.playerProfileVersion!==followUpOrigin.playerProfileVersion){
          followUpId=null;
          followUpOrigin=null;
          if($('coach-follow-up-context')){
            $('coach-follow-up-context').textContent='Player profile or clip assignment changed — starting a new review with the current profile.';
            $('coach-follow-up-context').hidden=false;
          }
        }
      }catch(_){
        followUpId=null;
        followUpOrigin=null;
      }
    }
    if(forceResetIds||subjectChanged){
      requestId=null;askFingerprint=null;askId=null;
    }
    saveDraft();
    if(lastData&&(forceResetIds||subjectChanged)){historyKey='';history(lastData);askAvailability(lastData);}
  }
  function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
  async function api(path,body,method='POST'){
    const r=await fetch('/api/coaching/'+path,body===undefined?{}:{method,headers:{'Content-Type':'application/json','X-Tennis-Request':'1'},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok){
      const err=new Error(d.error||'Request unavailable');
      err.status=r.status;
      throw err;
    }
    return d;
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
      sessionStorage.setItem(draftKey(),JSON.stringify({
        start:sel.start,
        end:sel.end,
        preset:$('coach-preset')?.value||'full',
        question:$('coach-question')?.value||'',
        subjectCustomized:Boolean(owner.subjectCustomized),
        subjectValue:$('coach-subject')?.value??'',
        subjectHint:$('coach-subject-hint')?.value||'',
        selectedExport,
        selectedAsk,
        followUpId,
        followUpOrigin
      }));
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
  window.addEventListener('resize',()=>requestAnimationFrame(mobileOffsets));
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
    if($('coach-subject'))$('coach-subject').disabled=value;
    if($('coach-manage-players'))$('coach-manage-players').disabled=value;
    if($('coach-subject-hint'))$('coach-subject-hint').disabled=value;
    if(lastData)askAvailability(lastData);
    else{
      if($('coach-ask'))$('coach-ask').disabled=value||!owner;
    }
  }
  function button(text,action,cls='study-button'){const b=el('button',text,cls);b.type='button';b.onclick=action;return b;}
  function makePanel(){
    const panel=el('section',undefined,'study-panel coaching-panel');panel.id='coaching-panel';
    panel.innerHTML=`<h3>Ask a coach</h3>
      <p class="study-muted">Select a 0.1–30s interval (or use Auto select &amp; zoom above), set zoom and speed, then click Ask coach to send up to 32 stills and your question to AI coach.</p>
      <p id="coach-range"></p>
      <div class="coach-subject-group">
        <div class="coach-subject-label-row">
          <label for="coach-subject">Player to coach</label>
          <button class="study-button coach-manage-players" id="coach-manage-players" type="button" title="Add or edit saved player profiles without changing clip or recording players">Saved players…</button>
        </div>
        <select id="coach-subject" aria-label="Player to coach"></select>
        <p id="coach-player-context" class="study-hint"></p>
        <label for="coach-subject-hint">Visible target cue <span>(optional, sent to AI coach)</span><input id="coach-subject-hint" type="text" maxlength="160" placeholder="e.g. far-court player in dark shirt (no names)"></label>
      </div>
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
    window.addEventListener('study-inspection-changed',syncInspectionChrome);
    window.addEventListener('study-capture-state',()=>{if(owner)askAvailability(lastData);});
    window.addEventListener('tennis-players-updated',e=>{
      if(!owner)return;
      owner.authoritativePlayers=true;
      if(e?.detail?.session&&e.detail.session.id===owner.session.id&&e.detail.session.published_at===owner.session.published_at){
        Object.assign(owner.session,e.detail.session);
        const freshClip=(e.detail.session.clips||[]).find(c=>c.id===owner.clip.id);
        if(freshClip)Object.assign(owner.clip,freshClip);
      }
      const hadProfile=hasSelectedPlayerProfile();
      const prevPreset=$('coach-preset')?.value||'full';
      syncSubjectOptions(owner);
      if(hasSelectedPlayerProfile()!==hadProfile&&prevPreset!=='custom'&&$('coach-question')){
        $('coach-question').value=presetQuestion(prevPreset);
      }
      requestId=null;askFingerprint=null;askId=null;
      saveDraft();
      if(lastData){historyKey='';history(lastData);askAvailability(lastData);}
    });
    $('coach-manage-players').onclick=()=>{playerUI()?.openSaved?.();};
    $('coach-subject').addEventListener('change',()=>{
      if(!owner)return;
      const defaultVal=subjectCatalog(owner).defaultValue;
      owner.subjectCustomized=$('coach-subject').value!==defaultVal;
      updatePlayerContextText(owner);
      const presetKey=$('coach-preset').value;
      if(presetKey!=='custom'){
        $('coach-question').value=presetQuestion(presetKey);
      }
      if(followUpId&&followUpOrigin){
        const nextPid=$('coach-subject').value||null;
        if(nextPid!==followUpOrigin.playerId){
          followUpId=null;
          followUpOrigin=null;
          $('coach-follow-up-context').textContent='Switched coaching player — starting a new independent review without the previous player’s response context.';
          $('coach-follow-up-context').hidden=false;
        }
      }
      requestId=null;askFingerprint=null;askId=null;
      saveDraft();
      if(lastData)askAvailability(lastData);
    });
    $('coach-subject-hint').addEventListener('input',()=>{
      if(!owner)return;
      requestId=null;askFingerprint=null;askId=null;
      saveDraft();
    });
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
  function captureSubjectSnapshot(targetOwner=owner){
    return C.resolveCoachingSubject({
      selectedPlayerId:$('coach-subject')?.value??(targetOwner?.clip?.main_player?.id||''),
      clip:targetOwner?.clip,
      session:targetOwner?.session,
      players:knownPlayers(),
      authoritativePlayers:Boolean(targetOwner?.authoritativePlayers),
      subjectHint:$('coach-subject-hint')?.value||''
    });
  }
  function captureRecipe(){
    const transform=window.ClipStudy.coachingSelection();if(!transform)throw new Error('Wait for the clip to load');
    const {start,end,speed,crop}=transform;
    if(start>=end||end-start>30||end-start<.1)throw new Error('Select between 0.1 and 30 seconds');
    const question=$('coach-question').value.trim();
    if(!question)throw new Error('Enter a question up to 4000 characters');
    const subject=captureSubjectSnapshot(owner);
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
      subjectMode:subject.subjectMode,
      playerId:subject.playerId,
      playerProfileVersion:subject.playerProfileVersion,
      subjectHint:subject.subjectHint
    };
  }
  async function submitAiRequest(exportId,captured){
    const current=owner;
    const fingerprint=JSON.stringify([exportId,captured.question,captured.followUpOf,captured.subjectMode,captured.playerId,captured.playerProfileVersion,captured.subjectHint]);
    if(fingerprint!==askFingerprint){askFingerprint=fingerprint;askId=cryptoId();}
    message('Getting response…');
    const j=await api('requests',{
      ...(captured.subjectMode?{subject_mode:captured.subjectMode}:{}),
      player_id:captured.playerId,
      player_profile_version:captured.playerProfileVersion,
      ...(captured.subjectHint?{subject_hint:captured.subjectHint}:{}),
      export_id:exportId,
      prompt:captured.question,
      consent:true,
      request_id:askId,
      ...(captured.followUpOf?{follow_up_of:captured.followUpOf}:{})
    });
    if(owner!==current)return;
    selectedAsk=j.id;
    followUpId=null;
    followUpOrigin=null;
    $('coach-follow-up-context').hidden=true;
    saveDraft();
  }
  async function askCoach(){
    if(busy||!owner)return;
    const current=owner,gen=generation;
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
        ...(captured.subjectMode?{subject_mode:captured.subjectMode}:{}),
        player_id:captured.playerId,
        player_profile_version:captured.playerProfileVersion,
        ...(captured.subjectHint?{subject_hint:captured.subjectHint}:{}),
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
      if(owner===current&&gen===generation){
        pendingAsk=null;
        requestId=null;
        message(e.message);
        if(e?.status===409)await syncPlayersFromServer(current,gen,{includeSession:true,forceResetIds:true});
      }
    }finally{
      if(owner===current){
        setBusy(false);
        if(lastData)askAvailability(lastData);
      }
    }
  }
  function revealMobilePlayer(){
    if(!$('clip-study'))return;
    $('clip-study').dataset.playerCollapsed='false';
    if($('study-mobile-collapse'))$('study-mobile-collapse').textContent='Hide player';
    requestAnimationFrame(mobileOffsets);
  }
  function syncCoachMarkers(data=lastData){
    if(!owner||!window.ClipStudy?.setCoachMarkers)return;
    if(!data||!Array.isArray(data.annotations)){window.ClipStudy.setCoachMarkers([]);return;}
    const clip=owner.clip,dur=Math.max(0.1,clip.duration_seconds||1),markers=[],jobById=new Map((data.jobs||[]).map(j=>[j.id,j]));
    for(const a of data.annotations){
      if(a.status==='dismissed'||a.historical)continue;
      const isSelected=highlightedKey==='a:'+a.id;
      const ranges=C.selectSuggestionRanges(a.current?.applies_to||[],clip.original_start_seconds,clip.original_end_seconds,dur);
      const selectedRangeIdx=activeRangeByAnn.get(a.id)??0;
      for(const r of ranges){
        markers.push({type:'range',start:r.start,end:r.end,active:isSelected&&selectedRangeIdx===r.index});
      }
      const job=jobById.get(a.job_id);
      const refs=a.current?.evidence_frame_ids||[];
      for(const f of (job?.body?.evidence_frames||[])){
        if(!refs.includes(f.frame_id))continue;
        if(f.original_seconds<clip.original_start_seconds||f.original_seconds>clip.original_end_seconds)continue;
        markers.push({
          type:'frame',
          time:C.clamp(f.original_seconds-clip.original_start_seconds,0,dur),
          primary:f.frame_id===a.current?.primary_frame_id,
          active:isSelected
        });
      }
    }
    window.ClipStudy.setCoachMarkers(markers);
  }
  function syncInspectionChrome(){
    const dialog=$('clip-study');
    const state=window.ClipStudy?.getInspectionState?.()||null;
    if(dialog)dialog.dataset.coachInspecting=String(Boolean(state));
    const exitBtn=$('coach-exit-inspect');
    if(exitBtn)exitBtn.hidden=!state;
    const toggleBtn=$('coach-overlay-toggle');
    if(toggleBtn){
      const enabled=state?state.overlayEnabled!==false:(toggleBtn.getAttribute('aria-pressed')!=='false');
      toggleBtn.setAttribute('aria-pressed',String(enabled));
    }
    const navCount=$('coach-nav-count');
    if(navCount&&orderedSuggestions.length){
      navCount.textContent=`Suggestion ${activeNavIdx+1} of ${orderedSuggestions.length}`;
    }
    const box=$('coach-history');
    if(box){
      for(const card of box.querySelectorAll('.coach-suggestion')){
        const id=card.dataset.annotation;
        const selected='a:'+id===highlightedKey;
        card.classList.toggle('is-highlighted',selected);
        const selIdx=activeRangeByAnn.get(id)??0;
        for(const chip of card.querySelectorAll('.coach-range-chip')){
          const idx=Number(chip.dataset.rangeIndex);
          chip.classList.toggle('is-selected-range',idx===selIdx);
          chip.setAttribute('aria-pressed',String(idx===selIdx));
        }
      }
    }
    syncCoachMarkers(lastData);
    requestAnimationFrame(mobileOffsets);
  }
  async function fetchPoseEnrichment(job,annotation){
    const hasPose=Boolean(annotation?.provenance?.has_pose_enrichment??job?.body?.has_pose_enrichment);
    if(!hasPose||!job?.id)return null;
    if(poseCache.has(job.id))return poseCache.get(job.id);
    const promise=fetch('/api/coaching/evidence/'+encodeURIComponent(job.id)+'/pose',{headers:{'X-Tennis-Request':'1'}})
      .then(r=>r.ok?r.json():null)
      .catch(()=>null);
    poseCache.set(job.id,promise);
    return promise;
  }
  function resolveSuggestionTargets(a,job,rangeIndexOverride=null){
    const clip=owner.clip,dur=Math.max(0.1,clip?.duration_seconds||1);
    const ranges=C.selectSuggestionRanges(a.current?.applies_to||[],clip.original_start_seconds,clip.original_end_seconds,dur);
    let rIdx=-1;
    if(typeof rangeIndexOverride==='number'&&rangeIndexOverride>=0&&ranges[rangeIndexOverride]){
      rIdx=rangeIndexOverride;
    }else if(ranges.length){
      const stored=activeRangeByAnn.get(a.id);
      rIdx=typeof stored==='number'&&ranges[stored]?stored:0;
    }
    const range=rIdx>=0?ranges[rIdx]:null;
    const allFrames=(job?.body?.evidence_frames||[]).map(f=>({
      ...f,
      clip_seconds:(f.original_seconds>=clip.original_start_seconds&&f.original_seconds<=clip.original_end_seconds)
        ?(C.finite(f.clip_seconds)?f.clip_seconds:C.clamp(f.original_seconds-clip.original_start_seconds,0,dur))
        :null
    }));
    const refs=a.current?.evidence_frame_ids||[];
    const cited=allFrames.filter(f=>refs.includes(f.frame_id));
    let primary=allFrames.find(f=>f.frame_id===a.current?.primary_frame_id)||null;
    if(!primary&&range){
      primary=cited.find(f=>f.original_seconds>=range.originalStart-0.02&&f.original_seconds<=range.originalEnd+0.02)||null;
    }
    if(!primary)primary=cited[0]||null;
    return {ranges,rangeIndex:rIdx,range,primaryFrame:primary};
  }
  async function inspectCardSuggestion(a,job,mode='frame',{frame=null,rangeIndex=null}={}){
    if(!owner||!window.ClipStudy)return;
    const current=owner,gen=generation;
    const targets=resolveSuggestionTargets(a,job,rangeIndex);
    if(targets.rangeIndex>=0)activeRangeByAnn.set(a.id,targets.rangeIndex);
    const clip=owner.clip,dur=Math.max(0.1,clip.duration_seconds||1);
    const pickedFrame=frame
      ?{...frame,clip_seconds:(frame.original_seconds>=clip.original_start_seconds&&frame.original_seconds<=clip.original_end_seconds)?C.clamp(frame.original_seconds-clip.original_start_seconds,0,dur):null}
      :targets.primaryFrame;
    if(mode==='frame'&&pickedFrame&&pickedFrame.clip_seconds===null){
      jump(pickedFrame.original_seconds);
      return;
    }
    highlightedKey='a:'+a.id;
    const foundNav=orderedSuggestions.findIndex(item=>item.ann.id===a.id);
    if(foundNav>=0)activeNavIdx=foundNav;
    revealMobilePlayer();
    syncInspectionChrome();
    const card=orderedSuggestions[foundNav]?.card;
    if(card)card.scrollIntoView({block:'nearest',behavior:'smooth'});
    const poseData=await fetchPoseEnrichment(job,a);
    if(owner!==current||gen!==generation)return;
    const poseFrame=pickedFrame&&poseData?.frames?(poseData.frames[pickedFrame.frame_id]||null):null;
    const payload={
      suggestion:a.current,
      frame:pickedFrame,
      poseFrame,
      crop:job?.body?.crop||[0,0,1,1],
      range:targets.range,
      rangeIndex:targets.rangeIndex,
      historical:Boolean(a.historical),
      nowTry:C.formatNowTry(a.current),
    };
    if(mode==='loop'){
      if(!targets.range){message('This suggestion has no timed moment on the current clip.');return;}
      await window.ClipStudy.loopMoment(payload);
      if(owner!==current||gen!==generation)return;
      message(`Looping moment ${targets.rangeIndex+1} (${C.clock(targets.range.originalStart)}–${C.clock(targets.range.originalEnd)}).`);
    }else if(mode==='walkthrough'){
      if(!targets.range&&!pickedFrame){message('No frame or timing available for walkthrough.');return;}
      const wt=await window.ClipStudy.startWalkthrough(payload);
      if(owner!==current||gen!==generation)return;
      if(wt?.started&&!wt?.reducedMotion)message('Walkthrough active · slow motion with pause at key frame.');
    }else{
      const res=window.ClipStudy.inspectSuggestion(payload);
      if(poseFrame?.subject_ambiguous){
        message(poseFrame.ambiguity_reason||'Target player uncertain in this frame; showing original image.');
      }else if(pickedFrame){
        message(`Inspecting frame ${pickedFrame.order} · Original ${C.clock(pickedFrame.original_seconds)}${res?.visible?' · Approximate body guide':''}.`);
      }else if(targets.range){
        message(`Inspecting moment ${C.clock(targets.range.originalStart)}–${C.clock(targets.range.originalEnd)}.`);
      }
    }
    syncInspectionChrome();
  }
  function jump(original){
    const clip=owner.clip;
    if(original>=clip.original_start_seconds&&original<clip.original_end_seconds){
      revealMobilePlayer();
      const localTime=original-clip.original_start_seconds;
      window.ClipStudy?.inspectSuggestion?.({
        suggestion:{title:'',cue:'',body_areas:[]},
        frame:{frame_id:'',order:0,original_seconds:original,clip_seconds:localTime},
        poseFrame:null,
        crop:[0,0,1,1],
        range:null,
        rangeIndex:-1,
        historical:false,
      });
      message(`Original ${C.clock(original)}`);
      if(mobile())document.querySelector('.study-workspace').scrollIntoView({block:'start',behavior:'smooth'});
      return;
    }
    const target=owner.session.clips.find(c=>c.original_start_seconds<=original&&original<c.original_end_seconds);
    if(target){location.href=`/review/${owner.session.id}?clip=${target.id}&coach_time=${original}`;}
    else message('This moment was cut from the current video. See the saved frame.');
  }
  function evidenceStrip(job,annotation,interactiveAnn=null){
    const strip=el('div',undefined,'coach-evidence');const refs=annotation.current.evidence_frame_ids;
    const frames=(job.body.evidence_frames||[]).filter(f=>refs.includes(f.frame_id));
    if(!frames.length){strip.append(el('p','No verified frame references.','study-muted'));return strip;}
    for(const f of frames){
      const item=el('figure'),image=el('img');
      image.src=`/api/coaching/evidence/${job.id}/${f.frame_id}.jpg`;
      image.loading='lazy';
      image.alt=`Submitted frame ${f.order}, original ${C.clock(f.original_seconds)}`;
      const isPrimary=interactiveAnn&&f.frame_id===annotation.current?.primary_frame_id;
      const go=button(
        `Frame ${f.order} · ${C.clock(f.original_seconds)}`,
        ()=>interactiveAnn?inspectCardSuggestion(interactiveAnn,job,'frame',{frame:f}):jump(f.original_seconds),
        'coach-frame-link'+(isPrimary?' is-primary-frame':'')
      );
      go.append(image);
      item.append(go,el('figcaption',`Clip ${C.clock(f.clip_seconds)} · excerpt ${C.clock(f.excerpt_seconds)}${isPrimary?' · Primary':''}`));
      strip.append(item);
    }
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
    for(const [key,name] of Object.entries(names)){const label=el('label',name),input=el('textarea');input.rows=key==='cue'?2:1;input.value=a.current[key]||'';input.maxLength=limits[key];label.append(input);form.append(label);fields[key]=input;}

    const nowLabel=el('label','Now summary · optional'),nowInput=el('input');
    nowInput.type='text';nowInput.maxLength=180;nowInput.value=a.current.now_summary||'';
    nowLabel.append(nowInput);form.append(nowLabel);

    const phaseLabel=el('label','Stroke phase · optional'),phaseSel=el('select');
    phaseSel.append(new Option('Unclear / not specified',''));
    for(const [val,txt] of Object.entries(C.STROKE_PHASE_LABELS||{}))phaseSel.append(new Option(txt,val));
    phaseSel.value=a.current.stroke_phase||'';
    phaseLabel.append(phaseSel);form.append(phaseLabel);

    const primaryLabel=el('label','Primary frame · optional'),primarySel=el('select');
    primarySel.append(new Option('None / uncertain',''));
    for(const f of job.body.evidence_frames||[])primarySel.append(new Option(`Frame ${f.order} · original ${C.clock(f.original_seconds)}`,f.frame_id));
    primarySel.value=a.current.primary_frame_id||'';
    primaryLabel.append(primarySel);form.append(primaryLabel);

    const areasFs=el('fieldset');areasFs.append(el('legend','Body areas · optional'));const areaChecks=[];
    const initAreas=Array.isArray(a.current.body_areas)?a.current.body_areas:[];
    for(const [key,txt] of Object.entries(C.BODY_AREA_LABELS||{})){
      const label=el('label',txt),input=el('input');input.type='checkbox';input.checked=initAreas.includes(key);
      label.prepend(input);areasFs.append(label);areaChecks.push([input,key]);
    }
    form.append(areasFs);

    form.append(el('p','Original recording times · optional','study-hint'));
    const ranges=el('div'),rows=[];
    function addRange(start,end){const row=el('div',undefined,'coach-time-row'),inputs=[];for(const [name,value] of [['Start',start],['End',end]]){const label=el('label',name+' seconds'),input=el('input');input.type='number';input.min=job.body.original_start;input.max=job.body.original_end;input.step='.001';input.required=true;input.value=value;label.append(input);row.append(label);inputs.push(input);}row.append(button('Remove',()=>{row.remove();rows.splice(rows.indexOf(inputs),1);form.dataset.dirty='true';}));rows.push(inputs);ranges.append(row);}
    for(const i of a.current.applies_to)addRange(i.start_seconds,i.end_seconds);
    form.append(ranges,button('Add excerpt interval',()=>{if(rows.length<12){addRange(job.body.original_start,job.body.original_end);form.dataset.dirty='true';}}));
    const refs=el('fieldset');refs.append(el('legend','Evidence frames'));const checks=[];
    for(const f of job.body.evidence_frames||[]){
      const label=el('label',`Frame ${f.order} · original ${C.clock(f.original_seconds)}`),input=el('input');
      input.type='checkbox';input.checked=a.current.evidence_frame_ids.includes(f.frame_id);
      input.addEventListener('change',()=>{if(!input.checked&&primarySel.value===f.frame_id)primarySel.value='';});
      label.prepend(input);refs.append(label);checks.push([input,f.frame_id]);
    }
    primarySel.addEventListener('change',()=>{
      if(primarySel.value){const match=checks.find(([,id])=>id===primarySel.value);if(match)match[0].checked=true;}
    });
    form.append(refs);
    form.addEventListener('input',()=>{form.dataset.dirty='true';});
    form.addEventListener('change',()=>{form.dataset.dirty='true';});
    function resetEditor(){
      for(const [key,input] of Object.entries(fields))input.value=a.current[key]||'';
      nowInput.value=a.current.now_summary||'';
      phaseSel.value=a.current.stroke_phase||'';
      primarySel.value=a.current.primary_frame_id||'';
      const savedAreas=Array.isArray(a.current.body_areas)?a.current.body_areas:[];
      for(const [input,key] of areaChecks)input.checked=savedAreas.includes(key);
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
      content.now_summary=nowInput.value;
      content.stroke_phase=phaseSel.value;
      content.primary_frame_id=primarySel.value&&(!content.evidence_frame_ids.length||content.evidence_frame_ids.includes(primarySel.value))?primarySel.value:'';
      content.body_areas=areaChecks.filter(([check])=>check.checked).map(([,k])=>k);
      content.focus_region=content.primary_frame_id&&content.primary_frame_id===(a.current.primary_frame_id||'')?(a.current.focus_region??null):null;
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

    const nowTry=C.formatNowTry(a.current);
    if(nowTry.phaseLabel||nowTry.bodyAreaLabels.length){
      const chips=el('div',undefined,'coach-meta-chips');
      if(nowTry.phaseLabel)chips.append(el('span',nowTry.phaseLabel,'coach-meta-chip coach-phase-chip'));
      for(const area of nowTry.bodyAreaLabels)chips.append(el('span',area,'coach-meta-chip'));
      card.append(chips);
    }
    if(nowTry.nowText||nowTry.tryText){
      const boxNow=el('div',undefined,'coach-now-try');
      if(nowTry.nowText){const p=el('p',undefined,'coach-now-line');p.append(el('strong','Now: '),document.createTextNode(nowTry.nowText));boxNow.append(p);}
      if(nowTry.tryText){const p=el('p',undefined,'coach-try-line');p.append(el('strong','Try: '),document.createTextNode(nowTry.tryText));boxNow.append(p);}
      card.append(boxNow);
    }
    for(const [key,title] of [['observation','Observed'],['drill','Practice'],['uncertainty','Uncertainty']])if(a.current[key]){const p=el('p');p.append(el('strong',title+': '),document.createTextNode(a.current[key]));card.append(p);}

    const targets=resolveSuggestionTargets(a,job);
    if(!a.historical&&(targets.primaryFrame||targets.ranges.length)){
      const inspectBar=el('div',undefined,'coach-inspect-bar');
      inspectBar.append(button('Inspect frame',()=>inspectCardSuggestion(a,job,'frame'),'study-button coach-inspect-btn'));
      if(targets.ranges.length){
        inspectBar.append(button('Loop moment',()=>inspectCardSuggestion(a,job,'loop'),'study-button coach-inspect-btn'));
      }
      inspectBar.append(button('Walkthrough',()=>inspectCardSuggestion(a,job,'walkthrough'),'study-button coach-inspect-btn'));
      card.append(inspectBar);
    }

    const timing=el('div',undefined,'coach-timing');
    if(a.current.applies_to.length){
      const clip=owner.clip;
      let clipRangeCounter=0;
      for(const i of a.current.applies_to){
        const overlapsClip=i.start_seconds<=clip.original_end_seconds+1e-6&&i.end_seconds>=clip.original_start_seconds-1e-6;
        if(overlapsClip){
          const thisIdx=clipRangeCounter++;
          const prefix=targets.ranges.length>1?`Moment ${thisIdx+1} · `:'Original ';
          const b=button(`${prefix}${C.clock(i.start_seconds)}–${C.clock(i.end_seconds)}`,()=>inspectCardSuggestion(a,job,'frame',{rangeIndex:thisIdx}),'study-button coach-range-chip');
          b.dataset.rangeIndex=String(thisIdx);
          timing.append(b);
        }else{
          timing.append(button(`Original ${C.clock(i.start_seconds)}–${C.clock(i.end_seconds)}`,()=>jump(i.start_seconds)));
        }
      }
    }else{
      timing.append(el('p','Add timing to show a playback cue.','study-muted'));
    }
    card.append(timing,evidenceStrip(job,a,a));
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
    selectedExport=exported.id;selectedAsk=null;pendingAsk=null;askId=null;askFingerprint=null;requestId=null;
    if(Number.isFinite(exported.body.start)&&Number.isFinite(exported.body.end)){
      window.ClipStudy?.setInterval?.(exported.body.start,exported.body.end);
    }
    const parentPlayerId=job.body.player_context?.player_id||'';
    const parentProfileVersion=job.body.player_context?.profile_version??null;
    const parentMode=job.body.subject_mode||(job.body.player_context?'main_player':'none');
    const parentHint=job.body.subject_hint||'';
    const catalog=subjectCatalog(owner);
    const parentAvailable=!parentPlayerId||catalog.options.some(o=>o.value===parentPlayerId);
    if($('coach-subject-hint'))$('coach-subject-hint').value=parentHint;
    if(!parentAvailable){
      owner.subjectCustomized=false;
      syncSubjectOptions(owner,{preferredValue:catalog.defaultValue});
      followUpId=null;
      followUpOrigin=null;
      $('coach-follow-up-context').textContent='Original player profile was removed — starting a new independent review.';
      $('coach-follow-up-context').hidden=false;
    }else{
      owner.subjectCustomized=parentPlayerId!==catalog.defaultValue;
      syncSubjectOptions(owner,{preferredValue:parentPlayerId});
      const resolved=captureSubjectSnapshot(owner);
      if(resolved.subjectMode===parentMode&&resolved.playerId===(parentPlayerId||null)&&resolved.playerProfileVersion===parentProfileVersion){
        followUpId=job.id;
        followUpOrigin={jobId:job.id,subjectMode:parentMode,playerId:resolved.playerId,playerProfileVersion:resolved.playerProfileVersion};
        $('coach-follow-up-context').textContent='Re-asking with previous question and response context.';
        $('coach-follow-up-context').hidden=false;
      }else{
        followUpId=null;
        followUpOrigin=null;
        $('coach-follow-up-context').textContent='Player profile or clip assignment changed since that review — starting a new review with the current profile.';
        $('coach-follow-up-context').hidden=false;
      }
    }
    $('coach-preset').value='custom';
    $('coach-question').value='';
    saveDraft();
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
  function formatJobSubject(job){
    const b=job?.body||{};
    const ctx=b.player_context||null;
    const mode=b.subject_mode||null;
    const hint=C.normalizeSubjectHint(b.subject_hint||'');
    const byId=subjectCatalog(owner).byId;
    let label='';
    if(ctx&&ctx.player_id){
      const p=byId[ctx.player_id]||knownPlayers().find(x=>x.id===ctx.player_id)||(owner?.clip?.main_player?.id===ctx.player_id?owner.clip.main_player:null);
      const name=p?.name||'saved player';
      const isOverride=mode==='selected_player'&&ctx.player_id!==owner?.clip?.main_player?.id;
      label=isOverride?`For ${name} · selected for this review`:`For ${name}`;
    }else if(mode==='none'){
      label='For unassigned player (no profile)';
    }
    if(hint){
      label=label?`${label} · Target: ${hint}`:`Target: ${hint}`;
    }
    return label;
  }
  function history(data){
    const box=$('coach-history');box.replaceChildren();
    const aiJobs=data.jobs
      .filter(j=>j.role==='ai'&&(j.body.clip_id===owner.clip.id||j.body.original_start<owner.clip.original_end_seconds&&j.body.original_end>owner.clip.original_start_seconds));
    const pendingAutoRenders=data.jobs
      .filter(j=>j.role==='render'&&j.body?.auto_detect===true&&j.body?.clip_id===owner.clip.id&&j.body?.publication===owner.session.published_at&&['queued','leased','failed'].includes(j.state)&&!data.jobs.some(a=>a.role==='ai'&&a.body?.export_id===j.id));
    const jobs=[...aiJobs,...pendingAutoRenders]
      .sort((a,b)=>((Number.isFinite(b.asked_at)?b.asked_at:(b.updated||0))-(Number.isFinite(a.asked_at)?a.asked_at:(a.updated||0)))||String(b.id||'').localeCompare(String(a.id||'')));
    if(!jobs.length){orderedSuggestions=[];box.append(el('p','No coach responses yet.','study-muted'));syncCoachMarkers(data);return;}
    orderedSuggestions=[];
    const navBar=el('div',undefined,'coach-nav');navBar.id='coach-nav';
    const navLeft=el('div',undefined,'coach-nav-row');
    const prevBtn=button('← Prev',()=>{
      if(!orderedSuggestions.length)return;
      activeNavIdx=(activeNavIdx-1+orderedSuggestions.length)%orderedSuggestions.length;
      const item=orderedSuggestions[activeNavIdx];
      inspectCardSuggestion(item.ann,item.job,'frame');
    },'study-button coach-nav-btn');
    prevBtn.setAttribute('aria-label','Previous suggestion');
    const navCount=el('span','','coach-nav-count');navCount.id='coach-nav-count';
    const nextBtn=button('Next →',()=>{
      if(!orderedSuggestions.length)return;
      activeNavIdx=(activeNavIdx+1)%orderedSuggestions.length;
      const item=orderedSuggestions[activeNavIdx];
      inspectCardSuggestion(item.ann,item.job,'frame');
    },'study-button coach-nav-btn');
    nextBtn.setAttribute('aria-label','Next suggestion');
    navLeft.append(prevBtn,navCount,nextBtn);
    const navRight=el('div',undefined,'coach-nav-row');
    const toggleBtn=button('Body guide',()=>{
      const enabled=window.ClipStudy?.toggleOverlay?.();
      toggleBtn.setAttribute('aria-pressed',String(enabled!==false));
    },'study-button coach-overlay-btn');
    toggleBtn.id='coach-overlay-toggle';
    toggleBtn.setAttribute('aria-pressed',String(window.ClipStudy?.getInspectionState?.()?.overlayEnabled!==false));
    const exitBtn=button('Exit inspect',()=>{
      window.ClipStudy?.exitInspection?.({restoreTime:false});
      message('Inspection exited; export settings preserved.');
    },'study-button coach-exit-btn');
    exitBtn.id='coach-exit-inspect';
    exitBtn.hidden=!window.ClipStudy?.getInspectionState?.();
    navRight.append(toggleBtn,exitBtn);
    navBar.append(navLeft,navRight);

    const jobNodes=[];
    for(const j of jobs){
      if(j.role==='render'){
        const origStart=(owner.clip.original_start_seconds||0)+(j.body.start||0);
        const origEnd=(owner.clip.original_start_seconds||0)+(j.body.end||0);
        const stateText=j.state==='failed'?'Failed':'Preparing clip…';
        const card=el('article',undefined,'coach-note');card.dataset.job=j.id;card.dataset.role='render';
        card.append(el('strong',`${C.clock(origStart)}–${C.clock(origEnd)} · ${stateText}`),el('p',formatAskedAt(j),'study-hint coach-asked-at'));
        const subjectText=formatJobSubject(j);
        if(subjectText)card.append(el('p',subjectText,'study-hint coach-job-subject'));
        if(j.body.question){const question=el('details');question.append(el('summary','Question asked'),el('p',j.body.question));card.append(question);}
        if(j.result?.error)card.append(el('p',j.result.error,'coach-warning'));
        if(['queued','leased'].includes(j.state)){
          const current=owner,gen=generation;
          card.append(button('Cancel request',async()=>{try{await api('jobs/'+j.id,{action:'cancel',version:j.version},'PUT');if(owner===current&&gen===generation)await refresh(true);}catch(e){if(owner===current&&gen===generation)message(e.message);}}));
        }
        jobNodes.push(card);
        continue;
      }
      const card=el('article',undefined,'coach-note');card.dataset.job=j.id;card.append(el('strong',`${C.clock(j.body.original_start)}–${C.clock(j.body.original_end)} · ${states[j.state]||j.state}${j.body.follow_up_of?' · Follow-up':''}`),el('p',formatAskedAt(j),'study-hint coach-asked-at'));
      const subjectText=formatJobSubject(j);
      if(subjectText)card.append(el('p',subjectText,'study-hint coach-job-subject'));
      const question=el('details');question.append(el('summary','Question asked'),el('p',j.body.prompt));card.append(question);
      const cardActions=el('div',undefined,'coach-card-actions');
      if(j.state==='completed'&&j.body.publication===owner.session.published_at){
        cardActions.append(button('Re-ask',()=>followUp(j),'study-button'));
      }
      if(!['leased','calling'].includes(j.state)){
        const current=owner,gen=generation;
        cardActions.append(button('Delete review',async()=>{
          try{
            await api('jobs/'+j.id,{},'DELETE');
            if(owner!==current||gen!==generation)return;
            if(selectedAsk===j.id)selectedAsk=null;
            if(selectedExport&&j.body?.export_id===selectedExport)selectedExport=null;
            if(followUpId===j.id){followUpId=null;followUpOrigin=null;$('coach-follow-up-context').hidden=true;}
            saveDraft();historyKey='';
            await refresh(true);
            window.dispatchEvent(new Event('coach-notes-changed'));
            message('Coaching review deleted.');
          }catch(e){if(owner===current&&gen===generation)message(e.message);}
        },'study-button coach-delete-btn'));
      }
      if(cardActions.children.length)card.append(cardActions);
      if(j.state==='completed'){
        if(highlightedKey==='j:'+j.id)card.classList.add('is-highlighted');
        if(j.result.structured){const result=j.result.structured;if(result.summary)card.append(el('p',result.summary,'coach-summary'));for(const warning of result.validation_warnings)card.append(el('p',warning,'coach-warning'));if(result.parse_status==='unstructured')card.append(el('p','Timing unavailable. See the original response and frames.','study-hint'));
          for(const a of data.annotations.filter(a=>a.job_id===j.id)){
            const sCard=suggestionCard(a,j);
            card.append(sCard);
            orderedSuggestions.push({ann:a,job:j,card:sCard});
          }
          const all=el('details');all.append(el('summary','All submitted snapshots'));const fake={current:{evidence_frame_ids:(j.body.evidence_frames||[]).map(f=>f.frame_id)}};all.append(evidenceStrip(j,fake,null));card.append(all);
          const blank={current:{title:'',topic:'',observation:'',cue:'',drill:'',uncertainty:'',now_summary:'',primary_frame_id:'',stroke_phase:'',body_areas:[],focus_region:null,evidence_frame_ids:[],applies_to:[]}};card.append(editor(blank,j,true));
        }else legacyCard(card,j);
        const raw=el('details');raw.append(el('summary','Original AI response'),el('p',j.result.answer,'coach-answer'));card.append(raw);
      }
      if(j.result?.error)card.append(el('p',j.result.error,'coach-warning'));
      if(['queued','leased'].includes(j.state))card.append(button('Cancel request',async()=>{try{await api('jobs/'+j.id,{action:'cancel',version:j.version},'PUT');await refresh(true);}catch(e){message(e.message);}}));
      jobNodes.push(card);
    }
    if(orderedSuggestions.length){
      const matchedIdx=highlightedKey?.startsWith('a:')?orderedSuggestions.findIndex(item=>'a:'+item.ann.id===highlightedKey):-1;
      activeNavIdx=matchedIdx>=0?matchedIdx:0;
      box.append(navBar);
    }
    for(const node of jobNodes)box.append(node);
    box.append(button('Export annotations and revision history',async()=>{
      const current=owner,merged={schema_version:1,annotations:[],responses:[]};let after='';
      try{do{const page=await api('annotations/export/'+current.session.id+'?after='+encodeURIComponent(after));merged.annotations.push(...page.annotations);merged.responses.push(...page.responses);after=page.next_after;}while(after);
        const url=URL.createObjectURL(new Blob([JSON.stringify(merged,null,2)],{type:'application/json'})),link=el('a');link.href=url;link.download='coaching-annotations.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }catch(e){if(owner===current)message(e.message);}
    }));
    syncInspectionChrome();
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
    $('coach-ask').disabled=busy||capturing||exportInProgress||aiInProgress||!workersEnabled||!hasQuestion;
    $('coach-ask').textContent='Ask coach';
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
        const ranges=C.selectSuggestionRanges(ann.current.applies_to,owner.clip.original_start_seconds,owner.clip.original_end_seconds,owner.clip.duration_seconds);
        const rIdx=C.pickSuggestionRangeIndex(ranges,Number.isFinite(target.startSeconds)?target.startSeconds:ann.current.evidence_points?.[0]?.original_seconds);
        if(rIdx>=0&&ranges[rIdx]){
          activeRangeByAnn.set(ann.id,rIdx);
          origStart=ranges[rIdx].originalStart;
          origEnd=ranges[rIdx].originalEnd;
        }
      }
      if(card){
        highlightedKey='a:'+target.annotationId;
        const found=orderedSuggestions.findIndex(item=>item.ann.id===target.annotationId);
        if(found>=0)activeNavIdx=found;
        syncInspectionChrome();
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
          if(owner===current&&gen===generation){
            message(e.message);
            if(e?.status===409)await syncPlayersFromServer(current,gen,{includeSession:true,forceResetIds:true});
          }
        }
      }
      const key=JSON.stringify([data.jobs.map(j=>[j.id,j.version,j.state]),data.annotations.map(a=>[a.id,a.version,a.status]),knownPlayers().map(p=>[p.id,p.version,p.name])]);
      if(force||key!==historyKey&&!document.activeElement?.closest('#coach-history')&&!$('coach-history').querySelector('.coach-editor[open]')){history(data);historyKey=key;}
      const focusOutcome=applyPendingFocus(data);
      askAvailability(data);
      if(focusOutcome.preserveMessage)return;
      if(!data.enabled)message('Coaching workers are not configured yet.');
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
      poseCache.clear();activeRangeByAnn.clear();orderedSuggestions=[];activeNavIdx=0;
      generation++;owner={session,clip,subjectCustomized:false,authoritativePlayers:false};selectedExport=null;selectedAsk=null;pendingAsk=null;requestId=null;busy=false;historyKey='';lastData=null;highlightedKey=null;
      $('clip-study').dataset.playerCollapsed='false';
      $('clip-study').dataset.coachInspecting='false';
      if($('study-mobile-collapse'))$('study-mobile-collapse').textContent='Hide player';
      followUpId=null;followUpOrigin=null;$('coach-follow-up-context').hidden=true;$('coach-status').textContent='';
      let d={};try{d=JSON.parse(sessionStorage.getItem(draftKey())||'{}');}catch(_){}
      const focusing=pendingFocus&&(!pendingFocus.clipId||pendingFocus.clipId===clip.id);
      const catalog=subjectCatalog(owner);
      const restoreCustomSubject=!focusing&&d.subjectCustomized===true&&typeof d.subjectValue==='string'&&catalog.options.some(o=>o.value===d.subjectValue);
      owner.subjectCustomized=restoreCustomSubject;
      syncSubjectOptions(owner,{preferredValue:restoreCustomSubject?d.subjectValue:catalog.defaultValue});
      if($('coach-subject-hint'))$('coach-subject-hint').value=!focusing&&typeof d.subjectHint==='string'?d.subjectHint:'';
      if(focusing&&Number.isFinite(pendingFocus.startSeconds)){
        applyClipInterval(pendingFocus.startSeconds,pendingFocus.endSeconds);
      }else if(Number.isFinite(d.start)&&Number.isFinite(d.end)&&d.start<d.end){
        window.ClipStudy?.setInterval?.(d.start,d.end);
      }
      const savedPreset=typeof d.preset==='string'&&(d.preset==='custom'||Object.prototype.hasOwnProperty.call(presetPrompts,d.preset))?d.preset:'full';
      const isBuiltInPreset=typeof d.question==='string'&&(d.question===oldDefault||Object.keys(presetPrompts).some(k=>d.question===presetQuestion(k,true)||d.question===presetQuestion(k,false)));
      const savedQuestion=typeof d.question==='string'&&d.question.trim()&&!isBuiltInPreset?d.question:presetQuestion(savedPreset==='custom'?'full':savedPreset);
      $('coach-question').value=savedQuestion;
      const matchingKey=Object.keys(presetPrompts).find(k=>presetQuestion(k)===savedQuestion);
      $('coach-preset').value=matchingKey||'custom';
      selectedExport=d.selectedExport||null;selectedAsk=d.selectedAsk||null;
      if(!focusing&&d.followUpId&&d.followUpOrigin){
        followUpId=d.followUpId;
        followUpOrigin=d.followUpOrigin;
        $('coach-follow-up-context').hidden=false;
        $('coach-follow-up-context').textContent='Re-asking with previous question and response context.';
      }
      showRange();setBusy(false);showMode(focusing?'coach':'watch',false);syncPlayersFromServer(owner,generation);refresh(true);clearInterval(timer);timer=setInterval(()=>refresh(),4000);
    },
    close(){window.ClipStudy?.cancelScan?.();stopIntervalSeek();saveDraft();poseCache.clear();activeRangeByAnn.clear();orderedSuggestions=[];activeNavIdx=0;pendingAsk=null;pendingFocus=null;highlightedKey=null;followUpOrigin=null;generation++;owner=null;clearInterval(timer);},
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
