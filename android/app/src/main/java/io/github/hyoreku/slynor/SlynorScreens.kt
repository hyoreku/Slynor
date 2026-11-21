package io.github.hyoreku.slynor

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.util.LruCache
import androidx.activity.compose.BackHandler
import androidx.annotation.DrawableRes
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private enum class Destination(val title: String, @param:DrawableRes val icon: Int) {
    Home("Главная", R.drawable.ic_home),
    Library("Моя музыка", R.drawable.ic_music),
    Queue("Очередь", R.drawable.ic_queue),
}

@Composable
internal fun SlynorScreens(
    tracks: List<LocalTrack>,
    player: MediaController?,
    playback: Playback,
    status: String?,
    importing: Boolean,
    onAdd: () -> Unit,
    onPlay: (LocalTrack) -> Unit,
    onRemove: (LocalTrack) -> Unit,
) {
    var destination by rememberSaveable { mutableStateOf(Destination.Home) }
    var showPlayer by rememberSaveable { mutableStateOf(false) }
    var search by rememberSaveable { mutableStateOf("") }
    val byId = remember(tracks) { tracks.associateBy { it.uri.toString() } }
    val currentTrack = byId[playback.trackId]
    val showingTrack = showPlayer && player != null && currentTrack != null
    BackHandler(showingTrack) { showPlayer = false }

    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(
            Modifier.fillMaxSize().background(
                Brush.verticalGradient(listOf(Color(0xFF221A31), MaterialTheme.colorScheme.background), endY = 760f),
            ).safeDrawingPadding(),
        ) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 9.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Image(painterResource(R.drawable.slynor_icon), null, Modifier.size(39.dp).clip(RoundedCornerShape(11.dp)))
                Text("Slynor", Modifier.weight(1f), style = MaterialTheme.typography.titleLarge)
                if (showingTrack) TextButton(onClick = { showPlayer = false }) { Text("К списку") }
                else FilledIconButton(onClick = onAdd, enabled = !importing) {
                    Glyph(R.drawable.ic_add, "Добавить музыку")
                }
            }
            status?.let {
                Text(it, Modifier.padding(horizontal = 18.dp, vertical = 6.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Box(Modifier.weight(1f).fillMaxWidth()) {
                if (showingTrack) {
                    TrackScreen(player, playback, currentTrack)
                } else when (destination) {
                    Destination.Home -> HomeScreen(tracks, currentTrack, playback, player != null, onPlay, onAdd) {
                        destination = Destination.Library
                    }
                    Destination.Library -> {
                        val query = search.trim()
                        val filtered = remember(tracks, query) {
                            tracks.filter { it.title.contains(query, ignoreCase = true) || it.artist.contains(query, ignoreCase = true) }
                        }
                        Column(Modifier.padding(horizontal = 18.dp)) {
                            ScreenTitle(destination.title)
                            OutlinedTextField(
                                search, { search = it }, Modifier.fillMaxWidth().padding(bottom = 12.dp),
                                placeholder = { Text("Поиск по библиотеке") },
                                leadingIcon = { Glyph(R.drawable.ic_search, null) },
                                singleLine = true,
                                shape = RoundedCornerShape(14.dp),
                            )
                            if (filtered.isEmpty()) {
                                Text(
                                    if (tracks.isEmpty()) "Добавьте музыку с устройства." else "Ничего не найдено.",
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                items(filtered, key = { it.uri.toString() }) { track ->
                                    TrackRow(track, track == currentTrack, player != null && !importing, { onPlay(track) }) {
                                        if (!importing) onRemove(track)
                                    }
                                }
                            }
                        }
                    }
                    Destination.Queue -> {
                        Column(Modifier.padding(horizontal = 18.dp)) {
                            ScreenTitle(destination.title)
                            if (playback.queue.isEmpty()) {
                                Text("Выберите трек в библиотеке, чтобы начать очередь.",
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            val queue = remember(playback.queue, byId) { playback.queue.mapIndexedNotNull { index, id ->
                                byId[id]?.let { index to it }
                            } }
                            LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                items(queue, key = { it.first }) { (index, track) ->
                                    TrackRow(track, index == playback.queueIndex, player != null, {
                                        player?.seekTo(index, 0L)
                                        player?.play()
                                    })
                                }
                            }
                        }
                    }
                }
            }
            if (player != null && currentTrack != null && !showingTrack) {
                PlayerBar(currentTrack, playback, { showPlayer = true }) { togglePlayback(player, playback) }
            }
            if (!showingTrack) {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                    for (target in Destination.entries) {
                        NavigationBarItem(
                            selected = destination == target, onClick = { destination = target },
                            icon = { Glyph(target.icon, null) }, label = { Text(target.title) },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ScreenTitle(title: String) {
    Text(title, Modifier.padding(top = 10.dp, bottom = 14.dp), style = MaterialTheme.typography.headlineMedium)
}

@Composable
private fun HomeScreen(
    tracks: List<LocalTrack>,
    current: LocalTrack?,
    playback: Playback,
    connected: Boolean,
    onPlay: (LocalTrack) -> Unit,
    onAdd: () -> Unit,
    onLibrary: () -> Unit,
) {
    Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp)) {
        ScreenTitle("Главная")
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(
                Brush.linearGradient(listOf(Color(0xFF342746), Color(0xFF23202F), Color(0xFF182A2C))),
            ).padding(18.dp),
        ) {
            Text(
                if (tracks.isEmpty()) "Включите первую песню" else "Моя музыка",
                style = MaterialTheme.typography.headlineSmall,
            )
            Text(
                if (tracks.isEmpty()) "Добавьте аудиофайлы с устройства." else "Треков в библиотеке: ${tracks.size}",
                Modifier.padding(top = 6.dp), color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            TextButton(onClick = if (tracks.isEmpty()) onAdd else onLibrary) {
                Text(if (tracks.isEmpty()) "Добавить музыку" else "Открыть библиотеку")
            }
        }
        if (tracks.isNotEmpty()) {
            Text("Треки", Modifier.padding(top = 22.dp, bottom = 8.dp), style = MaterialTheme.typography.titleLarge)
            val featured = remember(tracks, current) { (listOfNotNull(current) + tracks.take(5)).distinct().take(5) }
            for (track in featured) {
                TrackRow(track, playback.trackId == track.uri.toString(), connected, { onPlay(track) })
            }
        }
    }
}

@Composable
private fun TrackRow(track: LocalTrack, active: Boolean, enabled: Boolean, onPlay: () -> Unit, onRemove: (() -> Unit)? = null) {
    val colors = MaterialTheme.colorScheme
    val shape = RoundedCornerShape(12.dp)
    Row(
        Modifier.fillMaxWidth().padding(bottom = 4.dp).clip(shape)
            .background(if (active) colors.primaryContainer else colors.surface)
            .clickable(enabled = enabled, onClick = onPlay).padding(start = 8.dp, end = 3.dp, top = 5.dp, bottom = 5.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Cover(track.uri, Modifier.size(42.dp))
        Column(Modifier.weight(1f)) {
            Text(track.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
            if (track.artist.isNotBlank()) {
                Text(track.artist, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    color = colors.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
            }
        }
        if (onRemove != null) IconButton(onClick = onRemove) {
            Text("×", style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.semantics { contentDescription = "Убрать из библиотеки" })
        }
    }
}

@Composable
private fun PlayerBar(track: LocalTrack, state: Playback, onOpen: () -> Unit, onToggle: () -> Unit) {
    val shape = RoundedCornerShape(16.dp)
    val colors = MaterialTheme.colorScheme
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 7.dp).clip(shape)
            .background(colors.surface).border(1.dp, colors.outlineVariant, shape)
            .clickable(onClickLabel = "Открыть экран текущего трека", onClick = onOpen)
            .padding(horizontal = 9.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Cover(track.uri, Modifier.size(43.dp))
        Column(Modifier.weight(1f)) {
            Text(state.title.ifBlank { track.title }, maxLines = 1, overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.titleSmall)
            Text(state.artist.ifBlank { track.artist }.ifBlank { formatTime(state.position) },
                maxLines = 1, overflow = TextOverflow.Ellipsis, color = colors.onSurfaceVariant,
                style = MaterialTheme.typography.bodySmall)
        }
        FilledIconButton(onClick = onToggle) { PlaybackIcon(state.playing) }
    }
}

@Composable
private fun TrackScreen(player: MediaController, state: Playback, track: LocalTrack) {
    var selected by remember(state.trackId) { mutableStateOf<Float?>(null) }
    val progress = if (state.duration > 0) (state.position.toFloat() / state.duration).coerceIn(0f, 1f) else 0f
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 14.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Cover(track.uri, Modifier.widthIn(max = 365.dp).fillMaxWidth(.87f).aspectRatio(1f))
        Text(state.title.ifBlank { track.title }, Modifier.padding(top = 22.dp),
            maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.headlineSmall)
        val artist = state.artist.ifBlank { track.artist }
        if (artist.isNotBlank()) Text(artist, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Slider(value = selected ?: progress, onValueChange = { selected = it }, onValueChangeFinished = {
            selected?.let { player.seekTo((it * state.duration).toLong()) }
            selected = null
        }, enabled = state.seekable && state.duration > 0)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(formatTime(selected?.let { (it * state.duration).toLong() } ?: state.position))
            Text(formatTime(state.duration))
        }
        Row(Modifier.padding(top = 14.dp), horizontalArrangement = Arrangement.spacedBy(20.dp),
            verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = player::seekToPreviousMediaItem, enabled = state.hasPrevious) {
                Glyph(R.drawable.ic_previous, "Предыдущий трек")
            }
            FilledIconButton(onClick = { togglePlayback(player, state) }, modifier = Modifier.size(58.dp)) {
                PlaybackIcon(state.playing)
            }
            IconButton(onClick = player::seekToNextMediaItem, enabled = state.hasNext) {
                Glyph(R.drawable.ic_next, "Следующий трек")
            }
        }
    }
}

private fun togglePlayback(player: MediaController, state: Playback) {
    if (state.playing) player.pause()
    else {
        if (player.playbackState == Player.STATE_ENDED) player.seekTo(0L)
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.play()
    }
}

@Composable
private fun Glyph(@DrawableRes icon: Int, description: String?) =
    Icon(painterResource(icon), description, Modifier.size(20.dp))

@Composable
private fun PlaybackIcon(playing: Boolean) =
    Glyph(if (playing) R.drawable.ic_pause else R.drawable.ic_play, if (playing) "Пауза" else "Воспроизвести")

private val artworkCache = object : LruCache<Uri, Bitmap>(16 * 1024) {
    override fun sizeOf(key: Uri, value: Bitmap): Int = (value.allocationByteCount / 1024).coerceAtLeast(1)
}

@Composable
private fun Cover(uri: Uri, modifier: Modifier) {
    val context = LocalContext.current.applicationContext
    val bitmap by produceState<Bitmap?>(null, uri) {
        value = withContext(Dispatchers.IO) { artworkCache.get(uri) ?: readCover(context, uri)?.also { artworkCache.put(uri, it) } }
    }
    Box(modifier.clip(RoundedCornerShape(12.dp)).background(MaterialTheme.colorScheme.surfaceVariant),
        contentAlignment = Alignment.Center) {
        val artwork = bitmap
        if (artwork != null) Image(artwork.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        else Glyph(R.drawable.ic_music, null)
    }
}

private fun readCover(context: Context, uri: Uri): Bitmap? {
    val retriever = MediaMetadataRetriever()
    return try {
        retriever.setDataSource(context, uri)
        val bytes = retriever.embeddedPicture?.takeIf { it.size <= 12_000_000 } ?: return null
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val options = BitmapFactory.Options()
        while (maxOf(bounds.outWidth, bounds.outHeight) / (options.inSampleSize.coerceAtLeast(1)) > 640) {
            options.inSampleSize = options.inSampleSize.coerceAtLeast(1) * 2
        }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
    } catch (_: RuntimeException) {
        null
    } finally {
        retriever.release()
    }
}
