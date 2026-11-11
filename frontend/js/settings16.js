// Eight styles share the supplied alpha silhouette. No letter fallback or broken SVG paths.
(() => {
 const box=document.getElementById('app-icon-options'),msg=document.getElementById('app-icon-message'),brand=document.querySelector('.brand-mark');
 const names=['Белый','Акцент','Лава','Пиксель','Цвета песни','Хром','Неон','Космос'];
 const descriptions=['Чистый белый знак','Цвет оформления приложения','Живые перемешивающиеся цвета','Пиксельная версия силуэта','Три главных цвета текущей обложки','Холодный металлический блеск','Светящийся контур','Звёзды и северное сияние'];
 let selected='1',busy=false,lastCover='',paletteRevision=0,nativeHidden=false;
 const reduced=matchMedia('(prefers-reduced-motion: reduce)'),visible=new WeakMap();
 function motion(){for(const mark of document.querySelectorAll('.app-mark'))mark.classList.toggle('is-motion',!document.hidden&&!nativeHidden&&!reduced.matches&&!!visible.get(mark));}
 const observer=new IntersectionObserver(entries=>{for(const e of entries)visible.set(e.target,e.isIntersecting);motion();});
 function mark(value){const node=document.createElement('span');node.className='app-mark';node.dataset.variant=value;node.setAttribute('aria-hidden','true');for(let i=0;i<3;i++)node.append(document.createElement('i'));observer.observe(node);return node;}
 function apply(value){selected=/^[1-8]$/.test(value)?value:'1';brand.dataset.variant=selected;for(const old of brand.querySelectorAll('.app-mark'))observer.unobserve(old);brand.replaceChildren(mark(selected));for(const b of box.children)b.setAttribute('aria-pressed',String(b.dataset.value===selected));}
 for(let i=1;i<=8;i++){
  const b=document.createElement('button');b.type='button';b.dataset.value=String(i);b.setAttribute('aria-label',`Значок ${i} — ${names[i-1]}`);b.title=descriptions[i-1];
  const label=document.createElement('span');label.className='app-mark-label';label.textContent=names[i-1];b.append(mark(String(i)),label);
  b.addEventListener('click',async()=>{if(busy)return;busy=true;const before=selected;apply(b.dataset.value);for(const item of box.children)item.disabled=true;try{await window.pywebview.api.save_app_icon(selected);msg.textContent='Значок сохранён.';}catch(e){apply(before);msg.textContent='Не удалось сохранить: '+e.message;}finally{busy=false;for(const item of box.children)item.disabled=false;}});box.append(b);
 }
 function palette(){const url=document.querySelector('#player-cover img')?.getAttribute('src')||'';if(lastCover===url)return;lastCover=url;const revision=++paletteRevision;
  for(let i=1;i<=3;i++)document.documentElement.style.removeProperty('--mark-song-'+i);if(!url)return;
  void window.coverPalette?.(url).then(colors=>{if(!colors||revision!==paletteRevision)return;colors.forEach((c,i)=>document.documentElement.style.setProperty('--mark-song-'+(i+1),c.join(',')));});
 }
 new MutationObserver(palette).observe(document.getElementById('player-cover'),{childList:true,subtree:true,attributes:true,attributeFilter:['src']});palette();
 document.addEventListener('visibilitychange',motion);reduced.addEventListener('change',motion);window.addEventListener('native-window-state',e=>{nativeHidden=!!e.detail?.minimized;motion();});
 const technical=document.getElementById('audio-output-message').parentElement;
 const diag=document.createElement('button');diag.type='button';diag.className='secondary-button';diag.textContent='Проверить загрузчики и последние ошибки';
 const output=document.createElement('pre');output.className='provider-report';output.hidden=true;technical.append(diag,output);
 diag.addEventListener('click',async()=>{diag.disabled=true;output.hidden=false;output.textContent='Проверка…';try{const r=await fetch('/api/online/diagnostics',{signal:AbortSignal.timeout(8000)});if(!r.ok)throw Error('Диагностика недоступна');output.textContent=JSON.stringify(await r.json(),null,2);}catch(e){output.textContent=e.message;}finally{diag.disabled=false;}});
 function load(e){apply(e.detail.app_icon||'1');}window.addEventListener('visual-settings-loaded',load);if(window.visualSettingsSnapshot)load({detail:window.visualSettingsSnapshot});else apply('1');
})();
