package io.github.hyoreku.slynor

import android.content.ComponentName
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.ListenableFuture
import java.util.Locale
import java.util.concurrent.ExecutionException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    private var connection: ListenableFuture<MediaController>? = null
    private var controller by mutableStateOf<MediaController?>(null)
    private var connectionError by mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val library = LocalLibrary(applicationContext)
        setContent {
            MaterialTheme(colorScheme = slynorColors) {
                MusicLibrary(library, controller, connectionError)
            }
        }
    }

    override fun onStart() {
        super.onStart()
        connectionError = false
        val token = SessionToken(this, ComponentName(this, PlaybackService::class.java))
        val request = MediaController.Builder(this, token).buildAsync()
        connection = request
        request.addListener({
            if (connection === request) {
                try {
                    controller = request.get()
                } catch (_: ExecutionException) {
                    connectionError = true
                }
            }
        }, ContextCompat.getMainExecutor(this))
    }

    override fun onStop() {
        controller = null
        val request = connection
        connection = null
        request?.let { MediaController.releaseFuture(it) }
        super.onStop()
    }
}

private val slynorColors = darkColorScheme(
    primary = Color(0xFFCFB6FF),
    onPrimary = Color(0xFF26173D),
    background = Color(0xFF141319),
    surface = Color(0xFF201E27),
)

@Composable
private fun MusicLibrary(library: LocalLibrary, player: MediaController?, connectionError: Boolean) {
    var tracks by remember(library) { mutableStateOf(library.read()) }
    var importing by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    val playback = rememberPlayback(player)
    val scope = rememberCoroutineScope()
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNotEmpty()) {
            importing = true
            scope.launch {
                try {
                    val result = withContext(Dispatchers.IO) { library.add(uris) }
                    tracks = result.tracks
                    message = if (result.unavailable == 0) null else "Не удалось получить доступ к файлам (${result.unavailable})."
                } finally {
                    importing = false
                }
            }
        }
    }

    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(modifier = Modifier.safeDrawingPadding().padding(16.dp)) {
            Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween, Alignment.CenterVertically) {
                Text("Slynor", style = MaterialTheme.typography.headlineMedium)
                Button(onClick = { picker.launch(arrayOf("audio/*")) }, enabled = !importing) {
                    Text("Добавить файлы")
                }
            }
            val status = when {
                connectionError -> "Не удалось подключиться к плееру."
                playback.failed -> "Не удалось воспроизвести файл. Проверь доступ к нему."
                importing -> "Добавление файлов…"
                else -> message
            }
            if (status != null) Text(status, Modifier.padding(vertical = 8.dp))
            if (tracks.isEmpty()) {
                Text(
                    "Выбери аудиофайлы, чтобы добавить их в библиотеку.",
                    Modifier.weight(1f).padding(top = 20.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                LazyColumn(Modifier.weight(1f).padding(top = 12.dp)) {
                    itemsIndexed(tracks, key = { _, track -> track.uri.toString() }) { index, track ->
                        Row(
                            Modifier.fillMaxWidth().clickable(enabled = player != null && !importing) {
                                player?.setMediaItems(tracks.map(LocalTrack::mediaItem), index, 0)
                                player?.prepare()
                                player?.play()
                            }.padding(start = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                track.title, Modifier.weight(1f).padding(vertical = 16.dp),
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                                color = if (playback.trackId == track.uri.toString()) MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.onSurface,
                            )
                            TextButton(enabled = !importing, onClick = {
                                if (player != null) {
                                    val item = (0 until player.mediaItemCount).firstOrNull {
                                        player.getMediaItemAt(it).mediaId == track.uri.toString()
                                    }
                                    if (item != null) player.removeMediaItem(item)
                                }
                                tracks = library.remove(track)
                            }) { Text("Убрать") }
                        }
                        HorizontalDivider()
                    }
                }
            }
            if (player != null && playback.trackId != null) PlaybackControls(player, playback)
        }
    }
}

private data class Playback(
    val trackId: String? = null,
    val title: String = "",
    val playing: Boolean = false,
    val position: Long = 0,
    val duration: Long = 0,
    val hasPrevious: Boolean = false,
    val hasNext: Boolean = false,
    val seekable: Boolean = false,
    val failed: Boolean = false,
)

private fun Player.snapshot() = Playback(
    trackId = currentMediaItem?.mediaId,
    title = mediaMetadata.title?.toString().orEmpty(),
    playing = playWhenReady && playbackState != Player.STATE_ENDED,
    position = currentPosition.coerceAtLeast(0),
    duration = duration.coerceAtLeast(0),
    hasPrevious = hasPreviousMediaItem(),
    hasNext = hasNextMediaItem(),
    seekable = isCurrentMediaItemSeekable,
    failed = playerError != null,
)

@Composable
private fun rememberPlayback(player: MediaController?): Playback {
    var state by remember(player) { mutableStateOf(player?.snapshot() ?: Playback()) }
    DisposableEffect(player) {
        val listener = object : Player.Listener {
            override fun onEvents(player: Player, events: Player.Events) { state = player.snapshot() }
        }
        player?.addListener(listener)
        onDispose { player?.removeListener(listener) }
    }
    LaunchedEffect(player, state.playing) {
        if (player != null && state.playing) {
            while (true) {
                delay(250)
                state = player.snapshot()
            }
        }
    }
    return state
}

@Composable
private fun PlaybackControls(player: MediaController, state: Playback) {
    var selectedPosition by remember(state.trackId) { mutableStateOf<Float?>(null) }
    val progress = if (state.duration > 0L) (state.position.toFloat() / state.duration).coerceIn(0f, 1f) else 0f
    Column(Modifier.padding(top = 16.dp)) {
        Text(state.title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Slider(
            value = selectedPosition ?: progress,
            onValueChange = { selectedPosition = it },
            onValueChangeFinished = {
                selectedPosition?.let { player.seekTo((it * state.duration).toLong()) }
                selectedPosition = null
            },
            enabled = state.seekable && state.duration > 0L,
        )
        val shownPosition = selectedPosition?.let { (it * state.duration).toLong() } ?: state.position
        Text("${formatTime(shownPosition)} / ${formatTime(state.duration)}")
        Row(Modifier.fillMaxWidth(), Arrangement.SpaceBetween, Alignment.CenterVertically) {
            TextButton(onClick = player::seekToPreviousMediaItem, enabled = state.hasPrevious) { Text("Назад") }
            Button(onClick = {
                if (state.playing) player.pause()
                else {
                    if (player.playbackState == Player.STATE_ENDED) player.seekTo(0L)
                    if (player.playbackState == Player.STATE_IDLE) player.prepare()
                    player.play()
                }
            }) { Text(if (state.playing) "Пауза" else "Играть") }
            TextButton(onClick = player::seekToNextMediaItem, enabled = state.hasNext) { Text("Дальше") }
        }
    }
}

private fun formatTime(ms: Long): String = String.format(Locale.ROOT, "%d:%02d", ms / 60_000, ms / 1000 % 60)
