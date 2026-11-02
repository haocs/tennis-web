/* Dedicated coaching UI. No account credentials or media upload in the browser. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id), C=window.ClipStudyCore;
  let owner=null, generation=0, timer=null, selectedExport=null, busy=false, selectionVersion=0, requestId=null, askId=null, askFingerprint=null;
  let stopWatch=()=>{};
  function el(tag,text,cls) { const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n; }
  async function api(path,body,method='POST') {
    const r=await fetch('/api/coaching/'+path,body===undefined?{}:{method,headers:{'Content-Type':'application/json','X-Tennis-Request':'1'},body:JSON.stringify(body)});
    const data=await r.json();if(!r.ok)throw new Error(data.error||'Request unavailable');return data;
  }
  function message(text){$('coach-status').textContent=text;}
  function times(){return {start:Number($('coach-in').value),end:Number($('coach-out').value)};}
  function selection(){const t=times();$('coach-range').textContent=`${C.clock(t.start)} – ${C.clock(t.end)} · ${C.clock(Math.max(0,t.end-t.start)/Number($('study-speed').value))} exported`;selectedExport=null;selectionVersion++;$('coach-preview').hidden=true;$('coach-preview').pause();$('coach-preview').removeAttribute('src');delete $('coach-preview').dataset.job;$('coach-export-links').replaceChildren();$('coach-ask').disabled=true;requestId=null;}
  function setBusy(value){busy=value;$('coach-prepare').disabled=value||!owner;$('coach-question').disabled=value;}
  function makePanel(){
    const section=el('section',undefined,'study-panel coaching-panel');section.id='coaching-panel';
    section.innerHTML='<p class="study-kicker">ONE MOMENT, ONE QUESTION</p><h3>Ask a coach</h3><p class="study-muted">Choose up to 30 seconds. Your current zoom, focus and speed are baked into the prepared video.</p><p id="coach-range"></p><label>Start<input id="coach-in" type="range" min="0" max="1" step="0.01" value="0" aria-label="Coaching excerpt start"></label><button class="study-button" id="coach-set-in" type="button">Set start here</button><label>End<input id="coach-out" type="range" min="0" max="1" step="0.01" value="1" aria-label="Coaching excerpt end"></label><button class="study-button" id="coach-set-out" type="button">Set end here</button><button class="study-button" id="coach-play-selection" type="button">Play selection</button><label>Export size<select id="coach-quality"><option value="balanced">Balanced · up to 960px</option><option value="small">Smaller · up to 640px</option></select></label><button class="study-button" id="coach-prepare" type="button">Prepare coaching clip</button><video id="coach-preview" controls playsinline preload="metadata" hidden aria-label="Prepared coaching excerpt"></video><div id="coach-export-links"></div><label>What would you like help with?<textarea id="coach-question" rows="3" maxlength="4000">Please suggest one observable improvement and a simple drill for this beginner tennis sequence.</textarea></label><p class="study-hint">ChatGPT via Codex reviews up to 32 timestamped frames, not native video. Audio is removed. HDR exports are converted to SDR. Ask coach sends only this frame sequence and question to OpenAI using the Mac’s account.</p><button class="study-button study-primary" id="coach-ask" type="button" disabled>Ask coach · send frames to OpenAI</button><p id="coach-status" role="status" aria-live="polite"></p><h3>Coach notes</h3><div id="coach-history"></div>';
    document.querySelector('.study-sidebar').prepend(section);
    for(const id of ['coach-in','coach-out','coach-quality'])$(id).addEventListener('input',selection);
    $('coach-set-in').onclick=()=>{$('coach-in').value=$('study-video').currentTime;selection();};
    $('coach-set-out').onclick=()=>{$('coach-out').value=$('study-video').currentTime;selection();};
    $('coach-play-selection').onclick=()=>{const t=times(),v=$('study-video');if(t.start>=t.end){message('End must be after start.');return;}v.currentTime=t.start;v.play().catch(()=>message('Tap Play to preview.'));};
    $('study-video').addEventListener('timeupdate',()=>{if(!owner)return;const t=times(),v=$('study-video');if(v.currentTime>=t.end&&!v.paused)v.pause();});
    $('study-speed').addEventListener('change',selection);
    $('coach-question').addEventListener('input',()=>{requestId=null;});
    $('coach-prepare').onclick=prepare;$('coach-ask').onclick=ask;
  }
  function recipe(){
    const a=window.ClipStudy.coachingSelection();if(!a)throw new Error('Wait for the clip to load');
    const {start,end}=times();if(start>=end||end-start>30)throw new Error('Select between 0.1 and 30 seconds');
    return {source_id:owner.session.id,clip_id:owner.clip.id,publication:owner.session.published_at,start,end,speed:a.speed,crop:a.crop,quality:$('coach-quality').value,question:$('coach-question').value};
  }
  async function prepare(){
    if(busy||!owner)return;
    const current=owner,rev=selectionVersion;
    try{const body=recipe();setBusy(true);message('Preparing on the media worker…');requestId=requestId||cryptoId();const j=await api('exports',{...body,request_id:requestId});if(owner!==current||rev!==selectionVersion)return;selectedExport=j.id;await refresh();}
    catch(e){if(owner===current)message(e.message);}finally{if(owner===current){setBusy(false);if(rev!==selectionVersion)message('Selection changed. Prepare it again.');}}
  }
  function cryptoId(){return 'coach-'+Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');}
  async function ask(){
    if(busy||!selectedExport)return;const current=owner;
    try{setBusy(true);$('coach-ask').disabled=true;message('Queued for the AI worker…');const fingerprint=JSON.stringify([selectedExport,$('coach-question').value.trim()]);if(fingerprint!==askFingerprint){askFingerprint=fingerprint;askId=cryptoId();}await api('requests',{export_id:selectedExport,prompt:$('coach-question').value,consent:true,request_id:askId});await refresh();}
    catch(e){if(owner===current)message(e.message);}finally{if(owner===current){setBusy(false);$('coach-ask').disabled=!selectedExport;}}
  }
  const stateText={queued:'Queued',leased:'Working',calling:'ChatGPT is reviewing frames',needs_review:'Interrupted after sending. Check the worker result before asking again.',failed:'Failed',completed:'Ready',cancelled:'Cancelled'};
  function exportLinks(job){
    if($('coach-preview').dataset.job===job.id)return;$('coach-preview').dataset.job=job.id;
    const base='/api/coaching/exports/'+job.id+'/';$('coach-preview').src=base+'excerpt.mp4';$('coach-preview').hidden=false;
    const box=$('coach-export-links');box.replaceChildren();
    for(const [name,file] of [['Download coaching video','excerpt.mp4'],['Timing metadata','recipe.json'],['Prepared prompt','prompt.txt']]){const a=el('a',name,'study-button');a.href=base+file+'?download=1';a.setAttribute('download','');box.append(a);}
    const copy=el('button','Copy question + timing','study-button');copy.onclick=async()=>{const text=$('coach-question').value+'\n\nOriginal interval '+C.clock(job.body.original_start)+'–'+C.clock(job.body.original_end)+'; exported at '+job.body.speed+'×, fixed focus area '+JSON.stringify(job.body.crop)+'. The video is muted SDR.';const ok=await C.copyText(text,{secure:window.isSecureContext,writeText:t=>navigator.clipboard.writeText(t),legacyCopy:t=>{const input=el('textarea');input.value=t;document.querySelector('#clip-study').append(input);input.select();const done=document.execCommand('copy');input.remove();return done;}});message(ok?'Question and timing copied. Attach the prepared video in ChatGPT.':'Copy is unavailable. Select your question and use the timing metadata.');};box.append(copy);
    const chat=el('a','Open ChatGPT ↗','study-button');chat.href='https://chatgpt.com/';chat.target='_blank';chat.rel='noopener noreferrer';box.append(chat);
    const hint=el('p','To use regular ChatGPT chat: download the video and prompt, then attach/paste them. A link cannot automatically attach the local video.','study-hint');box.append(hint);
    const r=job.result;message(`Prepared ${(r.bytes/1024/1024).toFixed(1)} MB · ${C.clock(r.output_seconds)} · muted SDR video. Check the preview before sending.`);$('coach-ask').disabled=busy;
  }
  function history(data){
    const box=$('coach-history');box.replaceChildren();
    const exports=data.jobs.filter(j=>j.role==='render'&&j.body.clip_id===owner.clip.id);
    if(exports.length){const past=el('details');past.append(el('summary','Prepared excerpts'));for(const j of exports){const line=el('p',`${C.clock(j.body.start)}–${C.clock(j.body.end)} · ${j.body.speed}× · ${stateText[j.state]||j.state}`);if(j.state==='completed'){const view=el('button','View this export','study-button');view.onclick=()=>{selectedExport=j.id;exportLinks(j);};line.append(view);}past.append(line);}box.append(past);}
    const jobs=data.jobs.filter(j=>j.role==='ai');
    if(!jobs.length)box.append(el('p','Saved advice will appear here.','study-muted'));
    for(const j of jobs){
      const card=el('article',undefined,'coach-note');
      const historical=j.body.publication!==owner.session.published_at;
      card.append(el('strong',`${C.clock(j.body.original_start)}–${C.clock(j.body.original_end)} · ${stateText[j.state]||j.state}${historical?' · Previous publication':''}`));
      card.append(el('p',j.body.prompt));
      if(j.state==='completed'){
        const answer=el('details');answer.append(el('summary','ChatGPT response · frame sequence'),el('p',j.result.answer,'coach-answer'));card.append(answer);
        const label=el('label','Your playback cue (review before saving)');const input=el('textarea');input.maxLength=400;input.rows=2;input.value=j.result.cue||'';label.append(input);card.append(label);
        const save=el('button','Save reviewed cue','study-button');save.onclick=async()=>{try{await api('jobs/'+j.id,{action:'cue',cue:input.value,version:j.version},'PUT');await refresh(true);window.dispatchEvent(new Event('coach-notes-changed'));message('Cue saved. It will appear at this moment during playback.');}catch(e){message(e.message);await refresh();}};card.append(save);
        if(!historical&&j.body.clip_id===owner.clip.id){const go=el('button','Go to moment','study-button');go.onclick=()=>{$('study-video').currentTime=j.body.start;};card.append(go);}
      }
      if(j.result?.error)card.append(el('p',j.result.error));
      if(['queued','leased'].includes(j.state)){const cancel=el('button','Cancel request','study-button');cancel.onclick=async()=>{try{await api('jobs/'+j.id,{action:'cancel',version:j.version},'PUT');await refresh();}catch(e){message(e.message);}};card.append(cancel);}
      box.append(card);
    }
  }
  async function refresh(force=false){
    if(!owner)return;const current=owner,gen=generation;
    try{const data=await api('sessions/'+current.session.id);if(owner!==current||gen!==generation)return;if(force||!document.activeElement?.closest('#coach-history'))history(data);
      const j=data.jobs.find(j=>j.id===selectedExport);
      if(j?.state==='completed')exportLinks(j);
      else if(j)message(`${stateText[j.state]||j.state}${j.result?.error?': '+j.result.error:''}`);
      const online=data.workers.filter(w=>w.online).map(w=>w.role);
      $('coach-prepare').disabled=busy||!data.enabled;
      if(!data.enabled)message('Coaching workers are not configured yet.');
      else if(!online.includes('render')&&j?.state==='queued')message('Excerpt queued. Waiting for the media worker on your Mac.');
      if(data.jobs.some(j=>j.role==='ai'&&j.state==='queued')&&!online.includes('ai'))message('Coaching queued. Waiting for the AI worker on your Mac.');
    }catch(e){if(owner===current)message(e.message);}
  }
  window.Coach={
    open(session,clip){if(!$('coaching-panel'))makePanel();generation++;owner={session,clip};selectedExport=null;requestId=null;busy=false;for(const id of ['coach-in','coach-out'])$(id).max=clip.duration_seconds;$('coach-in').value=0;$('coach-out').value=Math.min(30,clip.duration_seconds);selection();setBusy(false);refresh();clearInterval(timer);timer=setInterval(refresh,4000);},
    close(){generation++;owner=null;clearInterval(timer);if($('coach-preview')){$('coach-preview').pause();$('coach-preview').removeAttribute('src');$('coach-preview').load();}},
    watch({session,clip,player}){
      stopWatch();
      let active=true,notes=[],badge=el('button','Coach cue','coach-playback-cue');badge.hidden=true;player.parentElement.after(badge);
      const loadNotes=()=>api('sessions/'+session.id).then(data=>{if(active){notes=data.jobs.filter(j=>j.role==='ai'&&j.state==='completed'&&j.result?.cue_reviewed&&j.body.publication===session.published_at);sync();}}).catch(()=>{});
      let matching=[];
      const sync=()=>{if(!badge.isConnected){active=false;player.removeEventListener('timeupdate',sync);return;}const original=clip?clip.original_start_seconds+player.currentTime:null;matching=notes.filter(j=>clip?j.body.clip_id===clip.id&&original>=j.body.original_start&&original<=j.body.original_end:session.clips.some(c=>j.body.clip_id===c.id&&player.currentTime>=c.playback_start_seconds+j.body.start&&player.currentTime<=c.playback_start_seconds+j.body.end));badge.hidden=!matching.length;badge.textContent=matching.map(j=>j.result.cue).join(' · ');};
      badge.onclick=()=>{player.pause();badge.classList.toggle('coach-expanded');};
      stopWatch=()=>{active=false;window.removeEventListener('coach-notes-changed',loadNotes);player.removeEventListener('timeupdate',sync);badge.remove();};
      window.addEventListener('coach-notes-changed',loadNotes);player.addEventListener('timeupdate',sync);loadNotes();sync();
    }
  };
})();
