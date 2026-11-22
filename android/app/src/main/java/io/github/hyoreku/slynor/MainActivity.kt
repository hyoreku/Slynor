package io.github.hyoreku.slynor

import android.content.ComponentName
import android.content.SharedPreferences
import android.graphics.Color
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
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
        val library = LocalLibrary(applicationContext)
        setContent {
            var theme by remember { mutableStateOf(ThemeMode.read(library.storage)) }
            val appearance = remember { AppearanceSettings(applicationContext) }
            LaunchedEffect(appearance) { appearance.loadWallpaper() }
            val systemDark = isSystemInDarkTheme()
            val dark = theme == ThemeMode.Dark || (theme == ThemeMode.System && systemDark)
            LaunchedEffect(dark) {
                val barStyle = if (dark) SystemBarStyle.dark(Color.TRANSPARENT)
                    else SystemBarStyle.light(Color.TRANSPARENT, Color.BLACK)
                enableEdgeToEdge(statusBarStyle = barStyle, navigationBarStyle = barStyle)
            }
            SlynorTheme(dark, appearance.accent) {
                MusicLibrary(library, controller, connectionError, theme, appearance) {
                    theme = it
                    library.storage.edit().putString("theme", it.name).apply()
                }
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

@Composable
private fun MusicLibrary(
    library: LocalLibrary,
    player: MediaController?,
    connectionError: Boolean,
    theme: ThemeMode,
    appearance: AppearanceSettings,
    onTheme: (ThemeMode) -> Unit,
) {
    var data by remember(library) { mutableStateOf(library.snapshot()) }
    var importing by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    val playback = rememberPlayback(player)
    val scope = rememberCoroutineScope()

    DisposableEffect(library) {
        val libraryListener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
            if (key in setOf("tracks", "playlists", "history")) data = library.snapshot()
        }
        library.storage.registerOnSharedPreferenceChangeListener(libraryListener)
        data = library.snapshot()
        onDispose { library.storage.unregisterOnSharedPreferenceChangeListener(libraryListener) }
    }
    LaunchedEffect(library) {
        withContext(Dispatchers.IO) { library.refreshLegacyMetadata() }
    }
    fun importTracks(read: () -> LocalLibrary.ImportResult) {
        if (importing) return
        importing = true
        scope.launch {
            try {
                val result = withContext(Dispatchers.IO) { read() }
                message = when {
                    result.unavailable > 0 -> "Добавлено: ${result.added}. Без доступа: ${result.unavailable}."
                    result.added == 0 -> "Новых аудиофайлов не найдено."
                    else -> null
                }
            } finally {
                importing = false
            }
        }
    }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNotEmpty()) importTracks { library.add(uris) }
    }
    val folderPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) importTracks { library.addFolder(uri) }
    }

    val status = when {
        connectionError -> "Не удалось подключиться к плееру."
        playback.failed -> "Не удалось воспроизвести файл. Проверьте доступ к нему."
        importing -> "Добавление файлов…"
        else -> message
    }
    SlynorScreens(
        library = library,
        data = data,
        player = player,
        playback = playback,
        status = status,
        importing = importing,
        theme = theme,
        appearance = appearance,
        onTheme = onTheme,
        onAdd = { if (!importing) picker.launch(arrayOf("audio/*")) },
        onAddFolder = { if (!importing) folderPicker.launch(null) },
        onPlay = { track, selection -> player?.playSelection(track, selection) },
        onFavorite = { track -> library.setFavorite(track.id, !track.favorite) },
        onRemove = { track ->
            player?.removeFromQueue(track)
            library.remove(track)
        },
    )
}

internal data class Playback(
    val trackId: String? = null,
    val title: String = "",
    val artist: String = "",
    val playing: Boolean = false,
    val position: Long = 0,
    val duration: Long = 0,
    val hasPrevious: Boolean = false,
    val hasNext: Boolean = false,
    val seekable: Boolean = false,
    val failed: Boolean = false,
    val queue: List<String> = emptyList(),
    val queueIndex: Int = -1,
    val speed: Float = 1f,
    val repeat: Int = Player.REPEAT_MODE_OFF,
)

private fun Player.snapshot() = Playback(
    trackId = currentMediaItem?.mediaId,
    title = mediaMetadata.title?.toString().orEmpty(),
    artist = mediaMetadata.artist?.toString().orEmpty(),
    playing = playWhenReady && playbackState != Player.STATE_ENDED,
    position = currentPosition.coerceAtLeast(0),
    duration = duration.coerceAtLeast(0),
    hasPrevious = hasPreviousMediaItem(),
    hasNext = hasNextMediaItem(),
    seekable = isCurrentMediaItemSeekable,
    failed = playerError != null,
    queue = (0 until mediaItemCount).map { getMediaItemAt(it).mediaId },
    queueIndex = currentMediaItemIndex,
    speed = playbackParameters.speed,
    repeat = repeatMode,
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
                state = state.copy(position = player.currentPosition.coerceAtLeast(0))
            }
        }
    }
    return state
}

internal fun formatTime(ms: Long): String = String.format(Locale.ROOT, "%d:%02d", ms / 60_000, ms / 1000 % 60)
