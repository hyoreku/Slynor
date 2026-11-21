"""Apply the packaged source patch with hash guards and an external source backup."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import os
import re
import shutil
from uuid import uuid4

BLOCKED = {'.git', '.venv', '.providers', '.search', '.buildenv', 'dist', 'build',
           'build-logs', 'downloads', '.local-artifacts', '.local-data', '.local-cache', '.local-backups'}
MEDIA = {'.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.flac', '.webm', '.mp4',
         '.exe', '.dll', '.pfx', '.p12', '.key', '.pem'}


class PatchError(RuntimeError): pass


def digest(path):
    with Path(path).open('rb') as stream: return sha256(stream.read()).hexdigest()


def checked_path(root, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise PatchError('invalid_patch_path')
    name = PurePosixPath(relative)
    if name.is_absolute() or not name.parts or any(p in ('', '.', '..') for p in name.parts):
        raise PatchError('invalid_patch_path')
    if any(p.casefold() in BLOCKED for p in name.parts) or name.suffix.lower() in MEDIA:
        raise PatchError('runtime_or_media_path_blocked')
    if name.name == 'runtime_paths.json' or name.name.startswith('.env') or name.name.startswith('cookies'):
        raise PatchError('private_config_path_blocked')
    current = Path(root).absolute()
    for item in (current, *current.parents):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise PatchError('reparse_point_blocked')
    for part in name.parts:
        current = current / part
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise PatchError('reparse_point_blocked')
    return current


def plan(project, payload, manifest):
    project, payload = Path(project), Path(payload)
    if not project.is_dir(): raise PatchError('project_directory_missing')
    if manifest.get('version') != 1 or manifest.get('patch_id') not in ('desktop-fix-a','desktop-catalogue-b'):
        raise PatchError('patch_manifest_invalid')
    files = manifest.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= 512: raise PatchError('patch_manifest_invalid')
    seen = set(); result = []
    for row in files:
        if not isinstance(row, dict): raise PatchError('patch_manifest_invalid')
        relative = row.get('path'); source = checked_path(payload, relative); target = checked_path(project, relative)
        if relative.casefold() in seen: raise PatchError('duplicate_patch_path')
        seen.add(relative.casefold())
        before, after = row.get('before_sha256'), row.get('after_sha256')
        if not isinstance(after, str) or not re.fullmatch('[a-f0-9]{64}', after): raise PatchError('patch_manifest_invalid')
        if before is not None and (not isinstance(before, str) or not re.fullmatch('[a-f0-9]{64}', before)):
            raise PatchError('patch_manifest_invalid')
        if not source.is_file() or digest(source) != after: raise PatchError('payload_hash_mismatch: ' + relative)
        if target.exists() and not target.is_file(): raise PatchError('target_not_regular: ' + relative)
        current = digest(target) if target.is_file() else None
        if current == after: continue
        if current != before: raise PatchError('local_changes_preserved: ' + relative)
        result.append({'path':relative,'source':source,'target':target,'before':before,'after':after})
    return result


def apply(project, payload, manifest, check_only=False):
    project = Path(project).absolute()
    if project.parent == project: raise PatchError('project_cannot_be_a_drive_root')
    pending = plan(project, payload, manifest)
    if check_only or not pending:
        return {'checked':True,'pending':len(pending),'applied':0,'backup':None}
    name = project.name + '-source-backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    backup = project.parent / name; backup.mkdir()
    for row in pending:
        if row['before'] is not None:
            saved = backup / row['path']; saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(row['target'], saved)
            if digest(saved) != row['before']: raise PatchError('backup_hash_mismatch')
    (backup/'PATCH_BACKUP.json').write_text(json.dumps({'patch_id':manifest['patch_id'],
        'files':[{k:r[k] for k in ('path','before','after')} for r in pending]},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Backup is complete before the first project file is changed.
    applied = 0
    try:
        for row in pending:
            source = checked_path(payload, row['path']); target = checked_path(project, row['path'])
            current = digest(target) if target.is_file() else None
            if current != row['before']: raise PatchError('local_changes_preserved: ' + row['path'])
            if digest(source) != row['after']: raise PatchError('payload_hash_mismatch: ' + row['path'])
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary = target.with_name(target.name+'.patch-a-'+uuid4().hex+'.tmp')
            try:
                shutil.copyfile(source,temporary)
                if digest(temporary) != row['after']: raise PatchError('copy_hash_mismatch')
                os.replace(temporary,target); applied += 1
            finally: temporary.unlink(missing_ok=True)
        return {'checked':True,'pending':len(pending),'applied':applied,'backup':str(backup)}
    except Exception as error:
        raise PatchError('apply_interrupted; retry is safe for unchanged files; source backup: '+str(backup)) from error


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--check',action='store_true',help='Verify only; do not write or create backups')
    args=parser.parse_args(); package=Path(__file__).resolve().parent
    try:
        manifest=json.loads((package/'PATCH_MANIFEST.json').read_text(encoding='utf-8'))
        print(json.dumps(apply(args.project,package,manifest,args.check),ensure_ascii=False,indent=2))
    except (PatchError,OSError,ValueError) as error:
        print('PATCH_NOT_COMPLETED:',str(error) if isinstance(error,PatchError) else type(error).__name__)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
