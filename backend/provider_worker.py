"""Optional spotDL/SCDL/yt-dlp worker. One JSON request, bounded by parent process."""
import contextlib
import json
import itertools
import math
import os
import re
from pathlib import Path
import shutil
import sys
from urllib.parse import urlparse, parse_qs
from backend.proxy_worker import worker_proxy, requests_proxy, safe_error, SafeWriter

HOSTS={'spotify':('open.spotify.com',),'youtube':('www.youtube.com','youtube.com','music.youtube.com','youtu.be'),'soundcloud':('soundcloud.com','www.soundcloud.com')}
def validate_url(url,provider):
 if not isinstance(url,str) or provider not in HOSTS:raise ValueError('Unsupported provider URL')
 u=urlparse(url)
 if u.scheme!='https' or u.hostname not in HOSTS[provider] or u.username or u.password or u.port not in (None,443):raise ValueError('Unsupported provider URL')
 return url

def validate_track_url(url,provider):
 """A provider host alone does not make a channel or a search page a song."""
 validate_url(url,provider);u=urlparse(url);parts=u.path.strip('/').split('/')
 if provider=='youtube':
  if u.hostname=='youtu.be' and len(parts)==1:identifier=parts[0]
  elif u.path=='/watch':
   values=parse_qs(u.query).get('v',[]);identifier=values[0] if len(values)==1 else ''
  elif len(parts)==2 and parts[0] in ('shorts','embed'):identifier=parts[1]
  else:identifier=''
  if not re.fullmatch(r'[A-Za-z0-9_-]{11}',identifier):raise ValueError('Not an individual YouTube video')
  return 'https://www.youtube.com/watch?v='+identifier
 if provider=='spotify':
  if len(parts)!=2 or parts[0]!='track' or not re.fullmatch(r'[A-Za-z0-9]{1,100}',parts[1]):raise ValueError('Not a Spotify track')
  return 'https://open.spotify.com/track/'+parts[1]
 if len(parts)!=2 or not all(parts) or parts[1] in ('sets','tracks','albums','popular-tracks','reposts','likes'):
  raise ValueError('Not an individual SoundCloud track')
 return 'https://soundcloud.com/'+'/'.join(parts)

def search_row(item,provider):
 if not isinstance(item,dict) or 'entries' in item or item.get('_type') in ('playlist','multi_video','compat_list'):
  return None
 if item.get('ie_key') in ('YoutubeTab','YoutubePlaylist','YoutubeSearch','SoundcloudUser','SoundcloudSet'):
  return None
 url=item.get('webpage_url') or item.get('url')
 if provider=='youtube':
  identifier=item.get('id')
  if not url and isinstance(identifier,str):url='https://www.youtube.com/watch?v='+identifier
  elif isinstance(url,str) and re.fullmatch(r'[A-Za-z0-9_-]{11}',url):url='https://www.youtube.com/watch?v='+url
 try:url=validate_track_url(url,provider)
 except (ValueError,TypeError):return None
 if provider=='youtube' and item.get('id') is not None and item['id']!=parse_qs(urlparse(url).query)['v'][0]:
  return None
 thumbnails=item.get('thumbnails') or []
 thumbnail=next((t.get('url') for t in reversed(thumbnails) if isinstance(t,dict) and t.get('url')),None) if isinstance(thumbnails,list) else None
 duration=item.get('duration')
 duration=round(duration*1000) if type(duration) in (int,float) and math.isfinite(duration) and 0<duration<=604800 else None
 return {'provider':provider,'url':url,'kind':'track','title':str(item.get('title') or 'Без названия'),
         'artist':str(item.get('artist') or item.get('uploader') or item.get('channel') or ''),
         'cover_url':item.get('thumbnail') or thumbnail,'duration_ms':duration}

def spotify_init():
 from spotdl.utils.spotify import SpotifyClient
 cid=os.environ.get('OWNPLAYER_SPOTIFY_CLIENT_ID','');secret=os.environ.get('OWNPLAYER_SPOTIFY_CLIENT_SECRET','')
 SpotifyClient.init(client_id=cid,client_secret=secret,no_cache=True,headless=True,max_retries=1,use_official_api=bool(cid and secret))

def spotify_rows(query):
 from spotdl.types.song import Song
 spotify_init()
 if query.startswith('https://open.spotify.com/track/'):
  song=Song.from_url(query);return [{'provider':'spotify','url':song.url,'title':song.name,'artist':', '.join(song.artists),'cover_url':song.cover_url,'duration_ms':song.duration*1000}]
 if query.startswith('https://open.spotify.com/playlist/'):
  from spotdl.types.playlist import Playlist
  playlist=Playlist.from_url(query,fetch_songs=False)
  songs=[Song.from_url(url) for url in playlist.urls[:50]]
  return [{'provider':'spotify','url':s.url,'title':s.name,'artist':', '.join(s.artists),'cover_url':s.cover_url,'duration_ms':s.duration*1000} for s in songs]
 raw=Song.search(query)
 return [{'provider':'spotify','url':'https://open.spotify.com/track/'+t['id'],'title':t['name'],'artist':', '.join(a['name'] for a in t.get('artists',[])), 'cover_url':next(iter(t.get('album',{}).get('images',[])),{}).get('url'),'duration_ms':t.get('duration_ms')} for t in raw.get('tracks',{}).get('items',[])[:12]]

def ytdlp_rows(query,provider):
 from yt_dlp import YoutubeDL
 prefix='ytsearch8:' if provider=='youtube' else 'scsearch8:'
 options={'proxy':worker_proxy(),'quiet':True,'no_warnings':True,'skip_download':True,'extract_flat':True,'socket_timeout':6,'retries':0,'playlistend':12,'ignoreerrors':True,'noplaylist':False}
 with YoutubeDL({**options,**js_options()}) as y:
  data=y.extract_info(query if query.startswith('https://') else prefix+query,download=False)
  if not isinstance(data,dict):return []
  # An empty result container must remain empty, never become [container].
  entries=data.get('entries') if 'entries' in data else [data]
  rows=[]
  for item in itertools.islice(entries or [],12):
   row=search_row(item,provider)
   if row:rows.append(row)
 return rows

def search(query,chosen=None):
 warnings=[];providers=['spotify','youtube','soundcloud']
 if chosen in HOSTS:providers=[chosen]
 if query.startswith(('http://','https://')):
  matched=next((p for p,hosts in HOSTS.items() if urlparse(query).hostname in hosts),None)
  if not matched:raise RuntimeError('Поддерживаются только ссылки Spotify, YouTube и SoundCloud.')
  validate_url(query,matched);providers=[matched]
 for provider in providers:
  try:
   rows=spotify_rows(query) if provider=='spotify' else ytdlp_rows(query,provider)
   if rows:return {'tracks':rows,'warnings':warnings}
  except Exception:warnings.append(provider+': поиск недоступен; требуется доступ к сервису или обновление библиотеки.')
 return {'tracks':[],'warnings':warnings}

def match_text(value):
 # Catalogue artists may use Latin spelling while public metadata uses Cyrillic.
 import re,unicodedata
 cyrillic=dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
  ['a','b','v','g','d','e','e','zh','z','i','y','k','l','m','n','o','p','r','s','t','u','f','kh','ts','ch','sh','shch','','y','','e','yu','ya']))
 text=''.join(cyrillic.get(c,c) for c in unicodedata.normalize('NFKC',str(value)).lower())
 return ' '.join(re.findall(r'\w+',text))

def enrich(tracks):
 from difflib import SequenceMatcher
 import re
 spotify_init();from spotdl.types.song import Song
 normalize=match_text
 updates=[]
 for track in tracks[:12]:
  artist=normalize(track.get('artist') or '')
  if not artist or artist in ('неизвестный исполнитель','unknown'):continue
  try:
   raw=Song.search(track['title']+' '+track['artist']);matches=[]
   for item in raw.get('tracks',{}).get('items',[])[:8]:
    artists=', '.join(a['name'] for a in item.get('artists',[]));ts=SequenceMatcher(None,normalize(track['title']),normalize(item['name'])).ratio();ars=SequenceMatcher(None,artist,normalize(artists)).ratio()
    if ts>=.90 and ars>=.86:matches.append((ts+ars,item))
   matches.sort(key=lambda x:x[0],reverse=True)
   if not matches:continue
   item=matches[0][1];images=item.get('album',{}).get('images',[])
   if images:updates.append({'path':track['path'],'cover_url':images[0]['url'],'metadata_url':'https://open.spotify.com/track/'+item['id']})
  except Exception:continue
 return {'tracks':updates}

def resolve_stream(data):
 # Resolve only a public, non-DRM direct audio resource. No cookies or subscriber access.
 from yt_dlp import YoutubeDL
 url=validate_track_url(data['url'],'youtube')
 options={'proxy':worker_proxy(),'quiet':True,'no_warnings':True,'noplaylist':True,'skip_download':True,'socket_timeout':6,'retries':0,'format':'bestaudio[protocol=https][ext=m4a]/bestaudio[protocol=https]'}
 with YoutubeDL({**options,**js_options()}) as y:info=y.extract_info(url,download=False)
 if not info or info.get('is_live') or info.get('has_drm') or info.get('vcodec') not in ('none',None):raise RuntimeError('Прямой аудиопоток недоступен.')
 remote=info.get('url','');u=urlparse(remote)
 if u.scheme!='https' or not (u.hostname or '').endswith('.googlevideo.com') or u.username or u.password or u.port not in (None,443):raise RuntimeError('Источник не вернул поддерживаемый аудиопоток.')
 headers={k:v for k,v in (info.get('http_headers') or {}).items() if isinstance(k,str) and k.lower() in ('user-agent','referer') and isinstance(v,str) and len(v)<=2048 and '\r' not in v and '\n' not in v}
 return {'stream_url':remote,'http_headers':headers}

def js_options():
 # Portable supplies an explicit private runtime; source runs may use a Deno
 # installed by the developer. Never download executable components at runtime.
 deno=os.environ.get('OWNPLAYER_DENO')
 return {'js_runtimes':{'deno':{'path':deno}}} if deno else {}

def downloader_args():
 import shlex
 args=['--proxy',worker_proxy()]
 if os.environ.get('OWNPLAYER_DENO'):args+=['--js-runtimes','deno:'+os.environ['OWNPLAYER_DENO']]
 return ' '.join(shlex.quote(a) for a in args)

def download(data):
 provider=data['provider'];url=validate_track_url(data['url'],provider);folder=Path(data['folder']).resolve();folder.mkdir(parents=True,exist_ok=True)
 audio_format=data.get('audio_format','original')
 if audio_format not in ('original','mp3'):raise RuntimeError('Неизвестный формат аудио.')
 ffmpeg=os.environ.get('OWNPLAYER_FFMPEG') or shutil.which('ffmpeg')
 if not ffmpeg:raise RuntimeError('FFmpeg не найден. Проверьте комплектность приложения.')
 if provider=='spotify':
  from spotdl import Spotdl
  from spotdl.types.song import Song
  cid=os.environ.get('OWNPLAYER_SPOTIFY_CLIENT_ID','');secret=os.environ.get('OWNPLAYER_SPOTIFY_CLIENT_SECRET','')
  downloader=Spotdl(client_id=cid,client_secret=secret,headless=True,no_cache=True,use_official_api=bool(cid and secret),downloader_settings={'audio_providers':['youtube-music','youtube','soundcloud'],'format':'mp3','bitrate':'auto','threads':1,'lyrics_providers':[],'output':str(folder/'{track-id}.{output-ext}'),'ffmpeg':ffmpeg,'max_retries':1,'yt_dlp_args':downloader_args()})
  song=Song.from_url(url)
  if audio_format=='mp3':
   _,path=downloader.download(song)
   if not path:raise RuntimeError('spotDL не нашёл доступное аудио для выбранной записи.')
  else:
   # Resolve the matched recording only. Do not use spotDL's conversion path
   # when the user requested the original encoded audio.
   urls=downloader.get_download_urls([song])
   if not urls or not urls[0]:raise RuntimeError('Не найден доступный аудиоисточник для записи.')
   url=urls[0];matched=next((p for p,hosts in HOSTS.items() if urlparse(url).hostname in hosts),None)
   if matched not in ('youtube','soundcloud'):raise RuntimeError('Неподдерживаемый источник сопоставленной записи.')
   url=validate_track_url(url,matched)
 if provider=='soundcloud' and audio_format=='mp3':
  import scdl.scdl
  os.environ['XDG_CONFIG_HOME']=str(folder/'config')
  sys.argv=['scdl','-l',url,'--path',str(folder),'--no-playlist','--no-original','--yt-dlp-args','--no-playlist --extract-audio --audio-format mp3 --audio-quality 0 --max-filesize 200M --socket-timeout 10 --retries 1 --no-progress --ffmpeg-location '+__import__('shlex').quote(str(ffmpeg))+' '+downloader_args()]
  scdl.scdl._main()
 elif not (provider=='spotify' and audio_format=='mp3'):
  from yt_dlp import YoutubeDL
  opts={'proxy':worker_proxy(),'format':'bestaudio','outtmpl':str(folder/'%(id)s.%(ext)s'),'noplaylist':True,'quiet':True,'no_warnings':True,'socket_timeout':10,'retries':1,'max_filesize':200*1024*1024,'ffmpeg_location':ffmpeg,**js_options()}
  if audio_format=='mp3':
   opts['postprocessors']=[{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'0'}]
  # No thumbnail request, cover embedding or re-encode in the original path.
  with YoutubeDL(opts) as y:y.download([url])
 allowed={'.mp3'} if audio_format=='mp3' else {'.mp3','.m4a','.opus','.ogg','.webm','.aac','.flac','.wav'}
 files=[p for p in folder.rglob('*') if p.suffix.lower() in allowed and p.is_file() and not p.is_symlink()]
 if len(files)!=1:raise RuntimeError('Загрузчик не вернул один готовый аудиофайл.')
 return {'file':str(files[0].resolve()),'audio_format':audio_format}

if __name__=='__main__':
 try:
  data=json.loads(sys.stdin.read(12000))
  # requests/spotDL use environment routing; yt-dlp receives the same route explicitly.
  for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):os.environ[key]=requests_proxy()
  os.environ['NO_PROXY']=os.environ['no_proxy']='localhost,127.0.0.1,::1'
  with contextlib.redirect_stderr(SafeWriter(sys.stderr)),contextlib.redirect_stdout(SafeWriter(sys.stderr)):
   if worker_proxy().startswith(('socks5://','socks5h://')) and (data['operation']=='enrich' or data.get('provider')=='spotify' or data['operation']=='download' and data.get('provider')=='soundcloud'):
    from importlib.util import find_spec
    if find_spec('socks') is None:raise RuntimeError('Missing dependencies for SOCKS support: install PySocks in .providers.')
   if data['operation']=='status':
    from importlib.metadata import version,PackageNotFoundError
    def installed(name):
     try:return version(name)
     except PackageNotFoundError:return None
    result={'spotdl':installed('spotdl'),'scdl':installed('scdl'),'socks_requests':bool(installed('PySocks')),'ffmpeg':bool((os.path.isfile(os.environ['OWNPLAYER_FFMPEG']) if os.environ.get('OWNPLAYER_FFMPEG') else shutil.which('ffmpeg'))),'python':sys.version.split()[0]}
   elif data['operation']=='stream':result=resolve_stream(data)
   elif data['operation']=='enrich':result=enrich(data.get('tracks',[]))
   else:result=search(data['query'],data.get('provider')) if data['operation']=='search' else download(data)
 except BaseException as error:
  result={'error':safe_error(str(error)) if isinstance(error,RuntimeError) else 'Ошибка внешнего загрузчика. Проверьте зависимости, сеть и доступность записи.'}
 sys.stdout.write(json.dumps(result,ensure_ascii=False))
