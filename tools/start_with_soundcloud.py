"""Private app credentials are prompted locally, never written to JSON/logs/CLI args."""
from getpass import getpass
import os
from pathlib import Path
import subprocess
import sys


def main():
    project=Path(__file__).resolve().parent.parent
    print('Нужны ключи собственного зарегистрированного приложения SoundCloud.')
    client_id=input('SoundCloud client_id: ').strip()
    client_secret=getpass('SoundCloud client_secret (ввод скрыт): ').strip()
    if not client_id or not client_secret:
        print('Запуск отменён: ключи не указаны.');return 1
    env=dict(os.environ,SOUNDCLOUD_CLIENT_ID=client_id,SOUNDCLOUD_CLIENT_SECRET=client_secret)
    env.pop('SOUNDCLOUD_ACCESS_TOKEN',None)
    try:
        return subprocess.call([sys.executable,str(project/'start.py')],cwd=project,env=env)
    finally:
        env.pop('SOUNDCLOUD_CLIENT_SECRET',None);env.pop('SOUNDCLOUD_CLIENT_ID',None)

if __name__=='__main__':
    try: raise SystemExit(main())
    except KeyboardInterrupt: raise SystemExit(130)
