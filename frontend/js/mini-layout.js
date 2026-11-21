(() => {
    const root = document.documentElement;
    const panel = document.getElementById('mini-panel');
    const unfold = document.getElementById('mini-unfold');
    const compact = matchMedia('(max-width: 180px)');
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    const idleDelay = 15000;
    let wanted = compact.matches;
    let resizing = false;
    let timer;
    let keyboard = false;
    let focusControls = false;
    let adjustingVolume = false;

    async function resize(next) {
        const api = window.pywebview?.api;
        if (!api?.set_mini_compact || document.hidden || next === wanted) return;
        wanted = next;
        syncLayout();
        if (resizing) return;

        resizing = true;
        try {
            let requested;
            do {
                requested = wanted;
                if (!await api.set_mini_compact(requested, !reducedMotion.matches)) {
                    wanted = compact.matches;
                    syncLayout();
                    break;
                }
            } while (requested !== wanted);
        } catch (error) {
            wanted = compact.matches;
            syncLayout();
            console.warn('Could not resize the mini-player', error);
        } finally {
            resizing = false;
        }
    }

    function scheduleCollapse() {
        clearTimeout(timer);
        if (wanted || document.hidden || panel.matches(':hover') || adjustingVolume
                || (keyboard && panel.contains(document.activeElement))) return;
        timer = setTimeout(() => void resize(true), idleDelay);
    }

    function syncLayout() {
        root.dataset.miniCompact = String(wanted);
        unfold.hidden = !wanted;
        document.dispatchEvent(new Event('mini-layout-change'));
        if (!wanted && focusControls) {
            focusControls = false;
            document.getElementById('mini-hide').focus({preventScroll: true});
        }
        scheduleCollapse();
    }

    panel.addEventListener('pointerenter', event => {
        clearTimeout(timer);
        if (event.pointerType === 'mouse' && wanted) void resize(false);
    });
    panel.addEventListener('pointerleave', () => {
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
    addEventListener('pywebviewready', scheduleCollapse);
    addEventListener('focus', scheduleCollapse);
    addEventListener('blur', endVolumeAdjustment);
    document.addEventListener('visibilitychange', scheduleCollapse);
    syncLayout();
})();
