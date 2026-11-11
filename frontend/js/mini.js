(() => {
 let state=null,lastPath=null,busy=false,timer=null,metadataGeneration=0,nativeHidden=false,metadataKey='',retryMetadataAt=0,commandRevision=0;
 const metadataCache=new Map();
 const toggle=document.getElementById('mini-toggle'),volume=document.getElementById('mini-volume'),error=document.getElementById('mini-error');
 const controls=[...document.querySelectorAll('button[data-action]'),toggle];
 let errorKind='';
 function showError(text,kind='command'){error.textContent=text||'';error.hidden=!text;errorKind=text?kind:'';}
 async function request(path,body,method='POST'){
  const response=await fetch('/api/'+path,{method,cache:'no-store',signal:AbortSignal.timeout(7000),...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
  const data=await response.json();if(!response.ok)throw new Error(data.detail||'Ошибка '+response.status);return data;
 }
 async function command(action){if(busy)return;busy=true;commandRevision++;clearTimeout(timer);controls.forEach(b=>b.disabled=true);
  try{if(action==='pause'||action==='resume'){toggle.textContent=action==='pause'?'▶':'Ⅱ';}await request('player/'+action);showError('');}catch(e){showError(e.message);}finally{busy=false;void poll();}}
 document.querySelectorAll('[data-action]').forEach(b=>b.addEventListener('click',()=>command(b.dataset.action)));
 toggle.addEventListener('click',()=>{if(state?.state==='playing')void command('pause');else if(state?.state==='paused')void command('resume');});
 document.getElementById('mini-hide').addEventListener('click',()=>request('desktop/hide_mini').catch(e=>showError(e.message)));
 document.getElementById('mini-expand').addEventListener('click',()=>request('desktop/show').catch(e=>showError(e.message)));
 let volumeBusy=false,pendingVolume=null;
 async function flushVolume(){if(volumeBusy)return;volumeBusy=true;while(pendingVolume!==null){const value=pendingVolume;pendingVolume=null;try{await request('player/volume',{volume:value});}catch(e){showError(e.message);}}volumeBusy=false;}
 volume.addEventListener('input',()=>{pendingVolume=Number(volume.value);void flushVolume();});
 function time(ms){const s=Math.max(0,Math.floor(ms/1000));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');}
 function safeCover(url){
  if(typeof url!=='string')return false;
  if(/^\/api\/assets\/(?:track-covers|online-covers)\/[a-f0-9]{64}\.jpg$/.test(url))return true;
  try{const parsed=new URL(url);return parsed.protocol==='https:'&&(parsed.hostname.endsWith('.sndcdn.com')||['i.scdn.co','mosaic.scdn.co','i.ytimg.com','img.youtube.com'].includes(parsed.hostname))&&!parsed.username&&!parsed.password&&(!parsed.port||parsed.port==='443');}catch{return false;}
 }
 async function metadata(path,provided){const generation=++metadataGeneration;
  const cover=document.getElementById('mini-cover');
  // A slow metadata response for the next song must not leave the previous
  // song's cover visible next to its filename.
  if(cover.dataset.path!==(path||'')){cover.dataset.path=path||'';cover.dataset.url='';cover.replaceChildren(document.createTextNode('♫'));}
  document.getElementById('mini-title').textContent=state?.filename||'Нет выбранной песни';document.getElementById('mini-artist').textContent='';
  if(!path){cover.dataset.url='';metadataKey='';cover.replaceChildren(document.createTextNode('♫'));return;}
  try{const meta=provided||metadataCache.get(path)||(await request('library/metadata',{paths:[path]})).tracks?.[0];if(generation!==metadataGeneration)return;
   if(meta){metadataCache.set(path,meta);if(metadataCache.size>64)metadataCache.delete(metadataCache.keys().next().value);metadataKey=JSON.stringify([meta.path,meta.title,meta.artist,meta.cover_url]);}
   document.getElementById('mini-title').textContent=meta?.title||state?.filename||'Нет выбранной песни';document.getElementById('mini-artist').textContent=meta?.artist||'';
   if(cover.dataset.url!==(meta?.cover_url||'')){cover.dataset.url=meta?.cover_url||'';cover.replaceChildren(document.createTextNode('♫'));if(safeCover(meta?.cover_url)){const image=new Image();image.alt='';image.onerror=()=>image.remove();image.src=meta.cover_url;cover.append(image);}}
  }catch(e){retryMetadataAt=Date.now()+1500;showError(e.message);}
 }
 let polling=false;
 async function poll(){clearTimeout(timer);if(polling)return;polling=true;
  try{const revision=commandRevision;const nextState=await request('player/state',null,'GET');
   if(busy||revision!==commandRevision)return;
   state=nextState;window.miniAppearance?.update(state);
   if(typeof state.window_visibility?.mini==='boolean')nativeHidden=!state.window_visibility.mini;
   if(document.hidden||nativeHidden)return;
   const fresh=state.current_metadata;const nextKey=fresh?JSON.stringify([fresh.path,fresh.title,fresh.artist,fresh.cover_url]):'';
   if(lastPath!==state.file_path||(fresh&&nextKey!==metadataKey)||retryMetadataAt&&Date.now()>=retryMetadataAt){lastPath=state.file_path;retryMetadataAt=0;void metadata(lastPath,fresh);}
   toggle.textContent=state.state==='playing'?'Ⅱ':'▶';controls.forEach(b=>b.disabled=busy||!state.file_path);
   toggle.disabled=busy||!['playing','paused'].includes(state.state);
   if(!volumeBusy&&pendingVolume===null&&document.activeElement!==volume)volume.value=String(state.volume??50);
   document.getElementById('mini-time').textContent=time(state.time_ms)+' / '+time(state.duration_ms);
   if(state.playback_error)showError(state.playback_error,'playback');
   else if(['playback','connection'].includes(errorKind))showError('');
  }catch(e){showError('Нет соединения с плеером: '+e.message,'connection');}finally{polling=false;timer=setTimeout(poll,document.hidden||nativeHidden?500:250);}
 }
 window.addEventListener('native-window-state',event=>{nativeHidden=Boolean(event.detail?.minimized);clearTimeout(timer);if(!nativeHidden)void poll();});
 document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)void poll();});
 void poll();
})();
