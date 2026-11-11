"""Process-local desktop bridge. Never expose native window objects in API JSON."""
from threading import RLock

_lock = RLock()
_controller = None

def register(controller):
    global _controller
    with _lock: _controller = controller

def invoke(action):
    with _lock: controller = _controller
    if controller is None:
        raise RuntimeError('Десктопное окно ещё не готово.')
    if action == 'status': return controller.status()
    if action not in ('show','mini','quit','hide_mini'):raise ValueError('Неизвестная команда окна.')
    controller.enqueue(action)
    return {'status':'ok'}

def get_controller():
    with _lock:return _controller
