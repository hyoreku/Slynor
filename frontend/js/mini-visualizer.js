(() => {
    const canvas = document.getElementById('mini-spectrum');
    const compactCanvas = document.getElementById('mini-compact-spectrum');
    const ctx = canvas.getContext('2d');
    const compactContext = compactCanvas.getContext('2d');
    if (!ctx || !compactContext) return;
    const wave = window.createWaveRenderer(ctx);
    const panel = document.getElementById('mini-panel');
    const cover = document.getElementById('mini-cover');
    let waveStyle, albumPalette, paletteUrl;
    function updatePalette() {
        const url = cover.dataset.url || '';
        if (paletteUrl === url) return;
        paletteUrl = url;
        albumPalette = null;
        if (url) void window.coverPalette(url).then(colors => {
            if (paletteUrl === url) albumPalette = colors;
        });
    }
    new MutationObserver(updatePalette).observe(cover, { attributes: true, attributeFilter: ['data-url'] });
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    const modes = new Set(['bars', 'wave', 'rays']);
    const bands = new Float32Array(48);
    const target = new Float32Array(48);
    let session, mode, color, highlight, timer, controller, raf;
    let width, height, radius, edge = [];
    let compact = document.documentElement.dataset.miniCompact === 'true';
    let theme;
    let lastReadyAt = -Infinity;

    function clear() {
        ctx.resetTransform();
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    function stop() {
        session = null;
        controller?.abort();
        controller = null;
        clearTimeout(timer);
        cancelAnimationFrame(raf);
        timer = raf = null;
        bands.fill(0);
        target.fill(0);
        lastReadyAt = -Infinity;
        clear();
        canvas.hidden = true;
        if (compact) paint(false);
    }

    function perimeter() {
        const left = radius, right = width - radius;
        const top = radius, bottom = height - radius;
        const horizontal = right - left, vertical = bottom - top, corner = Math.PI * radius / 2;
        const sides = [
            [left, top, 0, -1, horizontal], [right, top, 1, 0, vertical],
            [right, bottom, 0, 1, horizontal], [left, bottom, -1, 0, vertical],
        ];
        const points = [];
        for (const [x, y, nx, ny, length] of sides) {
            for (let step = 0; step < length; step += 4) {
                points.push([x + nx * radius - ny * step, y + ny * radius + nx * step, nx, ny]);
            }
            const angle = Math.atan2(ny, nx);
            for (let step = 0; step < corner; step += 3) {
                const a = angle + step / radius;
                const [dx, dy] = [Math.cos(a), Math.sin(a)];
                points.push([x - ny * length + dx * radius, y + nx * length + dy * radius, dx, dy]);
            }
        }
        return points;
    }

    function frame() {
        const nextWidth = canvas.clientWidth, nextHeight = canvas.clientHeight;
        if (!nextWidth || !nextHeight) return false;
        if (nextWidth !== width || nextHeight !== height) {
            width = nextWidth;
            height = nextHeight;
            radius = parseFloat(getComputedStyle(panel).borderTopLeftRadius);
            edge = perimeter();
        }
        const scale = Math.min(2, devicePixelRatio || 1);
        const w = Math.round(width * scale), h = Math.round(height * scale);
        if (canvas.width !== w) canvas.width = w;
        if (canvas.height !== h) canvas.height = h;
        ctx.setTransform(w / width, 0, 0, h / height, 0, 0);
        ctx.clearRect(0, 0, width, height);
        return true;
    }

    function amplitude(index) {
        const turn = index / edge.length * 4;
        const band = (1 - Math.abs(turn % 2 - 1)) * (bands.length - 1);
        const low = Math.floor(band), high = Math.ceil(band);
        return Math.sqrt(bands[low] + (bands[high] - bands[low]) * (band - low));
    }

    function outline(offset, travel, stroke, opacity) {
        ctx.beginPath();
        edge.forEach(([x, y, nx, ny], index) => {
            const distance = offset + amplitude(index) * travel;
            if (index === 0) ctx.moveTo(x - nx * distance, y - ny * distance);
            else ctx.lineTo(x - nx * distance, y - ny * distance);
        });
        ctx.closePath();
        ctx.strokeStyle = stroke;
        ctx.globalAlpha = opacity;
        ctx.stroke();
    }

    function drawPerimeter() {
        let bassEnergy = 0;
        for (let index = 0; index < 8; index++) bassEnergy += bands[index];
        const bass = Math.sqrt(bassEnergy / 8);
        const tone = index => {
            const source = albumPalette?.[index] ? `rgb(${albumPalette[index].join(',')})` : color;
            return `color-mix(in srgb, ${source} 68%, ${highlight})`;
        };
        ctx.save();
        ctx.beginPath();
        ctx.roundRect(0, 0, width, height, radius);
        ctx.clip();
        ctx.lineJoin = ctx.lineCap = 'round';
        ctx.lineWidth = 1.5;
        outline(1, 0, tone(0), .68);
        if (mode === 'wave') {
            ctx.translate(width / 2, height / 2);
            wave.draw({ width, height, radius, reach: 10,
                bands, time: reduced.matches ? 0 : performance.now(), style: waveStyle,
                palette: albumPalette, fallback: [color, highlight], mini: true });
        } else {
            if (mode === 'rays') outline(.7, bass * .7, tone(0), .7);
            const spacing = mode === 'rays' ? 3 : 2;
            const drawBands = (scale, stroke, lineWidth, opacity) => {
                ctx.beginPath();
                for (let i = 0; i < edge.length; i += spacing) {
                    const [px, py, nx, ny] = edge[i];
                    const length = Math.min(9, amplitude(i) * 8.5 + bass * 1.6) * scale;
                    if (length < .35) continue;
                    ctx.moveTo(px - nx, py - ny);
                    ctx.lineTo(px - nx * length, py - ny * length);
                }
                ctx.strokeStyle = stroke;
                ctx.globalAlpha = opacity;
                ctx.lineWidth = lineWidth;
                ctx.stroke();
            };
            if (mode === 'rays') drawBands(1, tone(1), 2.5, .42);
            drawBands(mode === 'rays' ? .68 : 1, tone(0), 1.3, .9);
        }
        ctx.restore();
    }

    function drawCompact(audible) {
        const width = compactCanvas.clientWidth, height = compactCanvas.clientHeight;
        if (!width || !height) return;
        const dpr = Math.min(2, devicePixelRatio || 1);
        const w = Math.round(width * dpr), h = Math.round(height * dpr);
        if (compactCanvas.width !== w) compactCanvas.width = w;
        if (compactCanvas.height !== h) compactCanvas.height = h;
        compactContext.setTransform(w / width, 0, 0, h / height, 0, 0);
        compactContext.clearRect(0, 0, width, height);
        const count = 12;
        const spacing = width / count;
        compactContext.fillStyle = theme === 'light' ? '#252132' : '#f7f4ff';
        compactContext.strokeStyle = theme === 'light' ? '#ffffff91' : '#101014bb';
        compactContext.lineWidth = 1.7;
        compactContext.beginPath();
        for (let i = 0; i < count; i++) {
            const offset = i * 4;
            const peak = Math.max(bands[offset], bands[offset + 1], bands[offset + 2], bands[offset + 3]);
            const bar = 3 + (audible ? Math.sqrt(peak) * (height - 5) : 0);
            compactContext.roundRect((i + .5) * spacing - 1.25, (height - bar) / 2, 2.5, bar, 1.25);
        }
        compactContext.stroke();
        compactContext.fill();
    }

    function paint(audible) {
        if (compact) drawCompact(audible);
        else if (frame() && audible) drawPerimeter();
    }

    function draw() {
        raf = null;
        if (!session) return;
        let audible = false;
        for (let i = 0; i < bands.length; i++) {
            bands[i] += (target[i] - bands[i]) * (reduced.matches ? 1 : target[i] > bands[i] ? .44 : .19);
            if (bands[i] < .001) bands[i] = 0;
            audible ||= bands[i] > 0;
        }
        paint(audible);
        if (audible && !reduced.matches) raf = requestAnimationFrame(draw);
    }

    async function read(key) {
        timer = null;
        if (key !== session) return;
        const request = new AbortController();
        controller = request;
        const timeout = setTimeout(() => request.abort(), 1200);
        let delay = reduced.matches ? 160 : 90;
        try {
            const response = await fetch('/api/player/spectrum', {cache: 'no-store', signal: request.signal});
            if (!response.ok) throw Error();
            const data = await response.json();
            if (key !== session) return;
            if (data.file_path === key.path && data.state === 'playing' && data.status === 'ready'
                    && (data.playback_generation == null || data.playback_generation === key.generation)
                    && Array.isArray(data.bands) && data.bands.length === bands.length
                    && data.bands.every(value => Number.isFinite(value) && value >= 0 && value <= 1)) {
                target.set(data.bands);
                lastReadyAt = performance.now();
            } else if (data.status !== 'preparing' || data.file_path !== key.path || performance.now() - lastReadyAt > 400) {
                target.fill(0);
            }
            if (['error', 'unavailable'].includes(data.status)) delay = 1500;
        } catch {
            if (key !== session) return;
            target.fill(0);
            delay = 1500;
        } finally {
            clearTimeout(timeout);
            if (controller === request) controller = null;
            if (key === session) {
                if (raf == null) raf = requestAnimationFrame(draw);
                timer = setTimeout(() => void read(key), delay);
            }
        }
    }

    window.miniVisualizer = {
        update(state) {
            const appearance = state?.appearance;
            const nextMode = appearance?.visualizer_mode;
            const path = state?.file_path;
            waveStyle = appearance?.wave_style;
            const themeChanged = appearance && appearance.theme !== theme;
            if (themeChanged) theme = appearance.theme;
            if (document.hidden || state?.window_visibility?.mini === false || state?.state !== 'playing'
                    || !path || path.startsWith('online:') || !modes.has(nextMode)) {
                if (session) stop();
                else if (compact && themeChanged) paint(false);
                return;
            }
            color = appearance.accent || '#c9c9d1';
            highlight = appearance.theme === 'light' ? '#565168' : '#ffffff';
            if (session?.path === path && session.generation === state.playback_generation && mode === nextMode) return;
            stop();
            mode = nextMode;
            canvas.dataset.mode = mode;
            canvas.hidden = false;
            session = {path, generation: state.playback_generation};
            void read(session);
        },
    };
    document.addEventListener('mini-layout-change', () => {
        compact = document.documentElement.dataset.miniCompact === 'true';
        width = undefined;
        paint(bands.some(value => value > 0));
    });
    document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
    if (compact) stop();
})();
