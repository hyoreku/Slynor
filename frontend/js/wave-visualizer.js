(() => {
    const count = 144;
    const tau = Math.PI * 2;
    const shapes = {
        surf: { lobes: 4, power: 1.35, echo: .35 },
        ion: { lobes: 9, power: 2.3, echo: .18, sparks: true },
        ember: { lobes: 5, power: 1.55, echo: .95 },
    };

    function perimeter(width, height, radius) {
        const x = width / 2 - radius, y = height / 2 - radius;
        const turn = radius * Math.PI / 2;
        const sides = [
            [-x, -height / 2, 1, 0, 0, -1, 2 * x],
            [width / 2, -y, 0, 1, 1, 0, 2 * y],
            [x, height / 2, -1, 0, 0, 1, 2 * x],
            [-width / 2, y, 0, -1, -1, 0, 2 * y],
        ];
        const length = 4 * (x + y + turn);
        return Array.from({ length: count }, (_, i) => {
            let distance = i * length / count;
            for (const [sx, sy, tx, ty, nx, ny, span] of sides) {
                if (distance <= span) return [sx + tx * distance, sy + ty * distance, nx, ny];
                distance -= span;
                if (distance <= turn) {
                    const angle = Math.atan2(ny, nx) + distance / radius;
                    const dx = Math.cos(angle), dy = Math.sin(angle);
                    return [sx + tx * span + radius * (dx - nx), sy + ty * span + radius * (dy - ny), dx, dy];
                }
                distance -= turn;
            }
            return [-x, -height / 2, 0, -1];
        });
    }

    window.createWaveRenderer = ctx => {
        const xs = new Float32Array(count), ys = new Float32Array(count);
        let geometry, gradient, colors, previousFrame, echo = 0;
        return {
            draw({ width, height, radius, reach, bands, time, style, palette, fallback, compact = false }) {
                if (width < 2 || height < 2 || reach < 1) return;
                const shape = shapes[style] || shapes.surf;
                const key = [width, height, radius].join('/');
                if (geometry?.key !== key) {
                    const corner = Math.max(1, Math.min(radius, width / 2, height / 2));
                    geometry = { key, points: perimeter(width, height, corner) };
                    gradient = null;
                }
                const tones = palette?.map(rgb => 'rgb(' + rgb.join(',') + ')') || fallback;
                const colorKey = tones.join('/');
                if (!gradient || colors !== colorKey) {
                    const mix = (tone, tint, percent) => `color-mix(in srgb, ${tone}, ${tint} ${percent}%)`;
                    const [first, second = first, third = second] = tones;
                    const dark = ctx.createLinearGradient(-width / 2, -height / 2, width / 2, height / 2);
                    dark.addColorStop(0, mix(first, 'white', 20));
                    dark.addColorStop(.52, mix(second, 'white', 34));
                    dark.addColorStop(1, mix(third, 'white', 23));
                    const light = ctx.createLinearGradient(-width / 2, -height / 2, width / 2, height / 2);
                    light.addColorStop(0, mix(second, 'white', 60));
                    light.addColorStop(1, mix(first, 'white', 54));
                    gradient = { dark, light, crest: mix(third, 'white', 78) };
                    colors = colorKey;
                }

                let bass = 0, peak = 0;
                for (let i = 0; i < bands.length; i++) {
                    if (i < 8) bass += bands[i] / 8;
                    peak = Math.max(peak, bands[i]);
                }
                if (peak < .008) return;
                const drive = Math.min(1, Math.sqrt(bass * 2.7 + peak * .22));
                const elapsed = time - previousFrame;
                echo = elapsed > 0 && elapsed < 220
                    ? echo + (drive - echo) * (1 - Math.exp(-elapsed / 120))
                    : drive;
                previousFrame = time;
                const phase = time * .0048;
                function trace(spread, offset, base, strength = drive) {
                    for (let i = 0; i < count; i++) {
                        const [x, y, nx, ny] = geometry.points[i];
                        const angle = i * tau / count;
                        const lobe = Math.pow(.5 + .5 * Math.sin(angle * shape.lobes - phase + offset), shape.power);
                        const detail = Math.sqrt(bands[Math.round(2 + Math.abs(Math.cos(2.1 * angle)) * (bands.length - 3))]);
                        const falloff = compact ? 1 : 1 - .38 * Math.abs(ny);
                        const distance = reach * falloff * Math.min(1, base + spread * (.12 + .72 * lobe + .16 * detail) * strength);
                        xs[i] = x + nx * distance;
                        ys[i] = y + ny * distance;
                    }
                    ctx.beginPath();
                    ctx.moveTo((xs[count - 1] + xs[0]) / 2, (ys[count - 1] + ys[0]) / 2);
                    for (let i = 0; i < count; i++) {
                        const next = (i + 1) % count;
                        ctx.quadraticCurveTo(xs[i], ys[i], (xs[i] + xs[next]) / 2, (ys[i] + ys[next]) / 2);
                    }
                    ctx.closePath();
                }
                function fill(color, opacity) {
                    ctx.fillStyle = color;
                    ctx.globalAlpha = opacity;
                    ctx.fill();
                }
                ctx.save();
                ctx.lineJoin = 'round';
                trace(1, shape.echo, .12, style === 'ember' ? echo : drive);
                fill(gradient.dark, .30 + .46 * drive);
                trace(.77, 0, .065);
                fill(gradient.dark, .43 + .43 * drive);
                trace(.57, -.24, .040);
                fill(gradient.light, .40 + .40 * drive);
                trace(style === 'surf' ? .39 : .29, -.54, .025);
                ctx.strokeStyle = gradient.crest;
                ctx.lineWidth = compact ? 1.35 : 2.5;
                ctx.globalAlpha = .63 + .31 * drive;
                ctx.stroke();
                if (shape.sparks && !compact) {
                    ctx.fillStyle = gradient.crest;
                    ctx.globalAlpha = drive * .62;
                    for (let i = 0; i < count; i += 11) {
                        const [x, y, nx, ny] = geometry.points[i];
                        const distance = reach * (.15 + .48 * Math.pow(.5 + .5 * Math.sin(i * .33 - phase * 2), 3)) * drive;
                        ctx.beginPath();
                        ctx.arc(x + nx * distance, y + ny * distance, 1.35, 0, tau);
                        ctx.fill();
                    }
                }
                ctx.restore();
            },
        };
    };
})();
