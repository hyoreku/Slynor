(() => {
 const message=document.getElementById('desktop-settings-message');
 void fetch('/api/online/provider-status',{signal:AbortSignal.timeout(5000)}).then(r=>r.json()).then(d=>{document.getElementById('provider-dependencies').textContent='Загрузчики: spotDL '+(d.spotdl||'не установлен')+', SCDL '+(d.scdl||'не установлен')+'. FFmpeg '+(d.ffmpeg?'найден.':'не найден. См. requirements-download.txt и инструкцию этапа 14.');}).catch(()=>{document.getElementById('provider-dependencies').textContent='Не удалось проверить загрузчики.';});
 const close=document.getElementById('close-to-tray');let saved=true;
 async function command(action){
  const response=await fetch('/api/desktop/'+action,{method:'POST',signal:AbortSignal.timeout(5000)});
  const data=await response.json();if(!response.ok)throw new Error(data.detail||'Команда окна недоступна.');return data;
 }
 document.getElementById('open-mini-player').addEventListener('click',()=>command('mini').catch(error=>{result.textContent='Ошибка: '+error.message;}));
 document.getElementById('capture-diagnostics').addEventListener('click',async()=>{try{const r=await fetch('/api/diagnostics/stacks',{method:'POST',signal:AbortSignal.timeout(3000)});if(!r.ok)throw new Error('Сервер не ответил');message.textContent='Снимок сохранён в %LOCALAPPDATA%/OwnPlayerMusic/logs/stacks.log';}catch(e){message.textContent='Ошибка диагностики: '+e.message;}});
 document.getElementById('quit-application').addEventListener('click',()=>command('quit').catch(error=>{message.textContent='Ошибка: '+error.message;}));
 close.addEventListener('change',async()=>{
  const requested=close.checked;close.disabled=true;
  try{await window.pywebview.api.save_desktop_preferences(requested);saved=requested;message.textContent='Настройка сохранена.';}
  catch(error){close.checked=saved;message.textContent='Не удалось сохранить: '+error.message;}
  finally{close.disabled=false;}
 });
 async function load(data){
  saved=data.close_to_tray??true;close.checked=saved;
  try{
   const response=await fetch('/api/desktop/status',{signal:AbortSignal.timeout(3000)});const status=await response.json();
   if(!response.ok)throw new Error(status.detail||'Трей недоступен.');
   close.disabled=false;message.textContent=status.tray_available?'В скрытом окне спектр отключён. Полный выход — через меню трея или кнопку ниже.':status.tray_error||'Трей запускается. Если он недоступен, закрытие завершит приложение.';
  }catch(error){message.textContent='Трей недоступен: '+error.message;}
 }
 window.addEventListener('visual-settings-loaded',event=>load(event.detail));
 if(window.visualSettingsSnapshot)void load(window.visualSettingsSnapshot);
})();
