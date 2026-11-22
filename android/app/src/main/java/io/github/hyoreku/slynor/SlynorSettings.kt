package io.github.hyoreku.slynor

import android.os.Bundle
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.media3.common.Player
import androidx.media3.session.MediaController

@Composable
internal fun SettingsScreen(
    theme: ThemeMode, onTheme: (ThemeMode) -> Unit, player: MediaController?, state: Playback, remaining: Long,
) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp)) {
        ScreenTitle("Настройки")
        SettingsRow("Тема") {
            ChoiceMenu(theme.title, ThemeMode.entries, ThemeMode::title, onChoice = onTheme)
        }
        SettingsRow("Скорость") {
            SpeedMenu(player, state.speed)
        }
        SettingsRow("Повтор") {
            RepeatMenu(player, state.repeat)
        }
        SettingsRow("Таймер сна") {
            SleepMenu(player, remaining)
        }
    }
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
internal fun SleepMenu(player: MediaController?, remaining: Long, compact: Boolean = false) {
    val minutes = listOf(0, 5, 10, 15, 30, 45, 60)
    val enabled = player?.isSessionCommandAvailable(sleepTimerCommand) == true
    val label = if (remaining > 0) formatTime(remaining + 999) else if (compact) "Таймер сна" else "Выключен"
    ChoiceMenu(label, minutes, { if (it == 0) "Выключить таймер" else "$it мин" }, enabled = enabled, compact = compact) {
        val args = Bundle().apply { putInt(sleepTimerMinutes, it) }
        player?.sendCustomCommand(sleepTimerCommand, args)
    }
}

@Composable
private fun <T> ChoiceMenu(
    current: String, options: List<T>, label: (T) -> String, enabled: Boolean = true,
    compact: Boolean = false, onChoice: (T) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        if (compact) TextButton(onClick = { expanded = true }, enabled = enabled) { Text(current) }
        else FilledTonalButton(onClick = { expanded = true }, enabled = enabled) { Text(current) }
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
