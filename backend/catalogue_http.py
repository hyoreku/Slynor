"""Public YouTube Music session, created only in the isolated optional worker."""
from importlib.metadata import version
from urllib.parse import urlsplit
from backend.catalogue_contract import CatalogueError

MAX_BODY = 2 * 1024 * 1024
MAX_PROXY_URL = 200


def proxy_endpoint(value):
    """An explicit research route; credentials, paths and credentials are refused."""
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_PROXY_URL:
        raise CatalogueError('invalid_proxy')
    value = value.strip()
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('http', 'https', 'socks5', 'socks5h')
                 and bool(parsed.hostname) and parsed.port is not None and 1 <= parsed.port <= 65535
                 and parsed.username is None and parsed.password is None
                 and parsed.path in ('', '/') and not parsed.query and not parsed.fragment)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise CatalogueError('invalid_proxy')
    return value


def public_session(proxy=None):
    import requests

    class Session(requests.Session):
        def request(self, method, url, **kwargs):
            try:
                parsed = urlsplit(url)
                allowed = parsed.scheme == 'https' and parsed.hostname == 'music.youtube.com' and not parsed.username and not parsed.password and parsed.port in (None, 443)
            except (ValueError, TypeError): allowed = False
            if not allowed: raise CatalogueError('blocked_url')
            if method.upper() not in ('GET', 'POST'): raise CatalogueError('unsupported_operation')
            kwargs.update(timeout=(3.05, 8), allow_redirects=False, stream=True, verify=True)
            response = super().request(method, url, **kwargs)
            try:
                if 300 <= response.status_code < 400: raise CatalogueError('blocked_redirect')
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > MAX_BODY: raise CatalogueError('response_too_large')
                response._content = bytes(body)
                response._content_consumed = True
                return response
            finally: response.close()

    session = Session()
    session.trust_env = False  # No environment proxy, netrc, browser credentials or auth files.
    if proxy is not None:
        endpoint = proxy_endpoint(proxy)
        session.proxies.update({'http': endpoint, 'https': endpoint})
    session.max_redirects = 0
    session.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))
    return session


def public_client(language='en', location='', proxy=None):
    if version('ytmusicapi') != '1.12.3': raise CatalogueError('dependency_version_mismatch')
    if language not in ('en', 'ru') or location not in ('', 'RU', 'US'):
        raise CatalogueError('invalid_locale')
    from ytmusicapi import YTMusic
    session = public_session(proxy)
    try:
        return YTMusic(auth=None, requests_session=session, proxies={}, language=language, location=location), session
    except BaseException:
        session.close(); raise
