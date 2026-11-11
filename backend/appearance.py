"""Small process-local appearance snapshot shared by main/mini. No filesystem polling."""
from copy import deepcopy
from threading import RLock
from pydantic import BaseModel,Field
from typing import Literal

class Appearance(BaseModel):
 theme:Literal['dark','light','transparent']='dark'
 accent:str=Field(default='#c9c9d1',pattern=r'^#[0-9a-fA-F]{6}$')
 wallpaper:str|None=Field(default=None,pattern=r'^[0-9a-f]{32}\.(jpg|gif|mp4|webm)$')
 dim:int=Field(default=65,ge=0,le=90)
 blur:int=Field(default=0,ge=0,le=30)
 mini_background:Literal['classic','wallpaper','colors']='classic'
_lock=RLock();_snapshot={**Appearance().model_dump(),'revision':0}
def publish(data):
 global _snapshot
 values=data.model_dump()
 with _lock:
  if any(_snapshot.get(k)!=v for k,v in values.items()):_snapshot={**values,'revision':_snapshot['revision']+1}
  return deepcopy(_snapshot)
def snapshot():
 with _lock:return deepcopy(_snapshot)
