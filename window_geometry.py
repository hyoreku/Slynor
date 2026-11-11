"""Work-area geometry; no Tk roots or hidden GUI event loops."""
import os

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
