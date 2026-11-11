"""Dependency-light worker proxy helpers; no GUI or httpx imports."""
import os,re
from urllib.parse import urlsplit,unquote,quote

# Worker helpers do not load user settings, guess ports, or copy browser credentials.
def worker_proxy():return os.environ.get('OWNPLAYER_PROXY','')
def requests_proxy():
    value=worker_proxy()
    # requests/PySocks: remote DNS for SOCKS5. httpx and yt-dlp already use proxy-side DNS.
    return value.replace('socks5://','socks5h://',1) if value.startswith('socks5://') else value

def safe_error(text):
    value=worker_proxy();text=str(text)
    if value:
        text=text.replace(value,'[proxy]')
        try:
            password=unquote(urlsplit(value).password or '')
            if password:text=text.replace(password,'[redacted]').replace(quote(password,safe=''),'[redacted]')
        except ValueError:pass
    return re.sub(r'(?i)(https?|socks5h?)://[^\s/@]+:[^\s/@]+@',r'\1://[redacted]@',text)

class SafeWriter:
    def __init__(self,stream):self.stream=stream
    def write(self,text):return self.stream.write(safe_error(text))
    def flush(self):return self.stream.flush()
    def __getattr__(self,name):return getattr(self.stream,name)
