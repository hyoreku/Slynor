// Sidebar lifecycle: one timer, no polling, no persisted manual collapsed state.
(() => {
    const root = document.documentElement;
    const sidebar = document.getElementById('sidebar');
    const toggle = document.getElementById('sidebar-toggle');
    const auto = document.getElementById('sidebar-auto-collapse');
    const message = document.getElementById('sidebar-settings-message');
    const delay = 60_000;
    let collapsed = false, hovering = false, timer = null, minimized = false;
    let ready = false, saving = false, pending = null, confirmed = true;
    let lastActivity = performance.now(), widthAnimation = null;
    function finishLayout() {
        widthAnimation?.cancel(); widthAnimation = null;
        root.style.removeProperty('--sidebar-width');
        root.classList.remove('sidebar-resizing');
        window.equalizerPanel?.layout();
        window.dispatchEvent(new Event('sidebar-layout-changed'));
        window.dispatchEvent(new Event('sidebar-animation-end'));
    }
    function updateLabel() {
        const label = collapsed ? 'Развернуть панель' : 'Свернуть панель';
        toggle.title = label; toggle.setAttribute('aria-label', label);
        toggle.setAttribute('aria-expanded', String(!collapsed));
        toggle.textContent = collapsed ? '›' : '‹';
    }
    function setCollapsed(value) {
        const from = parseFloat(getComputedStyle(root).getPropertyValue('--sidebar-width'));
        widthAnimation?.cancel(); widthAnimation = null;
        root.style.removeProperty('--sidebar-width');
        root.classList.add('sidebar-resizing');
        window.dispatchEvent(new Event('sidebar-animation-start'));
        collapsed = value;
        root.classList.toggle('sidebar-collapsed', collapsed);
        const to = parseFloat(getComputedStyle(root).getPropertyValue('--sidebar-width'));
        updateLabel();
        const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (!reduced && innerWidth > 560 && from !== to) {
            const started = performance.now();
            const step = now => {
                const progress = Math.min(1, (now - started) / 240);
                const eased = 1 - Math.pow(1 - progress, 3);
                root.style.setProperty('--sidebar-width', `${from + (to - from) * eased}px`);

                if (progress < 1) widthAnimation = {cancel: () => cancelAnimationFrame(requestAnimation)};
                else finishLayout();
                if (progress < 1) requestAnimation = requestAnimationFrame(step);
            };
            let requestAnimation = requestAnimationFrame(step);
            widthAnimation = {cancel: () => cancelAnimationFrame(requestAnimation)};
        } else {
            finishLayout();
        }
    }
    function blocked() {
        return hovering || sidebar.contains(document.activeElement)
            || Boolean(document.querySelector('dialog[open]'))
            || !document.getElementById('track-menu').hidden
            || !document.getElementById('queue-panel').hidden;
    }
    function eligible() {
        return !root.classList.contains('playing-focus') && ready && auto.checked && !collapsed && innerWidth > 560
            && activeScreen === 'playing' && currentPlaybackState === 'playing'
            && !document.hidden && !minimized;
    }
    function schedule(reset = false) {
        clearTimeout(timer); timer = null;
        if (reset) lastActivity = performance.now();
        if (!eligible() || blocked()) return;
        timer = setTimeout(() => {
            timer = null;
            if (eligible() && !blocked()) setCollapsed(true);
        }, Math.max(0, delay - (performance.now() - lastActivity)));
    }
    toggle.addEventListener('click', () => {if (activeScreen === 'playing') return; setCollapsed(!collapsed); schedule(true);});
    sidebar.addEventListener('pointerenter', () => {hovering = true; schedule(true);});
    sidebar.addEventListener('pointerleave', () => {hovering = false; schedule(true);});
    sidebar.addEventListener('pointermove', () => schedule(true), {passive: true});
    sidebar.addEventListener('keydown', () => schedule(true));
    sidebar.addEventListener('focusin', () => schedule(true));
    sidebar.addEventListener('focusout', () => queueMicrotask(() => schedule(true)));
    // Modal/menu close begins a fresh idle period, never an immediate collapse.
    const modalObserver = new MutationObserver(() => schedule(true));
    document.querySelectorAll('dialog,#track-menu,#queue-panel').forEach(element => {
        modalObserver.observe(element, {attributes:true,attributeFilter:['open','hidden']});
    });
    // Polling may update the same state text every second; reset only on a true change.
    let previousState = currentPlaybackState;
    new MutationObserver(() => {
        if (previousState !== currentPlaybackState) {
            previousState = currentPlaybackState; schedule(true);
        }
    }).observe(document.getElementById('player-state'), {childList:true,subtree:true,characterData:true});
    function syncPlayingFocus() {
        // Finish an interrupted width animation before taking the sidebar out of layout.
        finishLayout();
        const hidden = activeScreen === 'playing';
        root.classList.toggle('playing-focus', hidden);
        sidebar.inert = hidden;
        if (hidden && sidebar.contains(document.activeElement)) {
            document.getElementById('back-to-tracks').focus({preventScroll:true});
        }
        schedule(true);
    }
    window.addEventListener('player-screen-changed', syncPlayingFocus);
    window.addEventListener('resize', () => {finishLayout(); schedule(true);});
    document.addEventListener('visibilitychange', () => {
        if (document.hidden && widthAnimation) finishLayout();
    });
    window.addEventListener('native-window-state', event => {
        minimized = Boolean(event.detail?.minimized); schedule(true);
    });
    document.addEventListener('visibilitychange', () => schedule(true));
    document.addEventListener('language-changed', updateLabel);
    async function flush() {
        if (saving || pending === null) return;
        saving = true;
        while (pending !== null) {
            const value = pending; pending = null;
            try {
                await window.pywebview.api.save_sidebar_preferences(value);
                confirmed = value;
                message.textContent = 'Настройка боковой панели сохранена.';
            } catch (error) {
                if (pending === null) auto.checked = confirmed;
                message.textContent = 'Не удалось сохранить настройку боковой панели: ' + error.message;
                schedule(true);
            }
        }
        saving = false;
    }
    auto.addEventListener('change', () => {
        if (!ready) return;
        pending = auto.checked; schedule(true); void flush();
    });
    function load(data) {
        if (ready) return;
        confirmed = data.sidebar_auto_collapse ?? true;
        auto.checked = confirmed; auto.disabled = false; ready = true; schedule(true);
    }
    window.addEventListener('visual-settings-loaded', event => load(event.detail));
    if (window.visualSettingsSnapshot) load(window.visualSettingsSnapshot);
    updateLabel();
    syncPlayingFocus();
})();
