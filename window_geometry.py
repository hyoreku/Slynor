"""Work-area geometry; no Tk roots or hidden GUI event loops."""
import logging
import os


def round_mini_window(window):
    """Clip the Windows host itself so corners cannot expose its background."""
    form = getattr(window, "native", None)
    if os.name != "nt" or form is None:
        return

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

    previous = None

    def resize(*_):
        nonlocal previous
        width, height = form.Width, form.Height
        if width <= 0 or height <= 0 or not form.IsHandleCreated:
            return
        handle = form.Handle.ToInt64()
        diameter = max(1, round(24 * getattr(form, "DeviceDpi", 96) / 96))
        size = (handle, width, height, diameter)
        if previous == size:
            return
        previous = size
        region = create_region(0, 0, width + 1, height + 1, diameter, diameter)
        if not region or not set_region(handle, region, True):
            previous = None
            if region:
                delete_region(region)
            logging.getLogger("ownplayer").warning("Could not round the mini-player window")
        # Windows owns the region after SetWindowRgn succeeds.

    def install():
        form.Resize += resize
        form.HandleCreated += resize
        if hasattr(form, "DpiChanged"):
            form.DpiChanged += resize
        resize()

    if form.InvokeRequired:
        form.Invoke(Action(install))
    else:
        install()

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
