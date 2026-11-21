// Facelift: главная, верхняя строка и экран «Сейчас играет».
// Классический скрипт: не переопределяет app.js, а читает его состояние
// через DOM и события slynor-player / slynor-library / slynor-playlists.
(() => {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const ICON_BASE = '/static/assets/icons/';

    function uiIcon(node, name, size) {
        if (!node || !name) return;
        if (node.dataset.uiIcon !== name) {
            const previousIcon = node.dataset.uiIcon;
            node.dataset.uiIcon = name;
            node.style.setProperty('--ui-icon', "url('" + ICON_BASE + name + ".svg')");
            window.animateButtonGlyph?.(node, previousIcon);
        }
        if (size && node.style.getPropertyValue('--ui-icon-size') !== size) node.style.setProperty('--ui-icon-size', size);
    }

    function pad(value) { return String(value).padStart(2, '0'); }

    function clock(milliseconds) {
        const total = Math.max(0, Math.round(Number(milliseconds) / 1000) || 0);
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const seconds = total % 60;
        return hours ? hours + ':' + pad(minutes) + ':' + pad(seconds) : pad(minutes) + ':' + pad(seconds);
    }

    function baseName(path) {
        const parts = String(path || '').split(/[\\/]/);
        return parts[parts.length - 1] || 'Трек';
    }

    // Безопасный доступ к привязкам app.js: часть из них — let/const в глобальной области.
    const core = {
        files() { try { return Array.isArray(libraryFiles) ? libraryFiles : []; } catch (_) { return []; } },
        meta(path) { try { return metadataMap.get(fileKey(path)); } catch (_) { return undefined; } },
        play(path) { try { pathInput.value = path; void sendCommand('play'); } catch (_) {} },
        currentPath() { try { return currentTrackPath; } catch (_) { return null; } },
        details(path) { try { if (path) showTrackDetails(path); } catch (_) {} },
    };

    function showScreenByName(name) {
        try { showScreen(name); } catch (_) {
            const button = document.querySelector('.nav-button[data-screen="' + name + '"]');
            if (button) button.click();
        }
    }

    // ---------- Приветствие и счётчики ----------

    function updateGreeting() {
        const now = new Date();
        const hour = now.getHours();
        const title = hour < 5 ? 'Доброй ночи' : hour < 12 ? 'Доброе утро' : hour < 18 ? 'Добрый день' : 'Добрый вечер';
        const titleNode = $('home-greeting-title');
        if (titleNode) titleNode.textContent = title;
        const timeNode = $('home-greeting-time');
        if (timeNode) timeNode.textContent = pad(now.getHours()) + ':' + pad(now.getMinutes());
    }

    function syncCounts() {
        const files = core.files();
        const navCount = $('nav-music-count');
        if (navCount) {
            navCount.textContent = files.length ? String(files.length) : '';
            navCount.hidden = files.length === 0;
        }
        const storage = $('sidebar-storage-count');
        if (!storage) return;
        let bytes = 0;
        for (const path of files) {
            const size = core.meta(path)?.size_bytes;
            if (!Number.isSafeInteger(size) || size < 0) {
                storage.textContent = `Треков: ${files.length}`;
                return;
            }
            bytes += size;
        }
        const units = ['byte', 'kilobyte', 'megabyte', 'gigabyte', 'terabyte'];
        let order = 0;
        while (bytes >= 1000 && order < units.length - 1) {
            bytes /= 1000;
            order++;
        }
        storage.textContent = new Intl.NumberFormat(document.documentElement.lang, {
            style: 'unit', unit: units[order], maximumFractionDigits: 1
        }).format(bytes);
    }

    // ---------- Hero: продолжение прослушивания ----------

    function playerVisible() {
        const bar = $('player-bar');
        return !!bar && !bar.hidden && !!core.currentPath();
    }

    function heroInfo() {
        const path = core.currentPath();
        if (!path) return null;
        const meta = core.meta(path) || {};
        const fallbackName = ($('track-name') && $('track-name').textContent.trim()) || baseName(path);
        const title = meta.title || fallbackName;
        const artist = (meta.artist || '').trim();
        const album = (meta.album || '').trim();
        const cover = meta.cover_url || ($('player-cover') && $('player-cover').dataset.coverUrl) || '';
        return { path, title, sub: [artist, album].filter(Boolean).join(' · ') || 'Локальный файл', cover };
    }

    function syncHero() {
        const hero = $('home-hero');
        const empty = $('home-hero-empty');
        if (!hero || !empty) return;

        const info = playerVisible() ? heroInfo() : null;
        hero.hidden = !info;
        empty.hidden = !!info;
        if (!info) return;

        const titleNode = $('home-hero-title');
        if (titleNode && titleNode.textContent !== info.title) titleNode.textContent = info.title;
        const subNode = $('home-hero-sub');
        if (subNode && subNode.textContent !== info.sub) subNode.textContent = info.sub;

        const art = $('home-hero-art');
        if (art) setSongCover(art, info.cover);

        const seek = $('seek-slider');
        const max = seek ? Number(seek.max) : 0;
        const value = seek ? Number(seek.value) : 0;
        const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
        const fill = $('home-hero-fill');
        if (fill) fill.style.width = percent.toFixed(2) + '%';
        const heroTime = $('home-hero-time');
        if (heroTime) heroTime.textContent = ($('current-time') && $('current-time').textContent) || '00:00';
        const heroDuration = $('home-hero-duration');
        if (heroDuration) heroDuration.textContent = ($('duration-time') && $('duration-time').textContent) || '00:00';

        const play = $('home-hero-play');
        if (play) {
            const playing = currentPlaybackState === 'playing';
            uiIcon(play, playing ? 'pause' : 'play', '18px');
            play.setAttribute('aria-label', playing ? 'Пауза' : 'Продолжить воспроизведение');
        }
    }

    // ---------- Недавно добавленные ----------

    function renderRecent() {
        const list = $('home-recent');
        if (!list) return;
        const files = core.files();
        const empty = $('home-recent-empty');
        if (empty) empty.hidden = files.length > 0;
        list.replaceChildren();

        files.slice(-5).reverse().forEach((path, index) => {
            const meta = core.meta(path) || {};
            const title = meta.title || baseName(path);
            const artist = (meta.artist || '').trim();
            const duration = Number.isFinite(meta.duration_ms) ? clock(meta.duration_ms) : '—';

            const row = document.createElement('li');
            row.className = 'home-recent-item';
            row.tabIndex = 0;
            row.setAttribute('role', 'button');
            row.setAttribute('aria-label', 'Воспроизвести: ' + title);

            const number = document.createElement('span');
            number.className = 'home-recent-index';
            number.textContent = String(index + 1);

            const art = document.createElement('span');
            art.className = 'home-recent-art';
            art.setAttribute('aria-hidden', 'true');
            setSongCover(art, meta.cover_url);

            const text = document.createElement('span');
            text.className = 'home-recent-text';
            const name = document.createElement('span');
            name.className = 'home-recent-name';
            name.dataset.userText = 'true';
            name.textContent = title;
            const sub = document.createElement('span');
            sub.className = 'home-recent-sub';
            sub.dataset.userText = 'true';
            sub.textContent = artist || 'Исполнитель не указан';
            text.append(name, sub);

            const album = document.createElement('span');
            album.className = 'home-recent-album';
            album.textContent = meta.album || '—';
            const time = document.createElement('span');
            time.className = 'home-recent-time';
            time.textContent = duration;

            const play = document.createElement('button');
            play.type = 'button';
            play.className = 'home-recent-play';
            play.setAttribute('aria-label', 'Воспроизвести: ' + title);
            uiIcon(play, 'play', '14px');

            const start = () => core.play(path);
            play.addEventListener('click', (event) => { event.stopPropagation(); start(); });
            row.addEventListener('click', (event) => {
                if (event.target.closest('button')) return;
                start();
            });
            row.addEventListener('keydown', (event) => {
                if (event.target === row && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); start(); }
            });

            row.append(number, art, text, album, time, play);
            list.append(row);
        });
    }

    // ---------- «Сейчас играет»: синхронизация с нижним плеером ----------

    let nowSeekEditing = false;

    function syncNowControls() {
        // Ошибки нельзя терять вместе со скрытым нижним плеером.
        const sourceError = $('playback-error');
        const nowError = $('now-playback-error');
        if (sourceError && nowError) {
            if (nowError.textContent !== sourceError.textContent) nowError.textContent = sourceError.textContent;
            nowError.hidden = sourceError.hidden || !sourceError.textContent.trim();
        }
        const seek = $('seek-slider');
        const nowSeek = $('now-seek');
        if (seek && nowSeek) {
            nowSeek.max = seek.max || '0';
            nowSeek.disabled = seek.disabled;
            if (!nowSeekEditing) nowSeek.value = seek.value;
            const max = Number(seek.max);
            const value = Number(seek.value);
            const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
            nowSeek.style.setProperty('--now-fill', percent.toFixed(2) + '%');
            const current = $('now-current-time');
            if (current) current.textContent = ($('current-time') && $('current-time').textContent) || '00:00';
            const duration = $('now-duration-time');
            if (duration) duration.textContent = ($('duration-time') && $('duration-time').textContent) || '00:00';
        }

        const toggle = $('play-toggle-button');
        const nowPlay = $('now-play');
        if (toggle && nowPlay) {
            const name = currentPlaybackState === 'playing' ? 'pause' : 'play';
            uiIcon(nowPlay, name, '20px');
            nowPlay.disabled = toggle.disabled;
            nowPlay.setAttribute('aria-label', name === 'pause' ? 'Пауза' : 'Воспроизвести');
        }

        const shuffle = $('shuffle-button');
        const nowShuffle = $('now-shuffle');
        if (shuffle && nowShuffle) {
            nowShuffle.disabled = shuffle.disabled;
            nowShuffle.setAttribute('aria-pressed', shuffle.getAttribute('aria-pressed') || 'false');
        }

        const eqLabelNode = $('now-eq-label');
        const eqPreset = $('eq-preset');
        const preset = eqPreset.selectedOptions[0];
        const customPreset = preset?.dataset.userText === 'true';
        const eqLabel = eqPreset.disabled || !preset ? 'Эквалайзер' : window.i18n.source(preset);
        eqLabelNode.toggleAttribute('data-user-text', Boolean(customPreset && !eqPreset.disabled));
        const visibleLabel = eqLabelNode.hasAttribute('data-user-text') ? eqLabel : window.i18n.t(eqLabel);
        if (eqLabelNode.textContent !== visibleLabel) eqLabelNode.textContent = eqLabel;

        const speed = $('eq-speed');
        const speedButton = $('now-speed');
        if (speed && speedButton) {
            const labels = { standard: '1×', speedup: '1,25×', slowed: '0,8×' };
            speedButton.textContent = labels[speed.value] || '1×';
        }
    }

    // ---------- Очередь на экране «Сейчас играет» ----------

    let queueLoading = false;
    let queueTotal = null;
    let queueRevision = 0;
    let queueRetry = false;
    let queueStateKey = '';
    let upcomingPaths = [];

    function setQueueVisible(visible) {
        const panel = $('now-upcoming-queue');
        const chip = $('now-queue-chip');
        if (!panel || !chip) return;
        if (!visible && panel.contains(document.activeElement)) chip.focus({ preventScroll: true });
        if (!visible && !panel.inert) queueRevision++;
        panel.inert = !visible;
        panel.closest('.now-stage').classList.toggle('queue-hidden', !visible);
        chip.setAttribute('aria-expanded', String(visible));
        const label = visible ? 'Скрыть очередь' : 'Показать очередь';
        if (window.i18n.t(label) !== chip.title) chip.title = label;
    }

    function syncQueueChip() {
        const chip = $('now-queue-count');
        const count = String(queueTotal ?? 0);
        if (chip.textContent !== count) chip.textContent = count;
    }

    function renderNowQueue(paths) {
        const list = $('now-queue-list');
        const empty = $('now-queue-empty');
        if (!list) return;
        list.replaceChildren();
        empty.textContent = queueTotal ? 'Дальше треков нет.' : 'Очередь пуста.';
        empty.hidden = paths.length > 0;

        paths.forEach((path, index) => {
            const meta = core.meta(path) || {};
            const row = document.createElement('li');
            row.className = 'now-queue-item';
            row.classList.toggle('is-next', index === 0);
            // Это представление текущего порядка, как и полная очередь.
            // Повторный sendCommand('play') пересобирает очередь из открытой
            // библиотеки и может изменить её порядок и контекст.
            const art = document.createElement('span');
            art.className = 'now-queue-art';
            art.setAttribute('aria-hidden', 'true');
            setSongCover(art, meta.cover_url);

            const text = document.createElement('span');
            text.className = 'now-queue-text';
            const title = document.createElement('span');
            title.className = 'now-queue-title';
            title.dataset.userText = 'true';
            title.textContent = meta.title || baseName(path);
            const artist = document.createElement('span');
            artist.className = 'now-queue-artist';
            if (meta.artist) artist.dataset.userText = 'true';
            artist.textContent = meta.artist || 'Исполнитель не указан';
            text.append(title, artist);

            row.append(art, text);
            list.append(row);
        });
    }

    async function refreshNowQueue() {
        const screen = $('screen-playing');
        if (!screen || screen.hidden || $('now-upcoming-queue').inert) return;
        const requestRevision = ++queueRevision;
        if (queueLoading) { queueRetry = true; return; }
        queueLoading = true;
        try {
            const response = await fetch('/api/player/queue', { cache: 'no-store', signal: AbortSignal.timeout(3000) });
            if (!response.ok) throw new Error('queue ' + response.status);
            const data = await response.json();
            if (requestRevision !== queueRevision || screen.hidden) return;
            const paths = Array.isArray(data.paths) ? data.paths : [];
            const index = Number.isInteger(data.index) ? data.index : -1;
            queueTotal = paths.length;
            const total = $('now-queue-total');
            if (total) total.textContent = paths.length ? 'Треков: ' + paths.length : '';
            syncQueueChip();
            upcomingPaths = paths.slice(index + 1, index + 6);
            renderNowQueue(upcomingPaths);
        } catch (_) {
            if (requestRevision === queueRevision && !screen.hidden) {
                upcomingPaths = [];
                const total = $('now-queue-total');
                if (total) total.textContent = '';
                const empty = $('now-queue-empty');
                if (empty) { empty.textContent = 'Не удалось загрузить очередь.'; empty.hidden = false; }
                $('now-queue-list')?.replaceChildren();
                syncQueueChip();
            }
        } finally {
            queueLoading = false;
            if (queueRetry) { queueRetry = false; void refreshNowQueue(); }
        }
    }

    // ---------- Поиск в верхней строке ----------

    function bindSearch() {
        const input = $('global-search');
        if (!input) return;
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') { input.value = ''; input.blur(); return; }
            if (event.key !== 'Enter') return;
            event.preventDefault();
            const query = input.value.trim();
            const nav = document.querySelector('.nav-button[data-screen="music"]');
            if (nav) nav.click();
            const library = $('library-search');
            if (library) {
                library.value = query;
                library.dispatchEvent(new Event('input', { bubbles: true }));
                if (query) library.focus();
            }
        });
        document.addEventListener('keydown', (event) => {
            if ((event.ctrlKey || event.metaKey) && !event.altKey && String(event.key).toLowerCase() === 'k') {
                event.preventDefault();
                if (!input.offsetParent) {
                    const nav = document.querySelector('.nav-button[data-screen="home"]');
                    if (nav) nav.click();
                    else showScreenByName('home');
                    requestAnimationFrame(() => { input.focus(); input.select(); });
                    return;
                }
                input.focus();
                input.select();
            }
        });
    }

    // ---------- Статус Music Service ----------

    function syncServiceStatus() {
        const source = $('online-service-status');
        const text = $('sidebar-service-text');
        if (!source || !text) return;
        const status = source.textContent.trim();
        if (!status) return;
        const ready = !/недоступ|unavailable/i.test(status) && /готов|доступен|работает/i.test(status);
        const starting = /запуск/i.test(status);
        text.textContent = ready ? 'Music Service работает' : starting ? 'Music Service запускается' : 'Локальный поиск';
        const box = text.closest('.sidebar-status');
        if (box) box.classList.toggle('is-on', ready);
    }

    function observeService() {
        const source = $('online-service-status');
        if (!source) return;
        new MutationObserver(syncServiceStatus).observe(source, { childList: true, characterData: true, subtree: true });
        syncServiceStatus();
    }

    // ---------- Привязки ----------

    function bindHome() {
        const open = $('home-hero-open');
        if (open) open.addEventListener('click', () => {
            const cover = $('player-cover');
            if (cover) cover.click();
            else showScreenByName('playing');
        });
        const play = $('home-hero-play');
        if (play) play.addEventListener('click', () => $('play-toggle-button')?.click());
        const all = $('home-all-tracks');
        if (all) all.addEventListener('click', () => {
            const nav = document.querySelector('.nav-button[data-screen="music"]');
            if (nav) nav.click();
        });
    }

    function bindNowScreen() {
        ['now-shuffle', 'now-prev', 'now-play', 'now-next', 'now-stop'].forEach((id) => {
            const node = $(id);
            if (!node) return;
            node.addEventListener('click', () => {
                if (id === 'now-play') { $('play-toggle-button')?.click(); return; }
                if (id === 'now-shuffle') { $('shuffle-button')?.click(); return; }
                const action = { 'now-prev': 'previous', 'now-next': 'next', 'now-stop': 'stop' }[id];
                try { void sendCommand(action); } catch (_) {}
            });
        });
        uiIcon($('now-shuffle'), 'shuffle');
        uiIcon($('now-prev'), 'previous');
        uiIcon($('now-next'), 'next');
        uiIcon($('now-stop'), 'stop');

        const back = $('now-back');
        if (back) back.addEventListener('click', () => $('back-to-tracks')?.click());

        $('now-mini')?.addEventListener('click', () => $('open-mini-player')?.click());
        uiIcon($('now-mini'), 'mini', '16px');
        const queueChip = $('now-queue-chip');
        const queuePanel = $('now-upcoming-queue');
        uiIcon(queueChip.querySelector('.now-chip-icon'), 'list', '15px');
        queueChip?.addEventListener('click', () => {
            const show = queuePanel.inert;
            setQueueVisible(show);
            if (show) void refreshNowQueue();
        });
        $('now-queue-hide')?.addEventListener('click', () => setQueueVisible(false));
        queuePanel.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                setQueueVisible(false);
            }
        });
        uiIcon($('now-queue-hide'), 'close', '14px');
        // Полный список остаётся отдельной панелью, переключатель прячет только боковую карточку.
        $('now-queue-open').addEventListener('click', (event) => openQueue(event.currentTarget));
        setQueueVisible(false);
        const more = $('now-more');
        if (more) more.addEventListener('click', () => core.details(core.currentPath()));

        const eq = $('now-eq');
        uiIcon(eq.querySelector('.now-eq-icon'), 'equalizer', '15px');
        eq.addEventListener('click', () => $('equalizer-button')?.click());
        new MutationObserver(() => eq.setAttribute('aria-expanded', String(!$('eq-panel').hidden)))
            .observe($('eq-panel'), { attributes: true, attributeFilter: ['hidden'] });

        const seek = $('seek-slider');
        const nowSeek = $('now-seek');
        if (seek && nowSeek) {
            nowSeek.addEventListener('pointerdown', () => { nowSeekEditing = true; });
            nowSeek.addEventListener('input', () => {
                nowSeekEditing = true;
                const max = Number(nowSeek.max);
                const value = Number(nowSeek.value);
                const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
                nowSeek.style.setProperty('--now-fill', percent.toFixed(2) + '%');
                const current = $('now-current-time');
                if (current) current.textContent = clock(value);
                seek.value = nowSeek.value;
                seek.dispatchEvent(new Event('input', { bubbles: true }));
            });
            nowSeek.addEventListener('change', () => {
                seek.dispatchEvent(new Event('change', { bubbles: true }));
                nowSeekEditing = false;
            });
            const cancelSeek = () => {
                nowSeekEditing = false;
                seek.dispatchEvent(new Event('pointercancel'));
                syncNowControls();
            };
            nowSeek.addEventListener('pointercancel', cancelSeek);
            nowSeek.addEventListener('blur', () => {
                if (nowSeekEditing) cancelSeek();
            });
            nowSeek.addEventListener('keydown', (event) => {
                if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) || nowSeek.disabled) return;
                event.preventDefault();
                const direction = ['ArrowRight', 'ArrowUp'].includes(event.key) ? 1 : -1;
                nowSeek.value = String(Math.max(0, Math.min(Number(nowSeek.max), Number(nowSeek.value) + direction * 5000)));
                nowSeek.dispatchEvent(new Event('input', { bubbles: true }));
                nowSeek.dispatchEvent(new Event('change', { bubbles: true }));
            });
        }

        const speed = $('now-speed');
        const speedSelect = $('eq-speed');
        if (speed && speedSelect) {
            speed.addEventListener('click', () => {
                if (speedSelect.disabled) return;
                const order = ['standard', 'speedup', 'slowed'];
                const current = order.indexOf(speedSelect.value);
                speedSelect.value = order[(current + 1) % order.length];
                speedSelect.dispatchEvent(new Event('change', { bubbles: true }));
            });
        }
    }

    // ---------- Жизненный цикл ----------

    function syncAll() {
        syncHero();
        syncNowControls();
        syncQueueChip();
    }

    function start() {
        updateGreeting();
        syncCounts();
        renderRecent();
        bindHome();
        bindNowScreen();
        bindSearch();
        observeService();
        syncAll();

        document.addEventListener('slynor-player', ({ detail }) => {
            if (detail) {
                const currentPath = core.currentPath();
                const key = JSON.stringify([currentPath, detail.index, detail.size, detail.shuffled, detail.queueRevision, detail.revision]);
                if (key !== queueStateKey) {
                    queueStateKey = key;
                    queueTotal = detail.size;
                    if (!currentPath) setQueueVisible(false);
                    void refreshNowQueue();
                }
            }
            syncAll();
        });
        document.addEventListener('slynor-library', () => {
            syncCounts();
            renderRecent();
            syncHero();
            if (document.body.dataset.screen === 'playing' && !$('now-upcoming-queue').inert && upcomingPaths.length) {
                renderNowQueue(upcomingPaths);
            }
        });
        document.addEventListener('language-changed', syncCounts);
        window.addEventListener('player-screen-changed', () => {
            syncAll();
            if (document.body.dataset.screen === 'playing') void refreshNowQueue();
            else { queueRevision++; queueRetry = false; }
        });

        setInterval(() => { if (!document.hidden) syncAll(); }, 800);
        setInterval(() => { if (!document.hidden && document.body.dataset.screen === 'home') updateGreeting(); }, 30000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
})();
