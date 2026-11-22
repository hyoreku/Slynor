package io.github.hyoreku.slynor

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.util.AtomicFile
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color
import java.io.File
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

internal enum class ThemeAccent(val label: String, val light: Color, val dark: Color) {
    Lilac("Сиреневый", Color(0xFF604B78), Color(0xFFE2D4F5)),
    Blue("Синий", Color(0xFF315C96), Color(0xFFADC7FA)),
    Mint("Мятный", Color(0xFF006B59), Color(0xFF94D8C7)),
    Rose("Розовый", Color(0xFF92436C), Color(0xFFF9B5D9)),
    Gold("Янтарный", Color(0xFF83551C), Color(0xFFF8C487)),
}

internal class AppearanceSettings(private val context: Context) {
    private val prefs = context.getSharedPreferences("appearance", Context.MODE_PRIVATE)
    private val image = AtomicFile(File(context.filesDir, "slynor-wallpaper.jpg"))
    var accent by mutableStateOf(ThemeAccent.entries.firstOrNull { it.name == prefs.getString("accent", null) } ?: ThemeAccent.Lilac)
        private set
    var wallpaper by mutableStateOf<Bitmap?>(null)
        private set

    fun updateAccent(value: ThemeAccent) {
        accent = value
        prefs.edit().putString("accent", value.name).apply()
    }

    suspend fun loadWallpaper() {
        wallpaper = withContext(Dispatchers.IO) {
            runCatching { image.openRead().use { BitmapFactory.decodeStream(it) } }.getOrNull()
        }
    }

    suspend fun updateWallpaper(uri: Uri?): Boolean {
        val result = withContext(Dispatchers.IO) {
            runCatching {
                if (uri == null) {
                    image.delete()
                    return@runCatching null
                }
                val resolver = context.contentResolver
                val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, options) }
                if (options.outWidth <= 0 || options.outHeight <= 0) throw IOException("Invalid image")
                var sample = 1
                while (maxOf(options.outWidth, options.outHeight) / sample > 1600) sample *= 2
                val bitmap = resolver.openInputStream(uri)?.use {
                    BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inSampleSize = sample })
                } ?: throw IOException("Unreadable image")
                val out = image.startWrite()
                try {
                    if (!bitmap.compress(Bitmap.CompressFormat.JPEG, 86, out)) throw IOException("Could not save image")
                    image.finishWrite(out)
                } catch (error: Exception) {
                    image.failWrite(out)
                    throw error
                }
                bitmap
            }
        }
        if (result.isFailure) return false
        wallpaper = result.getOrNull()
        return true
    }
}
