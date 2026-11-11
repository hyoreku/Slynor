"""Validate downloaded audio by content; a filename alone is never sufficient."""
import math
from pathlib import Path


def validate_audio_file(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise ValueError('Empty or unsafe audio file')
    from mutagen import File
    try:
        audio = File(path)
        length = getattr(getattr(audio, 'info', None), 'length', 0)
        if isinstance(length, (int, float)) and math.isfinite(length) and length > 0:
            return
    except Exception:
        pass
    # Mutagen does not support every WebM/AAC container. PyAV is already a GUI
    # dependency. Decode a bounded prefix, not the entire track or arbitrary URL.
    try:
        import av
        demuxers = {'.webm':'matroska', '.m4a':'mov', '.aac':'aac',
                    '.opus':'ogg', '.ogg':'ogg', '.mp3':'mp3',
                    '.flac':'flac', '.wav':'wav'}
        demuxer = demuxers.get(path.suffix.lower())
        if not demuxer:
            raise ValueError('Unsupported audio container')
        # Do not autodetect a disguised HLS/playlist file and follow remote or
        # local references while validating a download.
        with av.open(str(path), mode='r', format=demuxer,
                     options={'protocol_whitelist':'file,pipe',
                              'probesize':'4194304', 'analyzeduration':'2000000'}) as container:
            streams = list(container.streams.audio)
            if not streams:
                raise ValueError('No audio stream')
            total = 0
            for number, packet in enumerate(container.demux(streams[0])):
                total += packet.size
                if number >= 256 or total > 4 * 1024 * 1024:
                    break
                for frame in packet.decode():
                    if frame.samples > 0:
                        return
    except Exception as exc:
        raise ValueError('Invalid audio content') from exc
    raise ValueError('No decodable audio in bounded probe')
