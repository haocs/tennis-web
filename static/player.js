'use strict';
const ReviewPlayer = {
 icon(name){
  const shapes={
   play:'<path d="m9 5 11 7-11 7z" fill="currentColor" stroke="none"/>',
   pause:'<path d="M7 5h4v14H7zm6 0h4v14h-4z" fill="currentColor" stroke="none"/>',
   previous:'<path d="M6 5v14m12-14L8 12l10 7z"/>',
   next:'<path d="M18 5v14M6 5l10 7-10 7z"/>',
   fullscreen:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
   exit:'<path d="M3 8h5V3m13 5h-5V3M8 21v-5H3m13 5v-5h5"/>',
   person:'<circle cx="12" cy="8" r="3"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
   study:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/>',
   download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
   more:'<circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none"/>'
  };
  return `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${shapes[name]||''}</svg>`;
 },
 markup(){return `<div class="video-stage" id="video-stage" role="region" aria-label="Video player" tabindex="0">
 <video id="player" playsinline preload="metadata"></video>
 <div class="video-overlay" aria-label="Playback controls">
 <div class="video-topbar"><span id="video-context" class="video-context"></span><label class="video-autoplay" title="Autoplay next clip"><span class="autoplay-text">Autoplay</span><input type="checkbox" id="autoplay-next" aria-label="Autoplay"><span class="autoplay-switch" aria-hidden="true"></span></label></div>
 <div class="video-transport">
 <button type="button" class="video-button" id="previous" aria-label="Previous clip" title="Previous clip">${this.icon('previous')}</button>
 <button type="button" class="video-button video-play" id="video-play" aria-label="Play video" title="Play video">${this.icon('play')}</button>
 <button type="button" class="video-button" id="next" aria-label="Next clip" title="Next clip">${this.icon('next')}</button>
 </div>
 <div class="video-bottom"><span id="video-time" class="video-time">0:00 / 0:00</span><div class="video-view-actions"><select id="video-rate" class="video-rate" aria-label="Playback speed" title="Playback speed"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="0.75">0.75×</option><option value="1" selected>1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option></select><button type="button" class="video-button" id="video-fullscreen" aria-label="Enter fullscreen" title="Enter fullscreen">${this.icon('fullscreen')}</button></div></div>
 <div class="video-seek-label"><span class="sr-only">Seek current video</span><input id="video-seek" aria-label="Seek current video" type="range" min="0" max="0" value="0" step="0.1" disabled></div>
 </div></div><p id="native-fullscreen-hint" class="native-fullscreen-hint" hidden>Exit fullscreen to change clips.</p>`;},
 mount({time,onError}){
  this.controller?.destroy();
  const stage=document.querySelector('#video-stage'),video=document.querySelector('#player');
  const find=id=>stage.querySelector('#'+id),seek=find('video-seek'),full=find('video-fullscreen'),overlay=stage.querySelector('.video-overlay'),rate=find('video-rate');
  let hideTimer,keyboardMode=false,mediaContext=null,suspended=false;
  const media=navigator.mediaSession,study=document.querySelector('#clip-study');
  const mediaActions=['play','pause','seekto','previoustrack','nexttrack'];
  const ownsMedia=()=>!suspended&&stage.isConnected&&!study?.open;
  function action(name,handler){
   // Safari versions can expose Media Session but reject individual actions.
   try{media?.setActionHandler(name,handler);}catch(error){/* Keep page controls working. */}
  }
  function clearMedia(){
   mediaActions.forEach(name=>action(name,null));
   try{if(media){media.metadata=null;media.playbackState='none';media.setPositionState?.();}}catch(error){}
  }
  function mediaPosition(){
   if(!media||!ownsMedia())return;
   try{
    media.playbackState=video.paused?'paused':'playing';
    const duration=video.duration;
    if(Number.isFinite(duration)&&duration>0&&video.playbackRate>0){
     media.setPositionState?.({duration,playbackRate:video.playbackRate,position:Math.max(0,Math.min(duration,video.currentTime||0))});
    }else media.setPositionState?.();
   }catch(error){/* Position reporting is optional. */}
  }
  function mediaActionsSync(context=mediaContext){
   mediaContext=context;
   if(!media||!context)return;
   if(!ownsMedia()){clearMedia();return;}
   const navigate=id=>{if(ownsMedia()&&!find(id).disabled)find(id).click();};
   action('previoustrack',find('previous').disabled?null:()=>navigate('previous'));
   action('nexttrack',find('next').disabled?null:()=>navigate('next'));
   action('play',()=>{if(ownsMedia())video.play().catch(()=>onError('Playback could not start. Try Play again.'));});
   action('pause',()=>{if(ownsMedia())video.pause();});
   action('seekto',details=>{
    if(ownsMedia()&&Number.isFinite(details.seekTime)&&Number.isFinite(video.duration)){
     video.currentTime=Math.max(0,Math.min(video.duration,details.seekTime));
     sync();mediaPosition();
    }
   });
   try{
    if(window.MediaMetadata)media.metadata=new MediaMetadata({title:context.title,artist:'Courtside',album:context.album});
   }catch(error){/* Metadata must never prevent navigation. */}
   mediaPosition();
  }
  const suspendMedia=()=>{suspended=true;clearMedia();};
  const resumeMedia=()=>{suspended=false;mediaActionsSync();};
  window.addEventListener('pagehide',suspendMedia);
  window.addEventListener('pageshow',resumeMedia);
  const studyObserver=study?new MutationObserver(()=>mediaActionsSync()):null;
  studyObserver?.observe(study,{attributes:true,attributeFilter:['open']});
  const elementFullscreen=()=>document.fullscreenElement===stage||document.webkitFullscreenElement===stage||document.fullscreenElement===video;
  const nativeFullscreen=()=>video.webkitDisplayingFullscreen||video.webkitPresentationMode==='fullscreen';
  function hide(){
   if(video.paused||document.activeElement===rate||(keyboardMode&&stage.contains(document.activeElement)))return;
   stage.classList.add('controls-hidden');overlay.inert=true;
  }
  function reveal(){
   clearTimeout(hideTimer);stage.classList.remove('controls-hidden');overlay.inert=false;
   if(!video.paused)hideTimer=setTimeout(hide,2500);
  }
  function sync(){
   const duration=Number.isFinite(video.duration)?video.duration:0;
   seek.max=duration;seek.value=video.currentTime||0;seek.disabled=!duration;
   seek.style.setProperty('--played',duration?`${100*video.currentTime/duration}%`:'0%');
   find('video-time').textContent=`${time(video.currentTime)} / ${time(duration)}`;
   const play=find('video-play'),label=video.paused?'Play video':'Pause video';
   play.innerHTML=ReviewPlayer.icon(video.paused?'play':'pause');play.setAttribute('aria-label',label);play.title=label;
  }
  function syncFullscreen(){
   const active=elementFullscreen()||nativeFullscreen(),label=active?'Exit fullscreen':'Enter fullscreen';
   if(nativeFullscreen())document.querySelector('#native-fullscreen-hint').hidden=false;
   full.innerHTML=ReviewPlayer.icon(active?'exit':'fullscreen');full.setAttribute('aria-label',label);full.title=label;
   full.setAttribute('aria-pressed',String(!!active));mediaActionsSync();reveal();
  }
  async function toggleFullscreen(){
   if(nativeFullscreen()){
    if(video.webkitExitFullscreen)video.webkitExitFullscreen();
    else if(video.webkitSetPresentationMode)video.webkitSetPresentationMode('inline');
   }else if(elementFullscreen()){
    const exit=document.exitFullscreen||document.webkitExitFullscreen;if(exit)await exit.call(document);
   }else{
    const request=stage.requestFullscreen||stage.webkitRequestFullscreen;
    const enterNative=video.webkitEnterFullscreen||video.webkitEnterFullScreen;
    // iPhone's real fullscreen uses the system video player. Never fake it with
    // a fixed-position page element, and call the native API in the user gesture.
    if(enterNative&&!document.fullscreenEnabled&&!document.webkitFullscreenEnabled){enterNative.call(video);}
    else if(request){
     try{await request.call(stage);}catch(error){if(enterNative)enterNative.call(video);else throw error;}
    }else if(enterNative){enterNative.call(video);}
    else if(video.requestFullscreen){await video.requestFullscreen();}
    else{throw new Error('Fullscreen unavailable');}
   }
   syncFullscreen();
  }
  function togglePlay(){
   if(video.paused)video.play().catch(()=>onError('Playback could not start. Try Play again.'));else video.pause();
   reveal();
  }
  find('video-play').addEventListener('click',togglePlay);
  video.addEventListener('click',()=>{
   if(matchMedia('(pointer: coarse)').matches){if(stage.classList.contains('controls-hidden')||video.paused)reveal();else hide();}
   else togglePlay();
  });
  stage.addEventListener('pointerdown',()=>{keyboardMode=false;});
  stage.addEventListener('pointermove',event=>{if(event.pointerType!=='touch')reveal();});
  stage.addEventListener('pointerleave',event=>{if(event.pointerType!=='touch')hide();});
  stage.addEventListener('focusin',()=>{if(keyboardMode||document.activeElement===stage)reveal();});
  rate.addEventListener('change',()=>{video.defaultPlaybackRate=Number(rate.value);video.playbackRate=Number(rate.value);reveal();});
  rate.addEventListener('focus',reveal);
  rate.addEventListener('blur',reveal);
  video.addEventListener('ratechange',()=>{rate.value=String(video.playbackRate);});
  seek.addEventListener('input',()=>{video.currentTime=Number(seek.value);sync();reveal();});
  full.addEventListener('click',()=>toggleFullscreen().catch(()=>onError('Fullscreen is unavailable. Wait for the video to load, then try again.')));
  stage.addEventListener('keydown',event=>{
   keyboardMode=true;reveal();
   if(event.target.matches('button,input,select'))return;
   if(event.key===' '||event.key==='k'){event.preventDefault();togglePlay();}
   if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
    event.preventDefault();if(Number.isFinite(video.duration))video.currentTime=Math.max(0,Math.min(video.duration,video.currentTime+(event.key==='ArrowLeft'?-5:5)));
   }
  });
  ['play','pause','ended','loadedmetadata','emptied','ratechange'].forEach(event=>video.addEventListener(event,()=>{sync();mediaActionsSync();reveal();}));
  ['timeupdate','durationchange'].forEach(event=>video.addEventListener(event,()=>{sync();mediaPosition();}));
  ['fullscreenchange','webkitfullscreenchange'].forEach(event=>document.addEventListener(event,syncFullscreen));
  ['webkitbeginfullscreen','webkitendfullscreen'].forEach(event=>video.addEventListener(event,syncFullscreen));
  sync();syncFullscreen();
  this.controller={sync,reveal,updateMediaSession:mediaActionsSync,destroy(){
   clearTimeout(hideTimer);suspended=true;clearMedia();studyObserver?.disconnect();
   window.removeEventListener('pagehide',suspendMedia);window.removeEventListener('pageshow',resumeMedia);
   ['fullscreenchange','webkitfullscreenchange'].forEach(event=>document.removeEventListener(event,syncFullscreen));
  }};
  return this.controller;
 }
};
