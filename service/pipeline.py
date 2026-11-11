"""The vertical slice: search -> candidate -> download -> delivery.

Handlers run inside the single dispatcher, so they are synchronous and short.
Every step persists its own record before the next one can use it: a failed job
leaves a Recording, a known AudioCandidate or a measured AudioAsset behind, and
the store publishes files atomically, so there is never a half-written asset.
"""
import shutil

from . import db
from .errors import ProviderError
from .models import (AudioCandidate, Availability, Delivery, DeliveryStatus, JobKind,
                     Recording)
from .providers import ProviderTrack

DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 20


class Pipeline:
    def __init__(self, connection, providers, store, *, sink=None):
        self.connection = connection
        self.providers = dict(providers)
        self.store = store
        self.sink = sink

    def handlers(self):
        return {JobKind.SEARCH: self.search, JobKind.DOWNLOAD: self.download,
                JobKind.DELIVER: self.deliver}

    def provider(self, name):
        if not isinstance(name, str) or name not in self.providers:
            raise ProviderError('Источник не подключён.', code='unknown_provider')
        return self.providers[name]

    def _remember(self, track):
        known = db.find_candidate(self.connection, track.provider, track.provider_id)
        if known is not None:
            recording = db.get_recording(self.connection, known.recording_id)
            if recording is not None:
                return recording, known, False
        recording = Recording(title=track.title,
                              artists=[track.artist] if track.artist else [],
                              duration_ms=track.duration_ms,
                              external_ids={track.provider: track.provider_id},
                              metadata_origin=track.provider)
        db.save_recording(self.connection, recording)
        candidate = AudioCandidate(recording_id=recording.id, provider=track.provider,
                                   provider_id=track.provider_id,
                                   duration_ms=track.duration_ms,
                                   availability=Availability.UNKNOWN)
        db.save_candidate(self.connection, candidate)
        return recording, candidate, True

    def _item(self, track, recording, candidate, created):
        return {'recording_id': recording.id, 'candidate_id': candidate.id,
                'provider': candidate.provider, 'provider_id': candidate.provider_id,
                'title': recording.title, 'artist': ', '.join(recording.artists),
                'duration_ms': (candidate.duration_ms if candidate.duration_ms is not None
                                else recording.duration_ms),
                'availability': candidate.availability.value,
                'cover_url': track.cover_url,
                'public_url': track.public_url, 'created': created}

    def search(self, job):
        payload = job.payload or {}
        provider = self.provider(payload.get('provider', 'youtube'))
        query = payload.get('query')
        limit = payload.get('limit', DEFAULT_SEARCH_LIMIT)
        found = provider.search(query, limit)
        items = []
        for track in found.tracks:
            recording, candidate, created = self._remember(track)
            items.append(self._item(track, recording, candidate, created))
        return {'provider': provider.name, 'query': query, 'limit': limit,
                'warnings': list(found.warnings), 'items': items}

    def _track_for(self, candidate, recording):
        return ProviderTrack(provider=candidate.provider, provider_id=candidate.provider_id,
                             title=recording.title, artist=', '.join(recording.artists),
                             duration_ms=(candidate.duration_ms if candidate.duration_ms is not None
                                          else recording.duration_ms))

    def _asset_summary(self, asset, deduplicated):
        return {'asset_id': asset.id, 'sha256': asset.sha256, 'size_bytes': asset.size_bytes,
                'container': asset.container, 'codec': asset.codec,
                'duration_ms': asset.duration_ms, 'path': asset.path,
                'deduplicated': deduplicated}

    def download(self, job):
        payload = job.payload or {}
        candidate_id = payload.get('candidate_id')
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ProviderError('Не указан кандидат для скачивания.', code='invalid_payload')
        candidate = db.get_candidate(self.connection, candidate_id)
        if candidate is None:
            raise ProviderError('Кандидат не найден.', code='unknown_candidate')
        if candidate.availability is Availability.UNAVAILABLE:
            raise ProviderError('Источник помечен недоступным.', code='candidate_unavailable')
        recording = db.get_recording(self.connection, candidate.recording_id)
        if recording is None:
            raise ProviderError('Запись кандидата не найдена.', code='unknown_candidate')
        provider = self.provider(candidate.provider)
        audio_format = payload.get('audio_format', 'original')
        work = self.store.new_workdir()
        try:
            source = provider.download(self._track_for(candidate, recording), work,
                                       audio_format=audio_format)
            asset = self.store.store(source, origin=provider.name, candidate_id=candidate.id)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        known = db.find_asset_by_sha256(self.connection, asset.sha256)
        if known is not None:
            return self._asset_summary(known, deduplicated=True)
        db.save_asset(self.connection, asset)
        return self._asset_summary(asset, deduplicated=False)

    def _delivery_summary(self, delivery, deduplicated=False):
        return {'delivery_id': delivery.id, 'asset_id': delivery.asset_id,
                'target': delivery.target, 'status': delivery.status.value,
                'external_ref': delivery.external_ref, 'deduplicated': deduplicated}

    def deliver(self, job):
        payload = job.payload or {}
        asset_id = payload.get('asset_id')
        if not isinstance(asset_id, str) or not asset_id:
            raise ProviderError('Не указана запись для доставки.', code='invalid_payload')
        asset = db.get_asset(self.connection, asset_id)
        if asset is None:
            raise ProviderError('Запись не найдена.', code='unknown_asset')
        try:
            delivery = Delivery(asset_id=asset.id, target=payload.get('target'))
        except ValueError:
            raise ProviderError('Адрес доставки должен выглядеть как telegram:<id>.',
                                code='invalid_target') from None
        if self.sink is None:
            raise ProviderError('Доставка ещё не настроена в этом сервисе.',
                                code='delivery_not_configured')
        path = self.store.resolve(asset)
        known = db.find_delivery(self.connection, asset.id, delivery.target)
        if known is not None and known.status == DeliveryStatus.SENT:
            return self._delivery_summary(known, deduplicated=True)
        if known is None:
            db.save_delivery(self.connection, delivery)
            delivery_id = delivery.id
        else:
            delivery_id = known.id
            db.set_delivery_status(self.connection, delivery_id, DeliveryStatus.PENDING)
        try:
            reference = self.sink.send(path, asset, delivery.target)
        except Exception:
            db.set_delivery_status(self.connection, delivery_id, DeliveryStatus.FAILED)
            raise ProviderError('Доставка не удалась.', code='delivery_failed') from None
        stored = db.set_delivery_status(self.connection, delivery_id, DeliveryStatus.SENT, reference)
        return self._delivery_summary(stored)

    def limits(self):
        return {'store': self.store.limits(),
                'search_limit_max': MAX_SEARCH_LIMIT}
