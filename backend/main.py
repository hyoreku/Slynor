import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from collections import OrderedDict
from threading import RLock
import ntpath
import httpx
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from urllib.parse import urlparse
from secrets import compare_digest
from backend.local_session import APP_TOKEN, COOKIE_NAME
from backend.online_music import OnlineLibrary, ProviderError
from backend.internet_music import InternetLibrary
from backend.catalog_music import CatalogueLibrary
from backend.service_client import ServiceClient
from backend.service_runtime import ServiceRuntime
from backend.library_service import LibraryService
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from player.audio_engine import AudioEngine
from player.post_eq_spectrum import PostEqSpectrumAnalyzer
from player.equalizer import EqualizerController
from backend.image_assets import get_data_dir
from backend.metadata import read_track_metadata
from backend.image_assets import get_cover_path
from backend.wallpaper_assets import get_wallpaper_path
from backend.track_covers import get_track_cover_path, clear_track_cover_cache


_metadata_cache=OrderedDict();_metadata_lock=RLock()
def metadata_key(path):return path if path.startswith('online:') else ntpath.normcase(ntpath.normpath(path))
def cache_metadata(track):
    with _metadata_lock:
        _metadata_cache[metadata_key(track['path'])]=dict(track)
        while len(_metadata_cache)>512:_metadata_cache.popitem(last=False)
PROJECT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_FILE = PROJECT_DIR / "frontend" / "index.html"


# Один аудиодвижок на время работы сервера; закрываем при завершении.
@asynccontextmanager
async def lifespan(app: FastAPI):
    service_client = None
    service_runtime = None
    try:
        service_client = ServiceClient()
        service_runtime = ServiceRuntime(service_client)
    except Exception:
        service_client = None
        service_runtime = None
    app.state.service_client = service_client
    app.state.service_runtime = service_runtime
    app.state.online = CatalogueLibrary(get_data_dir(), service=service_client)
    app.state.library_service = LibraryService(get_data_dir(), service_client)
    if service_runtime is not None:
        # Wait briefly so the first window reads the shared state, not only the
        # cache. A slow or broken service must not delay startup for long.
        try:
            await asyncio.to_thread(service_runtime.ensure_started, 6.0)
            await asyncio.to_thread(app.state.library_service.sync)
        except Exception:
            pass
    app.state.library_service.start_background()
    app.state.audio_engine = AudioEngine()
    try:
        import json
        saved=json.loads((get_data_dir()/'settings.json').read_text('utf-8')).get('audio_output','')
        if isinstance(saved,str) and 0<len(saved)<=2048:app.state.audio_engine._output_device=saved
    except (OSError,ValueError,AttributeError):pass
    def resolve_stream(ref):
        return app.state.online.play_url(ref), app.state.online.metadata(ref)['title']
    app.state.audio_engine.stream_resolver = resolve_stream
    app.state.spectrum_analyzer = PostEqSpectrumAnalyzer()
    app.state.equalizer = EqualizerController(app.state.audio_engine, get_data_dir())

    try:
        yield
    finally:
        app.state.spectrum_analyzer.close()
        app.state.audio_engine.close()
        app.state.online.close()
        if app.state.service_runtime is not None:
            app.state.service_runtime.stop()
        if app.state.service_client is not None:
            app.state.service_client.close()


app = FastAPI(
    title="Slynor API",
    version="0.2.0",
    lifespan=lifespan,
)

# Reject cross-site browser commands and require a per-process local capability.
# Media requests use their own unguessable handle because LibVLC has no browser cookie.
@app.middleware('http')
async def local_api_guard(request: Request, call_next):
    if request.url.path in ('/ui','/mini') or request.url.path.startswith('/api/'):
        if request.url.hostname not in ('127.0.0.1','localhost'):
            return JSONResponse({'detail':'Недопустимый локальный адрес.'},status_code=403)
    if request.url.path.startswith('/api/'):
        origin = request.headers.get('origin')
        if origin:
            try:
                parsed = urlparse(origin); origin_port=parsed.port
            except ValueError:
                return JSONResponse({'detail':'Недопустимый источник запроса.'},status_code=403)
            if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1','localhost') or origin_port != 8765:
                return JSONResponse({'detail':'Недопустимый источник запроса.'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail':'Межсайтовые команды запрещены.'}, status_code=403)
        if not request.url.path.startswith('/api/online/media/'):
            token = request.headers.get('x-ownplayer-token') or request.cookies.get(COOKIE_NAME) or ''
            if not compare_digest(token.encode(), APP_TOKEN.encode()):
                return JSONResponse({'detail':'Локальная сессия истекла. Откройте окно приложения.'},status_code=401)
    response = await call_next(request)
    if request.url.path in ('/ui','/mini'):
        response.set_cookie(COOKIE_NAME, APP_TOKEN, httponly=True, samesite='strict')
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
    return response

# Статические файлы интерфейса: CSS и JavaScript.
app.mount(
    "/static",
    StaticFiles(directory=PROJECT_DIR / "frontend"),
    name="static",
)


# Модели запросов: Pydantic проверяет типы и допустимые границы до вызова VLC.
class PlayRequest(BaseModel):
    path: str = Field(min_length=1)


class VolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100, strict=True)


class SeekRequest(BaseModel):
    file_path: str | None = None
    position_ms: int = Field(ge=0, strict=True)


class QueuePlayRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=10000)
    index: int = Field(ge=0, strict=True)


class ShuffleRequest(BaseModel):
    enabled: bool = Field(strict=True)
    paths: list[str] = Field(default_factory=list, max_length=10000)


class MetadataRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=100)
    refresh_covers: bool = Field(default=False, strict=True)


# Границы дополнительно проверяет общий валидатор: NaN, bool вместо числа и длина массива.
class EqualizerRequest(BaseModel):
    speed_mode: Literal["standard", "speedup", "slowed"] = "standard"
    enabled: bool = Field(strict=True)
    preamp: float = Field(ge=-20, le=20, strict=True, allow_inf_nan=False)
    bands: list[Annotated[float, Field(ge=-20, le=20, strict=True, allow_inf_nan=False)]] = Field(min_length=10, max_length=10)


@app.get("/api/player/equalizer")
def get_equalizer():
    return app.state.equalizer.snapshot()


def change_equalizer(body, save=False):
    try:
        started = perf_counter()
        result = app.state.equalizer.save(body.model_dump()) if save else app.state.equalizer.apply(body.model_dump())
        result['command_apply_ms'] = round((perf_counter() - started) * 1000, 2)
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (RuntimeError, OSError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/player/equalizer/apply")
def apply_equalizer(body: EqualizerRequest):
    return change_equalizer(body)


@app.post("/api/player/equalizer/save")
def save_equalizer(body: EqualizerRequest):
    return change_equalizer(body, save=True)


class PresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80, strict=True)
    config: EqualizerRequest


@app.post("/api/player/equalizer/presets")
def create_equalizer_preset(body: PresetRequest):
    try:
        return app.state.equalizer.create_preset(body.name, body.config.model_dump())
    except ValueError as error:raise HTTPException(status_code=400, detail=str(error)) from error
    except (RuntimeError, OSError) as error:raise HTTPException(status_code=503, detail=str(error)) from error


@app.delete("/api/player/equalizer/presets/{preset_id}")
def delete_equalizer_preset(preset_id: str):
    try:return app.state.equalizer.delete_preset(preset_id)
    except ValueError as error:raise HTTPException(status_code=404, detail=str(error)) from error
    except (RuntimeError, OSError) as error:raise HTTPException(status_code=503, detail=str(error)) from error


# Проверка доступности локального сервера.
@app.get("/")
def health_check():
    return {"status": "ok"}


# Выдача HTML интерфейса; CSS и JS загружаются отдельно.
@app.get("/ui", include_in_schema=False)
def get_interface():
    return FileResponse(FRONTEND_FILE)


# Команда интерфейса → аудиодвижок; ошибки превращаются в HTTP-ответ.
@app.post("/api/player/play")
def play_track(body: PlayRequest):
    try:
        filename = app.state.audio_engine.play(body.path)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, OSError) as error:
        raise HTTPException(
            status_code=400,
            detail="Некорректный путь к файлу.",
        ) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return {
        "status": "ok",
        "message": f"Команда воспроизведения отправлена: {filename}",
    }


# Команда интерфейса → аудиодвижок; ошибки превращаются в HTTP-ответ.
@app.post("/api/player/pause")
def pause_track():
    try:
        app.state.audio_engine.pause()
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return {"status": "ok", "message": "Команда паузы отправлена."}


# Команда интерфейса → аудиодвижок; ошибки превращаются в HTTP-ответ.
@app.post("/api/player/resume")
def resume_track():
    try:
        app.state.audio_engine.resume()
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return {"status": "ok", "message": "Команда продолжения отправлена."}


# Команда интерфейса → аудиодвижок; ошибки превращаются в HTTP-ответ.
@app.post("/api/player/stop")
def stop_track():
    app.state.audio_engine.stop()
    return {"status": "ok", "message": "Воспроизведение остановлено. Текущая песня и очередь сняты."}


@app.get("/api/player/state")
def get_player_state():
    result=app.state.audio_engine.get_state()
    from backend.desktop_controls import get_controller
    controller=get_controller()
    if controller is not None:result['window_visibility']={'main':controller.main_visible,'mini':controller.mini_visible}
    from backend.appearance import snapshot
    result['appearance']=snapshot()
    path=result.get('file_path')
    if path:
        with _metadata_lock:result['current_metadata']=_metadata_cache.get(metadata_key(path))
        if result['current_metadata'] is None and path.startswith('online:'):
            try:result['current_metadata']=app.state.online.metadata(path)
            except ProviderError:pass
    return result


# Команда интерфейса → аудиодвижок; ошибки превращаются в HTTP-ответ.
@app.post("/api/player/volume")
def set_player_volume(body: VolumeRequest):
    try:
        app.state.audio_engine.set_volume(body.volume)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return {
        "status": "ok",
        "message": f"Громкость установлена: {body.volume}%.",
    }


# Команда интерфейса → аудиодвижок; ошибки превращаются в HTTP-ответ.
@app.post("/api/player/seek")
def seek_track(body: SeekRequest):
    try:
        target_ms = app.state.audio_engine.seek(body.position_ms, body.file_path)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return {
        "status": "ok",
        "position_ms": target_ms,
        "message": "Команда перемотки отправлена.",
    }


# Общая обработка ошибок запуска и переключения песни в очереди.
def _queue_command_response(command):
    try:
        filename = command()
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(
            status_code=400,
            detail="Некорректный путь к файлу.",
        ) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return {
        "status": "ok",
        "message": f"Команда воспроизведения отправлена: {filename}",
    }


# Запуск выбранной песни и всего списка в обычном или случайном режиме.
# Команда интерфейса → аудиодвижок; ошибки превращаются в HTTP-ответ.
@app.post("/api/player/queue/play")
def play_queue(body: QueuePlayRequest):
    return _queue_command_response(
        lambda: app.state.audio_engine.play_queue(body.paths, body.index)
    )


# Long queues keep the existing small-request guard and are assembled in
# bounded, expiring chunks. All these routes use the local capability middleware.
from backend.queue_uploads import QueueUploads, QueueUploadError
_queue_uploads=QueueUploads()

class QueueChunkRequest(BaseModel):
    offset: int = Field(ge=0,strict=True)
    paths: list[Annotated[str,Field(min_length=1,max_length=4096)]] = Field(min_length=1,max_length=1000)

class QueueCommitRequest(BaseModel):
    expected_count: int = Field(ge=1,strict=True)
    index: int = Field(default=0,ge=0,strict=True)
    shuffle: bool = Field(default=False,strict=True)

@app.post('/api/player/queue/uploads')
def begin_queue_upload():
    try:return {'id':_queue_uploads.begin()}
    except QueueUploadError as error:raise HTTPException(409,str(error)) from error

@app.post('/api/player/queue/uploads/{key}/chunks')
def append_queue_upload(key:str,body:QueueChunkRequest):
    try:return {'accepted':_queue_uploads.append(key,body.offset,body.paths)}
    except QueueUploadError as error:raise HTTPException(409,str(error)) from error

@app.delete('/api/player/queue/uploads/{key}')
def cancel_queue_upload(key:str):
    _queue_uploads.cancel(key);return {'ok':True}

@app.post('/api/player/queue/uploads/{key}/commit')
def commit_queue_upload(key:str,body:QueueCommitRequest):
    try:paths=_queue_uploads.take(key,body.expected_count)
    except QueueUploadError as error:raise HTTPException(409,str(error)) from error
    if body.shuffle:
        return _queue_command_response(lambda:app.state.audio_engine.set_shuffle(True,paths))
    return _queue_command_response(lambda:app.state.audio_engine.play_queue(paths,body.index))


# Команда перехода вперёд в текущей очереди.
# Команда интерфейса → аудиодвижок; ошибки превращаются в HTTP-ответ.
@app.post("/api/player/next")
def next_track():
    return _queue_command_response(
        app.state.audio_engine.next_track
    )


# Команда перехода назад в текущем проходе.
# Команда интерфейса → аудиодвижок; ошибки превращаются в HTTP-ответ.
@app.post("/api/player/previous")
def previous_track():
    return _queue_command_response(
        app.state.audio_engine.previous_track
    )


# Переключение режима: случайный старт или восстановление обычного порядка.
# Команда интерфейса → аудиодвижок; ошибки превращаются в HTTP-ответ.
@app.post("/api/player/shuffle")
def set_shuffle(body: ShuffleRequest):
    try:
        message = app.state.audio_engine.set_shuffle(
            body.enabled,
            body.paths,
        )
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except OSError as error:
        raise HTTPException(
            status_code=400,
            detail="Некорректный путь к файлу.",
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    return {
        "status": "ok",
        "message": message,
    }


# Чтение метаданных пачки файлов; неудача одного не роняет весь список.
@app.post("/api/library/metadata")
def get_library_metadata(body: MetadataRequest):
    if body.refresh_covers:
        clear_track_cover_cache()
    tracks=[]
    for path in body.paths:
        if path.startswith('online:'):
            try: track=app.state.online.metadata(path)
            except ProviderError as error:
                track={'path':path,'title':'Интернет-трек','artist':None,'album':None,'duration_ms':None,
                       'cover_url':None,'cover_error':None,'error':str(error)}
        else: track=read_track_metadata(path)
        cache_metadata(track)
        tracks.append(track)
    return {'tracks':tracks}


# Снимок реальной очереди для правой панели интерфейса.
@app.get("/api/player/queue")
def get_player_queue():
    return app.state.audio_engine.get_queue()


# Выдача только управляемой JPEG-копии обложки плейлиста.
@app.get("/api/assets/playlist-covers/{filename}", include_in_schema=False)
def get_playlist_cover(filename: str):
    try:
        path = get_cover_path(filename)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Обложка не найдена.") from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Обложка не найдена.")
    return FileResponse(path, media_type="image/jpeg")


# Обложки песен выдаются отдельно от пользовательских обложек плейлистов.
# Выдача только кэшированной JPEG-копии обложки песни.
@app.get("/api/assets/track-covers/{filename}", include_in_schema=False)
def get_track_cover(filename: str):
    try:
        path = get_track_cover_path(filename)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Обложка песни не найдена.") from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Обложка песни не найдена.")
    return FileResponse(path, media_type="image/jpeg")


# Отдаём только JPEG-копии обоев, а не произвольные файлы AppData.
@app.get("/api/assets/wallpapers/{filename}", include_in_schema=False)
def get_wallpaper(filename: str):
    try:
        path = get_wallpaper_path(filename)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Обои не найдены.") from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Обои не найдены.")
    return FileResponse(path, media_type={".jpg":"image/jpeg",".gif":"image/gif",".mp4":"video/mp4",".webm":"video/webm"}[path.suffix])


# Анализируется только текущий локальный трек движка: клиент не передаёт путь к файлу.
@app.get("/api/player/spectrum")
def get_player_spectrum():
    state = app.state.audio_engine.get_state()
    if (state.get('file_path') or '').startswith('online:'):
        app.state.spectrum_analyzer.suspend()
        return {'file_path':state['file_path'],'state':state['state'],'status':'unavailable','bands':[],
                'message':'Спектр интернет-потока пока недоступен. Воспроизведение не затронуто.'}
    return app.state.spectrum_analyzer.snapshot(state, app.state.audio_engine.get_equalizer())


@app.post("/api/player/spectrum/suspend")
def suspend_spectrum():
    app.state.spectrum_analyzer.suspend()
    return {"status": "ok"}


@app.get('/mini')
def mini_ui():
    return FileResponse(PROJECT_DIR / 'frontend' / 'mini.html')

@app.get('/api/desktop/status')
def desktop_status():
    from backend.desktop_controls import invoke
    try: return invoke('status')
    except RuntimeError as error: raise HTTPException(503, str(error)) from error

@app.post('/api/desktop/{action}')
def desktop_action(action: Literal['show', 'mini', 'quit', 'hide_mini']):
    from backend.desktop_controls import invoke
    try: return invoke(action)
    except RuntimeError as error: raise HTTPException(503, str(error)) from error


class OnlineSearchRequest(BaseModel):
    source: Literal['soundcloud','vk']
    query: str = Field(min_length=1,max_length=200)

class OnlineImportRequest(BaseModel):
    source: Literal['soundcloud','vk']
    url: str = Field(min_length=1,max_length=2000)

@app.get('/api/service/status')
def service_status():
    runtime = app.state.service_runtime
    if runtime is None:
        return {'available': False, 'running': False, 'starting': False, 'url': None,
                'error': 'Music Service не настроен.'}
    return runtime.status()


@app.post('/api/service/start')
def service_start():
    runtime = app.state.service_runtime
    if runtime is None:
        raise HTTPException(503, 'Music Service не настроен.')
    runtime.ensure_started()
    return runtime.status(refresh=True)


@app.get('/api/online/status')
def online_status(): return app.state.online.status()

@app.get('/api/online/playlists')
def online_playlists(): return {'playlists':app.state.online.imported()}

@app.post('/api/online/search')
def online_search(body: OnlineSearchRequest):
    try: return {'tracks':app.state.online.search(body.source,body.query.strip())}
    except ProviderError as error: raise HTTPException(503,str(error)) from error

@app.post('/api/online/import')
def online_import(body: OnlineImportRequest):
    try: return {'playlist':app.state.online.import_playlist(body.source,body.url.strip())}
    except ProviderError as error: raise HTTPException(503,str(error)) from error
    except OSError: raise HTTPException(500,'Не удалось сохранить импорт. Предыдущий файл не изменён.') from None

@app.get('/api/online/media/{handle}/{resource}', include_in_schema=False)
def online_media(handle: str, resource: str, request: Request):
    try:
        library=app.state.online
        response,remote=library.open_media(library.resource(handle,resource),request.headers.get('range'))
        content_type=response.headers.get('content-type','application/octet-stream').split(';')[0]
        if 'mpegurl' in content_type.lower() or urlparse(remote).path.endswith('.m3u8'):
            chunks=[];size=0
            try:
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>2*1024*1024: raise ProviderError('Плейлист потока слишком большой.')
                    chunks.append(chunk)
                text=b''.join(chunks).decode('utf-8-sig')
            finally: response.close()
            if not text.lstrip().startswith('#EXTM3U'): raise ProviderError('Некорректный HLS-плейлист.')
            return Response(library.rewrite_playlist(handle,text,remote),media_type='application/vnd.apple.mpegurl')
        headers={key:response.headers[key] for key in ('content-range','accept-ranges') if key in response.headers}
        def chunks():
            try: yield from response.iter_bytes(chunk_size=65536)
            except httpx.HTTPError:
                # Headers may already have been sent: update playback state instead of claiming HTTP 503.
                from backend.provider_diagnostics import record
                record(None,'stream',0,'stream_read_error')
                app.state.audio_engine.report_stream_error('http://127.0.0.1:8765/api/online/media/'+handle+'/master','Соединение с аудиоисточником прервалось.')
            finally: response.close()
        return StreamingResponse(chunks(),status_code=response.status_code,media_type=content_type,
                                 headers=headers,background=BackgroundTask(response.close))
    except ProviderError as error:
        app.state.audio_engine.report_stream_error('http://127.0.0.1:8765/api/online/media/'+handle+'/master', str(error))
        raise HTTPException(503,str(error)) from error
    except (UnicodeError,ValueError): raise HTTPException(502,'Некорректные данные потока.') from None


@app.post('/api/diagnostics/stacks')
def diagnostic_stacks():
    from backend.diagnostics import dump_stacks
    dump_stacks()
    return {'status':'ok'}


class SearchJobRequest(BaseModel):
    source: Literal['auto','archive','hitmo','muzbomb','soundcloud','vk','service']='auto'
    query: str = Field(min_length=2,max_length=200)
class DownloadRequest(BaseModel):
    path: str = Field(min_length=1,max_length=200)
    confirmed: bool = Field(default=False,strict=True)
    audio_format: Literal['original','mp3']='original'
class PinRequest(BaseModel):
    path: str = Field(min_length=1,max_length=200)

@app.post('/api/online/search-jobs')
def search_job(body:SearchJobRequest):
    return app.state.online.start_search(body.query.strip(),body.source)
@app.get('/api/online/jobs/{key}')
def music_job(key:str):
    try:
        result=app.state.online.job(key)
        path=result.get('path') if result.get('status')=='done' else None
        if path:
            with _metadata_lock:cached=metadata_key(path) in _metadata_cache
            if not cached:cache_metadata(read_track_metadata(path))
        return result
    except ProviderError as error:raise HTTPException(404,str(error)) from error
@app.post('/api/online/jobs/{key}/cancel')
def cancel_music_job(key:str):return app.state.online.cancel_job(key)
@app.post('/api/online/downloads')
def download_music(body:DownloadRequest):
    try:return app.state.online.start_download(body.path,body.confirmed,body.audio_format)
    except ProviderError as error:raise HTTPException(400,str(error)) from error
@app.post('/api/online/pin')
def pin_music(body:PinRequest):
    try:return app.state.online.pin(body.path)
    except ProviderError as error:raise HTTPException(400,str(error)) from error


@app.get('/api/online/provider-status')
def provider_status():
    return app.state.online.provider_status()


from backend.appearance import Appearance,publish
@app.post('/api/appearance')
def update_appearance(body:Appearance):return publish(body)
@app.get('/api/player/output-devices')
def output_devices():return app.state.audio_engine.output_devices()
class OutputRequest(BaseModel):
    device:str=Field(max_length=2048)
@app.post('/api/player/output-device')
def choose_output(body:OutputRequest):
    try:return app.state.audio_engine.set_output_device(body.device)
    except ValueError as error:raise HTTPException(400,str(error)) from error


@app.get('/api/assets/online-covers/{filename}',include_in_schema=False)
def online_cover(filename:str):
    import re
    if not re.fullmatch(r'[a-f0-9]{64}\.jpg',filename):raise HTTPException(404,'Обложка не найдена.')
    try:return FileResponse(app.state.online.cover_file(filename[:-4]),media_type='image/jpeg')
    except ProviderError as error:raise HTTPException(404,str(error)) from error

@app.post('/api/online/stream-jobs')
def prepare_fast_stream(body:PinRequest):
    try:return app.state.online.start_stream(body.path)
    except ProviderError as error:raise HTTPException(400,str(error)) from error

@app.get('/api/online/diagnostics')
def provider_diagnostics():
    from backend.provider_diagnostics import snapshot
    return {'events':snapshot(),'providers':app.state.online.provider_status(),'native_audio':app.state.audio_engine.output_diagnostics(),'network':app.state.online.network.report(),'note':'Адреса потоков, ключи и stderr не сохраняются. Нативные сбои требуют логов Windows.'}

from typing import Literal
class UiStallRequest(BaseModel):
    duration_ms:int=Field(ge=3500,le=120000,strict=True)
    screen:Literal['home','music','playing','online','settings','unknown']='unknown'

_stall_report_at=0.0
@app.post('/api/diagnostics/ui-stall')
def possible_ui_stall(body:UiStallRequest):
    import time,logging
    global _stall_report_at
    now=time.monotonic()
    if now-_stall_report_at<60:return {'recorded':False}
    _stall_report_at=now
    logging.getLogger('ownplayer').warning('possible_ui_stall duration_ms=%s screen=%s',body.duration_ms,body.screen)
    from backend.diagnostics import dump_stacks
    dump_stacks()
    return {'recorded':True}


class NetworkRequest(BaseModel):
    mode:Literal['system','manual','direct']
    url:str=''
    search_first:Literal['auto','youtube','soundcloud','spotify']='auto'
    username:str|None=None
    password:SecretStr|None=None
    clear_credentials:bool=False

@app.get('/api/network/settings')
def network_settings():return app.state.online.network.view()

@app.post('/api/network/settings')
def network_save(body:NetworkRequest):
    from backend.network import NetworkError
    library=app.state.online
    try:
        value=library.network.apply({'mode':body.mode,'url':body.url,'search_first':body.search_first},body.username,body.password.get_secret_value() if body.password is not None else None,body.clear_credentials)
    except NetworkError as error:raise HTTPException(422,str(error)) from None
    except OSError:raise HTTPException(500,'Не удалось сохранить настройки сети. Предыдущий файл сохранён.') from None
    with library.job_lock:
        for key in list(library.jobs):library.cancel_job(key)
    with library.lock:
        library.ready_streams.clear();library.cover_failures.clear()
    library.clear_search_cache()
    return value

@app.post('/api/network/check')
def network_check():
    return app.state.online.network.check()
