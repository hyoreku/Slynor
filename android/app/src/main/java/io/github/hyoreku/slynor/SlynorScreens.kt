package io.github.hyoreku.slynor

import androidx.activity.compose.BackHandler
import androidx.annotation.DrawableRes
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.paint
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.painter.BitmapPainter
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.media3.session.MediaController
import java.text.Collator
import java.util.Locale

private enum class Destination(val title: String, @param:DrawableRes val icon: Int) {
    Home("Главная", R.drawable.ic_home),
    Library("Моя музыка", R.drawable.ic_music),
    Playlists("Плейлисты", R.drawable.ic_playlist),
    Settings("Настройки", R.drawable.ic_settings),
    Player("Сейчас играет", R.drawable.ic_play),
}

private val dockDestinations = listOf(Destination.Home, Destination.Library, Destination.Playlists)

private val screenMargin = 20.dp
private val screenWidth = 740.dp

@Composable
internal fun SlynorScreens(
    library: LocalLibrary,
    data: LibrarySnapshot,
    player: MediaController?,
    playback: Playback,
    status: String?,
    importing: Boolean,
    theme: ThemeMode,
    appearance: AppearanceSettings,
    onTheme: (ThemeMode) -> Unit,
    onAdd: () -> Unit,
    onAddFolder: () -> Unit,
    onPlay: (LocalTrack, List<LocalTrack>) -> Unit,
    onFavorite: (LocalTrack) -> Unit,
    onRemove: (LocalTrack) -> Unit,
) {
    var destination by rememberSaveable { mutableStateOf(Destination.Home) }
    var showPlayer by rememberSaveable { mutableStateOf(false) }
    var showQueue by rememberSaveable { mutableStateOf(false) }
    var showEditor by rememberSaveable { mutableStateOf(false) }
    var editorId by rememberSaveable { mutableStateOf<String?>(null) }
    var returnFromSettings by rememberSaveable { mutableStateOf(Destination.Home) }
    val screenState = rememberSaveableStateHolder()
    var search by rememberSaveable { mutableStateOf("") }
    var favoritesOnly by rememberSaveable { mutableStateOf(false) }
    var sort by remember(library) {
        val stored = library.storage.getString("librarySort", null)
        mutableStateOf(LibrarySort.entries.firstOrNull { it.name == stored } ?: LibrarySort.Added)
    }
    var playlistId by rememberSaveable { mutableStateOf<String?>(null) }
    var addTrackId by rememberSaveable { mutableStateOf<String?>(null) }
    var detailsId by rememberSaveable { mutableStateOf<String?>(null) }
    val tracks = data.tracks
    val playlist = data.playlists.firstOrNull { it.id == playlistId }
    val libraryScroll = rememberLazyListState()
    val byId = remember(tracks) { tracks.associateBy(LocalTrack::id) }
    val recentPlays = remember(data.history, byId) { data.history.mapNotNull(byId::get) }
    val current = byId[playback.trackId]
    val showingTrack = showPlayer && player != null && current != null
    val visibleScreen = if (showingTrack) Destination.Player else destination
    val keyboardVisible = WindowInsets.ime.getBottom(LocalDensity.current) > 0
    val colors = MaterialTheme.colorScheme

    fun goBack() {
        when {
            showingTrack -> showPlayer = false
            destination == Destination.Playlists && playlist != null -> playlistId = null
            destination == Destination.Settings -> destination = returnFromSettings
            else -> destination = Destination.Home
        }
    }
    BackHandler(showingTrack || destination != Destination.Home, onBack = ::goBack)

    fun showPlaylist(id: String?) {
        playlistId = id
        destination = Destination.Playlists
    }

    fun editPlaylist(id: String? = null) {
        editorId = id
        showEditor = true
    }


    addTrackId?.let(byId::get)?.let {
        AddTrackToPlaylist(it, data.playlists, library) { addTrackId = null }
    }
    detailsId?.let(byId::get)?.let { TrackDetails(it) { detailsId = null } }
    if (showQueue && player != null) {
        QueueSheet(byId, playback, player, { showQueue = false }) {
            showPlayer = false
            destination = Destination.Library
        }
    }
    if (showEditor) {
        val editing = remember(editorId) { data.playlists.firstOrNull { it.id == editorId } }
        if (editorId == null || editing != null) {
            PlaylistEditor(library, editing, { showEditor = false }, onSaved = {
                if (editorId == null) showPlaylist(it.id)
            }, onDeleted = {
                if (playlistId == editorId) playlistId = null
            })
        }
    }

    Surface(Modifier.fillMaxSize(), color = colors.background) {
        val wallpaper = appearance.wallpaper
        val backdrop = if (wallpaper == null) Modifier else Modifier.paint(
            painter = remember(wallpaper) { BitmapPainter(wallpaper.asImageBitmap()) },
            sizeToIntrinsics = false, contentScale = ContentScale.Crop, alpha = .3f,
        )
        Column(
            Modifier.fillMaxSize().then(backdrop).background(
                Brush.verticalGradient(colorStops = arrayOf(
                    0f to lerp(colors.background, colors.primary, .15f).copy(alpha = if (wallpaper == null) 1f else .25f),
                    .55f to colors.background.copy(alpha = if (wallpaper == null) 1f else .82f),
                )),
            ).safeDrawingPadding().imePadding(),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            AppHeader(
                title = if (showingTrack) "Сейчас играет" else "Slynor",
                playingScreen = showingTrack,
                showBack = showingTrack || destination == Destination.Settings ||
                    (destination == Destination.Playlists && playlist != null),
                canAdd = !showingTrack && destination != Destination.Settings && !importing,
                showMenu = !showingTrack && destination != Destination.Settings,
                importing = importing,
                queueSize = playback.queue.size,
                onQueue = { showQueue = true },
                onBack = ::goBack,
                onAdd = onAdd,
                onAddFolder = onAddFolder,
                onSettings = {
                    returnFromSettings = destination
                    destination = Destination.Settings
                },
            )
            AnimatedVisibility(
                visible = status != null,
                enter = fadeIn(tween(160)) + expandVertically(),
                exit = fadeOut(tween(120)) + shrinkVertically(),
            ) {
                Surface(
                    Modifier.widthIn(max = screenWidth).fillMaxWidth()
                        .padding(horizontal = screenMargin, vertical = 6.dp)
                        .semantics { liveRegion = LiveRegionMode.Polite },
                    shape = RoundedCornerShape(14.dp), color = colors.surfaceContainer,
                ) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(status.orEmpty(), color = colors.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
                        if (importing) LinearProgressIndicator(Modifier.fillMaxWidth().height(3.dp))
                    }
                }
            }
            AnimatedContent(
                targetState = visibleScreen,
                transitionSpec = {
                    screenMotion(expanding = targetState == Destination.Player, collapsing = initialState == Destination.Player)
                },
                modifier = Modifier.weight(1f).widthIn(max = screenWidth).fillMaxWidth(),
                label = "Переходы Slynor",
            ) { screen ->
                screenState.SaveableStateProvider(screen) {
                    when (screen) {
                        Destination.Player -> if (player != null && current != null) {
                            TrackScreen(player, playback, current, onFavorite = { onFavorite(current) })
                        }
                        Destination.Home -> HomeScreen(
                            library, tracks, data.playlists, recentPlays, current, playback, player,
                            importing, onAdd, onPlay, { destination = Destination.Library }, { showPlayer = true },
                            ::showPlaylist, { editPlaylist() }, { editPlaylist(it.id) },
                        )
                        Destination.Library -> {
                            val query = search.trim()
                            val filtered = remember(tracks, query, favoritesOnly, sort) {
                                sort.order(tracks.filter { (!favoritesOnly || it.favorite) && it.matches(query) })
                            }
                            Column(Modifier.fillMaxSize().padding(horizontal = screenMargin)) {
                                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                    Box(Modifier.weight(1f)) { ScreenTitle(screen.title, trackCount(filtered.size)) }
                                    TrackMenu("Сортировка библиотеки") { close ->
                                        for (option in LibrarySort.entries) {
                                            DropdownMenuItem(
                                                text = { Text(option.title) },
                                                onClick = {
                                                    close()
                                                    sort = option
                                                    library.storage.edit().putString("librarySort", option.name).apply()
                                                    libraryScroll.requestScrollToItem(0)
                                                },
                                                modifier = Modifier.semantics { selected = sort == option },
                                            )
                                        }
                                    }
                                    IconButton(
                                        enabled = player != null && !importing && filtered.size > 1,
                                        onClick = { filtered.shuffled().let { onPlay(it.first(), it) } },
                                    ) {
                                        Glyph(R.drawable.ic_shuffle, "Перемешать и включить")
                                    }
                                }
                                OutlinedTextField(
                                    value = search, onValueChange = {
                                        search = it
                                        libraryScroll.requestScrollToItem(0)
                                    },
                                    modifier = Modifier.fillMaxWidth(),
                                    placeholder = { Text("Трек, исполнитель или альбом") },
                                    leadingIcon = { Glyph(R.drawable.ic_search, null) },
                                    trailingIcon = if (search.isEmpty()) null else {
                                        { IconButton(onClick = { search = "" }) { Glyph(R.drawable.ic_close, "Очистить поиск") } }
                                    },
                                    singleLine = true, shape = RoundedCornerShape(16.dp),
                                )
                                Row(
                                    Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 10.dp),
                                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                                ) {
                                    FilterChip(
                                        selected = !favoritesOnly, onClick = {
                                            favoritesOnly = false
                                            libraryScroll.requestScrollToItem(0)
                                        },
                                        label = { Text("Все треки", maxLines = 1, overflow = TextOverflow.Ellipsis) },
                                    )
                                    FilterChip(
                                        selected = favoritesOnly, onClick = {
                                            favoritesOnly = true
                                            libraryScroll.requestScrollToItem(0)
                                        },
                                        label = { Text("Избранное", maxLines = 1, overflow = TextOverflow.Ellipsis) },
                                        leadingIcon = { Glyph(R.drawable.ic_heart_outline, null, Modifier.size(17.dp)) },
                                    )
                                }
                                LazyColumn(
                                    state = libraryScroll,
                                    verticalArrangement = Arrangement.spacedBy(8.dp),
                                    contentPadding = PaddingValues(bottom = 14.dp),
                                ) {
                                    if (filtered.isEmpty()) {
                                        item(key = "empty-library") {
                                            Box(Modifier.animateItem()) {
                                                when {
                                                    tracks.isEmpty() -> EmptyState(
                                                        R.drawable.ic_music, "Библиотека пуста", "Выберите аудиофайлы на устройстве.",
                                                        "Добавить музыку", onAdd, enabled = !importing,
                                                    )
                                                    favoritesOnly && tracks.none { it.favorite } -> EmptyState(
                                                        R.drawable.ic_heart_outline, "Избранное пусто",
                                                        "Добавьте любимый трек через его меню.", "Все треки", { favoritesOnly = false },
                                                    )
                                                    else -> EmptyState(
                                                        R.drawable.ic_search, "Ничего не найдено", "Попробуйте другой запрос.",
                                                        "Очистить поиск", { search = "" },
                                                    )
                                                }
                                            }
                                        }
                                    }
                                    items(filtered, key = LocalTrack::id) { track ->
                                        TrackRow(track, track == current, player != null && !importing,
                                            { onPlay(track, filtered) }, Modifier.animateItem()) {
                                            if (!importing) TrackMenu { close ->
                                                DropdownMenuItem(
                                                    text = { Text(if (track.favorite) "Убрать из избранного" else "В избранное") },
                                                    onClick = {
                                                        close()
                                                        onFavorite(track)
                                                    },
                                                )
                                                if (player != null && track != current) QueueMenuItems(player, track, close)
                                                DropdownMenuItem(text = { Text("Добавить в плейлист") }, onClick = {
                                                    close()
                                                    addTrackId = track.id
                                                })
                                                DropdownMenuItem(text = { Text("О треке") }, onClick = {
                                                    close()
                                                    detailsId = track.id
                                                })
                                                DropdownMenuItem(text = { Text("Убрать из библиотеки") }, onClick = {
                                                    close()
                                                    onRemove(track)
                                                })
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        Destination.Playlists -> PlaylistsScreen(
                            library, data, playlist, current, player, { playlistId = it },
                            { editPlaylist() }, { editPlaylist(it.id) }, onPlay,
                        )
                        Destination.Settings -> SettingsScreen(theme, onTheme, appearance)
                    }
                }
            }
            AnimatedVisibility(
                visible = !showingTrack && !keyboardVisible && destination != Destination.Settings,
                enter = fadeIn(tween(230)) + slideInVertically(tween(280)) { it / 2 } + expandVertically(),
                exit = fadeOut(tween(130)) + slideOutVertically(tween(240)) { it / 2 } + shrinkVertically(),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    AnimatedVisibility(
                        visible = player != null && current != null,
                        enter = fadeIn(tween(190)) + expandVertically(),
                        exit = fadeOut(tween(140)) + shrinkVertically(),
                    ) {
                        if (player != null && current != null) {
                            Box(Modifier.widthIn(max = screenWidth).fillMaxWidth().padding(horizontal = 12.dp)) {
                                PlayerBar(current, playback, { showPlayer = true }) { togglePlayback(player, playback) }
                            }
                        }
                    }
                    NavigationBar(
                        modifier = Modifier.widthIn(max = screenWidth).fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)
                            .clip(RoundedCornerShape(22.dp)),
                        containerColor = colors.surface, tonalElevation = 0.dp,
                        windowInsets = WindowInsets(0, 0, 0, 0),
                    ) {
                        for (target in dockDestinations) {
                            NavigationBarItem(
                                selected = destination == target, onClick = { destination = target },
                                icon = { Glyph(target.icon, null) },
                                label = { Text(target.title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                                colors = NavigationBarItemDefaults.colors(
                                    selectedIconColor = colors.primary, selectedTextColor = colors.onSurface,
                                    indicatorColor = colors.primaryContainer,
                                    unselectedIconColor = colors.onSurfaceVariant, unselectedTextColor = colors.onSurfaceVariant,
                                ),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AppHeader(
    title: String, playingScreen: Boolean, showBack: Boolean, canAdd: Boolean, showMenu: Boolean, importing: Boolean,
    queueSize: Int, onQueue: () -> Unit, onBack: () -> Unit, onAdd: () -> Unit, onAddFolder: () -> Unit, onSettings: () -> Unit,
) {
    Row(
        Modifier.widthIn(max = screenWidth).fillMaxWidth().heightIn(min = 59.dp)
            .padding(horizontal = 12.dp, vertical = 5.dp),
        horizontalArrangement = Arrangement.spacedBy(3.dp), verticalAlignment = Alignment.CenterVertically,
    ) {
        if (showBack) {
            IconButton(onClick = onBack) {
                Glyph(if (playingScreen) R.drawable.ic_collapse else R.drawable.ic_back,
                    if (playingScreen) "Свернуть плеер" else "Вернуться")
            }
        } else Image(painterResource(R.drawable.slynor_icon), null,
            Modifier.padding(horizontal = 7.dp).size(34.dp).clip(RoundedCornerShape(9.dp)))
        Text(title, Modifier.weight(1f), style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        if (playingScreen) {
            FilledTonalButton(onClick = onQueue, enabled = queueSize > 0) {
                Glyph(R.drawable.ic_queue, null)
                Text("Очередь · $queueSize", Modifier.padding(start = 5.dp))
            }
        }
        if (canAdd) IconButton(onClick = onAdd) { Glyph(R.drawable.ic_add, "Добавить") }
        if (showMenu) {
            TrackMenu("Меню Slynor") { close ->
                DropdownMenuItem(text = { Text("Добавить папку") }, enabled = !importing, onClick = {
                    close()
                    onAddFolder()
                })
            }
            IconButton(onClick = onSettings) { Glyph(R.drawable.ic_settings, "Настройки") }
        }
    }
}

@Composable
private fun QueueSheet(
    tracks: Map<String, LocalTrack>, state: Playback, player: MediaController,
    onDismiss: () -> Unit, onLibrary: () -> Unit,
) {
    val queue = remember(state.queue, tracks) {
        state.queue.mapIndexedNotNull { index, id -> tracks[id]?.let { index to it } }
    }
    SlynorSheet("Очередь", "Закрыть очередь", onDismiss) { close ->
        if (queue.isEmpty()) {
            Box(Modifier.padding(20.dp)) {
                EmptyState(R.drawable.ic_queue, "Очередь пуста", "Выберите трек в библиотеке.",
                    "Открыть мою музыку", { onLibrary(); close() })
            }
        } else {
            if (state.queueIndex in state.queue.indices) {
                Text("${state.queueIndex + 1} из ${state.queue.size}", Modifier.padding(horizontal = 20.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
            }
            LazyColumn(
                Modifier.fillMaxWidth().heightIn(max = 465.dp),
                verticalArrangement = Arrangement.spacedBy(7.dp), contentPadding = PaddingValues(16.dp),
            ) {
                items(queue, key = { it.second.id }) { (index, track) ->
                    TrackRow(track, index == state.queueIndex, true, { player.playFromQueue(index) }, Modifier.animateItem()) {
                        TrackMenu { dismissMenu ->
                            if (index != state.queueIndex) QueueMenuItems(player, track, dismissMenu)
                            if (index > 0) {
                                DropdownMenuItem(text = { Text("Переместить выше") }, onClick = {
                                    dismissMenu()
                                    player.moveQueueTrack(track, -1)
                                })
                            }
                            if (index < state.queue.lastIndex) {
                                DropdownMenuItem(text = { Text("Переместить ниже") }, onClick = {
                                    dismissMenu()
                                    player.moveQueueTrack(track, 1)
                                })
                            }
                            DropdownMenuItem(text = { Text("Убрать из очереди") }, onClick = {
                                dismissMenu()
                                player.removeFromQueue(track)
                            })
                        }
                    }
                }
            }
        }
    }
}

@Composable
internal fun ScreenTitle(title: String, detail: String? = null) {
    Column(Modifier.padding(top = 12.dp, bottom = 16.dp)) {
        Text(title, Modifier.semantics { heading() }, style = MaterialTheme.typography.headlineLarge)
        detail?.let {
            Text(it, Modifier.padding(top = 3.dp), color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyMedium)
        }
    }
}

internal fun trackCount(total: Int): String {
    val noun = when {
        total % 100 in 11..14 -> "треков"
        total % 10 == 1 -> "трек"
        total % 10 in 2..4 -> "трека"
        else -> "треков"
    }
    return "$total $noun"
}

@Composable
private fun HomeScreen(
    library: LocalLibrary, tracks: List<LocalTrack>, playlists: List<Playlist>, history: List<LocalTrack>,
    current: LocalTrack?, playback: Playback, player: MediaController?, importing: Boolean, onAdd: () -> Unit,
    onPlay: (LocalTrack, List<LocalTrack>) -> Unit, onLibrary: () -> Unit, onOpenPlayer: () -> Unit,
    onPlaylist: (String?) -> Unit, onCreate: () -> Unit, onEdit: (Playlist) -> Unit,
) {
    val recent = remember(tracks) { tracks.asReversed().take(5) }
    LazyColumn(
        Modifier.fillMaxSize(), contentPadding = PaddingValues(start = screenMargin, end = screenMargin, bottom = 14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item { ScreenTitle("Главная", trackCount(tracks.size).takeIf { tracks.isNotEmpty() }) }
        item(key = "current-track") {
            AnimatedContent(
                targetState = current,
                contentKey = { it?.id },
                transitionSpec = { screenMotion() },
                label = "Карточка текущего трека",
            ) { track ->
                if (track != null && player != null) {
                    CurrentTrackCard(track, playback, onOpenPlayer) { togglePlayback(player, playback) }
                } else {
                    LibraryCard(tracks.isEmpty(), importing, onAdd, onLibrary)
                }
            }
        }
        item(key = "playlist-shelf-heading") {
            Row(Modifier.fillMaxWidth().padding(top = 7.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Ваши плейлисты", Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.titleLarge)
                TextButton(onClick = onCreate) {
                    Glyph(R.drawable.ic_add, null)
                    Text("Плейлист", Modifier.padding(start = 5.dp))
                }
            }
        }
        if (playlists.isNotEmpty()) {
            item(key = "playlist-shelf") {
                LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    items(playlists, key = Playlist::id) { playlist ->
                        PlaylistTile(library, playlist, { onPlaylist(playlist.id) }, { onEdit(playlist) },
                            modifier = Modifier.width(146.dp).animateItem())
                    }
                }
            }
        }
        if (recent.isNotEmpty()) {
            item {
                Text("Недавно добавленные", Modifier.padding(top = 14.dp, bottom = 4.dp).semantics { heading() },
                    style = MaterialTheme.typography.titleLarge)
            }
            items(recent, key = { "recent:${it.id}" }) { track ->
                TrackRow(track, track == current, player != null && !importing, { onPlay(track, tracks) }, Modifier.animateItem())
            }
        }
        if (history.isNotEmpty()) {
            item {
                Text("Недавно слушали", Modifier.padding(top = 14.dp, bottom = 4.dp).semantics { heading() },
                    style = MaterialTheme.typography.titleLarge)
            }
            items(history.take(15), key = { "history:${it.id}" }) { track ->
                TrackRow(track, track == current, player != null && !importing, { onPlay(track, history) }, Modifier.animateItem())
            }
        }
    }
}

@Composable
private fun LibraryCard(empty: Boolean, importing: Boolean, onAdd: () -> Unit, onLibrary: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    Surface(shape = RoundedCornerShape(22.dp), border = BorderStroke(1.dp, colors.outlineVariant),
        color = colors.surface) {
        Column(
            Modifier.fillMaxWidth().background(
                Brush.horizontalGradient(listOf(lerp(colors.surface, colors.primary, .18f), colors.surfaceContainer, colors.surface)),
            ).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Image(painterResource(R.drawable.slynor_icon), null, Modifier.size(64.dp).clip(RoundedCornerShape(16.dp)))
            Text(if (empty) "Включите первую песню" else "Моя музыка", style = MaterialTheme.typography.headlineSmall)
            if (empty) Text("Добавьте аудиофайлы с устройства.", color = colors.onSurfaceVariant)
            Button(onClick = if (empty) onAdd else onLibrary, enabled = !empty || !importing) {
                if (empty) Glyph(R.drawable.ic_add, null, Modifier.size(17.dp))
                Text(if (empty) "Добавить музыку" else "Открыть библиотеку", Modifier.padding(start = if (empty) 6.dp else 0.dp))
            }
        }
    }
}

@Composable
internal fun EmptyState(@DrawableRes icon: Int, title: String, description: String, action: String,
                       onAction: () -> Unit, enabled: Boolean = true) {
    val colors = MaterialTheme.colorScheme
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp), color = colors.surface,
        border = BorderStroke(1.dp, colors.outlineVariant)) {
        Column(Modifier.padding(22.dp), horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(9.dp)) {
            Glyph(icon, null, Modifier.size(28.dp), tint = colors.primary)
            Text(title, style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center)
            Text(description, color = colors.onSurfaceVariant, textAlign = TextAlign.Center,
                style = MaterialTheme.typography.bodyMedium)
            TextButton(onClick = onAction, enabled = enabled) { Text(action) }
        }
    }
}

@Composable
internal fun TrackRow(
    track: LocalTrack, active: Boolean, enabled: Boolean, onPlay: () -> Unit,
    modifier: Modifier = Modifier, actions: @Composable () -> Unit = {},
) {
    val colors = MaterialTheme.colorScheme
    val shape = RoundedCornerShape(14.dp)
    val background by animateColorAsState(if (active) colors.primaryContainer else colors.surface, tween(215), label = "Активный трек")
    Surface(modifier, shape = shape, border = BorderStroke(1.dp, if (active) colors.outline else colors.outlineVariant),
        color = background) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 58.dp).clip(shape)
                .semantics { selected = active }.clickable(enabled = enabled, onClick = onPlay)
                .padding(start = 8.dp, end = 6.dp, top = 7.dp, bottom = 7.dp),
            horizontalArrangement = Arrangement.spacedBy(11.dp), verticalAlignment = Alignment.CenterVertically,
        ) {
            Cover(rememberArtwork(track.uri), Modifier.size(42.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(track.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleMedium)
                val subtitle = listOf(track.artist, track.album).filter(String::isNotBlank).joinToString(" · ")
                if (subtitle.isNotBlank()) {
                    Text(subtitle, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        color = colors.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                }
            }
            if (track.duration > 0) Text(formatTime(track.duration), style = MaterialTheme.typography.bodySmall)
            if (track.favorite) Glyph(R.drawable.ic_heart, "В избранном", Modifier.size(16.dp), tint = colors.primary)
            actions()
        }
    }
}

@Composable
internal fun TrackMenu(label: String = "Действия для трека", content: @Composable (() -> Unit) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { expanded = true }) { Glyph(R.drawable.ic_more, label) }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            content { expanded = false }
        }
    }
}

@Composable
internal fun QueueMenuItems(player: MediaController, track: LocalTrack, close: () -> Unit) {
    DropdownMenuItem(text = { Text("Играть следующим") }, onClick = {
        close()
        player.enqueue(track, playNext = true)
    })
    DropdownMenuItem(text = { Text("В конец очереди") }, onClick = {
        close()
        player.enqueue(track, playNext = false)
    })
}

@Composable
internal fun Glyph(@DrawableRes icon: Int, description: String?, modifier: Modifier = Modifier,
                   tint: Color = androidx.compose.material3.LocalContentColor.current) {
    Icon(painterResource(icon), description, modifier.size(20.dp), tint = tint)
}

private enum class LibrarySort(val title: String) {
    Added("По порядку добавления"),
    Newest("Сначала новые"),
    Title("По названию"),
    Artist("По исполнителю"),
    Album("По альбому"),
    Duration("По длительности");

    fun order(tracks: List<LocalTrack>): List<LocalTrack> {
        val localized = Collator.getInstance(Locale.forLanguageTag("ru")).apply { strength = Collator.PRIMARY }
        val title = compareBy<LocalTrack, String>(localized, LocalTrack::title)
        return when (this) {
            Added -> tracks
            Newest -> tracks.asReversed()
            Title -> tracks.sortedWith(title)
            Artist -> tracks.sortedWith(compareBy<LocalTrack> { it.artist.isBlank() }
                .thenBy(localized, LocalTrack::artist).then(title))
            Album -> tracks.sortedWith(compareBy<LocalTrack> { it.album.isBlank() }
                .thenBy(localized, LocalTrack::album).then(title))
            Duration -> tracks.sortedWith(compareByDescending<LocalTrack> { it.duration }.then(title))
        }
    }
}

@Composable
private fun TrackDetails(track: LocalTrack, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(track.title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (track.artist.isNotBlank()) Text("Исполнитель · ${track.artist}")
                if (track.album.isNotBlank()) Text("Альбом · ${track.album}")
                if (track.duration > 0) Text("Длительность · ${formatTime(track.duration)}")
                if (track.artist.isBlank() && track.album.isBlank() && track.duration == 0L) {
                    Text("У файла нет дополнительных метаданных.")
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Закрыть") } },
    )
}
