package io.github.hyoreku.slynor

import android.graphics.Bitmap
import android.net.Uri
import android.util.LruCache
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.BackHandler
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.media3.session.MediaController
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
internal fun PlaylistsScreen(
    library: LocalLibrary,
    data: LibrarySnapshot,
    selected: Playlist?,
    current: LocalTrack?,
    player: MediaController?,
    onSelect: (String?) -> Unit,
    onCreate: () -> Unit,
    onEdit: (Playlist) -> Unit,
    onPlay: (LocalTrack, List<LocalTrack>) -> Unit,
    onFavorite: (LocalTrack) -> Unit,
    onAddTracks: (List<String>) -> Unit,
    onTrackDetails: (LocalTrack) -> Unit,
) {
    AnimatedContent(
        targetState = selected,
        contentKey = { it?.id },
        transitionSpec = { screenMotion(collapsing = targetState == null) },
        modifier = Modifier.fillMaxSize(),
        label = "Открытие плейлиста",
    ) { playlist ->
        if (playlist == null) {
            Column(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(1f)) { ScreenTitle("Плейлисты", playlistCount(data.playlists.size)) }
                    FilledTonalButton(onClick = onCreate) {
                        Glyph(R.drawable.ic_add, null)
                        Text("Создать", Modifier.padding(start = 5.dp))
                    }
                }
                if (data.playlists.isEmpty()) {
                    EmptyState(R.drawable.ic_playlist, "Плейлистов пока нет", "Соберите треки в свой список.",
                        "Создать плейлист", onCreate)
                } else {
                    val tracksById = remember(data.tracks) { data.tracks.associateBy(LocalTrack::id) }
                    LazyVerticalGrid(
                        columns = GridCells.Adaptive(126.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        contentPadding = PaddingValues(bottom = 18.dp),
                    ) {
                        items(data.playlists, key = Playlist::id) { item ->
                            PlaylistTile(
                                library, item, { onSelect(item.id) }, { onEdit(item) },
                                onPlay = if (player != null && item.trackIds.isNotEmpty()) {
                                    {
                                        val tracks = item.trackIds.mapNotNull(tracksById::get)
                                        tracks.firstOrNull()?.let { onPlay(it, tracks) }
                                    }
                                } else null,
                                modifier = Modifier.animateItem(),
                            )
                        }
                    }
                }
            }
        } else {
            PlaylistDetail(
                library, data, playlist, current, player, { onSelect(null) }, { onEdit(playlist) }, onPlay,
                onFavorite, onAddTracks, onTrackDetails, { onSelect(it.id) },
            )
        }
    }
}

@Composable
internal fun PlaylistTile(
    library: LocalLibrary, playlist: Playlist, onOpen: () -> Unit, onEdit: () -> Unit,
    onPlay: (() -> Unit)? = null, modifier: Modifier = Modifier,
) {
    val colors = MaterialTheme.colorScheme
    val art = rememberPlaylistCover(library, playlist.cover)
    val tone = art?.tone ?: playlistTone(playlist.id)
    Surface(
        onClick = onOpen,
        modifier = modifier,
        shape = RoundedCornerShape(17.dp),
        color = colors.surface,
        border = BorderStroke(1.dp, colors.outlineVariant),
    ) {
        Column(Modifier.background(Brush.verticalGradient(listOf(tone.copy(alpha = .17f), Color.Transparent)))) {
            Box(Modifier.padding(8.dp)) {
                PlaylistCover(art, tone, Modifier.fillMaxWidth().aspectRatio(1f))
                if (onPlay != null) {
                    Surface(Modifier.align(Alignment.BottomEnd).padding(6.dp), shape = RoundedCornerShape(14.dp),
                        color = colors.primary, contentColor = colors.onPrimary) {
                        IconButton(onClick = onPlay) { Glyph(R.drawable.ic_play, "Воспроизвести плейлист «${playlist.name}»") }
                    }
                }
            }
            Row(Modifier.fillMaxWidth().padding(start = 12.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(playlist.name, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleMedium)
                    Text(trackCount(playlist.trackIds.size), color = colors.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                }
                IconButton(onClick = onEdit) { Glyph(R.drawable.ic_edit, "Редактировать плейлист «${playlist.name}»") }
            }
        }
    }
}

@Composable
private fun PlaylistDetail(
    library: LocalLibrary, data: LibrarySnapshot, playlist: Playlist, current: LocalTrack?, player: MediaController?,
    onBack: () -> Unit, onEdit: () -> Unit, onPlay: (LocalTrack, List<LocalTrack>) -> Unit,
    onFavorite: (LocalTrack) -> Unit, onAddTracks: (List<String>) -> Unit, onTrackDetails: (LocalTrack) -> Unit,
    onDuplicate: (Playlist) -> Unit,
) {
    var picking by rememberSaveable(playlist.id) { mutableStateOf(false) }
    var search by rememberSaveable(playlist.id) { mutableStateOf("") }
    var sort by rememberSaveable(playlist.id) { mutableStateOf(LibrarySort.Added) }
    var selecting by rememberSaveable(playlist.id) { mutableStateOf(false) }
    var selection by rememberSaveable(playlist.id) { mutableStateOf(arrayListOf<String>()) }
    var confirmRemove by rememberSaveable(playlist.id) { mutableStateOf(false) }
    var duplicating by remember { mutableStateOf(false) }
    var duplicateError by remember { mutableStateOf(false) }
    val focus = LocalFocusManager.current
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    val tracks = remember(playlist.trackIds, data.tracks) {
        val byId = data.tracks.associateBy(LocalTrack::id)
        playlist.trackIds.mapNotNull(byId::get)
    }
    val query = search.trim()
    val matching = remember(tracks, query, sort) {
        sort.order(tracks.filter { it.matches(query) })
    }
    val canReorder = sort == LibrarySort.Added && query.isEmpty() && !selecting
    val manualIds = remember(matching) { matching.map(LocalTrack::id) }
    val drag = rememberPlaylistDrag(listState) { from, anchor -> library.moveInPlaylist(playlist.id, from, anchor) }
    val displayed = remember(matching, drag.order) {
        val preview = drag.order ?: return@remember matching
        val byId = matching.associateBy(LocalTrack::id)
        preview.mapNotNull(byId::get)
    }
    val selectedIds = remember(playlist.trackIds, selection) {
        val selected = selection.toSet()
        playlist.trackIds.filter(selected::contains)
    }
    fun endSelection() {
        selecting = false
        selection = arrayListOf()
    }
    BackHandler(selecting) { endSelection() }

    LazyColumn(
        Modifier.fillMaxSize(),
        state = listState,
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 18.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onBack, modifier = Modifier.weight(1f)) {
                    Glyph(R.drawable.ic_back, null)
                    Text("Все плейлисты", Modifier.padding(start = 5.dp))
                }
                TrackMenu("Действия плейлиста") { close ->
                    DropdownMenuItem(text = { Text("Дублировать плейлист") }, enabled = !duplicating, onClick = {
                        close()
                        duplicating = true
                        duplicateError = false
                        scope.launch {
                            val result = withContext(Dispatchers.IO) { runCatching { library.duplicatePlaylist(playlist.id) } }
                            duplicating = false
                            result.onSuccess(onDuplicate).onFailure { duplicateError = true }
                        }
                    })
                }
            }
            PlaylistSummary(library, playlist, tracks.size, matching, player != null, onEdit, onPlay)
            if (duplicateError) Text("Не удалось дублировать плейлист.", color = MaterialTheme.colorScheme.error)
        }
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(if (query.isEmpty()) "Треки" else "${matching.size} из ${tracks.size}",
                    Modifier.weight(1f), style = MaterialTheme.typography.titleLarge)
                TextButton(onClick = { picking = true }, enabled = data.tracks.size > tracks.size) {
                    Glyph(R.drawable.ic_add, null)
                    Text("Добавить треки")
                }
                if (tracks.isNotEmpty()) {
                    TrackMenu("Сортировка плейлиста") { close ->
                        playlistSortOptions.forEach { option ->
                            DropdownMenuItem(
                                text = { Text(if (option == LibrarySort.Added) "Порядок плейлиста" else option.title) },
                                onClick = {
                                    sort = option
                                    close()
                                },
                                modifier = Modifier.semantics { selected = sort == option },
                            )
                        }
                    }
                }
            }
        }
        if (tracks.isNotEmpty()) {
            item {
                OutlinedTextField(
                    search, { search = it }, singleLine = true,
                    label = { Text("Поиск в плейлисте") },
                    leadingIcon = { Glyph(R.drawable.ic_search, null) },
                    trailingIcon = if (search.isEmpty()) null else {
                        {
                            IconButton(onClick = {
                                search = ""
                                focus.clearFocus()
                            }) {
                                Glyph(R.drawable.ic_close, "Очистить поиск в плейлисте")
                            }
                        }
                    },
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = { focus.clearFocus() }),
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(16.dp),
                )
            }
            item {
                Column(Modifier.animateContentSize()) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            if (selecting) "Выбрано: ${selectedIds.size}" else if (canReorder && tracks.size > 1) "Ручной порядок" else "",
                            Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodySmall,
                        )
                        TextButton(onClick = {
                            if (selecting) endSelection() else {
                                focus.clearFocus()
                                selecting = true
                            }
                        }) { Text(if (selecting) "Готово" else "Выбрать треки") }
                    }
                    AnimatedVisibility(selecting) {
                        Column {
                            TextButton(
                                enabled = matching.isNotEmpty(),
                                onClick = {
                                    selection = ArrayList(if (manualIds.all(selection::contains)) {
                                        selection - manualIds.toSet()
                                    } else (selection + manualIds).distinct())
                                },
                            ) { Text(if (matching.isNotEmpty() && manualIds.all(selection::contains)) "Снять выбор" else "Выбрать все") }
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                TextButton(enabled = selectedIds.isNotEmpty(), onClick = {
                                    onAddTracks(selectedIds)
                                    endSelection()
                                }) { Text("В плейлисты") }
                                TextButton(enabled = selectedIds.isNotEmpty(), onClick = { confirmRemove = true }) {
                                    Text("Убрать выбранные", color = MaterialTheme.colorScheme.error.takeIf { selectedIds.isNotEmpty() }
                                        ?: MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        }
                    }
                }
            }
        }
        if (tracks.isEmpty()) {
            item {
                EmptyState(
                    R.drawable.ic_playlist, "Плейлист пуст",
                    if (data.tracks.isEmpty()) "Сначала добавьте аудиофайлы в библиотеку." else "Выберите треки из библиотеки.",
                    "Добавить треки", { picking = true }, enabled = data.tracks.isNotEmpty(),
                )
            }
        } else if (matching.isEmpty()) {
            item {
                EmptyState(R.drawable.ic_search, "В этом плейлисте ничего не найдено", "Проверьте запрос.",
                    "Очистить поиск", {
                        search = ""
                        focus.clearFocus()
                    })
            }
        }
        itemsIndexed(displayed, key = { _, track -> track.id }) { index, track ->
            val checked = track.id in selection
            val rowModifier = drag.rowModifier(track.id, Modifier.animateItem(placementSpec = if (drag.activeId == track.id) null
                else androidx.compose.animation.core.spring()))
            TrackRow(
                track, if (selecting) checked else track.id == current?.id, selecting || player != null,
                {
                    if (selecting) selection = ArrayList(if (checked) selection - track.id else selection + track.id)
                    else onPlay(track, matching)
                },
                rowModifier,
            ) {
                if (selecting) {
                    Checkbox(checked, onCheckedChange = null)
                    return@TrackRow
                }
                if (canReorder && matching.size > 1) PlaylistDragHandle(track, manualIds, drag)
                TrackMenu { close ->
                    if (player != null && track.id != current?.id) QueueMenuItems(player, track, close)
                    DropdownMenuItem(
                        text = { Text(if (track.favorite) "Убрать из избранного" else "В избранное") },
                        onClick = {
                            close()
                            onFavorite(track)
                        },
                    )
                    DropdownMenuItem(text = { Text("Добавить в плейлист") }, onClick = {
                        close()
                        onAddTracks(listOf(track.id))
                    })
                    DropdownMenuItem(text = { Text("О треке") }, onClick = {
                        close()
                        onTrackDetails(track)
                    })
                    if (canReorder && index > 0) {
                        DropdownMenuItem(text = { Text("Переместить выше") }, onClick = {
                            close()
                            library.moveInPlaylist(playlist.id, track.id, -1)
                        })
                    }
                    if (canReorder && index < matching.lastIndex) {
                        DropdownMenuItem(text = { Text("Переместить ниже") }, onClick = {
                            close()
                            library.moveInPlaylist(playlist.id, track.id, 1)
                        })
                    }
                    DropdownMenuItem(text = { Text("Убрать из плейлиста") }, onClick = {
                        close()
                        library.removeFromPlaylist(playlist.id, setOf(track.id))
                    })
                }
            }
        }
    }
    if (picking) {
        val included = remember(playlist.trackIds) { playlist.trackIds.toSet() }
        PlaylistTrackPicker(data.tracks.filterNot { it.id in included }, { picking = false }) { ids ->
            library.addToPlaylist(playlist.id, ids)
        }
    }
    if (confirmRemove) AlertDialog(
        onDismissRequest = { confirmRemove = false },
        title = { Text("Убрать выбранные треки?") },
        text = { Text("${trackCount(selectedIds.size)} будут убраны из плейлиста «${playlist.name}».") },
        confirmButton = {
            TextButton(onClick = {
                library.removeFromPlaylist(playlist.id, selectedIds.toSet())
                confirmRemove = false
                endSelection()
            }) { Text("Убрать") }
        },
        dismissButton = { TextButton(onClick = { confirmRemove = false }) { Text("Отмена") } },
    )
}

private val playlistSortOptions = listOf(LibrarySort.Added, LibrarySort.Title, LibrarySort.Artist,
    LibrarySort.Album, LibrarySort.Duration)

@Composable
private fun PlaylistSummary(
    library: LocalLibrary, playlist: Playlist, total: Int, tracks: List<LocalTrack>, connected: Boolean,
    onEdit: () -> Unit, onPlay: (LocalTrack, List<LocalTrack>) -> Unit,
) {
    val colors = MaterialTheme.colorScheme
    val art = rememberPlaylistCover(library, playlist.cover)
    val tone = art?.tone ?: playlistTone(playlist.id)
    Surface(shape = RoundedCornerShape(20.dp), border = BorderStroke(1.dp, colors.outlineVariant), color = colors.surface) {
        Column(Modifier.fillMaxWidth().background(Brush.verticalGradient(listOf(tone.copy(alpha = .22f), Color.Transparent)))
            .padding(14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                PlaylistCover(art, tone, Modifier.size(92.dp))
                Column(Modifier.weight(1f)) {
                    Text(playlist.name, style = MaterialTheme.typography.headlineSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Text(trackCount(total), color = colors.onSurfaceVariant)
                    TextButton(onClick = onEdit, contentPadding = PaddingValues(horizontal = 0.dp)) { Text("Редактировать плейлист") }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Button(
                    enabled = connected && tracks.isNotEmpty(),
                    onClick = { onPlay(tracks.first(), tracks) },
                    contentPadding = ButtonDefaults.ButtonWithIconContentPadding,
                ) {
                    Glyph(R.drawable.ic_play, null)
                    Text("Слушать", Modifier.padding(start = 6.dp))
                }
                IconButton(
                    enabled = connected && tracks.size > 1,
                    onClick = { tracks.shuffled().let { onPlay(it.first(), it) } },
                ) { Glyph(R.drawable.ic_shuffle, "Перемешать и включить плейлист") }
            }
        }
    }
}

private sealed interface CoverPreview {
    data object Loading : CoverPreview
    data object Unreadable : CoverPreview
    data class Ready(val image: Bitmap) : CoverPreview
}

@Composable
internal fun PlaylistEditor(
    library: LocalLibrary, playlist: Playlist?, onDismiss: () -> Unit,
    initialTracks: List<String> = emptyList(), onSaved: (Playlist) -> Unit, onDeleted: () -> Unit = {},
) {
    var name by rememberSaveable(playlist?.id) { mutableStateOf(playlist?.name.orEmpty()) }
    var uriText by rememberSaveable(playlist?.id) { mutableStateOf<String?>(null) }
    var removeCover by rememberSaveable(playlist?.id) { mutableStateOf(false) }
    var deleting by rememberSaveable { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    var failure by remember { mutableStateOf<String?>(null) }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val preview by key(uriText) {
        produceState<CoverPreview?>(if (uriText == null) null else CoverPreview.Loading) {
            val uri = uriText?.let(Uri::parse) ?: return@produceState
            value = withContext(Dispatchers.IO) {
                runCatching { readDocumentImage(context.contentResolver, uri, 640) }
                    .fold({ CoverPreview.Ready(it) }, { CoverPreview.Unreadable })
            }
        }
    }
    val selectedArt = remember(preview) { (preview as? CoverPreview.Ready)?.image?.let { Artwork(it, artworkTone(it)) } }
    val savedArt = rememberPlaylistCover(library, playlist?.cover?.takeUnless { removeCover || uriText != null })
    val art = selectedArt ?: savedArt
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { image ->
        if (image != null) {
            uriText = image.toString()
            removeCover = false
        }
    }
    SlynorSheet(
        if (playlist == null) "Новый плейлист" else "Редактирование плейлиста",
        "Закрыть редактор плейлиста", onDismiss,
    ) { close ->
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 12.dp)
                .animateContentSize(),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                PlaylistCover(art, art?.tone ?: playlistTone(playlist?.id.orEmpty()), Modifier.size(82.dp))
                Column(Modifier.weight(1f)) {
                    Text("Обложка", style = MaterialTheme.typography.titleMedium)
                    TextButton(onClick = { picker.launch(arrayOf("image/*")) }, enabled = !saving,
                        contentPadding = PaddingValues(horizontal = 0.dp)) {
                        Text(if (art == null) "Выбрать обложку" else "Сменить обложку")
                    }
                    if (uriText != null || (playlist?.cover != null && !removeCover)) {
                        TextButton(onClick = {
                            uriText = null
                            removeCover = true
                        }, enabled = !saving, contentPadding = PaddingValues(horizontal = 0.dp)) { Text("Убрать обложку") }
                    }
                }
            }
            OutlinedTextField(
                name, { if (it.length <= 80) name = it }, singleLine = true,
                label = { Text("Название") }, modifier = Modifier.fillMaxWidth(), enabled = !saving,
            )
            val error = if (preview == CoverPreview.Unreadable) "Не удалось открыть изображение." else failure
            if (error != null) Text(error, color = MaterialTheme.colorScheme.error)
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = !saving && name.isNotBlank() && (preview == null || preview is CoverPreview.Ready),
                onClick = {
                    saving = true
                    val chosen = selectedArt?.bitmap
                    val title = name.trim()
                    val reset = removeCover
                    scope.launch {
                        val result = withContext(Dispatchers.IO) {
                            runCatching { library.savePlaylist(playlist?.id, title, chosen, reset, initialTracks) }
                        }
                        saving = false
                        result.onSuccess { onSaved(it); close() }
                            .onFailure { error ->
                                failure = if (error is PlaylistNameExists) error.message else "Не удалось сохранить плейлист."
                            }
                    }
                },
            ) { Text(if (saving) "Сохранение…" else "Сохранить") }
            if (playlist != null) {
                TextButton(onClick = { deleting = true }, enabled = !saving) {
                    Text("Удалить плейлист", color = MaterialTheme.colorScheme.error)
                }
                if (deleting) AlertDialog(
                    onDismissRequest = { deleting = false },
                    title = { Text("Удалить плейлист?") },
                    text = { Text(playlist.name) },
                    confirmButton = {
                        TextButton(onClick = {
                            deleting = false
                            saving = true
                            scope.launch {
                                val result = withContext(Dispatchers.IO) { runCatching { library.deletePlaylist(playlist.id) } }
                                saving = false
                                result.onSuccess { onDeleted(); close() }.onFailure { failure = "Не удалось удалить плейлист." }
                            }
                        }) { Text("Удалить") }
                    },
                    dismissButton = { TextButton(onClick = { deleting = false }) { Text("Отмена") } },
                )
            }
        }
    }
}

@Composable
internal fun AddTracksToPlaylist(
    trackIds: List<String>, playlists: List<Playlist>, library: LocalLibrary, onDismiss: () -> Unit,
) {
    var creating by rememberSaveable { mutableStateOf(false) }
    var createNext by rememberSaveable { mutableStateOf(false) }
    var selected by rememberSaveable { mutableStateOf(arrayListOf<String>()) }
    if (creating) {
        PlaylistEditor(library, null, onDismiss, initialTracks = trackIds, onSaved = {})
    } else {
        SlynorSheet("Добавить в плейлист", "Закрыть выбор плейлистов", {
            if (createNext) {
                createNext = false
                creating = true
            } else onDismiss()
        }) { close ->
            Column(
                Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                LazyColumn(Modifier.fillMaxWidth().heightIn(max = 265.dp)) {
                    items(playlists, key = Playlist::id) { playlist ->
                        val included = remember(trackIds, playlist.trackIds) {
                            val existing = playlist.trackIds.toHashSet()
                            trackIds.all(existing::contains)
                        }
                        val checked = included || playlist.id in selected
                        Row(
                            Modifier.fillMaxWidth().animateItem().toggleable(
                                checked, enabled = !included, role = Role.Checkbox,
                            ) { isChecked ->
                                selected = ArrayList(if (isChecked) selected + playlist.id else selected - playlist.id)
                            }.padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Checkbox(checked, onCheckedChange = null, enabled = !included)
                            Text(if (included) "${playlist.name} · уже в списке" else playlist.name, Modifier.padding(start = 8.dp))
                        }
                    }
                }
                if (selected.isNotEmpty()) Button(modifier = Modifier.fillMaxWidth(), onClick = {
                    library.addToPlaylists(trackIds, selected.toSet())
                    close()
                }) { Text("Добавить (${selected.size})") }
                TextButton(onClick = {
                    createNext = true
                    close()
                }) { Text("Новый плейлист") }
            }
        }
    }
}

private val playlistArtwork = object : LruCache<String, Artwork>(12 * 1024) {
    override fun sizeOf(key: String, value: Artwork) = ((value.bitmap?.allocationByteCount ?: 0) / 1024).coerceAtLeast(1)
}

@Composable
private fun rememberPlaylistCover(library: LocalLibrary, cover: String?): Artwork? = key(cover) {
    val art by produceState(cover?.let(playlistArtwork::get)) {
        if (cover != null && value == null) {
            value = withContext(Dispatchers.IO) {
                library.readPlaylistCover(cover)?.let { Artwork(it, artworkTone(it)).also { art -> playlistArtwork.put(cover, art) } }
            }
        }
    }
    art
}

@Composable
private fun PlaylistCover(art: Artwork?, tone: Color, modifier: Modifier) {
    val colors = MaterialTheme.colorScheme
    Box(modifier, contentAlignment = Alignment.Center) {
        Cover(art?.takeIf { it.bitmap != null }, Modifier.fillMaxSize(),
            fallbackTone = tone, fallbackIcon = R.drawable.ic_playlist, fallbackColor = colors.onSurface)
    }
}

private fun playlistTone(id: String): Color {
    val palette = listOf(Color(0xFF8174BC), Color(0xFF4E88AB), Color(0xFF5A9B93), Color(0xFFAD6E91))
    return palette[Math.floorMod(id.hashCode(), palette.size)]
}

private fun playlistCount(count: Int): String = when {
    count % 100 in 11..14 -> "$count плейлистов"
    count % 10 == 1 -> "$count плейлист"
    count % 10 in 2..4 -> "$count плейлиста"
    else -> "$count плейлистов"
}

@Composable
private fun PlaylistTrackPicker(tracks: List<LocalTrack>, onDismiss: () -> Unit, onSave: (List<String>) -> Unit) {
    var search by rememberSaveable { mutableStateOf("") }
    var selected by rememberSaveable { mutableStateOf(arrayListOf<String>()) }
    val matching = tracks.filter { it.matches(search.trim()) }

    SlynorSheet("Добавить треки", "Закрыть выбор треков", onDismiss) { close ->
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(search, { search = it }, singleLine = true, label = { Text("Поиск") },
                modifier = Modifier.fillMaxWidth())
            if (matching.isEmpty()) Text("Нет подходящих треков", color = MaterialTheme.colorScheme.onSurfaceVariant)
            LazyColumn(Modifier.fillMaxWidth().heightIn(max = 265.dp)) {
                items(matching, key = LocalTrack::id) { track ->
                    val checked = track.id in selected
                    Row(
                        Modifier.fillMaxWidth().animateItem().toggleable(checked, role = Role.Checkbox) { isChecked ->
                            selected = ArrayList(if (isChecked) selected + track.id else selected - track.id)
                        }.padding(vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Checkbox(checked, onCheckedChange = null)
                        Text(track.title, Modifier.padding(start = 10.dp), maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            Button(enabled = selected.isNotEmpty(), modifier = Modifier.fillMaxWidth(), onClick = {
                onSave(selected)
                close()
            }) {
                Text(if (selected.isEmpty()) "Добавить" else "Добавить (${selected.size})")
            }
        }
    }
}

internal fun LocalTrack.matches(query: String): Boolean =
    title.contains(query, ignoreCase = true) || artist.contains(query, ignoreCase = true) || album.contains(query, ignoreCase = true)
