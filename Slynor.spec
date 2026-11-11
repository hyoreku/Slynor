# -*- mode: python ; coding: utf-8 -*-
# Canonical Windows build; BUILD_WINDOWS.ps1 must use this file, not regenerate it.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH)
datas = [(str(root / 'frontend'), 'frontend')]
for name in ('__init__.py', 'provider_worker.py', 'msoc_worker.py', 'proxy_worker.py'):
    datas.append((str(root / 'backend' / name), 'backend'))
binaries = []
hiddenimports = collect_submodules('backend') + collect_submodules('player') + collect_submodules('uvicorn')
hiddenimports += ['backend.main', 'socksio', 'pystray._win32', 'webview.platforms.winforms']
for package in ('webview', 'av', 'pythonnet', 'clr_loader'):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
for package in ('fastapi', 'uvicorn', 'httpx', 'python-vlc', 'pystray'):
    datas += copy_metadata(package)

a = Analysis([str(root / 'start.py')], pathex=[str(root)], binaries=binaries,
    datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=['spotdl', 'scdl', 'yt_dlp', 'msoc'], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Slynor',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    icon=str(root / 'frontend/assets/icons/app.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Slynor')
