(() => {
    const layer = document.createElement('div');
    layer.id = 'mini-background';
    layer.setAttribute('aria-hidden', 'true');
    document.body.prepend(layer);
    const image = new Image();
    const video = document.createElement('video');
    video.muted = true;
    video.loop = true;
    video.playsInline = true;
    video.preload = 'auto';
    const colors = document.createElement('div');
    colors.className = 'mini-colors';
    for (let i = 0; i < 3; i++) colors.append(document.createElement('span'));
    image.hidden = video.hidden = colors.hidden = true;
    layer.append(image, video, colors);
    const liquid = window.createLiquidBackground(layer);
    const cover = document.getElementById('mini-cover');
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let current, lastState, albumColors;
    let lastMedia = '', lastCover = '', paletteRevision = 0, nativeHidden = false;

    function syncMedia() {
        if (!current) return;
        const visible = !nativeHidden && !document.hidden && lastState?.window_visibility?.mini !== false;
        const moving = visible && !reduced.matches;
        const mode = current.mini_background;
        const url = mode === 'wallpaper' && current.wallpaper ? '/api/assets/wallpapers/' + current.wallpaper : '';
        if (url !== lastMedia) {
            lastMedia = url;
            video.pause(); video.removeAttribute('src'); image.removeAttribute('src');
            video.hidden = image.hidden = true;
            if (/\.(mp4|webm)$/.test(url)) { video.src = url; video.hidden = false; }
            else if (url) { image.src = url; image.hidden = false; }
        }
        if (moving && video.getAttribute('src')) void video.play().catch(() => {});
        else video.pause();
        if (/\.gif$/.test(url)) {
            if (moving && !image.getAttribute('src')) image.src = url;
            if (!moving) image.removeAttribute('src');
        }
        colors.hidden = mode !== 'colors';
        colors.style.animationPlayState = moving ? 'running' : 'paused';
        for (const blob of colors.children) blob.style.animationPlayState = colors.style.animationPlayState;
        liquid.setActive(mode === 'liquid', moving);
    }

    function renderPalette() {
        for (let i = 0; i < 3; i++) {
            const property = '--mini-c' + (i + 1);
            if (albumColors) colors.style.setProperty(property, albumColors[i].join(','));
            else colors.style.removeProperty(property);
        }
        if (current?.mini_background === 'liquid') liquid.setPalette(albumColors);
    }

    function readPalette() {
        const url = cover.dataset.url || '';
        if (lastCover === url) return;
        lastCover = url;
        const revision = ++paletteRevision;
        if (!url) { albumColors = undefined; renderPalette(); return; }
        void window.coverPalette(url).then(rgb => {
            if (revision !== paletteRevision) return;
            albumColors = rgb || undefined;
            renderPalette();
        });
    }

    function applyAppearance(appearance) {
        const root = document.documentElement;
        layer.dataset.mode = appearance.mini_background;
        root.dataset.theme = appearance.theme;
        root.style.setProperty('--accent', appearance.accent);
        const rgb = appearance.accent.slice(1).match(/../g).map(value => parseInt(value, 16));
        root.style.setProperty('--accent-rgb', rgb.join(','));
        const [red, green, blue] = rgb.map(value => {
            const channel = value / 255;
            return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
        });
        const lum = .2126 * red + .7152 * green + .0722 * blue;
        root.style.setProperty('--accent-on', lum > .179 ? '#000000' : '#ffffff');
        layer.style.setProperty('--mini-dim', String(appearance.dim / 100));
        layer.style.setProperty('--mini-blur', appearance.blur + 'px');
    }

    window.miniAppearance = {
        update(state) {
            lastState = state;
            const appearance = state.appearance;
            if (appearance && JSON.stringify(appearance) !== JSON.stringify(current)) {
                current = appearance;
                applyAppearance(appearance);
                renderPalette();
            }
            syncMedia();
            readPalette();
        },
    };
    new MutationObserver(readPalette).observe(cover, { attributes: true, attributeFilter: ['data-url'] });
    window.addEventListener('native-window-state', event => { nativeHidden = Boolean(event.detail?.minimized); syncMedia(); });
    document.addEventListener('visibilitychange', syncMedia);
    reduced.addEventListener('change', syncMedia);
})();
