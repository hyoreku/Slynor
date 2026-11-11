"""Native tray + mini window. No audio engine lives here; all controls use the same API."""
import json
from pathlib import Path
from threading import RLock, Thread, Event
from queue import Queue, Empty, Full
from time import monotonic
import logging
from window_geometry import position
from backend.diagnostics import dump_stacks
from urllib.request import Request, build_opener, ProxyHandler
# Loopback controls must not inherit a Windows/HTTP_PROXY route.
urlopen=build_opener(ProxyHandler({})).open
import webview
from backend.local_session import APP_TOKEN
from PIL import Image, ImageDraw

def _tray_image(path=None):
    path=Path(path) if path else Path(__file__).resolve().parent/'frontend/assets/icons/tray.png'
    try:
        if path.stat().st_size>2*1024*1024:raise ValueError('Tray icon too large')
        with Image.open(path) as original:
            if original.format!='PNG' or max(original.size)>512:raise ValueError('Tray icon must be PNG <=512px')
            return original.convert('RGBA').resize((64,64),Image.Resampling.LANCZOS)
    except (OSError,ValueError,Image.DecompressionBombError):
        image=Image.new('RGBA',(64,64),'#19171f');draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((5,5,59,59),radius=14,fill='#8655eb');draw.text((25,20),'S',fill='white')
        return image

class DesktopShell:
    def __init__(self, window, api):
        self.window = window
        self.api = api
        self.mini = None
        self.creating_mini = False
        self.icon = None
        self.tray_ready = False
        self.tray_error = None
        self.exiting = False
        self.shutdown_event = Event()
        self.lock = RLock()
        self.main_visible=True;self.mini_visible=False
        self.main_mode="normal";self.return_mode="normal";self.main_minimized=False
        self.close_to_tray=api.load_settings().get('close_to_tray',True)
        self.commands=Queue(maxsize=24);self.pending=set();self.running_since=None
        self.worker=Thread(target=self._commands,name='native-window-commands',daemon=True);self.worker.start()
        self.watchdog=Thread(target=self._watchdog,name='native-window-watchdog',daemon=True);self.watchdog.start()

    def status(self):
        return {'tray_available':self.tray_ready,'tray_error':self.tray_error,
                'mini_open':self.mini is not None,'main_visible':self.main_visible,
                'mini_visible':self.mini_visible,'close_to_tray':self.close_to_tray,
                'native_busy':self.running_since is not None}

    def enqueue(self, action):
        with self.lock:
            if self.exiting:return
            if action in self.pending:return
            if self.commands.full():raise RuntimeError('Очередь команд окна занята.')
            self.pending.add(action);self.commands.put_nowait(action)

    def _commands(self):
        while True:
            try:action=self.commands.get(timeout=.5)
            except Empty:
                if self.exiting:return
                continue
            try:
                if action is None:return
                if self.exiting and action!='quit':continue
                self.running_since=monotonic()
                {'show':self.show_main,'mini':self.show_mini,'hide':self.hide_main,
                 'hide_mini':self.hide_mini,'quit':self.quit}[action]()
            except Exception:
                logging.getLogger('ownplayer').exception('Native command failed: %s',action)
            finally:
                self.running_since=None
                with self.lock:self.pending.discard(action)
                self.commands.task_done()

    def _watchdog(self):
        reported=False
        while not self.shutdown_event.wait(2):
            if self.exiting:return
            stalled=self.running_since is not None and monotonic()-self.running_since>10
            if stalled and not reported:
                logging.getLogger('ownplayer').error('Native command stalled >10 seconds');dump_stacks()
            reported=stalled
            value=getattr(self,'_pending_position',None)
            if value is not None:
                self._pending_position=None
                try:self.api.save_mini_position(*value)
                except Exception:logging.getLogger('ownplayer').exception('Could not save mini position')

    def notify_hidden(self, hidden):
        # No evaluate_js here: it can wait on the same WebView UI thread that
        # is waiting for a closing/restored event handler to return.
        self.main_visible=not hidden

    def hide_mini(self):
        if not self.tray_ready:
            self.show_main();return
        mini=self.mini
        if mini is not None:self.mini_visible=False;mini.hide()

    def show_main(self, *args):
        with self.lock:
            if self.exiting: return
            mini=self.mini
        # Do not hold state locks while the Windows UI thread is invoked.
        if mini is not None:
            self.mini_visible=False;mini.hide()
        self.window.show()
        # hide/show preserves normal bounds. Never restore a maximized window unconditionally.
        if self.return_mode=='maximized':self.window.maximize()
        elif self.return_mode=='fullscreen':
            if not getattr(self.window,'fullscreen',False):self.window.toggle_fullscreen()
        elif self.main_minimized:self.window.restore()
        self.main_minimized=False
        self.notify_hidden(False)

    def hide_main(self):
        # Refresh this for tray hides too, not only mini transitions. Otherwise
        # an old mini session can force a now-normal window back to maximized.
        if self.main_visible:
            self.return_mode='fullscreen' if getattr(self.window,'fullscreen',False) else self.main_mode
        self.notify_hidden(True); self.window.hide()

    def show_mini(self, *args):
        with self.lock:
            if self.exiting or self.creating_mini: return
            mini=self.mini
            if mini is None: self.creating_mini=True
        if self.main_visible:
            self.return_mode='fullscreen' if getattr(self.window,'fullscreen',False) else self.main_mode
        if mini is None:
            try:
                mini=webview.create_window('Slynor · Миниплеер',
                    url='http://127.0.0.1:8765/mini',width=360,height=112,
                    min_size=(320,108),on_top=True,frameless=True,easy_drag=False,
                    x=position(360,112,self.api.load_settings().get('mini_position'),top=True)[0],
                    y=position(360,112,self.api.load_settings().get('mini_position'),top=True)[1])
                def closed(*args):
                    with self.lock:
                        if self.mini is mini: self.mini=None
                    if not self.exiting:self.enqueue('show')
                mini.events.closed += closed
                if hasattr(mini.events,'moved'):
                    def moved(x=None,y=None,*args):
                        # Use event coordinates. Window.x/y call back into the native UI thread.
                        if isinstance(x,(int,float)) and isinstance(y,(int,float)):
                            self._pending_position=(int(x),int(y))
                    mini.events.moved += moved
                if hasattr(mini.events,'minimized'):
                    mini.events.minimized += lambda *args:setattr(self,'mini_visible',False)
                if hasattr(mini.events,'restored'):
                    mini.events.restored += lambda *args:setattr(self,'mini_visible',True)
                with self.lock:
                    exiting=self.exiting
                    if not exiting:self.mini=mini
                if exiting:mini.destroy();return
            finally:
                with self.lock:self.creating_mini=False
        else:
            mini.show();mini.restore()
        self.mini_visible=True
        self.hide_main()

    def closing(self, *args):
        if self.exiting:return True
        if self.tray_ready and self.close_to_tray:self.enqueue('hide')
        else:self.enqueue('quit')
        return False

    def quit(self, *args):
        with self.lock:
            if self.exiting: return
            self.exiting = True
            self.shutdown_event.set()
            # Wake an idle command worker without joining the native UI thread.
            try:self.commands.put_nowait(None)
            except Full:pass
            mini=self.mini;self.mini=None
        self.stop_tray()
        if mini is not None: mini.destroy()
        self.window.destroy()

    def stop_tray(self, *args):
        icon = self.icon; self.icon = None; self.tray_ready = False
        if icon is not None: icon.stop()

    def audio_command(self, action):
        def run():
            try:
                if action == 'toggle':
                    with urlopen(Request('http://127.0.0.1:8765/api/player/state', headers={'X-OwnPlayer-Token':APP_TOKEN}), timeout=3) as response:
                        state = json.load(response)['state']
                    if state not in ('playing','paused'): return
                    command = 'pause' if state == 'playing' else 'resume'
                else: command = action
                request = Request('http://127.0.0.1:8765/api/player/'+command, method='POST',headers={'X-OwnPlayer-Token':APP_TOKEN})
                with urlopen(request, timeout=5) as response: response.read(4096)
            except Exception:
                # Make normal UI playback errors visible rather than a console-only traceback.
                self.enqueue('show')
        Thread(target=run, name='tray-command',daemon=True).start()

    def start_tray(self):
        try:
            import pystray
            image = _tray_image()
            menu = pystray.Menu(
                pystray.MenuItem('Открыть Slynor', lambda *args:self.enqueue('show'), default=True),
                pystray.MenuItem('Миниплеер', lambda *args:self.enqueue('mini')),
                pystray.MenuItem('Скрыть миниплеер',lambda *args:self.enqueue('hide_mini')),
                pystray.MenuItem('Пауза / Продолжить', lambda *args:self.audio_command('toggle')),
                pystray.MenuItem('Следующий трек', lambda *args:self.audio_command('next')),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem('Выйти', lambda *args:self.enqueue('quit')),
            )
            self.icon = pystray.Icon('Slynor',image,'Slynor',menu)
            def setup(icon):
                icon.visible = True
                self.tray_ready = True
            self.icon.run_detached(setup=setup)
        except Exception:
            self.tray_error = 'Системный трей недоступен. Закрытие окна завершает приложение.'
            self.stop_tray()
