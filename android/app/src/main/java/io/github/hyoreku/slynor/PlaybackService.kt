package io.github.hyoreku.slynor

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import org.json.JSONArray

internal val playbackSpeeds = listOf(0.5f, 0.75f, 1f, 1.25f, 1.5f, 1.75f, 2f)

internal fun Context.playbackStorage() = getSharedPreferences("playback", Context.MODE_PRIVATE)

@androidx.annotation.OptIn(UnstableApi::class)
class PlaybackService : MediaSessionService() {
    private var session: MediaSession? = null
    private val handler = Handler(Looper.getMainLooper())
    private val storage by lazy { playbackStorage() }
    private val libraryWorker = Executors.newSingleThreadExecutor()
    private var restoringQueue = true
    private val checkpoint = object : Runnable {
        override fun run() {
            val player = session?.player ?: return
            storage.edit().putLong("position", player.currentPosition.coerceAtLeast(0)).apply()
            if (player.isPlaying) handler.postDelayed(this, 5000)
        }
    }
    override fun onCreate() {
        super.onCreate()
        val attributes = AudioAttributes.Builder()
            .setUsage(C.USAGE_MEDIA)
            .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
            .build()
        SoundSettings(this).restore()
        val renderers = object : DefaultRenderersFactory(this) {
            override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enablePlaybackParams: Boolean): AudioSink =
                DefaultAudioSink.Builder(context).setAudioProcessors(arrayOf(SoundProcessor())).build()
        }
        val player = ExoPlayer.Builder(this, renderers).build().apply {
            setAudioAttributes(attributes, true)
            setHandleAudioBecomingNoisy(true)
            setWakeMode(C.WAKE_MODE_LOCAL)
        }
        val activity = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val library = LocalLibrary.get(applicationContext)
        restoreModes(player)
        session = MediaSession.Builder(this, player).setSessionActivity(activity)
            .setCallback(callback(library)).build()
        restoreQueue(player, library)
        player.addListener(object : Player.Listener {
            override fun onEvents(player: Player, events: Player.Events) {
                if (events.containsAny(Player.EVENT_IS_PLAYING_CHANGED, Player.EVENT_MEDIA_ITEM_TRANSITION)) {
                    if (player.isPlaying) player.currentMediaItem?.mediaId?.let { id ->
                        libraryWorker.execute { library.rememberPlayed(id) }
                    }
                    handler.removeCallbacks(checkpoint)
                    if (player.isPlaying) handler.postDelayed(checkpoint, 5000)
                }
                if (events.containsAny(
                        Player.EVENT_TIMELINE_CHANGED, Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_POSITION_DISCONTINUITY,
                        Player.EVENT_IS_PLAYING_CHANGED, Player.EVENT_PLAYBACK_PARAMETERS_CHANGED, Player.EVENT_REPEAT_MODE_CHANGED,
                    )
                ) savePlayback(player, writeQueue = events.contains(Player.EVENT_TIMELINE_CHANGED))
            }
        })
    }

    private fun callback(library: LocalLibrary) = object : MediaSession.Callback {
        override fun onAddMediaItems(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: List<MediaItem>,
        ): ListenableFuture<List<MediaItem>> {
            restoringQueue = false
            return Futures.submit(Callable {
                val tracks = library.readById()
                mediaItems.map { item ->
                    tracks[item.mediaId]?.mediaItem() ?: throw IllegalArgumentException("Unknown local track")
                }
            }, libraryWorker)
        }
    }

    private fun restoreModes(player: Player) {
        val speed = storage.getFloat("speed", 1f)
        player.setPlaybackSpeed(speed.takeIf { it in playbackSpeeds } ?: 1f)
        player.repeatMode = storage.getInt("repeat", Player.REPEAT_MODE_OFF)
            .coerceIn(Player.REPEAT_MODE_OFF, Player.REPEAT_MODE_ALL)
    }

    private fun restoreQueue(player: Player, library: LocalLibrary) {
        libraryWorker.execute {
            val saved = runCatching { JSONArray(storage.getString("queue", "[]")) }.getOrDefault(JSONArray())
            val tracks = if (saved.length() == 0) emptyMap() else library.readById()
            val queue = (0 until saved.length()).mapNotNull { tracks[saved.optString(it)]?.mediaItem() }
            val current = storage.getString("current", null)
            val index = queue.indexOfFirst { it.mediaId == current }
            val position = if (index >= 0) storage.getLong("position", 0).coerceAtLeast(0) else 0
            handler.post {
                if (restoringQueue && session?.player === player) {
                    restoringQueue = false
                    if (queue.isNotEmpty()) player.setMediaItems(queue, index.coerceAtLeast(0), position)
                }
            }
        }
    }

    private fun savePlayback(player: Player, writeQueue: Boolean) {
        val edit = storage.edit()
        if (writeQueue) {
            val queue = (0 until player.mediaItemCount).map { player.getMediaItemAt(it).mediaId }
            edit.putString("queue", JSONArray(queue).toString())
        }
        edit
            .putString("current", player.currentMediaItem?.mediaId)
            .putLong("position", player.currentPosition.coerceAtLeast(0))
            .putFloat("speed", player.playbackParameters.speed)
            .putInt("repeat", player.repeatMode)
            .apply()
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    override fun onDestroy() {
        handler.removeCallbacks(checkpoint)
        session?.let {
            if (!restoringQueue) savePlayback(it.player, writeQueue = false)
            it.player.release()
            it.release()
        }
        session = null
        libraryWorker.shutdown()
        super.onDestroy()
    }
}
