from typing import Literal, TypedDict


PlaybackStatus = Literal[
    "idle", "opening", "buffering", "playing", "paused", "stopped",
    "ended", "error", "unknown",
]


class PlaybackSnapshot(TypedDict):
    """Current playback. file_path is a local path or a logical online reference."""

    state: PlaybackStatus
    filename: str | None
    file_path: str | None
    playback_generation: int
    time_ms: int
    duration_ms: int
    can_seek: bool
    rate: float
    speed_mode: str
    playback_error: str | None
