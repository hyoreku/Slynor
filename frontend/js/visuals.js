// ЭФФЕКТЫ: 1. Переключатели → 2. Цвета обложки → 3. Компоновка/полосы → 4. Настоящий спектр.
// Здесь нет случайных «музыкальных» анимаций: canvas получает только FFT с сервера.
(() => {
    const workspace = document.querySelector('.workspace');
    const atmosphere = document.getElementById('cover-atmosphere');
    const shell = document.querySelector('.app-shell');
    // One continuous surface, shared by content, navigation and transport.
    shell.prepend(atmosphere);
    const atmosphereImage = atmosphere.querySelector('.atmosphere-image');
    const blobs=Array.from({length:3},()=>{const b=document.createElement('span');b.className='atmosphere-blob';atmosphere.querySelector('.atmosphere-colors').append(b);return b;});
    let drift=[], sidebarResizing=false;
    function syncDrift(){
        const visible=coverEnabled && !atmosphere.hidden && activeScreen==='playing';
        shell.classList.toggle('cover-scene',visible);
        const enabled=visible && !document.hidden && !nativeMinimized && !reducedMotion.matches && !sidebarResizing;
        // Pause preserves the phase. Never re-roll the points on audio pause,
        // a routine state poll, resize or a trip to the mini window.
        if(!enabled){drift.forEach(a=>a.pause());return;}
        if(drift.length){drift.forEach(a=>{if(a.playState==='paused')a.play();});return;}
        drift=blobs.map((b,i)=>{
            // Closed smooth irregular paths: subsecond keyframes, compositor-only transforms.
            const phase=Array.from({length:4},()=>Math.random()*Math.PI*2);
            const frames=Array.from({length:49},(_,step)=>{
                const t=step/48*Math.PI*2;
                const x=Math.sin(t+phase[0])*22+Math.sin(t*2+phase[1])*8;
                const y=Math.cos(t+phase[1])*19+Math.sin(t*3+phase[2])*6;
                const sx=1.04+Math.sin(t*2+phase[2])*.13,sy=1.04+Math.cos(t+phase[3])*.12;
                return {transform:`translate(${x}%,${y}%) scale(${sx},${sy}) rotate(${Math.sin(t+phase[3])*35}deg)`};
            });
            return b.animate(frames,{duration:20000+i*1700,iterations:Infinity,easing:'linear'});
        });
    }
    const canvas = document.getElementById('spectrum-canvas');
    const context = canvas.getContext('2d');
    const status = document.getElementById('spectrum-status');
    const coverToggle = document.getElementById('cover-background-toggle');
    const spectrumToggle = document.getElementById('spectrum-toggle');
    const saveButton = document.getElementById('save-visual-effects');
    const message = document.getElementById('visual-effects-message');
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    // Обычные подтверждения не занимают место на экране песни; ошибки остаются видны.
    const commandResult = document.getElementById('result');
    new MutationObserver(() => commandResult.classList.toggle('is-error',
        /ошиб|не удалось|недоступ|не найден|error|failed|unavailable|not found|cannot/i.test(commandResult.textContent)))
        .observe(commandResult, { childList: true, characterData: true, subtree: true });
    const BANDS = 48;
    let coverEnabled = true, spectrumEnabled = false;
    let savedCover = true, savedSpectrum = false;
    let nativeMinimized = false;
    let trackPath = null, coverUrl = '', visualState = 'idle';
    let coverRevision = 0;
    const paletteCache = new Map();

    // 1. Переключатели независимы от обоев. Пока не сохранены — это предпросмотр.
    function loadSettings(data) {
        const cover = data.cover_background ?? true, spectrum = data.spectrum_enabled ?? false;
        if (typeof cover !== 'boolean' || typeof spectrum !== 'boolean') {
            message.textContent = 'Некорректные настройки эффектов. Файл не перезаписывается.';
            return;
        }
        savedCover = coverEnabled = cover;
        savedSpectrum = spectrumEnabled = spectrum;
        coverToggle.checked = cover; spectrumToggle.checked = spectrum;
        message.textContent = 'Эффекты загружены. Выбор сохраняется после перезапуска.';
        updateControls(); updateAtmosphere(); syncSpectrum();
    }
    function updateControls() {
        const disabled = !settingsReady || settingsSaving;
        coverToggle.disabled = spectrumToggle.disabled = disabled;
        saveButton.disabled = disabled || (coverEnabled === savedCover && spectrumEnabled === savedSpectrum);
        updateRanges();
    }
    [coverToggle, spectrumToggle].forEach(control => control.addEventListener('change', () => {
        coverEnabled = coverToggle.checked; spectrumEnabled = spectrumToggle.checked;
        message.textContent = 'Предпросмотр. Нажмите «Сохранить эффекты экрана», чтобы запомнить выбор.';
        updateControls(); updateAtmosphere(); syncSpectrum();
    }));
    saveButton.addEventListener('click', async () => {
        if (!settingsReady || settingsSaving) return;
        settingsSaving = true; updateSettingsControls();
        try {
            const data = await window.pywebview.api.save_visual_effects(coverEnabled, spectrumEnabled);
            savedCover = data.cover_background; savedSpectrum = data.spectrum_enabled;
            message.textContent = 'Эффекты экрана сохранены.';
        } catch (error) { message.textContent = 'Не удалось сохранить эффекты: ' + error.message; }
        finally { settingsSaving = false; updateSettingsControls(); }
    });
    window.addEventListener('visual-settings-loaded', event => loadSettings(event.detail));

    // 2. Простое извлечение доминантных цветов: маленькая копия и частые RGB-кластеры.
    // Считывается лишь наша same-origin обложка, не музыкальный файл и не чужой URL.
    function extractPalette(image) {
        const sample = document.createElement('canvas'); sample.width = sample.height = 64;
        const ctx = sample.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(image, 0, 0, 64, 64);
        const pixels = ctx.getImageData(0, 0, 64, 64).data;
        const buckets = new Map();
        for (let i = 0; i < pixels.length; i += 4) {
            if (pixels[i + 3] < 160) continue;
            const rgb = [pixels[i], pixels[i + 1], pixels[i + 2]];
            const max = Math.max(...rgb), min = Math.min(...rgb);
            if (max < 22 || min > 238) continue;
            const key = rgb.map(value => value >> 4).join(',');
            const item = buckets.get(key) || { count: 0, sum: [0, 0, 0] };
            item.count++; rgb.forEach((value, channel) => { item.sum[channel] += value; });
            buckets.set(key, item);
        }
        const clusters = [...buckets.values()].sort((a, b) => b.count - a.count);
        const colors = [];
        for (const cluster of clusters) {
            const color = cluster.sum.map(value => Math.round(value / cluster.count));
            if (colors.every(previous => color.reduce((sum, value, i) => sum + (value - previous[i]) ** 2, 0) > 2500)) colors.push(color);
            if (colors.length === 3) break;
        }
        if (!colors.length) colors.push([38, 38, 43]); // Почти чёрно-белая обложка, нейтральный фон.
        while (colors.length < 3) colors.push(colors[0]);
        return colors;
    }
    function applyGlow(colors) {
        const cover = document.getElementById('large-track-cover');
        const saturated = colors.filter(rgb => Math.max(...rgb)-Math.min(...rgb)>25 && Math.max(...rgb)>60).sort((a,b)=>(Math.max(...b)-Math.min(...b))-(Math.max(...a)-Math.min(...a)));
        const primary=saturated[0] || colors[0], secondary=saturated[1] || primary;
        cover.style.setProperty('--art-glow-1', primary.join(','));
        cover.style.setProperty('--art-glow-2', secondary.join(','));
        cover.classList.add('has-glow');
    }
    function applyAtmosphere(url, colors) {
        applyGlow(colors);
        atmosphereImage.style.backgroundImage = url ? `url("${url}")` : "none";
        colors.forEach((rgb, i) => atmosphere.style.setProperty('--cover-color-' + (i + 1), rgb.join(',')));
        atmosphere.hidden = !coverEnabled; syncDrift();
    }
    function updateAtmosphere() {
        const revision = ++coverRevision;
        atmosphere.hidden = true; syncDrift();
        const url = safeTrackCoverURL(coverUrl);
        document.getElementById('large-track-cover').classList.remove('has-glow');
        if (activeScreen !== 'playing') return;
        const accent=(typeof currentAccent==='string'?currentAccent:'#c9c9d1').slice(1).match(/../g).map(x=>parseInt(x,16));
        const fallback=()=>applyAtmosphere('',[accent,accent.map(x=>Math.round(x*.65)),accent.map(x=>Math.min(255,Math.round(x*.45+30)))]);
        if (!url) {fallback();return;}
        const cached = paletteCache.get(url);
        if (cached) { applyAtmosphere(url, cached); return; }
        const image = new Image(); image.crossOrigin='anonymous';
        image.onload = () => {
            if (revision !== coverRevision || activeScreen !== 'playing') return;
            try {
                const colors = extractPalette(image);
                if (paletteCache.size >= 24) paletteCache.delete(paletteCache.keys().next().value);
                paletteCache.set(url, colors); applyAtmosphere(url, colors);
            } catch (_) { fallback(); }
        };
        image.onerror = () => { if (revision === coverRevision) fallback(); };
        image.src = url;
    }

    // 3. В «Сейчас играет» центр не прокручивается: обложка учитывает доступную высоту.
    const presentation = document.querySelector('.playing-presentation');
    function fitPresentation() {
        if (activeScreen !== 'playing' || document.documentElement.classList.contains('sidebar-resizing')) return;
        const labels = ['large-track-title', 'large-track-artist', 'large-track-state']
            .reduce((sum, id) => sum + document.getElementById(id).offsetHeight, 0);
        const compact = innerHeight <= 600;
        const size = compact ? Math.min(96, Math.max(48, presentation.clientHeight))
            : Math.max(48, Math.min(340, presentation.clientWidth, presentation.clientHeight - labels - 48));
        const value = Math.floor(size) + 'px';
        if (presentation.style.getPropertyValue('--fitted-cover') !== value) presentation.style.setProperty('--fitted-cover', value);
        window.equalizerPanel?.layout();
    }
    new ResizeObserver(fitPresentation).observe(presentation);
    function updateRanges() {
        for (const slider of document.querySelectorAll("input[type=range]")) {
            const min = Number(slider.min), max = Number(slider.max);
            const percent = max > min ? Math.max(0, Math.min(100, (Number(slider.value) - min) / (max - min) * 100)) : 0;
            slider.style.setProperty('--range-fill', percent + '%');
        }
    }
    [...document.querySelectorAll("input[type=range]")].forEach(slider => {
        slider.addEventListener('input', updateRanges);
        slider.addEventListener('change', updateRanges);
        slider.addEventListener('pointerdown', () => slider.classList.add('is-interacting'));
        slider.addEventListener('pointercancel', () => slider.classList.remove('is-interacting'));
        slider.addEventListener('blur', () => slider.classList.remove('is-interacting'));
    });
    document.addEventListener('pointerup', () => { seekSlider.classList.remove('is-interacting'); volumeSlider.classList.remove('is-interacting'); });
    seekSlider.addEventListener('change', () => { target.fill(0); values.fill(0); invalidatePoll(); syncSpectrum(); });

    // 4. Настоящий спектр: примерно до 25 запросов/с, один незавершённый запрос максимум.
    // Плавность — интерполяция полученных частот, а не генерация случайных уровней.
    let pollTimer = null, pollController = null, pollGeneration = 0;
    let raf = null, lastFrame = 0, lastDraw = 0, packetAt = 0;
    let chartColor = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
    const previousTarget = new Float32Array(BANDS);
    const values = new Float32Array(BANDS), target = new Float32Array(BANDS);
    function activeSpectrum() { return spectrumEnabled && activeScreen === 'playing' && Boolean(currentTrackPath) && !document.hidden && !nativeMinimized; }
    function invalidatePoll() {
        pollGeneration++;
        clearTimeout(pollTimer); pollTimer = null;
        pollController?.abort(); pollController = null;
    }
    function clearSpectrum() {
        invalidatePoll(); target.fill(0); values.fill(0);
        cancelAnimationFrame(raf); raf = null;
        context.clearRect(0, 0, canvas.width, canvas.height);
    }
    function syncSpectrum() {
        if (!activeSpectrum()) { clearSpectrum(); canvas.hidden = status.hidden = true; return; }
        canvas.hidden = status.hidden = false;
        if (visualState !== 'playing') {
            invalidatePoll(); target.fill(0); status.textContent = visualState === 'paused' ? 'Спектр на паузе' : '';
            animate(); return;
        }
        if (pollTimer === null && pollController === null) void pollSpectrum();
    }
    async function pollSpectrum() {
        if (!activeSpectrum() || visualState !== 'playing' || pollController) return;
        const generation = pollGeneration, path = currentTrackPath;
        const controller = new AbortController(); pollController = controller;
        const timeout = setTimeout(() => controller.abort(), 1200);
        let delay = 30;
        try {
            const response = await fetch('/api/player/spectrum', { cache: 'no-store', signal: controller.signal });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const data = await response.json();
            if (generation !== pollGeneration || !activeSpectrum() || !data.file_path || fileKey(data.file_path) !== fileKey(path) || fileKey(currentTrackPath) !== fileKey(path)) return;
            const ready = data.status === 'ready' && data.state === 'playing' && !seekPending;
            if (ready && Array.isArray(data.bands) && data.bands.length === BANDS && data.bands.every(value => Number.isFinite(value) && value >= 0 && value <= 1)) {
                previousTarget.set(values); target.set(data.bands); packetAt = performance.now();
                status.textContent = 'После EQ · 50 Гц — 20 кГц';
            } else {
                if (data.status !== 'preparing' || performance.now() - packetAt > 350) target.fill(0);
                status.textContent = data.message || (data.status === 'preparing' ? 'Подготовка спектра…' : data.state === 'paused' ? 'Спектр на паузе' : '');
                if (['error', 'unavailable'].includes(data.status)) delay = 2000;
            }
            animate();
        } catch (error) {
            if (generation === pollGeneration && activeSpectrum()) {
                target.fill(0); status.textContent = 'Нет данных спектра. Воспроизведение не затронуто.'; delay = 1500; animate();
            }
        } finally {
            clearTimeout(timeout);
            if (pollController === controller) pollController = null;
            if (generation === pollGeneration && activeSpectrum() && visualState === 'playing') pollTimer = setTimeout(() => { pollTimer = null; void pollSpectrum(); }, delay);
        }
    }
    function animate() { if (raf === null && activeSpectrum()) { lastFrame = performance.now(); raf = requestAnimationFrame(draw); } }
    function draw(now) {
        raf = null;
        if (!activeSpectrum()) return;
        const elapsed = Math.min(100, now - lastFrame); lastFrame = now;
        let moving = false;
        for (let i = 0; i < BANDS; i++) {
            const blend = reducedMotion.matches ? 1 : Math.min(1, (now - packetAt) / 55);
            const interpolated = previousTarget[i] + (target[i] - previousTarget[i]) * blend;
            const factor = reducedMotion.matches ? 1 : 1 - Math.exp(-elapsed / (interpolated > values[i] ? 40 : 120));
            values[i] += (interpolated - values[i]) * factor;
            if (values[i] < .001) values[i] = 0;
            moving ||= Math.abs(values[i] - target[i]) > .001;
        }
        // Масштаб canvas ограничен 2x для экранов с высоким DPI.
        const dpr = Math.min(2, devicePixelRatio || 1), resizing=document.documentElement.classList.contains('sidebar-resizing');
        const width = resizing&&canvas.width ? canvas.width/dpr : workspace.clientWidth, height = resizing&&canvas.height ? canvas.height/dpr : workspace.clientHeight;
        if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
            canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
        }
        if (!reducedMotion.matches || now - lastDraw >= 90 || !moving) {
            lastDraw = now; context.setTransform(dpr, 0, 0, dpr, 0, 0); context.clearRect(0, 0, width, height);

            const chartWidth = Math.max(1, Math.min(1100, width - 32)), step = chartWidth / BANDS;
            const eqPanel = document.getElementById('eq-panel');
            const baseline = eqPanel && !eqPanel.hidden ? Math.max(0, Math.min(height - 4, eqPanel.getBoundingClientRect().top - workspace.getBoundingClientRect().top - 4)) : height - 4;
            const maximum = Math.min(150, height * .24, baseline); 
            context.fillStyle = chartColor; context.globalAlpha = .24;
            for (let i = 0; i < BANDS; i++) {
                const bar = values[i] * maximum;
                if (bar > .1) context.fillRect((width - chartWidth) / 2 + i * step, baseline - bar, Math.max(1, step - 4), bar);
            }
            context.globalAlpha = 1;
        }
        if (moving || visualState === 'playing') raf = requestAnimationFrame(draw); // Непрерывные кадры ~60 FPS; на паузе цикл останавливается после спада.
    }

    function screenChanged(name) {
        workspace.classList.toggle('playing-mode', name === 'playing');
        updateAtmosphere(); fitPresentation(); syncSpectrum(); syncDrift();
    }
    function trackChanged(meta, state) {
        const changed = trackPath !== currentTrackPath;
        if (changed) { trackPath = currentTrackPath; clearSpectrum(); }
        visualState = state;
        const url = meta?.cover_url || '';
        if (url !== coverUrl) { coverUrl = url; updateAtmosphere(); }
        fitPresentation(); syncSpectrum(); syncDrift();
    }
    window.playerVisuals = {
        screenChanged, trackChanged, updateControls, updateRanges,
        playbackChanged(state) { visualState = state; syncSpectrum(); syncDrift(); },
        stop() { visualState = 'idle'; trackPath = null; clearSpectrum(); atmosphere.hidden = true; coverRevision++; syncDrift(); },
        connectionLost() { target.fill(0); animate(); },
        themeChanged() { chartColor = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim(); updateRanges(); if (activeSpectrum()) animate(); }
    };
    for (const event of ['resize', 'sidebar-layout-changed']) {
        window.addEventListener(event, () => { fitPresentation(); if (activeSpectrum()) animate(); });
    }
    function lifecycle(hidden) {
        nativeMinimized = hidden; syncDrift();
        // Cancel stale RAF/request handles even if Chromium froze the previous frame.
        clearSpectrum(); fitPresentation();
        if (hidden) {
            void fetch('/api/player/spectrum/suspend',{method:'POST',signal:AbortSignal.timeout(1000)}).catch(()=>{});
        } else {
            visualState = currentPlaybackState;
            window.refreshPlayerState?.(); syncSpectrum(); animate();
            window.playerMotion?.restored();
        }
    }
    window.addEventListener('native-window-state', event => lifecycle(Boolean(event.detail?.minimized)));
    // Pause the large blurred surfaces while the grid changes width. Preserve
    // animation positions instead of restarting the background after each click.
    window.addEventListener('sidebar-animation-start',()=>{sidebarResizing=true;syncDrift();});
    window.addEventListener('sidebar-animation-end',()=>{sidebarResizing=false;syncDrift();});
    document.addEventListener('visibilitychange', () => lifecycle(document.hidden));
    window.addEventListener('pageshow', () => { if (!document.hidden) lifecycle(false); });
    window.addEventListener('focus', () => { if (!document.hidden) lifecycle(false); });
    reducedMotion.addEventListener('change', () => {syncDrift(); if (activeSpectrum()) animate(); });
    if (window.visualSettingsSnapshot) loadSettings(window.visualSettingsSnapshot);
    updateControls(); updateRanges(); screenChanged(activeScreen);
})();
