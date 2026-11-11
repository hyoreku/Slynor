"""Verify the real msoc wrapper contract with no network or external packages."""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
class FakeHttpError(RuntimeError): pass
class Response:
    status_code=200
    async def aiter_bytes(self): yield b'<html>fixture</html>'
    def raise_for_status(self):
        if self.status_code != 200: raise FakeHttpError('fixed synthetic HTTP failure')
class Client:
    response=Response()
    requested_url=None
    def __init__(self,**kwargs): pass
    async def aclose(self): pass
    @asynccontextmanager
    async def stream(self,*args):
        Client.requested_url=args[1]
        yield self.response

class MsocResponseContractTests(unittest.TestCase):
    def load_worker(self):
        spec=importlib.util.spec_from_file_location('msoc_contract_under_test',ROOT/'backend/msoc_worker.py')
        module=importlib.util.module_from_spec(spec)
        fake=SimpleNamespace(AsyncClient=Client)
        with patch.dict(sys.modules,{'httpx':fake}): spec.loader.exec_module(module)
        return module
    def test_hitmo_can_check_status_and_read_text(self):
        worker=self.load_worker();engine=SimpleNamespace()
        async def search(_):
            async with engine.ClientSession() as session:
                async with session.get('https://rus.hitmotop.com/search?q=fixture') as response:
                    response.raise_for_status()
                    self.assertIn('fixture',await response.text())
                    yield SimpleNamespace(title='Synthetic',artist='Test',url='https://example.invalid/audio')
        engine.search=search
        with patch.object(worker.importlib,'import_module',return_value=engine):
            rows=asyncio.run(worker.search('hitmo','fixture'))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['title'],'Synthetic')
    def test_http_failure_does_not_become_an_empty_success(self):
        worker=self.load_worker();engine=SimpleNamespace()
        async def search(_):
            async with engine.ClientSession() as session:
                async with session.get('https://rus.hitmotop.com/search?q=fixture') as response:
                    response.raise_for_status()
                    yield SimpleNamespace(title='never',artist='',url='')
        engine.search=search
        with patch.object(Client.response,'status_code',503),patch.object(worker.importlib,'import_module',return_value=engine):
            with self.assertRaises(FakeHttpError): asyncio.run(worker.search('hitmo','fixture'))

    def test_muzbomb_receives_ampersand_as_query_text(self):
        worker=self.load_worker();engine=SimpleNamespace()
        async def search(query):
            async with engine.aiohttp.ClientSession() as session:
                async with session.get('https://muzbomb.net/?song='+query) as response:
                    await response.text()
                    yield SimpleNamespace(title='Synthetic',artist='Test',url='https://example.invalid/audio')
        engine.search=search
        query='Miyagi & Endshpil I Got Love'
        with patch.object(worker.importlib,'import_module',return_value=engine):
            asyncio.run(worker.search('muzbomb',query))
        self.assertEqual(parse_qs(urlsplit(Client.requested_url).query),{'song':[query]})
