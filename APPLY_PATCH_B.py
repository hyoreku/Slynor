"""Apply cumulative catalogue B sources to either the original ZIP or patch A."""
import argparse
import json
from pathlib import Path
import re
import APPLY_PATCH_A as core


def apply(project, payload, manifest, check_only=False):
    if not isinstance(manifest,dict) or manifest.get('patch_id') != 'desktop-catalogue-b':
        raise core.PatchError('patch_manifest_invalid')
    rows=manifest.get('files')
    if not isinstance(rows,list) or not 1 <= len(rows) <= 512: raise core.PatchError('patch_manifest_invalid')
    normalized=[]
    for row in rows:
        if not isinstance(row,dict): raise core.PatchError('patch_manifest_invalid')
        target=core.checked_path(Path(project),row.get('path'))
        allowed=row.get('before_sha256_options')
        if not isinstance(allowed,list) or not 1 <= len(allowed) <= 2 or any(v is not None and (not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v)) for v in allowed):
            raise core.PatchError('patch_manifest_invalid')
        current=core.digest(target) if target.is_file() else None
        before=current if current in allowed else allowed[0]
        normalized.append({'path':row.get('path'),'before_sha256':before,'after_sha256':row.get('after_sha256')})
    # All of A's path, payload, backup, race recheck and idempotence guards remain active.
    return core.apply(project,payload,{'version':1,'patch_id':'desktop-catalogue-b','files':normalized},check_only)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args();payload=Path(__file__).resolve().parent
    try:
        manifest=json.loads((payload/'PATCH_MANIFEST_B.json').read_text(encoding='utf-8'))
        print(json.dumps(apply(args.project,payload,manifest,args.check),ensure_ascii=False,indent=2))
    except (core.PatchError,OSError,ValueError) as error:
        print('PATCH_NOT_COMPLETED:',str(error) if isinstance(error,core.PatchError) else type(error).__name__)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
