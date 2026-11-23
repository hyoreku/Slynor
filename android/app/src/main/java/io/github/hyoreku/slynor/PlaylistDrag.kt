package io.github.hyoreku.slynor

import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import kotlin.math.abs

internal class PlaylistDrag(private val list: LazyListState, private val commit: (String, String) -> Unit) {
    var activeId by mutableStateOf<String?>(null)
        private set
    var order by mutableStateOf<List<String>?>(null)
        private set
    private var original = emptyList<String>()
    private var top by mutableFloatStateOf(0f)
    private var height = 0

    fun start(id: String, ids: List<String>) {
        val row = list.layoutInfo.visibleItemsInfo.firstOrNull { it.key == id } ?: return
        original = ids
        top = row.offset.toFloat()
        height = row.size
        order = ids
        activeId = id
    }

    fun move(delta: Float) {
        val id = activeId ?: return
        val ids = order ?: return
        top += delta
        val center = top + height / 2f
        val closest = list.layoutInfo.visibleItemsInfo.asSequence()
            .filter { (it.key as? String) in ids }
            .minByOrNull { abs(it.offset + it.size / 2f - center) } ?: return
        if (closest.key == id) return
        val from = ids.indexOf(id)
        val to = ids.indexOf(closest.key as String)
        order = ids.toMutableList().apply { add(to, removeAt(from)) }
    }

    fun scrollStep(): Float {
        val layout = list.layoutInfo
        val center = top + height / 2f
        val edge = height / 2f
        return when {
            center < layout.viewportStartOffset + edge -> -12f
            center > layout.viewportEndOffset - edge -> 12f
            else -> 0f
        }
    }

    fun finish() {
        val id = activeId ?: return
        val index = order?.indexOf(id) ?: -1
        val anchor = original.getOrNull(index)
        cancel()
        if (anchor != null && id != anchor) commit(id, anchor)
    }

    fun cancel() {
        activeId = null
        order = null
    }

    fun rowModifier(id: String, modifier: Modifier): Modifier {
        if (activeId != id) return modifier
        return modifier.zIndex(1f).graphicsLayer {
            val offset = list.layoutInfo.visibleItemsInfo.firstOrNull { it.key == id }?.offset
            translationY = if (offset == null) 0f else top - offset
            shadowElevation = 10.dp.toPx()
        }
    }
}

@Composable
internal fun rememberPlaylistDrag(list: LazyListState, commit: (String, String) -> Unit): PlaylistDrag {
    val finish by rememberUpdatedState(commit)
    val state = remember(list) { PlaylistDrag(list) { from, anchor -> finish(from, anchor) } }
    LaunchedEffect(state.activeId) {
        while (state.activeId != null) {
            withFrameNanos { }
            val step = state.scrollStep()
            if (step != 0f && list.scrollBy(step) != 0f) state.move(0f)
        }
    }
    return state
}

@Composable
internal fun PlaylistDragHandle(track: LocalTrack, ids: List<String>, state: PlaylistDrag) {
    val items by rememberUpdatedState(ids)
    Box(
        Modifier.size(40.dp).semantics(mergeDescendants = true) {
            contentDescription = "Перетащить ${track.title}"
        }.pointerInput(track.id, state) {
            detectVerticalDragGestures(
                onDragStart = { state.start(track.id, items) },
                onDragCancel = state::cancel,
                onDragEnd = state::finish,
            ) { change, amount ->
                change.consume()
                state.move(amount)
            }
        }, contentAlignment = Alignment.Center,
    ) { Glyph(R.drawable.ic_reorder, null) }
}
