"""Patch only reviewed source paths; preserve local changes, music and environments."""
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
import APPLY_PATCH_A as patcher


class PatchApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.project=self.root/'OwnPlayerMusic';self.payload=self.root/'payload'
        self.project.mkdir();self.payload.mkdir()
        (self.project/'module.py').write_bytes(b'old source\n');(self.payload/'module.py').write_bytes(b'new source\n')
        (self.payload/'new.py').write_bytes(b'new module\n')
        self.manifest={'version':1,'patch_id':'desktop-fix-a','files':[
            {'path':'module.py','before_sha256':sha256(b'old source\n').hexdigest(),'after_sha256':patcher.digest(self.payload/'module.py')},
            {'path':'new.py','before_sha256':None,'after_sha256':patcher.digest(self.payload/'new.py')}]}

    def test_check_is_read_only(self):
        result=patcher.apply(self.project,self.payload,self.manifest,check_only=True)
        self.assertEqual(result['pending'],2);self.assertEqual(result['applied'],0)
        self.assertEqual((self.project/'module.py').read_bytes(),b'old source\n')
        self.assertFalse((self.project/'new.py').exists());self.assertFalse(list(self.root.glob('*source-backup*')))

    def test_apply_verifies_backup_outside_project_and_preserves_user_data(self):
        for directory in ('.venv','dist','Downloads'):
            p=self.project/directory;p.mkdir();(p/'preserved.txt').write_text('preserved')
        result=patcher.apply(self.project,self.payload,self.manifest)
        backup=Path(result['backup'])
        self.assertEqual(backup.parent,self.project.parent)
        self.assertEqual((backup/'module.py').read_bytes(),b'old source\n')
        self.assertEqual((self.project/'module.py').read_bytes(),b'new source\n')
        self.assertEqual((self.project/'new.py').read_bytes(),b'new module\n')
        for directory in ('.venv','dist','Downloads'):
            self.assertEqual((self.project/directory/'preserved.txt').read_text(),'preserved')

    def test_second_apply_is_idempotent(self):
        patcher.apply(self.project,self.payload,self.manifest)
        result=patcher.apply(self.project,self.payload,self.manifest)
        self.assertEqual(result['applied'],0);self.assertIsNone(result['backup'])

    def test_local_edits_are_not_overwritten(self):
        (self.project/'module.py').write_text('owner edit')
        with self.assertRaises(patcher.PatchError):patcher.apply(self.project,self.payload,self.manifest)
        self.assertEqual((self.project/'module.py').read_text(),'owner edit')
        self.assertFalse((self.project/'new.py').exists())

    def test_tampered_payload_cannot_be_applied(self):
        (self.payload/'new.py').write_text('different')
        with self.assertRaises(patcher.PatchError):patcher.apply(self.project,self.payload,self.manifest)
        self.assertEqual((self.project/'module.py').read_bytes(),b'old source\n')

    def test_traversal_runtime_and_private_files_are_blocked(self):
        for name in ('../outside.py','C:/outside.py','.venv/file.py','dist/file.py','Downloads/song.mp3',
                     'runtime_paths.json','.env','cookies.txt'):
            with self.subTest(name=name),self.assertRaises(patcher.PatchError):patcher.checked_path(self.project,name)

    def test_junction_or_symlink_ancestor_is_blocked(self):
        outside=self.root/'outside';outside.mkdir()
        try:(self.project/'frontend').symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('Symlinks unavailable')
        with self.assertRaises(patcher.PatchError):patcher.checked_path(self.project,'frontend/code.js')

    def test_duplicate_case_paths_are_rejected_for_windows(self):
        self.manifest['files'].append({**self.manifest['files'][0],'path':'MODULE.py'})
        (self.payload/'MODULE.py').write_bytes(b'new source\n')
        with self.assertRaises(patcher.PatchError):patcher.apply(self.project,self.payload,self.manifest)


if __name__=='__main__':unittest.main()
