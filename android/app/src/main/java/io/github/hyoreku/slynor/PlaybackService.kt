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
import org.json.JSONArray

internal val playbackSpeeds = listOf(0.5f, 0.75f, 1f, 1.25f, 1.5f, 1.75f, 2f)

internal fun Context.playbackStorage() = getSharedPreferences("playback", Context.MODE_PRIVATE)

@androidx.annotation.OptIn(UnstableApi::class)
class PlaybackService : MediaSessionService() {
    private var session: MediaSession? = null
    private val handler = Handler(Looper.getMainLooper())
    private val storage by lazy { playbackStorage() }
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
        val library = LocalLibrary(applicationContext)
        restore(player, library)
        session = MediaSession.Builder(this, player).setSessionActivity(activity)
            .setCallback(callback(library)).build()
        player.addListener(object : Player.Listener {
            override fun onEvents(player: Player, events: Player.Events) {
                if (events.containsAny(Player.EVENT_IS_PLAYING_CHANGED, Player.EVENT_MEDIA_ITEM_TRANSITION)) {
                    if (player.isPlaying) player.currentMediaItem?.let { library.rememberPlayed(it.mediaId) }
                    handler.removeCallbacks(checkpoint)
                    if (player.isPlaying) handler.postDelayed(checkpoint, 5000)
                }
                if (events.containsAny(
                        Player.EVENT_TIMELINE_CHANGED, Player.EVENT_MEDIA_ITEM_TRANSITION, Player.EVENT_POSITION_DISCONTINUITY,
                        Player.EVENT_IS_PLAYING_CHANGED, Player.EVENT_PLAYBACK_PARAMETERS_CHANGED, Player.EVENT_REPEAT_MODE_CHANGED,
                    )
                ) savePlayback(player)
            }
        })
    }

    private fun callback(library: LocalLibrary) = object : MediaSession.Callback {
        override fun onAddMediaItems(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: List<MediaItem>,
        ): ListenableFuture<List<MediaItem>> {
            val tracks = library.read().associateBy(LocalTrack::id)
            val queue = mediaItems.map { item ->
                tracks[item.mediaId]?.mediaItem()
                    ?: return Futures.immediateFailedFuture(IllegalArgumentException("Unknown local track"))
            }
            return Futures.immediateFuture(queue)
        }
    }

    private fun restore(player: Player, library: LocalLibrary) {
        val speed = storage.getFloat("speed", 1f)
        player.setPlaybackSpeed(speed.takeIf { it in playbackSpeeds } ?: 1f)
        player.repeatMode = storage.getInt("repeat", Player.REPEAT_MODE_OFF)
            .coerceIn(Player.REPEAT_MODE_OFF, Player.REPEAT_MODE_ALL)
        val saved = JSONArray(storage.getString("queue", "[]"))
        val tracks = library.read().associateBy(LocalTrack::id)
        val queue = (0 until saved.length()).mapNotNull { tracks[saved.optString(it)] }
        if (queue.isEmpty()) return
        val current = storage.getString("current", null)
        val index = queue.indexOfFirst { it.id == current }
        val position = if (index >= 0) storage.getLong("position", 0).coerceAtLeast(0) else 0
        player.setMediaItems(queue.map(LocalTrack::mediaItem), index.coerceAtLeast(0), position)
    }

    private fun savePlayback(player: Player) {
        val queue = (0 until player.mediaItemCount).map { player.getMediaItemAt(it).mediaId }
        storage.edit()
            .putString("queue", JSONArray(queue).toString())
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
            savePlayback(it.player)
            it.player.release()
            it.release()
        }
        session = null
        super.onDestroy()
    }
}
