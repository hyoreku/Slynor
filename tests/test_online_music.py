"""Official API fixtures only. No real credentials or online account requests."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from backend.online_music import SoundCloud, OnlineLibrary, ProviderError, safe_remote, artwork


def track(identifier=1, access='playable'):
    return {'id':identifier,'kind':'track','title':'Track '+str(identifier),'user':{'username':'Artist'},
            'access':access,'duration':180000,'artwork_url':'https://i1.sndcdn.com/art.jpg',
            'permalink_url':'https://soundcloud.com/artist/track'}

class SoundCloudTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'SOUNDCLOUD_CLIENT_ID':'fixture-id','SOUNDCLOUD_CLIENT_SECRET':'fixture-secret'},clear=True);self.env.start()
        self.requests=[];self.responses={};self.tokens=0
        def handle(request):
            self.requests.append(request)
            if request.url.path=='/oauth/token':
                self.tokens+=1;return httpx.Response(200,json={'access_token':'fixture-token','refresh_token':'fixture-refresh','expires_in':3600})
            data=self.responses.get(str(request.url),self.responses.get(request.url.path))
            if isinstance(data,httpx.Response):return data
            if callable(data):return data(request)
            return httpx.Response(200,json=data or {})
        self.provider=SoundCloud(httpx.Client(transport=httpx.MockTransport(handle),follow_redirects=False))
        self.tmp=tempfile.TemporaryDirectory();self.library=OnlineLibrary(self.tmp.name,self.provider)
    def tearDown(self):self.library.close();self.tmp.cleanup();self.env.stop()
    def test_search_normalizes_and_keeps_blocked_records(self):
        self.responses['/tracks']={'collection':[track(1),track(2,'preview'),track(3,'blocked')]}
        result=self.library.search('soundcloud','Artist');self.assertEqual(len(result),3)
        self.assertEqual(result[0]['path'],'online:soundcloud:1')
        self.assertEqual(result[1]['access'],'preview')
    def test_token_is_reused_not_reissued_per_search(self):
        self.responses['/tracks']={'collection':[]}
        self.provider.search('one');self.provider.search('two');self.assertEqual(self.tokens,1)
        self.assertTrue(any(r.headers.get('authorization')=='OAuth fixture-token' for r in self.requests))
    def test_refresh_uses_documented_form_credentials(self):
        self.provider.token();self.provider.expires=0;self.provider.token()
        requests=[r for r in self.requests if r.url.path=='/oauth/token']
        self.assertEqual(len(requests),2)
        self.assertIn('grant_type=refresh_token',requests[1].content.decode())
        self.assertIn('client_id=fixture-id',requests[1].content.decode())
        self.assertIn('client_secret=fixture-secret',requests[1].content.decode())
        self.assertTrue(requests[0].headers['authorization'].startswith('Basic '))
    def test_only_full_hls_not_preview_is_selected(self):
        self.responses['/tracks/1']=track(1)
        self.responses['/tracks/1/streams']={'hls_aac_160_url':'https://api.soundcloud.com/tracks/1/streams/full','preview_mp3_128_url':'https://api.soundcloud.com/preview'}
        self.assertEqual(self.provider.full_stream('online:soundcloud:1'),'https://api.soundcloud.com/tracks/1/streams/full')
    def test_preview_only_response_rejected_even_if_cached_playable(self):
        self.responses['/tracks/1']=track(1,'preview')
        with self.assertRaises(ProviderError):self.provider.full_stream('online:soundcloud:1')
        self.assertFalse(any(r.url.path.endswith('/streams') for r in self.requests))
    def test_preview_field_never_used_as_fallback(self):
        self.responses['/tracks/1']=track(1)
        self.responses['/tracks/1/streams']={'preview_mp3_128_url':'https://api.soundcloud.com/preview'}
        with self.assertRaises(ProviderError):self.provider.full_stream('online:soundcloud:1')
    def test_missing_credentials_are_explicit(self):
        with patch.dict(os.environ,{},clear=True),self.assertRaises(ProviderError):self.provider.search('one')
    def test_missing_vk_access_is_explicit(self):
        with self.assertRaises(ProviderError):self.library.search('vk','one')
        with self.assertRaises(ProviderError):self.library.import_playlist('vk','https://vk.com/music')
    def test_status_does_not_claim_configured_vk(self):
        status=self.library.status()['sources'];self.assertTrue(status[0]['configured']);self.assertFalse(status[1]['configured'])
    def test_normalizes_current_urn(self):
        data=track();data['urn']='soundcloud:tracks:42'
        result=self.provider.normalize(data);self.assertEqual(result['path'],'online:soundcloud:soundcloud:tracks:42')
    def test_import_paginates_and_keeps_unavailable(self):
        self.responses['/resolve']={'kind':'playlist','id':7,'title':'Playlist','track_count':3}
        self.responses['/playlists/7/tracks']={'collection':[track(1),track(2,'blocked')],'next_href':'https://api.soundcloud.com/page-two'}
        self.responses['/page-two']={'collection':[track(3,'preview')],'next_href':None}
        playlist=self.library.import_playlist('soundcloud','https://soundcloud.com/a/sets/b')
        self.assertEqual(len(playlist['tracks']),3);self.assertEqual(playlist['tracks'][1]['access'],'blocked')
        saved=json.loads(self.library.path.read_text());self.assertEqual(len(saved['playlists']),1)
        self.assertNotIn('fixture-secret',self.library.path.read_text());self.assertNotIn('fixture-token',self.library.path.read_text())
        loaded=OnlineLibrary(self.tmp.name,self.provider);self.assertEqual(len(loaded.imported()[0]['tracks']),3)
    def test_incomplete_import_does_not_save(self):
        self.responses['/resolve']={'kind':'playlist','id':7,'title':'Playlist','track_count':3}
        self.responses['/playlists/7/tracks']={'collection':[track(1)]}
        with self.assertRaises(ProviderError):self.library.import_playlist('soundcloud','https://soundcloud.com/a/sets/b')
        self.assertFalse(self.library.path.exists())
    def test_corrupt_saved_file_not_overwritten(self):
        self.library.path.write_text('{broken')
        loaded=OnlineLibrary(self.tmp.name,self.provider)
        with self.assertRaises(ProviderError):loaded.import_playlist('soundcloud','https://soundcloud.com/a/sets/b')
        self.assertEqual(self.library.path.read_text(),'{broken')
    def test_no_dangerous_import_url(self):
        for url in ['http://localhost/a','https://evil.example/a','https://soundcloud.com@evil.example/a']:
            with self.subTest(url=url),self.assertRaises(ProviderError):self.provider.playlist(url)
    def test_no_cross_host_pagination(self):
        self.responses['/resolve']={'kind':'playlist','id':7}
        self.responses['/playlists/7/tracks']={'collection':[track(1)],'next_href':'http://localhost/secret'}
        with self.assertRaises(ProviderError):self.provider.playlist('https://soundcloud.com/a/sets/b')
    def test_playlist_cycle_not_imported(self):
        self.responses['/resolve']={'kind':'playlist','id':7}
        self.responses['/playlists/7/tracks']={'collection':[],'next_href':'https://api.soundcloud.com/loop'}
        self.responses['/loop']={'collection':[],'next_href':'https://api.soundcloud.com/loop'}
        with self.assertRaises(ProviderError):self.provider.playlist('https://soundcloud.com/a/sets/b')
    def test_hls_nested_segments_and_key_urls_rewritten(self):
        self.library.remember([self.provider.normalize(track())]);url=self.library.play_url('online:soundcloud:1');handle=url.split('/')[-2]
        body='#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="keys/key"\n#EXTINF:10\nsegment.ts\nhttps://cdn.sndcdn.com/segment2.ts\n'
        output=self.library.rewrite_playlist(handle,body,'https://cdn.sndcdn.com/folder/master.m3u8')
        self.assertNotIn('sndcdn.com',output);self.assertNotIn('fixture-token',output)
        resources=self.library.media[handle]['resources'];self.assertEqual(len(resources),4)
        self.assertTrue(all(x.startswith('/api/online/media/') for x in output.splitlines() if x and not x.startswith('#')))
    def test_hls_bad_segment_domain_rejected(self):
        self.library.remember([self.provider.normalize(track())]);handle=self.library.play_url('online:soundcloud:1').split('/')[-2]
        with self.assertRaises(ProviderError):self.library.rewrite_playlist(handle,'#EXTM3U\nhttp://127.0.0.1/private','https://cdn.sndcdn.com/master.m3u8')
    def test_api_authorization_not_forwarded_to_cdn(self):
        self.responses['/tracks/1/streams/full']=httpx.Response(302,headers={'Location':'https://cdn.sndcdn.com/master.m3u8'})
        self.responses['/master.m3u8']=httpx.Response(200,headers={'content-type':'application/vnd.apple.mpegurl'},content=b'#EXTM3U\n')
        response,url=self.library.open_media('https://api.soundcloud.com/tracks/1/streams/full');response.close()
        api=[r for r in self.requests if r.url.path.endswith('/full')][0]
        cdn=[r for r in self.requests if r.url.host=='cdn.sndcdn.com'][0]
        self.assertEqual(api.headers['authorization'],'OAuth fixture-token');self.assertNotIn('authorization',cdn.headers)
    def test_stream_json_url_resolved(self):
        self.responses['/streams/full']={'url':'https://cdn.sndcdn.com/master.m3u8'}
        self.responses['/master.m3u8']=httpx.Response(200,content=b'#EXTM3U\n',headers={'content-type':'application/vnd.apple.mpegurl'})
        response,url=self.library.open_media('https://api.soundcloud.com/streams/full');response.close()
        self.assertEqual(url,'https://cdn.sndcdn.com/master.m3u8')
    def test_stream_urls_reject_private_hosts_and_credentials(self):
        for url in ['http://api.soundcloud.com/x','https://127.0.0.1/x','https://api.soundcloud.com.evil.test/x','https://api.soundcloud.com:8443/x','https://user:pass@api.soundcloud.com/x']:
            with self.subTest(url=url),self.assertRaises(ProviderError):safe_remote(url)
    def test_images_are_provider_whitelisted(self):
        self.assertIsNone(artwork('https://evil.test/image.png'));self.assertIsNone(artwork('javascript:alert(1)'))
        self.assertEqual(artwork('https://i1.sndcdn.com/image.jpg'),'https://i1.sndcdn.com/image.jpg')
    def test_no_play_url_for_preview(self):
        t=self.provider.normalize(track(1,'preview'));self.library.remember([t])
        with self.assertRaises(ProviderError):self.library.play_url(t['path'])
    def test_media_session_count_bounded(self):
        self.library.remember([self.provider.normalize(track())])
        for _ in range(20):self.library.play_url('online:soundcloud:1')
        self.assertEqual(len(self.library.media),8)

if __name__=='__main__':unittest.main()
