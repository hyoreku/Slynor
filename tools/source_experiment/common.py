"""Shared bounded, privacy-preserving protocol for the search experiment."""
from hashlib import sha256
import ipaddress
import math
from pathlib import Path
import re
import socket
from urllib.parse import urlsplit

DEFAULT_ADAPTERS = ('yt-dlp.youtube', 'msoc.hitmo', 'msoc.muzbomb', 'archive.http')
ADAPTERS = (*DEFAULT_ADAPTERS, 'ytmusicapi.catalogue')
MAX_CARDS = 10
MAX_LINE = 65536
MAX_BODY = 2 * 1024 * 1024
MAX_PROXY_URL = 200
SEARCH_TIMEOUT = 30
ERROR_CODES = {'invalid_query','invalid_proxy','query_urls_not_allowed','blocked_url','blocked_redirect','blocked_address',
               'rate_limited','auth_required','access_denied','http_error','dependency_missing','timeout','dns','network',
               'adapter_error','response_too_large','bad_redirect','too_many_redirects','request_too_large','unsupported_operation',
               'worker_output_limit','worker_protocol_error','worker_exited','memory_limit','worker_io_error',
               'dependency_version_mismatch'}
DATASET_SHA256 = 'e681e4d20cc25910035e20c1090e7d3e4e396c5b5cf13d96536645ace283f04c'
ROOT = Path(__file__).resolve().parents[2]
SOURCE_FAMILY = {'yt-dlp.youtube': 'YouTube', 'ytmusicapi.catalogue': 'YouTube', 'msoc.hitmo': 'Hitmo; audio origin unverified',
                 'msoc.muzbomb': 'Muzbomb; audio origin unverified', 'archive.http': 'Internet Archive'}
DOWNLOADER_FAMILY = {'yt-dlp.youtube': 'yt-dlp', 'ytmusicapi.catalogue': 'none; metadata only', 'msoc.hitmo': 'msoc + research HTTP',
                     'msoc.muzbomb': 'msoc + research HTTP', 'archive.http': 'research HTTP'}

class Failure(Exception):
    def __init__(self, code, http_status=None):
        self.code, self.http_status = code, http_status
        super().__init__(code)  # Never embed a remote message, URL or credentials.


def query_text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise Failure('invalid_query')
    if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise Failure('invalid_query')
    if re.search(r'(?i)(?:https?|file|data|ftp):', value):
        raise Failure('query_urls_not_allowed')
    return value.strip()


def safe_text(value, limit=500):
    text = str(value or '')[:4000]
    text = re.sub(r'(?i)(?:https?|socks5h?)://\S+', '[url]', text)
    text = re.sub(r'\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b', '[redacted]', text)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,})\b', '[redacted]', text)
    text = re.sub(r'(?i)(?:bearer\s+|(?:token|secret|password|api_key)[=: ]+)[A-Za-z0-9_+./=-]+', '[redacted]', text)
    return ''.join(c if ord(c) >= 32 else ' ' for c in text)[:limit]


def duration_ms(value):
    try:
        n = float(value)
        return round(n * 1000) if math.isfinite(n) and 0 < n <= 7 * 86400 else None
    except (ValueError, TypeError):
        return None


def http_failure(status):
    if status == 429: return Failure('rate_limited', status)
    if status == 401: return Failure('auth_required', status)
    if status == 403: return Failure('access_denied', status)  # Not proof of geo/DRM.
    return Failure('http_error', status)


def classify(error):
    if isinstance(error, Failure): return error.code, error.http_status
    if getattr(error, 'code', None) in ERROR_CODES:
        return error.code, getattr(error, 'http_status', None)
    if isinstance(error, ModuleNotFoundError): return 'dependency_missing', None
    if isinstance(error, (TimeoutError, socket.timeout)): return 'timeout', None
    status = getattr(getattr(error, 'response', None), 'status_code', None)
    if isinstance(status, int): return http_failure(status).code, status
    # Used for classification only. The original message is never returned.
    text = str(error).lower()
    for words, code in [(('timed out', 'timeout'), 'timeout'),
                        (('name resolution', 'getaddrinfo', 'nodename nor servname'), 'dns'),
                        (('connection refused', 'network is unreachable', 'failed to establish', 'connecterror'), 'network'),
                        (('429', 'too many requests'), 'rate_limited'),
                        (('403', 'forbidden'), 'access_denied'),
                        (('sign in', 'login required', 'authentication required'), 'auth_required')]:
        if any(w in text for w in words): return code, None
    return 'adapter_error', None


def proxy_url(value):
    """An explicit operator route; the stand never reads environment or OS proxies.

    A proxy route has to be requested on the command line and is written to the
    manifest, because identical queries over different routes are not comparable.
    Only the endpoint is kept: credentials are rejected, not stored or reported.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_PROXY_URL:
        raise Failure('invalid_proxy')
    value = value.strip()
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').encode('idna').decode('ascii')
        valid = (parsed.scheme in ('http', 'https', 'socks5', 'socks5h')
                 and bool(host) and parsed.port is not None and 1 <= parsed.port <= 65535
                 and parsed.username is None and parsed.password is None
                 and parsed.path in ('', '/') and not parsed.query and not parsed.fragment)
        valid = valid and re.fullmatch(r'[A-Za-z0-9.\-:]+', host) is not None
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise Failure('invalid_proxy')
    authority = ('[' + host + ']') if ':' in host else host
    return parsed.scheme + '://' + authority + ':' + str(parsed.port)


def route_record(proxy=None):
    if proxy is None:
        return {'mode': 'direct', 'endpoint': None}
    return {'mode': 'proxy', 'endpoint': proxy_url(proxy)}


def checked_url(url, allowed_host, resolve=True):
    try:
        u = urlsplit(url)
        host = (u.hostname or '').lower()
        if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
            raise Failure('blocked_url')
        if host != allowed_host and not host.endswith('.' + allowed_host):
            raise Failure('blocked_redirect')
        if resolve:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise Failure('blocked_address')
        return url
    except (ValueError, TypeError):
        raise Failure('blocked_url') from None


def card(title, artist, source_id='', public_page=None, length=None, artist_kind='reported', fingerprint=None):
    return {'title': safe_text(title), 'artist': safe_text(artist), 'artist_kind': artist_kind,
            'source_id': safe_text(source_id, 180), 'public_page': public_page,
            'duration_ms': duration_ms(length), 'full_or_preview': 'unknown',
            'recording_version': 'unknown', 'resource_fingerprint': fingerprint}


def msoc_card(item):
    # URL is not opened or stored. Even a signed URL must never appear in exports.
    url = str(getattr(item, 'url', ''))
    try:
        u = urlsplit(url)
        valid = u.scheme == 'https' and not u.username and not u.password and u.port in (None, 443)
        host = (u.hostname or '').lower()
        valid = valid and any(host == h or host.endswith('.' + h) for h in ('hitmotop.com', 'muzbomb.net', 'mp3uk.net'))
    except ValueError:
        valid = False
    if not valid: return None
    return card(getattr(item, 'title', ''), getattr(item, 'artist', ''),
                fingerprint=sha256(url.encode()).hexdigest())


def safe_output_root(root=ROOT):
    destination = Path(root) / '.local-artifacts' / 'source-experiment' / 'runs'
    current = Path(root)
    for part in destination.relative_to(root).parts:
        current = current / part
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise Failure('output_reparse_point')
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def protocol_card(adapter, value):
    if not isinstance(value, dict): raise Failure('worker_protocol_error')
    result = card(value.get('title'),value.get('artist'),value.get('source_id'),
                  artist_kind=value.get('artist_kind') if value.get('artist_kind') in ('reported','uploader_or_channel') else 'unknown')
    length=value.get('duration_ms')
    if type(length) in (int,float) and math.isfinite(length) and 0 < length <= 604800000: result['duration_ms']=round(length)
    page=value.get('public_page')
    if page:
        pattern=r'https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}' if adapter=='yt-dlp.youtube' else r'https://music\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}' if adapter=='ytmusicapi.catalogue' else r'https://archive\.org/details/[A-Za-z0-9_.-]{1,160}' if adapter=='archive.http' else None
        if not pattern or not isinstance(page,str) or not re.fullmatch(pattern,page): raise Failure('worker_protocol_error')
        result['public_page']=page
    for key in ('resource_fingerprint','file_name_fingerprint'):
        digest=value.get(key)
        if isinstance(digest,str) and re.fullmatch(r'[a-f0-9]{64}',digest):result[key]=digest
    if value.get('license_claim') in ('present_unverified','not_reported'):result['license_claim']=value['license_claim']
    if adapter == 'ytmusicapi.catalogue':
        # Revalidate the normalized entity; never pass through arbitrary nested provider fields.
        raw = value.get('catalogue_item')
        if not isinstance(raw, dict) or not isinstance(raw.get('ref'), dict): raise Failure('worker_protocol_error')
        sys_root = str(ROOT)
        import sys
        if sys_root not in sys.path: sys.path.insert(0, sys_root)
        from backend.catalogue_contract import EntityRef, CatalogueError
        try: ref = EntityRef(**raw['ref'])
        except (CatalogueError, TypeError): raise Failure('worker_protocol_error') from None
        if ref.kind != 'track' or ref.provider_id != result['source_id'] or ref.public_page != result['public_page']:
            raise Failure('worker_protocol_error')
        # Export only a small reviewed subset. Availability is a catalogue claim, not an audio check.
        result['catalogue_kind'] = ref.kind
        result['catalogue_key'] = ref.key
        if raw.get('full_or_preview') == 'preview': result['full_or_preview'] = 'preview'
        result['catalogue_availability'] = raw.get('availability') if raw.get('availability') in ('reported_available','reported_unavailable') else 'unknown'
        result['title_version_hints'] = [v for v in raw.get('title_version_hints', []) if v in ('live','remix','cover','acoustic','remaster','slowed','sped_up')][:7]
        result['artist_ids'] = []
        for credit in raw.get('artists', [])[:20]:
            try: artist_ref = EntityRef(**credit['ref'])
            except (CatalogueError, TypeError, KeyError): continue
            if artist_ref.kind == 'artist': result['artist_ids'].append(artist_ref.provider_id)
    return result
