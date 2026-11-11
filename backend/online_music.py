"""Official SoundCloud public search/playlists and authenticated full HLS proxy.
No scraping, preview playback, audio downloads or persisted credentials.
"""
from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from secrets import token_urlsafe
from threading import RLock
from time import monotonic
from urllib.parse import quote, urljoin, urlparse
from uuid import uuid4

import httpx

API = 'https://api.soundcloud.com'
AUTH = 'https://secure.soundcloud.com/oauth/token'
REFERENCE = re.compile(r'^online:soundcloud:([0-9]+|soundcloud:tracks:[0-9]+)$')

class ProviderError(RuntimeError): pass


def safe_remote(url, api_only=False):
    if not isinstance(url, str): raise ProviderError('Некорректный URL провайдера.')
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    allowed = host == 'api.soundcloud.com' or (not api_only and (host == 'sndcdn.com' or host.endswith('.sndcdn.com')))
    if parsed.scheme != 'https' or not allowed or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ProviderError('Провайдер вернул неподдерживаемый адрес потока.')
    return url


def artwork(url):
    if not isinstance(url, str): return None
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme == 'https' and host.endswith('.sndcdn.com') and not parsed.username and not parsed.password and parsed.port in (None,443): return url
    return None

class SoundCloud:
    def __init__(self, client=None):
        self.client = client or httpx.Client(timeout=httpx.Timeout(15,connect=5),follow_redirects=False)
        self.lock = RLock()
        self.access_token = None; self.refresh_token = None; self.expires = 0

    def close(self): self.client.close()
    def configured(self):
        return bool(os.environ.get('SOUNDCLOUD_ACCESS_TOKEN') or (os.environ.get('SOUNDCLOUD_CLIENT_ID') and os.environ.get('SOUNDCLOUD_CLIENT_SECRET')))

    def token(self, renew=False):
        with self.lock:
            if self.access_token and monotonic() < self.expires and not renew: return self.access_token
            client_id=os.environ.get('SOUNDCLOUD_CLIENT_ID'); secret=os.environ.get('SOUNDCLOUD_CLIENT_SECRET')
            external=os.environ.get('SOUNDCLOUD_ACCESS_TOKEN')
            if external and not (client_id and secret):
                if renew: raise ProviderError('Токен SoundCloud истёк. Нужна повторная авторизация.')
                return external
            if not client_id or not secret: raise ProviderError('SoundCloud не настроен: нужны ключи зарегистрированного приложения.')
            payload={'grant_type':'client_credentials'}
            if self.refresh_token: payload={'grant_type':'refresh_token','refresh_token':self.refresh_token}
            try:
                if payload['grant_type']=='refresh_token':
                    response=self.client.post(AUTH,data={**payload,'client_id':client_id,'client_secret':secret},headers={'Accept':'application/json'})
                else:
                    response=self.client.post(AUTH,data=payload,auth=(client_id,secret),headers={'Accept':'application/json'})
                # A consumed/revoked refresh token can be replaced using the registered app.
                if response.status_code in (400,401) and self.refresh_token:
                    self.refresh_token=None
                    response=self.client.post(AUTH,data={'grant_type':'client_credentials'},auth=(client_id,secret),headers={'Accept':'application/json'})
                if response.status_code!=200: raise ProviderError('SoundCloud не выдал токен. Проверьте доступ приложения и ключи.')
                data=response.json()
                token=data.get('access_token')
                if not isinstance(token,str) or not token: raise ProviderError('SoundCloud вернул некорректный токен.')
                self.access_token=token; self.refresh_token=data.get('refresh_token')
                self.expires=monotonic()+max(10,min(float(data.get('expires_in',3600))-60,7200))
                return token
            except (httpx.HTTPError,ValueError,TypeError): raise ProviderError('Не удалось авторизовать SoundCloud.') from None

    def get(self, endpoint, params=None):
        url=endpoint if endpoint.startswith('https://') else API+endpoint
        safe_remote(url,api_only=True)
        try:
            for attempt in range(2):
                response=self.client.get(url,params=params,headers={'Authorization':'OAuth '+self.token(renew=attempt==1),'Accept':'application/json'})
                if response.status_code==401 and attempt==0: continue
                if response.status_code==429: raise ProviderError('SoundCloud ограничил запросы. Попробуйте позже.')
                if response.status_code in (403,404): raise ProviderError('Ресурс SoundCloud недоступен для этого приложения.')
                if response.status_code!=200: raise ProviderError('Ошибка API SoundCloud.')
                return response.json()
        except (httpx.HTTPError,ValueError): raise ProviderError('SoundCloud не ответил корректно. Проверьте соединение.') from None

    def normalize(self, data):
        identifier=data.get('urn') or data.get('id')
        if isinstance(identifier,int): identifier=str(identifier)
        if not isinstance(identifier,str): raise ProviderError('В записи SoundCloud нет идентификатора.')
        # Current API can use numeric IDs or soundcloud:tracks URNs.
        identifier=identifier.removeprefix('urn:')
        ref='online:soundcloud:'+identifier
        if not REFERENCE.fullmatch(ref): raise ProviderError('Неподдерживаемый идентификатор SoundCloud.')
        user=data.get('user') or {}
        access=data.get('access')
        access=access if access in ('playable','preview','blocked') else 'unknown'
        title=data.get('title') or 'Недоступная запись SoundCloud'
        return {'path':ref,'source':'soundcloud','provider_id':identifier,'title':str(title)[:1000],
                'artist':str(user.get('username') or '')[:500],'album':None,
                'duration_ms':data.get('duration') if type(data.get('duration')) in (int,float) else None,
                'cover_url':artwork(data.get('artwork_url') or user.get('avatar_url')),
                'access':access,'permalink_url':data.get('permalink_url') if isinstance(data.get('permalink_url'),str) and data['permalink_url'].startswith('https://soundcloud.com/') else None,
                'error':None,'cover_error':None}

    def search(self, query):
        data=self.get('/tracks',{'q':query,'limit':40,'linked_partitioning':'true'})
        items=data.get('collection',[]) if isinstance(data,dict) else data
        if not isinstance(items,list): raise ProviderError('Некорректный ответ поиска SoundCloud.')
        return [self.normalize(item) for item in items if isinstance(item,dict)]

    def full_stream(self, ref):
        match=REFERENCE.fullmatch(ref)
        if not match: raise ProviderError('Некорректный трек SoundCloud.')
        identifier=quote(match.group(1),safe='')
        track=self.get('/tracks/'+identifier)
        # Mandatory authoritative re-check: cached search/import status is not permission.
        if not isinstance(track,dict) or track.get('access')!='playable':
            raise ProviderError('Полное прослушивание этого трека недоступно. Превью не используется.')
        data=self.get('/tracks/'+identifier+'/streams')
        if not isinstance(data,dict): raise ProviderError('Некорректный ответ потоков SoundCloud.')
        url=data.get('hls_aac_160_url') or data.get('hls_mp3_128_url')
        if not url: raise ProviderError('SoundCloud не предоставил полный HLS-поток. Превью не используется.')
        return safe_remote(url)

    def playlist(self, url):
        parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname not in ('soundcloud.com','www.soundcloud.com','on.soundcloud.com') or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ProviderError('Нужна HTTPS-ссылка на плейлист SoundCloud.')
        playlist=self.get('/resolve',{'url':url})
        if not isinstance(playlist,dict) or playlist.get('kind')!='playlist': raise ProviderError('Ссылка не относится к доступному плейлисту SoundCloud.')
        identifier=playlist.get('urn') or playlist.get('id')
        if not isinstance(identifier,(str,int)): raise ProviderError('Нет идентификатора плейлиста.')
        endpoint='/playlists/'+quote(str(identifier).removeprefix('urn:'),safe='')+'/tracks'
        items=[]; visited=set(); page_url=endpoint; params={'limit':200,'linked_partitioning':'true'}
        while page_url:
            if page_url in visited: raise ProviderError('SoundCloud вернул циклическую пагинацию.')
            visited.add(page_url)
            data=self.get(page_url,params);params=None
            batch=data.get('collection',[]) if isinstance(data,dict) else data
            if not isinstance(batch,list): raise ProviderError('Некорректный список песен SoundCloud.')
            for item in batch:
                if not isinstance(item,dict): raise ProviderError('Некорректная запись плейлиста. Импорт не сохранён.')
                items.append(self.normalize(item))
            if len(items)>5000: raise ProviderError('В плейлисте больше 5000 записей. Частичный импорт не сохраняется.')
            page_url=data.get('next_href') if isinstance(data,dict) else None
            if page_url: safe_remote(page_url,api_only=True)
        # Do not silently call a partial/inconsistent response a complete import.
        declared=playlist.get('track_count')
        if isinstance(declared,int) and declared>len(items):
            raise ProviderError('SoundCloud не вернул все записи плейлиста. Неполный импорт не сохранён.')
        return {'id':uuid4().hex,'source':'soundcloud','name':str(playlist.get('title') or 'SoundCloud')[:1000],
                'cover_url':artwork(playlist.get('artwork_url')),'source_url':url,'tracks':items}

class OnlineLibrary:
    def __init__(self, data_dir, provider=None):
        self.path=Path(data_dir)/'online_playlists.json';self.lock=RLock()
        self.provider=provider or SoundCloud();self.tracks=OrderedDict();self.playlists=[];self.warning=None
        if self.path.exists():
            try:
                data=json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('playlists'),list): raise ValueError()
                for playlist in data['playlists']:
                    if not isinstance(playlist,dict) or not isinstance(playlist.get('id'),str) or not isinstance(playlist.get('name'),str) or not isinstance(playlist.get('tracks'),list): raise ValueError()
                    for track in playlist['tracks']:
                        if not isinstance(track,dict) or not REFERENCE.fullmatch(track.get('path','')) or track.get('access') not in ('playable','preview','blocked','unknown'): raise ValueError()
                        if not isinstance(track.get('title'),str) or not isinstance(track.get('artist'),str): raise ValueError()
                        self.tracks[track['path']]=track
                self.playlists=data['playlists']
            except (ValueError,TypeError,OSError):
                self.warning='Не удалось прочитать online_playlists.json. Файл не будет перезаписан.'
                self.playlists=[];self.tracks.clear()
        self.media=OrderedDict()

    def status(self):
        return {'sources':[
            {'id':'soundcloud','name':'SoundCloud','configured':self.provider.configured(),
             'status':'configured' if self.provider.configured() else 'credentials_required',
             'message':'Для поиска и полного аудио нужны ключи зарегистрированного приложения SoundCloud.'},
            {'id':'vk','name':'ВК Музыка','configured':False,'status':'official_access_unconfirmed',
             'message':'Официальный доступ к поиску, полному аудио и импорту ВК не подтверждён. Интеграция не подключена.'}], 'warning':self.warning}

    def remember(self, tracks):
        with self.lock:
            for track in tracks: self.tracks[track['path']]=deepcopy(track)
            # Persisted playlist rows cannot be evicted while they remain imported.
            protected={t['path'] for p in self.playlists for t in p['tracks']}
            for key in list(self.tracks):
                if len(self.tracks)<=12000: break
                if key not in protected: del self.tracks[key]

    def metadata(self, ref):
        with self.lock:
            item=self.tracks.get(ref)
            if item is None: raise ProviderError('Запись интернет-трека не найдена. Повторите поиск или импорт.')
            return deepcopy(item)

    def search(self, source, query):
        if source!='soundcloud': raise ProviderError('ВК Музыка не подключена: официальный доступ к аудио не подтверждён.')
        tracks=self.provider.search(query);self.remember(tracks);return tracks

    def imported(self):
        with self.lock: return deepcopy(self.playlists)

    def import_playlist(self, source, url):
        if source!='soundcloud': raise ProviderError('Импорт ВК недоступен без официального доступа.')
        if self.warning: raise ProviderError(self.warning)
        playlist=self.provider.playlist(url)
        with self.lock:
            if len(self.playlists)>=100: raise ProviderError('Лимит: 100 импортированных плейлистов.')
            updated=[*self.playlists,playlist]
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_name('online.'+uuid4().hex+'.tmp')
            try:
                temp.write_text(json.dumps({'version':1,'playlists':updated},ensure_ascii=False,indent=2),encoding='utf-8')
                os.replace(temp,self.path)
            finally: temp.unlink(missing_ok=True)
            self.playlists=updated;self.remember(playlist['tracks'])
            return deepcopy(playlist)

    def play_url(self, ref):
        track=self.metadata(ref)
        if track['access']!='playable': raise ProviderError('Полное аудио этой записи недоступно. Превью не используется.')
        with self.lock:
            handle=token_urlsafe(24)
            self.media[handle]={'ref':ref,'resources':{'master':None}}
            while len(self.media)>8:self.media.popitem(last=False)
        return 'http://127.0.0.1:8765/api/online/media/'+handle+'/master'

    def resource(self, handle, resource):
        with self.lock:
            session=self.media.get(handle)
            if session is None or resource not in session['resources']: raise ProviderError('Сессия потока истекла. Запустите трек снова.')
            url=session['resources'][resource];ref=session['ref']
        if url is None:
            url=self.provider.full_stream(ref)
            with self.lock:
                if handle not in self.media: raise ProviderError('Сессия потока истекла.')
                self.media[handle]['resources']['master']=url
        return url

    def rewrite_playlist(self, handle, text, base):
        safe_remote(base)
        def replace(uri):
            url=safe_remote(urljoin(base,uri));key=sha256(url.encode()).hexdigest()[:32]
            with self.lock:
                session=self.media.get(handle)
                if session is None: raise ProviderError('Сессия потока истекла.')
                if len(session['resources'])>=4096 and key not in session['resources']: raise ProviderError('Лимит ресурсов потока. Запустите песню снова.')
                session['resources'][key]=url
            return '/api/online/media/'+handle+'/'+key
        lines=[]
        for line in text.splitlines():
            if line.startswith('#'):
                line=re.sub(r'URI="([^"]+)"',lambda m:'URI="'+replace(m.group(1))+'"',line)
            elif line.strip(): line=replace(line.strip())
            lines.append(line)
        return '\n'.join(lines)+'\n'

    def open_media(self, url, range_header=None):
        renewed=False
        for _ in range(6):
            safe_remote(url)
            headers={'Accept-Encoding':'identity'}
            if urlparse(url).hostname=='api.soundcloud.com': headers['Authorization']='OAuth '+self.provider.token()
            if range_header and re.fullmatch(r'bytes=\d*-\d*',range_header):headers['Range']=range_header
            try:
                response=self.provider.client.send(self.provider.client.build_request('GET',url,headers=headers),stream=True)
            except httpx.HTTPError: raise ProviderError('Не удалось получить аудиопоток SoundCloud.') from None
            if response.status_code in (301,302,303,307,308):
                location=response.headers.get('Location','');response.close()
                url=safe_remote(urljoin(url,location));continue
            if response.status_code==401 and urlparse(url).hostname=='api.soundcloud.com' and not renewed:
                response.close(); self.provider.token(renew=True); renewed=True; continue
            if response.status_code not in (200,206):
                response.close();raise ProviderError('Полный аудиопоток SoundCloud недоступен или истёк.')
            if response.headers.get('content-type','').split(';')[0]=='application/json':
                parts=[];size=0
                try:
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>2*1024*1024: raise ProviderError('Ответ потока слишком большой.')
                        parts.append(chunk)
                    data=json.loads(b''.join(parts))
                except (ValueError,UnicodeError): raise ProviderError('Некорректный ответ потока.') from None
                finally: response.close()
                if not isinstance(data,dict) or not isinstance(data.get('url'),str): raise ProviderError('В ответе нет адреса полного потока.')
                url=safe_remote(data['url']);continue
            return response,url
        raise ProviderError('Слишком много перенаправлений аудиопотока.')

    def close(self): self.provider.close()
