// External SVG masks. Observe changed controls, not a full library scan on every timer tick.
(() => {
 const fixed={'back-to-tracks':'down','shuffle-button':'shuffle','open-mini-player':'mini','equalizer-button':'equalizer','queue-button':'list','eq-reset':'reset','eq-retry':'reset','eq-close':'close','close-queue-button':'close','close-track-details':'close','close-playlist-editor':'close','mini-hide':'hide','mini-expand':'restore'};
 const nav={home:'home',music:'library',online:'search',settings:'settings'};
 const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
 const animatedGlyphs = new Set(['play-toggle-button', 'home-hero-play', 'now-play', 'sidebar-toggle']);
 const glyphAnimations = new WeakMap();
 let miniAnimation;
 function animateGlyph(node, previousIcon) {
    if (!previousIcon || !animatedGlyphs.has(node.id)) return;
    glyphAnimations.get(node)?.revert();
    if (document.hidden || reducedMotion.matches || !window.anime?.animate) return;

    const direction = node.dataset.uiIcon === 'expand' ? -1 : 1;
    const angle = node.id === 'sidebar-toggle' ? 22 * direction : 0;
    const animation = window.anime.animate(node, {
        '--ui-glyph-rotation': [`${angle}deg`, '0deg'],
        '--ui-glyph-scale': [.78, 1],
        '--ui-glyph-opacity': [.45, 1],
        duration: 180,
        ease: 'outCubic',
    });
    glyphAnimations.set(node, animation);
 }
 window.animateButtonGlyph = animateGlyph;
 reducedMotion.addEventListener('change', () => {
    if (!reducedMotion.matches) return;
    miniAnimation?.cancel();
    for (const id of animatedGlyphs) glyphAnimations.get(document.getElementById(id))?.revert();
 });
 function put(node, name) {
    if (!node || !name || node.dataset.uiIcon === name) return;
    const previousIcon = node.dataset.uiIcon;
    node.dataset.uiIcon = name;
    node.style.setProperty('--ui-icon', `url('/static/assets/icons/${name}.svg')`);
    animateGlyph(node, previousIcon);
    if (previousIcon && node.id === 'mini-toggle' && !document.hidden && !reducedMotion.matches) {
        miniAnimation?.cancel();
        miniAnimation = node.animate([{opacity:.45, transform:'scale(.82)'}, {opacity:1, transform:'scale(1)'}],
            {duration:180, easing:'cubic-bezier(.2,.8,.2,1)'});
    }
 }
 function control(node){if(!node||node.nodeType!==1)return;put(node,fixed[node.id]);if(node.matches('[data-action="previous"],[data-action="next"],[data-action="stop"]'))put(node,node.dataset.action);if(node.matches('#play-toggle-button,#mini-toggle'))put(node,/Ⅱ|❚❚|⏸/.test(node.textContent)?'pause':'play');if(node.classList.contains('track-actions-button'))put(node,'more');if(node.matches('.online-track-actions .icon-button'))put(node,'play');}
 const selector=Object.keys(fixed).map(id=>'#'+id).join(',')+',#play-toggle-button,#mini-toggle,[data-action="previous"],[data-action="next"],[data-action="stop"],.track-actions-button,.online-track-actions .icon-button';
 function scan(parent){control(parent);parent.querySelectorAll?.(selector).forEach(control);}
 document.querySelectorAll('.nav-button').forEach(n=>put(n.querySelector('.nav-icon'),nav[n.dataset.screen]));scan(document.body);
 function sidebar(){put(document.getElementById('sidebar-toggle'),document.documentElement.classList.contains('sidebar-collapsed')?'expand':'collapse');}
 new MutationObserver(records=>{for(const r of records){const target=r.target.nodeType===1?r.target:r.target.parentElement;control(target?.closest('button'));for(const n of r.addedNodes)if(n.nodeType===1)scan(n);}}).observe(document.body,{subtree:true,childList:true,characterData:true});
 new MutationObserver(sidebar).observe(document.documentElement,{attributes:true,attributeFilter:['class']});sidebar();
})();
