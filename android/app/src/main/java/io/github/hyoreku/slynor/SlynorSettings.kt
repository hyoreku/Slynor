package io.github.hyoreku.slynor

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.Surface
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
internal fun SettingsScreen(
    theme: ThemeMode, onTheme: (ThemeMode) -> Unit, appearance: AppearanceSettings, library: LocalLibrary,
) {
    val context = LocalContext.current
    val sound = remember(context) { SoundSettings(context.applicationContext) }
    val visualizer by SoundState.visualizer.collectAsState()
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenTitle("Настройки")
        SettingsGroup("Тема и цвет") {
            SettingsRow("Тема приложения") {
                ChoiceMenu(theme.title, ThemeMode.entries, ThemeMode::title, onChoice = onTheme)
            }
            SettingsRow("Цвет акцента") {
                ChoiceMenu(appearance.accent.label, ThemeAccent.entries, ThemeAccent::label, onChoice = appearance::updateAccent)
            }
            WallpaperSettings(appearance)
        }
        SettingsGroup("Сейчас играет") {
            SettingsRow("Визуализатор") {
                ChoiceMenu(visualizer.label, VisualizerStyle.entries, VisualizerStyle::label, onChoice = sound::setVisualizer)
            }
        }
        SettingsGroup("Резервная копия") {
            BackupControls(library) {
                onTheme(ThemeMode.read(library.storage))
                appearance.reload()
                sound.restore()
            }
        }
    }
}

@Composable
private fun BackupControls(library: LocalLibrary, onRestore: suspend () -> Unit) {
    val context = LocalContext.current
    val backup = remember(context, library) { AndroidBackup(context.applicationContext, library) }
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var status by rememberSaveable { mutableStateOf<String?>(null) }
    var source by rememberSaveable { mutableStateOf<String?>(null) }
    val export = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        if (uri != null) {
            busy = true
            scope.launch {
                val result = withContext(Dispatchers.IO) { runCatching { backup.write(uri) } }
                status = if (result.isSuccess) "Резервная копия сохранена." else "Не удалось сохранить копию."
                busy = false
            }
        }
    }
    val importPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        source = uri?.toString()
    }
    Text(
        "Плейлисты, обложки, избранное и настройки. При переносе сначала добавьте аудиофайлы в библиотеку.",
        color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium,
    )
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(top = 8.dp)) {
        FilledTonalButton(onClick = { export.launch("Slynor-backup.zip") }, enabled = !busy) { Text("Сохранить копию") }
        TextButton(onClick = { importPicker.launch(arrayOf("application/zip", "application/octet-stream")) }, enabled = !busy) {
            Text("Восстановить")
        }
    }
    if (busy) Text("Обработка резервной копии…")
    status?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
    source?.let { uri ->
        AlertDialog(
            onDismissRequest = { source = null },
            title = { Text("Восстановить из копии?") },
            text = { Text("Плейлисты из копии и настройки будут обновлены.") },
            confirmButton = {
                TextButton(onClick = {
                    source = null
                    busy = true
                    scope.launch {
                        val result = withContext(Dispatchers.IO) { runCatching { backup.restore(Uri.parse(uri)) } }
                        status = result.fold(
                            onSuccess = {
                                onRestore()
                                val missing = if (it.unavailable == 0) "" else
                                    " Треков без доступа: ${it.unavailable}. Добавьте файлы и повторите восстановление."
                                "Плейлистов восстановлено: ${it.playlists}.$missing"
                            },
                            onFailure = { "Не удалось восстановить копию. Проверьте файл." },
                        )
                        busy = false
                    }
                }) { Text("Восстановить копию") }
            },
            dismissButton = { TextButton(onClick = { source = null }) { Text("Отмена") } },
        )
    }
}

@Composable
private fun SettingsGroup(title: String, content: @Composable () -> Unit) {
    val colors = MaterialTheme.colorScheme
    Surface(shape = RoundedCornerShape(20.dp), color = colors.surface, border = BorderStroke(1.dp, colors.outlineVariant)) {
        Column(Modifier.fillMaxWidth().padding(16.dp)) {
            Text(title, style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(bottom = 6.dp))
            content()
        }
    }
}

@Composable
private fun WallpaperSettings(appearance: AppearanceSettings) {
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf(false) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            busy = true
            scope.launch {
                try { failed = !appearance.updateWallpaper(uri) } finally { busy = false }
            }
        }
    }
    SettingsRow("Обои") {
        FilledTonalButton(onClick = { picker.launch(arrayOf("image/*")) }, enabled = !busy) {
            Text(if (appearance.wallpaper == null) "Выбрать" else "Заменить")
        }
    }
    if (appearance.wallpaper != null) {
        TextButton(enabled = !busy, onClick = { scope.launch { failed = !appearance.updateWallpaper(null) } }) {
            Text("Убрать обои")
        }
    }
    if (failed) Text("Не удалось сохранить выбранное изображение.", color = MaterialTheme.colorScheme.error)
}

@Composable
private fun SettingsRow(title: String, content: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(title, Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
        content()
    }
}

@Composable
internal fun SpeedMenu(player: MediaController?, speed: Float) {
    ChoiceMenu(speedLabel(speed), playbackSpeeds, ::speedLabel, enabled = player != null) { player?.setPlaybackSpeed(it) }
}

@Composable
internal fun RepeatMenu(player: MediaController?, repeat: Int) {
    val label: (Int) -> String = {
        when (it) {
            Player.REPEAT_MODE_ONE -> "Один трек"
            Player.REPEAT_MODE_ALL -> "Вся очередь"
            else -> "Без повтора"
        }
    }
    ChoiceMenu(label(repeat), listOf(Player.REPEAT_MODE_OFF, Player.REPEAT_MODE_ONE, Player.REPEAT_MODE_ALL),
        label, enabled = player != null) { player?.repeatMode = it }
}

@Composable
private fun <T> ChoiceMenu(
    current: String, options: List<T>, label: (T) -> String, enabled: Boolean = true,
    onChoice: (T) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        FilledTonalButton(onClick = { expanded = true }, enabled = enabled) { Text(current) }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            for (option in options) {
                DropdownMenuItem(text = { Text(label(option)) }, onClick = {
                    expanded = false
                    onChoice(option)
                })
            }
        }
    }
}

private fun speedLabel(speed: Float) = "${speed.toString().removeSuffix(".0").replace('.', ',')}×"
