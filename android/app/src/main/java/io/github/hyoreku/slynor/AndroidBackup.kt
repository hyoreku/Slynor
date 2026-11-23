package io.github.hyoreku.slynor

import android.content.Context
import android.content.SharedPreferences
import android.graphics.BitmapFactory
import android.net.Uri
import android.util.AtomicFile
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream
import org.json.JSONArray
import org.json.JSONObject

internal data class BackupLibrary(
    val tracks: List<LocalTrack>, val playlists: List<Playlist>, val covers: Map<String, ByteArray>,
)

internal data class BackupResult(val playlists: Int, val unavailable: Int)

internal class AndroidBackup(private val context: Context, private val library: LocalLibrary) {
    private val appearance = context.getSharedPreferences("appearance", Context.MODE_PRIVATE)
    private val sound = context.playbackStorage()
    private val wallpaper = AtomicFile(File(context.filesDir, WALLPAPER_FILE))
    private val settings = listOf(
        Settings("library", library.storage, listOf("theme", "librarySort")),
        Settings("appearance", appearance, listOf("accent")),
        Settings("sound", sound, listOf("equalizer", "equalizerPresets", "visualizer")),
    )

    fun write(destination: Uri) {
        val data = library.exportLibrary()
        val preferences = JSONObject()
        for ((name, prefs, keys) in settings) {
            val section = JSONObject()
            for (key in keys) prefs.getString(key, null)?.let { section.put(key, it) }
            preferences.put(name, section)
        }
        val manifest = JSONObject().put("format", "slynor-android").put("version", 1)
            .put("tracks", JSONArray(encodeTracks(data.tracks)))
            .put("playlists", JSONArray(encodePlaylists(data.playlists)))
            .put("settings", preferences)
            .toString().toByteArray(Charsets.UTF_8)
        val background = if (wallpaper.baseFile.isFile) wallpaper.readFully() else null
        val covers = data.covers
        require(manifest.size <= MAX_MANIFEST && covers.size <= MAX_COVERS)
        require(covers.values.all { it.size <= MAX_COVER } && (background?.size ?: 0) <= MAX_WALLPAPER)
        require(manifest.size + (background?.size ?: 0) + covers.values.sumOf { it.size.toLong() } <= MAX_BACKUP)

        val output = context.contentResolver.openOutputStream(destination, "wt") ?: throw IOException("Cannot write backup")
        ZipOutputStream(output.buffered()).use { zip ->
            zip.writeEntry("library.json", manifest)
            for ((key, data) in covers) zip.writeEntry("covers/$key.jpg", data)
            if (background != null) zip.writeEntry("wallpaper.jpg", background)
        }
    }

    fun restore(source: Uri): BackupResult {
        val files = readZip(source)
        val manifest = JSONObject(files.getValue("library.json").toString(Charsets.UTF_8))
        require(manifest.getString("format") == "slynor-android" && manifest.getInt("version") == 1)
        val tracks = decodeTracks(manifest.getJSONArray("tracks"))
        val playlists = decodePlaylists(manifest.getJSONArray("playlists"))
        val ids = tracks.mapTo(hashSetOf(), LocalTrack::id)
        require(ids.size == tracks.size && tracks.all { it.uri.scheme == "content" && !it.uri.authority.isNullOrBlank() })
        require(playlists.distinctBy(Playlist::id).size == playlists.size)
        require(playlists.all { localUuid.matches(it.id) && it.name.isNotBlank() && it.name.length <= 80 })
        val covers = files.filterKeys { it.startsWith("covers/") }
            .mapKeys { it.key.removePrefix("covers/").removeSuffix(".jpg") }
        for (cover in covers.values) requireJpeg(cover, 1280)
        val background = files["wallpaper.jpg"]
        if (background != null) requireJpeg(background, 3200)
        val preferences = manifest.getJSONObject("settings")
        val replacements = settings.map { (name, prefs, keys) ->
            val section = preferences.getJSONObject(name)
            val values = keys.associateWith { if (section.has(it)) section.getString(it) else null }
            prefs to values
        }

        val result = library.restoreLibrary(BackupLibrary(tracks, playlists, covers))
        if (background == null) wallpaper.delete() else {
            val output = wallpaper.startWrite()
            try {
                output.write(background)
                wallpaper.finishWrite(output)
            } catch (failure: IOException) {
                wallpaper.failWrite(output)
                throw failure
            }
        }
        for ((prefs, values) in replacements) {
            val edit = prefs.edit()
            for ((key, value) in values) edit.putString(key, value)
            if (!edit.commit()) throw IOException("Cannot save restored settings")
        }
        return result
    }

    private fun readZip(source: Uri): Map<String, ByteArray> {
        val input = context.contentResolver.openInputStream(source) ?: throw IOException("Cannot open backup")
        val files = mutableMapOf<String, ByteArray>()
        var remaining = MAX_BACKUP
        ZipInputStream(input.buffered()).use { zip ->
            val buffer = ByteArray(8192)
            while (true) {
                val name = zip.nextEntry?.name ?: break
                val limit = when {
                    name == "library.json" -> MAX_MANIFEST
                    name == "wallpaper.jpg" -> MAX_WALLPAPER
                    name.startsWith("covers/") && name.endsWith(".jpg") &&
                        localUuid.matches(name.removePrefix("covers/").removeSuffix(".jpg")) -> MAX_COVER
                    else -> throw IOException("Unexpected backup entry")
                }
                require(name !in files && files.size < MAX_COVERS + 2)
                val bytes = ByteArrayOutputStream()
                while (true) {
                    val read = zip.read(buffer)
                    if (read < 0) break
                    remaining -= read
                    require(remaining >= 0 && bytes.size() + read <= limit)
                    bytes.write(buffer, 0, read)
                }
                files[name] = bytes.toByteArray()
            }
        }
        return files
    }

    private data class Settings(val name: String, val prefs: SharedPreferences, val keys: List<String>)
}

internal const val WALLPAPER_FILE = "slynor-wallpaper.jpg"
private const val MAX_MANIFEST = 8 * 1024 * 1024
private const val MAX_COVER = 2 * 1024 * 1024
private const val MAX_WALLPAPER = 5 * 1024 * 1024
private const val MAX_BACKUP = 40 * 1024 * 1024
private const val MAX_COVERS = 1024

private fun ZipOutputStream.writeEntry(name: String, data: ByteArray) {
    putNextEntry(ZipEntry(name))
    write(data)
    closeEntry()
}

private fun requireJpeg(data: ByteArray, maxSide: Int) {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(data, 0, data.size, bounds)
    require(bounds.outMimeType == "image/jpeg" && bounds.outWidth in 1..maxSide && bounds.outHeight in 1..maxSide)
}
