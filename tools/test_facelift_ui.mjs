// Regression test for a running isolated Slynor WebView2 preview.
// Usage: node tools/test_facelift_ui.mjs [http://127.0.0.1:9223]
// Uses deterministic page-only audio fixtures: never plays music or pushes Git.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const endpoint = process.argv[2] || 'http://127.0.0.1:9223';
const targets = await (await fetch(endpoint + '/json/list')).json();
const target = targets.find(t => t.type === 'page' && t.url.startsWith('http://127.0.0.1:8765/'));
assert.ok(target, 'Slynor preview page must be running');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, {once:true});
    socket.addEventListener('error', reject, {once:true});
});
let seq = 0;
const pending = new Map();
const errors = [];
socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
        const request = pending.get(message.id);
        pending.delete(message.id); clearTimeout(request.timer);
        message.error ? request.reject(new Error(JSON.stringify(message.error))) : request.resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
});
function send(method, params = {}) {
    return new Promise((resolve, reject) => {
        const id = ++seq;
        const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 20000);
        pending.set(id, {resolve, reject, timer}); socket.send(JSON.stringify({id, method, params}));
    });
}
async function evaluate(expression) {
    const response = await send('Runtime.evaluate', {expression, awaitPromise:true, returnByValue:true});
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
    return response.result.value;
}
async function until(expression) {
    return evaluate('(async()=>{const deadline=performance.now()+12000;do {if(' + expression + ')return true;await new Promise(r=>requestAnimationFrame(r));}while(performance.now()<deadline);throw new Error("UI condition timed out");})()');
}
const preferenceKey = 'slynor-now-queue-visible';
const playerPositions = `(() => ['large-track-cover','now-wave','now-seek','now-play','now-volume-slider'].map(id => {
    const {x,y,width,height} = document.getElementById(id).getBoundingClientRect();
    return [x,y,width,height];
}))()`;
let initId;
let originalPreference;
let checks = 0;
const check = (value, label) => { assert.ok(value, label); checks++; };
const out = path.join(root, '.local-artifacts', 'design', 'regression');
await mkdir(out, {recursive:true});
const fixture = String.raw`
(() => {
    const track = {path:'C:/SlynorDemo/one.mp3',title:'Северный ветер',artist:'Полярный круг',album:'Зима',duration_ms:191000,cover_url:''};
    const paths = [track.path, ...Array.from({length:5},(_,i)=>'C:/SlynorDemo/track-'+(i+2)+'.mp3')];
    window.__uiFixture = {posts:[],bars:[],paths,index:0,shuffled:false,state:'playing',volume:50,spectrumReads:0,queueReads:0};
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (input, options = {}) => {
        const url = new URL(typeof input === 'string' ? input : input.url, location.href);
        const json = data => Promise.resolve(new Response(JSON.stringify(data), {headers:{'Content-Type':'application/json'}}));
        if (url.pathname.startsWith('/api/player/') && options.method && options.method !== 'GET') {
            window.__uiFixture.posts.push(url.pathname);
            if (url.pathname === '/api/player/pause') window.__uiFixture.state = 'paused';
            if (url.pathname === '/api/player/resume') window.__uiFixture.state = 'playing';
            if (url.pathname === '/api/player/volume') window.__uiFixture.volume = JSON.parse(options.body).volume;
            return json({status:'ok',message:'fixture'});
        }
        if (url.pathname === '/api/player/state') return json({state:window.__uiFixture.state,filename:'one.mp3',file_path:track.path,current_metadata:track,volume:window.__uiFixture.volume,time_ms:63000,duration_ms:191000,can_seek:true,queue_index:window.__uiFixture.index,queue_size:paths.length,shuffle_enabled:window.__uiFixture.shuffled,rate:1,speed_mode:'standard',playback_error:null});
        if (url.pathname === '/api/player/spectrum') {
            window.__uiFixture.spectrumReads++;
            return json({file_path:track.path,state:'playing',status:'ready',bands:Array(48).fill(.6)});
        }
        if (url.pathname === '/api/player/queue') {
            window.__uiFixture.queueReads++;
            return json({paths:window.__uiFixture.paths,index:window.__uiFixture.index,shuffle_enabled:window.__uiFixture.shuffled});
        }
        return nativeFetch(input, options);
    };
    const proto = CanvasRenderingContext2D.prototype;
    const clear = proto.clearRect;
    proto.clearRect = function(...args) { if(this.canvas.id === 'spectrum-canvas') window.__uiFixture.bars=[]; return clear.apply(this,args); };
    const stroke = proto.stroke;
    proto.stroke = function(...args) { if(this.canvas.id === 'now-radial-spectrum') window.__uiFixture.radialStrokes=(window.__uiFixture.radialStrokes || 0)+1; return stroke.apply(this,args); };
    const lineTo = proto.lineTo;
    proto.lineTo = function(...args) { if(this.canvas.id === 'now-radial-spectrum') window.__uiFixture.radialLines=(window.__uiFixture.radialLines || 0)+1; return lineTo.apply(this,args); };
    const round = proto.roundRect;
    proto.roundRect = function(x,y,w,h,...rest) { if(this.canvas.id === 'spectrum-canvas') window.__uiFixture.bars.push({x,y,w,h});return round.call(this,x,y,w,h,...rest); };
    const gradient = proto.createLinearGradient;
    proto.createLinearGradient = function(...args) {
        const result = gradient.apply(this,args);
        if (this.canvas.id === 'spectrum-canvas') {
            const colors = window.__uiFixture.spectrumColors = [];
            const add = result.addColorStop.bind(result);
            result.addColorStop = (stop,color) => { colors.push(color); add(stop,color); };
        }
        return result;
    };
})();
`;
const layouts = [];
try {
    await send('Page.enable'); await send('Runtime.enable');
    originalPreference = await evaluate('localStorage.getItem(' + JSON.stringify(preferenceKey) + ')');
    await evaluate('localStorage.setItem(' + JSON.stringify(preferenceKey) + ', "true")');
    initId = (await send('Page.addScriptToEvaluateOnNewDocument', {source:fixture})).identifier;
    await send('Page.reload', {ignoreCache:true});
    await until('window.__uiFixture && document.getElementById("track-name")?.textContent === "Северный ветер"');
    await send('Emulation.setDeviceMetricsOverride', {width:1400,height:900,deviceScaleFactor:1,mobile:false});
    const queue = 'document.getElementById("now-upcoming-queue")';
    const stage = 'document.querySelector(".now-stage")';
    const chip = 'document.getElementById("now-queue-chip")';
    const settled = `${stage}.getAnimations({subtree:true}).every(a => a.playState !== "running")`;
    check(await evaluate(`(() => {
        const add = document.getElementById('add-files-button');
        return add.textContent.trim() === '' && add.getBoundingClientRect().width === add.getBoundingClientRect().height
            && Boolean(add.getAttribute('aria-label')) && getComputedStyle(document.body,'::after').content === 'none';
    })()`), 'Add control is an accessible icon button and the body has no dot layer');
    await evaluate(`playlists = [{id:'sidebar-fixture',name:'Ночная поездка',tracks:[]}]; renderSidebarPlaylists()`);
    const headerGeometry = `(() => {
        const mark = document.querySelector('#sidebar .brand-mark').getBoundingClientRect();
        const toggle = document.getElementById('sidebar-toggle');
        const {left,top,width,height,right} = toggle.getBoundingClientRect();
        const centerY = top + height / 2;
        return {left,top,width,height,right, markRight:mark.right, markMiddle:mark.top+mark.height/2,
            hitsBothSides: [left + 3, right - 3].every(x => document.elementFromPoint(x, centerY) === toggle),
            expanded:toggle.getAttribute('aria-expanded'), sidebarRight:document.getElementById('sidebar').getBoundingClientRect().right};
    })()`;
    const headerExpanded = await evaluate(headerGeometry);
    await evaluate('document.getElementById("sidebar-toggle").click()');
    await until('document.documentElement.classList.contains("sidebar-collapsed") && !document.documentElement.classList.contains("sidebar-resizing")');
    const headerCollapsed = await evaluate(headerGeometry);
    check([headerExpanded, headerCollapsed].every(box => Math.abs(box.left + box.width/2 - box.sidebarRight) < 1 &&
        Math.abs(box.top + box.height/2 - box.markMiddle) < 1 && box.hitsBothSides), 'The arrow straddles the sidebar divider and is clickable from both sides');
    check(headerCollapsed.expanded === 'false' && headerExpanded.expanded === 'true' &&
        headerCollapsed.width === 24 && headerCollapsed.height === 32 &&
        ['top','width','height','markRight','markMiddle'].every(key => Math.abs(headerExpanded[key] - headerCollapsed[key]) < 1), 'Collapse preserves the logo position and arrow size');
    check(await evaluate(`(() => {
        const centers = [...document.querySelectorAll('#sidebar .nav-icon, #sidebar .sidebar-playlist-symbol, #sidebar #new-playlist-shortcut')].map(node => {
            const {left,width} = node.getBoundingClientRect();
            return left + width / 2;
        });
        return centers.length === 6 && Math.max(...centers) - Math.min(...centers) < 1;
    })()`), 'Collapsed navigation, actions and playlist icons share the same axis');
    await evaluate('document.getElementById("sidebar-toggle").click()');
    await until('!document.documentElement.classList.contains("sidebar-collapsed") && !document.documentElement.classList.contains("sidebar-resizing")');
    const headerRestored = await evaluate(headerGeometry);
    check(['left','top','width','height'].every(key => Math.abs(headerExpanded[key] - headerRestored[key]) < 1) && headerRestored.expanded === 'true', 'Expanding restores the title without moving the arrow');
    await evaluate('showScreen("music")');
    check(await evaluate(`(() => {
        const input = document.getElementById('library-search');
        const field = input.closest('.search-field');
        return getComputedStyle(input).borderTopWidth === '0px' && getComputedStyle(input).backgroundColor === 'rgba(0, 0, 0, 0)'
            && getComputedStyle(field).borderTopWidth === '1px';
    })()`), 'Library search paints one border');
    await evaluate(`(() => {
        const [current, second] = window.__uiFixture.paths;
        libraryFiles = [current, second];
        setTrackMetadata(fileKey(second), {title:'Альфа', artist:'Исполнитель', duration_ms:2000});
        playlists[0].tracks = [second];
        playlistsReady = true;
        librarySort.value = 'original';
        refreshPlaylistSelectors();
        renderLibrary();
    })()`);
    check(await evaluate(`(() => {
        const sort = document.querySelector('[data-select-icon="sort"].styled-select > button');
        const collection = document.querySelector('.library-collection .styled-select > button');
        const search = document.querySelector('.library-toolbar .search-field');
        const {width,height} = sort.getBoundingClientRect();
        return sort.textContent === '' && sort.getAttribute('aria-label').includes(librarySort.options[0].textContent) &&
            width === height && width === 46 && collection.textContent === playlistView.options[0].textContent &&
            Math.abs(search.getBoundingClientRect().top - sort.getBoundingClientRect().top) < 1;
    })()`), 'The toolbar fits a labeled square sort icon and the current collection beside search');
    check(await evaluate(`(() => {
        const sort = document.querySelector('[data-select-icon="sort"].styled-select > button');
        const key = value => sort.dispatchEvent(new KeyboardEvent('keydown', {key:value, bubbles:true}));
        sort.focus();
        key('Enter');
        const options = [...document.querySelectorAll('.select-popup[data-select-icon="sort"] [role="option"]')];
        const initial = options.length === 4 && options[0].getAttribute('aria-selected') === 'true';
        key('ArrowDown');
        key('Enter');
        return initial && librarySort.value === 'title' && document.activeElement === sort &&
            document.querySelector('#library-list .library-item').dataset.path === window.__uiFixture.paths[1];
    })()`), 'Keyboard sorts the displayed library and restores focus to the icon');
    check(await evaluate(`(() => {
        const order = getLibraryOrder();
        const [current, second] = window.__uiFixture.paths;
        const metadata = metadataMap.get(fileKey(second));
        librarySearch.value = 'альфа';
        librarySearch.dispatchEvent(new Event('input'));
        const reuse = order === getLibraryOrder() && getOrderedLibraryFiles().length === 2 &&
            document.querySelectorAll('#library-list .library-item').length === 1;
        setTrackMetadata(fileKey(second), {...metadata, title: 'Я'});
        const updated = getLibraryOrder().paths[0] === current && getFilteredLibraryFiles(getLibraryOrder()).length === 0;
        setTrackMetadata(fileKey(second), metadata);
        librarySearch.value = '';
        librarySearch.dispatchEvent(new Event('input'));
        return reuse && updated;
    })()`), 'Queries reuse the full sorted queue and tag edits invalidate its text and order');
    check(await evaluate(`(() => {
        const collection = document.querySelector('.library-collection .styled-select > button');
        collection.click();
        document.querySelectorAll('.select-popup [role="option"]')[1].click();
        const selected = playlistView.value === 'sidebar-fixture' && document.querySelectorAll('#library-list .library-item').length === 1;
        collection.click();
        document.querySelector('.select-popup [role="option"]').click();
        return selected && playlistView.value === '' && document.querySelectorAll('#library-list .library-item').length === 2;
    })()`), 'The compact collection control switches to a playlist and back to all tracks');
    check(await evaluate(`(() => {
        const row = document.querySelector('#library-list .is-current');
        const status = row.querySelector('.library-meta .track-status');
        const previousState = currentPlaybackState;
        const height = row.offsetHeight;
        currentPlaybackState = 'paused'; highlightCurrentTrack();
        const paused = row.dataset.playbackState === 'paused' && !status.hidden && status.textContent === 'На паузе' && row.offsetHeight === height;
        currentPlaybackState = previousState; highlightCurrentTrack();
        return paused && status.textContent === 'Сейчас играет' && row.getAttribute('aria-current') === 'true' && row.offsetHeight === height;
    })()`), 'Current-track badges stay inline and keep the row height through pause and resume');
    await send('Emulation.setEmulatedMedia', {features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
    const entry = await evaluate(`(async () => {
        const cover = document.getElementById('cover-background-toggle');
        cover.checked = true;
        cover.dispatchEvent(new Event('change'));
        const mode = document.getElementById('visualizer-mode');
        mode.value = 'bars';
        mode.dispatchEvent(new Event('change'));
        document.getElementById('player-cover').click();
        const reveal = screenReveal;
        const native = document.documentElement.classList.contains('playing-reveal');
        showScreen('playing');
        const sameReveal = Boolean(reveal) && screenReveal === reveal;
        if (native && document.body.dataset.screen !== 'playing') {
            await Promise.race([
                reveal.finished,
                new Promise(resolve => window.addEventListener('player-screen-changed', resolve, {once:true}))
            ]);
        }
        const screen = document.getElementById('screen-playing');
        return {
            sameReveal,
            circleOrFallback: native
                ? getComputedStyle(document.documentElement, '::view-transition-new(root)').animationName === 'playing-window-reveal'
                : screen.getAnimations().some(animation => animation.finished === reveal?.finished),
            wrapperClip: getComputedStyle(document.querySelector('.app-shell')).clipPath,
            waveReserved: !document.getElementById('now-wave').hidden,
            atmosphereHidden: document.getElementById('cover-atmosphere').hidden,
            spectrumReads: window.__uiFixture.spectrumReads,
            queueReads: window.__uiFixture.queueReads,
            queueCount: document.getElementById('now-queue-count').textContent
        };
    })()`);
    check(entry.sameReveal && entry.circleOrFallback && entry.wrapperClip === 'none', 'One entry reveal runs on the transition layer, with an opacity fallback');
    check(entry.waveReserved && !entry.atmosphereHidden && entry.spectrumReads === 0, 'The background is visible during entry, while FFT polling waits for the reveal');
    check(entry.queueReads === 0 && entry.queueCount === '6', 'Closed queue shows the count without loading the track list');
    await until('screenReveal === null && !document.getElementById("cover-atmosphere").hidden && window.__uiFixture.bars.length===24');
    check(await evaluate('getComputedStyle(document.getElementById("sidebar-toggle")).display === "none"'), 'The sidebar divider control is hidden along with the sidebar in Now Playing');
    check(await evaluate(`${queue}.inert && getComputedStyle(document.getElementById('screen-playing'),'::after').content === 'none'`), 'Now playing starts closed even with a previous saved value and has no inset shade');
    async function showQueue(visible) {
        await evaluate(`if (${queue}.inert === ${visible}) ${chip}.click()`);
        await until(`${queue}.inert === ${!visible} && ${settled}`);
    }
    await showQueue(true);
    await until('window.__uiFixture.queueReads === 1 && document.getElementById("now-queue-list").children.length === 5');
    for (const {width, height} of [{width:1400,height:900},{width:1000,height:700},{width:800,height:600},{width:390,height:540}]) {
        for (const dpr of [1, 1.25, 2]) {
            await send('Emulation.setDeviceMetricsOverride', {width,height,deviceScaleFactor:dpr,mobile:false});
            for (const theme of ['dark', 'light', 'transparent']) {
                await evaluate('document.documentElement.dataset.theme=' + JSON.stringify(theme));
                for (const visible of [true, false]) {
                    await showQueue(visible);
                    const result = await evaluate(`(async () => {
                        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
                        const slot = document.getElementById('now-wave');
                        const canvas = document.getElementById('spectrum-canvas');
                        const {left, top, width, height} = canvas.getBoundingClientRect();
                        const sx = width / slot.clientWidth, sy = height / slot.clientHeight;
                        const bars = window.__uiFixture.bars;
                        const column = document.querySelector('.now-main').getBoundingClientRect();
                        const stage = document.querySelector('.now-stage').getBoundingClientRect();
                        const leftEdge = Math.min(...bars.map(b => b.x));
                        const rightEdge = Math.max(...bars.map(b => b.x + b.w));
                        const panel = document.getElementById('now-upcoming-queue');
                        return {
                            delta:Math.abs(left + (leftEdge + rightEdge) * sx / 2 - (column.left + column.width / 2)),
                            top:Math.min(...bars.map(b => b.y)) * sy + top,
                            bottom:Math.max(...bars.map(b => b.y + b.h)) * sy + top,
                            canvasTop:top, canvasBottom:top + height,
                            bitmapOk:canvas.width === Math.round(slot.clientWidth * devicePixelRatio),
                            hidden:getComputedStyle(panel).visibility === 'hidden',
                            expanded:document.getElementById('now-queue-chip').getAttribute('aria-expanded'),
                            column:column.width, stage:stage.width, panel:panel.getBoundingClientRect().width,
                            modal:!document.getElementById('queue-panel').hidden
                        };
                    })()`);
                    check(result.delta < 1, 'Spectrum follows its column: ' + JSON.stringify({width,dpr,theme,visible,result}));
                    check(result.top >= result.canvasTop-1 && result.bottom <= result.canvasBottom+1 && result.bitmapOk, 'Spectrum stays in the slot at the expected DPI');
                    check(result.hidden === !visible && result.expanded === String(visible), 'Queue visibility and accessible state agree');
                    check(!result.modal, 'Queue toggle leaves the full queue closed');
                    if (!visible || width <= 900) check(result.column >= result.stage-2, 'Hidden or overlay queue uses no column');
                    else check(result.column + result.panel < result.stage, 'Desktop queue occupies a column');
                    layouts.push({width,height,dpr,theme,visible,delta:+result.delta.toFixed(3)});
                }
            }
        }
    }
    await send('Emulation.setDeviceMetricsOverride',{width:1400,height:900,deviceScaleFactor:1,mobile:false});
    await evaluate(`const effect = document.getElementById('cover-background-toggle'); effect.checked = true; effect.dispatchEvent(new Event('change'))`);
    await until('document.querySelector(".atmosphere-blob").getAnimations().length === 1');
    check(await evaluate(`(() => {
        const layers = [...document.querySelectorAll('.atmosphere-blob')];
        const artwork = document.querySelector('.atmosphere-image');
        return layers.length === 3 && getComputedStyle(artwork).filter === 'none' && layers.every(layer => {
            const style = getComputedStyle(layer);
            const animation = layer.getAnimations()[0];
            const frames = animation.effect.getKeyframes();
            return style.filter === 'none' && Number(style.opacity) > .8 && Number(style.opacity) < 1 &&
                (style.backgroundImage.match(/radial-gradient/g) || []).length === 3 &&
                animation.effect.getTiming().duration < 10000 && frames.every(frame =>
                    frame.transform.includes('rotate(') && !('filter' in frame) && !('backgroundImage' in frame));
        });
    })()`), 'Soft overlapping artwork colors move by transforms and avoid fullscreen blur');
    const playCenter = await evaluate(`(() => { const {x,y,width,height} = document.getElementById('now-play').getBoundingClientRect(); return {x:x+width/2,y:y+height/2}; })()`);
    const beforeHover = await evaluate(playerPositions);
    await send('Input.dispatchMouseEvent', {type:'mouseMoved',...playCenter});
    await until('document.getElementById("now-play").matches(":hover")');
    check(await evaluate(playerPositions).then(after => after.every((box,i) => box.every((value,j) => Math.abs(value - beforeHover[i][j]) < 1))), 'Hover leaves the play button and controls in place');
    await evaluate('document.documentElement.dataset.theme="dark"');
    const accent = await evaluate(`(() => {
        const control = document.getElementById('now-play');
        const snapshot = () => ({
            control: getComputedStyle(control).backgroundImage,
            backdrop: getComputedStyle(document.body, '::before').backgroundImage,
            palette: document.getElementById('cover-atmosphere').style.getPropertyValue('--cover-color-1'),
            songMark: getComputedStyle(document.querySelector('.app-mark[data-variant="5"]')).backgroundColor,
            accentMark: getComputedStyle(document.querySelector('.app-mark[data-variant="2"]')).backgroundColor
        });
        const before = snapshot();
        applyAccent('#13865e');
        return {before, after:snapshot()};
    })()`);
    check(accent.before.control !== accent.after.control && accent.before.accentMark !== accent.after.accentMark, 'Transport and explicit accent logo follow the accent');
    check(['backdrop', 'palette', 'songMark'].every(key => accent.before[key] === accent.after[key]), 'Cover atmosphere and logo keep their palette when the accent changes');
    await until('window.__uiFixture.spectrumColors?.[0] === "#13865e"');
    await showQueue(true);
    const reversing = await evaluate(`(() => {
        ${chip}.click();
        const closing = ${queue}.inert && getComputedStyle(${queue}).visibility !== "hidden"
            && ${stage}.getAnimations({subtree:true}).some(a => a.transitionProperty === "grid-template-columns");
        ${chip}.click();
        return {closing, open:!${queue}.inert};
    })()`);
    check(reversing.closing, 'Queue starts closing visually and becomes inert immediately');
    check(reversing.open, 'Rapidly reopening cancels the previous close');
    await showQueue(false);
    check(await evaluate(`(() => {
        ${chip}.click();
        getComputedStyle(${queue}).opacity;
        return ${stage}.getAnimations({subtree:true}).some(a => a.transitionProperty === "grid-template-columns");
    })()`), 'Opening restores the desktop column with a transition');
    await showQueue(true);
    check(await evaluate(`getComputedStyle(${queue}).opacity === "1"`), 'Reversing a close restores the queue');
    await evaluate('document.getElementById("now-queue-open").click()');
    await until('!document.getElementById("queue-panel").hidden && document.activeElement.id === "close-queue-button"');
    await evaluate('document.activeElement.click()');
    await until('document.getElementById("queue-panel").hidden && document.activeElement.id === "now-queue-open"');
    check(await evaluate('document.getElementById("now-queue-open").getAttribute("aria-expanded") === "false"'), 'Full queue returns focus and resets its trigger');
    await evaluate('document.getElementById("now-eq").click()');
    await until(`!document.getElementById("eq-panel").hidden && document.getElementById("eq-panel").getBoundingClientRect().top >= document.getElementById("now-wave").getBoundingClientRect().bottom`);
    await evaluate('document.getElementById("eq-close").click()');
    await until('document.getElementById("eq-panel").hidden');
    check(await evaluate('document.activeElement.id === "now-eq"'), 'EQ leaves the spectrum visible and restores focus');
    await evaluate('window.__uiFixture.index = 2; window.__uiFixture.shuffled = true; window.refreshPlayerState()');
    await until('document.querySelector(".now-queue-title")?.textContent === "track-4.mp3"');
    check(await evaluate('document.querySelectorAll(".now-queue-item").length === 3'), 'Queue updates when index and shuffle change with the same track path');
    const playingPositions = await evaluate(playerPositions);
    const [x, y, width, height] = playingPositions[3];
    await send('Input.dispatchMouseEvent', {type:'mouseMoved', x:x + width / 2, y:y + height / 2});
    await until(`(() => {
        const button = document.getElementById('now-play');
        const fill = getComputedStyle(button, '::before');
        return button.matches(':hover') && Math.abs(new DOMMatrix(fill.transform).m41) < 1;
    })()`);
    check(await evaluate(`(() => {
        const button = document.getElementById('now-play');
        const rect = button.getBoundingClientRect();
        return getComputedStyle(button).transform === 'none' && Math.abs(rect.x - ${x}) < 1 && Math.abs(rect.y - ${y}) < 1;
    })()`), 'The accent wipe moves while the play button stays in place');
    await send('Input.dispatchMouseEvent', {type:'mouseMoved', x:1, y:1});
    await evaluate('document.getElementById("now-play").click()');
    await until('window.__uiFixture.posts.includes("/api/player/pause") && !document.getElementById("now-play").disabled && document.getElementById("large-track-state").textContent.trim().length > 0');
    const pausedPositions = await evaluate(playerPositions);
    check(playingPositions.every((position,i) => position.every((value,j) => Math.abs(value - pausedPositions[i][j]) < 1)), 'Pausing leaves cover, spectrum and transport in place');
    await evaluate('document.getElementById("now-play").click()');
    await until('window.__uiFixture.posts.includes("/api/player/resume") && document.getElementById("now-play").dataset.uiIcon === "pause" && window.__uiFixture.bars.length === 24');
    await until('document.getElementById("now-play").style.getPropertyValue("--ui-glyph-scale") !== ""');
    check(await evaluate('typeof window.anime.animate === "function" && getComputedStyle(document.getElementById("now-play")).transform === "none"'), 'The local Anime.js bundle animates the updated glyph without moving the control');
    check(await evaluate('!document.getElementById("screen-playing").hidden'), 'Pause and resume keep the Now playing screen available');
    await evaluate('document.getElementById("now-seek").dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowRight"}))');
    await until('window.__uiFixture.posts.includes("/api/player/seek")');
    await evaluate(`const slider = document.getElementById('now-volume-slider'); slider.focus(); slider.value = '63'; slider.dispatchEvent(new Event('input'));`);
    await until('window.__uiFixture.volume === 63');
    check(await evaluate(`document.getElementById('now-volume-slider').offsetWidth > 0 && document.getElementById('volume-slider').value === '63' && document.getElementById('now-volume-value').textContent === '63'`), 'Visible inline volume control updates the player immediately');
    check(await evaluate(`(async () => {
        const posted = window.__uiFixture.posts.length;
        document.getElementById('now-back').click();
        const reveal = screenReveal;
        const root = document.documentElement;
        if (typeof document.startViewTransition === 'function') {
            if (!root.classList.contains('playing-reveal-exit') || !reveal) return false;
            await Promise.race([
                reveal.finished,
                new Promise(resolve => window.addEventListener('player-screen-changed', resolve, {once:true}))
            ]);
            const previous = getComputedStyle(root, '::view-transition-old(root)');
            if (previous.animationName !== 'playing-window-reveal' || previous.animationDirection !== 'reverse' ||
                getComputedStyle(root, '::view-transition-new(root)').animationName !== 'none') return false;
            await reveal.finished;
        }
        return activeScreen === 'music' && document.activeElement.id === 'screen-title' && screenReveal === null &&
            window.__uiFixture.posts.length === posted && window.__uiFixture.state === 'playing';
    })()`), 'Back shrinks the old view, restores focus and keeps playback running');
    await evaluate('document.getElementById("player-cover").click()');
    await until('activeScreen === "playing" && screenReveal === null');
    await showQueue(true);
    await send('Emulation.setEmulatedMedia', {features:[{name:'prefers-reduced-motion',value:'reduce'}]});
    await until('document.querySelector(".atmosphere-blob").getAnimations().length === 0');
    check(await evaluate(`(() => {
        const play = document.getElementById('now-play');
        return getComputedStyle(play, '::before').transitionDuration === '0s' && !play.style.getPropertyValue('--ui-glyph-scale');
    })()`), 'Reduced motion removes the wipe transition and resets the glyph');
    const before = await evaluate('window.__uiFixture.posts.length');
    await evaluate('document.getElementById("now-queue-hide").focus();document.activeElement.click()');
    check(await evaluate(`document.activeElement.id === "now-queue-chip" && ${queue}.inert && ${settled}`), 'Hiding with reduced motion is immediate and returns focus');
    check(await evaluate('window.__uiFixture.posts.length') === before, 'Hiding queue makes no playback mutation');
    const selectMode = mode => evaluate(`(() => {
        const select = document.getElementById('visualizer-mode');
        select.value = ${JSON.stringify(mode)};
        select.dispatchEvent(new Event('change'));
    })()`);
    const toggleLiquid = enabled => evaluate(`(() => {
        const input = document.getElementById('tester-cover-liquid');
        input.checked = ${enabled};
        input.dispatchEvent(new Event('change'));
    })()`);
    const testerMode = enabled => evaluate(`(() => {
        const tester = document.getElementById('tester-mode');
        tester.checked = ${enabled};
        tester.dispatchEvent(new Event('change'));
    })()`);
    await until('settingsReady');
    // The preview may point at the user's regular desktop bridge. Keep preference writes inside the fixture.
    const settingsMethods = await evaluate(`(() => {
        const api = window.pywebview.api;
        const methods = ['save_tester_mode', 'save_visual_effects'].filter(name => typeof api[name] === 'function');
        for (const name of methods) api[name] = async () => {};
        return methods;
    })()`);
    check(settingsMethods.length === 2, 'Preview has both settings bridge methods');
    await testerMode(false);
    check(await evaluate('document.getElementById("settings-tab-tester").hidden && !document.querySelector("#settings-appearance #cover-background-toggle").disabled && document.getElementById("cover-atmosphere").dataset.coverStyle === "soft"'), 'Soft cover colours remain available in Appearance outside tester mode');
    await testerMode(true);
    await toggleLiquid(true);
    check(await evaluate(`(() => {
        const layer = document.querySelector('#cover-atmosphere .liquid-backdrop');
        const sprite = layer.firstElementChild;
        const {data} = sprite.getContext('2d').getImageData(0, sprite.height / 2, sprite.width, 1);
        const alpha = data.filter((_, i) => i % 4 === 3);
        const gradual = alpha.slice(1).every((value, i) => Math.abs(value - alpha[i]) < 20);
        return !layer.hidden && layer.children.length === 12 && gradual && Math.max(...alpha) > 100 &&
            !!layer.style.getPropertyValue('--liquid-ambient') &&
            getComputedStyle(sprite).animationPlayState === 'paused';
    })()`), 'Liquid backgrounds use twelve soft shapes with a cover palette and respect reduced motion');
    const visualizerMatchesCover = `(() => {
        const canvas = document.getElementById('now-radial-spectrum');
        const artwork = document.getElementById('large-track-cover');
        const cover = artwork.getBoundingClientRect();
        const ring = canvas.getBoundingClientRect();
        return canvas.parentElement === artwork.parentElement && !canvas.hidden && document.getElementById('now-wave').hidden &&
            ring.width > cover.width && ring.height > cover.height &&
            Math.abs(cover.left + cover.width / 2 - ring.left - ring.width / 2) < 1 &&
            Math.abs(cover.top + cover.height / 2 - ring.top - ring.height / 2) < 1 &&
            getComputedStyle(canvas).pointerEvents === 'none';
    })()`;
    let waveReach;
    for (const mode of ['wave', 'rays']) {
        const beforeStrokes = await evaluate('window.__uiFixture.radialStrokes || 0');
        await selectMode(mode);
        await until('window.__uiFixture.radialStrokes > ' + beforeStrokes);
        check(await evaluate(visualizerMatchesCover), 'The ' + mode + ' canvas follows the cover when the queue is closed');
        const reach = await evaluate(`(() => {
            const canvas = document.getElementById('now-radial-spectrum');
            const cover = document.getElementById('large-track-cover');
            const scale = canvas.width / canvas.clientWidth;
            const inset = Math.floor((canvas.width - cover.offsetWidth * scale) / 2);
            const {data} = canvas.getContext('2d').getImageData(0, 0, inset, canvas.height);
            let left = inset;
            for (let pixel = 0; pixel < data.length / 4; pixel++) {
                if (data[pixel * 4 + 3] > 48) left = Math.min(left, pixel % inset);
            }
            return (inset - left) / scale;
        })()`);
        if (mode === 'wave') waveReach = reach;
        else {
            check(reach > waveReach * 1.3, 'Contours extend farther out from the cover than Waves');
            check(await evaluate('!window.__uiFixture.radialLines'), 'Contours are drawn without separate rays');
        }
        check(await evaluate(`(() => {
            const canvas = document.getElementById('now-radial-spectrum');
            const cover = document.getElementById('large-track-cover');
            const ctx = canvas.getContext('2d');
            const inset = Math.round((canvas.width - cover.offsetWidth * canvas.width / canvas.clientWidth) / 2);
            const center = ctx.getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data[3] === 0;
            const band = ctx.getImageData(inset - 1, Math.round(canvas.height * .24), 3, Math.round(canvas.height * .52)).data;
            return center && band.some((value, i) => i % 4 === 3 && value > 12);
        })()`), 'The ' + mode + ' leaves the center clear and touches the cover edge');
        await showQueue(true);
        check(await evaluate(visualizerMatchesCover), 'The ' + mode + ' canvas follows the cover when the queue is open');
        await showQueue(false);
    }
    await selectMode('off');
    const beforeReads = await evaluate('window.__uiFixture.spectrumReads');
    check(await evaluate(`(async () => {
        await new Promise(resolve => setTimeout(resolve, 140));
        return document.getElementById('now-wave').hidden && document.getElementById('now-radial-spectrum').hidden &&
            window.__uiFixture.spectrumReads === ${beforeReads};
    })()`), 'Off hides both canvases and stops spectrum reads');
    await send('Emulation.setEmulatedMedia', {features:[]});
    await until('getComputedStyle(document.querySelector(".liquid-backdrop > .liquid-shape")).animationPlayState === "running"');
    await testerMode(false);
    check(await evaluate('document.querySelector(".liquid-backdrop").hidden && document.getElementById("tester-cover-liquid").checked'), 'Turning off tester mode hides the liquid layer and retains the experiment choice');
    await testerMode(true);
    await toggleLiquid(false);
    check(await evaluate('document.querySelector(".liquid-backdrop").hidden'), 'Switching back to soft colours hides the liquid layer');
    await showQueue(true);
    await send('Page.reload',{ignoreCache:true});
    await until('window.__uiFixture && document.getElementById("track-name")?.textContent === "Северный ветер"');
    await selectMode('bars');
    await evaluate('document.getElementById("player-cover").click()');
    await until('window.__uiFixture.bars.length===24');
    check(await evaluate(`${queue}.inert`), 'A new app session starts with the queue closed');
    await writeFile(path.join(out,'queue-hidden.png'),Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await showQueue(true);
    await writeFile(path.join(out,'queue-visible.png'),Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
    check(await evaluate('getComputedStyle(document.querySelector(".brand-mark")).backgroundImage === "none"'), 'Logo has no colored background tile');
    check(errors.length===0, 'No uncaught JavaScript errors: ' + errors.join('; '));
    await writeFile(path.join(out,'results.json'),JSON.stringify({checks,layouts,errors},null,2));
    console.log(JSON.stringify({ok:true,checks,layoutCases:layouts.length,maxCenterErrorPx:Math.max(...layouts.map(x=>x.delta)),report:path.relative(root,path.join(out,'results.json'))}));
} finally {
    if(initId) await send('Page.removeScriptToEvaluateOnNewDocument',{identifier:initId});
    await evaluate('(()=>{const k=' + JSON.stringify(preferenceKey) + ',v=' + JSON.stringify(originalPreference ?? null) + ';v===null?localStorage.removeItem(k):localStorage.setItem(k,v);})()').catch(()=>{});
    await send('Emulation.setEmulatedMedia', {features:[]}).catch(()=>{});
    await send('Emulation.clearDeviceMetricsOverride').catch(()=>{});
    await send('Page.reload',{ignoreCache:true}).catch(()=>{});
    socket.close();
}
