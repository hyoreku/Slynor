// Removed SoundCloud plate, clickable artist names and artist page wiring. No browser/layout claims.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
const online = fs.readFileSync(path.join(root, 'frontend/js/online.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'frontend/css/styles.css'), 'utf8');
let checks = 0;
function check(condition, name) { assert.ok(condition, name); checks++; }

// 1. The SoundCloud plate is gone from every surface and its asset is not bundled.
for (const marker of ['soundcloud-credit', 'playing-source-link', 'soundcloud-white.png', 'mini-source']) {
  check(!html.includes(marker), 'index.html has no ' + marker);
  check(!online.includes(marker), 'online.js has no ' + marker);
  check(!css.includes('.soundcloud-credit'), 'styles.css has no ' + marker);
}
check(!app.includes('updateSourceAttribution'), 'the removed-attribution feature leaves no logic behind');
check(!fs.existsSync(path.join(root, 'frontend/assets/soundcloud-white.png')), 'SoundCloud logo asset removed');
check(!fs.existsSync(path.join(root, 'frontend/assets/ATTRIBUTION.txt')), 'SoundCloud attribution note removed with the asset');

// 2. Artist links: pure functions split names for links and for track matching.
const begin = app.indexOf('function artistKey(');
const end = app.indexOf('function getArtistFiles(', begin);
assert.ok(begin >= 0 && end > begin);
const context = {String, RegExp};
vm.createContext(context);
vm.runInContext(app.slice(begin, end), context);
check(JSON.stringify(context.splitArtistNames('Пошлая Молли, Элджей')) === JSON.stringify(['Пошлая Молли', 'Элджей']), 'comma-separated artists become separate links');
check(JSON.stringify(context.splitArtistNames('Miyagi & Эндшпиль')) === JSON.stringify(['Miyagi & Эндшпиль']), 'an ampersand duo stays one artist');
check(JSON.stringify(context.artistMatchParts('Пошлая Молли feat. Элджей')) === JSON.stringify(['Пошлая Молли', 'Элджей']), 'a feat. credit matches both artists');
check(context.artistKey('  Кино   ') === 'кино', 'artist keys normalise case and spaces');

// 3. Artist screen and navigation wiring exist.
for (const id of ['screen-artist', 'artist-name', 'artist-tracks', 'artist-search-online', 'artist-back']) {
  check(html.includes('id="' + id + '"'), 'index has ' + id);
}
check(app.includes('window.openArtistPage'), 'artist page is exposed to other scripts');
check(online.includes('searchArtist'), 'online search can start from the artist page');
check(online.includes('online-search-state'), 'online search publishes state for the artist page');
check(css.includes('.artist-line .artist-link'), 'artist links are styled');

// 3b. The global playlist target is replaced by a multi-playlist picker.
for (const id of ['playlist-picker', 'playlist-picker-list', 'artist-online-tracks', 'artist-online-status']) {
  check(html.includes('id="' + id + '"'), 'index has ' + id);
}
check(!html.includes('id="online-target"'), 'the global online playlist target is gone');
check(!html.includes('id="playlist-add-target"'), 'the library playlist target is gone');
check(app.includes('add_track_to_playlists'), 'the multi-playlist bridge is used');
check(app.includes('function choosePlaylists('), 'the playlist picker exists');

// 3c. Music Service search mode is wired.
for (const id of ['online-service-mode', 'online-service-status', 'online-service-retry']) {
  check(html.includes('id="' + id + '"'), 'index has ' + id);
}
check(online.includes("serviceMode.checked?'service':'auto'"), 'service mode selects the service source');
check(online.includes('online:service:'), 'service track paths are handled');
check(online.includes('/api/service/status'), 'service status is requested');

// 4. Existing desktop fix A checks: local metadata refresh keeps artist and refreshes views.
const refreshBegin = online.indexOf('async function refreshDownloadedMetadata(');
const refreshEnd = online.indexOf('\n async function download(', refreshBegin);
assert.ok(refreshBegin >= 0 && refreshEnd > refreshBegin);
(async () => {
  let rendered = 0, polled = 0;
  const meta = {path:'C:/Music/new.opus',title:'Song',artist:'Artist',cover_url:null};
  const ctx = {metadataMap:new Map(),fileKey:v=>v,AbortSignal,
    fetch:async()=>({ok:true,json:async()=>({tracks:[meta]})}),
    renderLibrary:()=>rendered++,window:{refreshPlayerState:()=>polled++}};
  vm.createContext(ctx);vm.runInContext(online.slice(refreshBegin,refreshEnd),ctx);
  await vm.runInContext('refreshDownloadedMetadata({}, "C:/Music/new.opus")',ctx);
  check(ctx.metadataMap.get(meta.path).artist === 'Artist', 'local metadata refresh includes artist without artwork');
  check(rendered === 1 && polled === 1, 'library and player refresh without navigation');
  process.stdout.write('Artist page / badge removal: ' + checks + ' source checks passed; browser layout not exercised\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
