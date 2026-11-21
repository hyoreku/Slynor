// Progressive, keyboard-accessible comboboxes. Native selects remain the data model.
(() => {
 const widgets=new Map();let active=null,typed='',typedAt=0;
 function close(focus=false){if(!active)return;const w=active;active=null;w.button.setAttribute('aria-expanded','false');w.button.removeAttribute('aria-activedescendant');try{w.menu.hidePopover();}catch{}w.menu.remove();if(focus)w.button.focus();}
 function sync(w) {
  const {select, button} = w;
  const value = select.selectedOptions[0]?.textContent || '—';
  const label = document.querySelector(`label[for="${CSS.escape(select.id)}"]`);
  const name = (label?.textContent.trim() || select.getAttribute('aria-label') || 'Выбор') + ': ' + value;
  const iconOnly = Boolean(select.dataset.selectIcon);
  if (!iconOnly && w.valueLabel.textContent !== value) w.valueLabel.textContent = value;
  if (button.disabled !== select.disabled) button.disabled = select.disabled;
  const title = iconOnly ? name : value;
  if (button.title !== title) button.title = title;
  if (button.getAttribute('aria-label') !== name) button.setAttribute('aria-label', name);
 }
 function position(w) {
  const rect = w.button.getBoundingClientRect();
  const edge = 8;
  const gap = 5;
  const width = Math.min(420, innerWidth - 2 * edge);
  w.menu.style.minWidth = Math.min(rect.width, width) + 'px';
  w.menu.style.maxWidth = width + 'px';

  if (w.select.dataset.selectLayout === 'audio-output') {
   const anchorTop = Math.min(rect.top, innerHeight - edge);
   const anchorBottom = Math.max(rect.bottom, edge);
   const below = Math.max(0, innerHeight - anchorBottom - gap - edge);
   const above = Math.max(0, anchorTop - gap - edge);
   const idealHeight = Math.min(w.menu.scrollHeight + 2, 300, innerHeight * .65);
   const opensUp = idealHeight > below && above > below;
   w.menu.style.maxHeight = Math.min(300, innerHeight * .65, opensUp ? above : below) + 'px';
   w.menu.style.top = (opensUp ? anchorTop - gap - w.menu.offsetHeight : anchorBottom + gap) + 'px';
   w.menu.style.left = Math.max(edge, Math.min(rect.right - w.menu.offsetWidth, innerWidth - w.menu.offsetWidth - edge)) + 'px';
   return;
  }

  w.menu.style.left = Math.max(edge, Math.min(rect.left, innerWidth - w.menu.offsetWidth - edge)) + 'px';
  w.menu.style.top = Math.max(edge, Math.min(rect.bottom + gap, innerHeight - w.menu.offsetHeight - edge)) + 'px';
 }
 function open(w, index) {
  close();
  sync(w);
  w.menu.replaceChildren();
  w.items = [];
  for (const [i, option] of [...w.select.options].entries()) {
   const item = document.createElement('div');
   item.role = 'option';
   item.id = w.id + '-' + i;
   item.tabIndex = -1;
   item.textContent = option.textContent;
   if (w.select.dataset.selectLayout === 'audio-output') item.title = option.textContent;
   if (option.dataset.userText) item.dataset.userText = option.dataset.userText;
   item.setAttribute('aria-selected', String(option.selected));
   item.setAttribute('aria-disabled', String(option.disabled));
   item.addEventListener('click', () => {
    if (option.disabled) return;
    w.select.value = option.value;
    w.select.dispatchEvent(new Event('input', {bubbles:true}));
    w.select.dispatchEvent(new Event('change', {bubbles:true}));
    sync(w);
    close(true);
   });
   w.menu.append(item);
   w.items.push(item);
  }
  (w.select.closest('dialog') || document.body).append(w.menu);
  active = w;
  w.button.setAttribute('aria-expanded', 'true');
  w.menu.showPopover?.();
  position(w);
  w.button.focus({preventScroll:true});
  focusItem(w, index ?? Math.max(0, w.select.selectedIndex));
 }
 function focusItem(w,index){if(!w.items.length)return;w.index=Math.max(0,Math.min(index,w.items.length-1));const item=w.items[w.index];w.menu.querySelector('.is-active')?.classList.remove('is-active');item.classList.add('is-active');const top=item.offsetTop,bottom=top+item.offsetHeight;if(top<w.menu.scrollTop)w.menu.scrollTop=top;else if(bottom>w.menu.scrollTop+w.menu.clientHeight)w.menu.scrollTop=bottom-w.menu.clientHeight;w.button.setAttribute('aria-activedescendant',item.id);}
 function init(s) {
  if (widgets.has(s) || s.multiple || s.size > 1 || s.hidden) return;
  const wrap = document.createElement('span');
  wrap.className = 'styled-select';
  s.before(wrap);
  wrap.append(s);
  s.classList.add('native-select-model');
  s.tabIndex = -1;
  s.setAttribute('aria-hidden', 'true');
  const button = document.createElement('button');
  button.type = 'button';
  const valueLabel = document.createElement('span');
  valueLabel.className = 'select-value';
  if (!s.dataset.selectIcon) button.append(valueLabel);
  button.role = 'combobox';
  button.setAttribute('aria-haspopup', 'listbox');
  button.setAttribute('aria-expanded', 'false');
  const description = s.getAttribute('aria-describedby');
  if (description) button.setAttribute('aria-describedby', description);
  const id = 'choice-' + (widgets.size + 1);
  const menu = document.createElement('div');
  menu.id = id;
  menu.className = 'select-popup';
  menu.role = 'listbox';
  menu.setAttribute('popover', 'manual');
  button.setAttribute('aria-controls', id);
  wrap.append(button);
  if (s.dataset.selectIcon) {
   wrap.dataset.selectIcon = s.dataset.selectIcon;
   menu.dataset.selectIcon = s.dataset.selectIcon;
  }
  if (s.dataset.selectLayout) {
   wrap.dataset.selectLayout = menu.dataset.selectLayout = s.dataset.selectLayout;
  }
  const w = {select:s, button, valueLabel, menu, id};
  widgets.set(s, w);
  sync(w);
  button.addEventListener('click', () => active === w ? close() : open(w));
  s.addEventListener('change', () => sync(w));
  s.addEventListener('focus', () => button.focus());
  new MutationObserver(() => {
   sync(w);
   if (active === w) close();
  }).observe(s, {childList:true, subtree:true, attributes:true, attributeFilter:['disabled','label','selected','hidden']});
 button.addEventListener('keydown',e=>{const enabled=[...s.options].map((o,i)=>o.disabled?-1:i).filter(i=>i>=0);if(['ArrowDown','ArrowUp','Home','End','Enter',' '].includes(e.key)){e.preventDefault();if(active!==w){open(w);return;}if(e.key==='Enter'||e.key===' '){w.items[w.index]?.click();return;}let index=enabled.indexOf(w.index);index=e.key==='Home'?0:e.key==='End'?enabled.length-1:Math.max(0,Math.min(enabled.length-1,index+(e.key==='ArrowDown'?1:-1)));focusItem(w,enabled[index]);}else if(e.key==='Escape'){e.preventDefault();close(true);}else if(e.key==='Tab')close();else if(e.key.length===1){typed=Date.now()-typedAt>600?e.key:typed+e.key;typedAt=Date.now();if(active!==w)open(w);const found=[...s.options].findIndex(o=>!o.disabled&&o.textContent.toLocaleLowerCase().startsWith(typed.toLocaleLowerCase()));if(found>=0)focusItem(w,found);}});
 const label=document.querySelector(`label[for="${CSS.escape(s.id)}"]`);label?.addEventListener('click',e=>{e.preventDefault();button.focus();});
 }
 function discover(){document.querySelectorAll('select').forEach(init);}
 discover();new MutationObserver(discover).observe(document.body,{childList:true,subtree:true});
 document.addEventListener('pointerdown',e=>{if(active&&!active.menu.contains(e.target)&&!active.button.contains(e.target))close();});window.addEventListener('resize',()=>close());document.addEventListener('scroll',e=>{if(active&&!active.menu.contains(e.target))position(active);},true);
 window.addEventListener('player-screen-changed',()=>close());
 setInterval(()=>{if(!document.hidden)for(const w of widgets.values())sync(w);},300);
})();
