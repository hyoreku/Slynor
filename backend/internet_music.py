"""Bounded public search/download jobs. No credentials/DRM bypass or silent match replacement."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from threading import Event, RLock
from time import monotonic
from urllib.parse import quote, urljoin, urlparse
from uuid import uuid4

import httpx
from backend.online_music import OnlineLibrary, ProviderError, SoundCloud
from backend.network import NetworkSettings, RoutedClient
from backend.worker_runtime import worker_available
from backend.download_metadata import publish_download, write_missing_tags
from backend.service_client import ServiceError, ServiceUnavailable

PUBLIC_HOSTS=('archive.org','hitmotop.com','muzbomb.net','mp3uk.net')
PUBLIC_REF=re.compile(r'^online:web:[a-f0-9]{32}$')
MAX_AUDIO=200*1024*1024
# Auto search keeps asking other public sources until this many playable rows
# are collected or the time budget is spent; one lucky source is not enough.
SEARCH_MIN_PLAYABLE=20
SEARCH_BUDGET_SECONDS=25

def public_url(url, resolve=False):
    try:
        u=urlparse(url);host=(u.hostname or '').lower()
        if u.scheme!='https' or u.username or u.password or u.port not in (None,443) or not any(host==h or host.endswith('.'+h) for h in PUBLIC_HOSTS): raise ValueError()
        if resolve:
            addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses): raise ValueError()
    except (ValueError,TypeError,OSError): raise ProviderError('Источник вернул неподдерживаемый или небезопасный адрес аудио.') from None
    return url

class InternetLibrary(OnlineLibrary):
    def __init__(self,data_dir,provider=None,service=None):
        self.network=NetworkSettings(data_dir)
        super().__init__(data_dir,provider or SoundCloud(client=RoutedClient(self.network,httpx.Timeout(15,connect=5))))
        self.root=Path(data_dir);self.public=OrderedDict();self.pinned={};self.public_warning=None
        # Optional Music Service: its search returns stable candidate IDs and its
        # download stores a measured file that we copy over loopback.
        self.service=service;self.service_tracks=OrderedDict();self.service_lock=RLock()
        self.jobs=OrderedDict();self.job_lock=RLock();self.latest=None;self.stopping=Event()
        self.search_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='music-search')
        self.download_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='music-download')
        self.stream_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='music-stream')
        self.provider_search_pool=ThreadPoolExecutor(max_workers=3,thread_name_prefix='catalogue-search')
        self.web=RoutedClient(self.network,httpx.Timeout(8,connect=4))
        self.registry=self.root/'public_tracks.json'
        if self.registry.exists():
            try:
                data=json.loads(self.registry.read_text('utf-8'))
                if data.get('version')!=1 or not isinstance(data.get('tracks'),list) or len(data['tracks'])>2000: raise ValueError()
                for track in data['tracks']:
                    if not isinstance(track,dict) or not PUBLIC_REF.fullmatch(track.get('path','')) or not isinstance(track.get('title'),str) or not isinstance(track.get('artist'),str): raise ValueError()
                    public_url(track['_url']);self.pinned[track['path']]=track
                self.public.update(self.pinned)
            except (ValueError,TypeError,KeyError,OSError,ProviderError):
                self.public_warning='public_tracks.json повреждён: файл не перезаписывается.';self.public.clear();self.pinned.clear()

    def status(self):
        result=super().status()
        result['sources'].insert(0,{'id':'auto','name':'Все доступные источники','configured':True,'status':'ready',
            'message':'Автопоиск: SoundCloud (если настроен), публичные ссылки msoc (если установлен), Internet Archive. Нет гарантии найти любую песню. Скачивание — отдельно, по выбранной записи.'})
        result['sources'].append({'id':'archive','name':'Internet Archive','configured':True,'status':'ready','message':'Публичный аудиоархив. Поиск и MP3 без ключей. Проверяйте лицензию конкретной записи.'})
        result['warning']='; '.join(filter(None,[result.get('warning'),self.public_warning])) or None
        return result

    def metadata(self,ref):
        if PUBLIC_REF.fullmatch(ref):
            with self.lock:
                item=self.public.get(ref)
                if not item: raise ProviderError('Запись больше не доступна. Повторите поиск.')
                return {k:deepcopy(v) for k,v in item.items() if not k.startswith('_')}
        return super().metadata(ref)

    def register(self,title,artist,url,source,permalink=None,cover=None,license_url=None):
        public_url(url)
        ref='online:web:'+sha256(url.encode()).hexdigest()[:32]
        track={'path':ref,'source':source,'title':title[:1000],'artist':artist[:500],'album':None,'duration_ms':None,
               'cover_url':cover,'access':'playable','permalink_url':permalink,'download_available':True,
               'license_url':license_url,'error':None,'cover_error':None,'_url':url}
        with self.lock:
            self.public[ref]=track
            for key in list(self.public):
                if len(self.public)<=2400: break
                if key not in self.pinned: self.public.pop(key)
        return self.metadata(ref)

    def pin(self,ref):
        if not PUBLIC_REF.fullmatch(ref):
            # SC search records are not durable: require import for persistent SC playlists.
            raise ProviderError('Для SoundCloud используйте импорт плейлиста сервиса.')
        with self.lock:
            if self.public_warning: raise ProviderError(self.public_warning)
            if ref not in self.public: raise ProviderError('Повторите поиск записи.')
            if ref not in self.pinned and len(self.pinned)>=2000: raise ProviderError('Лимит сохранённых потоков: 2000.')
            updated={**self.pinned,ref:self.public[ref]};self._atomic(self.registry,{'version':1,'tracks':list(updated.values())});self.pinned=updated
        return {'ok':True}

    @staticmethod
    def _atomic(path,data):
        path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
        try: temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8');os.replace(temp,path)
        finally: temp.unlink(missing_ok=True)

    def resource(self,handle,resource):
        with self.lock:
            session=self.media.get(handle)
            if session and PUBLIC_REF.fullmatch(session['ref']):
                if resource!='master': raise ProviderError('Ресурс потока не найден.')
                track=self.public.get(session['ref'])
                if not track: raise ProviderError('Повторите поиск записи.')
                return public_url(track['_url'])
        return super().resource(handle,resource)

    def open_media(self,url,range_header=None):
        if not any((urlparse(url).hostname or '').endswith(h) for h in PUBLIC_HOSTS): return super().open_media(url,range_header)
        for _ in range(5):
            public_url(url,resolve=not bool(self.network.resolve()))
            try:
                headers={'Accept':'audio/*,application/octet-stream'}
                if range_header and re.fullmatch(r'bytes=\d+-\d*',range_header): headers['Range']=range_header
                response=self.web.send(self.web.build_request('GET',url,headers=headers),stream=True)
            except httpx.HTTPError: raise ProviderError('Аудиоисточник не ответил. Попробуйте другой результат.') from None
            if response.status_code in (301,302,303,307,308):
                location=response.headers.get('location');response.close()
                if not location: raise ProviderError('Некорректный переход аудиоисточника.')
                url=urljoin(url,location);continue
            if response.status_code not in (200,206):response.close();raise ProviderError('Аудиоисточник отказал в доступе.')
            content=response.headers.get('content-type','').lower()
            if 'text/' in content or 'json' in content or 'mpegurl' in content:
                response.close();raise ProviderError('Вместо аудио источник вернул страницу/неподдерживаемый поток.')
            return response,url
        raise ProviderError('Слишком много переходов аудиоисточника.')

    def _new_job(self,kind):
        with self.job_lock:
            active=sum(j['kind']==kind and j['status'] in ('queued','running') for j in self.jobs.values())
            if kind in ('download','stream') and active>=4: raise ProviderError('Очередь загрузки заполнена (4). Дождитесь завершения.')
            key=uuid4().hex;self.jobs[key]={'id':key,'kind':kind,'status':'queued','tracks':[],'warnings':[],'bytes':0,'cancel':Event()}
            for old in list(self.jobs):
                if len(self.jobs)<=24:break
                if self.jobs[old]['status'] not in ('queued','running'):self.jobs.pop(old)
            return key

    def job(self,key):
        with self.job_lock:
            job=self.jobs.get(key)
            if not job:raise ProviderError('Задание не найдено или устарело.')
            return {k:deepcopy(v) for k,v in job.items() if k not in ('cancel','future')}

    def cancel_job(self,key):
        with self.job_lock:
            job=self.jobs.get(key)
            if job and job['status'] in ('queued','running'):
                job['cancel'].set();job['status']='cancelled';job.get('future') and job['future'].cancel()
        return {'ok':True}

    def start_search(self,query,source='auto'):
        with self.job_lock:
            if self.latest:self.cancel_job(self.latest)
            key=self._new_job('search');self.latest=key
            self.jobs[key]['future']=self.search_pool.submit(self._search,key,query,source)
        return self.job(key)

    def _patch(self,key,**values):
        with self.job_lock:
            job=self.jobs.get(key)
            if job and job['status']!='cancelled':job.update(values)

    def _cancelled(self,key):
        with self.job_lock:
            job=self.jobs.get(key)
            return self.stopping.is_set() or job is None or job['cancel'].is_set()

    def _msoc(self,key,engine,query):
        # Subprocess cannot inherit service tokens. Hard stop even if parser hangs.
        env={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE','LOCALAPPDATA','LANG','PYTHONIOENCODING')};env['PYTHONIOENCODING']='utf-8';env.update(self.network.worker_env())
        from backend.worker_runtime import worker_python
        process=subprocess.Popen([worker_python('search'),'-m','backend.msoc_worker'],cwd=Path(__file__).resolve().parent.parent,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        deadline=monotonic()+9;payload=json.dumps({'engine':engine,'query':query}).encode();first=True
        try:
            while not self._cancelled(key) and monotonic()<deadline:
                try:
                    out,err=process.communicate(payload if first else None,timeout=.2)
                    if len(out)>160000: raise ProviderError('Слишком большой ответ парсера.')
                    data=json.loads(out.decode('utf-8'))
                    if data.get('error'):raise ProviderError(data['error'])
                    if not data.get('tracks') and err:raise ProviderError('Источник отклонил запрос или не вернул аудиоссылки.')
                    return data.get('tracks',[])
                except subprocess.TimeoutExpired:first=False
            raise ProviderError('Поиск отменён или источник не ответил за 9 секунд.')
        finally:
            if process.poll() is None:process.kill()
            process.communicate()

    def _archive(self,key,query):
        # Escape all advanced-search syntax supplied by the user.
        words=' '.join(re.findall(r'[\w\-]+',query,flags=re.UNICODE))[:160]
        if not words:return []
        response=self.web.get('https://archive.org/advancedsearch.php',params={'q':'mediatype:audio AND (collection:opensource_audio OR collection:netlabels OR collection:audio_music) AND (title:("'+words+'") OR creator:("'+words+'"))','output':'json','rows':6,'fl[]':'identifier'})
        response.raise_for_status();rows=[]
        deadline=monotonic()+20
        for doc in response.json().get('response',{}).get('docs',[])[:6]:
            if self._cancelled(key) or monotonic()>deadline:break
            identifier=doc.get('identifier')
            if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}',identifier):continue
            r=self.web.get('https://archive.org/metadata/'+quote(identifier,safe=''),timeout=4);r.raise_for_status();data=r.json();meta=data.get('metadata') or {}
            creator=meta.get('creator') or '';creator=', '.join(map(str,creator)) if isinstance(creator,list) else str(creator)
            files=[f for f in data.get('files',[]) if isinstance(f,dict) and str(f.get('format','')).lower() in ('vbr mp3','mp3','128kbps mp3','64kbps mp3') and isinstance(f.get('name'),str)]
            for file in files[:3]:
                if int(file.get('size') or 0)>MAX_AUDIO:continue
                url='https://archive.org/download/'+quote(identifier,safe='')+'/'+quote(file['name'],safe='/')
                rows.append(self.register(str(file.get('title') or Path(file['name']).stem),creator,url,'archive','https://archive.org/details/'+identifier,None,meta.get('licenseurl') if isinstance(meta.get('licenseurl'),str) else None))
        return rows

    def _search(self,key,query,source):
        self._patch(key,status='running');tracks=[];warnings=[]
        try:
            if source=='service':
                tracks,warnings=self._service_results(query)
                self._patch(key,status='done',tracks=tracks,warnings=warnings)
                return
            stages=[]
            if source in ('auto','soundcloud') and self.provider.configured():stages.append(('SoundCloud',lambda:self.search('soundcloud',query)))
            elif source=='soundcloud':raise ProviderError('SoundCloud требует ключи приложения.')
            if source=='vk':raise ProviderError('ВК не подключён. Официального доступа к аудио нет.')
            if source in ('auto','hitmo','muzbomb') and worker_available('search'):
                for engine in (('hitmo','muzbomb') if source=='auto' else (source,)):
                    def parse(engine=engine):
                        rows=[]
                        for item in self._msoc(key,engine,query):
                            try:rows.append(self.register(item['title'],item.get('artist') or '',item['url'],engine))
                            except ProviderError:continue
                        return rows
                    stages.append((engine,parse))
            if source in ('hitmo','muzbomb') and not worker_available('search'):raise ProviderError('Установите requirements-search.txt (Python 3.12–3.14).')
            if source in ('auto','archive'):stages.append(('Internet Archive',lambda:self._archive(key,query)))
            search_started=monotonic()
            for name,run in stages:
                if self._cancelled(key):return
                self._patch(key,stage=name)
                try:batch=run();tracks.extend(batch)
                except Exception as error:
                    warnings.append(name+': '+(str(error) if isinstance(error,ProviderError) else 'Источник не ответил корректно.'))
                self._patch(key,tracks=tracks,warnings=warnings)
                playable=sum(1 for track in tracks if track.get('access')=='playable')
                # An explicitly chosen source has one stage anyway; auto search
                # must not stop at the first source that returned a single track.
                if playable>=SEARCH_MIN_PLAYABLE or monotonic()-search_started>=SEARCH_BUDGET_SECONDS:break
            if hasattr(self,'enrich_tracks'):tracks=self.enrich_tracks(key,tracks)
            self._patch(key,status='done',tracks=tracks,warnings=warnings)
        except ProviderError as error:self._patch(key,status='failed',error=str(error))
        except Exception:self._patch(key,status='failed',error='Ошибка поиска. Повторите запрос.')

    def start_download(self,ref,confirmed,audio_format='original'):
        if audio_format not in ('original','mp3'):raise ProviderError('Неизвестный формат аудио.')
        if confirmed is not True:raise ProviderError('Подтвердите право скачать выбранную запись.')
        if ref.startswith('online:service:'):
            if self.service is None:raise ProviderError('Music Service не подключён.')
            with self.service_lock:track=deepcopy(self.service_tracks.get(ref))
            if not track:raise ProviderError('Запись устарела. Повторите поиск.')
            key=self._new_job('download')
            with self.job_lock:self.jobs[key]['future']=self.download_pool.submit(self._download_service,key,ref,track,audio_format)
            return self.job(key)
        with self.lock:
            track=deepcopy(self.public.get(ref))
        if not track:raise ProviderError('У этой записи нет публичной ссылки загрузки. Повторите поиск.')
        key=self._new_job('download')
        with self.job_lock:self.jobs[key]['future']=self.download_pool.submit(self._download,key,track)
        return self.job(key)

    def _service_results(self,query):
        if self.service is None:raise ProviderError('Music Service не подключён.')
        try:items,warnings=self.service.search(query,limit=10,timeout=75)
        except ServiceUnavailable:raise ProviderError('Music Service недоступен. Включите его или выберите локальный поиск.') from None
        except ServiceError as error:raise ProviderError(str(error)) from None
        tracks=[]
        for item in items:
            track=self._service_track(item)
            if track:tracks.append(track)
        return tracks,warnings

    def _service_track(self,item):
        if not isinstance(item,dict):return None
        candidate=item.get('candidate_id');provider=item.get('provider')
        if not isinstance(candidate,str) or not re.fullmatch(r'[a-f0-9]{32}',candidate):return None
        if provider not in ('youtube','spotify','soundcloud'):return None
        title=item.get('title')
        if not isinstance(title,str) or not title.strip():return None
        artist=item.get('artist') if isinstance(item.get('artist'),str) else ''
        duration=item.get('duration_ms')
        if type(duration) is not int or not 0<duration<=604800000:duration=None
        cover=item.get('cover_url')
        cover=cover if isinstance(cover,str) and 0<len(cover)<=2048 and cover.startswith('https://') else None
        page=item.get('public_url')
        page=page if isinstance(page,str) and 0<len(page)<=2048 and page.startswith('https://') else None
        track={'path':'online:service:'+candidate,'source':provider,'title':title.strip()[:1000],
               'artist':artist.strip()[:500],'album':None,'duration_ms':duration,'cover_url':cover,
               'access':'prepare','permalink_url':page,'download_available':True,'error':None,
               'cover_error':None,'via':'service'}
        with self.service_lock:
            self.service_tracks[track['path']]=dict(track)
            while len(self.service_tracks)>512:self.service_tracks.popitem(last=False)
        return track

    def _download_service(self,key,ref,track,audio_format):
        self._patch(key,status='running');folder=self.root/'Downloads';part=None
        try:
            if self.service is None:raise ProviderError('Music Service не подключён.')
            candidate=ref.split(':',2)[2]
            asset=self.service.download(candidate,audio_format=audio_format,timeout=600)
            asset_id=asset.get('asset_id');container=str(asset.get('container') or '')
            if not isinstance(asset_id,str) or not container:raise ProviderError('Music Service вернул неполные сведения о файле.')
            suffix='.'+container
            if suffix not in ('.mp3','.m4a','.opus','.ogg','.webm','.aac','.flac','.wav'):raise ProviderError('Music Service вернул неподдерживаемый контейнер.')
            folder.mkdir(parents=True,exist_ok=True);part=folder/(uuid4().hex+suffix)
            self.service.asset_content(asset_id,part,max_bytes=MAX_AUDIO)
            if self._cancelled(key):return
            if not part.is_file() or part.stat().st_size<=0:raise ProviderError('Music Service вернул пустой файл.')
            expected=asset.get('sha256')
            if isinstance(expected,str) and len(expected)==64:
                digest=sha256()
                with part.open('rb') as stream:
                    for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
                if digest.hexdigest()!=expected.lower():raise ProviderError('Файл Music Service не совпал по хешу.')
            tag_status=write_missing_tags(part,track,suffix=suffix)
            with self.job_lock:
                if self._cancelled(key):return
                dest=publish_download(part,folder,track,suffix=suffix)
                self._patch(key,status='done',path=str(dest.resolve()),title=track.get('title'),tags_status=tag_status,bytes=dest.stat().st_size)
        except ProviderError as error:self._patch(key,status='failed',error=str(error))
        except ServiceUnavailable as error:self._patch(key,status='failed',error=str(error))
        except ServiceError as error:self._patch(key,status='failed',error=str(error))
        except Exception:self._patch(key,status='failed',error='Не удалось сохранить аудио из Music Service.')
        finally:
            if part is not None:part.unlink(missing_ok=True)

    def _download(self,key,track):
        self._patch(key,status='running');folder=self.root/'Downloads';part=None;response=None
        try:
            folder.mkdir(parents=True,exist_ok=True);part=folder/(uuid4().hex+'.part');response,_=self.open_media(track['_url'])
            if response.status_code!=200:raise ProviderError('Источник вернул неполный файл.')
            try:declared=int(response.headers.get('content-length',0))
            except ValueError:declared=0
            if declared>MAX_AUDIO:raise ProviderError('Файл больше 200 МБ.')
            size=0;deadline=monotonic()+120
            with part.open('xb') as out:
                for chunk in response.iter_bytes(chunk_size=65536):
                    if self._cancelled(key):return
                    if monotonic()>deadline:raise ProviderError('Превышено время загрузки (120 секунд).')
                    size+=len(chunk)
                    if size>MAX_AUDIO:raise ProviderError('Файл больше 200 МБ.')
                    out.write(chunk);self._patch(key,bytes=size)
            if not size or (declared and size!=declared):raise ProviderError('Файл загрузился не полностью.')
            from mutagen.mp3 import MP3
            try:
                audio=MP3(part)
                if not audio.info or audio.info.length<=0:raise ValueError()
            except Exception:raise ProviderError('Загрузка не является корректным MP3. Файл не добавлен.') from None
            tag_status=write_missing_tags(part,track,suffix='.mp3')
            with self.job_lock:
                if self._cancelled(key):return
                dest=publish_download(part,folder,track,suffix='.mp3')
                self._patch(key,status='done',path=str(dest.resolve()),title=track['title'],tags_status=tag_status)
        except ProviderError as error:self._patch(key,status='failed',error=str(error))
        except Exception:self._patch(key,status='failed',error='Не удалось сохранить аудио. Проверьте сеть и доступ к папке Downloads.')
        finally:
            if response is not None:response.close()
            if part is not None:part.unlink(missing_ok=True)

    def close(self):
        self.stopping.set()
        with self.job_lock:
            for key in list(self.jobs):self.cancel_job(key)
        self.search_pool.shutdown(wait=True,cancel_futures=True)
        self.provider_search_pool.shutdown(wait=True,cancel_futures=True)
        self.stream_pool.shutdown(wait=True,cancel_futures=True)
        self.download_pool.shutdown(wait=True,cancel_futures=True)
        self.web.close();super().close()
