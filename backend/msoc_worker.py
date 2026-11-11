"""Optional public-page parsers run outside the audio/UI process, under a hard deadline."""
import asyncio
import importlib
import json
import sys
import contextlib
from types import SimpleNamespace
from urllib.parse import urlencode
from backend.proxy_worker import worker_proxy
import httpx

async def search(engine, query):
    if engine not in ('hitmo','muzbomb'): raise ValueError('Unsupported engine')
    module=importlib.import_module('msoc.engines.'+engine)
    # Use our explicit route instead of aiohttp's implicit/no-proxy default.
    class Session:
        def __init__(self,headers=None):self.client=httpx.AsyncClient(proxy=worker_proxy() or None,trust_env=False,headers=headers,timeout=6,follow_redirects=True,max_redirects=4)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):await self.client.aclose()
        @contextlib.asynccontextmanager
        async def get(self,url):
            if engine=='muzbomb' and url.startswith('https://muzbomb.net/?song='):
                url='https://muzbomb.net/?'+urlencode({'song':query})
            async with self.client.stream('GET',url) as response:
                data=bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>2*1024*1024:raise RuntimeError('Слишком большая страница источника.')
                async def text():return bytes(data).decode('utf-8',errors='replace')
                yield SimpleNamespace(status=response.status_code,text=text,raise_for_status=response.raise_for_status)
    if engine=='hitmo':module.ClientSession=Session
    else:module.aiohttp=SimpleNamespace(ClientSession=Session)
    rows=[]
    async with asyncio.timeout(7):
        async for item in module.search(query):
            rows.append({'title':str(item.title)[:1000],'artist':str(item.artist or '')[:500],'url':str(item.url)[:4096]})
            if len(rows)>=20: break
    return rows

if __name__=='__main__':
    try:
        data=json.loads(sys.stdin.read(2048))
        result={'tracks':asyncio.run(search(data['engine'],data['query']))}
    except ModuleNotFoundError: result={'tracks':[],'error':'Не установлен requirements-search.txt (Python 3.12–3.14).'}
    except TimeoutError: result={'tracks':[],'error':'Источник не ответил за 7 секунд.'}
    except Exception: result={'tracks':[],'error':'Источник недоступен или изменил формат страницы.'}
    sys.stdout.write(json.dumps(result,ensure_ascii=False))
