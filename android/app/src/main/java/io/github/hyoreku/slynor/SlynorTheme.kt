package io.github.hyoreku.slynor

import android.content.SharedPreferences
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

private val graphite = darkColorScheme(
    primary = Color(0xFFE2D4F5),
    onPrimary = Color(0xFF241A31),
    primaryContainer = Color(0xFF30283B),
    onPrimaryContainer = Color(0xFFF0E4FF),
    secondary = Color(0xFFD3C7DE),
    onSecondary = Color(0xFF271D30),
    background = Color(0xFF100E14),
    onBackground = Color(0xFFF5F0FA),
    surface = Color(0xFF1A171F),
    surfaceContainer = Color(0xFF201C26),
    surfaceContainerHighest = Color(0xFF2C2633),
    surfaceVariant = Color(0xFF302A37),
    onSurface = Color(0xFFF5F0FA),
    onSurfaceVariant = Color(0xFFC1B7CB),
    outline = Color(0xFF8F839C),
    outlineVariant = Color(0xFF403749),
    error = Color(0xFFFFB8C6),
)

private val defaults = Typography()
private val typography = Typography(
    headlineLarge = defaults.headlineLarge.copy(fontSize = 34.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-1).sp),
    headlineMedium = defaults.headlineMedium.copy(fontWeight = FontWeight.SemiBold, letterSpacing = (-.6).sp),
    headlineSmall = defaults.headlineSmall.copy(fontWeight = FontWeight.SemiBold, letterSpacing = (-.35).sp),
    titleLarge = defaults.titleLarge.copy(fontWeight = FontWeight.SemiBold, letterSpacing = (-.25).sp),
    titleMedium = defaults.titleMedium.copy(fontWeight = FontWeight.SemiBold),
)

@Composable
internal fun SlynorTheme(dark: Boolean, accent: ThemeAccent, content: @Composable () -> Unit) {
    val base = if (dark) graphite else lilac
    val primary = if (dark) accent.dark else accent.light
    val colors = if (accent == ThemeAccent.Lilac) base else base.copy(
        primary = primary,
        onPrimary = if (dark) base.background else base.surface,
        primaryContainer = lerp(base.surfaceContainer, primary, if (dark) .18f else .22f),
        onPrimaryContainer = base.onSurface,
    )
    MaterialTheme(colorScheme = colors, typography = typography, content = content)
}

private val lilac = lightColorScheme(
    primary = Color(0xFF604B78),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFEBDCF7),
    onPrimaryContainer = Color(0xFF30203F),
    background = Color(0xFFFCF7FE),
    onBackground = Color(0xFF201925),
    surface = Color(0xFFFFFBFF),
    surfaceContainer = Color(0xFFF4EDF7),
    surfaceContainerHighest = Color(0xFFEDE4F1),
    surfaceVariant = Color(0xFFECE2EF),
    onSurface = Color(0xFF201925),
    onSurfaceVariant = Color(0xFF504657),
    outline = Color(0xFF82748A),
    outlineVariant = Color(0xFFD9CEDF),
)

internal enum class ThemeMode(val title: String) {
    System("Как в системе"),
    Dark("Тёмная"),
    Light("Светлая");

    companion object {
        fun read(storage: SharedPreferences): ThemeMode =
            entries.firstOrNull { it.name == storage.getString("theme", null) } ?: Dark
    }
}
