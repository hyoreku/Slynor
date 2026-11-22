package io.github.hyoreku.slynor

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.media3.session.MediaController

@Composable
internal fun PlaylistsScreen(
    library: LocalLibrary,
    data: LibrarySnapshot,
    selected: Playlist?,
    current: LocalTrack?,
    player: MediaController?,
    onSelect: (String?) -> Unit,
    onPlay: (LocalTrack, List<LocalTrack>) -> Unit,
) {
    var editing by rememberSaveable { mutableStateOf(false) }
    var deleting by rememberSaveable { mutableStateOf(false) }
    var picking by rememberSaveable { mutableStateOf(false) }
    val playlistTracks = remember(selected, data.tracks) {
        val tracks = data.tracks.associateBy(LocalTrack::id)
        selected?.trackIds.orEmpty().mapNotNull(tracks::get)
    }

    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) {
                ScreenTitle(selected?.name ?: "Плейлисты", selected?.let { trackCount(playlistTracks.size) })
            }
            if (selected == null) {
                Button(onClick = { editing = true }) { Text("Создать") }
            } else {
                TrackMenu("Действия для плейлиста") { close ->
                    DropdownMenuItem(text = { Text("Переименовать") }, onClick = { close(); editing = true })
                    DropdownMenuItem(text = { Text("Удалить плейлист") }, onClick = { close(); deleting = true })
                }
            }
        }

        if (selected == null) {
            if (data.playlists.isEmpty()) {
                EmptyState(R.drawable.ic_playlist, "Плейлистов пока нет", "Соберите треки в свой список.",
                    "Создать плейлист", { editing = true })
            } else {
                LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp), contentPadding = PaddingValues(bottom = 14.dp)) {
                    items(data.playlists, key = Playlist::id) { playlist ->
                        Surface(shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.surface) {
                            TextButton(onClick = { onSelect(playlist.id) }, modifier = Modifier.fillMaxWidth()) {
                                Row(
                                    Modifier.fillMaxWidth().padding(horizontal = 6.dp, vertical = 9.dp),
                                    horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Glyph(R.drawable.ic_playlist, null)
                                    Text(playlist.name, Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis,
                                        style = MaterialTheme.typography.titleMedium)
                                    Text(trackCount(playlist.trackIds.size), style = MaterialTheme.typography.bodySmall)
                                }
                            }
                        }
                    }
                }
            }
        } else {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp),
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = { onSelect(null) }) { Text("Все плейлисты") }
                TextButton(onClick = { picking = true }, enabled = data.tracks.size > playlistTracks.size) {
                    Text("Добавить треки")
                }
                if (playlistTracks.size > 1 && player != null) {
                    IconButton(onClick = { playlistTracks.shuffled().let { onPlay(it.first(), it) } }) {
                        Glyph(R.drawable.ic_shuffle, "Перемешать и включить плейлист")
                    }
                }
            }
            if (playlistTracks.isEmpty()) {
                EmptyState(R.drawable.ic_playlist, "Плейлист пуст",
                    if (data.tracks.isEmpty()) "Сначала добавьте аудиофайлы в библиотеку." else "Выберите треки из библиотеки.",
                    "Добавить треки", { picking = true }, enabled = data.tracks.isNotEmpty())
            } else {
                LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp), contentPadding = PaddingValues(bottom = 14.dp)) {
                    itemsIndexed(playlistTracks, key = { _, track -> track.id }) { index, track ->
                        TrackRow(track, track == current, player != null, { onPlay(track, playlistTracks) }) {
                            TrackMenu { close ->
                                if (player != null && track != current) QueueMenuItems(player, track, close)
                                if (index > 0) {
                                    DropdownMenuItem(text = { Text("Переместить выше") }, onClick = {
                                        close()
                                        library.moveInPlaylist(selected.id, track.id, -1)
                                    })
                                }
                                if (index < playlistTracks.lastIndex) {
                                    DropdownMenuItem(text = { Text("Переместить ниже") }, onClick = {
                                        close()
                                        library.moveInPlaylist(selected.id, track.id, 1)
                                    })
                                }
                                DropdownMenuItem(text = { Text("Убрать из плейлиста") }, onClick = {
                                    close()
                                    library.removeFromPlaylist(selected.id, track.id)
                                })
                            }
                        }
                    }
                }
            }
        }
    }

    if (editing) PlaylistNameDialog(selected?.name.orEmpty(), { editing = false }) { name ->
        if (selected == null) onSelect(library.createPlaylist(name).id)
        else library.renamePlaylist(selected.id, name)
        editing = false
    }
    if (deleting && selected != null) {
        AlertDialog(
            onDismissRequest = { deleting = false },
            title = { Text("Удалить плейлист?") },
            text = { Text(selected.name) },
            confirmButton = { TextButton(onClick = {
                deleting = false
                library.deletePlaylist(selected.id)
                onSelect(null)
            }) { Text("Удалить") } },
            dismissButton = { TextButton(onClick = { deleting = false }) { Text("Отмена") } },
        )
    }
    if (picking && selected != null) {
        PlaylistTrackPicker(data.tracks.filterNot { it.id in selected.trackIds }, { picking = false }) { ids ->
            library.addToPlaylist(selected.id, ids)
            picking = false
        }
    }
}

@Composable
internal fun PlaylistNameDialog(initial: String = "", onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var name by rememberSaveable(initial) { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (initial.isBlank()) "Новый плейлист" else "Переименовать плейлист") },
        text = {
            OutlinedTextField(name, { if (it.length <= 80) name = it }, singleLine = true,
                label = { Text("Название") }, modifier = Modifier.fillMaxWidth())
        },
        confirmButton = {
            TextButton(onClick = { onSave(name.trim()) }, enabled = name.isNotBlank()) { Text("Сохранить") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}

@Composable
internal fun AddTrackToPlaylist(
    track: LocalTrack, playlists: List<Playlist>, library: LocalLibrary, onDismiss: () -> Unit,
) {
    var creating by rememberSaveable { mutableStateOf(false) }
    if (creating) {
        PlaylistNameDialog(onDismiss = { creating = false }) { name ->
            library.createPlaylist(name, track.id)
            onDismiss()
        }
    } else {
        AlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("Добавить в плейлист") },
            text = {
                LazyColumn(Modifier.fillMaxWidth().heightIn(max = 270.dp)) {
                    items(playlists, key = Playlist::id) { playlist ->
                        val included = track.id in playlist.trackIds
                        TextButton(enabled = !included, modifier = Modifier.fillMaxWidth(), onClick = {
                            library.addToPlaylist(playlist.id, listOf(track.id))
                            onDismiss()
                        }) { Text(if (included) "${playlist.name} · добавлен" else playlist.name) }
                    }
                }
            },
            confirmButton = { TextButton(onClick = { creating = true }) { Text("Новый плейлист") } },
            dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
        )
    }
}

@Composable
private fun PlaylistTrackPicker(tracks: List<LocalTrack>, onDismiss: () -> Unit, onSave: (List<String>) -> Unit) {
    var search by rememberSaveable { mutableStateOf("") }
    var selected by rememberSaveable { mutableStateOf(arrayListOf<String>()) }
    val matching = tracks.filter { it.matches(search.trim()) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Добавить треки") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(search, { search = it }, singleLine = true, label = { Text("Поиск") },
                    modifier = Modifier.fillMaxWidth())
                if (matching.isEmpty()) Text("Нет подходящих треков")
                LazyColumn(Modifier.fillMaxWidth().heightIn(max = 265.dp)) {
                    items(matching, key = LocalTrack::id) { track ->
                        val checked = track.id in selected
                        Row(
                            Modifier.fillMaxWidth().toggleable(checked, role = Role.Checkbox) { isChecked ->
                                selected = ArrayList(if (isChecked) selected + track.id else selected - track.id)
                            }.padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            androidx.compose.material3.Checkbox(checked, onCheckedChange = null)
                            Text(track.title, Modifier.padding(start = 10.dp), maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { onSave(selected) }, enabled = selected.isNotEmpty()) {
                Text(if (selected.isEmpty()) "Добавить" else "Добавить (${selected.size})")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}

internal fun LocalTrack.matches(query: String): Boolean =
    title.contains(query, ignoreCase = true) || artist.contains(query, ignoreCase = true) || album.contains(query, ignoreCase = true)
