// App network routing; proxy passwords are sent only to the local backend, never persisted here.
(() => {
 const group=document.getElementById('settings-network');
 group.setAttribute('aria-labelledby','settings-network-heading');
 const box=document.createElement('div');box.className='network-settings';
 box.innerHTML=`<h2 id="settings-network-heading">Сеть и прокси</h2>
 <p class="settings-note">Поиск, обложки, потоки и загрузчики используют выбранный маршрут. Адрес и порт возьмите из своей VPN-программы — приложение их не угадывает.</p>
 <form id="network-form"><div class="network-fields">
 <label for="network-mode">Подключение<select id="network-mode"><option value="system">Системные настройки</option><option value="manual">Указать прокси</option><option value="direct">Без явного прокси</option></select></label>
 <label id="network-url-label">Адрес прокси<input id="network-url" type="text" autocomplete="off" spellcheck="false" placeholder="http://адрес:порт или socks5://адрес:порт" maxlength="2048"></label>
 <label for="network-first">Искать сначала<select id="network-first"><option value="auto">Автоматически: Spotify → YouTube → SoundCloud</option><option value="youtube">YouTube</option><option value="soundcloud">SoundCloud</option><option value="spotify">Spotify</option></select></label>
 </div><fieldset id="network-auth"><legend>Авторизация прокси — только если требуется</legend>
 <label>Логин<input id="network-user" type="text" autocomplete="off" maxlength="512"></label>
 <label>Пароль<input id="network-password" type="password" autocomplete="off" maxlength="512"></label>
 <label><input id="network-clear-auth" type="checkbox"> Удалить ранее введённые данные авторизации</label>
 <p class="settings-note">Логин и пароль хранятся только до закрытия приложения и не входят в диагностический отчёт.</p></fieldset>
 <div class="network-actions"><button type="submit" class="primary-button" id="network-save" hidden>Подключить прокси</button><button type="button" class="secondary-button" id="network-check">Проверить доступ</button></div></form>
 <p id="network-status" class="settings-note" role="status"></p><pre id="network-report" class="network-check-report" hidden></pre>`;
 group.prepend(box);
 const form=box.querySelector('form'),mode=box.querySelector('#network-mode'),url=box.querySelector('#network-url'),first=box.querySelector('#network-first'),user=box.querySelector('#network-user'),password=box.querySelector('#network-password'),clear=box.querySelector('#network-clear-auth'),message=box.querySelector('#network-status'),report=box.querySelector('#network-report'),save=box.querySelector('#network-save'),check=box.querySelector('#network-check');
 let saved, busy = false;
 const addressRow = document.getElementById('network-url-label');
 const credentials = document.getElementById('network-auth');

 function syncControls() {
  const manual = mode.value === 'manual';
  addressRow.hidden = credentials.hidden = save.hidden = !manual;
  mode.disabled = first.disabled = check.disabled = busy || !saved;
  url.disabled = credentials.disabled = save.disabled = busy || !saved || !manual;
 }
 function apply(value, keepDraft = false) {
  saved = value;
  if (!keepDraft) {
   mode.value = value.mode;
   url.value = value.url || '';
  }
  first.value = value.search_first || 'auto';
  message.textContent = value.error || (value.mode === 'manual' && mode.value === 'manual' && value.proxy_detected
   ? 'Прокси: ' + value.effective
   : value.mode === 'direct' ? 'Явный прокси отключён.' : '');
 }
 async function request(method, body) {
  const response = await fetch('/api/network/' + (method === 'CHECK' ? 'check' : 'settings'), {
   method: method === 'GET' ? 'GET' : 'POST', cache: 'no-store', signal: AbortSignal.timeout(20000),
   ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Не удалось применить настройки сети.');
  return data;
 }
 async function saveNetwork(body, keepDraft = false, keepOnError = keepDraft) {
  if (busy || !saved) return;
  busy = true;
  message.textContent = '';
  syncControls();
  try {
   window.dispatchEvent(new Event('ownplayer-user-command'));
   apply(await request('SAVE', body), keepDraft);
   if (!keepDraft) { user.value = ''; password.value = ''; clear.checked = false; }
   report.hidden = true;
   message.textContent = 'Настройки сохранены. Текущие задания отменены; повторите поиск и запуск трека.';
  } catch (error) {
   apply(saved, keepOnError);
   message.textContent = error.message;
  } finally {
   busy = false;
   syncControls();
  }
 }
 mode.addEventListener('change', () => {
  if (!saved || busy) return;
  syncControls();
  if (mode.value === 'manual') {
   message.textContent = 'Укажите адрес и нажмите «Подключить прокси».';
   return;
  }
  void saveNetwork({ mode: mode.value, url: saved.url || '', search_first: first.value });
 });
 first.addEventListener('change', () => {
  if (!saved || busy) return;
  void saveNetwork({ mode: saved.mode, url: saved.url || '', search_first: first.value }, true);
 });
 form.addEventListener('submit', event => {
  event.preventDefault();
  if (mode.value !== 'manual') return;
  const body = { mode: mode.value, url: url.value.trim(), search_first: first.value, clear_credentials: clear.checked };
  if (user.value || password.value) { body.username = user.value; body.password = password.value; }
  void saveNetwork(body, false, true);
 });
 check.addEventListener('click', async () => {
  if (busy || !saved) return;
  busy = true;
  syncControls();
  report.hidden = false;
  report.textContent = 'Проверяем сохранённый маршрут…';
  try { report.textContent = JSON.stringify(await request('CHECK'), null, 2); }
  catch (error) { report.textContent = error.message; }
  finally { busy = false; syncControls(); }
 });
 syncControls();
 void request('GET').then(value => apply(value)).catch(error => { message.textContent = error.message; }).finally(syncControls);

 // Selectable text, explicit copy with fallback, native Save dialog (browser download fallback).
 const output=document.querySelector('.provider-report:not(#network-report)');
 const actions=document.createElement('div');actions.className='network-actions';
 const copy=document.createElement('button'),download=document.createElement('button'),status=document.createElement('p');
 copy.type=download.type='button';copy.className=download.className='secondary-button';copy.id='copy-diagnostics';download.id='save-diagnostics';copy.textContent='Копировать отчёт';download.textContent='Сохранить отчёт';status.role='status';actions.append(copy,download);
 output.after(actions,status);
 const valid=()=>{try{return !!JSON.parse(output.textContent).events;}catch{return false;}};
 const update=()=>{copy.disabled=download.disabled=!valid();};new MutationObserver(update).observe(output,{childList:true,characterData:true,subtree:true});update();
 copy.addEventListener('click',async()=>{try{
  const text=output.textContent;
  try{await navigator.clipboard.writeText(text);}catch{
   const input=document.createElement('textarea');input.value=text;input.style.position='fixed';input.style.opacity='0';document.body.append(input);input.select();const ok=document.execCommand('copy');input.remove();if(!ok){const range=document.createRange();range.selectNodeContents(output);getSelection().removeAllRanges();getSelection().addRange(range);throw Error('Текст выделен. Нажмите Ctrl+C.');}
  }status.textContent='Отчёт скопирован.';
 }catch(e){status.textContent=e.message;}});
 download.addEventListener('click',async()=>{download.disabled=true;try{
  if(window.pywebview?.api?.save_diagnostics_report){const result=await window.pywebview.api.save_diagnostics_report(output.textContent);status.textContent=result.saved?'Отчёт сохранён.':'Сохранение отменено.';}
  else{const link=document.createElement('a'),uri=URL.createObjectURL(new Blob([output.textContent],{type:'application/json'}));link.href=uri;link.download='Slynor-diagnostics.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(uri),1000);status.textContent='Запрошено сохранение отчёта.';}
 }catch{status.textContent='Не удалось сохранить отчёт. Используйте «Копировать отчёт».';}finally{update();}});
})();
