"""Facts about a downloaded file: hash, size, declared container, measured audio.

A catalogue duration or a file extension is not proof. `measure` reads the file
once for the SHA-256 and asks PyAV for the real duration and codec. When PyAV is
unavailable or the container is broken, the duration stays unknown instead of
being guessed, and the caller decides whether that is acceptable.
"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

AUDIO_EXTENSIONS = frozenset({'.mp3', '.m4a', '.opus', '.ogg', '.webm', '.aac', '.flac', '.wav'})
MAX_MEASURED_MS = 24 * 60 * 60 * 1000
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class MediaFacts:
    """What this exact file is, as opposed to what its catalogue card claims."""

    sha256: str
    size_bytes: int
    container: str
    codec: str | None
    duration_ms: int | None


def file_sha256(path, chunk=CHUNK_BYTES):
    digest = sha256()
    with Path(path).open('rb') as stream:
        while True:
            block = stream.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def probe_audio(path):
    """Return (codec, measured duration ms), using unknown values on failure."""
    try:
        import av
        with av.open(str(path), mode='r', metadata_errors='ignore') as container:
            streams = list(container.streams.audio)
            codec = streams[0].codec_context.name if streams else None
            duration = container.duration
        milliseconds = None
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0:
            value = int(round(duration / 1000))
            if 0 < value <= MAX_MEASURED_MS:
                milliseconds = value
        if not isinstance(codec, str) or not codec:
            codec = None
        return codec, milliseconds
    except Exception:
        return None, None


def measure(path):
    """Measure a regular audio file; raises ValueError for unsupported input."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('media file is missing or a link')
    size = path.stat().st_size
    if size <= 0:
        raise ValueError('media file is empty')
    suffix = path.suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise ValueError('unsupported media container')
    codec, duration_ms = probe_audio(path)
    return MediaFacts(sha256=file_sha256(path), size_bytes=size, container=suffix[1:],
                      codec=codec, duration_ms=duration_ms)
