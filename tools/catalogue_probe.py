"""Read-only typed catalogue probe. It has no audio, credential or shell operations."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from backend.catalogue_contract import EntityRef
from backend.catalogue_http import public_client
from backend.ytmusic_catalogue import YTMusicCatalogue
from tools.source_experiment.common import classify, query_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation', choices=('search','artist','album','playlist'), required=True)
    parser.add_argument('--query')
    parser.add_argument('--id')
    parser.add_argument('--kind', choices=('all','track','artist','album','playlist'), default='track')
    parser.add_argument('--limit', type=int, choices=range(1,101), default=20)
    parser.add_argument('--language', choices=('en','ru'), default='en')
    parser.add_argument('--location', choices=('','RU','US'), default='')
    args = parser.parse_args()
    session = None; start = perf_counter()
    try:
        # Validate input before initializing a client or making any network call.
        query = query_text(args.query) if args.operation == 'search' else None
        ref = EntityRef(args.operation,args.id) if args.operation != 'search' else None
        with open(os.devnull,'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            client, session = public_client(args.language,args.location)
            catalogue = YTMusicCatalogue(client,args.language,args.location)
            data = catalogue.search(query,args.kind,args.limit) if query else catalogue.artist(ref,args.limit) if ref.kind == 'artist' else catalogue.collection(ref,args.limit)
        print(json.dumps({'status':'ok','elapsed_ms':round((perf_counter()-start)*1000,3),
            'mode':'metadata_only','audio_downloaded':False,'catalogue':data},ensure_ascii=False,indent=2))
        return 0
    except Exception as error:
        code, status = classify(error)
        print(json.dumps({'status':'failed','error_category':code,'http_status':status,
            'mode':'metadata_only','audio_downloaded':False},ensure_ascii=False))
        return 1
    finally:
        if session: session.close()


if __name__ == '__main__': raise SystemExit(main())
