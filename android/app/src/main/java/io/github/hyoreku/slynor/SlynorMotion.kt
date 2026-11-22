package io.github.hyoreku.slynor

import androidx.compose.animation.ContentTransform
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

internal fun screenMotion(expanding: Boolean = false, collapsing: Boolean = false): ContentTransform {
    val direction = if (collapsing) -1 else 1
    val distance = if (expanding || collapsing) 5 else 22
    return (fadeIn(tween(250, delayMillis = 25)) +
        slideInVertically(tween(300)) { direction * it / distance }) togetherWith
        (fadeOut(tween(145)) + slideOutVertically(tween(240)) { -direction * it / distance })
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun SlynorSheet(
    title: String,
    closeLabel: String,
    onDismiss: () -> Unit,
    content: @Composable ColumnScope.(close: () -> Unit) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    val close: () -> Unit = {
        scope.launch {
            sheetState.hide()
            if (!sheetState.isVisible) onDismiss()
        }
    }
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Row(
            Modifier.fillMaxWidth().padding(start = 20.dp, end = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(title, Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.headlineSmall)
            IconButton(onClick = close) { Glyph(R.drawable.ic_close, closeLabel) }
        }
        content(close)
    }
}
