// LANGUAGE / DEVELOPER MODE: immediate preview, serialized atomic persistence.
(() => {
 const language=document.getElementById('language-choice'), developer=document.getElementById('developer-mode');
 const languageMessage=document.getElementById('language-message'), developerMessage=document.getElementById('developer-message');
 let ready=false, writing=false, pending=null;
 function preview() {
  document.documentElement.dataset.developer=String(developer.checked);
  window.i18n.setLanguage(language.value);
 }
 async function flush() {
  if(writing||!pending)return;writing=true;
  while(pending){const data=pending;pending=null;
   try{await window.pywebview.api.save_preferences(data.language,data.developer_mode);languageMessage.textContent='Язык сохранён.';developerMessage.textContent='Настройка сохранена.';}
   catch(error){languageMessage.textContent='Не удалось сохранить настройки: '+error.message;developerMessage.textContent=languageMessage.textContent;}
  }writing=false;
 }
 function changed(){if(!ready)return;preview();pending={language:language.value,developer_mode:developer.checked};void flush();}
 language.addEventListener('change',changed);developer.addEventListener('change',changed);
 function load(data){language.value=data.language||'ru';developer.checked=Boolean(data.developer_mode);ready=true;language.disabled=developer.disabled=false;preview();}
 window.addEventListener('visual-settings-loaded',event=>load(event.detail));
 if(window.visualSettingsSnapshot)load(window.visualSettingsSnapshot);
 const result=document.getElementById('result');
 new MutationObserver(()=>{const source=window.i18n.source(result);result.classList.toggle('is-error',/ошиб|не удалось|недоступ|не найден|error|failed|unavailable|not found|cannot/i.test(source));}).observe(result,{childList:true,characterData:true,subtree:true});
})();
