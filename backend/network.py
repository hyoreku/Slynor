"""Application-owned proxy policy. Passwords remain in memory, never in settings/reports."""
import json,os,re
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit,urlunsplit,quote,unquote
from urllib.request import getproxies
from uuid import uuid4
import httpx

class NetworkError(ValueError):pass

def endpoint(value,credentials=False):
    if not isinstance(value,str) or not value or len(value)>2048 or any(c.isspace() or ord(c)<32 for c in value):
        raise NetworkError('Нужен адрес HTTP/HTTPS/SOCKS5-прокси без пробелов.')
    try:
        u=urlsplit(value)
        if u.scheme not in ('http','https','socks5','socks5h') or not u.hostname or u.port is None or not 1<=u.port<=65535 or u.path not in ('','/') or u.query or u.fragment:raise ValueError()
        if not credentials and (u.username is not None or u.password is not None):
            raise NetworkError('Введите логин и пароль в отдельных полях: они не сохраняются на диск.')
        host=u.hostname.encode('idna').decode('ascii')
        if not re.fullmatch(r'[A-Za-z0-9.\-:]+',host):raise ValueError()
        authority=('['+host+']' if ':' in host else host)+':'+str(u.port)
        if credentials and u.username is not None:
            authority=quote(unquote(u.username),safe='')+(':'+quote(unquote(u.password or ''),safe=''))+'@'+authority
        return urlunsplit((u.scheme,authority,'','',''))
    except NetworkError:raise
    except (ValueError,UnicodeError):raise NetworkError('Некорректный адрес прокси. Укажите протокол, адрес и порт.') from None

def public_endpoint(value):
    if not value:return None
    u=urlsplit(value);host=u.hostname or ''
    return u.scheme+'://'+('['+host+']' if ':' in host else host)+':'+str(u.port)

def registry_proxies():
    import urllib.request
    reader=getattr(urllib.request,'getproxies_registry',None)
    return reader() if reader else {}

def system_proxy():
    try:
        values=getproxies()
        # urllib may return only NO_PROXY and then omit the Windows registry entirely.
        if not any(values.get(k) for k in ('https','all','http')):values=registry_proxies()
    except OSError:raise NetworkError('Не удалось прочитать системный прокси Windows.') from None
    raw=values.get('https') or values.get('all') or values.get('http')
    if not raw:return None
    if '://' not in raw:raw='http://'+raw
    return endpoint(raw,credentials=True)

class NetworkSettings:
    def __init__(self,root):
        self.path=Path(root)/'network.json';self.lock=RLock();self.auth=None;self.problem=False;self.last_checks=[]
        self.config={'version':1,'mode':'system','url':'','search_first':'auto'}
        try:
            if self.path.exists():
                data=json.loads(self.path.read_text('utf-8'))
                self.config=self.validate(data)
        except (OSError,ValueError,TypeError):self.problem=True
    @staticmethod
    def validate(data):
        if not isinstance(data,dict) or data.get('mode') not in ('system','manual','direct') or data.get('search_first','auto') not in ('auto','youtube','soundcloud','spotify'):raise NetworkError('Некорректный режим сети.')
        if data.get('version',1)!=1:raise NetworkError('Неизвестная версия настроек сети.')
        value=data.get('url','')
        if not isinstance(value,str):raise NetworkError('Адрес прокси должен быть строкой.')
        if value:value=endpoint(value)
        if data['mode']=='manual' and not value:raise NetworkError('Для ручного режима укажите адрес прокси.')
        return {'version':1,'mode':data['mode'],'url':value,'search_first':data.get('search_first','auto')}
    def resolve(self):
        with self.lock:
            if self.problem:raise NetworkError('network.json повреждён. Настройте сеть заново; файл не перезаписан автоматически.')
            mode=self.config['mode'];value=self.config['url'];auth=self.auth
        if mode=='direct':return None
        if mode=='system':return system_proxy()
        if auth:
            u=urlsplit(value);value=urlunsplit((u.scheme,quote(auth[0],safe='')+':'+quote(auth[1],safe='')+'@'+u.netloc,'','',''))
        return value
    def view(self):
        with self.lock:result=dict(self.config);result['credentials_present']=bool(self.auth);result['configuration_error']=self.problem
        try:
            proxy=self.resolve();result.update(proxy_detected=bool(proxy),effective=public_endpoint(proxy),error=None)
        except NetworkError:
            result.update(proxy_detected=False,effective=None,error='Не удалось прочитать прокси. Проверьте настройки сети.')
        result['system_note']='Системный режим читает HTTP(S)/ALL_PROXY и статический прокси Windows. PAC/WPAD и расширения браузера автоматически не поддерживаются.'
        return result
    def apply(self,data,username=None,password=None,clear_credentials=False):
        config=self.validate(data)
        for value in (username,password):
            if value is not None and (not isinstance(value,str) or len(value)>512 or any(ord(c)<32 for c in value)):raise NetworkError('Некорректные данные авторизации прокси.')
        with self.lock:
            auth=None if clear_credentials or config['url']!=self.config['url'] else self.auth
            if username is not None or password is not None:
                auth=(username or '',password or '') if username or password else None
            # Probe construction only: fail visibly if SOCKS support hasn't been installed.
            candidate=NetworkSettings.__new__(NetworkSettings);candidate.lock=RLock();candidate.problem=False;candidate.config=config;candidate.auth=auth
            proxy=candidate.resolve()
            try:
                client=httpx.Client(proxy=proxy,trust_env=False);client.close()
            except ImportError:raise NetworkError('Не установлена поддержка SOCKS. Обновите requirements.txt в .venv.') from None
            except (ValueError,httpx.HTTPError):raise NetworkError('Не удалось применить настройки прокси.') from None
            self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_name('network.'+uuid4().hex+'.tmp')
            try:temp.write_text(json.dumps(config,ensure_ascii=False,indent=2),'utf-8');os.replace(temp,self.path)
            finally:temp.unlink(missing_ok=True)
            self.config=config;self.auth=auth;self.problem=False;self.last_checks=[]
        return self.view()
    def report(self):
        v=self.view()
        result={k:v[k] for k in ('mode','search_first','proxy_detected','configuration_error','credentials_present','error')}
        with self.lock:result['checks']=[dict(row) for row in self.last_checks]
        return result
    def check(self):
        from concurrent.futures import ThreadPoolExecutor
        from time import monotonic
        targets=[('youtube','https://www.youtube.com/robots.txt'),('artwork','https://i.ytimg.com/vi/PPIZ5SpWRGA/hqdefault.jpg'),('spotify','https://open.spotify.com/robots.txt'),('soundcloud','https://soundcloud.com/robots.txt')]
        def probe(item):
            name,url=item;began=monotonic();code='network_error'
            try:
                proxy=self.resolve()
                with httpx.Client(proxy=proxy,trust_env=False,follow_redirects=False,timeout=httpx.Timeout(6,connect=4)) as client:
                    with client.stream('GET',url) as response:code='http_'+str(response.status_code)
            except NetworkError:code='proxy_configuration'
            except ImportError:code='socks_dependency_missing'
            except httpx.ProxyError:code='proxy_error'
            except httpx.ConnectTimeout:code='connect_timeout'
            except httpx.ReadTimeout:code='read_timeout'
            except httpx.ConnectError:code='connection_error'
            except httpx.HTTPError:code='network_error'
            return {'service':name,'code':code,'elapsed_ms':round((monotonic()-began)*1000)}
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(probe,targets))
        with self.lock:self.last_checks=results
        return {'network':self.report(),'checks':results,'note':'HTTP-ответ означает доступность адреса, но не гарантирует поиск, авторизацию или воспроизведение любого трека.'}
    def worker_env(self):
        proxy=self.resolve() or ''
        # Explicit env is allowlisted by the parent, not inherited indiscriminately.
        return {'OWNPLAYER_PROXY':proxy,'OWNPLAYER_NETWORK_MODE':self.config['mode'],
                'HTTP_PROXY':proxy,'HTTPS_PROXY':proxy,'ALL_PROXY':proxy,
                'http_proxy':proxy,'https_proxy':proxy,'all_proxy':proxy,
                'NO_PROXY':'localhost,127.0.0.1,::1','no_proxy':'localhost,127.0.0.1,::1'}
    def search_order(self):
        first=self.config['search_first'];base=['spotify','youtube','soundcloud']
        return [first]+[p for p in base if p!=first] if first!='auto' else base

class RoutedClient:
    """Keep clients until shutdown so applying a setting doesn't close an in-flight stream."""
    def __init__(self,policy,timeout):self.policy=policy;self.timeout=timeout;self.clients={};self.lock=RLock()
    def _client(self):
        try:proxy=self.policy.resolve()
        except NetworkError:raise httpx.ProxyError('Проверьте настройки прокси в разделе «Технические».') from None
        with self.lock:
            if proxy not in self.clients:
                if len(self.clients)>=16:raise httpx.ProxyError('Слишком много изменений прокси. Перезапустите приложение.')
                try:self.clients[proxy]=httpx.Client(proxy=proxy,trust_env=False,timeout=self.timeout,follow_redirects=False)
                except ImportError:raise httpx.ProxyError('Установите httpx[socks] из requirements.txt.') from None
            return self.clients[proxy]
    def build_request(self,*args,**kwargs):return self._client().build_request(*args,**kwargs)
    def send(self,*args,**kwargs):return self._client().send(*args,**kwargs)
    def get(self,*args,**kwargs):return self._client().get(*args,**kwargs)
    def post(self,*args,**kwargs):return self._client().post(*args,**kwargs)
    def close(self):
        with self.lock:clients=list(self.clients.values());self.clients.clear()
        for client in clients:client.close()

