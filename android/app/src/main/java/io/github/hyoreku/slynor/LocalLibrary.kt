package io.github.hyoreku.slynor

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import org.json.JSONArray
import org.json.JSONObject

data class LocalTrack(val uri: Uri, val title: String) {
    fun mediaItem(): MediaItem = MediaItem.Builder()
        .setMediaId(uri.toString())
        .setUri(uri)
        .setMediaMetadata(MediaMetadata.Builder().setTitle(title).build())
        .build()
}

class LocalLibrary(context: Context) {
    private val storage = context.getSharedPreferences("library", Context.MODE_PRIVATE)
    private val resolver = context.contentResolver

    fun read(): List<LocalTrack> {
        val saved = JSONArray(storage.getString("tracks", "[]"))
        return List(saved.length()) { index ->
            val track = saved.getJSONObject(index)
            LocalTrack(Uri.parse(track.getString("uri")), track.getString("title"))
        }
    }

    data class ImportResult(val tracks: List<LocalTrack>, val unavailable: Int)

    fun add(uris: List<Uri>): ImportResult {
        val tracks = read().associateByTo(linkedMapOf()) { it.uri }
        var unavailable = 0
        for (uri in uris) {
            if (uri in tracks) continue
            try {
                require(uri.scheme == "content")
                resolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
            } catch (_: SecurityException) {
                unavailable++
                continue
            } catch (_: IllegalArgumentException) {
                unavailable++
                continue
            }
            val title = try {
                resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
                    ?.use { if (it.moveToFirst()) it.getString(0) else null }
            } catch (_: SecurityException) {
                null
            } catch (_: IllegalArgumentException) {
                null
            }
            tracks[uri] = LocalTrack(uri, title ?: "Аудиофайл")
        }
        save(tracks.values)
        return ImportResult(tracks.values.toList(), unavailable)
    }

    fun remove(track: LocalTrack): List<LocalTrack> {
        val tracks = read().filterNot { it.uri == track.uri }
        save(tracks)
        try {
            resolver.releasePersistableUriPermission(track.uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        } catch (_: SecurityException) {
            // The document provider may already have revoked the grant.
        }
        return tracks
    }

    private fun save(tracks: Collection<LocalTrack>) {
        val encoded = JSONArray()
        tracks.forEach { encoded.put(JSONObject().put("uri", it.uri.toString()).put("title", it.title)) }
        storage.edit().putString("tracks", encoded.toString()).apply()
    }
}
