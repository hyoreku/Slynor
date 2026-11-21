"""Work-area geometry; no Tk roots or hidden GUI event loops."""
import logging
import os
from time import monotonic

MINI_EXPANDED_SIZE = (360, 112)
MINI_COMPACT_SIZE = (120, 46)
MINI_EXPANDED_RADIUS = 14
MINI_COMPACT_RADIUS = 23
MINI_TRANSITION_SECONDS = .26


def configure_mini_window(window):
    """Clip the Windows host and return a function to switch its size."""
    form = getattr(window, "native", None)
    if os.name != "nt" or form is None:
        return lambda compact, animate=False: window.resize(*(MINI_COMPACT_SIZE if compact else MINI_EXPANDED_SIZE))

    import ctypes
    from ctypes import wintypes
    from System import Action

    create_region = ctypes.windll.gdi32.CreateRoundRectRgn
    create_region.argtypes = [ctypes.c_int] * 6
    create_region.restype = wintypes.HANDLE
    delete_region = ctypes.windll.gdi32.DeleteObject
    delete_region.argtypes = [wintypes.HANDLE]
    set_region = ctypes.windll.user32.SetWindowRgn
    set_region.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
    get_dpi = ctypes.windll.user32.GetDpiForWindow
    get_dpi.argtypes = [wintypes.HWND]
    get_dpi.restype = wintypes.UINT
    set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
    set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    from System.Windows.Forms import Screen, Timer

    previous = None
    timer = None
    transition = None

    def on_ui_thread(callback):
        if form.InvokeRequired:
            form.Invoke(Action(callback))
        else:
            callback()

    def scale():
        return (get_dpi(form.Handle.ToInt64()) or 96) / 96

    def shape(*_):
        nonlocal previous
        width, height = form.Width, form.Height
        if width <= 0 or height <= 0 or not form.IsHandleCreated:
            return
        handle = form.Handle.ToInt64()
        dpi_scale = scale()
        fraction = max(0, min(1, (height / dpi_scale - MINI_COMPACT_SIZE[1]) /
                              (MINI_EXPANDED_SIZE[1] - MINI_COMPACT_SIZE[1])))
        radius = MINI_COMPACT_RADIUS + (MINI_EXPANDED_RADIUS - MINI_COMPACT_RADIUS) * fraction
        diameter = min(width, height, max(1, round(2 * radius * dpi_scale)))
        size = (handle, width, height, diameter)
        if previous == size:
            return
        # The DWM frame and backdrop are composed separately from the client region.
        if previous is None or previous[0] != handle:
            for attribute, value in ((33, 1), (34, 0xFFFFFFFE), (38, 1)):
                data = wintypes.DWORD(value)
                set_attribute(handle, attribute, ctypes.byref(data), ctypes.sizeof(data))
        previous = size
        region = create_region(0, 0, width, height, diameter, diameter)
        if not region or not set_region(handle, region, True):
            previous = None
            if region:
                delete_region(region)
            logging.getLogger("ownplayer").warning("Could not round the mini-player window")
        # Windows owns the region after SetWindowRgn succeeds.

    def tick(*_):
        started, origin, target = transition
        elapsed = min(1.0, (monotonic() - started) / MINI_TRANSITION_SECONDS)
        progress = elapsed * elapsed * (3 - 2 * elapsed)
        form.SetBounds(*(round(a + (b - a) * progress) for a, b in zip(origin, target)))
        if elapsed == 1.0:
            timer.Stop()

    def install():
        nonlocal timer
        timer = Timer()
        timer.Interval = 15
        timer.Tick += tick
        form.Disposed += lambda *_: timer.Dispose()
        form.Resize += shape
        form.HandleCreated += shape
        if hasattr(form, "DpiChanged"):
            form.DpiChanged += shape
        shape()

    def resize(use_compact, animate=False):
        def apply():
            nonlocal transition
            if form.IsDisposed:
                return
            size = MINI_COMPACT_SIZE if use_compact else MINI_EXPANDED_SIZE
            dpi_scale = scale()
            width, height = (round(value * dpi_scale) for value in size)
            area = Screen.FromControl(form).WorkingArea
            x = max(area.Left, min(form.Left, area.Right - width))
            y = max(area.Top, min(form.Top, area.Bottom - height))
            origin = (form.Left, form.Top, form.Width, form.Height)
            target = (x, y, width, height)
            timer.Stop()
            if animate and form.Visible and origin != target:
                transition = (monotonic(), origin, target)
                timer.Start()
            else:
                form.SetBounds(*target)
                shape()
        on_ui_thread(apply)

    on_ui_thread(install)
    return resize

def work_area():
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        class MONITORINFO(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('rcMonitor',wintypes.RECT),('rcWork',wintypes.RECT),('dwFlags',wintypes.DWORD)]
        user=ctypes.windll.user32
        point=wintypes.POINT();user.GetCursorPos(ctypes.byref(point))
        user.MonitorFromPoint.argtypes=[wintypes.POINT,wintypes.DWORD];user.MonitorFromPoint.restype=wintypes.HMONITOR
        user.GetMonitorInfoW.argtypes=[wintypes.HMONITOR,ctypes.POINTER(MONITORINFO)]
        monitor=user.MonitorFromPoint(point,2)
        info=MONITORINFO();info.cbSize=ctypes.sizeof(info)
        if user.GetMonitorInfoW(monitor,ctypes.byref(info)):
            r=info.rcWork
            scale=1.0
            try:
                dx=wintypes.UINT();dy=wintypes.UINT()
                get_dpi=ctypes.windll.shcore.GetDpiForMonitor
                get_dpi.argtypes=[wintypes.HMONITOR,ctypes.c_int,ctypes.POINTER(wintypes.UINT),ctypes.POINTER(wintypes.UINT)]
                get_dpi.restype=ctypes.c_long
                if get_dpi(monitor,0,ctypes.byref(dx),ctypes.byref(dy))==0:scale=max(1.0,dx.value/96)
            except (AttributeError,OSError):pass
            return tuple(round(n/scale) for n in (r.left,r.top,r.right-r.left,r.bottom-r.top))
    return 0,0,1920,1080

def position(width,height,saved=None,top=False,area=None):
    x,y,w,h=area or work_area()
    if saved is not None:
        px,py=saved
        # Disconnected-monitor coordinates return to the currently active work area.
        if px+width<x or py+height<y or px>x+w or py>y+h:saved=None
    if saved is None:px=x+(w-width)//2;py=y+16 if top else y+(h-height)//2
    return max(x,min(int(px),x+max(0,w-width))),max(y,min(int(py),y+max(0,h-height)))
