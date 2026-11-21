(() => {
    const root = document.documentElement;
    const panel = document.getElementById('mini-panel');
    const unfold = document.getElementById('mini-unfold');
    const compact = matchMedia('(max-width: 180px)');
    let wanted = compact.matches;
    let resizing = false;
    let timer;
    let keyboard = false;
    let focusControls = false;
    let mouseInside = false;
    let adjustingVolume = false;

    async function resize(next) {
        const api = window.pywebview?.api;
        if (!api?.set_mini_compact || document.hidden) return;
        wanted = next;
        if (resizing) return;

        resizing = true;
        try {
            let requested;
            do {
                requested = wanted;
                if (requested === compact.matches) break;
                if (!await api.set_mini_compact(requested)) {
                    wanted = compact.matches;
                    break;
                }
            } while (requested !== wanted);
        } catch (error) {
            wanted = compact.matches;
            console.warn('Could not resize the mini-player', error);
        } finally {
            resizing = false;
        }
    }

    function scheduleCollapse() {
        clearTimeout(timer);
        if (compact.matches || document.hidden || panel.matches(':hover') || adjustingVolume
                || (keyboard && panel.contains(document.activeElement))) return;
        timer = setTimeout(() => void resize(true), 3000);
    }

    function syncLayout() {
        root.dataset.miniCompact = String(compact.matches);
        unfold.hidden = !compact.matches;
        document.dispatchEvent(new Event('mini-layout-change'));
        if (compact.matches && mouseInside) void resize(false);
        if (!compact.matches && focusControls) {
            focusControls = false;
            document.getElementById('mini-hide').focus({preventScroll: true});
        }
        scheduleCollapse();
    }

    panel.addEventListener('pointerenter', event => {
        clearTimeout(timer);
        mouseInside = event.pointerType === 'mouse';
        if (mouseInside && (compact.matches || resizing)) void resize(false);
    });
    panel.addEventListener('pointerleave', () => {
        mouseInside = false;
        scheduleCollapse();
    });
    panel.addEventListener('pointerdown', event => {
        keyboard = false;
        adjustingVolume = event.target.id === 'mini-volume';
    });
    function endVolumeAdjustment() {
        if (!adjustingVolume) return;
        adjustingVolume = false;
        scheduleCollapse();
    }
    document.addEventListener('pointerup', endVolumeAdjustment);
    document.addEventListener('pointercancel', endVolumeAdjustment);
    panel.addEventListener('keydown', () => { keyboard = true; clearTimeout(timer); });
    panel.addEventListener('focusin', () => { if (keyboard) clearTimeout(timer); });
    panel.addEventListener('focusout', () => setTimeout(scheduleCollapse, 0));
    unfold.addEventListener('click', event => {
        focusControls = keyboard = event.detail === 0;
        void resize(false);
    });
    compact.addEventListener('change', syncLayout);
    addEventListener('pywebviewready', scheduleCollapse);
    addEventListener('focus', scheduleCollapse);
    addEventListener('blur', endVolumeAdjustment);
    document.addEventListener('visibilitychange', scheduleCollapse);
    syncLayout();
})();
