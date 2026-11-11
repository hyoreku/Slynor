// ПЛАВНОЕ ВРЕМЯ: сервер — источник истины, requestAnimationFrame — только отображение.
// Не изменяет позицию VLC и не отправляет перемотку при каждом кадре.
(() => {
    let anchor = null, frame = null, displayed = 0, previousFrame = 0, nativeHidden = false;
    function estimated(now) {
        if (!anchor) return 0;
        const elapsed = anchor.state === 'playing' ? Math.min(1500, Math.max(0, now - anchor.at)) : 0;
        return Math.min(anchor.duration, Math.max(0, anchor.time + elapsed * (anchor.rate || 1)));
    }
    function paint(now) {
        frame = null;
        if (!anchor || playerBar.hidden || document.hidden || nativeHidden) return;
        if (!isEditingSeek && !seekPending && !seekSlider.disabled) {
            const wanted = estimated(now);
            const elapsed = Math.min(60, Math.max(1, now - previousFrame)); previousFrame = now;
            displayed += (wanted - displayed) * (1 - Math.exp(-elapsed / 35));
            seekSlider.value = String(Math.round(displayed));
            currentTime.textContent = formatTime(displayed);
            const fill = anchor.duration ? displayed / anchor.duration * 100 : 0;
            seekSlider.style.setProperty('--range-fill', fill + '%');
        }
        if (anchor.state === 'playing' && performance.now() - anchor.at < 1600) start();
    }
    function start() {
        if (frame === null && anchor && !document.hidden && !nativeHidden) frame = requestAnimationFrame(paint);
    }
    window.playerMotion = {
        // Каждый ответ состояния обновляет опорное время. Большие скачки — перемотка/смена песни.
        state(data) {
            const now = performance.now();
            const position = Math.min(Math.max(0, data.time_ms || 0), data.duration_ms || 0);
            const jump = !anchor || fileKey(anchor.path) !== fileKey(data.file_path || '') || Math.abs(estimated(now) - position) > 450;
            anchor = { path: data.file_path || '', time: position, duration: data.duration_ms || 0, state: data.state, rate: data.rate || 1, at: now };
            if (jump || data.state !== 'playing') displayed = position;
            previousFrame = now;
            if (!isEditingSeek && !seekPending) {
                seekSlider.max = String(anchor.duration); seekSlider.value = String(Math.round(displayed));
                currentTime.textContent = formatTime(displayed);
            }
            start();
        },
        playback(state) {
            if (!anchor) return;
            const now = performance.now();
            anchor.time = estimated(now); anchor.at = now; anchor.state = state;
            displayed = anchor.time; start();
        },
        rate(rate) { if (anchor) { const now=performance.now(); anchor.time=estimated(now); anchor.at=now; anchor.rate=rate; start(); } },
        seek(position) {
            if (!anchor) return;
            anchor.time = position; anchor.at = performance.now(); displayed = position; start();
        },
        restored() { cancelAnimationFrame(frame); frame = null; start(); },
        stop() { anchor = null; cancelAnimationFrame(frame); frame = null; },
        connectionLost() { if (anchor) this.playback('unknown'); }
    };
    window.addEventListener('native-window-state', event => {
        nativeHidden=Boolean(event.detail?.minimized);
        if(nativeHidden){cancelAnimationFrame(frame);frame=null;}else start();
    });
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) { cancelAnimationFrame(frame); frame = null; }
        else start();
    });
    // Клавиатурная перемотка крупным шагом; мелкий step нужен для плавного рисования.
    seekSlider.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) || seekSlider.disabled) return;
        event.preventDefault();
        const direction = ['ArrowRight', 'ArrowUp'].includes(event.key) ? 1 : -1;
        seekSlider.value = String(Math.max(0, Math.min(Number(seekSlider.max), Number(seekSlider.value) + direction * 5000)));
        seekSlider.dispatchEvent(new Event('input')); seekSlider.dispatchEvent(new Event('change'));
    });
})();
