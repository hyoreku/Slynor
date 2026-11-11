"""The optional transport is tested separately in the locked research environment."""
from io import BytesIO
import unittest
from unittest.mock import Mock, patch
try:
    import requests
except ImportError:
    requests = None

from backend.catalogue_contract import CatalogueError
from backend.catalogue_http import MAX_BODY, public_client, public_session
from tools.source_experiment.common import classify


def response(status=200, body=b'{}'):
    result=requests.Response();result.status_code=status;result.raw=BytesIO(body)
    result.url='https://music.youtube.com/youtubei/v1/search'
    return result


@unittest.skipIf(requests is None, 'Optional catalogue research dependencies are not installed')
class TransportTests(unittest.TestCase):
    def test_only_fixed_public_origin_and_read_only_methods_reach_transport(self):
        session=public_session();self.addCleanup(session.close)
        with patch('requests.Session.request') as network:
            for url in ('http://music.youtube.com/','https://music.youtube.com.evil.invalid/',
                        'https://www.youtube.com/','https://127.0.0.1/'):
                with self.subTest(url=url),self.assertRaises(CatalogueError):session.get(url)
            with self.assertRaises(CatalogueError):session.delete('https://music.youtube.com/')
            network.assert_not_called()

    def test_timeouts_tls_no_redirects_and_no_env_credentials_are_fixed(self):
        session=public_session();self.addCleanup(session.close)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.max_redirects,0)
        with patch('requests.Session.request',return_value=response(body=b'{"ok":true}')) as network:
            result=session.post('https://music.youtube.com/youtubei/v1/search',verify=False,timeout=999)
            self.assertTrue(result.json()['ok'])
            self.assertEqual(network.call_args.kwargs['timeout'],(3.05,8))
            self.assertIs(network.call_args.kwargs['verify'],True)
            self.assertIs(network.call_args.kwargs['allow_redirects'],False)

    def test_explicit_proxy_is_used_and_credentials_are_rejected(self):
        session=public_session('http://127.0.0.1:10809');self.addCleanup(session.close)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies['http'],'http://127.0.0.1:10809')
        self.assertEqual(session.proxies['https'],'http://127.0.0.1:10809')
        for value in ('http://user:pass@127.0.0.1:10809','http://127.0.0.1','127.0.0.1:10809',5):
            with self.assertRaisesRegex(CatalogueError,'invalid_proxy'):public_session(value)

    def test_redirect_is_not_followed(self):
        session=public_session();self.addCleanup(session.close)
        value=response(302);value.headers['Location']='https://example.invalid/'
        with patch('requests.Session.request',return_value=value) as network:
            with self.assertRaisesRegex(CatalogueError,'blocked_redirect'):session.get('https://music.youtube.com/')
            self.assertEqual(network.call_count,1)

    def test_body_limit_is_enforced_after_decompression(self):
        session=public_session();self.addCleanup(session.close)
        with patch('requests.Session.request',return_value=response(body=b'x'*(MAX_BODY+1))):
            with self.assertRaisesRegex(CatalogueError,'response_too_large'):session.get('https://music.youtube.com/')

    def test_http_errors_are_categories_without_server_message(self):
        session=public_session();self.addCleanup(session.close)
        for status,category in ((401,'auth_required'),(403,'access_denied'),(429,'rate_limited'),(503,'http_error')):
            with patch('requests.Session.request',return_value=response(status)):
                try:session.get('https://music.youtube.com/')
                except Exception as error:self.assertEqual(classify(error),(category,status))
                else:self.fail('HTTP failure was accepted')

    def test_client_has_no_auth_and_accepts_only_reviewed_version(self):
        with patch('backend.catalogue_http.version',return_value='wrong'),patch('requests.Session.request') as network:
            with self.assertRaisesRegex(CatalogueError,'dependency_version_mismatch'):public_client()
            network.assert_not_called()
        with patch('ytmusicapi.YTMusic') as factory:
            client,session=public_client();self.addCleanup(session.close)
            self.assertIs(client,factory.return_value)
            self.assertIsNone(factory.call_args.kwargs['auth'])
            self.assertEqual(factory.call_args.kwargs['proxies'],{})
            self.assertIs(factory.call_args.kwargs['requests_session'],session)


if __name__=='__main__':unittest.main()
