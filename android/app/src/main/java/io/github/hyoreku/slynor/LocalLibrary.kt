package io.github.hyoreku.slynor

import android.content.Context
import android.content.Intent
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.provider.DocumentsContract
import android.provider.DocumentsContract.Document
import android.provider.OpenableColumns
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import java.util.UUID
import org.json.JSONArray
import org.json.JSONObject

data class LocalTrack(
    val uri: Uri,
    val title: String,
    val artist: String = "",
    val favorite: Boolean = false,
    val album: String = "",
    val duration: Long = 0,
    val metadataRead: Boolean = true,
) {
    val id: String get() = uri.toString()

    fun mediaItem(): MediaItem = MediaItem.Builder()
        .setMediaId(id)
        .setUri(uri)
        .setMediaMetadata(
            MediaMetadata.Builder().setTitle(title).setArtist(artist.ifBlank { null })
                .setAlbumTitle(album.ifBlank { null }).build(),
        )
        .build()
}

internal data class Playlist(val id: String, val name: String, val trackIds: List<String> = emptyList())

internal data class LibrarySnapshot(
    val tracks: List<LocalTrack>,
    val playlists: List<Playlist>,
    val history: List<String>,
)

internal class LocalLibrary(private val context: Context) {
    internal val storage = context.getSharedPreferences("library", Context.MODE_PRIVATE)
    private val resolver = context.contentResolver

    fun snapshot(): LibrarySnapshot = synchronized(lock) {
        LibrarySnapshot(read(), readPlaylists(), JSONArray(storage.getString("history", "[]")).strings())
    }

    fun read(): List<LocalTrack> = JSONArray(storage.getString("tracks", "[]")).objects { track ->
        LocalTrack(
            Uri.parse(track.getString("uri")), track.getString("title"),
            track.optString("artist"), track.optBoolean("favorite"),
            track.optString("album"), track.optLong("duration").coerceAtLeast(0),
            track.optBoolean("metadataRead", track.has("duration")),
        )
    }

    private fun readPlaylists(): List<Playlist> = JSONArray(storage.getString("playlists", "[]")).objects { playlist ->
        Playlist(playlist.getString("id"), playlist.getString("name"), playlist.getJSONArray("tracks").strings())
    }

    data class ImportResult(val added: Int = 0, val unavailable: Int = 0)

    fun add(uris: List<Uri>, folder: Uri? = null): ImportResult {
        val known = read().associateByTo(mutableMapOf()) { documentKey(it.uri) }
        val pending = mutableListOf<LocalTrack>()
        var unavailable = 0
        for (uri in uris) {
            val key = documentKey(uri)
            val existing = known[key]
            if (existing != null) {
                if (folder == null && existing.uri == uri && !takeGrant(uri)) unavailable++
                continue
            }
            if (folder == null && !takeGrant(uri)) {
                unavailable++
                continue
            }
            val title = try {
                resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
                    ?.use { if (it.moveToFirst()) it.getString(0) else null }
            } catch (_: RuntimeException) {
                null
            }
            val track = readTags(uri, title ?: "Аудиофайл")
            known[key] = track
            pending.add(track)
        }

        synchronized(lock) {
            val tracks = read()
            val keys = tracks.mapTo(mutableSetOf()) { documentKey(it.uri) }
            val added = pending.filter { keys.add(documentKey(it.uri)) }
            if (added.isNotEmpty()) storage.edit().putString("tracks", encodeTracks(tracks + added)).apply()
            return ImportResult(added.size, unavailable)
        }
    }

    fun addFolder(tree: Uri): ImportResult {
        if (!takeGrant(tree)) return ImportResult(unavailable = 1)
        val root = DocumentsContract.getTreeDocumentId(tree)
        val directories = ArrayDeque(listOf(root))
        val seen = mutableSetOf<String>()
        val audio = mutableListOf<Uri>()
        var unavailable = 0
        while (directories.isNotEmpty()) {
            val parent = directories.removeFirst()
            if (!seen.add(parent)) continue
            val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, parent)
            try {
                val found = resolver.query(
                    children, arrayOf(Document.COLUMN_DOCUMENT_ID, Document.COLUMN_MIME_TYPE, Document.COLUMN_DISPLAY_NAME),
                    null, null, null,
                )?.use { cursor ->
                    while (cursor.moveToNext()) {
                        val id = cursor.getString(0) ?: continue
                        val mime = cursor.getString(1).orEmpty()
                        val extension = cursor.getString(2).orEmpty().substringAfterLast('.', "").lowercase()
                        when {
                            mime == Document.MIME_TYPE_DIR -> directories.addLast(id)
                            mime.startsWith("audio/") || extension in audioExtensions ->
                                audio.add(DocumentsContract.buildDocumentUriUsingTree(tree, id))
                        }
                    }
                }
                if (found == null) unavailable++
            } catch (_: RuntimeException) {
                unavailable++
            }
        }
        val result = add(audio, folder = tree)
        synchronized(lock) { releaseIfUnused(tree, read()) }
        return result.copy(unavailable = result.unavailable + unavailable)
    }

    private fun takeGrant(uri: Uri): Boolean = try {
        require(uri.scheme == "content")
        resolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        true
    } catch (_: SecurityException) {
        false
    } catch (_: IllegalArgumentException) {
        false
    }

    fun refreshLegacyMetadata() {
        val updates = read().filterNot(LocalTrack::metadataRead).associate { track ->
            track.id to readTags(track.uri, track.title)
        }
        if (updates.isEmpty()) return
        synchronized(lock) {
            val tracks = read().map { previous ->
                val updated = updates[previous.id]
                if (updated == null || previous.metadataRead) previous
                else updated.copy(artist = updated.artist.ifBlank { previous.artist }, favorite = previous.favorite)
            }
            storage.edit().putString("tracks", encodeTracks(tracks)).apply()
        }
    }

    private fun readTags(uri: Uri, filename: String): LocalTrack {
        val retriever = MediaMetadataRetriever()
        return try {
            retriever.setDataSource(context, uri)
            fun tag(key: Int) = retriever.extractMetadata(key).orEmpty()
            LocalTrack(
                uri, tag(MediaMetadataRetriever.METADATA_KEY_TITLE).ifBlank { filename },
                tag(MediaMetadataRetriever.METADATA_KEY_ARTIST),
                album = tag(MediaMetadataRetriever.METADATA_KEY_ALBUM),
                duration = tag(MediaMetadataRetriever.METADATA_KEY_DURATION).toLongOrNull()?.coerceAtLeast(0) ?: 0,
            )
        } catch (_: RuntimeException) {
            LocalTrack(uri, filename)
        } finally {
            runCatching { retriever.release() }
        }
    }

    fun remove(track: LocalTrack) = synchronized(lock) {
        val tracks = read().filterNot { it.id == track.id }
        val playlists = readPlaylists().map { it.copy(trackIds = it.trackIds - track.id) }
        val history = JSONArray(storage.getString("history", "[]")).strings() - track.id
        storage.edit().putString("tracks", encodeTracks(tracks))
            .putString("playlists", encodePlaylists(playlists)).putString("history", JSONArray(history).toString()).apply()
        releaseIfUnused(grantUri(track.uri), tracks)
    }

    private fun releaseIfUnused(grant: Uri, remaining: List<LocalTrack>) {
        if (remaining.any { grantUri(it.uri) == grant }) return
        try {
            resolver.releasePersistableUriPermission(grant, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        } catch (_: SecurityException) {
            // The document provider may already have revoked the grant.
        }
    }

    fun setFavorite(id: String, favorite: Boolean) = synchronized(lock) {
        val tracks = read().map { if (it.id == id) it.copy(favorite = favorite) else it }
        storage.edit().putString("tracks", encodeTracks(tracks)).apply()
    }

    fun rememberPlayed(id: String) = synchronized(lock) {
        val history = JSONArray(storage.getString("history", "[]")).strings()
        if (history.firstOrNull() != id && read().any { it.id == id }) {
            storage.edit().putString("history", JSONArray((listOf(id) + (history - id)).take(50)).toString()).apply()
        }
    }

    fun createPlaylist(name: String, firstTrack: String? = null): Playlist = synchronized(lock) {
        val tracks = listOfNotNull(firstTrack?.takeIf { id -> read().any { it.id == id } })
        val playlist = Playlist(UUID.randomUUID().toString(), name, tracks)
        updatePlaylists { it + playlist }
        playlist
    }

    fun renamePlaylist(id: String, name: String) = updatePlaylist(id) { it.copy(name = name) }

    fun deletePlaylist(id: String) = updatePlaylists { playlists -> playlists.filterNot { it.id == id } }

    fun addToPlaylist(id: String, trackIds: List<String>) = updatePlaylist(id) {
        val available = read().mapTo(mutableSetOf(), LocalTrack::id)
        it.copy(trackIds = (it.trackIds + trackIds.filter { trackId -> trackId in available }).distinct())
    }

    fun removeFromPlaylist(id: String, trackId: String) = updatePlaylist(id) { it.copy(trackIds = it.trackIds - trackId) }

    fun moveInPlaylist(id: String, trackId: String, offset: Int) = updatePlaylist(id) {
        val from = it.trackIds.indexOf(trackId)
        val to = from + offset
        if (from < 0 || to !in it.trackIds.indices) it
        else it.copy(trackIds = it.trackIds.toMutableList().apply { add(to, removeAt(from)) })
    }

    private fun updatePlaylist(id: String, change: (Playlist) -> Playlist) =
        updatePlaylists { playlists -> playlists.map { if (it.id == id) change(it) else it } }

    private fun updatePlaylists(change: (List<Playlist>) -> List<Playlist>) = synchronized(lock) {
        storage.edit().putString("playlists", encodePlaylists(change(readPlaylists()))).apply()
    }

    private fun documentKey(uri: Uri): String =
        if (DocumentsContract.isDocumentUri(context, uri)) "${uri.authority}:${DocumentsContract.getDocumentId(uri)}"
        else uri.toString()

    private fun grantUri(uri: Uri): Uri = if (DocumentsContract.isTreeUri(uri)) {
        DocumentsContract.buildTreeDocumentUri(uri.authority, DocumentsContract.getTreeDocumentId(uri))
    } else uri

    private companion object {
        val lock = Any()
        val audioExtensions = setOf("mp3", "flac", "wav", "ogg", "oga", "m4a", "aac", "opus", "wma", "aiff", "amr", "mka")
    }
}

private fun JSONArray.strings() = List(length()) { getString(it) }

private fun <T> JSONArray.objects(decode: (JSONObject) -> T) = List(length()) { decode(getJSONObject(it)) }

private fun encodeTracks(tracks: List<LocalTrack>): String = JSONArray().apply {
    tracks.forEach { track ->
        put(JSONObject().put("uri", track.id).put("title", track.title).put("artist", track.artist)
            .put("favorite", track.favorite).put("album", track.album).put("duration", track.duration)
            .put("metadataRead", track.metadataRead))
    }
}.toString()

private fun encodePlaylists(playlists: List<Playlist>): String = JSONArray().apply {
    playlists.forEach { playlist ->
        put(JSONObject().put("id", playlist.id).put("name", playlist.name).put("tracks", JSONArray(playlist.trackIds)))
    }
}.toString()
