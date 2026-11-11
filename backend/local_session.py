"""Process-local capability for browser/API commands; never written to user settings."""
from secrets import token_urlsafe
APP_TOKEN = token_urlsafe(32)
COOKIE_NAME = 'ownplayer_session'
