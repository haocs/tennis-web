'use strict';
const ReviewPlayer = {
 icon(name){
  const shapes={
   play:'<path d="m9 5 11 7-11 7z" fill="currentColor" stroke="none"/>',
   pause:'<path d="M7 5h4v14H7zm6 0h4v14h-4z" fill="currentColor" stroke="none"/>',
   previous:'<path d="M6 5v14m12-14L8 12l10 7z"/>',
   next:'<path d="M18 5v14M6 5l10 7-10 7z"/>',
   fullscreen:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
   expand:'<path d="M14 4h6v6M20 4l-7 7M10 20H4v-6m0 6 7-7"/>',
   collapse:'<path d="m6 9 6 6 6-6"/>',
   close:'<path d="m6 6 12 12M18 6 6 18"/>',
   exit:'<path d="M3 8h5V3m13 5h-5V3M8 21v-5H3m13 5v-5h5"/>',
   study:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/>',
   download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
   more:'<circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none"/>'
  };
  return `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${shapes[name]||''}</svg>`;
 },
 markup(){return `<div class="video-stage" id="video-stage" role="region" aria-label="Video player" tabindex="0">
 <video id="player" playsinline preload="metadata"></video>
 <div class="video-overlay" aria-label="Playback controls">
 <div class="video-topbar"><button type="button" class="video-button video-close-expanded hidden" id="video-close-expanded" aria-label="Close expanded player" title="Close expanded player">${this.icon('close')}</button><span id="video-context" class="video-context"></span><label class="video-autoplay" title="Autoplay next clip"><span class="autoplay-text">Autoplay</span><input type="checkbox" id="autoplay-next" aria-label="Autoplay"><span class="autoplay-switch" aria-hidden="true"></span></label></div>
 <div class="video-transport">
 <button type="button" class="video-button" id="previous" aria-label="Previous clip" title="Previous clip">${this.icon('previous')}</button>
 <button type="button" class="video-button video-play" id="video-play" aria-label="Play video" title="Play video">${this.icon('play')}</button>
 <button type="button" class="video-button" id="next" aria-label="Next clip" title="Next clip">${this.icon('next')}</button>
 </div>
 <div class="video-bottom"><span id="video-time" class="video-time">0:00 / 0:00</span><div class="video-view-actions"><button type="button" class="video-button" id="video-expand" aria-label="Expand player" title="Expand player" aria-pressed="false">${this.icon('expand')}</button><button type="button" class="video-button" id="video-fullscreen" aria-label="Enter fullscreen" title="Enter fullscreen">${this.icon('fullscreen')}</button></div></div>
 <label class="video-seek-label"><span class="sr-only">Seek current video</span><input id="video-seek" type="range" min="0" max="0" value="0" step="0.1" disabled></label>
 </div></div>`;},
 mount({time,onError}){
  this.controller?.destroy();
  const stage=document.querySelector('#video-stage'),video=document.querySelector('#player');
  const find=id=>stage.querySelector('#'+id),seek=find('video-seek'),full=find('video-fullscreen'),overlay=stage.querySelector('.video-overlay');
  let hideTimer,keyboardMode=false,mediaContext=null,suspended=false;
  let expanded=false,expandedScroll=0,bodyStyle=null,inertElements=[];
  const expand=find('video-expand'),closeExpanded=find('video-close-expanded');
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
  function collapsePlayer(restoreFocus=true){
   if(!expanded)return;
   expanded=false;stage.classList.remove('player-expanded');
   stage.setAttribute('role','region');stage.setAttribute('aria-label','Video player');stage.removeAttribute('aria-modal');
   closeExpanded.classList.add('hidden');expand.innerHTML=ReviewPlayer.icon('expand');
   expand.setAttribute('aria-label','Expand player');expand.title='Expand player';expand.setAttribute('aria-pressed','false');
   inertElements.forEach(([element,value])=>{element.inert=value;});inertElements=[];
   Object.entries(bodyStyle).forEach(([name,value])=>{document.body.style[name]=value;});bodyStyle=null;
   window.scrollTo(0,expandedScroll);if(restoreFocus)expand.focus({preventScroll:true});reveal();
  }
  function expandPlayer(){
   if(expanded){collapsePlayer();return;}
   if(nativeFullscreen()||elementFullscreen())return;
   expandedScroll=window.scrollY;bodyStyle={};
   ['position','top','left','width','overflow'].forEach(name=>{bodyStyle[name]=document.body.style[name];});
   Object.assign(document.body.style,{position:'fixed',top:`-${expandedScroll}px`,left:'0',width:'100%',overflow:'hidden'});
   // Keep the same video element in place: moving it into a modal can interrupt
   // playback on iOS. Make the surrounding page inert while the player expands.
   for(let node=stage;node.parentElement&&node!==document.body;node=node.parentElement){
    [...node.parentElement.children].filter(sibling=>sibling!==node).forEach(element=>{
     inertElements.push([element,element.inert]);element.inert=true;
    });
   }
   expanded=true;stage.classList.add('player-expanded');stage.setAttribute('role','dialog');
   stage.setAttribute('aria-label','Expanded video player');stage.setAttribute('aria-modal','true');
   closeExpanded.classList.remove('hidden');expand.innerHTML=ReviewPlayer.icon('collapse');
   expand.setAttribute('aria-label','Collapse player');expand.title='Collapse player';expand.setAttribute('aria-pressed','true');
   closeExpanded.focus({preventScroll:true});reveal();
  }
  expand.addEventListener('click',expandPlayer);closeExpanded.addEventListener('click',()=>collapsePlayer());
  function hide(){
   if(video.paused||(keyboardMode&&stage.contains(document.activeElement)))return;
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
   full.innerHTML=ReviewPlayer.icon(active?'exit':'fullscreen');full.setAttribute('aria-label',label);full.title=label;
   full.setAttribute('aria-pressed',String(!!active));mediaActionsSync();reveal();
  }
  async function toggleFullscreen(){
   collapsePlayer(false);
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
  seek.addEventListener('input',()=>{video.currentTime=Number(seek.value);sync();reveal();});
  full.addEventListener('click',()=>toggleFullscreen().catch(()=>onError('Fullscreen is unavailable. Wait for the video to load, then try again.')));
  stage.addEventListener('keydown',event=>{
   keyboardMode=true;reveal();
   if(expanded&&event.key==='Escape'){event.preventDefault();event.stopPropagation();collapsePlayer();return;}
   if(expanded&&event.key==='Tab'){
    const controls=[...stage.querySelectorAll('button,input')].filter(el=>!el.disabled&&el.getClientRects().length);
    const first=controls[0],last=controls.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
   }
   if(event.target.matches('button,input'))return;
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
   collapsePlayer(false);clearTimeout(hideTimer);suspended=true;clearMedia();studyObserver?.disconnect();
   window.removeEventListener('pagehide',suspendMedia);window.removeEventListener('pageshow',resumeMedia);
   ['fullscreenchange','webkitfullscreenchange'].forEach(event=>document.removeEventListener(event,syncFullscreen));
  }};
  return this.controller;
 }
};
