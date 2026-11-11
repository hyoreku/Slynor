// View preference never stops audio, drops the queue, or changes the mini window.
(() => {
 const input=document.getElementById('playing-screen-enabled'),message=document.getElementById('playing-screen-message');let ready=false,saving=false,confirmed=true;
 function apply(value){window.playingScreenEnabled=value;input.checked=value;
  const cover=document.getElementById('player-cover');cover.disabled=!value;cover.setAttribute('aria-disabled',String(!value));cover.title=value?'Открыть экран текущей песни':'Экран текущей песни отключён в настройках';
  if(!value&&activeScreen==='playing'){const kind=playbackLibraryContext?.kind;restorePlaybackLibraryContext();showScreen(kind==='online'?'online':kind==='artist'?'artist':'music');}
 }
 function load(data){confirmed=data.playing_screen_enabled??true;apply(confirmed);ready=true;input.disabled=false;}
 window.addEventListener('visual-settings-loaded',e=>load(e.detail));if(window.visualSettingsSnapshot)load(window.visualSettingsSnapshot);
 input.addEventListener('change',async()=>{if(!ready||saving)return;const value=input.checked;apply(value);saving=true;input.disabled=true;
  try{await window.pywebview.api.save_playing_screen(value);confirmed=value;message.textContent='Настройка экрана песни сохранена.';}catch(e){apply(confirmed);message.textContent='Не удалось сохранить: '+e.message;}finally{saving=false;input.disabled=false;}
 });
})();
// Show bounded timing fields only. No filenames, URLs, cookies or device names.
(() => {
 const section=document.getElementById('audio-output-message').parentElement;
 const group=document.createElement('div');group.className='startup-diagnostics';
 const heading=document.createElement('h4');heading.textContent='Открытие аудио';
 const button=document.createElement('button');button.type='button';button.className='secondary-button';button.textContent='Показать время запуска';
 const result=document.createElement('p');result.className='settings-note';result.setAttribute('role','status');
 group.append(heading,button,result);section.append(group);
 button.addEventListener('click',async()=>{button.disabled=true;try{
  const r=await fetch('/api/player/state',{cache:'no-store',signal:AbortSignal.timeout(3000)});if(!r.ok)throw Error('Нет связи с плеером');const m=(await r.json()).startup;
  if(!m){result.textContent='Сначала запустите трек.';return;}
  const format=n=>Number.isFinite(n)?Math.max(0,Math.round(n))+' мс':'ожидание';
  result.textContent=(m.source==='online'?'Интернет':'Локальный файл')+' · до команды VLC: '+format(m.request_to_play_ms)+' · до события Playing: '+format(m.request_to_playing_event_ms)+'. Это не измерение появления звука в динамиках.';
 }catch(e){result.textContent=e.message;}finally{button.disabled=false;}});
})();

// Finish transient view reveals immediately when motion is disabled or the window hides.
(() => {
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const cancel=()=>{screenRevealAnimation?.cancel();screenRevealAnimation=null;};
 reduced.addEventListener('change',()=>{if(reduced.matches)cancel();});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)cancel();});
 window.addEventListener('native-window-state',e=>{if(e.detail?.minimized)cancel();});
})();
