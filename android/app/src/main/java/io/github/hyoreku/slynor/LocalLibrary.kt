package io.github.hyoreku.slynor

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.provider.DocumentsContract
import android.provider.DocumentsContract.Document
import android.provider.OpenableColumns
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import java.io.File
import java.io.IOException
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

internal data class Playlist(
    val id: String, val name: String, val trackIds: List<String> = emptyList(), val cover: String? = null,
)

internal class PlaylistNameExists : IllegalArgumentException("Плейлист с таким названием уже существует.")

internal data class LibrarySnapshot(
    val tracks: List<LocalTrack>,
    val playlists: List<Playlist>,
    val history: List<String>,
)

internal class LocalLibrary(private val context: Context) {
    internal val storage = context.getSharedPreferences("library", Context.MODE_PRIVATE)
    private val resolver = context.contentResolver
    private val coverDirectory = File(context.filesDir, "playlist-covers")

    fun snapshot(): LibrarySnapshot = synchronized(lock) {
        LibrarySnapshot(read(), readPlaylists(), JSONArray(storage.getString("history", "[]")).strings())
    }

    fun read(): List<LocalTrack> = decodeTracks(JSONArray(storage.getString("tracks", "[]")))

    private fun readPlaylists(): List<Playlist> = decodePlaylists(JSONArray(storage.getString("playlists", "[]")))

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

    fun savePlaylist(id: String?, name: String, cover: Bitmap?, removeCover: Boolean, initialTracks: List<String> = emptyList()): Playlist =
        synchronized(lock) {
            val playlists = readPlaylists()
            val previous = if (id == null) {
                val available = read().mapTo(mutableSetOf(), LocalTrack::id)
                val tracks = initialTracks.filter { it in available }.distinct()
                Playlist(UUID.randomUUID().toString(), name, tracks)
            } else playlists.firstOrNull { it.id == id } ?: throw IOException("Playlist no longer exists")
            if ((id == null || !previous.name.equals(name, ignoreCase = true)) &&
                playlists.any { it.id != id && it.name.equals(name, ignoreCase = true) }
            ) throw PlaylistNameExists()
            val replacement = cover?.let { writeCover(it) }
            val coverKey = when {
                replacement != null -> replacement
                removeCover -> null
                else -> previous.cover
            }
            val updated = previous.copy(name = name, cover = coverKey)
            val next = if (id == null) playlists + updated else playlists.map { if (it.id == id) updated else it }
            if (!storage.edit().putString("playlists", encodePlaylists(next)).commit()) {
                replacement?.let { coverFile(it).delete() }
                throw IOException("Could not save playlist")
            }
            deleteUnusedCover(previous.cover, next)
            updated
        }

    fun duplicatePlaylist(id: String): Playlist = synchronized(lock) {
        val playlists = readPlaylists()
        val source = playlists.firstOrNull { it.id == id } ?: throw IOException("Playlist no longer exists")
        val duplicate = source.copy(
            id = UUID.randomUUID().toString(),
            name = availablePlaylistName(source.name, "копия", playlists.map(Playlist::name)),
        )
        if (!storage.edit().putString("playlists", encodePlaylists(playlists + duplicate)).commit()) {
            throw IOException("Could not duplicate playlist")
        }
        duplicate
    }

    fun deletePlaylist(id: String) = synchronized(lock) {
        val playlists = readPlaylists()
        val removed = playlists.firstOrNull { it.id == id } ?: return@synchronized
        val next = playlists.filterNot { it.id == id }
        if (!storage.edit().putString("playlists", encodePlaylists(next)).commit()) {
            throw IOException("Could not delete playlist")
        }
        deleteUnusedCover(removed.cover, next)
    }

    private fun deleteUnusedCover(key: String?, playlists: List<Playlist>) {
        if (key != null && playlists.none { it.cover == key }) coverFile(key).delete()
    }

    fun readPlaylistCover(key: String): Bitmap? = BitmapFactory.decodeFile(coverFile(key).path)

    fun exportLibrary(): BackupLibrary = synchronized(lock) {
        val playlists = readPlaylists()
        val covers = playlists.mapNotNull(Playlist::cover).distinct().mapNotNull { key ->
            val file = coverFile(key)
            if (file.isFile) key to file.readBytes() else null
        }.toMap()
        BackupLibrary(read(), playlists, covers)
    }

    fun restoreLibrary(backup: BackupLibrary): BackupResult = synchronized(lock) {
        val active = read().associateByTo(linkedMapOf(), LocalTrack::id)
        val byDocument = active.values.associateBy { documentKey(it.uri) }
        val permitted = resolver.persistedUriPermissions.filter { it.isReadPermission }.mapTo(hashSetOf()) { it.uri }
        val resolved = mutableMapOf<String, String>()
        for (track in backup.tracks) {
            val existing = byDocument[documentKey(track.uri)]
            val local = when {
                existing != null -> existing.copy(favorite = track.favorite)
                grantUri(track.uri) in permitted -> track
                else -> continue
            }
            active[local.id] = local
            resolved[track.id] = local.id
        }
        val before = readPlaylists()
        val restored = before.associateByTo(linkedMapOf(), Playlist::id)
        val covers = mutableMapOf<String, String>()
        try {
            for (playlist in backup.playlists) {
                val cover = playlist.cover?.let { key ->
                    backup.covers[key]?.let { data -> covers.getOrPut(key) { createCover { it.write(data) } } }
                }
                val usedNames = restored.values.filter { it.id != playlist.id }.map(Playlist::name)
                val name = if (usedNames.any { it.equals(playlist.name, ignoreCase = true) }) {
                    availablePlaylistName(playlist.name, "импорт", usedNames)
                } else playlist.name
                restored[playlist.id] = playlist.copy(name = name, cover = cover,
                    trackIds = playlist.trackIds.mapNotNull(resolved::get).distinct())
            }
            val playlists = restored.values.toList()
            if (!storage.edit().putString("tracks", encodeTracks(active.values.toList()))
                    .putString("playlists", encodePlaylists(playlists)).commit()) {
                throw IOException("Could not restore library")
            }
            before.mapNotNull(Playlist::cover).distinct().forEach { deleteUnusedCover(it, playlists) }
        } catch (error: IOException) {
            covers.values.forEach { coverFile(it).delete() }
            throw error
        }
        BackupResult(backup.playlists.size, backup.tracks.size - resolved.size)
    }

    private fun coverFile(key: String) = File(coverDirectory, "$key.jpg")

    private fun writeCover(bitmap: Bitmap): String = createCover {
        if (!bitmap.compress(Bitmap.CompressFormat.JPEG, 88, it)) throw IOException("Could not save cover")
    }

    private fun createCover(write: (java.io.OutputStream) -> Unit): String {
        if (!coverDirectory.isDirectory && !coverDirectory.mkdirs()) throw IOException("Could not create cover directory")
        val key = UUID.randomUUID().toString()
        val file = coverFile(key)
        try {
            file.outputStream().use(write)
        } catch (error: IOException) {
            file.delete()
            throw error
        }
        return key
    }

    fun addToPlaylist(id: String, trackIds: List<String>) = updatePlaylist(id) {
        val available = read().mapTo(mutableSetOf(), LocalTrack::id)
        it.copy(trackIds = (it.trackIds + trackIds.filter { trackId -> trackId in available }).distinct())
    }

    fun addToPlaylists(trackIds: List<String>, playlistIds: Set<String>) = updatePlaylists { playlists ->
        val available = read().mapTo(mutableSetOf(), LocalTrack::id)
        val newTracks = trackIds.filter { it in available }
        playlists.map { playlist ->
            if (playlist.id in playlistIds) playlist.copy(trackIds = (playlist.trackIds + newTracks).distinct())
            else playlist
        }
    }

    fun removeFromPlaylist(id: String, trackIds: Set<String>) = updatePlaylist(id) {
        it.copy(trackIds = it.trackIds.filterNot(trackIds::contains))
    }

    fun moveInPlaylist(id: String, trackId: String, offset: Int) = updatePlaylist(id) {
        val from = it.trackIds.indexOf(trackId)
        val to = from + offset
        if (from < 0 || to !in it.trackIds.indices) it
        else it.copy(trackIds = it.trackIds.toMutableList().apply { add(to, removeAt(from)) })
    }

    fun moveInPlaylist(id: String, trackId: String, anchorId: String) = updatePlaylist(id) {
        val from = it.trackIds.indexOf(trackId)
        val to = it.trackIds.indexOf(anchorId)
        if (from < 0 || to < 0 || from == to) it
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

private fun availablePlaylistName(name: String, label: String, taken: Collection<String>): String {
    var number = 1
    while (true) {
        val suffix = if (number == 1) " ($label)" else " ($label $number)"
        val candidate = name.take(80 - suffix.length).trimEnd() + suffix
        if (taken.none { it.equals(candidate, ignoreCase = true) }) return candidate
        number++
    }
}

private fun JSONArray.strings() = List(length()) { getString(it) }

private fun <T> JSONArray.objects(decode: (JSONObject) -> T) = List(length()) { decode(getJSONObject(it)) }

internal val localUuid = Regex("[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}")

internal fun decodeTracks(array: JSONArray): List<LocalTrack> = array.objects { track ->
    LocalTrack(
        Uri.parse(track.getString("uri")), track.getString("title"),
        track.optString("artist"), track.optBoolean("favorite"),
        track.optString("album"), track.optLong("duration").coerceAtLeast(0),
        track.optBoolean("metadataRead", track.has("duration")),
    )
}

internal fun decodePlaylists(array: JSONArray): List<Playlist> = array.objects { playlist ->
    Playlist(
        playlist.getString("id"), playlist.getString("name"), playlist.getJSONArray("tracks").strings(),
        playlist.optString("cover", "").takeIf(localUuid::matches),
    )
}

internal fun encodeTracks(tracks: List<LocalTrack>): String = JSONArray().apply {
    tracks.forEach { track ->
        put(JSONObject().put("uri", track.id).put("title", track.title).put("artist", track.artist)
            .put("favorite", track.favorite).put("album", track.album).put("duration", track.duration)
            .put("metadataRead", track.metadataRead))
    }
}.toString()

internal fun encodePlaylists(playlists: List<Playlist>): String = JSONArray().apply {
    playlists.forEach { playlist ->
        put(JSONObject().put("id", playlist.id).put("name", playlist.name)
            .put("tracks", JSONArray(playlist.trackIds)).put("cover", playlist.cover))
    }
}.toString()
