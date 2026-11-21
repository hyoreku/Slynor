// ЭФФЕКТЫ: 1. Переключатели → 2. Цвета обложки → 3. Компоновка/полосы → 4. Настоящий спектр.
// Здесь нет случайных «музыкальных» анимаций: canvas получает только FFT с сервера.
(() => {
    const workspace = document.querySelector('.workspace');
    const atmosphere = document.getElementById('cover-atmosphere');
    const artwork = document.getElementById('large-track-cover');
    const shell = document.querySelector('.app-shell');
    // One continuous surface, shared by content, navigation and transport.
    shell.prepend(atmosphere);
    const atmosphereImage = atmosphere.querySelector('.atmosphere-image');
    const atmosphereColors = atmosphere.querySelector('.atmosphere-colors');
    const liquidBackground = window.createLiquidBackground(atmosphere, atmosphere.querySelector('.atmosphere-shade'));
    const blobs = Array.from({ length: 3 }, () => {
        const blob = document.createElement('span');
        blob.className = 'atmosphere-blob';
        atmosphereColors.append(blob);
        return blob;
    });
    let drift = [], sidebarResizing = false;

    function startDrift(blob, index) {
        // Transform the painted layers. Their soft edges and colors stay fixed between frames.
        const duration = 7200 + index * 1300;
        const phase = index * Math.PI * 2 / blobs.length;
        const frames = Array.from({ length: 36 }, (_, step) => {
            const t = step / 36 * Math.PI * 2 + phase;
            const x = Math.cos(t) * 49 + Math.sin(t * 2) * 9;
            const y = Math.sin(t) * 36 + Math.cos(t * 2) * 8;
            const tilt = Math.sin(t + phase) * 22;
            const width = 1 + Math.cos(t * 2) * .16;
            const height = 1 + Math.sin(t * 2) * .12;
            return { transform: `translate(${x}%, ${y}%) rotate(${tilt}deg) scale(${width}, ${height})` };
        });
        frames.push(frames[0]);
        return blob.animate(frames, { duration, iterations: Infinity });
    }

    function syncDrift() {
        const visible = coverEnabled && !atmosphere.hidden && activeScreen === 'playing';
        shell.classList.toggle('cover-scene', visible);
        const moving = visible && !document.hidden && !nativeMinimized && !sidebarResizing && !reducedMotion.matches;
        const liquid = coverBackgroundStyle === 'liquid';
        liquidBackground.setActive(visible && liquid, moving);
        if (reducedMotion.matches) {
            drift.forEach(animation => animation.cancel());
            drift = [];
            return;
        }
        const enabled = moving && !liquid;
        if (enabled && drift.length === 0) drift = blobs.map(startDrift);
        for (const animation of drift) {
            if (enabled && animation.playState === 'paused') animation.play();
            if (!enabled && animation.playState === 'running') animation.pause();
        }
    }
    const canvas = document.getElementById('spectrum-canvas');
    const wave = document.getElementById('now-wave');
    const context = canvas.getContext('2d');
    const compactCanvas = document.getElementById('player-spectrum');
    const compactContext = compactCanvas.getContext('2d');
    const compactViewport = matchMedia('(min-width: 900px)');
    const radialCanvas = document.getElementById('now-radial-spectrum');
    const coverVisualizer = window.createCoverVisualizer(radialCanvas, artwork);
    const status = document.getElementById('spectrum-status');
    const coverToggle = document.getElementById('cover-background-toggle');
    const liquidToggle = document.getElementById('tester-cover-liquid');
    const experimentActive = document.getElementById('cover-experiment-active');
    const visualizerSelect = document.getElementById('visualizer-mode');
    const message = document.getElementById('visual-effects-message');
    const experimentMessage = document.getElementById('liquid-cover-message');
    const writer = createSettingsWriter(
        ({ cover, style, mode }) => window.pywebview.api.save_visual_effects(cover, mode !== 'off', mode, style),
        applySettings
    );
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    // Обычные подтверждения не занимают место на экране песни; ошибки остаются видны.
    const commandResult = document.getElementById('result');
    new MutationObserver(() => commandResult.classList.toggle('is-error',
        /ошиб|не удалось|недоступ|не найден|error|failed|unavailable|not found|cannot/i.test(commandResult.textContent)))
        .observe(commandResult, { childList: true, characterData: true, subtree: true });
    const BANDS = 48;
    const DRAWN_BARS = 24;
    let coverEnabled = true, coverBackgroundStyle = 'soft', visualizerMode = 'off';
    let nativeMinimized = false;
    let trackPath = null, coverUrl = '', visualState = 'idle';
    let coverRevision = 0;
    let entryReveal = null;
    const coverCache = new Map();
    const pendingCovers = new Map();
    const neutralCover = { colors: [[77, 79, 91], [61, 64, 74], [45, 47, 58]], texture: '' };

    // 1. Persist the experimental choice while constraining its active use to tester mode.
    function loadSettings(data) {
        const cover = data.cover_background ?? true;
        const style = data.cover_background_style ?? 'soft';
        const mode = data.visualizer_mode ?? (data.spectrum_enabled ? 'bars' : 'off');
        const settings = { cover, style, mode };
        writer.setSaved(settings);
        applySettings(settings);
    }
    function applySettings({ cover, style, mode }) {
        coverToggle.checked = cover;
        liquidToggle.checked = style === 'liquid';
        visualizerSelect.value = mode;
        visualizerMode = mode;
        window.publishAppearance?.();
        syncBackgroundStyle();
        syncSpectrum();
    }
    function syncBackgroundStyle() {
        const liquid = Boolean(window.testerModeEnabled && liquidToggle.checked);
        const style = liquid ? 'liquid' : 'soft';
        const enabled = liquid || coverToggle.checked;
        const changed = style !== coverBackgroundStyle || enabled !== coverEnabled;
        coverBackgroundStyle = style;
        coverEnabled = enabled;
        experimentActive.hidden = !liquid;
        if (changed) updateAtmosphere();
        updateControls();
    }
    function updateControls() {
        coverToggle.disabled = visualizerSelect.disabled = !settingsReady;
        liquidToggle.disabled = !settingsReady || !window.testerModeEnabled;
        updateRanges();
    }
    for (const control of [coverToggle, liquidToggle, visualizerSelect]) {
        control.addEventListener('change', () => {
            const settings = { cover: coverToggle.checked, style: liquidToggle.checked ? 'liquid' : 'soft', mode: visualizerSelect.value };
            applySettings(settings);
            message.textContent = experimentMessage.textContent = '';
            writer.save(settings, control === liquidToggle ? experimentMessage : message);
        });
    }
    window.addEventListener('tester-mode-changed', () => syncBackgroundStyle());
    window.addEventListener('visual-settings-loaded', event => loadSettings(event.detail));

    // 2. Простое извлечение доминантных цветов: маленькая копия и частые RGB-кластеры.
    // Читаем пиксели обложки, если браузер разрешил доступ к её изображению.
    function extractPalette(pixels) {
        const buckets = new Map();
        for (let i = 0; i < pixels.length; i += 4) {
            if (pixels[i + 3] < 160) continue;
            const rgb = [pixels[i], pixels[i + 1], pixels[i + 2]];
            const max = Math.max(...rgb), min = Math.min(...rgb);
            if (max < 22 || min > 238 || (min > 220 && max - min < 25)) continue;
            const key = rgb.map(value => value >> 4).join(',');
            const item = buckets.get(key) || { count: 0, sum: [0, 0, 0] };
            item.count++; rgb.forEach((value, channel) => { item.sum[channel] += value; });
            buckets.set(key, item);
        }
        const clusters = [...buckets.values()].map(({ count, sum }) => {
            const color = sum.map(value => Math.round(value / count));
            const chroma = Math.max(...color) - Math.min(...color);
            return { color, weight: count * (1 + chroma / 48) };
        }).sort((a, b) => b.weight - a.weight);
        const colors = [];
        for (const { color } of clusters) {
            if (colors.every(previous => color.reduce((sum, value, i) => sum + (value - previous[i]) ** 2, 0) > 2500)) colors.push(color);
            if (colors.length === 3) break;
        }
        if (!colors.length) colors.push([38, 38, 43]); // Почти чёрно-белая обложка, нейтральный фон.
        while (colors.length < 3) colors.push(colors[0]);
        return colors;
    }
    function readCover(image) {
        const sample = document.createElement('canvas');
        sample.width = sample.height = 64;
        const context = sample.getContext('2d', { willReadFrequently: true });
        context.drawImage(image, 0, 0, sample.width, sample.height);
        const colors = extractPalette(context.getImageData(0, 0, sample.width, sample.height).data);
        return { colors, texture: createCoverTexture(sample) };
    }
    function createCoverTexture(sample) {
        // Blur a small bitmap once. The full-window layer only scales the cached texture.
        const texture = document.createElement('canvas');
        texture.width = texture.height = 48;
        const context = texture.getContext('2d');
        if (!context) return '';
        const margin = 16;
        context.filter = 'blur(6px) saturate(1.2)';
        context.drawImage(sample, -margin, -margin, texture.width + margin * 2, texture.height + margin * 2);
        try { return texture.toDataURL('image/png'); }
        catch (_) { return ''; }
    }
    function applyGlow(colors) {
        const saturated = colors.filter(rgb => Math.max(...rgb)-Math.min(...rgb)>25 && Math.max(...rgb)>60).sort((a,b)=>(Math.max(...b)-Math.min(...b))-(Math.max(...a)-Math.min(...a)));
        const primary=saturated[0] || colors[0], secondary=saturated[1] || primary;
        artwork.style.setProperty('--art-glow-1', primary.join(','));
        artwork.style.setProperty('--art-glow-2', secondary.join(','));
        artwork.classList.add('has-glow');
    }
    function applyAtmosphere(url, { colors, texture }) {
        if (url) applyGlow(colors);
        else artwork.classList.remove('has-glow');
        atmosphere.classList.toggle('has-cover-palette', Boolean(url));
        atmosphereImage.style.backgroundImage = texture ? `url("${texture}")` : 'none';
        colors.forEach((rgb, i) => atmosphere.style.setProperty('--cover-color-' + (i + 1), rgb.join(',')));
        if (coverBackgroundStyle === 'liquid') liquidBackground.setPalette(colors);
        atmosphere.hidden = !coverEnabled; syncDrift();
    }
    function hideAtmosphere() {
        atmosphere.hidden = true;
        artwork.classList.remove('has-glow');
        syncDrift();
    }
    function prepareCover(url) {
        const pending = pendingCovers.get(url);
        if (pending) return pending;
        const ready = new Promise(resolve => {
            const image = new Image();
            image.crossOrigin = 'anonymous';
            image.onload = () => {
                if (!coverEnabled || coverUrl !== url) { resolve(null); return; }
                try {
                    const cover = readCover(image);
                    if (coverCache.size >= 24) coverCache.delete(coverCache.keys().next().value);
                    coverCache.set(url, cover);
                    resolve(cover);
                } catch (_) { resolve(null); }
            };
            image.onerror = () => resolve(null);
            image.src = url;
        });
        pendingCovers.set(url, ready);
        void ready.then(() => pendingCovers.delete(url));
        return ready;
    }
    function updateAtmosphere() {
        const revision = ++coverRevision;
        atmosphere.dataset.coverStyle = coverBackgroundStyle;
        const visible = coverEnabled && activeScreen === 'playing';
        if (!visible) hideAtmosphere();
        if (!coverEnabled) return;
        const url = coverUrl;
        const cached = coverCache.get(url);
        if (visible && cached) applyAtmosphere(url, cached);
        else if (visible && (!url || atmosphere.hidden)) applyAtmosphere('', neutralCover);
        if (!url || cached) return;
        const ready = prepareCover(url);
        // Outside Now Playing, warm the cache without changing the visible screen.
        if (!visible) return;
        void ready.then(cover => {
            if (revision !== coverRevision) return;
            applyAtmosphere(cover ? url : '', cover ?? neutralCover);
        });
    }

    // 3. Размер обложки задаёт CSS, canvas занимает только отведённое ему место.
    const spectrumResize = new ResizeObserver(() => {
        coverVisualizer.invalidateGeometry();
        animate(); window.equalizerPanel?.layout();
    });
    spectrumResize.observe(wave);
    spectrumResize.observe(radialCanvas);
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
    let chartFill;
    let chartHeight = 0;
    let spectrumColors;
    function syncVisualAccent() {
        const style = getComputedStyle(document.documentElement);
        spectrumColors = [style.getPropertyValue('--accent-strong').trim(), style.getPropertyValue('--accent').trim()];
        coverVisualizer.setColors(spectrumColors);
        chartHeight = 0;
    }
    const previousTarget = new Float32Array(BANDS);
    const values = new Float32Array(BANDS), target = new Float32Array(BANDS);
    function fullscreenSpectrum() { return visualizerMode !== 'off' && activeScreen === 'playing' && Boolean(currentTrackPath); }
    function compactSpectrum() {
        return visualizerMode !== 'off' && activeScreen !== 'playing' && compactViewport.matches &&
            Boolean(currentTrackPath && !currentTrackPath.startsWith('online:'));
    }
    function activeSpectrum() {
        return (fullscreenSpectrum() || compactSpectrum()) && !entryReveal && !document.hidden && !nativeMinimized;
    }
    function invalidatePoll() {
        pollGeneration++;
        clearTimeout(pollTimer); pollTimer = null;
        pollController?.abort(); pollController = null;
    }
    function clearSpectrum() {
        invalidatePoll(); target.fill(0); values.fill(0);
        previousTarget.fill(0); packetAt = 0;
        cancelAnimationFrame(raf); raf = null;
        context.resetTransform();
        context.clearRect(0, 0, canvas.width, canvas.height);
        compactContext.clearRect(0, 0, compactCanvas.width, compactCanvas.height);
        coverVisualizer.clear();
    }
    function syncSpectrum() {
        const visible = fullscreenSpectrum();
        status.hidden = !visible;
        wave.hidden = !visible || visualizerMode !== 'bars';
        radialCanvas.hidden = !visible || visualizerMode === 'bars';
        compactCanvas.hidden = !compactSpectrum();
        radialCanvas.dataset.mode = visualizerMode;
        if (entryReveal && visible) {
            status.textContent = visualState === 'paused' ? 'Спектр на паузе' : 'Подготовка спектра…';
            return;
        }
        if (!activeSpectrum()) { clearSpectrum(); return; }
        if (visualState !== 'playing') {
            invalidatePoll();
            previousTarget.fill(0); target.fill(0); packetAt = 0;
            status.textContent = visualState === 'paused' ? 'Спектр на паузе' : '';
            animate(); return;
        }
        if (pollTimer === null && pollController === null) void pollSpectrum();
    }
    async function pollSpectrum() {
        if (!activeSpectrum() || visualState !== 'playing' || pollController) return;
        const generation = pollGeneration, path = currentTrackPath;
        const controller = new AbortController(); pollController = controller;
        const timeout = setTimeout(() => controller.abort(), 1200);
        let delay = compactSpectrum() ? 140 : 30;
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
        if (!reducedMotion.matches || now - lastDraw >= 90 || (visualState !== 'playing' && !moving)) {
            lastDraw = now;
            if (compactSpectrum()) drawCompactBars();
            else if (visualizerMode === 'bars') drawBars();
            else coverVisualizer.draw(visualizerMode, values, reducedMotion.matches ? 0 : now);
        }
        if (!reducedMotion.matches && (moving || (fullscreenSpectrum() && visualState === 'playing'))) raf = requestAnimationFrame(draw);
    }

    function drawCompactBars() {
        const { width, height } = compactCanvas;
        compactContext.clearRect(0, 0, width, height);
        compactContext.fillStyle = spectrumColors[1];
        const step = width / 12;
        for (let i = 0; i < 12; i++) {
            const first = i * 4;
            const peak = Math.max(values[first], values[first + 1], values[first + 2], values[first + 3]);
            const bar = Math.max(3, Math.sqrt(peak) * (height - 4));
            compactContext.globalAlpha = peak > .01 ? .9 : .4;
            compactContext.beginPath();
            compactContext.roundRect((i + .5) * step - 2, (height - bar) / 2, 4, bar, 2);
            compactContext.fill();
        }
    }

    function drawBars() {
        const width = wave.clientWidth, height = wave.clientHeight;
        if (!width || !height) return;
        const dpr = Math.min(2, devicePixelRatio || 1);
        const bitmapWidth = Math.round(width * dpr), bitmapHeight = Math.round(height * dpr);
        const bitmapChanged = canvas.width !== bitmapWidth || canvas.height !== bitmapHeight;
        if (bitmapChanged) {
            canvas.width = bitmapWidth; canvas.height = bitmapHeight;
        }
        context.setTransform(bitmapWidth / width, 0, 0, bitmapHeight / height, 0, 0);
        if (bitmapChanged || chartHeight !== height) {
            chartHeight = height;
            chartFill = context.createLinearGradient(0, height, 0, 0);
            chartFill.addColorStop(0, spectrumColors[0]);
            chartFill.addColorStop(1, spectrumColors[1]);
        }
        context.clearRect(0, 0, width, height);
        context.fillStyle = chartFill;
        const step = width / DRAWN_BARS, barWidth = step * .56;
        for (let i = 0; i < DRAWN_BARS; i++) {
            // Соседние частоты образуют одну видимую полосу, FFT по-прежнему приходит после EQ.
            const value = Math.max(values[i * 2], values[i * 2 + 1]);
            const bar = Math.pow(Math.min(1, value * 1.7), .75) * (height - 2);
            if (bar <= .1) continue;
            context.beginPath();
            context.roundRect((i + .5) * step - barWidth / 2, height - 1 - bar, barWidth, bar, Math.min(2, barWidth / 2));
            context.fill();
        }
    }

    function screenChanged(name, reveal = null) {
        entryReveal = reveal;
        workspace.classList.toggle('playing-mode', name === 'playing');
        invalidatePoll();
        updateAtmosphere(); syncSpectrum();
        if (!reveal) return;
        // The background is already visible. Start spectrum polling after the reveal.
        const startEffects = () => {
            if (entryReveal !== reveal) return;
            entryReveal = null;
            syncSpectrum();
        };
        reveal.finished.then(startEffects, startEffects);
    }
    function trackChanged(meta, state) {
        const changed = trackPath !== currentTrackPath;
        if (changed) { trackPath = currentTrackPath; clearSpectrum(); }
        visualState = state;
        const url = safeTrackCoverURL(meta?.cover_url);
        if (url !== coverUrl) { coverUrl = url; updateAtmosphere(); }
        syncSpectrum(); syncDrift();
        window.equalizerPanel?.layout();
    }
    window.playerVisuals = {
        screenChanged, trackChanged, updateControls, updateRanges,
        playbackChanged(state) { visualState = state; syncSpectrum(); syncDrift(); },
        stop() { visualState = 'idle'; trackPath = null; coverUrl = ''; wave.hidden = radialCanvas.hidden = compactCanvas.hidden = status.hidden = true; clearSpectrum(); coverRevision++; hideAtmosphere(); },
        connectionLost() { target.fill(0); animate(); },
        themeChanged() { syncVisualAccent(); updateRanges(); animate(); }
    };
    window.addEventListener('resize', () => { coverVisualizer.invalidateGeometry(); animate(); });
    compactViewport.addEventListener('change', syncSpectrum);
    function lifecycle(hidden) {
        nativeMinimized = hidden; syncDrift();
        // Cancel stale RAF/request handles even if Chromium froze the previous frame.
        clearSpectrum();
        if (hidden) {
            void fetch('/api/player/spectrum/suspend',{method:'POST',signal:AbortSignal.timeout(1000)}).catch(()=>{});
        } else {
            visualState = currentPlaybackState;
            syncSpectrum(); animate();
            window.playerMotion?.restored();
        }
    }
    window.addEventListener('native-window-state', event => lifecycle(document.hidden || Boolean(event.detail?.minimized)));
    // Pause the color layers while the grid changes width. Preserve
    // animation positions instead of restarting the background after each click.
    window.addEventListener('sidebar-animation-start',()=>{sidebarResizing=true;syncDrift();});
    window.addEventListener('sidebar-animation-end',()=>{sidebarResizing=false;syncDrift();});
    document.addEventListener('visibilitychange', () => lifecycle(document.hidden || window.__mainVisible === false));
    window.addEventListener('pageshow', () => { if (!document.hidden && window.__mainVisible !== false) lifecycle(false); });
    window.addEventListener('focus', () => { if (!document.hidden && window.__mainVisible !== false) lifecycle(false); });
    reducedMotion.addEventListener('change', () => {syncDrift(); if (activeSpectrum()) animate(); });
    syncVisualAccent();
    if (window.visualSettingsSnapshot) loadSettings(window.visualSettingsSnapshot);
    updateControls(); updateRanges(); screenChanged(activeScreen);
})();
