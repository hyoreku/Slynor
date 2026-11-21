"""Publish the player snapshot on a worker independent of the WebView."""

import logging
import math
import re
import time
from threading import Event, Lock, Thread
from backend.discord_artwork import public_cover_url
from backend.discord_rpc import DiscordRpc, DiscordRpcRejected
from player.playback_snapshot import PlaybackSnapshot


logger = logging.getLogger("ownplayer.discord")
# Public Application ID of the Slynor registration in the Discord Developer Portal.
DISCORD_APPLICATION_ID = "1573407288759296051"
_ACTIVE_STATES = {"playing", "paused"}
_POLL_INTERVAL = .12
_UPDATE_INTERVAL = 5.0
_RECONNECT_INTERVAL = 2.0
_METADATA_RETRY_INTERVAL = 2.0
_PRIVATE_TEXT = re.compile(r"://|\b(?:file|online):|[a-z]:[\\/]|\\\\|(?:^|\s)/\S", re.IGNORECASE)


def _presence_text(value, limit=128):
    if not isinstance(value, str) or _PRIVATE_TEXT.search(value) or "\\" in value:
        return None
    text = " ".join(value.split())
    # The RPC client limits details/state to 128 bytes and rejects a single byte.
    text = text.encode("utf-8", errors="ignore")[:limit].decode("utf-8", errors="ignore")
    return text if len(text.encode("utf-8")) >= 2 else None


def _elapsed(milliseconds):
    minutes, seconds = divmod(max(0, milliseconds) // 1000, 60)
    return f"{minutes:02}:{seconds:02}"


def listening_activity(snapshot: PlaybackSnapshot, metadata: dict, now: float, logo=None):
    title = metadata.get("title") or snapshot.get("filename")
    activity = {"type": 2, "details": _presence_text(title) or "Slynor"}
    cover = public_cover_url(metadata.get("public_cover_url"))
    if cover:
        activity["assets"] = {"large_image": cover, "large_text": "Обложка трека"}
        if logo:
            activity["assets"].update(small_image=logo, small_text="Slynor")
    elif logo:
        activity["assets"] = {"large_image": logo, "large_text": "Slynor"}
    position, duration, rate = snapshot["time_ms"], snapshot["duration_ms"], snapshot["rate"]
    if snapshot["state"] == "paused":
        elapsed = _elapsed(min(position, duration) if duration > 0 else position)
        progress = f"{elapsed} / {_elapsed(duration)}" if duration > 0 else elapsed
        suffix = f"На паузе · {progress}"
        artist = _presence_text(metadata.get("artist"), 128 - len((" · " + suffix).encode("utf-8")))
        activity["state"] = f"{artist} · {suffix}" if artist else suffix
        return activity

    artist = _presence_text(metadata.get("artist"))
    if artist:
        activity["state"] = artist
    if snapshot["state"] == "playing" and 0 <= position < duration and math.isfinite(rate) and rate > 0:
        activity["timestamps"] = {
            "start": int(now - position / (1000 * rate)),
            "end": math.ceil(now + (duration - position) / (1000 * rate)),
        }
    return activity


def _same_activity(previous, current):
    old = previous.get("timestamps", {})
    new = current.get("timestamps", {})
    return (
        previous.get("details") == current.get("details")
        and previous.get("state") == current.get("state")
        and previous.get("assets") == current.get("assets")
        and old.keys() == new.keys()
        and all(abs(old[key] - new[key]) < 2 for key in old)
    )


class LatestTrackMetadata:
    """Keep tag reads off the IPC thread and coalesce pending paths."""

    def __init__(self, read, ready):
        self._read = read
        self._ready = ready
        self._lock = Lock()
        self._wake = Event()
        self._path = None
        self._request = None
        self._data = None
        self._retry = 0
        self._closed = False
        Thread(target=self._run, name="discord-track-tags", daemon=True).start()

    def lookup(self, path):
        with self._lock:
            if self._path != path:
                self._path, self._data, self._retry = path, None, 0
            if self._request != path and time.monotonic() >= self._retry:
                self._request = path
                self._wake.set()
            return self._data

    def close(self):
        with self._lock:
            self._closed = True
        self._wake.set()

    def _run(self):
        while self._wake.wait():
            with self._lock:
                self._wake.clear()
                if self._closed:
                    return
                path = self._request
            if path is None:
                continue
            data = None
            try:
                data = self._read(path)
            except Exception as error:
                logger.debug("Discord track metadata unavailable (%s)", type(error).__name__)
            with self._lock:
                if self._closed:
                    return
                if path != self._request:
                    continue
                self._data = data
                self._request = None
                self._retry = time.monotonic() + _METADATA_RETRY_INTERVAL
            self._ready()


class DiscordPresence:
    def __init__(self, playback, metadata, connect=DiscordRpc, *, artwork=None):
        self._playback = playback
        self._metadata = metadata
        self._connect = connect
        self._artwork = artwork
        self._rejected_covers = set()
        self._rejected_logo = None
        self._lock = Lock()
        self._wake = Event()
        self._stopping = Event()
        self._enabled = False
        self._session_id = None
        self._rpc = None
        self._published = None
        self._track = None
        self._track_metadata = {}
        self._metadata_retry = 0.0
        self._next_connect = 0.0
        self._next_update = 0.0
        self._retry_update = 0.0
        self._rate_backoff = 1.0
        self._metadata_reader = None
        self._thread = Thread(target=self._run, name="discord-presence", daemon=True)

    def start(self):
        self._metadata_reader = LatestTrackMetadata(self._metadata, self.refresh)
        self._thread.start()

    def refresh(self):
        self._wake.set()

    def configure(self, enabled):
        with self._lock:
            self._enabled = enabled is True
        self._wake.set()

    def _configured_id(self):
        with self._lock:
            if self._enabled and not self._stopping.is_set():
                return DISCORD_APPLICATION_ID
            return None

    def close(self):
        self._stopping.set()
        if self._metadata_reader is not None:
            self._metadata_reader.close()
        self._wake.set()
        self._thread.join(timeout=4)

    def _disconnect(self, clear):
        rpc, self._rpc = self._rpc, None
        published, self._published = self._published, None
        if rpc is None:
            return
        try:
            if clear and published is not None:
                rpc.set_activity(None)
        except (OSError, ValueError):
            pass
        finally:
            rpc.close()

    def _cover(self, metadata, duration):
        known = public_cover_url(metadata.get("public_cover_url"))
        if self._artwork is None:
            return known if known not in self._rejected_covers else None
        extras = {key: metadata[key] for key in ("source", "permalink_url", "isrc") if metadata.get(key)}
        extras["allow_apple"] = True
        if known and known not in self._rejected_covers:
            extras["preferred_url"] = known
        arguments = (metadata.get("title"), metadata.get("artist"), metadata.get("album"),
                     duration or metadata.get("duration_ms"))
        found = public_cover_url(self._artwork.lookup(*arguments, **extras))
        if found and found not in self._rejected_covers:
            return found
        if found and ("permalink_url" in extras or "preferred_url" in extras):
            # The public preview can be rejected even when the underlying song has other artwork.
            extras.pop("permalink_url", None)
            extras.pop("preferred_url", None)
            found = public_cover_url(self._artwork.lookup(*arguments, **extras))
        return found if found not in self._rejected_covers else None

    def _sync(self, application_id, now):
        if application_id != self._session_id:
            self._disconnect(clear=True)
            self._session_id = application_id
            self._next_connect = self._next_update = self._retry_update = 0.0
            self._rate_backoff = 1.0
        if application_id is None:
            self._track, self._track_metadata = None, {}
            return

        snapshot = self._playback()
        session = (snapshot["file_path"], snapshot.get("playback_generation"))
        states = _ACTIVE_STATES
        if self._published:
            if (snapshot["state"] in {"opening", "buffering"} and self._published[0][:2] == session
                    and self._published[0][2] in _ACTIVE_STATES):
                # Keep the clock for the same track when VLC buffers during a seek.
                self._rpc.poll()
                return
            states = _ACTIVE_STATES | {"opening", "buffering"}
        path = snapshot["file_path"] if snapshot["state"] in states else None
        if not path:
            if self._published is not None:
                try:
                    self._rpc.set_activity(None)
                except (OSError, ValueError):
                    self._disconnect(clear=False)
                self._published = None
                self._next_connect = 0.0
            self._next_update = 0.0
            return
        if self._rpc is not None:
            self._rpc.poll()
        elif now >= self._next_connect:
            self._rpc = self._connect(application_id)
        else:
            return
        if now < self._retry_update:
            return
        if path != self._track:
            self._track, self._track_metadata, self._metadata_retry = path, {}, 0.0
            self._rejected_covers.clear()
        if not self._track_metadata.get("public_cover_url") and (
                self._metadata_reader is not None or now >= self._metadata_retry):
            if self._metadata_reader is None:
                self._metadata_retry = now + _METADATA_RETRY_INTERVAL
                metadata = self._metadata(path)
            else:
                metadata = self._metadata_reader.lookup(path)
            if metadata:
                self._track_metadata = dict(metadata)

        # Refresh position after opening the pipe or reading tags. A quick stop
        # and replay has a new generation even if both snapshots say playing.
        snapshot = self._playback()
        if (self._configured_id() != application_id or snapshot["state"] not in states
                or (snapshot["file_path"], snapshot.get("playback_generation")) != session):
            self.refresh()
            return
        metadata = self._track_metadata
        cover = self._cover(metadata, snapshot["duration_ms"])
        logo = self._artwork.logo() if self._artwork is not None else None
        if logo == self._rejected_logo:
            logo = None
        removed_logo = None
        while True:
            snapshot = self._playback()
            if (self._configured_id() != application_id or snapshot["state"] not in states
                    or (snapshot["file_path"], snapshot.get("playback_generation")) != session):
                self.refresh()
                return
            identity = (*session, snapshot["state"], snapshot["rate"], snapshot.get("seek_revision", 0))
            activity = listening_activity(snapshot, {**metadata, "public_cover_url": cover}, time.time(), logo)
            if self._published and self._published[0] == identity:
                previous = self._published[1]
                same_display = all(previous.get(key) == activity.get(key) for key in ("details", "state", "assets"))
                if same_display and (now < self._next_update or _same_activity(previous, activity)):
                    if removed_logo:
                        self._rejected_logo = removed_logo
                    return
            try:
                self._rpc.set_activity(activity)
                self._rate_backoff = 1.0
                if removed_logo:
                    self._rejected_logo = removed_logo
                break
            except DiscordRpcRejected as error:
                if error.rate_limited:
                    self._retry_update = time.monotonic() + (error.retry_after or self._rate_backoff)
                    self._rate_backoff = min(5.0, self._rate_backoff * 2)
                    return
                if cover and logo:
                    removed_logo, logo = logo, None
                elif cover:
                    self._rejected_covers.add(cover)
                    cover = self._cover(metadata, snapshot["duration_ms"])
                    logo, removed_logo = removed_logo, None
                elif logo:
                    self._rejected_logo, logo = logo, None
                else:
                    raise
        self._published = (identity, activity)
        self._next_update = time.monotonic() + _UPDATE_INTERVAL

    def _run(self):
        try:
            while not self._stopping.is_set():
                self._wake.clear()
                application_id = self._configured_id()
                try:
                    self._sync(application_id, time.monotonic())
                except (OSError, ValueError):
                    self._disconnect(clear=False)
                    self._next_connect = time.monotonic() + _RECONNECT_INTERVAL
                except Exception as error:
                    # A player/metadata failure must not stop playback or leak track data in logs.
                    logger.warning("Discord presence update failed (%s)", type(error).__name__)
                    self._disconnect(clear=True)
                    self._next_connect = time.monotonic() + _RECONNECT_INTERVAL
                if not self._stopping.is_set():
                    self._wake.wait(_POLL_INTERVAL if application_id else None)
        finally:
            self._disconnect(clear=True)
