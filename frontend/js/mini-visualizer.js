(() => {
    const canvas = document.getElementById('mini-spectrum');
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const wave = window.createWaveRenderer(ctx);
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
    let width, height, edge = [];

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
        clear();
        canvas.hidden = true;
    }

    function perimeter(width, height) {
        const inset = 2, radius = 8;
        const left = inset + radius, right = width - left;
        const top = inset + radius, bottom = height - top;
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
        if (nextWidth < 24 || nextHeight < 24) return false;
        if (nextWidth !== width || nextHeight !== height) {
            width = nextWidth;
            height = nextHeight;
            edge = perimeter(width, height);
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
        ctx.lineJoin = ctx.lineCap = 'round';
        ctx.lineWidth = 1.3;
        if (mode === 'bars') {
            ctx.beginPath();
            for (let i = 0; i < edge.length; i += 2) {
                const [x, y, nx, ny] = edge[i];
                const length = amplitude(i) * 5.5;
                if (length < .35) continue;
                ctx.moveTo(x, y);
                ctx.lineTo(x - nx * length, y - ny * length);
            }
            ctx.strokeStyle = color;
            ctx.stroke();
        } else if (mode === 'wave') {
            const inset = 9;
            ctx.save();
            ctx.translate(width / 2, height / 2);
            ctx.beginPath();
            ctx.rect(-width / 2, -height / 2, width, height);
            ctx.roundRect(inset - width / 2, inset - height / 2, width - 2 * inset, height - 2 * inset, 8);
            ctx.clip('evenodd');
            wave.draw({ width: width - 2 * inset, height: height - 2 * inset, radius: 8, reach: 8,
                bands, time: reduced.matches ? 0 : performance.now(), style: waveStyle,
                palette: albumPalette, fallback: [color, highlight], compact: true });
            ctx.restore();
        } else {
            outline(0, 0, color, .85);
            outline(2.1, 2.6, highlight, .75);
        }
        ctx.globalAlpha = 1;
    }

    function draw() {
        raf = null;
        if (!session) return;
        let audible = false;
        for (let i = 0; i < bands.length; i++) {
            bands[i] += (target[i] - bands[i]) * (reduced.matches ? 1 : .28);
            if (bands[i] < .001) bands[i] = 0;
            audible ||= bands[i] > 0;
        }
        if (frame() && audible) drawPerimeter();
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
            } else if (data.status !== 'preparing' || data.file_path !== key.path) {
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
            if (document.hidden || state?.window_visibility?.mini === false || state?.state !== 'playing'
                    || !path || path.startsWith('online:') || !modes.has(nextMode)) {
                if (session) stop();
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
    document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
})();
