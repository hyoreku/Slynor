from hashlib import sha256
from pathlib import Path
import json
import tempfile
import unittest
import APPLY_PATCH_A as core
import APPLY_PATCH_B as patcher


class PatchBTests(unittest.TestCase):
    def test_original_and_patch_a_are_both_accepted_with_verified_external_backup(self):
        for old in (b'original',b'patch a'):
            with self.subTest(old=old),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);project=root/'project';project.mkdir();payload=root/'payload';payload.mkdir()
                (project/'source.py').write_bytes(old);(payload/'source.py').write_bytes(b'patch b')
                manifest={'patch_id':'desktop-catalogue-b','files':[{'path':'source.py',
                    'before_sha256_options':[sha256(b'original').hexdigest(),sha256(b'patch a').hexdigest()],
                    'after_sha256':sha256(b'patch b').hexdigest()}]}
                self.assertEqual(patcher.apply(project,payload,manifest,True)['pending'],1)
                result=patcher.apply(project,payload,manifest)
                self.assertEqual(json.loads((Path(result['backup'])/'PATCH_BACKUP.json').read_text())['patch_id'],'desktop-catalogue-b')
                self.assertEqual((Path(result['backup'])/'source.py').read_bytes(),old)
                self.assertEqual(patcher.apply(project,payload,manifest)['applied'],0)

    def test_unknown_local_edit_and_invalid_alternative_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=root/'project';project.mkdir();payload=root/'payload';payload.mkdir()
            (project/'source.py').write_bytes(b'owner edit');(payload/'source.py').write_bytes(b'patch b')
            row={'path':'source.py','before_sha256_options':[sha256(b'original').hexdigest()],
                 'after_sha256':sha256(b'patch b').hexdigest()}
            for options in ([sha256(b'original').hexdigest()],['invalid']):
                with self.assertRaises(core.PatchError):patcher.apply(project,payload,{'patch_id':'desktop-catalogue-b','files':[{**row,'before_sha256_options':options}]})
                self.assertEqual((project/'source.py').read_bytes(),b'owner edit')


if __name__=='__main__':unittest.main()
