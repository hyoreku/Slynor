package io.github.hyoreku.slynor

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.util.LruCache
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Composable
internal fun CurrentTrackCard(track: LocalTrack, playback: Playback, onOpen: () -> Unit, onToggle: () -> Unit) {
    val art = rememberArtwork(track.uri)
    val colors = MaterialTheme.colorScheme
    Surface(
        shape = RoundedCornerShape(22.dp), color = colors.surface,
        border = BorderStroke(1.dp, colors.outlineVariant),
    ) {
        Column(
            Modifier.fillMaxWidth().background(
                Brush.horizontalGradient(listOf((art?.tone ?: colors.primary).copy(alpha = .19f), Color.Transparent)),
            ).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(13.dp),
        ) {
            Row(
                Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(13.dp),
            ) {
                Cover(art, Modifier.size(76.dp), brandedFallback = true)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("Текущий трек", color = colors.onSurfaceVariant, style = MaterialTheme.typography.labelLarge)
                    Text(playback.title.ifBlank { track.title }, maxLines = 2, overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.titleLarge)
                    val artist = playback.artist.ifBlank { track.artist }
                    if (artist.isNotBlank()) Text(artist, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        color = colors.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
                }
            }
            if (playback.duration > 0L) {
                TrackProgress(playback, Modifier.fillMaxWidth().height(3.dp))
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onOpen) { Text("Открыть плеер") }
                FilledIconButton(onClick = onToggle) { PlaybackIcon(playback.playing) }
            }
        }
    }
}

@Composable
internal fun PlayerBar(track: LocalTrack, state: Playback, onOpen: () -> Unit, onToggle: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val shape = RoundedCornerShape(18.dp)
    val art = rememberArtwork(track.uri)
    Surface(shape = shape, color = colors.surfaceContainer, border = BorderStroke(1.dp, colors.outlineVariant)) {
        Column(
            Modifier.fillMaxWidth().clip(shape)
                .clickable(onClickLabel = "Открыть экран текущего трека", onClick = onOpen)
                .padding(horizontal = 10.dp, vertical = 7.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Cover(art, Modifier.size(46.dp))
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(state.title.ifBlank { track.title }, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.titleMedium)
                    val artist = state.artist.ifBlank { track.artist }
                    if (artist.isNotBlank()) Text(artist, maxLines = 1, overflow = TextOverflow.Ellipsis, color = colors.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall)
                }
                FilledTonalIconButton(onClick = onToggle) { PlaybackIcon(state.playing) }
            }
            if (state.duration > 0L) {
                TrackProgress(state, Modifier.fillMaxWidth().padding(top = 5.dp).height(2.dp))
            }
        }
    }
}

@Composable
private fun TrackProgress(state: Playback, modifier: Modifier) = key(state.trackId) {
    val progress by animateFloatAsState(
        targetValue = state.progress, animationSpec = tween(210), label = "Позиция воспроизведения",
    )
    LinearProgressIndicator(
        progress = { progress }, modifier = modifier,
        trackColor = MaterialTheme.colorScheme.surfaceVariant, gapSize = 0.dp, drawStopIndicator = {},
    )
}

@Composable
internal fun TrackScreen(player: MediaController, state: Playback, track: LocalTrack, onQueue: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val art = rememberArtwork(track.uri)
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val content = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 22.dp, vertical = 12.dp)
        if (maxWidth >= 580.dp) {
            Row(content, horizontalArrangement = Arrangement.spacedBy(28.dp), verticalAlignment = Alignment.CenterVertically) {
                TrackArtwork(art, Modifier.weight(1f))
                TrackControls(player, state, track, onQueue, Modifier.weight(1f))
            }
        } else {
            Column(content, horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(20.dp)) {
                TrackArtwork(art, Modifier.widthIn(max = 360.dp).fillMaxWidth())
                TrackControls(player, state, track, onQueue, Modifier.fillMaxWidth())
            }
        }
    }
}

@Composable
private fun TrackArtwork(art: Artwork?, modifier: Modifier) {
    val colors = MaterialTheme.colorScheme
    val tone = art?.tone ?: colors.primary
    Surface(
        modifier.aspectRatio(1f), color = colors.surface, shape = RoundedCornerShape(26.dp),
        border = BorderStroke(1.dp, colors.outlineVariant),
    ) {
        Box(Modifier.fillMaxSize().background(
            Brush.radialGradient(listOf(tone.copy(alpha = .32f), colors.surface)),
        ).padding(12.dp)) {
            Cover(art, Modifier.fillMaxSize(), brandedFallback = true)
        }
    }
}

@Composable
private fun TrackControls(player: MediaController, state: Playback, track: LocalTrack, onQueue: () -> Unit, modifier: Modifier) {
    var selected by remember(state.trackId) { mutableStateOf<Float?>(null) }
    val colors = MaterialTheme.colorScheme
    val artist = state.artist.ifBlank { track.artist }
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        Text(state.title.ifBlank { track.title }, maxLines = 3, overflow = TextOverflow.Ellipsis,
            style = MaterialTheme.typography.headlineMedium, textAlign = TextAlign.Center)
        if (artist.isNotBlank()) Text(artist, Modifier.padding(top = 5.dp), color = colors.onSurfaceVariant,
            textAlign = TextAlign.Center, style = MaterialTheme.typography.bodyLarge)
        Slider(
            value = selected ?: state.progress,
            onValueChange = { selected = it },
            onValueChangeFinished = {
                selected?.let { player.seekTo((it * state.duration).toLong()) }
                selected = null
            },
            enabled = state.seekable && state.duration > 0L,
            modifier = Modifier.padding(top = 12.dp).semantics { contentDescription = "Позиция в треке" },
        )
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(formatTime(selected?.let { (it * state.duration).toLong() } ?: state.position),
                color = colors.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
            Text(formatTime(state.duration), color = colors.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
        }
        Row(Modifier.padding(top = 16.dp), horizontalArrangement = Arrangement.spacedBy(20.dp),
            verticalAlignment = Alignment.CenterVertically) {
            FilledTonalIconButton(onClick = player::seekToPreviousMediaItem, enabled = state.hasPrevious) {
                Glyph(R.drawable.ic_previous, "Предыдущий трек")
            }
            FilledIconButton(onClick = { togglePlayback(player, state) }, modifier = Modifier.size(64.dp)) {
                PlaybackIcon(state.playing, Modifier.size(23.dp))
            }
            FilledTonalIconButton(onClick = player::seekToNextMediaItem, enabled = state.hasNext) {
                Glyph(R.drawable.ic_next, "Следующий трек")
            }
        }
        if (state.queue.isNotEmpty()) {
            TextButton(onClick = onQueue, modifier = Modifier.padding(top = 8.dp)) {
                Glyph(R.drawable.ic_queue, null, Modifier.size(17.dp))
                Text("Открыть очередь", Modifier.padding(start = 7.dp))
            }
        }
    }
}

private val Playback.progress: Float
    get() = if (duration > 0) (position.toFloat() / duration).coerceIn(0f, 1f) else 0f

internal fun togglePlayback(player: MediaController, state: Playback) {
    if (state.playing) player.pause()
    else {
        if (player.playbackState == Player.STATE_ENDED) player.seekTo(0L)
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        player.play()
    }
}

internal fun MediaController.playSelection(track: LocalTrack, selection: List<LocalTrack>) {
    val start = selection.indexOf(track)
    if (start < 0) return
    setMediaItems(selection.map(LocalTrack::mediaItem), start, 0L)
    prepare()
    play()
}

internal fun MediaController.playFromQueue(index: Int) {
    seekTo(index, 0L)
    if (playbackState == Player.STATE_IDLE) prepare()
    play()
}

internal fun MediaController.enqueue(track: LocalTrack, playNext: Boolean) {
    val index = queueIndexOf(track)
    val count = mediaItemCount
    val current = currentMediaItemIndex
    if (index != -1 && index == current) return

    val insertion = if (playNext) (current + 1).coerceAtMost(count) else count
    if (index == -1) {
        addMediaItem(insertion, track.mediaItem())
    } else {
        val destination = if (index < insertion) insertion - 1 else insertion
        if (index != destination) moveMediaItem(index, destination)
    }
}

internal fun MediaController.removeFromQueue(track: LocalTrack) {
    val index = queueIndexOf(track)
    if (index >= 0) removeMediaItem(index)
}

private fun MediaController.queueIndexOf(track: LocalTrack): Int {
    val id = track.uri.toString()
    return (0 until mediaItemCount).firstOrNull { getMediaItemAt(it).mediaId == id } ?: -1
}

@Composable
private fun PlaybackIcon(playing: Boolean, modifier: Modifier = Modifier) =
    Glyph(if (playing) R.drawable.ic_pause else R.drawable.ic_play, if (playing) "Пауза" else "Воспроизвести", modifier)

internal data class Artwork(val bitmap: Bitmap?, val tone: Color?)

private val artworkCache = object : LruCache<Uri, Artwork>(16 * 1024) {
    override fun sizeOf(key: Uri, value: Artwork): Int = ((value.bitmap?.allocationByteCount ?: 0) / 1024).coerceAtLeast(4)
}

@Composable
internal fun rememberArtwork(uri: Uri): Artwork? = key(uri) {
    val context = LocalContext.current.applicationContext
    val artwork by produceState(artworkCache.get(uri), uri) {
        if (value == null) {
            value = withContext(Dispatchers.IO) {
                val bitmap = readCover(context, uri)
                Artwork(bitmap, bitmap?.let(::artworkTone)).also { artworkCache.put(uri, it) }
            }
        }
    }
    artwork
}

@Composable
internal fun Cover(art: Artwork?, modifier: Modifier, brandedFallback: Boolean = false) {
    Box(modifier.clip(RoundedCornerShape(12.dp)).background(MaterialTheme.colorScheme.surfaceVariant),
        contentAlignment = Alignment.Center) {
        Crossfade(art?.bitmap, animationSpec = tween(160), label = "Обложка") { bitmap ->
            if (bitmap != null) Image(bitmap.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
            else if (brandedFallback) Image(painterResource(R.drawable.slynor_icon), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
            else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Glyph(R.drawable.ic_music, null) }
        }
    }
}

private fun artworkTone(bitmap: Bitmap): Color {
    var r = 0
    var g = 0
    var b = 0
    var total = 0
    for (y in 1..7) for (x in 1..7) {
        val pixel = bitmap.getPixel(x * (bitmap.width - 1) / 8, y * (bitmap.height - 1) / 8)
        val red = android.graphics.Color.red(pixel)
        val green = android.graphics.Color.green(pixel)
        val blue = android.graphics.Color.blue(pixel)
        val weight = 1 + (maxOf(red, green, blue) - minOf(red, green, blue)) / 32
        r += red * weight
        g += green * weight
        b += blue * weight
        total += weight
    }
    return Color(r / total, g / total, b / total)
}

private fun readCover(context: Context, uri: Uri): Bitmap? {
    val retriever = MediaMetadataRetriever()
    return try {
        retriever.setDataSource(context, uri)
        val bytes = retriever.embeddedPicture?.takeIf { it.size <= 12_000_000 } ?: return null
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val options = BitmapFactory.Options().apply { inSampleSize = 1 }
        while (maxOf(bounds.outWidth, bounds.outHeight) / options.inSampleSize > 640) {
            options.inSampleSize *= 2
        }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
    } catch (_: RuntimeException) {
        null
    } finally {
        retriever.release()
    }
}
