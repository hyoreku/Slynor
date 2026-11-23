"""Build-time only: stage private CPython and native dependencies on Windows.

No end-user pip, winget, system Python or runtime executable downloads.
Inputs are developer-supplied official redistributables; never copy a venv.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def require_file(path):
    if not path.is_file():
        raise FileNotFoundError(path)


def extract_embed(archive, target):
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            relative = Path(item.filename)
            if relative.is_absolute() or '..' in relative.parts or ':' in item.filename or '\\' in item.filename:
                raise ValueError('Unsafe embeddable ZIP entry')
        bundle.extractall(target)
    require_file(target / 'python.exe')
    require_file(target / 'python313.zip')
    require_file(target / 'python313._pth')
    # Explicit isolated paths, relative to each private runtime. No user-site
    # imports or PYTHONPATH dependency, no registration in Windows.
    (target / 'python313._pth').write_text(
        'python313.zip\n.\nLib/site-packages\n../_internal\nimport site\n', encoding='ascii')


def check_inputs(native, embed, expected_hash, system_webview2=False):
    native, embed = Path(native), Path(embed)
    require_file(embed)
    if len(expected_hash) != 64 or any(c not in '0123456789abcdefABCDEF' for c in expected_hash):
        raise ValueError('Supply the official embeddable ZIP SHA256')
    with embed.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual.lower() != expected_hash.lower():
        raise ValueError('Embeddable ZIP hash mismatch')
    required = ('vlc/libvlc.dll', 'vlc/libvlccore.dll', 'ffmpeg/ffmpeg.exe',
                'ffmpeg/ffprobe.exe', 'deno/deno.exe')
    if not system_webview2:
        required += ('webview2/msedgewebview2.exe',)
    for relative in required:
        require_file(native / relative)
    dotnet = native / 'dotnet'
    config = json.loads((dotnet / 'Slynor.runtimeconfig.json').read_text(encoding='utf-8'))
    framework = config['runtimeOptions']['framework']
    version = framework['version']
    for relative in (f'host/fxr/{version}/hostfxr.dll',
                     f'shared/Microsoft.NETCore.App/{version}/coreclr.dll',
                     f'shared/Microsoft.WindowsDesktop.App/{version}/System.Windows.Forms.dll'):
        require_file(dotnet / relative)
    if not (native / 'vlc/plugins').is_dir() or not (native / 'licenses').is_dir():
        raise FileNotFoundError('Supply VLC plugins and redistributable licenses')
    with zipfile.ZipFile(embed) as archive:
        provided = set(archive.namelist())
        missing = {'python.exe', 'python313.dll', 'python313.zip', 'python313._pth'} - provided
        if missing:
            raise ValueError('Private Python is missing ' + ', '.join(sorted(missing)))


def prepare(root, output, native, embed, expected_hash, system_webview2=False):
    if os.name != 'nt' or sys.version_info[:2] != (3, 13):
        raise RuntimeError('Preparation requires Windows x64 and CPython 3.13')
    import struct
    if struct.calcsize('P') != 8:
        raise RuntimeError('x64 interpreter required')
    root, output, native, embed = (Path(p).resolve() for p in (root, output, native, embed))
    require_file(output / 'Slynor.exe')
    check_inputs(native, embed, expected_hash, system_webview2)
    for name in ('vlc', 'ffmpeg', 'deno', 'dotnet', 'webview2', 'licenses'):
        destination = output / name
        if destination.exists():
            shutil.rmtree(destination)
        if name == 'webview2' and system_webview2:
            continue
        shutil.copytree(native / name, destination)
    for folder, group in (('.providers', 'provider'), ('.search', 'search')):
        requirements = root / 'requirements' / 'validated' / f'{group}-win-py313.txt'
        runtime = output / folder
        if runtime.exists():
            shutil.rmtree(runtime)
        runtime.mkdir()
        extract_embed(embed, runtime)
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '--require-hashes',
                        '--target', str(runtime / 'Lib/site-packages'),
                        '-r', str(requirements)], check=True)
        code = ('import spotdl,scdl,yt_dlp,yt_dlp_ejs; import backend.provider_worker'
                if folder == '.providers' else 'import msoc,httpx; import backend.msoc_worker')
        subprocess.run([str(runtime / 'python.exe'), '-c', code],
                       cwd=output / '_internal', check=True, timeout=45)
    # Do not ship paths from the developer's workstation.
    (output / 'runtime_paths.json').unlink(missing_ok=True)
    webview_note = ('Requires Microsoft Edge WebView2 Runtime: https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download\n'
                    if system_webview2 else 'WebView2 Fixed Runtime is bundled.\n')
    (output / 'PORTABLE_README.txt').write_text(
        'Extract the entire Slynor folder and run Slynor.exe. Do not move the EXE alone.\n'
        'Python, .NET Desktop Runtime, VLC, FFmpeg and Deno are private bundled components.\n'
        + webview_note +
        'User data: %LOCALAPPDATA%\\OwnPlayerMusic (compatible with existing versions).\n'
        'Online services require Internet access and may impose access restrictions.\n', encoding='utf-8')
    print('Private runtime staging complete; clean-Windows GUI/audio acceptance is still required.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--output')
    parser.add_argument('--check-inputs', action='store_true')
    parser.add_argument('--native', required=True)
    parser.add_argument('--embed', required=True)
    parser.add_argument('--embed-sha256', required=True)
    parser.add_argument('--system-webview2', action='store_true')
    args = parser.parse_args()
    if args.check_inputs:
        check_inputs(args.native, args.embed, args.embed_sha256, args.system_webview2)
    elif not args.output:
        parser.error('--output is required when staging the package')
    else:
        prepare(args.root, args.output, args.native, args.embed, args.embed_sha256, args.system_webview2)
