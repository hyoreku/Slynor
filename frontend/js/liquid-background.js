// Paint small gradient textures when the cover changes; CSS moves the finished layers.
(() => {
    const WIDTH = 256, HEIGHT = 208, SHAPE_COUNT = 12;
    const neutral = [[114, 119, 136], [86, 94, 112], [62, 67, 82]];
    const rgba = (rgb, opacity = 1) => `rgba(${rgb.join(',')},${opacity})`;
    const mix = (rgb, tint, amount) => rgb.map((value, i) => Math.round(value + (tint[i] - value) * amount));

    function paint(sprite, color, neighbor, index) {
        const ctx = sprite.getContext('2d');
        if (!ctx) return;
        ctx.clearRect(0, 0, WIDTH, HEIGHT);
        const path = new Path2D();
        const phase = index * 1.78;
        for (let i = 0; i < 96; i++) {
            const angle = i / 96 * Math.PI * 2;
            const radius = .78 + .18 * Math.cos(2 * angle + phase) + .16 * Math.sin(3 * angle - phase) + .07 * Math.cos(5 * angle + phase);
            const x = WIDTH / 2 + Math.cos(angle) * radius * 86;
            const y = HEIGHT / 2 + Math.sin(angle) * radius * 65;
            if (i === 0) path.moveTo(x, y);
            else path.lineTo(x, y);
        }
        path.closePath();
        const body = ctx.createRadialGradient(111, 90, 4, 132, 108, 106);
        body.addColorStop(0, rgba(mix(color, neighbor, .22), .94));
        body.addColorStop(.5, rgba(color, .8));
        body.addColorStop(1, rgba(mix(color, [17, 18, 25], .32), .35));
        ctx.save();
        ctx.filter = 'blur(11px)';
        ctx.fillStyle = body;
        ctx.fill(path);
        ctx.restore();
    }

    window.createLiquidBackground = (host, before = null) => {
        const layer = document.createElement('div');
        layer.className = 'liquid-backdrop';
        layer.hidden = true;
        let sprites;
        host.insertBefore(layer, before);
        let paintedColors = '';
        return {
            setPalette(colors = neutral) {
                const key = colors.join('|');
                if (key === paintedColors) return;
                paintedColors = key;
                layer.style.setProperty('--liquid-ambient', rgba(colors[0], .16));
                sprites ??= Array.from({ length: SHAPE_COUNT }, () => {
                    const sprite = document.createElement('canvas');
                    sprite.width = WIDTH;
                    sprite.height = HEIGHT;
                    sprite.className = 'liquid-shape';
                    layer.append(sprite);
                    return sprite;
                });
                sprites.forEach((sprite, i) => paint(sprite, colors[i % colors.length], colors[(i + 1) % colors.length], i));
            },
            setActive(visible, moving) {
                layer.hidden = !visible;
                layer.classList.toggle('is-moving', visible && moving);
            },
        };
    };
})();
