package io.github.hyoreku.slynor

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt

@Composable
internal fun EqualizerDialog(onClose: () -> Unit) {
    val context = LocalContext.current
    val storage = remember(context) { SoundSettings(context.applicationContext) }
    val state by SoundState.equalizer.collectAsState()
    var presets by remember { mutableStateOf(storage.presets()) }
    var naming by rememberSaveable { mutableStateOf(false) }
    var presetName by rememberSaveable { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Эквалайзер") },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text("Включён", Modifier.weight(1f))
                    Switch(
                        checked = state.enabled, onCheckedChange = { storage.setEqualizer(state.copy(enabled = it)) },
                        modifier = Modifier.semantics { contentDescription = "Включить эквалайзер" },
                    )
                    TrackMenu("Пресеты эквалайзера") { close ->
                        for (preset in factoryPresets + presets) {
                            DropdownMenuItem(text = { Text(preset.name) }, onClick = {
                                close()
                                storage.setEqualizer(preset.value.copy(enabled = true))
                            })
                        }
                    }
                }
                if (presets.isNotEmpty()) {
                    TrackMenu("Удалить свой пресет") { close ->
                        for (preset in presets) {
                            DropdownMenuItem(text = { Text(preset.name) }, onClick = {
                                close()
                                presets = presets.filterNot { it.name == preset.name }
                                storage.savePresets(presets)
                            })
                        }
                    }
                }
                GainSlider("Предусиление", state.preamp, state.enabled) {
                    storage.setEqualizer(state.copy(preamp = it))
                }
                equalizerBands.forEachIndexed { index, band ->
                    GainSlider(if (band >= 1000) "${band / 1000} кГц" else "$band Гц", state.gains[index], state.enabled) { gain ->
                        val gains = state.gains.toMutableList().apply { this[index] = gain }
                        storage.setEqualizer(state.copy(gains = gains))
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text("Готово") } },
        dismissButton = {
            TextButton(onClick = { presetName = ""; naming = true }, enabled = presets.size < 30) { Text("Сохранить пресет") }
        },
    )

    if (naming) {
        val name = presetName.trim()
        AlertDialog(
            onDismissRequest = { naming = false },
            title = { Text("Новый пресет") },
            text = { OutlinedTextField(presetName, { if (it.length <= 40) presetName = it },
                label = { Text("Название") }, singleLine = true) },
            confirmButton = {
                TextButton(
                    enabled = name.isNotEmpty() && (factoryPresets + presets).none { it.name.equals(name, ignoreCase = true) },
                    onClick = {
                        presets = presets + SoundPreset(name, state.copy(enabled = true))
                        storage.savePresets(presets)
                        naming = false
                    },
                ) { Text("Сохранить") }
            },
            dismissButton = { TextButton(onClick = { naming = false }) { Text("Отмена") } },
        )
    }
}

@Composable
private fun GainSlider(label: String, gain: Int, enabled: Boolean, onChange: (Int) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
        Slider(
            value = gain.toFloat(), onValueChange = { onChange(it.roundToInt()) }, enabled = enabled,
            valueRange = -20f..20f, modifier = Modifier.weight(1.4f).semantics { contentDescription = label },
        )
        Text("${if (gain > 0) "+" else ""}$gain дБ", Modifier.weight(.6f), style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
internal fun SpectrumStrip(playing: Boolean) {
    val style by SoundState.visualizer.collectAsState()
    if (style == VisualizerStyle.Off || !playing) return
    val levels by SoundState.spectrum.collectAsState()
    val color = MaterialTheme.colorScheme.primary
    Canvas(Modifier.fillMaxWidth().height(42.dp).semantics { contentDescription = "Спектр воспроизведения" }) {
        val width = size.width / levels.size
        if (style == VisualizerStyle.Line) {
            val line = Path()
            levels.forEachIndexed { index, level ->
                val x = width * (index + .5f)
                val y = size.height * (1 - level)
                if (index == 0) line.moveTo(x, y) else line.lineTo(x, y)
            }
            drawPath(line, color, style = Stroke(2.dp.toPx()))
        } else {
            levels.forEachIndexed { index, level ->
                val height = (size.height * level).coerceAtLeast(2.dp.toPx())
                drawRoundRect(color, Offset(width * (index + .18f), size.height - height), Size(width * .64f, height),
                    cornerRadius = CornerRadius(3.dp.toPx()))
            }
        }
    }
}
