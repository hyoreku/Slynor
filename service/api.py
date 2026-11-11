"""Local HTTP API for the Music Service.

Loopback only: device authentication for remote clients is a later stage, so the
service must not be exposed to a network before that exists. Jobs are accepted
here and executed by the single dispatcher; when the application is created
without a pipeline the service reports health and keeps jobs queued, which is
what the skeleton tests rely on.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import db
from .jobs import Dispatcher, JobQueue
from .models import Job, JobKind, JobStatus


class JobCreate(BaseModel):
    kind: JobKind
    payload: dict = Field(default_factory=dict)


class LibraryBody(BaseModel):
    tracks: list[str] = Field(default_factory=list, max_length=200000)


class PlaylistsBody(BaseModel):
    playlists: list[dict] = Field(default_factory=list, max_length=2000)


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TrackPath(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class TracksAdd(BaseModel):
    playlist_ids: list[str] = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)


class CoverBody(BaseModel):
    cover: str | None = Field(default=None, max_length=80)


def _library_response(connection):
    return {'tracks': db.load_library(connection), **db.library_state(connection)}


def _playlist_response(connection):
    return {'playlists': db.load_playlists(connection), **db.library_state(connection)}


def _playlist_error(error):
    text = str(error)
    if text == 'Плейлист не найден.':
        return HTTPException(status_code=404, detail=text)
    if 'уже существует' in text:
        return HTTPException(status_code=409, detail=text)
    return HTTPException(status_code=400, detail=text)


def provider_operations(name: str):
    """What one connected source can actually do today."""
    return ['search', 'download'] + (['resolve'] if name == 'youtube' else [])


def create_app(db_path=None, pipeline_factory=None, *, run_jobs=False, poll_seconds=0.2) -> FastAPI:
    path = Path(db_path) if db_path else db.default_db_path()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        connection = db.connect(path)
        queue = JobQueue(connection)
        app.state.connection = connection
        app.state.queue = queue
        app.state.pipeline = None
        app.state.dispatcher = None
        try:
            if pipeline_factory is not None:
                app.state.pipeline = pipeline_factory(connection)
                app.state.dispatcher = Dispatcher(queue, app.state.pipeline.handlers(),
                                                  poll_seconds=poll_seconds)
                if run_jobs:
                    app.state.dispatcher.start()
            yield
        finally:
            if app.state.dispatcher is not None:
                app.state.dispatcher.stop()
            connection.close()

    app = FastAPI(title='Slynor Music Service', version='0.2.0', lifespan=lifespan)

    @app.get('/health')
    def health():
        connection = app.state.connection
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        journal = connection.execute('PRAGMA journal_mode').fetchone()[0]
        counts = {status.value: 0 for status in JobStatus}
        for row in connection.execute('SELECT status, COUNT(*) AS total FROM jobs GROUP BY status'):
            counts[row['status']] = row['total']
        return {'status': 'ok', 'schema_version': version, 'journal_mode': journal,
                'jobs': counts, 'dispatcher': app.state.dispatcher is not None}

    @app.get('/providers')
    def providers():
        pipeline = app.state.pipeline
        names = sorted(pipeline.providers) if pipeline is not None else []
        return {'providers': [{'name': name, 'operations': provider_operations(name)}
                              for name in names]}

    @app.get('/limits')
    def limits():
        if app.state.pipeline is None:
            raise HTTPException(status_code=404, detail='Задания ещё не подключены.')
        return app.state.pipeline.limits()

    @app.post('/jobs', status_code=201, response_model=Job)
    def create_job(body: JobCreate):
        return app.state.queue.enqueue(body.kind, body.payload)

    @app.get('/jobs', response_model=list[Job])
    def list_jobs(status: JobStatus | None = None, limit: int = Query(50, ge=1, le=500)):
        return app.state.queue.list(status, limit)

    @app.get('/jobs/{job_id}', response_model=Job)
    def get_job(job_id: str):
        job = app.state.queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail='Задание не найдено.')
        return job

    @app.post('/jobs/{job_id}/cancel', response_model=Job)
    def cancel_job(job_id: str):
        if app.state.queue.get(job_id) is None:
            raise HTTPException(status_code=404, detail='Задание не найдено.')
        try:
            return app.state.queue.cancel(job_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get('/recordings/{recording_id}')
    def get_recording(recording_id: str):
        connection = app.state.connection
        recording = db.get_recording(connection, recording_id)
        if recording is None:
            raise HTTPException(status_code=404, detail='Запись не найдена.')
        return {'recording': recording,
                'candidates': db.list_candidates(connection, recording_id)}

    @app.get('/assets/{asset_id}')
    def get_asset(asset_id: str):
        asset = db.get_asset(app.state.connection, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail='Файл не найден.')
        return asset

    @app.get('/assets/{asset_id}/content')
    def get_asset_content(asset_id: str):
        pipeline = app.state.pipeline
        if pipeline is None:
            raise HTTPException(status_code=404, detail='Файл не найден.')
        asset = db.get_asset(app.state.connection, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail='Файл не найден.')
        return FileResponse(pipeline.store.resolve(asset), media_type='application/octet-stream',
                            filename=Path(asset.path).name)

    @app.get('/deliveries')
    def list_deliveries(asset_id: str):
        return db.list_deliveries(app.state.connection, asset_id)

    @app.get('/library')
    def get_library():
        return _library_response(app.state.connection)

    @app.put('/library')
    def put_library(body: LibraryBody):
        try:
            db.replace_library(app.state.connection, body.tracks)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _library_response(app.state.connection)

    @app.get('/library/revision')
    def get_library_revision():
        return db.library_state(app.state.connection)

    @app.get('/playlists')
    def get_playlists():
        return _playlist_response(app.state.connection)

    @app.put('/playlists')
    def put_playlists(body: PlaylistsBody):
        try:
            db.replace_playlists(app.state.connection, body.playlists)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _playlist_response(app.state.connection)

    @app.post('/playlists', status_code=201)
    def create_playlist(body: PlaylistCreate):
        try:
            db.create_playlist(app.state.connection, body.name)
        except ValueError as error:
            raise _playlist_error(error) from error
        return _playlist_response(app.state.connection)

    @app.patch('/playlists/{playlist_id}')
    def rename_playlist(playlist_id: str, body: PlaylistCreate):
        try:
            db.rename_playlist(app.state.connection, playlist_id, body.name)
        except ValueError as error:
            raise _playlist_error(error) from error
        return _playlist_response(app.state.connection)

    @app.delete('/playlists/{playlist_id}')
    def delete_playlist(playlist_id: str):
        try:
            db.delete_playlist(app.state.connection, playlist_id)
        except ValueError as error:
            raise _playlist_error(error) from error
        return _playlist_response(app.state.connection)

    @app.post('/playlists/{playlist_id}/tracks')
    def add_playlist_track(playlist_id: str, body: TrackPath):
        try:
            db.add_track_to_playlist(app.state.connection, playlist_id, body.path)
        except ValueError as error:
            raise _playlist_error(error) from error
        return _playlist_response(app.state.connection)

    @app.post('/playlists/{playlist_id}/tracks/remove')
    def remove_playlist_track(playlist_id: str, body: TrackPath):
        try:
            db.remove_track_from_playlist(app.state.connection, playlist_id, body.path)
        except ValueError as error:
            raise _playlist_error(error) from error
        return _playlist_response(app.state.connection)

    @app.post('/playlists/tracks')
    def add_track_to_playlists(body: TracksAdd):
        try:
            db.add_track_to_playlists(app.state.connection, body.playlist_ids, body.path)
        except ValueError as error:
            raise _playlist_error(error) from error
        return _playlist_response(app.state.connection)

    @app.post('/playlists/{playlist_id}/cover')
    def set_playlist_cover(playlist_id: str, body: CoverBody):
        try:
            db.set_playlist_cover(app.state.connection, playlist_id, body.cover)
        except ValueError as error:
            raise _playlist_error(error) from error
        return _playlist_response(app.state.connection)

    return app
