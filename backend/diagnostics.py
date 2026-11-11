"""Bounded local logs and opt-in all-thread stack capture. Never log URLs/tokens."""
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading

_stack_file=None

def setup():
    global _stack_file
    directory=Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'OwnPlayerMusic' / 'logs'
    directory.mkdir(parents=True,exist_ok=True)
    logger=logging.getLogger('ownplayer')
    if not logger.handlers:
        handler=RotatingFileHandler(directory/'app.log',maxBytes=2_000_000,backupCount=2,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(threadName)s %(message)s'))
        logger.addHandler(handler);logger.setLevel(logging.INFO)
        def failure(kind,value,tb):
            logger.error('Uncaught %s',kind.__name__,exc_info=(kind,value,tb))
            sys.__excepthook__(kind,value,tb)
        sys.excepthook=failure
        threading.excepthook=lambda args:logger.error('Thread failure',exc_info=(args.exc_type,args.exc_value,args.exc_traceback))
        stack_path=directory/'stacks.log'
        if stack_path.exists() and stack_path.stat().st_size>2_000_000:
            stack_path.replace(directory/'stacks.previous.log')
        _stack_file=stack_path.open('a',encoding='utf-8')
        faulthandler.enable(file=_stack_file,all_threads=True)
        logger.info('Slynor startup stage23')
    return directory

def dump_stacks():
    if _stack_file:
        faulthandler.dump_traceback(file=_stack_file,all_threads=True);_stack_file.flush()
