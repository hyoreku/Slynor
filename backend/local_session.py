"""Process-local capability for browser/API commands; never written to user settings."""
from secrets import token_urlsafe
DESKTOP_HOST = '127.0.0.1'
DESKTOP_PORT = 8765
DEFAULT_DESKTOP_URL = f'http://{DESKTOP_HOST}:{DESKTOP_PORT}'
APP_TOKEN = token_urlsafe(32)
COOKIE_NAME = 'ownplayer_session'
