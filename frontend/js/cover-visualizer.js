// Draw behind the artwork. Each shape starts inside the rounded cover and grows out with the audio.
(() => {
    const TAU = Math.PI * 2;
    const SAMPLES = 144;
    const smoothBand = (bands, i) => (bands[Math.max(0, i - 1)] + 2 * bands[i] + bands[Math.min(bands.length - 1, i + 1)]) / 4;

    function outline(w, h, radius, width, height) {
        const halfW = w / 2, halfH = h / 2;
        const cornerX = halfW - radius, cornerY = halfH - radius;
        const padX = Math.max(1, (width - w) / 2 - 6);
        const padY = Math.max(1, (height - h) / 2 - 6);
        return Array.from({ length: SAMPLES }, (_, i) => {
            const angle = TAU * i / SAMPLES - Math.PI / 2;
            const x = Math.cos(angle), y = Math.sin(angle);
            const ax = Math.abs(x), ay = Math.abs(y);
            let edge = Math.min(halfW / ax, halfH / ay);
            if (ax * edge > cornerX && ay * edge > cornerY) {
                const along = ax * cornerX + ay * cornerY;
                const across = ax * cornerY - ay * cornerX;
                edge = along + Math.sqrt(Math.max(0, radius * radius - across * across));
            }
            const reach = Math.min(1 / Math.hypot(x / padX, y / padY),
                Math.min((width / 2 - 6) / ax, (height / 2 - 6) / ay) - edge);
            return { angle, x, y, root: edge - 4, reach: Math.max(0, reach) };
        });
    }

    window.createCoverVisualizer = (canvas, cover) => {
        const ctx = canvas.getContext('2d');
        const wave = window.createWaveRenderer(ctx);
        let albumPalette;
        const levels = new Float32Array(SAMPLES), radii = new Float32Array(SAMPLES);
        let geometry, palette, colors, painted = false;

        function clear() {
            if (!painted) return;
            ctx.resetTransform();
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            painted = false;
        }

        function prepareColors(width, height, [accent, highlight]) {
            const gradient = (stops) => {
                const result = ctx.createLinearGradient(-width / 2, -height / 2, width / 2, height / 2);
                for (const [at, color] of stops) result.addColorStop(at, color);
                return result;
            };
            return {
                liquid: gradient([[0, accent], [.32, highlight], [.62, accent], [.84, highlight], [1, accent]]),
                edge: gradient([[0, highlight], [.23, '#fff'], [.48, accent], [.7, highlight], [1, accent]]),
                depth: gradient([[0, accent], [.38, highlight], [.68, accent], [1, highlight]]),
                crest: gradient([[0, '#fff'], [.24, highlight], [.54, accent], [.76, highlight], [1, '#fff']]),
            };
        }

        function trace(points, spread) {
            for (let i = 0; i < points.length; i++) {
                const { root, reach } = points[i];
                radii[i] = root + reach * levels[i] * spread;
            }
            const last = points.at(-1), first = points[0];
            let px = last.x * radii.at(-1), py = last.y * radii.at(-1);
            ctx.beginPath();
            ctx.moveTo((px + first.x * radii[0]) / 2, (py + first.y * radii[0]) / 2);
            for (let i = 0; i < points.length; i++) {
                const nextIndex = (i + 1) % points.length;
                const point = points[i], next = points[nextIndex];
                px = point.x * radii[i]; py = point.y * radii[i];
                ctx.quadraticCurveTo(px, py, (px + next.x * radii[nextIndex]) / 2, (py + next.y * radii[nextIndex]) / 2);
            }
            ctx.closePath();
        }

        function paintShape(fill, opacity, edge, edgeOpacity, lineWidth) {
            ctx.globalAlpha = opacity;
            ctx.fillStyle = fill;
            ctx.fill();
            ctx.globalAlpha = edgeOpacity;
            ctx.strokeStyle = edge;
            ctx.lineWidth = lineWidth;
            ctx.stroke();
        }

        function drawContours(points, phase, intensity) {
            const tilt = .18 + .11 * Math.sin(phase);
            for (let i = 0; i < points.length; i++) {
                const angle = points[i].angle;
                const sides = Math.abs(Math.cos(angle + tilt));
                const sway = .88 + .12 * Math.sin(3 * angle + phase);
                levels[i] *= (.08 + .92 * sides ** 4) * sway;
            }
            trace(points, 1);
            paintShape(colors.depth, intensity * .26, colors.liquid, intensity * .62, 1.4);
            trace(points, .72);
            paintShape(colors.liquid, intensity * .35, colors.edge, intensity * .76, 1.5);
            trace(points, .43);
            paintShape(colors.crest, intensity * .48, colors.crest, intensity * .84, 1.6);
        }

        return {
            setColors(values) { palette = values; colors = null; },
            setPalette(values) { albumPalette = values; },
            invalidateGeometry() { geometry = null; },
            clear,
            draw(mode, bands, time, style) {
                clear();
                const width = canvas.clientWidth, height = canvas.clientHeight;
                const peak = Math.max(...bands);
                if (!palette || !width || !height || peak < .008) return;
                const scale = Math.min(1.5, devicePixelRatio || 1);
                const bitmapW = Math.round(width * scale), bitmapH = Math.round(height * scale);
                if (canvas.width !== bitmapW || canvas.height !== bitmapH) {
                    canvas.width = bitmapW;
                    canvas.height = bitmapH;
                    colors = null;
                }
                if (geometry?.width !== width || geometry.height !== height) {
                    const w = cover.offsetWidth, h = cover.offsetHeight;
                    const radius = Math.min(w / 2, h / 2, parseFloat(getComputedStyle(cover).borderTopLeftRadius) || 0);
                    geometry = { width, height, w, h, radius, points: outline(w, h, radius, width, height) };
                    colors = null;
                }
                ctx.setTransform(bitmapW / width, 0, 0, bitmapH / height, bitmapW / 2, bitmapH / 2);
                colors ??= prepareColors(width, height, palette);
                ctx.save();
                // Leave a small overlap at the edge and a clear center, including for translucent placeholders.
                const { w, h, radius } = geometry;
                ctx.beginPath();
                ctx.rect(-width / 2, -height / 2, width, height);
                ctx.roundRect(2 - w / 2, 2 - h / 2, Math.max(0, w - 4), Math.max(0, h - 4), Math.max(0, radius - 2));
                ctx.clip('evenodd');
                ctx.lineJoin = 'round';
                ctx.lineCap = 'round';
                if (mode === 'wave') {
                    wave.draw({ width: w, height: h, radius, reach: Math.min(width - w, height - h) / 2 - 4,
                        bands, time, style, palette: albumPalette, fallback: palette });
                } else {
                    const bass = (bands[0] + bands[1] + bands[2] + bands[3] + bands[4] + bands[5]) / 6;
                    for (let i = 0; i < SAMPLES; i++) {
                        const position = 1 - Math.abs(1 - 2 * i / SAMPLES);
                        const band = Math.round(position * (bands.length - 1));
                        levels[i] = Math.sqrt(Math.min(1, 1.8 * (.3 * smoothBand(bands, band) + .7 * bass)));
                    }
                    drawContours(geometry.points, time * .0018, Math.min(1, Math.sqrt(peak) * 1.8));
                }
                ctx.restore();
                painted = true;
            },
        };
    };
})();
