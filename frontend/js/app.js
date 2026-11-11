let menuAnimation = null, menuRevision = 0;
// КАРТА ФАЙЛА: библиотека → команды → состояние/громкость → плейлисты → навигация/очередь → изображения.
// Ищите название функции через Ctrl+F; DOM-элементы связываются с id из index.html.

// 1. Элементы HTML и состояние общей библиотеки.
const pathInput = document.getElementById("file-path");
const result = document.getElementById("result");
const buttons = document.querySelectorAll("[data-action]");
const playToggleButton = document.getElementById("play-toggle-button");
const playerArtist = document.getElementById("player-artist");
const playerCover = document.getElementById("player-cover");
const playerBar = document.getElementById("player-bar");
const appShell = document.querySelector(".app-shell");

const chooseFileButton =
    document.getElementById("choose-file-button");
const addFilesButton = document.getElementById("add-files-button");
const libraryList = document.getElementById("library-list");
const libraryCount = document.getElementById("library-count");
const libraryEmpty = document.getElementById("library-empty");
const librarySearch = document.getElementById("library-search");
const librarySort = document.getElementById("library-sort");
const refreshMetadataButton = document.getElementById("refresh-metadata-button");
const textCollator = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });
let libraryReady = false;
let commandBusy = false;
let playbackRevision = 0;
let trackMenuAnchor = null;

const playlistView = document.getElementById("playlist-view");
let activePlaylistId = "";
let activeArtistName = "";
let artistReturn = null;
let artistOnlineKey = "";
let playbackLibraryContext = null;
function captureLibraryContext() {
    return {kind: activeArtistName ? "artist" : "library", artist: activeArtistName,
        playlistId: activePlaylistId, query: librarySearch.value,
        sort: librarySort.value, scrollTop: document.querySelector('.workspace').scrollTop};
}
function restorePlaybackLibraryContext() {
    if (!playbackLibraryContext) return;
    const context = playbackLibraryContext;
    if (context.kind === "online") {window.onlineMusic?.restore(context); return;}
    if (context.kind === "artist") {
        activeArtistName = context.artist || "";
        renderArtistPage();
        return;
    }
    activeArtistName = "";
    activePlaylistId = playlists.some(p => p.id === context.playlistId) ? context.playlistId : "";
    librarySearch.value = context.query;
    librarySort.value = context.sort;
    refreshPlaylistSelectors();
    renderLibrary();
}

const libraryFiles = [];
const metadataMap = new Map();

let metadataLoading = false;
let metadataReloadRequested = false;
let reloadCoverCache = false;
let currentTrackPath = null;
let currentPlaybackState = "idle";
window.playingScreenEnabled = true;
let screenRevealAnimation = null;
let libraryInitStarted = false;
let libraryBusy = false;

// Одинаковый ключ для сравнения путей Windows: слеши и регистр.

function fileKey(path) {
    return path.replaceAll("\\", "/").toLowerCase();
}

// Имя файла без полного пути — запасное название песни.

function fileName(path) {
    return path.replaceAll("\\", "/").split("/").pop();
}
// Сохранение общей библиотеки через Python-мост. Файлы музыки не копируются.
async function saveLibrary(nextFiles) {
    if (libraryBusy) {
        throw new Error(
            "Список уже сохраняется. Повтори действие через секунду."
        );
    }

    libraryBusy = true;

    try {
        await window.pywebview.api.save_library(nextFiles);

        libraryFiles.splice(
            0,
            libraryFiles.length,
            ...nextFiles
        );

        renderLibrary();
        void loadLibraryMetadata();
    } finally {
        libraryBusy = false;
    }
}
// Добавление новых файлов без дублей и переход в общую библиотеку.
async function addFilesToLibrary(paths, {navigate = true} = {}) {
    const nextFiles = [...libraryFiles];

    const existingKeys = new Set(
        nextFiles.map((path) => fileKey(path))
    );

    let addedCount = 0;

    for (const path of paths) {
        const key = fileKey(path);

        if (!existingKeys.has(key)) {
            nextFiles.push(path);
            existingKeys.add(key);
            addedCount += 1;
        }
    }

    if (addedCount > 0) {
        await saveLibrary(nextFiles);
    }

    // Background online downloads must not steal the user's current screen.
    if (navigate) {
        activePlaylistId = "";
        refreshPlaylistSelectors();
        renderLibrary();
        showScreen("music");
    }
    return addedCount;
}

// Подсветка текущей песни и подпись «Сейчас играет / На паузе».

function highlightCurrentTrack() {
    const activeStates = new Set([
        "opening",
        "buffering",
        "playing",
        "paused"
    ]);

    const canHighlight =
        currentTrackPath &&
        activeStates.has(currentPlaybackState);

    const items = libraryList.querySelectorAll(".library-item");

    items.forEach((item) => {
        const isCurrent = Boolean(
            canHighlight &&
            fileKey(item.dataset.path) === fileKey(currentTrackPath)
        );

        item.classList.toggle("is-current", isCurrent);
        let badge = item.querySelector(".track-status");
        if (isCurrent) {
            if (!badge) {
                badge = document.createElement("span");
                badge.className = "track-status";
                item.querySelector(".library-info").append(badge);
            }
            badge.textContent = currentPlaybackState === "paused" ? "На паузе"
                : currentPlaybackState === "playing" ? "Сейчас играет" : "Открываем трек";
        } else if (badge) badge.remove();

        if (isCurrent) {
            item.setAttribute("aria-current", "true");
        } else {
            item.removeAttribute("aria-current");
        }
    });
}

// Загрузка названий, исполнителей, длительности и URL обложек пачками по 100.

async function loadLibraryMetadata() {
    if (metadataLoading) {
        metadataReloadRequested = true;
        return;
    }

    metadataLoading = true;
    refreshMetadataButton.disabled = true;

    try {
        do {
            metadataReloadRequested = false;

            const pendingPaths = getAllKnownFiles().filter(
                (path) => !metadataMap.has(fileKey(path))
            );

            for (
                let offset = 0;
                offset < pendingPaths.length;
                offset += 100
            ) {
                const batch = pendingPaths.slice(
                    offset,
                    offset + 100
                );

                const response = await fetch(
                    "/api/library/metadata",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify({
                            paths: batch,
                            refresh_covers: reloadCoverCache
                        }),
                        signal: AbortSignal.timeout(30000)
                    }
                );

                if (!response.ok) {
                    throw new Error(
                        `Сервер вернул код ${response.status}`
                    );
                }

                const data = await response.json();
                reloadCoverCache = false;

                for (const track of data.tracks) {
                    metadataMap.set(
                        fileKey(track.path),
                        track
                    );
                }

                renderLibrary();
                if (activeArtistName && activeScreen === "artist") renderArtistPage();
            }
        } while (metadataReloadRequested);
    } catch (error) {
        result.textContent =
            "Не удалось загрузить метаданные: " +
            error.message;
    } finally {
        metadataLoading = false;
        refreshMetadataButton.disabled = !libraryReady;
    }
}

        // Нормализация текста поиска: регистр, пробелы, ё → е.

        function normalizeSearchText(value) {
    return String(value || "")
        .toLowerCase()
        .replaceAll("ё", "е")
        .trim();
}

// Полный состав выбранного плейлиста или общей библиотеки.

function getActiveLibraryFiles() {
    if (activeArtistName) return getArtistFiles(activeArtistName);
    if (!activePlaylistId) return [...libraryFiles];
    const playlist = playlists.find((p) => p.id === activePlaylistId);
    return playlist ? [...(playlist.tracks || [])] : [];
}

// Все известные пути для чтения метаданных, включая вручную запущенный файл.

function getAllKnownFiles() {
    const paths = [...libraryFiles, ...playlists.flatMap((p) => p.tracks || []), ...(currentTrackPath ? [currentTrackPath] : [])];
    const unique = new Map();
    paths.forEach((path) => unique.set(fileKey(path), path));
    return [...unique.values()];
}

// Сортировка полного списка. Поиск не участвует в построении очереди.

function getOrderedLibraryFiles() {
    const ordered = getActiveLibraryFiles();
    const mode = librarySort.value;
    if (mode === "original") return ordered;

    function titleFor(path) {
        return metadataMap.get(fileKey(path))?.title || fileName(path);
    }

    ordered.sort((left, right) => {
        const a = metadataMap.get(fileKey(left));
        const b = metadataMap.get(fileKey(right));

        if (mode === "artist") {
            const artistA = a?.artist || "";
            const artistB = b?.artist || "";
            if (Boolean(artistA) !== Boolean(artistB)) return artistA ? -1 : 1;
            const artistOrder = textCollator.compare(artistA, artistB);
            if (artistOrder) return artistOrder;
        }

        if (mode === "duration") {
            const durationA = a?.duration_ms;
            const durationB = b?.duration_ms;
            const knownA = Number.isFinite(durationA);
            const knownB = Number.isFinite(durationB);
            if (knownA !== knownB) return knownA ? -1 : 1;
            if (knownA && durationA !== durationB) return durationA - durationB;
        }

        return textCollator.compare(titleFor(left), titleFor(right));
    });
    return ordered;
}

// Фильтр только для отображения: все слова запроса должны встретиться в тегах.

function getFilteredLibraryFiles() {
    const query = normalizeSearchText(librarySearch.value);
    const orderedFiles = getOrderedLibraryFiles();

    if (!query) {
        return orderedFiles;
    }

    const words = query.split(/\s+/);

    return orderedFiles.filter((path) => {
        const metadata = metadataMap.get(fileKey(path));

        const searchableText = normalizeSearchText([
            metadata?.title,
            metadata?.artist,
            metadata?.album,
            fileName(path)
        ].filter(Boolean).join(" "));

        return words.every(
            (word) => searchableText.includes(word)
        );
    });
}

// ОТОБРАЖЕНИЕ СПИСКА ПЕСЕН: создание строк, кнопок, миниатюр и меню.

function renderLibrary() {
    closeTrackMenu(false);
    // Keep the previous extent until the replacement window is ready; clearing
    // first clamps scrollTop during asynchronous metadata refreshes.
    const visibleFiles = getFilteredLibraryFiles();
    const sourceFiles = getActiveLibraryFiles();
    const activePlaylist = playlists.find((p) => p.id === activePlaylistId);
    document.getElementById("library-heading").textContent = activePlaylist
        ? `Плейлист: ${activePlaylist.name}`
        : "Локальные треки";
    renderPlaylistSummary(activePlaylist);
    const hasQuery = librarySearch.value.trim().length > 0;

    libraryCount.textContent = hasQuery
        ? `Найдено: ${visibleFiles.length} из ${sourceFiles.length}`
        : `Треков в списке: ${sourceFiles.length}`;

    libraryEmpty.hidden = visibleFiles.length > 0;

    libraryEmpty.textContent = sourceFiles.length === 0
        ? (activePlaylistId
            ? "Плейлист пуст. Переключись на все локальные треки и добавь песни кнопкой «В плейлист»."
            : "Список пуст. Нажми «Добавить музыку».")
        : "По этому запросу ничего не найдено.";

    document.getElementById("track-columns").hidden = visibleFiles.length === 0;
    const orderedFiles = getOrderedLibraryFiles();
    const positions = new Map(orderedFiles.map((path, index) => [fileKey(path), index + 1]));
    libraryWindowState={files:visibleFiles,positions,first:-1,last:-1};
    renderLibraryWindow(true);
    updateHomeOverview();
    renderSidebarPlaylists();
}

let libraryWindowState={files:[],positions:new Map(),first:-1,last:-1};
let libraryWindowRaf=null;
function renderLibraryWindow(force=false) {
    const workspace=document.querySelector('.workspace');
    const visibleFiles=libraryWindowState.files,positions=libraryWindowState.positions;
    const virtual=visibleFiles.length>150;
    const step=innerWidth<=560?128:96; // CSS row height plus 8px gap.
    const top=libraryList.getBoundingClientRect().top-workspace.getBoundingClientRect().top+workspace.scrollTop;
    const first=virtual?Math.min(Math.max(0,visibleFiles.length-1),Math.max(0,Math.floor((workspace.scrollTop-top)/step)-6)):0;
    const last=virtual?Math.min(visibleFiles.length,first+Math.ceil(workspace.clientHeight/step)+14):visibleFiles.length;
    if(!force&&first===libraryWindowState.first&&last===libraryWindowState.last)return;
    libraryWindowState.first=first;libraryWindowState.last=last;
    closeTrackMenu(false);
    libraryList.style.paddingTop=virtual?`${first*step}px`:'';
    libraryList.style.paddingBottom=virtual?`${(visibleFiles.length-last)*step}px`:'';
    const fragment=document.createDocumentFragment();
    for (const path of visibleFiles.slice(first, last)) {
        // Одна строка песни: запуск → картинка → информация → альбом → время → ⋯.
        const item = document.createElement("li");
        item.className = "library-item";
        item.dataset.path = path;

        const info = document.createElement("div");
        info.className = "library-info";

        const metadata = metadataMap.get(fileKey(path));
        // Миниатюра песни берётся из её тегов; это НЕ обложка плейлиста.
        const artwork = document.createElement("div");
        artwork.className = "song-art";
        artwork.setAttribute("aria-hidden", "true");
        artwork.textContent = "♫";
        setSongCover(artwork, metadata?.cover_url);

        const name = document.createElement("span");
        name.className = "library-name"; name.dataset.userText = "true";
        name.textContent =
            metadata?.title || fileName(path);

        const details = document.createElement("span");
        details.className = "library-details"; details.dataset.userText = "true";

        details.textContent = !metadata ? window.i18n.t("Читаем сведения...")
            : metadata.error ? window.i18n.t(metadata.error)
            : metadata.artist || window.i18n.t("Исполнитель не указан");
        details.title = details.textContent;
        name.title = name.textContent;

        const album = document.createElement("span");
        album.className = "track-album"; album.dataset.userText = "true";
        album.textContent = metadata?.album || "—";
        album.title = metadata?.album || window.i18n.t("Альбом не указан");
        const duration = document.createElement("span");
        duration.className = "track-duration";
        duration.textContent = Number.isFinite(metadata?.duration_ms)
            ? formatTime(metadata.duration_ms) : "—";
        duration.setAttribute("aria-label", "Длительность: " + duration.textContent);

        const fullPath = document.createElement("span");
        fullPath.className = "library-path";
        fullPath.textContent = path;

        // Кнопка запуска именно этой песни; очередь формируется в sendCommand().
        const playButton = document.createElement("button");
        playButton.type = "button";
        playButton.className = "track-play";
        playButton.innerHTML = '<span class="track-number" aria-hidden="true"></span><span class="track-play-symbol" aria-hidden="true">▶</span>';
        playButton.querySelector(".track-number").textContent = String(positions.get(fileKey(path)));
        playButton.setAttribute("aria-label", "Воспроизвести: " + name.textContent);
        playButton.title = "Воспроизвести: " + name.textContent;
        playButton.dataset.playTrack = "true";
        playButton.disabled = commandBusy;

        playButton.addEventListener("click", async () => {
            pathInput.value = path;
            playButton.disabled = true;

            try {
                await sendCommand("play");
            } finally {
                playButton.disabled = false;
            }
        });

        // Удаление записи из текущего списка, а не файла на диске.
        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "remove-button";
        removeButton.textContent = activePlaylistId
            ? "Убрать из плейлиста" : "Убрать из библиотеки";

        removeButton.addEventListener("click", async () => {
            if (activePlaylistId) {
                const playlistId = activePlaylistId;
                await mutatePlaylists(
                    () => window.pywebview.api.remove_track_from_playlist(playlistId, path),
                    "Песня убрана из плейлиста. Файл на диске не удалён."
                );
                return;
            }
            removeButton.disabled = true;

            try {
                const nextFiles = libraryFiles.filter(
                    (itemPath) => itemPath !== path
                );

                await saveLibrary(nextFiles);

                result.textContent =
                    "Трек убран из списка. Файл на диске не удалён.";
            } catch (error) {
                result.textContent =
                    "Не удалось сохранить удаление: " + error.message;
            } finally {
                removeButton.disabled = false;
            }
        });

        const addToPlaylistButton = document.createElement("button");
        addToPlaylistButton.type = "button";
        addToPlaylistButton.textContent = "В плейлисты…";
        addToPlaylistButton.disabled = !playlistsReady || playlistsBusy || !playlists.length;
        addToPlaylistButton.addEventListener("click", async () => {
            const ids = await choosePlaylists(name.textContent);
            if (ids.length) await addPathToPlaylists(path, ids);
        });

        const actionsButton = document.createElement("button");
        actionsButton.type = "button";
        actionsButton.className = "track-actions-button";
        actionsButton.textContent = "⋯";
        actionsButton.title = "Действия с песней";
        actionsButton.setAttribute("aria-label", "Действия: " + name.textContent);
        actionsButton.setAttribute("aria-haspopup", "menu");
        actionsButton.setAttribute("aria-expanded", "false");
        const infoButton = document.createElement("button");
        infoButton.type = "button";
        infoButton.textContent = "Сведения о файле";
        infoButton.addEventListener("click", () => showTrackDetails(path));
        actionsButton.addEventListener("click", () => {
            openTrackMenu(actionsButton, [addToPlaylistButton, infoButton, removeButton]);
        });
        info.append(name, details, fullPath);
        item.addEventListener('click', event => {
            if (event.target.closest('button,a,input,select,textarea') || commandBusy) return;
            if (window.getSelection()?.toString()) return;
            playButton.click();
        });
        item.append(playButton, artwork, info, album, duration, actionsButton);
        item.setAttribute('aria-setsize', String(visibleFiles.length));
        item.setAttribute('aria-posinset', String(first+fragment.childElementCount+1));
        fragment.append(item);
    }

    libraryList.replaceChildren(fragment);
    highlightCurrentTrack();
}
function scheduleLibraryWindow() {
    if(libraryWindowRaf!==null)return;
    libraryWindowRaf=requestAnimationFrame(()=>{libraryWindowRaf=null;if(activeScreen==='music')renderLibraryWindow();});
}
document.querySelector('.workspace').addEventListener('scroll',scheduleLibraryWindow,{passive:true});
window.addEventListener('resize',()=>{libraryWindowState.first=-1;scheduleLibraryWindow();});
window.addEventListener('player-screen-changed',scheduleLibraryWindow);

// Защита от одновременного выполнения нескольких команд плеера.
function setPlaybackControlsBusy(busy) {
    playbackRevision += 1; // Ответы опроса до/во время команды могут уже устареть.
    commandBusy = busy;
    playToggleButton.disabled = busy || window.onlinePreparing || ["opening","buffering"].includes(currentPlaybackState);
    buttons.forEach((button) => { button.disabled = busy; });
    libraryList.querySelectorAll("[data-play-track]").forEach((button) => {
        button.disabled = busy;
    });
    shuffleButton.disabled = busy || shufflePending ||
        (!shuffleEnabled && getActiveLibraryFiles().length === 0);
}

// Отправка команды в FastAPI. При запуске берём весь отсортированный список.

// Large queues are uploaded in small byte-bounded portions, not an unbounded
// JSON body. The server also enforces a memory budget and expires abandoned jobs.
async function uploadLargeQueue(paths,index,shuffle=false) {
    let id=null;
    const request=async(url,body)=>{
        const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(10000)});
        if(!response.ok){const data=await response.json();throw new Error(typeof data.detail==='string'?data.detail:'Ошибка подготовки очереди');}
        return response;
    };
    try {
        id=(await (await request('/api/player/queue/uploads',{})).json()).id;
        const encoder=new TextEncoder();let offset=0;
        while(offset<paths.length){
            const chunk=[];let bytes=0;
            while(offset+chunk.length<paths.length&&chunk.length<1000){
                const path=paths[offset+chunk.length],size=encoder.encode(path).length+64;
                if(chunk.length&&bytes+size>256*1024)break;
                chunk.push(path);bytes+=size;
            }
            await request(`/api/player/queue/uploads/${id}/chunks`,{offset,paths:chunk});offset+=chunk.length;
        }
        const response=await request(`/api/player/queue/uploads/${id}/commit`,{expected_count:paths.length,index,shuffle});
        id=null;return response;
    } finally {
        if(id)void fetch(`/api/player/queue/uploads/${id}`,{method:'DELETE',signal:AbortSignal.timeout(2000)}).catch(()=>{});
    }
}

async function sendCommand(action) {
    if (commandBusy || (window.onlinePreparing && !["stop","next","previous"].includes(action)) || (["pause","resume"].includes(action) && !["playing","paused"].includes(currentPlaybackState))) return;
    const path = pathInput.value.trim().replace(/^"|"$/g, "");

    if (action === "play" && !path) {
        result.textContent = "Сначала введи путь к файлу.";
        pathInput.focus();
        return;
    }

    // Отключение кнопки Stop может снять фокус: запоминаем его заранее.
    if (["play","stop","next","previous"].includes(action)) window.dispatchEvent(new Event("ownplayer-user-command"));
    const nextLibraryContext = action === "play" ? captureLibraryContext() : null;
    const restoreAfterStop = action === "stop" &&
        (playerBar.contains(document.activeElement) || queuePanel.contains(document.activeElement));
    if (['play','stop','next','previous'].includes(action)) cancelQueuedSeeks();
    setPlaybackControlsBusy(true);

    if (["play","next","previous"].includes(action)) currentPlaybackState="opening";
    result.textContent = "Отправляем команду...";

    try {
        let url = `/api/player/${action}`;
        let preparedResponse=null;

        const options = {
            method: "POST",
            signal: AbortSignal.timeout(10000)
        };

        if (action === "play") {
            const orderedPaths = getOrderedLibraryFiles();
            const selectedIndex = orderedPaths.findIndex(
                (itemPath) => fileKey(itemPath) === fileKey(path)
            );

            const queuePaths = selectedIndex >= 0
                ? orderedPaths
                : [path];

            const queueIndex = selectedIndex >= 0
                ? selectedIndex
                : 0;

            if(queuePaths.length>10000)preparedResponse=await uploadLargeQueue(queuePaths,queueIndex);
            url = "/api/player/queue/play";

            options.headers = {
                "Content-Type": "application/json"
            };

            options.body = JSON.stringify({
                paths: queuePaths,
                index: queueIndex
            });
        }

        const response = preparedResponse || await fetch(url, options);
        const data = await response.json();

        if (!response.ok) {
            const message = typeof data.detail === "string"
                ? data.detail
                : `Ошибка запроса: ${response.status}`;

            throw new Error(message);
        }

        result.textContent = data.message;
        if (action === "pause" || action === "resume") window.playerVisuals?.playbackChanged(action === "pause" ? "paused" : "playing");
        if (action === "pause" || action === "resume") window.playerMotion?.playback(action === "pause" ? "paused" : "playing");
        if (action === "play") {
            playbackLibraryContext = nextLibraryContext;
            revealPlayingOnPoll = true;
        }
        if (action === "stop") {
            // После подтверждения сервера скрываем без ожидания секундного опроса.
            window.playerVisuals?.stop();
            window.playerMotion?.stop();
            revealPlayingOnPoll = false;
            currentTrackPath = null;
            playbackLibraryContext = null;
            setSongCover(largeTrackCover, null);
            currentPlaybackState = "idle";
            highlightCurrentTrack();
            setSongCover(playerCover, null);
            setPlayerVisible(false);
            if (restoreAfterStop) screenTitle.focus({ preventScroll: true });
        }
    } catch (error) {
        result.textContent = "Ошибка: " + error.message;
    } finally {
        setPlaybackControlsBusy(false);
        void refreshPlayerState();
    }
}

buttons.forEach((button) => {
    button.addEventListener("click", () => {
        sendCommand(button.dataset.action);
    });
});
// Первоначальная загрузка сохранённой библиотеки после готовности PyWebView.
async function enableFilePicker() {
    if (libraryInitStarted) {
        return;
    }

    libraryInitStarted = true;

    try {
        const savedFiles =
            await window.pywebview.api.load_library();

        libraryFiles.splice(
            0,
            libraryFiles.length,
            ...savedFiles
        );

        libraryReady = true;
        renderLibrary();

        chooseFileButton.disabled = false;
        addFilesButton.disabled = false;

        result.textContent = savedFiles.length > 0
            ? `Список загружен. Треков: ${savedFiles.length}.`
            : "Добавь музыкальные файлы, чтобы начать.";
            void loadLibraryMetadata();
    } catch (error) {
        result.textContent =
            "Не удалось загрузить библиотеку: " + error.message;

        // Не разрешаем добавление, чтобы случайно
        // не перезаписать повреждённый файл новым списком.
        chooseFileButton.disabled = true;
        addFilesButton.disabled = true;
    }
}

window.addEventListener("pywebviewready", enableFilePicker);

// На случай, если мост уже готов к моменту выполнения скрипта.
if (window.pywebview && window.pywebview.api) {
    enableFilePicker();
}

chooseFileButton.addEventListener("click", async () => {
    chooseFileButton.disabled = true;

    try {
        const selectedPath =
            await window.pywebview.api.choose_audio_file();

        if (selectedPath) {
            pathInput.value = selectedPath;
            await addFilesToLibrary([selectedPath]);

            result.textContent =
                "Файл выбран и добавлен в список. " +
                "Нажми «Воспроизвести».";
        }
    } catch (error) {
        result.textContent =
            "Не удалось открыть выбор файла: " + error.message;
    } finally {
        chooseFileButton.disabled = false;
    }
});

// 2. Элементы нижнего плеера и состояние регуляторов.
const playerState = document.getElementById("player-state");
const trackName = document.getElementById("track-name");
const queuePosition = document.getElementById("queue-position");
const shuffleButton = document.getElementById("shuffle-button");

let shuffleEnabled = false;
let shufflePending = false;
const playbackError = document.getElementById("playback-error");
const actualVolume = document.getElementById("actual-volume");

const volumeSlider = document.getElementById("volume-slider");
const volumeValue = document.getElementById("volume-value");
let pendingVolume = null;
let volumeRequestBusy = false;
let volumeTimer = null;
let volumeDragging = false;
let volumeRevision = 0;

const stateLabels = {
    idle: "Ожидание",
    opening: "Открываем файл",
    buffering: "Буферизация",
    playing: "Играет",
    paused: "Пауза",
    stopped: "Остановлен",
    ended: "Трек завершён",
    error: "Ошибка VLC",
    unknown: "Неизвестное состояние"
};
    const currentTime = document.getElementById("current-time");
    const durationTime = document.getElementById("duration-time");
    const seekSlider = document.getElementById("seek-slider");

    let isEditingSeek = false;
    let seekPending = false;

    // Преобразование миллисекунд в минуты:секунды для интерфейса.

    function formatTime(milliseconds) {
        const totalSeconds = Math.floor(
            Math.max(0, milliseconds) / 1000
        );

        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;

        return (
            String(minutes).padStart(2, "0") +
            ":" +
            String(seconds).padStart(2, "0")
        );
    }

    // ОПРОС СОСТОЯНИЯ: каждую секунду обновляет плеер, обложку, время и громкость.

    let statePollTimer = null, statePollBusy = false, statePollController = null, stateRefreshQueued = false;
    window.refreshPlayerState = () => {
        clearTimeout(statePollTimer);
        if (statePollBusy) { stateRefreshQueued = true; statePollController?.abort(); }
        else void updatePlayerState();
    };
    async function updatePlayerState() {
    if (statePollBusy) return;
    statePollBusy = true; clearTimeout(statePollTimer);
    statePollController = new AbortController();
    const requestedSeekRevision = seekRevision;
    const timeout = setTimeout(() => statePollController?.abort(), 3000);
    try {
        const requestedVolumeRevision = volumeRevision;
        const requestedPlaybackRevision = playbackRevision;
        const response = await fetch("/api/player/state", {
            cache: "no-store",
            signal: statePollController.signal
        });

        if (!response.ok) {
            throw new Error(`Ошибка сервера: ${response.status}`);
        }

        const data = await response.json();
        if (typeof data.window_visibility?.main === 'boolean' && window.__mainVisible !== data.window_visibility.main) {
            window.__mainVisible = data.window_visibility.main;
            window.dispatchEvent(new CustomEvent('native-window-state', {detail:{minimized:!window.__mainVisible}}));
        }
        if (data.window_visibility?.main === false) return;
        if (commandBusy || requestedPlaybackRevision !== playbackRevision) return;
        currentTrackPath = data.file_path || null;
        currentPlaybackState = data.state;
        // The server may enrich artwork/title after an internet file is prepared.
        // Do not keep an earlier incomplete search result forever.
        const freshMetadata = data.current_metadata;
        if (currentTrackPath && freshMetadata && typeof freshMetadata === "object" &&
            typeof freshMetadata.path === "string" &&
            fileKey(freshMetadata.path) === fileKey(currentTrackPath)) {
            metadataMap.set(fileKey(currentTrackPath), freshMetadata);
        }
        if (revealPlayingOnPoll && currentTrackPath) {
            revealPlayingOnPoll = false;
            showScreen("playing");
        }
        setPlayerVisible(Boolean(currentTrackPath));
        // Ручной запуск файла вне библиотеки тоже получает сведения и обложку.
        if (currentTrackPath && !metadataMap.has(fileKey(currentTrackPath))) {
            void loadLibraryMetadata();
        }

        highlightCurrentTrack();
        highlightArtistTrack();

        shuffleEnabled = Boolean(data.shuffle_enabled);

        const shuffleLabel = shuffleEnabled
            ? "Выключить случайный режим" : "Включить случайный режим";
        shuffleButton.textContent = "⇄";
        shuffleButton.title = shuffleLabel;
        shuffleButton.setAttribute("aria-label", shuffleLabel);

        shuffleButton.setAttribute(
            "aria-pressed",
            String(shuffleEnabled)
        );

        shuffleButton.disabled =
            commandBusy || shufflePending ||
            (!shuffleEnabled && getActiveLibraryFiles().length === 0);

        playbackError.textContent = data.playback_error || "";
        playbackError.hidden = !data.playback_error;

        playerState.textContent =
            stateLabels[data.state] || "Неизвестное состояние";

        const currentMetadata = data.file_path
            ? metadataMap.get(fileKey(data.file_path))
            : null;

        const currentTitle =
            currentMetadata?.title ||
            data.filename ||
            "Не выбран";

        // Не пересоздаём img при каждом секундном опросе состояния.
        setSongCover(playerCover, currentMetadata?.cover_url);
        trackName.textContent = currentTitle;
        trackName.title = currentTitle;
        updatePlayingPresentation(currentMetadata, currentTitle, data.state);
        renderArtistLinks(playerArtist, currentMetadata?.artist,
            data.filename ? window.i18n.t("Исполнитель не указан") : window.i18n.t("Ваша локальная библиотека"));
        const toggleLabel = data.state === "playing" ? "Пауза"
            : data.state === "paused" ? "Продолжить" : "Воспроизвести";
        playToggleButton.textContent = data.state === "playing" ? "Ⅱ" : "▶";
        playToggleButton.title = toggleLabel;
        playToggleButton.setAttribute("aria-label", toggleLabel);
        playToggleButton.disabled = commandBusy || window.onlinePreparing ||
            data.state === "opening" || data.state === "buffering";
        if (!queuePanel.hidden) void refreshQueue();

        queuePosition.textContent =
            data.queue_index === null || data.queue_size === 0
                ? "Не создана"
                : `${data.queue_index + 1} из ${data.queue_size}`;

        actualVolume.textContent =
            data.volume === null ? "—" : `${data.volume}%`;

        // Не возвращаем ползунок назад устаревшим ответом состояния.
        if (Number.isFinite(data.volume) &&
            requestedVolumeRevision === volumeRevision &&
            !volumeDragging && !volumeRequestBusy &&
            pendingVolume === null && volumeTimer === null) {
            volumeSlider.value = String(data.volume);
            volumeValue.textContent = String(data.volume);
        }

        durationTime.textContent = formatTime(data.duration_ms);

        // Не двигаем ползунок, пока пользователь его меняет
        // или пока выполняется запрос перемотки.
        seekSlider.disabled = !data.can_seek && !(seekPending && Number(seekSlider.max) > 0);
        if (window.playerMotion && requestedSeekRevision === seekRevision && !seekPending) window.playerMotion.state(data);
        else if (!isEditingSeek && !seekPending) {
            seekSlider.max = String(data.duration_ms);
            seekSlider.value = String(Math.min(data.time_ms, data.duration_ms));
            currentTime.textContent = formatTime(data.time_ms);
        }
        window.playerVisuals?.updateRanges();
    } catch (error) {
        if (stateRefreshQueued) return;
        playerState.textContent = "Нет связи с сервером";
        window.playerVisuals?.connectionLost();
        window.playerMotion?.connectionLost();
        currentPlaybackState = "unknown";
        playToggleButton.disabled = true;
        highlightCurrentTrack();
        shuffleButton.disabled = true;
        actualVolume.textContent = "—";
        seekSlider.disabled = true;
        isEditingSeek = false;
    } finally {
        clearTimeout(timeout); statePollController = null; statePollBusy = false;
        statePollTimer = setTimeout(updatePlayerState, stateRefreshQueued ? 0 : (!document.hidden && window.__mainVisible !== false && ["opening","buffering"].includes(currentPlaybackState) ? 200 : 1000)); stateRefreshQueued = false;
    }
}

// Последовательная отправка громкости: быстрые движения объединяются,
// а последнее выбранное значение обязательно отправляется.
// Очередь изменения громкости: отправляем последовательно последнее значение.
async function flushVolume() {
    if (volumeRequestBusy || pendingVolume === null) return;
    const volume = pendingVolume;
    pendingVolume = null;
    volumeRequestBusy = true;
    try {
        const response = await fetch("/api/player/volume", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ volume }),
            signal: AbortSignal.timeout(5000)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(typeof data.detail === "string"
                ? data.detail : `Ошибка запроса: ${response.status}`);
        }
    } catch (error) {
        result.textContent = "Не удалось изменить громкость: " + error.message;
    } finally {
        volumeRequestBusy = false;
        if (pendingVolume !== null) {
            volumeTimer = setTimeout(() => {
                volumeTimer = null;
                void flushVolume();
            }, 75);
        }
    }
}

// Изменение ползунка сразу ставит новое значение громкости в очередь отправки.
volumeSlider.addEventListener("input", () => {
    volumeValue.textContent = volumeSlider.value;
    pendingVolume = Number(volumeSlider.value);
    volumeRevision += 1;
    if (!volumeRequestBusy && volumeTimer === null) void flushVolume();
});
volumeSlider.addEventListener("pointerdown", () => { volumeDragging = true; });
document.addEventListener("pointerup", () => { volumeDragging = false; });
volumeSlider.addEventListener("pointercancel", () => { volumeDragging = false; });
volumeSlider.addEventListener("blur", () => { volumeDragging = false; });

// Предпросмотр перемотки; запрос отправляется после завершения изменения.
seekSlider.addEventListener("input", () => {
        isEditingSeek = true;

        currentTime.textContent = formatTime(
            Number(seekSlider.value)
        );
    });

    // Latest-seek wins. Slider/buttons stay usable while one request is in flight.
    let seekRevision = 0, queuedSeek = null, seekSending = false;
    function cancelQueuedSeeks() { queuedSeek = null; seekRevision++; }
    async function flushSeek() {
        if (seekSending || queuedSeek === null) return;
        seekSending = true; seekPending = true;
        while (queuedSeek !== null) {
            const positionMs = queuedSeek, revision = seekRevision, path = currentTrackPath;
            queuedSeek = null;
            try {
                const response = await fetch('/api/player/seek', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({position_ms:positionMs,file_path:path}),signal:AbortSignal.timeout(5000)});
                const data = await response.json();
                if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'HTTP '+response.status);
                if (revision === seekRevision) {
                    window.playerMotion?.seek(Number.isFinite(data.position_ms) ? data.position_ms : positionMs);
                    result.textContent = data.message || '';
                }
            } catch(error) {
                if (revision === seekRevision) result.textContent = 'Не удалось перемотать трек: '+error.message;
            }
        }
        seekSending = false; seekPending = false;
        window.refreshPlayerState();
    }
    seekSlider.addEventListener('change', () => {
        queuedSeek = Number(seekSlider.value); seekRevision++; isEditingSeek = false;
        window.playerMotion?.seek(queuedSeek); void flushSeek();
    });

    seekSlider.addEventListener("pointercancel", () => {
        isEditingSeek = false;
    });

    seekSlider.addEventListener("blur", () => {
        isEditingSeek = false;
    });
// Диалог добавления нескольких музыкальных файлов.
addFilesButton.addEventListener("click", async () => {
    addFilesButton.disabled = true;
    chooseFileButton.disabled = true;

    try {
        const paths =
            await window.pywebview.api.choose_audio_files();

        if (paths.length > 0) {
            const addedCount = await addFilesToLibrary(paths);

            result.textContent =
                `Добавлено новых треков: ${addedCount}.`;
        }
    } catch (error) {
        result.textContent =
            "Не удалось добавить файлы: " + error.message;
    } finally {
        addFilesButton.disabled = false;
        chooseFileButton.disabled = false;
    }
});

// Переключение случайного прохода по полному текущему списку.
shuffleButton.addEventListener("click", async () => {
    if (commandBusy) return;
    const nextEnabled = !shuffleEnabled;
    const nextLibraryContext = captureLibraryContext();

    if (nextEnabled && getActiveLibraryFiles().length === 0) {
        result.textContent = "Выбранный список пуст. Добавь в него треки.";
        return;
    }

    shufflePending = true;
    setPlaybackControlsBusy(true);

    try {
        const paths=nextEnabled?getOrderedLibraryFiles():[];
        const response = paths.length>10000?await uploadLargeQueue(paths,0,true):await fetch("/api/player/shuffle", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                enabled: nextEnabled,
                paths
            }),
            signal: AbortSignal.timeout(10000)
        });

        const data = await response.json();

        if (!response.ok) {
            const message = typeof data.detail === "string"
                ? data.detail
                : `Ошибка запроса: ${response.status}`;

            throw new Error(message);
        }

        result.textContent = data.message;
        if (nextEnabled) revealPlayingOnPoll = true;
        if (nextEnabled) playbackLibraryContext = nextLibraryContext;
    } catch (error) {
        result.textContent =
            "Не удалось изменить случайный режим: " +
            error.message;
    } finally {
        shufflePending = false;
        // Режим уточнит очередная проверка состояния.
        shuffleButton.disabled = true;
        setPlaybackControlsBusy(false);
        void refreshPlayerState();
    }
});
librarySort.addEventListener("change", () => {
    renderLibrary();
});

// Ручное повторное чтение текстовых тегов и встроенных обложек.
refreshMetadataButton.addEventListener("click", async () => {
    if (!libraryReady || metadataLoading) return;
    metadataMap.clear();
    reloadCoverCache = true;
    delete playerCover.dataset.coverUrl;
    delete largeTrackCover.dataset.coverUrl;
    setSongCover(playerCover, null);
    renderLibrary();
    await loadLibraryMetadata();
});

librarySearch.addEventListener("input", () => {
    renderLibrary();
});

// 3. Элементы и состояние плейлистов.
const playlistNameInput = document.getElementById("playlist-name");
const createPlaylistButton = document.getElementById("create-playlist-button");
const playlistsList = document.getElementById("playlists-list");
const playlistsMessage = document.getElementById("playlists-message");
let playlists = [];
let playlistsReady = false;
let playlistsBusy = false;
let playlistsInitStarted = false;

// КАРТОЧКИ ПЛЕЙЛИСТОВ: обложка, название, счётчик и действия.

// Кнопки открытия и редактирования соседние, не вложенные: корректный HTML и клавиатура.
function renderPlaylists() {
    playlistsList.replaceChildren();
    for (const playlist of playlists) {
        const row = document.createElement("li");
        row.className = "playlist-tile";
        const open = document.createElement("button");
        open.type = "button";
        open.className = "playlist-open";
        open.setAttribute("aria-label", `Открыть плейлист «${playlist.name}»`);
        const cover = document.createElement("span");
        cover.className = "playlist-cover";
        cover.setAttribute("aria-hidden", "true");
        const symbol = document.createElement("span");
        symbol.textContent = "♫";
        cover.append(symbol);
        addPlaylistCoverImage(cover, playlist.cover);
        const label = document.createElement("strong");
        label.className = "playlist-name";
        label.dataset.userText = "true"; label.textContent = playlist.name;
        const count = document.createElement("span");
        count.className = "playlist-card-count";
        count.textContent = `Треков: ${(playlist.tracks || []).length}`;
        open.append(cover, label, count);
        open.addEventListener("click", () => {
            activePlaylistId = playlist.id;
            librarySearch.value = "";
            refreshPlaylistSelectors();
            renderLibrary();
            showScreen("music");
        });
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "playlist-edit icon-button";
        edit.setAttribute("aria-label", `Редактировать плейлист «${playlist.name}»`);
        edit.title = "Редактировать плейлист";
        edit.setAttribute("aria-haspopup", "dialog");
        edit.disabled = playlistsBusy;
        edit.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m15 5 4 4M4 20l5-1L21 7a2.8 2.8 0 0 0-4-4L5 15z"/></svg>';
        edit.addEventListener("click", () => openPlaylistEditor(playlist.id));
        row.append(open, edit);
        playlistsList.append(row);
    }
    renderSidebarPlaylists();
    updateHomeOverview();
    playlistsMessage.hidden = playlists.length > 0 &&
        !/ошиб|не удалось/i.test(playlistsMessage.textContent);
}

// Общий обработчик сохранения плейлистов: блокировка, результат, ошибки.

async function mutatePlaylists(action, message) {
    if (!playlistsReady || playlistsBusy) return false;
    playlistsBusy = true;
    createPlaylistButton.disabled = true;
    renderPlaylists();
    syncPlaylistEditor();
    refreshPlaylistSelectors();
    renderLibrary();
    try {
        playlists = await action();
        const text = typeof message === "function" ? message() : message;
        playlistsMessage.textContent = text;
        if (typeof message === "function") result.textContent = text;
        return true;
    } catch (error) {
        playlistsMessage.textContent = "Ошибка: " + error.message;
        return false;
    } finally {
        playlistsBusy = false;
        createPlaylistButton.disabled = !playlistsReady;
        renderPlaylists();
        refreshPlaylistSelectors();
        renderLibrary();
        syncPlaylistEditor();
        void loadLibraryMetadata();
    }
}

// Чтение сохранённых плейлистов при запуске окна.

async function initializePlaylists() {
    if (playlistsInitStarted) return;
    playlistsInitStarted = true;
    try {
        playlists = await window.pywebview.api.load_playlists();
        playlistsReady = true;
        createPlaylistButton.disabled = false;
        playlistsMessage.textContent = playlists.length
            ? `Плейлистов: ${playlists.length}.`
            : "Пока нет плейлистов. Создай первый.";
        renderPlaylists();
        refreshPlaylistSelectors();
        renderLibrary();
        void loadLibraryMetadata();
    } catch (error) {
        playlistsMessage.textContent = "Не удалось загрузить плейлисты: " + error.message;
    }
}

// Заполнение выпадающего списка просмотра библиотеки.

window.getOwnMusicPlaylists=()=>playlists.map(p=>({id:p.id,name:p.name}));
function refreshPlaylistSelectors() {
    window.dispatchEvent(new Event('own-playlists-changed'));
    if (!playlists.some((p) => p.id === activePlaylistId)) activePlaylistId = "";
    playlistView.replaceChildren();
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "Все локальные треки";
    playlistView.append(all);
    for (const playlist of playlists) {
        const viewOption = document.createElement("option");
        viewOption.value = playlist.id;
        viewOption.dataset.userText = "true"; viewOption.textContent = `${playlist.name} (${(playlist.tracks || []).length})`;
        playlistView.append(viewOption);
    }
    playlistView.value = activePlaylistId;
    playlistView.disabled = !playlistsReady;
}

// Выбор плейлистов: одна песня может попасть сразу в несколько плейлистов.
const playlistPicker = document.getElementById("playlist-picker");
const playlistPickerList = document.getElementById("playlist-picker-list");
const playlistPickerTrack = document.getElementById("playlist-picker-track");
const playlistPickerMessage = document.getElementById("playlist-picker-message");
let playlistPickerResolve = null;

function settlePlaylistPicker(ids) {
    const resolve = playlistPickerResolve;
    playlistPickerResolve = null;
    if (playlistPicker.open) playlistPicker.close();
    if (resolve) resolve(ids || []);
}

function choosePlaylists(trackLabel) {
    if (!playlistsReady || !playlists.length || playlistPicker.open) return Promise.resolve([]);
    playlistPickerTrack.textContent = trackLabel || "";
    playlistPickerMessage.textContent = "";
    playlistPickerList.replaceChildren();
    const fragment = document.createDocumentFragment();
    for (const playlist of playlists) {
        const option = document.createElement("label");
        option.className = "playlist-picker-option";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = playlist.id;
        checkbox.name = "playlist-picker-item";
        const name = document.createElement("span");
        name.dataset.userText = "true";
        name.textContent = playlist.name;
        const count = document.createElement("small");
        count.className = "subtle-label";
        count.textContent = String((playlist.tracks || []).length);
        option.append(checkbox, name, count);
        fragment.append(option);
    }
    playlistPickerList.append(fragment);
    return new Promise((resolve) => {
        playlistPickerResolve = resolve;
        playlistPicker.showModal();
        playlistPickerList.querySelector("input[type=checkbox]")?.focus();
    });
}

async function addPathToPlaylists(path, playlistIds) {
    const ids = [...new Set((playlistIds || []).filter((id) => typeof id === "string" && id))];
    if (!ids.length) return 0;
    const api = window.pywebview?.api;
    if (typeof api?.add_track_to_playlists === "function") {
        const saved = await mutatePlaylists(
            () => api.add_track_to_playlists(ids, path),
            () => `Песня добавлена в плейлисты: ${ids.length}.`
        );
        return saved ? ids.length : 0;
    }
    let added = 0;
    for (const playlistId of ids) {
        if (await mutatePlaylists(() => api.add_track_to_playlist(playlistId, path), "")) added += 1;
    }
    return added;
}

window.choosePlaylists = choosePlaylists;
window.addPathToPlaylists = addPathToPlaylists;

document.getElementById("playlist-picker-cancel").addEventListener("click", () => settlePlaylistPicker([]));
document.getElementById("close-playlist-picker").addEventListener("click", () => settlePlaylistPicker([]));
playlistPicker.addEventListener("cancel", (event) => { event.preventDefault(); settlePlaylistPicker([]); });
playlistPicker.addEventListener("click", (event) => {
    if (event.target === playlistPicker) settlePlaylistPicker([]);
});
document.getElementById("playlist-picker-add").addEventListener("click", () => {
    const ids = [...playlistPickerList.querySelectorAll("input[type=checkbox]:checked")]
        .map((input) => input.value);
    if (!ids.length) {
        playlistPickerMessage.textContent = "Отметьте хотя бы один плейлист.";
        return;
    }
    settlePlaylistPicker(ids);
});

playlistView.addEventListener("change", () => {
    activePlaylistId = playlistView.value;
    librarySearch.value = "";
    renderLibrary();
});

// Создание плейлиста по названию из формы на главной.

async function createNamedPlaylist() {
    const name = playlistNameInput.value.trim();
    if (!name) {
        playlistsMessage.textContent = "Введите название плейлиста.";
        playlistNameInput.focus();
        return;
    }
    const created = await mutatePlaylists(
        () => window.pywebview.api.create_playlist(name),
        "Плейлист создан."
    );
    if (created) {
        playlistNameInput.value = "";
        setPlaylistForm(false);
    }
}

createPlaylistButton.addEventListener("click", createNamedPlaylist);
playlistNameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        void createNamedPlaylist();
    }
});
window.addEventListener("pywebviewready", initializePlaylists);
if (window.pywebview && window.pywebview.api) {
    void initializePlaylists();
}

// UI-каркас. Воспроизведение остаётся в существующем API.
// 4. Экраны приложения, боковая панель и панель очереди.
const screenTitle = document.getElementById("screen-title");
const homeTrackCount = document.getElementById("home-track-count");
const homePlaylistCount = document.getElementById("home-playlist-count");
const sidebarPlaylistsList = document.getElementById("sidebar-playlists-list");
const queuePanel = document.getElementById("queue-panel");
const queueBackdrop = document.getElementById("queue-backdrop");
const queueButton = document.getElementById("queue-button");
const closeQueueButton = document.getElementById("close-queue-button");
const queueList = document.getElementById("queue-list");
const queueMessage = document.getElementById("queue-message");
let queueLoading = false;
let activeScreen = "home";

// Навигация между главной, музыкой и настройками без перезагрузки страницы.

function showScreen(name) {
    if (name === "playing" && !window.playingScreenEnabled) return;
    const enteringPlaying = name === "playing" && activeScreen !== "playing";
    screenRevealAnimation?.cancel(); screenRevealAnimation = null;
    const titles = { home: "Главная", music: "Моя музыка", settings: "Настройки", online: "Поиск музыки", artist: "Исполнитель", playing: "Сейчас играет" };
    if (!titles[name]) return;
    activeScreen = name;
    if (["home", "music", "settings", "online"].includes(name)) activeArtistName = "";
    document.querySelectorAll(".screen").forEach((screen) => {
        screen.hidden = screen.id !== `screen-${name}`;
    });
    document.querySelectorAll("[data-screen]").forEach((button) => {
        const active = button.dataset.screen === (name === "playing" ? (playbackLibraryContext?.kind === "online" ? "online" : "music") : name);
        button.classList.toggle("is-active", active);
        if (active) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
    });
    screenTitle.textContent = titles[name];
    document.getElementById('back-to-tracks').hidden = name !== 'playing';
    window.dispatchEvent(new Event('player-screen-changed'));
    document.querySelector(".workspace").scrollTop = 0;
    window.playerVisuals?.screenChanged(name);
    window.equalizerPanel?.layout();
    addFilesButton.hidden = !['home','music'].includes(name);
    addFilesButton.classList.toggle('home-add-button', name === 'home');
    if (enteringPlaying && !document.hidden && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
        const radius=Math.ceil(Math.hypot(innerWidth,innerHeight)/2)+2;
        screenRevealAnimation=appShell.animate([{clipPath:'circle(0px at 50% 50%)',opacity:.72},{clipPath:`circle(${radius}px at 50% 50%)`,opacity:1}],{duration:380,easing:'cubic-bezier(.16,1,.3,1)'});
    }
}

// 4.1. Страница исполнителя: имя из тегов открывает все локальные песни автора.
function artistKey(value) {
    return String(value || "").normalize("NFC").toLocaleLowerCase("ru").replace(/\s+/g, " ").trim();
}

// В интерфейсе делим только по запятым: "Miyagi & Эндшпиль" — один исполнитель.
function splitArtistNames(value) {
    return String(value || "").split(",").map((part) => part.trim()).filter(Boolean);
}

// Для подбора песен учитываем ещё feat./ft.: "A feat. B" относится и к A, и к B.
function artistMatchParts(value) {
    return String(value || "").split(/,|\s(?:feat\.?|ft\.?|при\s+уч\.?)\s/i)
        .map((part) => part.trim()).filter(Boolean);
}

function getArtistFiles(name) {
    const targets = new Set(artistMatchParts(name).map(artistKey).filter(Boolean));
    if (!targets.size) return [];
    const seen = new Set();
    const files = [];
    for (const path of getAllKnownFiles()) {
        if (path.startsWith("online:")) continue;
        const key = fileKey(path);
        if (seen.has(key)) continue;
        const artist = metadataMap.get(key)?.artist;
        if (!artist) continue;
        if (!artistMatchParts(artist).map(artistKey).some((part) => targets.has(part))) continue;
        seen.add(key);
        files.push(path);
    }
    return files.sort((left, right) => textCollator.compare(
        metadataMap.get(fileKey(left))?.title || fileName(left),
        metadataMap.get(fileKey(right))?.title || fileName(right)));
}

function renderArtistLinks(container, artistText, fallback) {
    if (!container) return;
    const parts = splitArtistNames(artistText);
    const signature = parts.length ? parts.map(artistKey).join("|") : "text:" + String(fallback || "");
    if (container.dataset.artistSignature === signature) return;
    container.dataset.artistSignature = signature;
    container.replaceChildren();
    if (!parts.length) {
        container.textContent = fallback || window.i18n.t("Исполнитель не указан");
        return;
    }
    parts.forEach((part, index) => {
        if (index > 0) {
            const separator = document.createElement("span");
            separator.className = "artist-separator";
            separator.setAttribute("aria-hidden", "true");
            separator.textContent = ", ";
            container.append(separator);
        }
        const button = document.createElement("button");
        button.type = "button";
        button.className = "artist-link";
        button.dataset.userText = "true";
        button.textContent = part;
        button.title = window.i18n.t("Все песни исполнителя") + ": " + part;
        button.setAttribute("aria-label", button.title);
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            openArtistPage(part);
        });
        container.append(button);
    });
}

function renderArtistPage() {
    const name = activeArtistName;
    const files = getArtistFiles(name);
    const nameElement = document.getElementById("artist-name");
    nameElement.textContent = name;
    nameElement.title = name;
    document.getElementById("artist-count").textContent = `Треков: ${files.length}`;
    document.getElementById("artist-play-all").disabled = files.length === 0;
    document.getElementById("artist-empty").hidden = files.length > 0;
    setSongCover(document.getElementById("artist-cover"),
        files.map((path) => metadataMap.get(fileKey(path))?.cover_url).find(Boolean) || null);
    const list = document.getElementById("artist-tracks");
    list.replaceChildren();
    const fragment = document.createDocumentFragment();
    for (const path of files) {
        const metadata = metadataMap.get(fileKey(path));
        const row = document.createElement("li");
        row.className = "artist-track";
        row.dataset.path = path;
        if (currentTrackPath && fileKey(currentTrackPath) === fileKey(path)) row.classList.add("is-current");
        const play = document.createElement("button");
        play.type = "button";
        play.className = "icon-button artist-track-play";
        play.textContent = "▶";
        play.title = window.i18n.t("Воспроизвести:") + " " + (metadata?.title || fileName(path));
        play.setAttribute("aria-label", play.title);
        play.disabled = commandBusy;
        play.addEventListener("click", (event) => { event.stopPropagation(); void playArtistFile(path); });
        const artwork = document.createElement("span");
        artwork.className = "song-art";
        artwork.setAttribute("aria-hidden", "true");
        artwork.textContent = "♫";
        setSongCover(artwork, metadata?.cover_url);
        const info = document.createElement("span");
        info.className = "artist-track-info";
        const title = document.createElement("strong");
        title.className = "artist-track-title";
        title.dataset.userText = "true";
        title.textContent = metadata?.title || fileName(path);
        title.title = title.textContent;
        const album = document.createElement("span");
        album.className = "artist-track-album";
        album.dataset.userText = "true";
        album.textContent = metadata?.album || "—";
        info.append(title, album);
        const duration = document.createElement("span");
        duration.className = "artist-track-duration";
        duration.textContent = Number.isFinite(metadata?.duration_ms) ? formatTime(metadata.duration_ms) : "—";
        row.append(play, artwork, info, duration);
        row.addEventListener("click", (event) => {
            if (event.target.closest("button, a, input, select")) return;
            void playArtistFile(path);
        });
        fragment.append(row);
    }
    list.append(fragment);
    startArtistOnlineSearch(name);
}

function startArtistOnlineSearch(name) {
    const key = artistKey(name);
    if (!key || artistOnlineKey === key) return;
    artistOnlineKey = key;
    const status = document.getElementById("artist-online-status");
    const list = document.getElementById("artist-online-tracks");
    if (status) status.textContent = "Ищем в интернете…";
    if (list) list.replaceChildren();
    if (!window.onlineMusic?.searchArtist) {
        if (status) status.textContent = "Поиск в интернете недоступен.";
        return;
    }
    window.onlineMusic.searchArtist(name, {navigate: false});
}

function artistPlaybackContext() {
    return {kind: "artist", artist: activeArtistName, scrollTop: 0};
}

function onlineSourceLabel(source) {
    return {youtube: "YouTube", soundcloud: "SoundCloud", spotify: "Spotify",
        archive: "Internet Archive", hitmo: "hitmotop.com", muzbomb: "muzbomb.net"}[source] || "";
}

function artistOnlineRow(track) {
    const row = document.createElement("li");
    row.className = "artist-online-track";
    const titleText = track.title || fileName(track.path);
    const play = document.createElement("button");
    play.type = "button";
    play.className = "icon-button";
    play.textContent = "▶";
    play.title = window.i18n.t("Воспроизвести:") + " " + titleText;
    play.setAttribute("aria-label", play.title);
    play.disabled = !["playable", "prepare"].includes(track.access);
    play.addEventListener("click", (event) => {
        event.stopPropagation();
        void window.onlineMusic?.playTrack?.(track, artistPlaybackContext());
    });
    const artwork = document.createElement("span");
    artwork.className = "song-art";
    artwork.setAttribute("aria-hidden", "true");
    artwork.textContent = "♫";
    setSongCover(artwork, track.cover_url);
    const info = document.createElement("span");
    info.className = "artist-online-info";
    const title = document.createElement("strong");
    title.className = "artist-online-title";
    title.dataset.userText = "true";
    title.textContent = titleText;
    title.title = titleText;
    const meta = document.createElement("span");
    meta.className = "artist-online-meta";
    meta.dataset.userText = "true";
    meta.textContent = [track.artist, onlineSourceLabel(track.source)].filter(Boolean).join(" · ");
    info.append(title, meta);
    const actions = document.createElement("span");
    actions.className = "artist-online-actions";
    if (track.path.startsWith("online:web:") || track.path.startsWith("online:catalog:")) {
        const add = document.createElement("button");
        add.type = "button";
        add.className = "icon-button";
        add.textContent = "+";
        add.title = window.i18n.t("Добавить в библиотеку и плейлисты");
        add.setAttribute("aria-label", add.title + ": " + titleText);
        add.addEventListener("click", (event) => {
            event.stopPropagation();
            void window.onlineMusic?.addTrack?.(track);
        });
        const download = document.createElement("button");
        download.type = "button";
        download.className = "icon-button";
        download.textContent = "↓";
        download.title = window.i18n.t("Скачать");
        download.setAttribute("aria-label", window.i18n.t("Скачать") + ": " + titleText);
        download.addEventListener("click", (event) => {
            event.stopPropagation();
            void window.onlineMusic?.downloadTrack?.(track);
        });
        actions.append(add, download);
    }
    const duration = document.createElement("span");
    duration.className = "artist-online-duration";
    duration.textContent = Number.isFinite(track.duration_ms) ? formatTime(track.duration_ms) : "—";
    row.append(play, artwork, info, actions, duration);
    row.addEventListener("click", (event) => {
        if (event.target.closest("button, a, input, select")) return;
        if (!["playable", "prepare"].includes(track.access)) return;
        void window.onlineMusic?.playTrack?.(track, artistPlaybackContext());
    });
    return row;
}

function renderArtistOnline(tracks, status, message) {
    const statusElement = document.getElementById("artist-online-status");
    const list = document.getElementById("artist-online-tracks");
    if (!statusElement || !list) return;
    const seen = new Set();
    const items = [];
    for (const track of tracks) {
        if (!track || typeof track.path !== "string" || seen.has(track.path)) continue;
        seen.add(track.path);
        items.push(track);
        if (items.length >= 10) break;
    }
    if (status === "failed") {
        statusElement.textContent = message || "Не удалось найти песни в интернете.";
        list.replaceChildren();
        return;
    }
    if (["done", "cancelled", "idle"].includes(status)) {
        statusElement.textContent = items.length
            ? `Найдено: ${items.length}${tracks.length > items.length ? " из " + tracks.length : ""}`
            : "Ничего не найдено в интернете.";
    } else {
        statusElement.textContent = items.length ? `Ищем… найдено ${items.length}` : "Ищем в интернете…";
    }
    const fragment = document.createDocumentFragment();
    for (const track of items) fragment.append(artistOnlineRow(track));
    list.replaceChildren(fragment);
}

window.addEventListener("online-search-state", (event) => {
    const detail = event.detail || {};
    if (activeScreen !== "artist" || !activeArtistName) return;
    if (artistKey(detail.query) !== artistKey(activeArtistName)) return;
    renderArtistOnline(Array.isArray(detail.tracks) ? detail.tracks : [],
        detail.status || "running", detail.message || "");
});

async function playArtistFile(path) {
    if (commandBusy || !path) return;
    pathInput.value = path;
    await sendCommand("play");
}

function highlightArtistTrack() {
    if (activeScreen !== "artist") return;
    for (const row of document.querySelectorAll("#artist-tracks .artist-track")) {
        row.classList.toggle("is-current",
            Boolean(currentTrackPath) && fileKey(row.dataset.path) === fileKey(currentTrackPath));
    }
}

function openArtistPage(name) {
    const value = String(name || "").trim();
    if (!value) return;
    if (activeScreen !== "artist") {
        artistReturn = {screen: activeScreen,
            scrollTop: document.querySelector(".workspace").scrollTop};
    }
    activeArtistName = value;
    artistOnlineKey = "";
    showScreen("artist");
    renderArtistPage();
    document.getElementById("artist-name").focus({preventScroll: true});
}

function closeArtistPage() {
    const origin = artistReturn || {screen: "music", scrollTop: 0};
    const target = origin.screen === "playing" && !window.playingScreenEnabled ? "music" : origin.screen;
    activeArtistName = "";
    artistReturn = null;
    showScreen(target || "music");
    const workspace = document.querySelector(".workspace");
    if (workspace) workspace.scrollTop = origin.scrollTop || 0;
    screenTitle.focus({preventScroll: true});
}

window.openArtistPage = openArtistPage;
document.getElementById("artist-back").addEventListener("click", closeArtistPage);
document.getElementById("artist-play-all").addEventListener("click", () => {
    const files = getArtistFiles(activeArtistName);
    if (files.length) void playArtistFile(files[0]);
});
document.getElementById("artist-search-online").addEventListener("click", () => {
    if (activeArtistName) window.onlineMusic?.searchArtist?.(activeArtistName);
});

// Количество локальных песен и плейлистов на главной.

function updateHomeOverview() {
    homeTrackCount.textContent = String(libraryFiles.length);
    homePlaylistCount.textContent = String(playlists.length);
}

// Кнопки плейлистов и их маленькие обложки в боковой панели.

function renderSidebarPlaylists() {
    sidebarPlaylistsList.replaceChildren();
    if (!playlists.length) {
        const empty = document.createElement("li");
        empty.className = "sidebar-empty";
        empty.textContent = "Создайте первую подборку";
        sidebarPlaylistsList.append(empty);
        return;
    }
    for (const playlist of playlists) {
        const row = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "sidebar-playlist-button";
        button.classList.toggle("is-active", playlist.id === activePlaylistId);
        button.title = playlist.name;
        button.setAttribute("aria-label", playlist.name);
        const icon = document.createElement("span");
        icon.className = "sidebar-playlist-symbol";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = "♫";
        addPlaylistCoverImage(icon, playlist.cover);
        const label = document.createElement("span");
        label.dataset.userText = "true"; label.textContent = playlist.name;
        button.append(icon, label);
        button.addEventListener("click", () => {
            activeArtistName = "";
            activePlaylistId = playlist.id;
            librarySearch.value = "";
            refreshPlaylistSelectors();
            renderLibrary();
            showScreen("music");
        });
        row.append(button);
        sidebarPlaylistsList.append(row);
    }
}

document.querySelectorAll("[data-screen]").forEach((button) => {
    button.addEventListener("click", () => {
        if (button.dataset.screen === "music") {
            activeArtistName = "";
            activePlaylistId = "";
            librarySearch.value = "";
            refreshPlaylistSelectors();
            renderLibrary();
        }
        showScreen(button.dataset.screen);
    });
});
document.getElementById("brand-home").addEventListener("click", (event) => {
    event.preventDefault();
    showScreen("home");
});
document.getElementById("open-library-button").addEventListener("click", () => {
    activeArtistName = "";
    activePlaylistId = "";
    librarySearch.value = "";
    refreshPlaylistSelectors();
    renderLibrary();
    showScreen("music");
});
let playlistFormAnimation=null,playlistFormRevision=0,playlistFormOpen=false;
function setPlaylistForm(open){
 const form=document.getElementById('playlist-create-form');playlistFormAnimation?.cancel();const revision=++playlistFormRevision;playlistFormOpen=open;
 document.getElementById('show-playlist-form').setAttribute('aria-expanded',String(open));
 form.inert=!open;
 const finish=()=>{if(revision===playlistFormRevision){form.hidden=!open;form.style.overflow='';if(open)playlistNameInput.focus();}};
 if(matchMedia('(prefers-reduced-motion: reduce)').matches){finish();return;}
 form.hidden=false;form.style.overflow='hidden';const height=form.scrollHeight;
 playlistFormAnimation=form.animate(open?[{opacity:0,maxHeight:'0px',transform:'translateY(-8px)'},{opacity:1,maxHeight:height+'px',transform:'translateY(0)'}]:[{opacity:1,maxHeight:height+'px',transform:'translateY(0)'},{opacity:0,maxHeight:'0px',transform:'translateY(-8px)'}],{duration:200,easing:'ease-out',fill:'both'});
 playlistFormAnimation.finished.then(()=>{if(revision===playlistFormRevision){finish();playlistFormAnimation?.cancel();}}).catch(()=>{});
}
document.getElementById('new-playlist-shortcut').addEventListener('click',()=>{showScreen('home');setPlaylistForm(true);});
document.getElementById('show-playlist-form').addEventListener('click',()=>setPlaylistForm(!playlistFormOpen));
document.getElementById("playlist-create-form").addEventListener("submit", (event) => {
    event.preventDefault();
});

// Общая кнопка: пауза, продолжение или запуск выбранной/первой песни.
playToggleButton.addEventListener("click", async () => {
    if (commandBusy || window.onlinePreparing || ["opening","buffering"].includes(currentPlaybackState)) return;
    if (currentPlaybackState === "playing") {
        await sendCommand("pause");
    } else if (currentPlaybackState === "paused") {
        await sendCommand("resume");
    } else {
        const paths = getOrderedLibraryFiles();
        const selected = currentTrackPath && paths.some((path) => fileKey(path) === fileKey(currentTrackPath))
            ? currentTrackPath : paths[0];
        if (selected) pathInput.value = selected;
        if (!selected && !pathInput.value.trim()) {
            result.textContent = "Добавьте музыку или выберите непустой плейлист.";
            return;
        }
        await sendCommand("play");
    }
});

// Правый список очереди: только просмотр текущего порядка воспроизведения.

async function refreshQueue() {
    if (queueLoading || queuePanel.hidden) return;
    queueLoading = true;
    try {
        const response = await fetch("/api/player/queue", {
            cache: "no-store", signal: AbortSignal.timeout(3000)
        });
        if (!response.ok) throw new Error(`Ошибка сервера: ${response.status}`);
        const data = await response.json();
        queueList.replaceChildren();
        queueMessage.textContent = data.paths.length
            ? `${data.paths.length} треков · ${data.shuffle_enabled ? "случайный порядок" : "по порядку"}`
            : "Очередь пуста. Запустите песню из библиотеки или плейлиста.";
        data.paths.forEach((path, index) => {
            const meta = metadataMap.get(fileKey(path));
            const row = document.createElement("li");
            row.className = "queue-item";
            row.classList.toggle("is-current", index === data.index);
            row.classList.toggle("is-past", data.index !== null && index < data.index);
            if (index === data.index) row.setAttribute("aria-current", "true");
            const info = document.createElement("div");
            const title = document.createElement("span");
            title.className = "queue-title"; title.dataset.userText = "true";
            title.textContent = meta?.title || fileName(path);
            const artist = document.createElement("span");
            artist.className = "queue-artist"; artist.dataset.userText = "true";
            artist.textContent = meta?.artist || window.i18n.t("Исполнитель не указан");
            info.append(title, artist);
            const cover = document.createElement('span');
            cover.className = 'queue-cover song-art';
            cover.setAttribute('aria-hidden', 'true');
            setSongCover(cover, meta?.cover_url);
            row.append(cover, info);
            queueList.append(row);
        });
    } catch (error) {
        queueMessage.textContent = "Не удалось получить очередь: " + error.message;
    } finally {
        queueLoading = false;
    }
}
// Closing is cancellable: re-opening never inherits a stale hidden callback.
let queueAnimation = null, queueCloseGeneration = 0, queueClosing = false;
function closeQueue(immediate = false) {
    if (queuePanel.hidden) return;
    const generation = ++queueCloseGeneration;
    queueAnimation?.cancel(); queueAnimation = null;
    queueClosing = true;
    const finish = () => {
        if (generation !== queueCloseGeneration) return;
        queuePanel.hidden = true; queueBackdrop.hidden = true;
        queueClosing = false; queueAnimation?.cancel(); queueAnimation = null;
        queuePanel.classList.remove('is-closing');
        queueBackdrop.classList.remove('is-closing');
        queueButton.setAttribute('aria-expanded', 'false');
        if (!playerBar.hidden) queueButton.focus({preventScroll:true});
    };
    if (immediate === true || matchMedia('(prefers-reduced-motion: reduce)').matches) {finish(); return;}
    queuePanel.classList.add('is-closing'); queueBackdrop.classList.add('is-closing');
    queueAnimation = queuePanel.animate([
        {opacity:1, transform:'translateX(0)'},
        {opacity:0, transform:'translateX(48px)'}
    ], {duration:200,easing:'ease-in',fill:'forwards'});
    queueAnimation.finished.then(finish).catch(()=>{});
}
function openQueue() {
    ++queueCloseGeneration; queueAnimation?.cancel(); queueAnimation = null;
    queueClosing = false;
    queuePanel.classList.remove('is-closing'); queueBackdrop.classList.remove('is-closing');
    queuePanel.hidden = false; queueBackdrop.hidden = false;
    queueButton.setAttribute('aria-expanded','true');
    closeQueueButton.focus({preventScroll:true}); void refreshQueue();
}
queueButton.addEventListener('click', () => {
    if (!queuePanel.hidden && !queueClosing) closeQueue(); else openQueue();
});
closeQueueButton.addEventListener("click", closeQueue);
queueBackdrop.addEventListener("click", closeQueue);
document.addEventListener("keydown", (event) => {
    if (queuePanel.hidden) return;
    if (event.key === "Escape") { event.preventDefault(); closeQueue(); }
    if (event.key === "Tab") { event.preventDefault(); closeQueueButton.focus(); }
});

// Короткие сообщения приложения: показываем в центре и затем скрываем.
let messageTimer = null;
new MutationObserver(() => {
    if (!result.textContent.trim()) return;
    if (/^Список загружен|^Добавь музыкальные файлы/.test(result.textContent)) {
        result.hidden = true;
        return;
    }
    result.hidden = false;
    clearTimeout(messageTimer);
    messageTimer = setTimeout(() => { result.hidden = true; },
        /ошиб|не удалось|не найден/i.test(result.textContent) ? 8000 : 3500);
}).observe(result, { childList: true, characterData: true, subtree: true });

showScreen("home");
renderLibrary();
updatePlayerState();


// Меню находится в body, чтобы прокрутка списка не обрезала его.
// Закрытие меню ⋯ и, при необходимости, возврат фокуса к песне.
function closeTrackMenu(restoreFocus = true, immediate = false) {
    const menu = document.getElementById("track-menu");
    const anchor = trackMenuAnchor;
    trackMenuAnchor = null;
    menuAnimation?.cancel();
    const revision = ++menuRevision;
    const finish = () => { if(revision === menuRevision){menu.hidden=true;menu.replaceChildren();menu.style.pointerEvents='';} };
    if (!immediate && !menu.hidden && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
        menu.style.pointerEvents='none';
        menuAnimation=menu.animate([{opacity:1,transform:'translateY(0) scale(1)'},{opacity:0,transform:'translateY(-5px) scale(.98)'}],{duration:140,easing:'ease-in',fill:'forwards'});
        menuAnimation.finished.then(finish).catch(()=>{});
    } else finish();
    if (anchor) {
        anchor.setAttribute("aria-expanded", "false");
        if (restoreFocus && anchor.isConnected) anchor.focus();
    }
}

// Позиционирование меню действий вне области прокрутки библиотеки.

function openTrackMenu(anchor, actions) {
    if (trackMenuAnchor === anchor) { closeTrackMenu(); return; }
    closeTrackMenu(false, true);
    const menu = document.getElementById("track-menu");
    trackMenuAnchor = anchor;
    anchor.setAttribute("aria-expanded", "true");
    actions.forEach((button) => {
        button.setAttribute("role", "menuitem");
        button.tabIndex = -1;
        // Capture закрывает меню до запуска исходного обработчика кнопки.
        if (!button.dataset.menuBound) {
            button.addEventListener("click", () => closeTrackMenu(), { capture: true });
            button.dataset.menuBound = "true";
        }
        menu.append(button);
    });
    menu.hidden = false;
    const rect = anchor.getBoundingClientRect();
    const bounds = menu.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(rect.right - bounds.width, innerWidth - bounds.width - 8)) + "px";
    const playerTop = playerBar.hidden ? innerHeight : playerBar.getBoundingClientRect().top;
    const bottom = Math.min(innerHeight - 8, playerTop - 8);
    const top = rect.bottom + bounds.height + 4 <= bottom ? rect.bottom + 4 : rect.top - bounds.height - 4;
    menu.style.top = Math.max(8, Math.min(top, bottom - bounds.height)) + "px";
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches)
        menuAnimation=menu.animate([{opacity:0,transform:'translateY(-5px) scale(.98)'},{opacity:1,transform:'translateY(0) scale(1)'}],{duration:170,easing:'ease-out'});
    actions.find((button) => !button.disabled)?.focus();
}

// Окно сведений о песне. Теги и музыкальный файл здесь не меняются.

function showTrackDetails(path) {
    const dialog = document.getElementById("track-details-dialog");
    const meta = metadataMap.get(fileKey(path));
    dialog.dataset.path=path; document.getElementById("track-details-heading").dataset.userText="true";
    document.getElementById("track-details-heading").textContent = meta?.title || fileName(path);
    const list = document.getElementById("track-details-list");
    list.replaceChildren();
    const pairs = [
        ["Исполнитель", meta?.artist || window.i18n.t("Не указан")],
        ["Альбом", meta?.album || window.i18n.t("Не указан")],
        ["Длительность", Number.isFinite(meta?.duration_ms) ? formatTime(meta.duration_ms) : window.i18n.t("Неизвестна")],
        ["Файл", fileName(path)], ["Полный путь", path]
    ];
    if (meta?.error) pairs.push(["Ошибка чтения", window.i18n.t(meta.error)]);
    if (meta?.cover_error) pairs.push(["Обложка", window.i18n.t(meta.cover_error)]);
    else pairs.push(["Обложка", window.i18n.t(meta?.cover_url ? "Из метаданных файла" : "Встроенная обложка не найдена")]);
    pairs.forEach(([label, value]) => {
        const dt = document.createElement("dt"); dt.textContent = label;
        const dd = document.createElement("dd"); dd.textContent = value;
        list.append(dt, dd);
    });
    detailsAnimation?.cancel(); ++detailsRevision;
    dialog.style.pointerEvents='';
    if (!dialog.open) {
        dialog.showModal();
        if (!matchMedia('(prefers-reduced-motion: reduce)').matches)
            detailsAnimation=dialog.animate([{opacity:0,transform:'translateY(10px) scale(.98)'},{opacity:1,transform:'translateY(0) scale(1)'}],{duration:180,easing:'ease-out'});
    }
}

let detailsAnimation=null, detailsRevision=0;
function closeTrackDetails(){
    const dialog=document.getElementById('track-details-dialog');
    if(!dialog.open)return;detailsAnimation?.cancel();const revision=++detailsRevision;
    const finish=()=>{if(revision===detailsRevision){dialog.close();dialog.style.pointerEvents='';}};
    if(matchMedia('(prefers-reduced-motion: reduce)').matches){finish();return;}
    dialog.style.pointerEvents='none';
    detailsAnimation=dialog.animate([{opacity:1,transform:'translateY(0) scale(1)'},{opacity:0,transform:'translateY(10px) scale(.98)'}],{duration:160,easing:'ease-in',fill:'forwards'});
    detailsAnimation.finished.then(finish).catch(()=>{});
}
document.getElementById('close-track-details').addEventListener('click',closeTrackDetails);
document.getElementById('track-details-dialog').addEventListener('cancel',event=>{event.preventDefault();closeTrackDetails();});
document.addEventListener("pointerdown", (event) => {
    if (trackMenuAnchor && !document.getElementById("track-menu").contains(event.target)
        && !trackMenuAnchor.contains(event.target)) closeTrackMenu(false);
});
document.addEventListener("focusin", (event) => {
    if (trackMenuAnchor && !document.getElementById("track-menu").contains(event.target)
        && event.target !== trackMenuAnchor) closeTrackMenu(false);
});
document.addEventListener("keydown", (event) => {
    if (!trackMenuAnchor) return;
    const menu = document.getElementById("track-menu");
    const enabled = [...menu.querySelectorAll("button:not(:disabled)")];
    const index = enabled.indexOf(document.activeElement);
    if (event.key === "Escape") { event.preventDefault(); closeTrackMenu(); }
    else if (event.key === "Tab") closeTrackMenu();
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? enabled.length - 1
            : (index + (event.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length;
        enabled[next]?.focus();
    }
});
window.addEventListener("resize", () => closeTrackMenu(false));
document.querySelector(".workspace").addEventListener("scroll", () => closeTrackMenu(false));


// Обложка плейлиста — пользовательское изображение, не обложка песни.
// Показ пользовательской обложки плейлиста или заглушки при ошибке.
function addPlaylistCoverImage(container, filename) {
    if (typeof filename !== "string" || !/^[0-9a-f]{32}\.jpg$/.test(filename)) return;
    const image = document.createElement("img");
    image.alt = ""; // Название плейлиста уже есть рядом.
    image.loading = "lazy";
    image.src = "/api/assets/playlist-covers/" + encodeURIComponent(filename);
    image.addEventListener("load", () => container.classList.add("has-image"));
    image.addEventListener("error", () => {
        image.remove();
        container.classList.remove("has-image");
    });
    container.append(image);
}


// Обложки локальных песен: разрешаем только собственный маршрут сервера.
// При смене песни старый img удаляется сразу, чтобы картинка не перепуталась.
// Показ встроенной обложки песни. Не используем чужую картинку для заглушки.
function safeTrackCoverURL(url) {
    if (typeof url !== 'string') return '';
    if (/^\/api\/assets\/(?:track-covers|online-covers)\/[0-9a-f]{64}\.jpg$/.test(url)) return url;
    try {
        const parsed = new URL(url);
        if (parsed.protocol === 'https:' && (parsed.hostname.endsWith('.sndcdn.com')||['i.scdn.co','mosaic.scdn.co','i.ytimg.com','img.youtube.com'].includes(parsed.hostname)) &&
            !parsed.username && !parsed.password && (!parsed.port || parsed.port === '443')) return parsed.href;
    } catch {}
    return '';
}
function setSongCover(container, url) {
    const safeUrl = safeTrackCoverURL(url);
    if (container.dataset.coverUrl === safeUrl) return;
    container.dataset.coverUrl = safeUrl;
    container.replaceChildren(document.createTextNode("♫"));
    container.classList.remove("has-image");
    if (!safeUrl) return;
    const image = document.createElement("img");
    image.alt = "";
    image.loading = container.closest("#online-results") ? "eager" : container !== playerCover ? "lazy" : "eager";
    image.decoding = "async";
    image.addEventListener("load", () => {
        if (image.parentElement === container) container.classList.add("has-image");
    });
    image.addEventListener("error", () => {
        if (image.parentElement !== container) return;
        image.remove();
        container.classList.remove("has-image");
        // Не повторяем неисправный запрос каждую секунду.
    });
    image.src = safeUrl;
    container.append(image);
}


// 12. Видимость нижнего плеера: нет песни — нет панели и занятого места.
// Пауза сохраняет file_path, Стоп очищает его на сервере.
function setPlayerVisible(visible) {
    if (playerBar.hidden === !visible) return;
    const focusWasInPlayer = playerBar.contains(document.activeElement);
    const focusWasInQueue = queuePanel.contains(document.activeElement);
    playerBar.hidden = !visible;
    window.equalizerPanel?.onPlayerVisibility(visible);
    appShell.classList.toggle("player-hidden", !visible);
    if (!visible) {
        revealPlayingOnPoll = false;
        if (activeScreen === "playing") showScreen("music");
        if (!queuePanel.hidden) closeQueue();
        closeTrackMenu(false);
        if (focusWasInPlayer || focusWasInQueue) screenTitle.focus({ preventScroll: true });
    }
}

// 13. Настройки акцента. Предпросмотр отделён от записи на диск.
const accentPicker = document.getElementById("accent-picker");
const accentCurrent = document.getElementById("accent-current");
const settingsMessage = document.getElementById("settings-message");
const saveAccentButton = document.getElementById("save-accent-button");
const resetAccentButton = document.getElementById("reset-accent-button");
const accentPresets = [...document.querySelectorAll("[data-accent]")];
let settingsInitStarted = false;
let settingsReady = false;
let settingsSaving = false;
let savedAccent = "#c9c9d1";
let currentAccent = savedAccent;

// WCAG-яркость нужна, чтобы на жёлтой кнопке был тёмный, а не белый текст.
function colorLuminance(rgb) {
    const linear = rgb.map((value) => {
        const channel = value / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
}

// Из одного цвета получаем акцент текста, фон кнопок, мягкую заливку и границы.
function applyAccent(hex) {
    if (typeof hex !== "string" || !/^#[0-9a-f]{6}$/i.test(hex)) return;
    currentAccent = hex.toLowerCase();
    const rgb = [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16));
    const light = document.documentElement.dataset.theme === "light";
    const textRgb = light ? rgb.map(value => Math.round(value * .62)) : rgb.map(value => Math.round(value + (255 - value) * .52));
    const root = document.documentElement.style;
    root.setProperty("--accent-strong", currentAccent);
    root.setProperty("--accent", `rgb(${textRgb.join(",")})`);
    root.setProperty("--accent-soft", `rgba(${rgb.join(",")}, .14)`);
    root.setProperty("--accent-surface", `rgba(${rgb.join(",")}, .22)`);
    root.setProperty("--accent-border", `rgba(${textRgb.join(",")}, .45)`);
    root.setProperty("--accent-on", colorLuminance(rgb) > .179 ? "#000000" : "#ffffff");
    accentPicker.value = currentAccent;
    accentCurrent.textContent = currentAccent.toUpperCase();
    window.playerVisuals?.themeChanged();
    accentPresets.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.accent === currentAccent)));
}

// При ошибке чтения не разрешаем случайно перезаписать повреждённый JSON.
function updateSettingsControls() {
    const disabled = !settingsReady || settingsSaving;
    accentPicker.disabled = disabled;
    resetAccentButton.disabled = disabled;
    accentPresets.forEach((button) => { button.disabled = disabled; });
    saveAccentButton.disabled = disabled || currentAccent === savedAccent;
    updateAppearanceControls();
    window.playerVisuals?.updateControls();
    window.themeControls?.updateControls();
}

function previewAccent(hex) {
    applyAccent(hex);
    settingsMessage.textContent = currentAccent === savedAccent
        ? "Этот цвет сохранён." : "Предпросмотр. Чтобы запомнить цвет, нажмите «Сохранить цвет».";
    updateSettingsControls();
}

// Настройки загружаем только после готовности моста PyWebView.
async function initializeSettings() {
    if (settingsInitStarted) return;
    settingsInitStarted = true;
    try {
        const data = await window.pywebview.api.load_settings();
        if (!data || data.version !== 1 || !/^#[0-9a-f]{6}$/i.test(data.accent || "")) {
            throw new Error("Некорректные настройки цвета.");
        }
        savedAccent = data.accent.toLowerCase();
        applyAccent(savedAccent);
        applyStoredAppearance(data);
        settingsReady = true;
        window.visualSettingsSnapshot = data;
        window.dispatchEvent(new CustomEvent("visual-settings-loaded", { detail: data }));
        settingsMessage.textContent = "Цвет загружен. Выбор сохраняется после перезапуска.";
    } catch (error) {
        settingsMessage.textContent = "Не удалось загрузить настройки: " + error.message;
    } finally {
        updateSettingsControls();
    }
}

// Записываем только проверенный HEX-цвет; библиотека и плейлисты не меняются.
async function saveAccent() {
    if (!settingsReady || settingsSaving || currentAccent === savedAccent) return;
    settingsSaving = true;
    updateSettingsControls();
    try {
        const data = await window.pywebview.api.save_settings(currentAccent);
        savedAccent = data.accent.toLowerCase();
        applyAccent(savedAccent);
        settingsMessage.textContent = "Цвет сохранён. Он останется после перезапуска.";
    } catch (error) {
        settingsMessage.textContent = "Не удалось сохранить цвет: " + error.message + " Предпросмотр не сохранён.";
    } finally {
        settingsSaving = false;
        updateSettingsControls();
    }
}

accentPresets.forEach((button) => {
    button.style.setProperty("--swatch", button.dataset.accent);
    button.addEventListener("click", () => previewAccent(button.dataset.accent));
});
accentPicker.addEventListener("input", () => previewAccent(accentPicker.value));
resetAccentButton.addEventListener("click", () => previewAccent("#c9c9d1"));
saveAccentButton.addEventListener("click", saveAccent);
window.addEventListener("pywebviewready", initializeSettings);
if (window.pywebview && window.pywebview.api) void initializeSettings();

// 14. Редактор плейлиста: правая модальная панель вместо действий на карточке.
const playlistEditor = document.getElementById("playlist-editor");
const editorName = document.getElementById("editor-name");
const editorMessage = document.getElementById("editor-message");
let editingPlaylistId = null;

function openPlaylistEditor(id) {
    closeTrackMenu(false);
    if (!queuePanel.hidden) closeQueue();
    editingPlaylistId = id;
    editorMessage.textContent = "";
    syncPlaylistEditor(true);
    if (!editingPlaylistId) return;
    playlistEditor.showModal();
    editorName.focus();
}

// Не перезаписываем введённое название при ошибке записи или выборе обложки.
function syncPlaylistEditor(resetName = false) {
    if (!editingPlaylistId) return;
    const playlist = playlists.find(item => item.id === editingPlaylistId);
    if (!playlist) { closePlaylistEditor(); return; }
    if (resetName) editorName.value = playlist.name;
    const cover = document.getElementById("editor-cover");
    cover.replaceChildren(document.createTextNode("♫"));
    cover.classList.remove("has-image");
    addPlaylistCoverImage(cover, playlist.cover);
    document.getElementById("editor-track-count").textContent = `Треков: ${(playlist.tracks || []).length}`;
    playlistEditor.querySelectorAll("input, button:not(#close-playlist-editor)").forEach(control => { control.disabled = playlistsBusy; });
    document.getElementById("editor-reset-cover").disabled = playlistsBusy || !playlist.cover;
    document.getElementById("editor-change-cover").textContent = playlist.cover ? "Сменить обложку" : "Выбрать обложку";
}

async function editorMutation(action, message) {
    const ok = await mutatePlaylists(action, message);
    editorMessage.textContent = playlistsMessage.textContent;
    return ok;
}
// Возврат фокуса делаем явно: карточки могли быть пересозданы во время сохранения.
function finishPlaylistEditorClose() {
    if (!editingPlaylistId) return;
    const id = editingPlaylistId;
    editingPlaylistId = null;
    const index = playlists.findIndex(item => item.id === id);
    const target = index >= 0 ? playlistsList.children[index]?.querySelector(".playlist-edit") : screenTitle;
    (target && !document.getElementById("screen-home").hidden ? target : screenTitle).focus({ preventScroll: true });
}
function closePlaylistEditor() {
    playlistEditor.close();
    finishPlaylistEditorClose();
}
document.getElementById("close-playlist-editor").addEventListener("click", closePlaylistEditor);
playlistEditor.addEventListener("close", () => { if (!playlistEditor.open) finishPlaylistEditorClose(); });
playlistEditor.addEventListener("cancel", event => { event.preventDefault(); closePlaylistEditor(); });
playlistEditor.addEventListener("click", event => {
    if (event.target !== playlistEditor) return;
    const rect = playlistEditor.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closePlaylistEditor();
});
document.getElementById("playlist-edit-form").addEventListener("submit", async event => {
    event.preventDefault();
    const id = editingPlaylistId, name = editorName.value.trim();
    if (!name) { editorMessage.textContent = "Введите название."; editorName.focus(); return; }
    if (await editorMutation(() => window.pywebview.api.rename_playlist(id, name), "Название сохранено.")) syncPlaylistEditor(true);
});
document.getElementById("editor-change-cover").addEventListener("click", async () => {
    const id = editingPlaylistId;
    let changed = false;
    await editorMutation(async () => {
        const response = await window.pywebview.api.choose_playlist_cover(id);
        changed = response.changed;
        return response.playlists;
    }, () => changed ? "Обложка сохранена." : "Выбор обложки отменён.");
});
document.getElementById("editor-reset-cover").addEventListener("click", () => {
    const id = editingPlaylistId;
    void editorMutation(() => window.pywebview.api.reset_playlist_cover(id), "Обложка сброшена. Исходное изображение не изменено.");
});
document.getElementById("editor-delete").addEventListener("click", async () => {
    const id = editingPlaylistId;
    const playlist = playlists.find(item => item.id === id);
    if (!playlist || !window.confirm(`Удалить плейлист «${playlist.name}»? Файлы на диске останутся.`)) return;
    await editorMutation(() => window.pywebview.api.delete_playlist(id), "Плейлист удалён. Музыкальные файлы сохранены.");
});

// 15. Экран текущей песни. Опциональный фон обложки подключён через visuals.js.
let revealPlayingOnPoll = false;
const largeTrackCover = document.getElementById("large-track-cover");
function updatePlayingPresentation(meta, title, state) {
    setSongCover(largeTrackCover, meta?.cover_url);
    document.getElementById("large-track-title").textContent = title;
    document.getElementById("large-track-title").title = title;
    renderArtistLinks(document.getElementById("large-track-artist"), meta?.artist,
        window.i18n.t("Исполнитель не указан"));
    document.getElementById("large-track-state").textContent = stateLabels[state] || "";
    window.playerVisuals?.trackChanged(meta, state);
}
playerCover.addEventListener("click", () => { if (currentTrackPath) showScreen("playing"); });
document.getElementById("back-to-tracks").addEventListener("click", () => {
    const kind = playbackLibraryContext?.kind;
    restorePlaybackLibraryContext();
    showScreen(kind === 'online' ? 'online' : kind === 'artist' ? 'artist' : 'music');
    document.querySelector('.workspace').scrollTop = playbackLibraryContext?.scrollTop || 0;
    screenTitle.focus({ preventScroll: true });
});

// Summary uses textContent for user data; no interpolation into HTML.
function renderPlaylistSummary(playlist) {
    const summary = document.getElementById('playlist-summary');
    summary.hidden = !playlist;
    summary.parentElement.classList.toggle('has-playlist', Boolean(playlist));
    if (!playlist) return;
    document.getElementById('playlist-summary-name').textContent = playlist.name;
    document.getElementById('playlist-summary-count').textContent = `Треков: ${(playlist.tracks || []).length}`;
    const cover = document.getElementById('playlist-summary-cover');
    const nextCover = playlist.cover || '';
    if (cover.dataset.cover !== nextCover) {
        cover.dataset.cover = nextCover;
        cover.replaceChildren(document.createTextNode('♫'));
        addPlaylistCoverImage(cover, playlist.cover);
    }
    document.getElementById('playlist-summary-edit').disabled = playlistsBusy;
}
document.getElementById('playlist-summary-edit').addEventListener('click', () => {
    if (activePlaylistId) openPlaylistEditor(activePlaylistId);
});

// 16. Форма карточек и обои. Предпросмотр не пишет JSON; кнопки сохранения пишут.
const playlistShape = document.getElementById("playlist-shape");
const saveShapeButton = document.getElementById("save-shape-button");
const shapeMessage = document.getElementById("shape-message");
const wallpaperLayer = document.getElementById("wallpaper-layer");
const wallpaperDim = document.getElementById("wallpaper-dim");
const wallpaperBlur = document.getElementById("wallpaper-blur");
const wallpaperMessage = document.getElementById("wallpaper-message");
let savedShape = "square";
let wallpaperFile = null;
let savedDim = 65, savedBlur = 0;
let wallpaperLoadRevision = 0;

// Настройки предыдущей версии без новых полей остаются совместимыми.
function applyStoredAppearance(data) {
    const shape = data.playlist_shape ?? "square";
    const file = data.wallpaper ?? null;
    const dim = data.wallpaper_dim ?? 65, blur = data.wallpaper_blur ?? 0;
    if (!["square", "landscape"].includes(shape) ||
        (file !== null && !/^[0-9a-f]{32}\.(jpg|gif|mp4|webm)$/.test(file)) ||
        !Number.isInteger(dim) || dim < 0 || dim > 90 || !Number.isInteger(blur) || blur < 0 || blur > 30) {
        throw new Error("Некорректные настройки оформления.");
    }
    savedShape = shape;
    playlistShape.value = shape;
    applyPlaylistShape();
    applyStoredWallpaper(data);
    shapeMessage.textContent = "Форма загружена.";
}
function applyPlaylistShape() {
    document.documentElement.dataset.playlistShape = playlistShape.value;
}
function updateAppearanceControls() {
    const disabled = !settingsReady || settingsSaving;
    playlistShape.disabled = disabled;
    saveShapeButton.disabled = disabled || playlistShape.value === savedShape;
    document.getElementById("choose-wallpaper").disabled = disabled;
    document.getElementById("reset-wallpaper").disabled = disabled || !wallpaperFile;
    wallpaperDim.disabled = disabled || !wallpaperFile;
    wallpaperBlur.disabled = disabled || !wallpaperFile;
    document.getElementById("save-wallpaper").disabled = disabled || !wallpaperFile ||
        (Number(wallpaperDim.value) === savedDim && Number(wallpaperBlur.value) === savedBlur);
}
function previewWallpaperEffects() {
    document.documentElement.style.setProperty("--wallpaper-dim", Number(wallpaperDim.value) / 100);
    document.documentElement.style.setProperty("--wallpaper-blur", wallpaperBlur.value + "px");
    document.getElementById("wallpaper-dim-value").value = wallpaperDim.value + "%";
    document.getElementById("wallpaper-blur-value").value = wallpaperBlur.value + " px";
}

// Сперва загружаем копию, потом показываем. Запоздалая загрузка старых обоев игнорируется.
function applyStoredWallpaper(data) {
    wallpaperFile = data.wallpaper ?? null;
    savedDim = data.wallpaper_dim ?? 65;
    savedBlur = data.wallpaper_blur ?? 0;
    wallpaperDim.value = String(savedDim);
    wallpaperBlur.value = String(savedBlur);
    previewWallpaperEffects();
    const revision = ++wallpaperLoadRevision;
    // Visual preferences must not detach/reload an already displayed GIF or video.
    if (wallpaperFile && wallpaperLayer.dataset.file === wallpaperFile && !wallpaperLayer.hidden) {
        window.dispatchEvent(new Event("wallpaper-rendered"));
        return;
    }
    wallpaperLayer.dataset.file = wallpaperFile || '';
    wallpaperLayer.hidden = true;
    wallpaperLayer.style.backgroundImage = "none";
    document.documentElement.classList.remove("has-wallpaper");
    if (!wallpaperFile) {window.dispatchEvent(new CustomEvent('wallpaper-media',{detail:{url:null}})); wallpaperMessage.textContent = "Обои не выбраны. Используется обычный тёмный фон."; return; }
    const url = "/api/assets/wallpapers/" + encodeURIComponent(wallpaperFile);
    if (/\.(mp4|webm)$/.test(wallpaperFile)) {
        wallpaperLayer.hidden=false;document.documentElement.classList.add('has-wallpaper');
        window.dispatchEvent(new CustomEvent('wallpaper-media',{detail:{url}}));
        wallpaperMessage.textContent='Видеофон выбран. Звук отключён.';return;
    }
    window.dispatchEvent(new CustomEvent('wallpaper-media',{detail:{url:null}}));
    const image = new Image();
    image.onload = () => {
        if (revision !== wallpaperLoadRevision) return;
        wallpaperLayer.style.backgroundImage = `url("${url}")`;
        wallpaperLayer.hidden = false;
        document.documentElement.classList.add("has-wallpaper");
        wallpaperMessage.textContent = "Копия обоев загружена. Эффекты можно менять ниже.";
        window.dispatchEvent(new Event("wallpaper-rendered"));
    };
    image.onerror = () => {
        if (revision !== wallpaperLoadRevision) return;
        wallpaperMessage.textContent = "Копия обоев не найдена или не читается. Выберите изображение заново либо сбросьте обои.";
    };
    image.src = url;
}

// Блокировка исключает одновременное сохранение цвета, формы и обоев из интерфейса.
async function appearanceMutation(action, onSuccess, messageElement) {
    if (!settingsReady || settingsSaving) return;
    settingsSaving = true;
    updateSettingsControls();
    try { onSuccess(await action()); }
    catch (error) { messageElement.textContent = "Не удалось сохранить: " + error.message; }
    finally { settingsSaving = false; updateSettingsControls(); }
}
playlistShape.addEventListener("change", () => {
    applyPlaylistShape();
    shapeMessage.textContent = playlistShape.value === savedShape ? "Эта форма сохранена." : "Предпросмотр. Нажмите «Сохранить форму».";
    updateAppearanceControls();
    window.playerVisuals?.updateControls();
});
saveShapeButton.addEventListener("click", () => appearanceMutation(
    () => window.pywebview.api.save_playlist_shape(playlistShape.value),
    data => { savedShape = data.playlist_shape; shapeMessage.textContent = "Форма сохранена."; }, shapeMessage
));
[wallpaperDim, wallpaperBlur].forEach(control => control.addEventListener("input", () => {
    previewWallpaperEffects();
    wallpaperMessage.textContent = "Предпросмотр. Чтобы запомнить эффекты, нажмите «Сохранить эффекты».";
    updateAppearanceControls();
    window.playerVisuals?.updateControls();
}));
document.getElementById("choose-wallpaper").addEventListener("click", () => appearanceMutation(
    () => window.pywebview.api.choose_wallpaper(),
    response => { if (response.changed) applyStoredWallpaper(response.settings); else wallpaperMessage.textContent = "Выбор изображения отменён. Обои не изменены."; }, wallpaperMessage
));
document.getElementById("save-wallpaper").addEventListener("click", () => appearanceMutation(
    () => window.pywebview.api.save_wallpaper_effects(Number(wallpaperDim.value), Number(wallpaperBlur.value)),
    data => { savedDim = data.wallpaper_dim; savedBlur = data.wallpaper_blur; wallpaperMessage.textContent = "Эффекты обоев сохранены."; }, wallpaperMessage
));
document.getElementById("reset-wallpaper").addEventListener("click", () => appearanceMutation(
    () => window.pywebview.api.reset_wallpaper(),
    data => { applyStoredWallpaper(data); wallpaperMessage.textContent = "Обои и эффекты сброшены. Исходное изображение не изменено."; }, wallpaperMessage
));

// Match the initial Home button even before the first navigation.
addFilesButton.classList.toggle('home-add-button', activeScreen === 'home');

document.addEventListener('language-changed', () => {
    if (libraryReady) renderLibrary();
    if (!queuePanel.hidden) void refreshQueue();
    const dialog=document.getElementById('track-details-dialog');
    if (dialog.open && dialog.dataset.path) showTrackDetails(dialog.dataset.path);
    window.refreshPlayerState?.();
});
