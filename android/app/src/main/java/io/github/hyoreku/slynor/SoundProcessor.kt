package io.github.hyoreku.slynor

import android.content.Context
import androidx.media3.common.C
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.BaseAudioProcessor
import androidx.media3.common.util.UnstableApi
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.log10
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONArray
import org.json.JSONObject

internal val equalizerBands = listOf(60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000)
internal data class Equalizer(val enabled: Boolean = false, val preamp: Int = 0, val gains: List<Int> = List(10) { 0 })
internal data class SoundPreset(val name: String, val value: Equalizer)

internal val factoryPresets = listOf(
    SoundPreset("Ровный", Equalizer(true)),
    SoundPreset("Бас", Equalizer(true, -6, listOf(6, 4, 2, 0, 0, 0, 0, 0, 0, 0))),
    SoundPreset("Вокал", Equalizer(true, -4, listOf(-3, -2, 0, 2, 3, 4, 2, 0, -1, -2))),
    SoundPreset("Рок", Equalizer(true, -5, listOf(4, 3, 1, -1, -2, 1, 2, 3, 4, 3))),
    SoundPreset("Мягкий", Equalizer(true, -2, listOf(2, 1, 0, 0, 0, 0, -1, -2, -3, -4))),
)

internal enum class VisualizerStyle(val label: String) {
    Off("Выключен"), Bars("Столбцы"), Line("Линия"),
}

internal object SoundState {
    val equalizer = MutableStateFlow(Equalizer())
    val visualizer = MutableStateFlow(VisualizerStyle.Off)
    val spectrum = MutableStateFlow(FloatArray(equalizerBands.size))
}

internal class SoundSettings(context: Context) {
    private val prefs = context.playbackStorage()

    fun restore() {
        SoundState.equalizer.value = decode(prefs.getString("equalizer", null))
        SoundState.visualizer.value = VisualizerStyle.entries.firstOrNull { it.name == prefs.getString("visualizer", null) }
            ?: VisualizerStyle.Off
    }

    fun setEqualizer(value: Equalizer) {
        SoundState.equalizer.value = value
        prefs.edit().putString("equalizer", value.encode().toString()).apply()
    }

    fun setVisualizer(value: VisualizerStyle) {
        SoundState.visualizer.value = value
        prefs.edit().putString("visualizer", value.name).apply()
    }

    fun presets(): List<SoundPreset> = runCatching {
        val items = JSONArray(prefs.getString("equalizerPresets", "[]"))
        List(items.length()) { i ->
            val item = items.getJSONObject(i)
            SoundPreset(item.getString("name"), decode(item.getJSONObject("value").toString()))
        }
    }.getOrDefault(emptyList())

    fun savePresets(values: List<SoundPreset>) {
        val items = JSONArray()
        values.forEach { items.put(JSONObject().put("name", it.name).put("value", it.value.encode())) }
        prefs.edit().putString("equalizerPresets", items.toString()).apply()
    }

    private fun Equalizer.encode() = JSONObject().put("enabled", enabled).put("preamp", preamp).put("bands", JSONArray(gains))

    private fun decode(text: String?): Equalizer = runCatching {
        val data = JSONObject(text ?: "{}")
        val levels = data.optJSONArray("bands")
        Equalizer(data.optBoolean("enabled"), data.optInt("preamp").coerceIn(-20, 20),
            equalizerBands.indices.map { levels?.optInt(it)?.coerceIn(-20, 20) ?: 0 })
    }.getOrDefault(Equalizer())
}

@androidx.annotation.OptIn(UnstableApi::class)
internal class SoundProcessor : BaseAudioProcessor() {
    private var applied: Equalizer? = null
    private var filters = emptyList<Peak>()
    private var gain = 1f
    private var meter = SpectrumMeter(1)

    override fun onConfigure(inputAudioFormat: AudioProcessor.AudioFormat): AudioProcessor.AudioFormat {
        if (inputAudioFormat.encoding != C.ENCODING_PCM_16BIT) return AudioProcessor.AudioFormat.NOT_SET
        return inputAudioFormat
    }

    override fun onFlush(streamMetadata: AudioProcessor.StreamMetadata) {
        applied = null
        if (inputAudioFormat.sampleRate > 0) meter = SpectrumMeter(inputAudioFormat.sampleRate)
        SoundState.spectrum.value = FloatArray(equalizerBands.size)
    }

    override fun onReset() {
        applied = null
        filters = emptyList()
    }

    override fun queueInput(inputBuffer: ByteBuffer) {
        if (!inputBuffer.hasRemaining()) return
        val config = SoundState.equalizer.value
        val channels = inputAudioFormat.channelCount
        if (applied != config) {
            val rate = inputAudioFormat.sampleRate
            filters = if (config.enabled) equalizerBands.mapIndexedNotNull { i, frequency ->
                config.gains[i].takeIf { it != 0 && frequency < rate * .47 }
                    ?.let { Peak(frequency, it, rate, channels) }
            } else emptyList()
            gain = if (config.enabled) 10.0.pow(config.preamp / 20.0).toFloat() else 1f
            applied = config
        }
        val measure = SoundState.spectrum.subscriptionCount.value > 0
        val output = replaceOutputBuffer(inputBuffer.remaining()).order(ByteOrder.nativeOrder())
        if (filters.isEmpty() && gain == 1f && !measure) {
            output.put(inputBuffer)
        } else {
            val input = inputBuffer.order(ByteOrder.nativeOrder())
            while (input.remaining() >= channels * 2) {
                var mono = 0f
                for (channel in 0 until channels) {
                    var value = input.short * (gain / 32768f)
                    for (filter in filters) value = filter.apply(value, channel)
                    val clipped = value.coerceIn(-1f, 32767f / 32768)
                    output.putShort((clipped * 32768f).roundToInt().toShort())
                    mono += clipped
                }
                if (measure) meter.add(mono / channels)
            }
            if (input.hasRemaining()) output.put(input)
        }
        output.flip()
    }
}

private class Peak(frequency: Int, gain: Int, rate: Int, channels: Int) {
    private val amplitude = 10.0.pow(gain / 40.0)
    private val angle = 2 * PI * frequency / rate
    private val alpha = sin(angle) / 2.8
    private val denominator = 1 + alpha / amplitude
    private val b0 = ((1 + alpha * amplitude) / denominator).toFloat()
    private val b1 = (-2 * cos(angle) / denominator).toFloat()
    private val b2 = ((1 - alpha * amplitude) / denominator).toFloat()
    private val a2 = ((1 - alpha / amplitude) / denominator).toFloat()
    private val first = FloatArray(channels)
    private val second = FloatArray(channels)

    fun apply(sample: Float, channel: Int): Float {
        val result = b0 * sample + first[channel]
        first[channel] = b1 * (sample - result) + second[channel]
        second[channel] = b2 * sample - a2 * result
        return result
    }
}

private class SpectrumMeter(rate: Int) {
    private val coefficients = equalizerBands.map { (2 * cos(2 * PI * it / rate)).toFloat() }.toFloatArray()
    private val valid = equalizerBands.map { it < rate * .47 }
    private val previous = FloatArray(equalizerBands.size)
    private val older = FloatArray(equalizerBands.size)
    private val window = FloatArray(2048) { (.5 - .5 * cos(2 * PI * it / 2047)).toFloat() }
    private var count = 0

    fun add(sample: Float) {
        for (i in coefficients.indices) {
            val next = sample * window[count] + coefficients[i] * previous[i] - older[i]
            older[i] = previous[i]
            previous[i] = next
        }
        count++
        if (count == window.size) {
            SoundState.spectrum.value = FloatArray(coefficients.size) { i ->
                if (!valid[i]) 0f else {
                    val energy = previous[i] * previous[i] + older[i] * older[i] - coefficients[i] * previous[i] * older[i]
                    val amplitude = sqrt(energy.coerceAtLeast(0f)) / (window.size / 4)
                    ((20 * log10(amplitude.coerceAtLeast(.001f)) + 60) / 60).coerceIn(0f, 1f)
                }
            }
            previous.fill(0f)
            older.fill(0f)
            count = 0
        }
    }
}
