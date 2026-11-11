"""Typed domain records for the Slynor Music Service.

Metadata never proves that audio exists: Recording, AudioCandidate and
AudioAsset stay separate so a catalogue card cannot be mistaken for a fetched
file, and an expiring stream URL never becomes a stable identifier.
"""
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

SHA256 = re.compile(r'^[a-f0-9]{64}$')
SLUG = re.compile(r'^[a-z0-9_]{1,32}$')
TARGET = re.compile(r'^(telegram|desktop|device):[^\s]{1,160}$')
HEX_ID = re.compile(r'^[a-f0-9]{1,64}$')


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobKind(str, Enum):
    SEARCH = 'search'
    RESOLVE = 'resolve'
    DOWNLOAD = 'download'
    CONVERT = 'convert'
    DELIVER = 'deliver'


class JobStatus(str, Enum):
    QUEUED = 'queued'
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class Availability(str, Enum):
    AVAILABLE = 'available'
    UNKNOWN = 'unknown'
    UNAVAILABLE = 'unavailable'


class DeliveryStatus(str, Enum):
    PENDING = 'pending'
    SENT = 'sent'
    FAILED = 'failed'


def _clean_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError('empty value')
    return value


class Recording(BaseModel):
    """A musical record as catalogued; it does not imply a playable file."""

    model_config = ConfigDict(extra='forbid')

    id: str = Field(default_factory=lambda: uuid4().hex)
    title: str = Field(min_length=1, max_length=500)
    artists: list[str] = Field(default_factory=list, max_length=32)
    album: str | None = Field(default=None, max_length=500)
    duration_ms: int | None = Field(default=None, ge=0)
    version: str | None = Field(default=None, max_length=120)
    external_ids: dict[str, str] = Field(default_factory=dict)
    metadata_origin: str | None = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=utcnow)

    @field_validator('title')
    @classmethod
    def _title(cls, value: str) -> str:
        return _clean_name(value)

    @field_validator('artists')
    @classmethod
    def _artists(cls, value: list[str]) -> list[str]:
        cleaned = []
        for artist in value:
            artist = artist.strip()
            if not artist:
                raise ValueError('empty artist')
            if len(artist) > 200:
                raise ValueError('artist is too long')
            cleaned.append(artist)
        return cleaned

    @field_validator('external_ids')
    @classmethod
    def _external_ids(cls, value: dict[str, str]) -> dict[str, str]:
        for key, reference in value.items():
            if not SLUG.match(key):
                raise ValueError(f'invalid external id provider: {key!r}')
            if not isinstance(reference, str) or not reference.strip() or len(reference) > 256:
                raise ValueError('invalid external id value')
        return value


class AudioCandidate(BaseModel):
    """A concrete source for a recording; availability is a claim, not a file."""

    model_config = ConfigDict(extra='forbid')

    id: str = Field(default_factory=lambda: uuid4().hex)
    recording_id: str = Field(min_length=1, max_length=64)
    provider: str = Field(max_length=32)
    provider_id: str = Field(min_length=1, max_length=200)
    duration_ms: int | None = Field(default=None, ge=0)
    availability: Availability = Availability.UNKNOWN
    version_hint: str | None = Field(default=None, max_length=120)
    score: float | None = Field(default=None, ge=0, le=1)
    created_at: datetime = Field(default_factory=utcnow)

    @field_validator('provider')
    @classmethod
    def _provider(cls, value: str) -> str:
        if not SLUG.match(value):
            raise ValueError('provider must be a lowercase slug')
        return value

    @field_validator('provider_id')
    @classmethod
    def _provider_id(cls, value: str) -> str:
        value = value.strip()
        if value.startswith(('http://', 'https://')):
            raise ValueError('expiring stream URLs are not stable identifiers')
        return _clean_name(value)


class AudioAsset(BaseModel):
    """A file that was actually fetched and measured."""

    model_config = ConfigDict(extra='forbid')

    id: str = Field(default_factory=lambda: uuid4().hex)
    candidate_id: str | None = Field(default=None, max_length=64)
    container: str = Field(min_length=1, max_length=32)
    codec: str | None = Field(default=None, max_length=32)
    sha256: str
    size_bytes: int = Field(ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    origin: str | None = Field(default=None, max_length=200)
    path: str = Field(min_length=1, max_length=4096)
    created_at: datetime = Field(default_factory=utcnow)

    @field_validator('sha256')
    @classmethod
    def _sha256(cls, value: str) -> str:
        value = value.lower()
        if not SHA256.match(value):
            raise ValueError('sha256 must be 64 lowercase hex characters')
        return value


class Delivery(BaseModel):
    """Handing a finished asset to a user or device."""

    model_config = ConfigDict(extra='forbid')

    id: str = Field(default_factory=lambda: uuid4().hex)
    asset_id: str = Field(min_length=1, max_length=64)
    target: str = Field(max_length=200)
    status: DeliveryStatus = DeliveryStatus.PENDING
    external_ref: str | None = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=utcnow)

    @field_validator('target')
    @classmethod
    def _target(cls, value: str) -> str:
        value = value.strip()
        if not TARGET.match(value):
            raise ValueError('target must look like telegram:<id>, desktop:<name> or device:<id>')
        return value


class Job(BaseModel):
    """A persisted unit of work; a job is never lost on restart."""

    model_config = ConfigDict(extra='forbid')

    id: str = Field(default_factory=lambda: uuid4().hex)
    kind: JobKind
    status: JobStatus = JobStatus.QUEUED
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
