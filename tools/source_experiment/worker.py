"""Fixed metadata-only adapters, one NDJSON request at a time; no download operation."""
import argparse
import asyncio
import contextlib
from hashlib import sha256
from importlib import metadata, util, import_module
import itertools
import json
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request

# -I excludes script directories; only this trusted folder is restored.
if __package__:
    from .common import (ADAPTERS, MAX_BODY, MAX_CARDS, MAX_LINE, Failure, card, checked_url, classify, duration_ms, http_failure, msoc_card, proxy_url, query_text, safe_text)
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import (ADAPTERS, MAX_BODY, MAX_CARDS, MAX_LINE, Failure, card, checked_url, classify, duration_ms, http_failure, msoc_card, proxy_url, query_text, safe_text)

PROTOCOL = sys.stdout
START = time.perf_counter()

def emit(event, **values):
    PROTOCOL.write(json.dumps({'event': event, **values}, ensure_ascii=False) + '\n')
    PROTOCOL.flush()


def package_info(name):
    try:
        d = metadata.distribution(name)
        raw = d.read_text('METADATA') or ''
        return {'version': safe_text(d.version,80), 'metadata_sha256': sha256(raw.encode()).hexdigest(),
                'license': safe_text(d.metadata.get('License-Expression') or d.metadata.get('License') or 'unverified', 120),
                'origin_assertion': 'installed metadata only; wheel hash and upstream commit unverified'}
    except metadata.PackageNotFoundError:
        return {'version': None, 'license': 'unverified', 'origin_assertion': 'not installed'}


def preflight(adapter, proxy=None):
    names = {'yt-dlp.youtube': ('yt-dlp',), 'msoc.hitmo': ('msoc', 'httpx'),
             'msoc.muzbomb': ('msoc', 'httpx'), 'archive.http': (),
             'ytmusicapi.catalogue': ('ytmusicapi','requests','urllib3','certifi','charset-normalizer','idna')}[adapter]
    packages = {name: package_info(name) for name in names}
    available = all(v['version'] for v in packages.values())
    reason = None
    if adapter == 'ytmusicapi.catalogue' and packages['ytmusicapi']['version'] not in (None,'1.12.3'):
        available, reason = False, 'dependency_version_mismatch'
    routing = ('explicit_proxy; environment and OS proxy settings ignored' if proxy
               else 'direct; environment proxies ignored; OS/VPN routing external')
    return {'adapter': adapter, 'available': available, 'reason': reason,
            'packages': packages, 'python': sys.version.split()[0],
            'mode': 'metadata_only', 'downloads_enabled': False,
            'routing': routing,
            'route_endpoint': proxy,
            'auth': 'public only; no credentials/cookies read', 'subscription': 'not checked'}

class QuietLogger:
    def debug(self, *_): pass
    def info(self, *_): pass
    def warning(self, *_): pass
    def error(self, *_): pass


class MusicCatalogue:
    def __init__(self, proxy=None):
        root = str(Path(__file__).resolve().parents[2])
        if root not in sys.path: sys.path.insert(0, root)
        from backend.catalogue_http import public_client
        from backend.ytmusic_catalogue import YTMusicCatalogue
        client, self.session = public_client(proxy=proxy)
        self.catalogue = YTMusicCatalogue(client)
    def search(self, query, publish):
        result = self.catalogue.search(query, kind='track', limit=MAX_CARDS)
        for item in result['items']:
            artists = ', '.join(c['name'] for c in item['artists'] if c.get('name'))
            row = card(item['title'], artists, item['ref']['provider_id'], item['public_page'],
                       item['duration_ms']/1000 if item['duration_ms'] else None)
            row['catalogue_item'] = item
            publish(row)
        return result['timings']
    def close(self): self.session.close()

class YouTube:
    def __init__(self, proxy=None):
        from yt_dlp import YoutubeDL
        self.client = YoutubeDL({'quiet': True, 'no_warnings': True, 'logger': QuietLogger(),
            'proxy': proxy or '', 'skip_download': True, 'extract_flat': True, 'lazy_playlist': True,
            'cachedir': False, 'socket_timeout': 8, 'retries': 0, 'extractor_retries': 0,
            'playlistend': MAX_CARDS, 'noprogress': True, 'remote_components': set(),
            'js_runtimes': {}, 'cookiefile': None})
    def search(self, query, publish):
        data = self.client.extract_info('ytsearch10:' + query, download=False)
        entries = (data or {}).get('entries') or []
        for item in itertools.islice(entries, MAX_CARDS):
            if not isinstance(item, dict): continue
            identifier = str(item.get('id') or '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier): continue
            artist = item.get('artist') or item.get('uploader') or item.get('channel') or ''
            publish(card(item.get('title', ''), artist, identifier,
                         'https://www.youtube.com/watch?v=' + identifier,
                         item.get('duration'), 'reported' if item.get('artist') else 'uploader_or_channel'))
    def close(self): self.client.close()

class Redirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 4
    max_repeats = 2
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl, 'archive.org')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

class Archive:
    def __init__(self, proxy=None):
        proxies = {'http': proxy, 'https': proxy} if proxy else {}
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler(proxies), Redirect())
    def get(self, url):
        checked_url(url, 'archive.org')
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'SlynorSourceExperiment/0.1', 'Accept': 'application/json'})
            with self.client.open(request, timeout=8) as response:
                if response.status != 200: raise http_failure(response.status)
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY: raise Failure('response_too_large')
                return json.loads(body)
        except urllib.error.HTTPError as error:
            raise http_failure(error.code) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, BaseException): raise error.reason from None
            raise Failure('network') from None
    def search(self, query, publish):
        words = ' '.join(re.findall(r'[\w-]+', query))[:180]
        params = {'q': 'mediatype:audio AND (title:("' + words + '") OR creator:("' + words + '"))',
                  'output': 'json', 'rows': 4, 'fl[]': 'identifier'}
        data = self.get('https://archive.org/advancedsearch.php?' + urllib.parse.urlencode(params))
        count = 0
        for row in data.get('response', {}).get('docs', [])[:4]:
            identifier = row.get('identifier', '')
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', identifier): continue
            info = self.get('https://archive.org/metadata/' + urllib.parse.quote(identifier, safe=''))
            meta = info.get('metadata') or {}
            artist = meta.get('creator') or ''
            if isinstance(artist, list): artist = ', '.join(map(str, artist))
            for f in info.get('files', []):
                if not isinstance(f, dict) or str(f.get('format', '')).lower() not in ('mp3', 'vbr mp3', '128kbps mp3', '64kbps mp3', 'flac', 'ogg vorbis'):
                    continue
                name = f.get('name')
                if not isinstance(name, str): continue
                result = card(f.get('title') or Path(name).stem, artist, identifier,
                              'https://archive.org/details/' + identifier, f.get('length'))
                result['file_name_fingerprint'] = sha256(name.encode()).hexdigest()
                result['license_claim'] = 'present_unverified' if meta.get('licenseurl') else 'not_reported'
                publish(result); count += 1
                if count >= MAX_CARDS: return
    def close(self): pass

class Msoc:
    def __init__(self, engine, proxy=None): self.engine = engine; self.proxy = proxy
    async def async_search(self, query, publish):
        import httpx
        module = import_module('msoc.engines.' + self.engine)
        host = 'hitmotop.com' if self.engine == 'hitmo' else 'muzbomb.net'
        self_engine = self.engine
        route = self.proxy or None
        class Session:
            def __init__(self, headers=None):
                self.client = httpx.AsyncClient(headers=headers, proxy=route,
                                                trust_env=False, timeout=8, follow_redirects=False)
            async def __aenter__(self): return self
            async def __aexit__(self, *_): await self.client.aclose()
            @contextlib.asynccontextmanager
            async def get(self, url):
                if self_engine == 'muzbomb' and url.startswith('https://muzbomb.net/?song='):
                    url='https://muzbomb.net/?'+urllib.parse.urlencode({'song':query})
                for redirect_count in range(4):
                    checked_url(url, host)
                    async with self.client.stream('GET', url) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get('location')
                            if not location: raise Failure('bad_redirect')
                            url = urllib.parse.urljoin(url, location)
                            continue
                        if response.status_code != 200: raise http_failure(response.status_code)
                        body = bytearray()
                        async for b in response.aiter_bytes():
                            body.extend(b)
                            if len(body) > MAX_BODY: raise Failure('response_too_large')
                        async def text(): return bytes(body).decode('utf-8', 'replace')
                        yield SimpleNamespace(status=response.status_code, text=text,
                                              raise_for_status=response.raise_for_status)
                        return
                raise Failure('too_many_redirects')
        if self.engine == 'hitmo': module.ClientSession = Session
        else: module.aiohttp = SimpleNamespace(ClientSession=Session)
        count = 0
        async for item in module.search(query):
            result = msoc_card(item)
            if result is not None:
                publish(result); count += 1
                if count >= MAX_CARDS: break
    def search(self, query, publish): asyncio.run(self.async_search(query, publish))
    def close(self): pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', choices=ADAPTERS, required=True)
    parser.add_argument('--proxy', default=None)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    proxy = proxy_url(args.proxy) if args.proxy else None
    details = preflight(args.adapter, proxy)
    if args.preflight:
        emit('preflight', **details); return 0
    emit('ready', **details)
    adapter = None
    try:
        while True:
            raw = sys.stdin.buffer.readline(MAX_LINE + 1)
            if not raw: break
            if len(raw) > MAX_LINE: emit('error', code='request_too_large'); return 1
            began = time.perf_counter(); count = 0; adapter_init_ms = None; stage = 'validate'; initialized = searched = None
            try:
                request = json.loads(raw)
                if set(request) != {'query'}: raise Failure('unsupported_operation')
                query = query_text(request['query'])
                def publish(result):
                    nonlocal count
                    if count >= MAX_CARDS: return
                    count += 1
                    emit('card', rank=count, elapsed_ms=round((time.perf_counter() - began)*1000, 3), card=result)
                # Third-party diagnostics are never written to protocol or reports.
                with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    stage = 'adapter_initialization'
                    if adapter is None:
                        initialized = time.perf_counter()
                        adapter = (YouTube(proxy) if args.adapter == 'yt-dlp.youtube'
                                   else MusicCatalogue(proxy) if args.adapter == 'ytmusicapi.catalogue'
                                   else Archive(proxy) if args.adapter == 'archive.http'
                                   else Msoc(args.adapter.split('.')[1], proxy))
                        adapter_init_ms = (time.perf_counter() - initialized)*1000
                    stage = 'search'
                    searched = time.perf_counter()
                    phases = adapter.search(query, publish)
                    adapter_search_ms = (time.perf_counter() - searched)*1000
                phases = phases if isinstance(phases, dict) else {}
                emit('done', cards=count, elapsed_ms=round((time.perf_counter() - began)*1000, 3), status='ok' if count else 'no_results',
                     adapter_init_ms=adapter_init_ms, adapter_search_ms=adapter_search_ms, **phases)
            except Exception as error:
                code, status = classify(error)
                if adapter_init_ms is None and initialized is not None: adapter_init_ms = (time.perf_counter()-initialized)*1000
                emit('error', code=code, http_status=status, elapsed_ms=round((time.perf_counter() - began)*1000, 3), failed_stage=stage,
                     adapter_init_ms=adapter_init_ms, adapter_search_ms=(time.perf_counter()-searched)*1000 if searched is not None else None)
    finally:
        if adapter:
            with contextlib.suppress(Exception): adapter.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
