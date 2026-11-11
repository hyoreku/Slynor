"""Opt-in local preflight; never installs packages, opens URLs or reads user settings."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PACKAGES=('fastapi','uvicorn','pywebview','python-vlc','mutagen','Pillow','numpy','av','pystray','httpx','socksio')

def native_probe(root, runner=subprocess.run):
    code='''import json
from player.audio_engine import AudioEngine
e=AudioEngine()
try:
 d=e.output_devices()
 print(json.dumps({"status":"ok","enumeration_available":d.get("available",False),"device_count":len([v for v in d.get("devices",[]) if v.get("id")])}))
finally:e.close()
'''
    try:
        result=runner([sys.executable,'-c',code],cwd=root,capture_output=True,text=True,timeout=15)
        if result.returncode:return {'status':'native_failed'}
        data=json.loads(result.stdout.strip().splitlines()[-1])
        if data.get('status')!='ok':return {'status':'native_failed'}
        return {'status':'ok','enumeration_available':bool(data.get('enumeration_available')),'device_count':int(data.get('device_count',0))}
    except subprocess.TimeoutExpired:return {'status':'native_timeout'}
    except (OSError,ValueError,IndexError,TypeError):return {'status':'native_failed'}

def report(root, native=False):
    packages={}
    for name in PACKAGES:
        try:packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:packages[name]=None
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:
        try:
            configured=json.loads((root/'runtime_paths.json').read_text(encoding='utf-8-sig')).get('ffmpeg')
            if isinstance(configured,str) and Path(configured).name.lower() in ('ffmpeg','ffmpeg.exe') and Path(configured).is_file():ffmpeg=configured
        except (OSError,ValueError,AttributeError):pass
    data={'app':'Slynor','stage':20,'python':sys.version.split()[0],
          'packages':packages,'ffmpeg_found':bool(ffmpeg),'native':{'status':'not_requested'},
          'note':'No playback, network access, installation or source availability is tested.'}
    if native:data['native']=native_probe(root)
    return data

def main():
    parser=argparse.ArgumentParser(description='Slynor local dependency check; no installation')
    parser.add_argument('--native',action='store_true',help='Create LibVLC and enumerate outputs in a child process (15s limit); no audio is played')
    args=parser.parse_args();data=report(Path(__file__).resolve().parents[1],args.native)
    print(json.dumps(data,ensure_ascii=False,indent=2))
    return 1 if any(v is None for v in data['packages'].values()) or data['native']['status'] in ('native_failed','native_timeout') else 0
if __name__=='__main__':raise SystemExit(main())
