'use strict';
// Keep navigation inside the fullscreen element, including on iPhone where
// fullscreening the video itself replaces HTML controls with the system player.
const ReviewPlayer = {
 markup(){return `<div class="video-stage" id="video-stage" role="region" aria-label="Video player" tabindex="-1">
 <div class="video-context" id="video-context" aria-live="polite"></div><video id="player" playsinline preload="metadata"></video>
 <div class="video-controls" aria-label="Playback controls">
 <label class="video-seek-label"><span class="sr-only">Seek current video</span><input id="video-seek" type="range" min="0" max="0" value="0" step="0.1" disabled></label>
 <div class="video-control-row">
 <button type="button" class="video-button" id="video-play" aria-label="Play video" title="Play video">▶</button>
 <button type="button" class="video-button" id="previous" aria-label="Previous clip" title="Previous clip">❮</button>
 <button type="button" class="video-button" id="next" aria-label="Next clip" title="Next clip">❯</button>
 <span id="video-time" class="video-time" aria-live="off">0:00 / 0:00</span>
 <span class="video-control-spacer"></span>
 <label class="video-autoplay" title="Play the next clip automatically"><input type="checkbox" id="autoplay-next">Autoplay</label>
 <button type="button" class="video-button" id="video-mute" aria-label="Mute video" title="Mute video">♪</button>
 <label class="video-rate"><span class="sr-only">Playback speed</span><select id="video-rate" aria-label="Playback speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label>
 <button type="button" class="video-button" id="video-fullscreen" aria-label="Enter fullscreen" title="Enter fullscreen">⛶</button>
 </div></div></div>`;},
 mount({time, onError}){
  const stage=document.querySelector('#video-stage'), video=document.querySelector('#player');
  const find=id=>stage.querySelector('#'+id), seek=find('video-seek'), full=find('video-fullscreen');
  const actualFullscreen=()=>document.fullscreenElement===stage||document.webkitFullscreenElement===stage;
  const expanded=()=>actualFullscreen()||stage.classList.contains('viewport-fullscreen');
  function sync(){
   const duration=Number.isFinite(video.duration)?video.duration:0;
   seek.max=duration;seek.value=video.currentTime||0;seek.disabled=!duration;
   find('video-time').textContent=`${time(video.currentTime)} / ${time(duration)}`;
   const play=find('video-play'), label=video.paused?'Play video':'Pause video';
   play.textContent=video.paused?'▶':'Ⅱ';play.setAttribute('aria-label',label);play.title=label;
   const mute=find('video-mute'), muted=video.muted||video.volume===0;
   mute.textContent=muted?'⌁':'♪';mute.setAttribute('aria-label',muted?'Unmute video':'Mute video');mute.title=muted?'Unmute video':'Mute video';
  }
  function syncFullscreen(){
   const active=expanded(),label=active?'Exit fullscreen':'Enter fullscreen';
   full.setAttribute('aria-label',label);full.title=label;full.setAttribute('aria-pressed',String(active));
  }
  function viewportFullscreen(active){
   stage.classList.toggle('viewport-fullscreen',active);
   document.body.classList.toggle('player-expanded',active);
   // Keep keyboard/screen reader focus inside the expanded player.
   if(active){stage.setAttribute('role','dialog');stage.setAttribute('aria-modal','true');stage.focus();}
   else{stage.setAttribute('role','region');stage.removeAttribute('aria-modal');full.focus();}
   syncFullscreen();
  }
  async function toggleFullscreen(){
   if(actualFullscreen()){
    const exit=document.exitFullscreen||document.webkitExitFullscreen;
    if(exit)await exit.call(document);
   }else if(expanded()){viewportFullscreen(false);}
   else{
    const request=stage.requestFullscreen||stage.webkitRequestFullscreen;
    try{if(!request)throw new Error('Element fullscreen unavailable');await request.call(stage);}
    catch{viewportFullscreen(true);}
   }
   syncFullscreen();
  }
  function togglePlay(){if(video.paused)video.play().catch(()=>onError('Playback could not start. Try Play again.'));else video.pause();}
  find('video-play').addEventListener('click',togglePlay);
  video.addEventListener('click',togglePlay);
  find('video-mute').addEventListener('click',()=>{video.muted=!video.muted;sync();});
  find('video-rate').addEventListener('change',event=>video.playbackRate=Number(event.target.value));
  seek.addEventListener('input',()=>{video.currentTime=Number(seek.value);sync();});
  full.addEventListener('click',()=>toggleFullscreen().catch(()=>onError('Unable to change fullscreen mode.')));
  stage.addEventListener('keydown',event=>{
   if(event.key==='Escape'&&stage.classList.contains('viewport-fullscreen')){event.preventDefault();viewportFullscreen(false);return;}
   if(event.key==='Tab'&&stage.classList.contains('viewport-fullscreen')){
    const controls=[...stage.querySelectorAll('button:not(:disabled),input:not(:disabled),select')];
    const first=controls[0],last=controls.at(-1);
    if(event.shiftKey&&(document.activeElement===first||document.activeElement===stage)){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
   }
   if(event.target.matches('button,input,select'))return;
   if(event.key===' '||event.key==='k'){event.preventDefault();togglePlay();}
   if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
    event.preventDefault();if(Number.isFinite(video.duration))video.currentTime=Math.max(0,Math.min(video.duration,video.currentTime+(event.key==='ArrowLeft'?-5:5)));
   }
  });
  ['play','pause','ended','timeupdate','loadedmetadata','durationchange','emptied','volumechange'].forEach(event=>video.addEventListener(event,sync));
  document.addEventListener('fullscreenchange',syncFullscreen);
  document.addEventListener('webkitfullscreenchange',syncFullscreen);
  sync();syncFullscreen();
  return {sync};
 }
};
