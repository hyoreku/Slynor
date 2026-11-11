"""Content-addressed asset store with visible limits.

A downloaded file becomes an AudioAsset only after it is measured: the SHA-256
names the stored copy, so identical bytes are never kept twice. Every limit is
reported with real numbers, and nothing is silently trimmed or evicted. The
stored path is relative to the store root, so the data directory can move
without rewriting the database.
"""
from pathlib import Path, PurePosixPath
import os
import shutil
from uuid import uuid4

from .errors import LimitError, ProviderError
from .media import AUDIO_EXTENSIONS, measure
from .models import AudioAsset

DEFAULT_MAX_FILE_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


class AssetStore:
    def __init__(self, root, *, max_file_bytes=DEFAULT_MAX_FILE_BYTES,
                 max_total_bytes=DEFAULT_MAX_TOTAL_BYTES):
        self.root = Path(root)
        self.assets = self.root / 'assets'
        self.work = self.root / 'work'
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.max_total_bytes = max(1, int(max_total_bytes))
        self.work.mkdir(parents=True, exist_ok=True)

    def new_workdir(self):
        """A private folder for one download; the caller removes it afterwards."""
        path = self.work / uuid4().hex
        path.mkdir(parents=True, exist_ok=False)
        return path

    def usage(self):
        total = 0
        for path in self.assets.rglob('*'):
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        return total

    def limits(self):
        return {'max_file_bytes': self.max_file_bytes,
                'max_total_bytes': self.max_total_bytes,
                'used_bytes': self.usage()}

    def relative_path(self, facts, container=None):
        container = container or facts.container
        return 'assets/' + facts.sha256[:2] + '/' + facts.sha256 + '.' + container

    def store(self, source, *, origin=None, candidate_id=None):
        """Measure, then publish one file under the store; returns an unsaved asset."""
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise ProviderError('Файл для сохранения не найден.', code='asset_missing')
        size = source.stat().st_size
        if size <= 0:
            raise ProviderError('Пустой файл не сохраняется.', code='asset_empty')
        if size > self.max_file_bytes:
            raise LimitError('Файл ' + str(size) + ' байт больше предела '
                             + str(self.max_file_bytes) + ' байт.', code='file_too_large')
        try:
            facts = measure(source)
        except ValueError:
            raise ProviderError('Файл не является поддерживаемым аудио.',
                                code='unsupported_media') from None
        if facts.container not in {value[1:] for value in AUDIO_EXTENSIONS}:
            raise ProviderError('Неподдерживаемый контейнер аудио.', code='unsupported_container')
        relative = self.relative_path(facts)
        target = self.root / Path(*PurePosixPath(relative).parts)
        if target.exists():
            if target.stat().st_size != facts.size_bytes:
                # The stored copy does not match its own hash: replace the damaged bytes.
                self._move(source, target)
        else:
            used = self.usage()
            if used + facts.size_bytes > self.max_total_bytes:
                raise LimitError('Хранилище занято ' + str(used) + ' из ' + str(self.max_total_bytes)
                                 + ' байт; файл ' + str(facts.size_bytes) + ' байт не помещается.',
                                 code='store_full')
            target.parent.mkdir(parents=True, exist_ok=True)
            self._move(source, target)
        if not target.is_file() or target.stat().st_size != facts.size_bytes:
            raise ProviderError('Сохранённый файл не совпал по размеру.', code='asset_write_failed')
        return AudioAsset(candidate_id=candidate_id, container=facts.container, codec=facts.codec,
                          sha256=facts.sha256, size_bytes=facts.size_bytes,
                          duration_ms=facts.duration_ms, origin=origin, path=relative)

    def resolve(self, asset):
        """Absolute path of a stored asset; never leaves the store root."""
        relative = PurePosixPath(str(asset.path))
        if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                or any(part.endswith(':') for part in relative.parts)):
            raise ProviderError('Путь записи некорректен.', code='unsafe_asset_path')
        path = self.root / Path(*relative.parts)
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            raise ProviderError('Файл записи отсутствует.', code='asset_missing') from None
        if resolved.is_symlink() or not resolved.is_file():
            raise ProviderError('Файл записи недоступен.', code='asset_missing')
        try:
            resolved.relative_to(self.root.resolve())
        except ValueError:
            raise ProviderError('Файл записи вне хранилища.', code='unsafe_asset_path') from None
        return resolved

    @staticmethod
    def _move(source, target):
        try:
            os.replace(source, target)
            return
        except OSError:
            pass
        temporary = target.with_name(target.name + '.' + uuid4().hex + '.part')
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
            source.unlink()
        finally:
            temporary.unlink(missing_ok=True)
