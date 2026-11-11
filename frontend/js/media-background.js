// Muted video lives outside the audio engine. Hidden/reduced-motion views stop decoding.
(() => {
 const layer=document.getElementById('wallpaper-layer'),video=document.createElement('video');video.muted=true;video.loop=true;video.playsInline=true;video.preload='auto';video.autoplay=true;video.className='wallpaper-video';video.setAttribute('aria-hidden','true');layer.prepend(video);let nativeHidden=false;
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 function sync(){if(/\.gif$/.test(wallpaperFile||'')){const background=document.hidden||nativeHidden||reduced.matches?'none':`url("/api/assets/wallpapers/${wallpaperFile}")`;if(layer.style.backgroundImage!==background)layer.style.backgroundImage=background;}if(!video.getAttribute('src'))return;if(document.hidden||nativeHidden||reduced.matches)video.pause();else void video.play().catch(()=>{document.getElementById('wallpaper-message').textContent='Браузер не воспроизводит этот видеофон. Попробуйте MP4 H.264.';});}
 function apply(url){
  // Saving dim/accent/theme can re-emit the same wallpaper. Never restart its timeline.
  const next=url||'';
  if((video.getAttribute('src')||'')===next){sync();return;}
  video.pause();
  if(next){video.src=next;sync();}else{video.removeAttribute('src');video.load();}
 }
 video.addEventListener('loadeddata',sync);
 window.addEventListener('wallpaper-media',e=>apply(e.detail?.url));
 const file=typeof wallpaperFile==='string'?wallpaperFile:null;if(file&&/\.(mp4|webm)$/.test(file))apply('/api/assets/wallpapers/'+file);
 window.addEventListener('native-window-state',e=>{nativeHidden=!!e.detail?.minimized;sync();});
 window.addEventListener('wallpaper-rendered',sync);document.addEventListener('visibilitychange',sync);reduced.addEventListener('change',sync);
})();
