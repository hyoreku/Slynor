// Eight styles share the supplied alpha silhouette. No letter fallback or broken SVG paths.
(() => {
 const box=document.getElementById('app-icon-options'),msg=document.getElementById('app-icon-message'),brand=document.querySelector('.brand-mark');
 const names=['Белый','Акцент','Лава','Пиксель','Цвета песни','Хром','Неон','Космос'];
 const descriptions=['Чистый белый знак','Цвет оформления приложения','Живые перемешивающиеся цвета','Пиксельная версия силуэта','Три главных цвета текущей обложки','Холодный металлический блеск','Светящийся контур','Звёзды и северное сияние'];
 let selected='1',lastCover='',paletteRevision=0,nativeHidden=false;
 const writer=createSettingsWriter(value=>window.pywebview.api.save_app_icon(value),apply);
 const reduced=matchMedia('(prefers-reduced-motion: reduce)'),visible=new WeakMap();
 function motion(){for(const mark of document.querySelectorAll('.app-mark'))mark.classList.toggle('is-motion',!document.hidden&&!nativeHidden&&!reduced.matches&&!!visible.get(mark));}
 const observer=new IntersectionObserver(entries=>{for(const e of entries)visible.set(e.target,e.isIntersecting);motion();});
 function mark(value){const node=document.createElement('span');node.className='app-mark';node.dataset.variant=value;node.setAttribute('aria-hidden','true');for(let i=0;i<3;i++)node.append(document.createElement('i'));observer.observe(node);return node;}
 function apply(value){selected=/^[1-8]$/.test(value)?value:'1';brand.dataset.variant=selected;for(const old of brand.querySelectorAll('.app-mark'))observer.unobserve(old);brand.replaceChildren(mark(selected));for(const b of box.children)b.setAttribute('aria-pressed',String(b.dataset.value===selected));}
 for(let i=1;i<=8;i++){
  const b=document.createElement('button');b.type='button';b.dataset.value=String(i);b.setAttribute('aria-label',`Значок ${i} — ${names[i-1]}`);b.title=descriptions[i-1];
  const label=document.createElement('span');label.className='app-mark-label';label.textContent=names[i-1];b.append(mark(String(i)),label);
  b.disabled=true;
  b.addEventListener('click',()=>{apply(b.dataset.value);writer.save(selected,msg);});box.append(b);
 }
 async function palette() {
  const url = document.querySelector('#player-cover img')?.getAttribute('src') || '';
  if (lastCover === url) return;
  lastCover = url;
  const revision = ++paletteRevision;
  const colors = url ? await window.coverPalette(url) : null;
  if (revision !== paletteRevision) return;
  const style = document.documentElement.style;
  for (let i = 0; i < 3; i++) {
   const property = '--mark-song-' + (i + 1);
   if (colors) style.setProperty(property, colors[i].join(','));
   else style.removeProperty(property);
  }
 }
 new MutationObserver(palette).observe(document.getElementById('player-cover'),{childList:true,subtree:true,attributes:true,attributeFilter:['src']});palette();
 document.addEventListener('visibilitychange',motion);reduced.addEventListener('change',motion);window.addEventListener('native-window-state',e=>{nativeHidden=!!e.detail?.minimized;motion();});
 const technical=document.getElementById('settings-diagnostics');
 const diag=document.createElement('button');diag.type='button';diag.className='secondary-button';diag.textContent='Проверить загрузчики и последние ошибки';
 const output=document.createElement('pre');output.className='provider-report';output.hidden=true;technical.append(diag,output);
 diag.addEventListener('click',async()=>{diag.disabled=true;output.hidden=false;output.textContent='Проверка…';try{const r=await fetch('/api/online/diagnostics',{signal:AbortSignal.timeout(8000)});if(!r.ok)throw Error('Диагностика недоступна');output.textContent=JSON.stringify(await r.json(),null,2);}catch(e){output.textContent=e.message;}finally{diag.disabled=false;}});
 function load(e){const value=e.detail.app_icon||'1';writer.setSaved(value);apply(value);for(const button of box.children)button.disabled=false;}window.addEventListener('visual-settings-loaded',load);if(window.visualSettingsSnapshot)load({detail:window.visualSettingsSnapshot});else apply('1');
})();
