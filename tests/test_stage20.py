"""Preflight timeout/redaction contract; not a native Windows test."""
import json,subprocess,types,unittest
from pathlib import Path
from tools.check_runtime import native_probe
ROOT=Path(__file__).resolve().parents[1]
class PreflightTests(unittest.TestCase):
 def test_native_probe_bounds_and_only_safe_fields(self):
  def run(args,**kw):
   self.assertEqual(kw['timeout'],15);self.assertTrue(kw['capture_output']);self.assertNotIn('shell',kw)
   return types.SimpleNamespace(returncode=0,stdout=json.dumps({'status':'ok','enumeration_available':True,'device_count':2,'private':'secret'}))
  self.assertEqual(native_probe(ROOT,run),{'status':'ok','enumeration_available':True,'device_count':2})
 def test_native_timeout(self):
  def run(*args,**kw):raise subprocess.TimeoutExpired('native',15)
  self.assertEqual(native_probe(ROOT,run),{'status':'native_timeout'})
 def test_stderr_never_exported(self):
  result=types.SimpleNamespace(returncode=1,stdout='private',stderr='secret path')
  self.assertEqual(native_probe(ROOT,lambda *a,**k:result),{'status':'native_failed'})
 def test_bad_response(self):
  for text in ('','not json','{}'):
   self.assertEqual(native_probe(ROOT,lambda *a,**k:types.SimpleNamespace(returncode=0,stdout=text)),{'status':'native_failed'})
