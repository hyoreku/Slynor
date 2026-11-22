package io.github.hyoreku.slynor

import androidx.activity.compose.BackHandler
import androidx.annotation.DrawableRes
import androidx.compose.animation.Crossfade
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
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
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

private enum class Destination(val title: String, @param:DrawableRes val icon: Int) {
    Home("Главная", R.drawable.ic_home),
    Library("Моя музыка", R.drawable.ic_music),
    Queue("Очередь", R.drawable.ic_queue),
}

private val screenMargin = 20.dp
private val screenWidth = 740.dp

@Composable
internal fun SlynorScreens(
    tracks: List<LocalTrack>,
    player: MediaController?,
    playback: Playback,
    status: String?,
    importing: Boolean,
    onAdd: () -> Unit,
    onPlay: (LocalTrack, List<LocalTrack>) -> Unit,
    onFavorite: (LocalTrack) -> Unit,
    onRemove: (LocalTrack) -> Unit,
) {
    var destination by rememberSaveable { mutableStateOf(Destination.Home) }
    var showPlayer by rememberSaveable { mutableStateOf(false) }
    var search by rememberSaveable { mutableStateOf("") }
    var favoritesOnly by rememberSaveable { mutableStateOf(false) }
    val libraryScroll = rememberLazyListState()
    val byId = remember(tracks) { tracks.associateBy { it.uri.toString() } }
    val current = byId[playback.trackId]
    val showingTrack = showPlayer && player != null && current != null
    val keyboardVisible = WindowInsets.ime.getBottom(LocalDensity.current) > 0
    val colors = MaterialTheme.colorScheme

    BackHandler(showingTrack || destination != Destination.Home) {
        if (showingTrack) showPlayer = false else destination = Destination.Home
    }

    Surface(Modifier.fillMaxSize(), color = colors.background) {
        Column(
            Modifier.fillMaxSize().background(
                Brush.verticalGradient(colorStops = arrayOf(
                    0f to Color(0xFF241C2C),
                    .50f to colors.background,
                )),
            ).safeDrawingPadding().imePadding(),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            AppHeader(
                title = if (showingTrack) "Сейчас играет" else "Slynor",
                trackScreen = showingTrack, canAdd = tracks.isNotEmpty() && !importing,
                onBack = { showPlayer = false }, onAdd = onAdd,
            )
            if (status != null) {
                Surface(
                    Modifier.widthIn(max = screenWidth).fillMaxWidth()
                        .padding(horizontal = screenMargin, vertical = 5.dp)
                        .semantics { liveRegion = LiveRegionMode.Polite },
                    shape = RoundedCornerShape(14.dp), color = colors.surfaceContainer,
                ) {
                    Text(status, Modifier.padding(12.dp), color = colors.onSurfaceVariant,
                        style = MaterialTheme.typography.bodyMedium)
                }
            }
            Box(Modifier.weight(1f).widthIn(max = screenWidth).fillMaxWidth()) {
                if (showingTrack) {
                    TrackScreen(player, playback, current) {
                        showPlayer = false
                        destination = Destination.Queue
                    }
                } else {
                    Crossfade(destination, animationSpec = tween(170), label = "Раздел Slynor") { screen ->
                        when (screen) {
                            Destination.Home -> HomeScreen(
                                tracks, current, playback, player, importing, onAdd, { onPlay(it, tracks) },
                                { destination = Destination.Library }, { showPlayer = true },
                            )
                            Destination.Library -> {
                                val query = search.trim()
                                val filtered = remember(tracks, query, favoritesOnly) {
                                    tracks.filter {
                                        (!favoritesOnly || it.favorite) &&
                                            (it.title.contains(query, ignoreCase = true) ||
                                                it.artist.contains(query, ignoreCase = true))
                                    }
                                }
                                Column(Modifier.fillMaxSize().padding(horizontal = screenMargin)) {
                                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                        Box(Modifier.weight(1f)) { ScreenTitle(screen.title, trackCount(filtered.size)) }
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
                                        placeholder = { Text("Поиск по библиотеке") },
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
                                    if (filtered.isEmpty()) {
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
                                    } else {
                                        LazyColumn(
                                            state = libraryScroll,
                                            verticalArrangement = Arrangement.spacedBy(8.dp),
                                            contentPadding = PaddingValues(bottom = 14.dp),
                                        ) {
                                            items(filtered, key = { it.uri.toString() }) { track ->
                                                TrackRow(track, track == current, player != null && !importing, { onPlay(track, filtered) }) {
                                                    if (!importing) TrackMenu { close ->
                                                        DropdownMenuItem(
                                                            text = { Text(if (track.favorite) "Убрать из избранного" else "В избранное") },
                                                            onClick = {
                                                                close()
                                                                onFavorite(track)
                                                            },
                                                        )
                                                        if (player != null && track != current) QueueMenuItems(player, track, close)
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
                            }
                            Destination.Queue -> {
                                val queue = remember(playback.queue, byId) {
                                    playback.queue.mapIndexedNotNull { index, id -> byId[id]?.let { index to it } }
                                }
                                val position = if (playback.queueIndex in playback.queue.indices)
                                    "${playback.queueIndex + 1} из ${playback.queue.size}" else null
                                Column(Modifier.fillMaxSize().padding(horizontal = screenMargin)) {
                                    ScreenTitle(screen.title, position)
                                    if (queue.isEmpty()) {
                                        EmptyState(
                                            R.drawable.ic_queue, "Очередь пуста",
                                            "Выберите трек в библиотеке, чтобы начать воспроизведение.",
                                            "Открыть мою музыку", { destination = Destination.Library },
                                        )
                                    } else {
                                        LazyColumn(
                                            verticalArrangement = Arrangement.spacedBy(8.dp),
                                            contentPadding = PaddingValues(bottom = 14.dp),
                                        ) {
                                            items(queue, key = { it.second.uri.toString() }) { (index, track) ->
                                                TrackRow(track, index == playback.queueIndex, player != null, { player?.playFromQueue(index) }) {
                                                    if (player != null) TrackMenu { close ->
                                                        if (index != playback.queueIndex) QueueMenuItems(player, track, close)
                                                        DropdownMenuItem(text = { Text("Убрать из очереди") }, onClick = {
                                                            close()
                                                            player.removeFromQueue(track)
                                                        })
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            if (!showingTrack && !keyboardVisible) {
                if (player != null && current != null) {
                    Box(Modifier.widthIn(max = screenWidth).fillMaxWidth().padding(horizontal = 12.dp)) {
                        PlayerBar(current, playback, { showPlayer = true }) { togglePlayback(player, playback) }
                    }
                }
                NavigationBar(
                    modifier = Modifier.widthIn(max = screenWidth).fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)
                        .clip(RoundedCornerShape(22.dp)),
                    containerColor = colors.surface, tonalElevation = 0.dp,
                ) {
                    for (target in Destination.entries) {
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

@Composable
private fun AppHeader(title: String, trackScreen: Boolean, canAdd: Boolean, onBack: () -> Unit, onAdd: () -> Unit) {
    Row(
        Modifier.widthIn(max = screenWidth).fillMaxWidth().heightIn(min = 62.dp)
            .padding(horizontal = screenMargin, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically,
    ) {
        if (trackScreen) IconButton(onClick = onBack) { Glyph(R.drawable.ic_back, "Вернуться") }
        else Image(painterResource(R.drawable.slynor_icon), null, Modifier.size(40.dp).clip(RoundedCornerShape(12.dp)))
        Text(title, Modifier.weight(1f), style = MaterialTheme.typography.titleLarge)
        if (!trackScreen && canAdd) {
            FilledTonalButton(onClick = onAdd, contentPadding = PaddingValues(horizontal = 14.dp)) {
                Glyph(R.drawable.ic_add, null, Modifier.size(17.dp))
                Text("Добавить", Modifier.padding(start = 6.dp))
            }
        }
    }
}

@Composable
private fun ScreenTitle(title: String, detail: String? = null) {
    Column(Modifier.padding(top = 12.dp, bottom = 16.dp)) {
        Text(title, Modifier.semantics { heading() }, style = MaterialTheme.typography.headlineLarge)
        detail?.let {
            Text(it, Modifier.padding(top = 3.dp), color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyMedium)
        }
    }
}

private fun trackCount(total: Int): String {
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
    tracks: List<LocalTrack>, current: LocalTrack?, playback: Playback, player: MediaController?,
    importing: Boolean, onAdd: () -> Unit, onPlay: (LocalTrack) -> Unit, onLibrary: () -> Unit, onOpenPlayer: () -> Unit,
) {
    val recent = remember(tracks) { tracks.asReversed().take(5) }
    LazyColumn(
        Modifier.fillMaxSize(), contentPadding = PaddingValues(start = screenMargin, end = screenMargin, bottom = 14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item { ScreenTitle("Главная", trackCount(tracks.size).takeIf { tracks.isNotEmpty() }) }
        item {
            if (current != null && player != null) {
                CurrentTrackCard(current, playback, onOpenPlayer) { togglePlayback(player, playback) }
            } else {
                LibraryCard(tracks.isEmpty(), importing, onAdd, onLibrary)
            }
        }
        if (recent.isNotEmpty()) {
            item {
                Text("Недавно добавленные", Modifier.padding(top = 14.dp, bottom = 4.dp).semantics { heading() },
                    style = MaterialTheme.typography.titleLarge)
            }
            items(recent, key = { it.uri.toString() }) { track ->
                TrackRow(track, track == current, player != null && !importing, { onPlay(track) })
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
                Brush.horizontalGradient(listOf(Color(0xFF3D3047), Color(0xFF24202D), Color(0xFF271F29))),
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
private fun EmptyState(@DrawableRes icon: Int, title: String, description: String, action: String,
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
private fun TrackRow(track: LocalTrack, active: Boolean, enabled: Boolean, onPlay: () -> Unit,
                     actions: @Composable () -> Unit = {}) {
    val colors = MaterialTheme.colorScheme
    val shape = RoundedCornerShape(14.dp)
    Surface(shape = shape, border = BorderStroke(1.dp, if (active) colors.outline else colors.outlineVariant),
        color = if (active) colors.primaryContainer else colors.surface) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 66.dp).clip(shape)
                .semantics { selected = active }.clickable(enabled = enabled, onClick = onPlay)
                .padding(start = 8.dp, end = 6.dp, top = 7.dp, bottom = 7.dp),
            horizontalArrangement = Arrangement.spacedBy(11.dp), verticalAlignment = Alignment.CenterVertically,
        ) {
            Cover(rememberArtwork(track.uri), Modifier.size(48.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(track.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleMedium)
                if (track.artist.isNotBlank()) {
                    Text(track.artist, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        color = colors.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                }
            }
            if (track.favorite) Glyph(R.drawable.ic_heart, "В избранном", Modifier.size(16.dp), tint = colors.primary)
            actions()
        }
    }
}

@Composable
private fun TrackMenu(content: @Composable (() -> Unit) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { expanded = true }) { Glyph(R.drawable.ic_more, "Действия для трека") }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            content { expanded = false }
        }
    }
}

@Composable
private fun QueueMenuItems(player: MediaController, track: LocalTrack, close: () -> Unit) {
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
