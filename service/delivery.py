"""Delivery sinks: hand a stored asset to one explicit target.

A sink receives the absolute path of an already measured asset. The Telegram
sink will arrive with the bot; the file outbox is a working local target today
and keeps the same contract, so the delivery job does not change.
"""
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

from .errors import ProviderError

NAME = re.compile(r'[^A-Za-z0-9._-]+')


def safe_name(target, limit=40):
    value = NAME.sub('_', str(target)).strip('._')[:limit]
    return value or 'target'


class FileDeliverySink:
    """Publish a link or copy of the asset into an outbox, with a sidecar record."""

    def __init__(self, outbox):
        self.outbox = Path(outbox)

    def send(self, path, asset, target):
        self.outbox.mkdir(parents=True, exist_ok=True)
        stem = safe_name(target) + '-' + asset.id[:8]
        suffix = Path(path).suffix.lower()
        for attempt in range(1, 100):
            name = stem + ('' if attempt == 1 else '-' + str(attempt)) + suffix
            destination = self.outbox / name
            companion = self.outbox / (name + '.delivery.json')
            if destination.exists() or companion.exists():
                continue
            temporary = self.outbox / (name + '.' + uuid4().hex + '.tmp')
            try:
                try:
                    os.link(path, destination)
                except FileExistsError:
                    continue
                except OSError:
                    with destination.open('xb') as out, Path(path).open('rb') as stream:
                        shutil.copyfileobj(stream, out)
                record = {'version': 1, 'asset_id': asset.id, 'sha256': asset.sha256,
                          'size_bytes': asset.size_bytes, 'duration_ms': asset.duration_ms,
                          'target': target, 'file_name': name}
                temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n',
                                      encoding='utf-8')
                os.replace(temporary, companion)
                return name
            except FileExistsError:
                destination.unlink(missing_ok=True)
                continue
            except OSError:
                destination.unlink(missing_ok=True)
                raise ProviderError('Не удалось доставить файл в папку выдачи.',
                                    code='delivery_failed') from None
            finally:
                temporary.unlink(missing_ok=True)
        raise ProviderError('Слишком много файлов с таким именем в папке выдачи.',
                            code='delivery_name_conflict')
