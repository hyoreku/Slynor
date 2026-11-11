"""Optional catalogue search and isolated per-track downloads. No audio work under GUI/VLC locks."""
from copy import deepcopy
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, wait
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from time import monotonic
from urllib.parse import urlparse
from uuid import uuid4
from secrets import token_urlsafe
from threading import Lock
import httpx
from backend.internet_music import InternetLibrary,ProviderError,MAX_AUDIO
from backend.provider_worker import validate_url, validate_track_url
from backend.download_metadata import publish_download, write_missing_tags, remember_cached_metadata

COVER_HOSTS=('i.scdn.co','mosaic.scdn.co','i.ytimg.com','img.youtube.com')
# googlevideo URLs stay valid for hours, so a resolved stream is reused instead
# of paying yt-dlp extraction again on every play within the same session.
STREAM_CACHE_SECONDS=1800
def cover_url(url):
 try:
  u=urlparse(url)
  if u.scheme=='https' and (u.hostname in COVER_HOSTS or (u.hostname or '').endswith('.sndcdn.com')) and not u.username and not u.password and u.port in (None,443):return url
 except (ValueError,TypeError):pass
 return None

class CatalogueLibrary(InternetLibrary):
 def __init__(self,*args,**kwargs):
  super().__init__(*args,**kwargs);self.catalogue={};self.downloaded={};self.active_downloads={};self.active_streams={};self.ready_streams={};self.cover_fallbacks={};self.download_index=self.root/'catalog_downloads.json';self._read_download_index()
  self.cover_failures={};self.cover_gates=[Lock() for _ in range(16)]
  self._provider_python=None
  self.search_cache=OrderedDict();self.search_cache_ttl=120;self.search_cache_limit=64
 @property
 def provider_python(self):
  from backend.worker_runtime import worker_python
  return self._provider_python or worker_python('provider')
 @provider_python.setter
 def provider_python(self,value):self._provider_python=value
 def provider_status(self):
  try:
   child=subprocess.run([self.provider_python,'-m','backend.provider_worker'],cwd=Path(__file__).resolve().parent.parent,input=b'{"operation":"status"}',stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=4,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
   return json.loads(child.stdout.decode('utf-8'))
  except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired):return {'spotdl':None,'scdl':None,'ffmpeg':False,'error':'Проверьте OWNPLAYER_PROVIDER_PYTHON и requirements-download.txt.'}
 def _run_provider(self,key,payload,timeout):
  env={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE','LOCALAPPDATA','LANG','OWNPLAYER_FFMPEG','OWNPLAYER_DENO','OWNPLAYER_SPOTIFY_CLIENT_ID','OWNPLAYER_SPOTIFY_CLIENT_SECRET')};env['PYTHONIOENCODING']='utf-8';env['PYTHONUTF8']='1';env.update(self.network.worker_env())
  # Log to a file in a temporary directory, not an unbounded pipe buffer.
  with tempfile.TemporaryFile() as err:
   try:
    child=subprocess.Popen([self.provider_python,'-m','backend.provider_worker'],cwd=Path(__file__).resolve().parent.parent,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}))
   except (OSError,RuntimeError):raise ProviderError('Не удалось запустить Python загрузчиков. Проверьте OWNPLAYER_PROVIDER_PYTHON.') from None
   sent=False;began=monotonic();deadline=began+timeout
   from backend.provider_diagnostics import record,classify
   try:
    while not self._cancelled(key) and monotonic()<deadline:
     try:
      out,_=child.communicate(None if sent else json.dumps(payload).encode(),timeout=.2)
      if len(out)>300000:raise ProviderError('Ответ загрузчика слишком большой.')
      try:data=json.loads(out.decode('utf-8'))
      except (ValueError,UnicodeError):raise ProviderError('Загрузчик завершился без корректного ответа.') from None
      if not isinstance(data,dict):raise ProviderError('Некорректный ответ загрузчика.')
      if data.get('error'):
       err.seek(0);code=classify(err.read(64000).decode('utf-8','replace')+' '+str(data['error']));record(payload.get('provider'),payload.get('operation'),monotonic()-began,code)
       hints={'auth_required':'Источник запросил вход или проверку доступа. Публичное аудио не получено.','runtime_missing':'Загрузчику не хватает JavaScript runtime. Проверьте версии загрузчиков и их зависимости.','network':'Не удалось связаться с источником. Проверьте сеть.','proxy_auth':'Прокси запросил авторизацию. Укажите логин и пароль в настройках сети.','proxy_error':'Не удалось подключиться к прокси. Проверьте VPN, адрес и порт.','socks_missing':'Установите PySocks в окружение загрузчиков .providers.','ffmpeg_missing':'FFmpeg не найден. Проверьте комплектность приложения; для исходников используйте INSTALL.ps1.'}
       raise ProviderError(hints.get(code,str(data['error'])[:350])+' ['+code+']')
      record(payload.get('provider'),payload.get('operation'),monotonic()-began)
      return data
     except subprocess.TimeoutExpired:sent=True
     if err.tell()>2_000_000:raise ProviderError('Загрузчик остановлен: слишком много диагностических сообщений.')
     if payload.get('folder'):
      size=sum(p.stat().st_size for p in Path(payload['folder']).rglob('*') if p.is_file())
      if size>MAX_AUDIO*2:raise ProviderError('Превышен лимит временных файлов загрузчика.')
    record(payload.get('provider'),payload.get('operation'),monotonic()-began,'cancelled' if self._cancelled(key) else 'timeout')
    raise ProviderError('Операция отменена или загрузчик превысил время ожидания.')
   finally:
    if os.name=='nt':
     if child.poll() is None:
      try:subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
      except (OSError,subprocess.TimeoutExpired):pass
    else:
     import signal
     try:os.killpg(child.pid,signal.SIGKILL)
     except (ProcessLookupError,PermissionError):pass
    if child.poll() is None:
     try:child.kill()
     except OSError:pass
    try:child.communicate(timeout=2)
    except subprocess.TimeoutExpired:
     # Never wait indefinitely for a descendant retaining a pipe handle.
     for stream in (child.stdin,child.stdout):
      if stream:
       try:stream.close()
       except OSError:pass

 def _cache_key(self,query):
  # Credentials/URLs never enter diagnostics or on-disk cache. Network changes
  # invalidate the in-memory cache, including proxy credential changes.
  route=json.dumps(self.network.worker_env(),sort_keys=True)
  return (sha256(route.encode()).hexdigest(),self.network.config['search_first'],' '.join(query.split()).casefold())

 def clear_search_cache(self):
  with self.lock:self.search_cache.clear()

 def _catalogue_tracks(self,items):
  tracks=[];seen=set()
  for item in items[:50]:
   if not isinstance(item,dict):continue
   if item.get('kind','track')!='track':continue
   provider=item.get('provider');url=item.get('url')
   try:url=validate_track_url(url,provider)
   except (ValueError,TypeError,KeyError,AttributeError):continue
   ref='online:catalog:'+sha256(url.encode()).hexdigest()[:32]
   if ref in seen:continue
   seen.add(ref)
   track={'path':ref,'source':provider,'title':str(item.get('title',''))[:1000],'artist':str(item.get('artist',''))[:500],'cover_url':self.cached_cover_url(item.get('cover_url')),'cover_source':provider,'metadata_source':provider,'duration_ms':item.get('duration_ms'),'album':None,'access':'prepare','download_available':True,'permalink_url':url}
   if provider=='youtube' and track['cover_url']:
    from urllib.parse import parse_qs
    import re
    u=urlparse(url);video=u.path.strip('/') if u.hostname=='youtu.be' else parse_qs(u.query).get('v',[''])[0]
    if re.fullmatch(r'[A-Za-z0-9_-]{11}',video):self.cover_fallbacks[track['cover_url'].rsplit('/',1)[-1][:-4]]='https://i.ytimg.com/vi/'+video+'/hqdefault.jpg'
   with self.lock:
    self.catalogue[ref]=track
    while len(self.catalogue)>1200:self.catalogue.pop(next(iter(self.catalogue)))
   tracks.append(track)
  return tracks

 def _catalogue_search(self,key,query,source):
  self._patch(key,status='running',stage='Параллельный поиск')
  pending={};rows=[];warnings=[];cache_key=None;tracks=[]
  try:
   from backend.provider_worker import HOSTS
   chosen=next((p for p,hosts in HOSTS.items() if urlparse(query).hostname in hosts),None)
   # Explicit URLs are case-sensitive; only text searches are normalized.
   cache_key=self._cache_key(query if not chosen else '')+(query if chosen else '',)
   with self.lock:
    cached=self.search_cache.get(cache_key)
    if cached and cached['until']>monotonic():
     items=deepcopy(cached['rows']);self.search_cache.move_to_end(cache_key)
    else:
     items=None;self.search_cache.pop(cache_key,None)
   if items is not None:
    tracks=self._catalogue_tracks(items)
    if tracks:self._patch(key,status='done',tracks=tracks,cache_hit=True);return
   providers=[chosen] if chosen else self.network.search_order()
   for provider in providers:
    if self._cancelled(key):return
    future=self.provider_search_pool.submit(self._run_provider,key,{'operation':'search','provider':provider,'query':query},10)
    pending[future]=provider
   while pending:
    if self._cancelled(key):return
    done,_=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
    # Order simultaneous completions deterministically; never wait for a slow
    # preferred provider before publishing another provider's usable results.
    for future in sorted(done,key=lambda f:providers.index(pending[f])):
     provider=pending.pop(future)
     try:
      batch=future.result()
      if not isinstance(batch,dict) or not isinstance(batch.get('tracks',[]),list):raise ProviderError('Некорректный ответ каталога.')
      warnings.extend(str(w)[:350] for w in batch.get('warnings',[])[:12])
      rows.extend(batch.get('tracks',[])[:50]);tracks=self._catalogue_tracks(rows)
     except Exception as error:
      warnings.append(provider+': '+(str(error)[:350] if isinstance(error,ProviderError) else 'Источник недоступен.'))
     self._patch(key,tracks=tracks,warnings=warnings,stage='Найдено: '+str(len(tracks))+(' · поиск продолжается' if pending else ''))
   if self._cancelled(key):return
   if tracks:
    with self.lock:
     self.search_cache[cache_key]={'until':monotonic()+self.search_cache_ttl,'rows':deepcopy(rows[:50])}
     self.search_cache.move_to_end(cache_key)
     while len(self.search_cache)>self.search_cache_limit:self.search_cache.popitem(last=False)
    self._patch(key,status='done',tracks=tracks,warnings=warnings,cache_hit=False);return
  except Exception:
   warnings.append('Сбой каталога. Используется резервный поиск.')
  finally:
   for future in pending:future.cancel()
  if not self._cancelled(key):super()._search(key,query,source)
 def _search(self,key,query,source):
  if source=='auto':return self._catalogue_search(key,query,source)
  return super()._search(key,query,source)
 def start_download(self,ref,confirmed,audio_format='original'):
  if audio_format not in ('original','mp3'):raise ProviderError('Неизвестный формат аудио.')
  if confirmed is not True:raise ProviderError('Не подтверждено действие скачивания.')
  if not ref.startswith('online:catalog:'):return super().start_download(ref,confirmed,audio_format)
  with self.lock:track=deepcopy(self.catalogue.get(ref))
  if not track:raise ProviderError('Запись устарела. Повторите поиск.')
  cache_ref=ref if audio_format=='original' else ref+'|mp3'
  cached=self.downloaded.get(cache_ref)
  if not cached and audio_format=='mp3':
   candidate=self.downloaded.get(ref)
   if candidate and Path(candidate).suffix.lower()=='.mp3':cached=candidate
  if cached and Path(cached).is_file():
   remember_cached_metadata(cached,track)
   key=self._new_job('cache');self._patch(key,status='done',path=cached,bytes=Path(cached).stat().st_size,audio_format=audio_format);return self.job(key)
  with self.job_lock:
   previous=self.active_downloads.get(cache_ref)
   if previous and self.jobs.get(previous,{}).get('status') in ('queued','running'):return self.job(previous)
   key=self._new_job('download');self.active_downloads[cache_ref]=key
   self.jobs[key]['future']=self.download_pool.submit(self._catalogue_download,key,track,audio_format)
  return self.job(key)
 def _catalogue_download(self,key,track,audio_format='original'):
  cache_ref=track['path'] if audio_format=='original' else track['path']+'|mp3'
  self._patch(key,status='running',stage='Подготовка аудио через '+('spotDL' if track['source']=='spotify' else 'SCDL' if track['source']=='soundcloud' and audio_format=='mp3' else 'yt-dlp'))
  try:
   cached=self.downloaded.get(cache_ref)
   if cached and Path(cached).is_file():
    remember_cached_metadata(cached,track);self._patch(key,status='done',path=cached,bytes=Path(cached).stat().st_size);return
   parent=self.root/'Downloads';parent.mkdir(parents=True,exist_ok=True)
   with tempfile.TemporaryDirectory(prefix='.ownplayer-',dir=parent) as folder:
    data=self._run_provider(key,{'operation':'download','provider':track['source'],'url':track['permalink_url'],'folder':folder,'audio_format':audio_format},240)
    candidate=Path(data.get('file',''))
    if candidate.is_symlink():raise ProviderError('Загрузчик вернул символическую ссылку.')
    file=candidate.resolve()
    if not file.is_relative_to(Path(folder).resolve()) or not file.is_file() or file.is_symlink() or file.suffix.lower() not in (('.mp3',) if audio_format=='mp3' else ('.mp3','.m4a','.opus','.ogg','.webm','.aac','.flac','.wav')) or file.stat().st_size>MAX_AUDIO:raise ProviderError('Некорректный результат загрузчика.')
    from backend.audio_validation import validate_audio_file
    try:validate_audio_file(file)
    except (OSError,ValueError):raise ProviderError('Загрузчик вернул повреждённое или неподдерживаемое аудио.') from None
    tag_status=write_missing_tags(file,track)
    with self.job_lock:
     if self._cancelled(key):return
     dest=publish_download(file,parent,track);self.downloaded[cache_ref]=str(dest.resolve());self._write_download_index();self._patch(key,status='done',path=str(dest.resolve()),bytes=dest.stat().st_size,audio_format=audio_format,tags_status=tag_status)
  except ProviderError as error:self._patch(key,status='failed',error=str(error))
  except Exception:self._patch(key,status='failed',error='Не удалось запустить загрузчик. Установите requirements-download.txt и FFmpeg.')

 def cached_cover_url(self,url):
  url=cover_url(url)
  if not url:return None
  key=sha256(url.encode()).hexdigest()
  with self.lock:
   if not hasattr(self,'cover_sources'):self.cover_sources={}
   self.cover_sources[key]=url
   while len(self.cover_sources)>1000:self.cover_sources.pop(next(iter(self.cover_sources)))
  return '/api/assets/online-covers/'+key+'.jpg'

 def _fetch_cover_file(self,key):
  import re,io,ipaddress,socket
  from PIL import Image,ImageOps
  if not re.fullmatch(r'[a-f0-9]{64}',key):raise ProviderError('Обложка не найдена.')
  folder=self.root/'online-covers';path=folder/(key+'.jpg')
  if path.is_file():return path
  with self.lock:url=getattr(self,'cover_sources',{}).get(key)
  if not url:raise ProviderError('Обложка устарела. Повторите поиск.')
  for _ in range(4):
   if not cover_url(url):raise ProviderError('Неподдерживаемый CDN обложки.')
   try:
    if not self.network.resolve():
     addresses=socket.getaddrinfo(urlparse(url).hostname,443,type=socket.SOCK_STREAM)
     if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ProviderError('Небезопасный адрес обложки.')
    response=self.web.send(self.web.build_request('GET',url,timeout=5),stream=True)
    if response.status_code in (301,302,303,307,308):
     from urllib.parse import urljoin
     location=response.headers.get('location','');response.close();url=urljoin(url,location);continue
    try:
     if response.status_code!=200:
      error=ProviderError('CDN не вернул обложку.');error.code='image_http_'+str(response.status_code);raise error
     data=bytearray()
     for chunk in response.iter_bytes():
      data.extend(chunk)
      if len(data)>4*1024*1024:raise ProviderError('Обложка больше 4 МБ.')
    finally:response.close()
    with Image.open(io.BytesIO(data)) as original:
     if original.width*original.height>16_000_000:raise ProviderError('Обложка слишком большая.')
     image=ImageOps.exif_transpose(original).convert('RGB');image.thumbnail((512,512));folder.mkdir(parents=True,exist_ok=True);temp=folder/(uuid4().hex+'.tmp')
     try:image.save(temp,'JPEG',quality=86);os.replace(temp,path)
     finally:temp.unlink(missing_ok=True);image.close()
    files=sorted(folder.glob('*.jpg'),key=lambda p:p.stat().st_mtime)
    for old in files[:-300]:old.unlink(missing_ok=True)
    return path
   except ProviderError:raise
   except Exception as cause:
    import socket
    code=('image_connect_timeout' if isinstance(cause,httpx.ConnectTimeout) else
          'image_read_timeout' if isinstance(cause,httpx.ReadTimeout) else
          'image_dns' if isinstance(cause,socket.gaierror) else
          'image_connect_error' if isinstance(cause,httpx.ConnectError) else
          'image_decode_error' if isinstance(cause,(OSError,ValueError)) else 'image_unavailable')
    error=ProviderError('Не удалось получить изображение от провайдера.');error.code=code;raise error from None
  raise ProviderError('Слишком много переходов CDN.')

 def enrich_tracks(self,key,tracks):
  candidates=[t for t in tracks if t.get('source') in ('hitmo','muzbomb','archive') and not t.get('cover_url')][:12]
  if not candidates or self._cancelled(key):return tracks
  self._patch(key,stage='Обложки и метаданные')
  try:
   data=self._run_provider(key,{'operation':'enrich','tracks':[{'path':t['path'],'title':t['title'],'artist':t.get('artist','')} for t in candidates]},15)
   updates={t['path']:t for t in data.get('tracks',[])}
   for track in tracks:
    update=updates.get(track['path'])
    if not update:continue
    url=self.cached_cover_url(update.get('cover_url'))
    if url:
     track['cover_url']=url;track['cover_source']='spotify';track['metadata_source']='spotify';track['metadata_url']=update.get('metadata_url')
     with self.lock:
      if track['path'] in self.public:self.public[track['path']].update({k:track[k] for k in ('cover_url','cover_source','metadata_source','metadata_url')})
  except Exception:pass # Absence of a verified match must not become a fabricated cover.
  return tracks

 def cover_file(self,key):
  import re
  from backend.provider_diagnostics import record
  if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{64}',key):raise ProviderError('Обложка не найдена.')
  # Fixed-size lock striping: concurrent requests for the same cover share one fetch.
  with self.cover_gates[int(key[:2],16)%len(self.cover_gates)]:
   with self.lock:failed_until=self.cover_failures.get(key,0)
   if failed_until>monotonic():raise ProviderError('CDN обложки временно недоступен. Повторите попытку через 15 секунд.')
   began=monotonic()
   try:return self._fetch_cover_file(key)
   except ProviderError as error:
    code=getattr(error,'code','image_unavailable')
    # A different size at the same CDN won't fix a network timeout; don't double the delay.
    with self.lock:
     alternative=None if code in ('image_connect_timeout','image_read_timeout','image_dns','image_connect_error') else self.cover_fallbacks.pop(key,None)
     if alternative:self.cover_sources[key]=alternative
    if alternative:
     try:return self._fetch_cover_file(key)
     except ProviderError as second:code=getattr(second,'code','image_unavailable')
    with self.lock:
     self.cover_failures[key]=monotonic()+15
     while len(self.cover_failures)>1000:self.cover_failures.pop(next(iter(self.cover_failures)))
    record('artwork','cover',monotonic()-began,code)
    raise ProviderError('Не удалось загрузить обложку. Причина без адресов записана в диагностике; повторите попытку через 15 секунд.') from None

 def _read_download_index(self):
  try:
   data=json.loads(self.download_index.read_text('utf-8'))
   if not isinstance(data,dict):return
   for ref,name in data.items():
    if not isinstance(ref,str) or not ref.startswith('online:catalog:') or not isinstance(name,str) or Path(name).name!=name:continue
    file=(self.root/'Downloads'/name).resolve()
    if file.parent==(self.root/'Downloads').resolve() and file.suffix.lower() in ('.mp3','.m4a','.opus','.ogg','.webm','.aac','.flac','.wav') and file.is_file():self.downloaded[ref]=str(file)
  except (OSError,ValueError,TypeError):pass
 def _write_download_index(self):
  try:self._atomic(self.download_index,{ref:Path(path).name for ref,path in self.downloaded.items()})
  except OSError:
   from backend.provider_diagnostics import record
   record(None,'download',0,'cache_index_write_failed')
 def _with_cover_proxy(self,item):
  data=deepcopy(item);url=data.get('cover_url')
  if isinstance(url,str) and url.startswith('https://'):data['cover_url']=self.cached_cover_url(url)
  return data
 def search(self,source,query):
  return [self._with_cover_proxy(row) for row in super().search(source,query)]
 def imported(self):
  rows=super().imported()
  for row in rows:
   row['tracks']=[self._with_cover_proxy(track) for track in row.get('tracks',[])]
   if row.get('cover_url'):row.update(self._with_cover_proxy(row))
  return rows
 def import_playlist(self,source,url):
  row=super().import_playlist(source,url)
  row['tracks']=[self._with_cover_proxy(track) for track in row.get('tracks',[])]
  return self._with_cover_proxy(row)
 def metadata(self,ref):
  with self.lock:track=self.catalogue.get(ref)
  return self._with_cover_proxy(track if track else super().metadata(ref))
 def play_url(self,ref):
  if ref.startswith('online:catalog:'):
   cached=self.downloaded.get(ref)
   if cached and Path(cached).is_file():return Path(cached).as_uri()
   with self.lock:ready=self.ready_streams.get(ref)
   if ready and ready['until']>monotonic():
    with self.lock:
     handle=token_urlsafe(24)
     self.media[handle]={'ref':ref,'resources':{'master':ready['url']},'headers':dict(ready.get('headers',{}))}
     while len(self.media)>8:self.media.popitem(last=False)
    return 'http://127.0.0.1:8765/api/online/media/'+handle+'/master'
   raise ProviderError('Поток устарел. Запустите трек из поиска снова.')
  return super().play_url(ref)
 def start_stream(self,ref):
  with self.lock:track=deepcopy(self.catalogue.get(ref))
  if not track or track['source']!='youtube':raise ProviderError('Быстрый поток доступен для результатов YouTube. Для остальных используется подготовка файла.')
  cached=self.downloaded.get(ref)
  if cached and Path(cached).is_file():
   key=self._new_job('cache');self._patch(key,status='done',path=cached);return self.job(key)
  with self.job_lock:
   previous=self.active_streams.get(ref)
   if previous and self.jobs.get(previous,{}).get('status') in ('queued','running'):return self.job(previous)
   key=self._new_job('stream');self.active_streams[ref]=key
   self.jobs[key]['future']=self.stream_pool.submit(self._prepare_stream,key,track)
  return self.job(key)
 def _prepare_stream(self,key,track):
  self._patch(key,status='running',stage='Получение прямого аудиопотока')
  try:
   cached=self.downloaded.get(track['path'])
   if cached and Path(cached).is_file():self._patch(key,status='done',path=cached);return
   with self.lock:ready=self.ready_streams.get(track['path'])
   if ready and ready['until']>monotonic():self._patch(key,status='done',ref=track['path']);return
   data=self._run_provider(key,{'operation':'stream','provider':'youtube','url':track['permalink_url']},18)
   remote=self._validate_stream_url(data.get('stream_url'))
   if self._cancelled(key):return
   with self.lock:
    self.ready_streams[track['path']]={'url':remote,'until':monotonic()+STREAM_CACHE_SECONDS,'headers':self._stream_headers(data.get('http_headers'))}
    while len(self.ready_streams)>32:self.ready_streams.pop(next(iter(self.ready_streams)))
   self._patch(key,status='done',ref=track['path'])
  except ProviderError as e:self._patch(key,status='failed',error=str(e))
  except Exception:self._patch(key,status='failed',error='Прямой поток недоступен. Попробуйте скачать запись.')

 @staticmethod
 def _stream_headers(values):
  if not isinstance(values,dict):return {}
  return {k:v for k,v in values.items() if isinstance(k,str) and k.lower() in ('user-agent','referer') and isinstance(v,str) and len(v)<=2048 and '\r' not in v and '\n' not in v}

 def _validate_stream_url(self,url):
  import socket,ipaddress
  try:
   if not isinstance(url,str):raise ValueError()
   u=urlparse(url)
   if u.scheme!='https' or not (u.hostname or '').endswith('.googlevideo.com') or u.username or u.password or u.port not in (None,443):raise ValueError()
  except (TypeError,ValueError):raise ProviderError('Неподдерживаемый аудиопоток.') from None
  # With a configured proxy, DNS belongs to that route. The CDN allowlist still applies.
  if self.network.resolve():return url
  try:addresses=socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM)
  except OSError:raise ProviderError('Не удалось определить адрес аудиоисточника.') from None
  if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ProviderError('Небезопасный адрес потока.')
  return url

 def open_media(self,url,range_header=None):
  if not (urlparse(url).hostname or '').endswith('.googlevideo.com'):return super().open_media(url,range_header)
  import re
  from urllib.parse import urljoin
  from backend.provider_diagnostics import record
  began=monotonic()
  # Only internally registered resources can carry resolver headers.
  with self.lock:
   sessions=[v for v in self.media.values() if url in v['resources'].values() and v['ref'].startswith('online:catalog:')]
   if not sessions:raise ProviderError('Сессия потока истекла. Запустите трек снова.')
   headers=self._stream_headers(sessions[-1].get('headers'))
  headers['Accept-Encoding']='identity'
  if range_header and re.fullmatch(r'bytes=\d*-\d*',range_header):headers['Range']=range_header
  for _ in range(4):
   self._validate_stream_url(url)
   try:response=self.web.send(self.web.build_request('GET',url,headers=headers,timeout=8),stream=True)
   except httpx.HTTPError:
    record('youtube','stream',monotonic()-began,'stream_network')
    raise ProviderError('Аудиоисточник не ответил. Попробуйте подготовку файла.') from None
   if response.status_code in (301,302,303,307,308):
    location=response.headers.get('location','');response.close()
    if not location:raise ProviderError('Аудиоисточник вернул пустой переход.')
    url=urljoin(url,location);continue
   if response.status_code not in (200,206):
    code=response.status_code;response.close();record('youtube','stream',monotonic()-began,'stream_http_'+str(code))
    raise ProviderError('Источник отказал в доступе к аудиопотоку (HTTP '+str(code)+').')
   content=response.headers.get('content-type','').lower()
   if not content.startswith(('audio/','video/mp4','video/webm','application/octet-stream')):
    response.close();record('youtube','stream',monotonic()-began,'stream_not_audio')
    raise ProviderError('Источник вернул не аудиофайл.')
   return response,url
  raise ProviderError('Слишком много переходов аудиоисточника.')
