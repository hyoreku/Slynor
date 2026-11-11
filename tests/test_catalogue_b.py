"""Contract/identity/safety tests with synthetic metadata, never real audio."""
from copy import deepcopy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.catalogue_contract import CatalogueError, EntityRef, normalize, reported_duration, request_key
from backend.ytmusic_catalogue import YTMusicCatalogue
from tools.source_experiment import common, run

ARTIST_A = 'UC' + 'a' * 22
ARTIST_B = 'UC' + 'b' * 22
SONG = {'resultType':'song','videoId':'abcdefghijk','title':'Тестовая песня',
        'artists':[{'name':'Одинаковое имя','id':ARTIST_A}], 'duration_seconds':123,
        'album':{'name':'Альбом','id':'MPREb_fixture'}, 'isAvailable':True,
        'thumbnails':[{'url':'https://lh3.googleusercontent.com/cover=w120?token=synthetic'}]}


class ContractTests(unittest.TestCase):
    def test_four_entity_kinds_keep_independent_keys(self):
        rows=[SONG,{'resultType':'artist','browseId':ARTIST_A,'artist':'Artist'},
              {'resultType':'album','browseId':'MPREb_fixture','title':'Album'},
              {'resultType':'playlist','browseId':'VLPLfixture','title':'Playlist'}]
        self.assertEqual([normalize(r).ref.kind for r in rows],['track','artist','album','playlist'])
        self.assertEqual(normalize(rows[3]).ref.provider_id,'PLfixture')
        self.assertEqual(len({normalize(r).ref.key for r in rows}),4)

    def test_omonymous_artists_do_not_share_identity(self):
        a=normalize({'resultType':'artist','browseId':ARTIST_A,'artist':'Same'})
        b=normalize({'resultType':'artist','browseId':ARTIST_B,'artist':'Same'})
        self.assertNotEqual(a.ref,b.ref);self.assertEqual(a.title,b.title)

    def test_artist_relationship_uses_id_and_never_infers_one_from_name(self):
        row=normalize(SONG);self.assertEqual(row.artists[0].ref.provider_id,ARTIST_A)
        row=normalize({**SONG,'artists':[{'name':'Same'}]})
        self.assertIsNone(row.artists[0].ref)

    def test_untyped_video_profile_episode_and_podcast_are_not_tracks(self):
        for kind in ('video','profile','episode','podcast',None):
            with self.subTest(kind=kind),self.assertRaises(CatalogueError):normalize({**SONG,'resultType':kind})

    def test_music_video_never_becomes_an_audio_track(self):
        for value in ('MUSIC_VIDEO_TYPE_OMV','MUSIC_VIDEO_TYPE_UGC','unknown'):
            with self.subTest(value=value),self.assertRaises(CatalogueError):normalize({**SONG,'videoType':value})

    def test_id_and_type_are_validated_before_any_request(self):
        for kind,value in [('track','A4'),('artist','Same name'),('album','https://example.invalid'),
                           ('playlist','../file'),('track','abcdefghijk?token=x')]:
            with self.subTest(kind=kind),self.assertRaises(CatalogueError):EntityRef(kind,value)

    def test_duration_is_reported_not_guessed(self):
        self.assertEqual(reported_duration({'duration':'1:02:03'}),3723000)
        for value in ('3 minutes','3:99','',None):self.assertIsNone(reported_duration({'duration':value}))
        self.assertIsNone(reported_duration({'duration_seconds':float('nan')}))

    def test_live_remix_cover_are_only_title_hints(self):
        item=normalize({**SONG,'title':'Track (Live Cover Remix)','isExplicit':True})
        self.assertEqual(set(item.title_version_hints),{'live','remix','cover'})
        self.assertEqual(item.version_classification,'unknown')
        self.assertEqual(item.full_or_preview,'unknown')
        self.assertTrue(item.explicit_reported)

    def test_short_and_long_songs_do_not_imply_preview_or_full(self):
        for seconds in (30,3600):self.assertEqual(normalize({**SONG,'duration_seconds':seconds}).full_or_preview,'unknown')
        self.assertEqual(normalize({**SONG,'isPreview':True}).full_or_preview,'preview')

    def test_catalogue_does_not_assert_download_permission_or_create_asset(self):
        item=normalize(SONG).to_dict()
        self.assertIsNone(item['audio_candidate']);self.assertIsNone(item['audio_asset'])
        self.assertEqual(item['download_permission'],'not_established')
        self.assertEqual(item['availability'],'reported_available')

    def test_missing_text_is_unknown_without_filename_or_artist_invention(self):
        item=normalize({'resultType':'song','videoId':'abcdefghijk'})
        self.assertIsNone(item.title);self.assertEqual(item.artists,());self.assertIsNone(item.duration_ms)

    def test_public_image_and_export_cannot_keep_signed_urls_or_secrets(self):
        item=normalize({**SONG,'title':'https://media.invalid/?token=synthetic',
            'feedbackTokens':{'add':'synthetic'},'stream_url':'https://media.invalid/private'})
        exported=json.dumps(item.to_dict())
        self.assertNotIn('synthetic',exported);self.assertNotIn('feedbackTokens',exported)
        self.assertEqual(item.cover_url,'https://lh3.googleusercontent.com/cover=w120')

    def test_cache_identity_separates_kind_locale_and_route(self):
        keys=[request_key('Same',*values) for values in [('track','ru','RU','direct'),
            ('artist','ru','RU','direct'),('track','en','RU','direct'),('track','ru','US','direct'),('track','ru','RU','route-b')]]
        self.assertEqual(len(set(keys)),5)


class AdapterTests(unittest.TestCase):
    def setUp(self):self.client=Mock();self.catalogue=YTMusicCatalogue(self.client)

    def test_song_filter_and_exact_query_are_used(self):
        self.client.search.return_value=[SONG]
        result=self.catalogue.search('Тестовая песня')
        self.client.search.assert_called_once_with('Тестовая песня',filter='songs',limit=10,ignore_spelling=True)
        self.assertEqual(result['items'][0]['ref']['provider_id'],'abcdefghijk')
        self.assertGreaterEqual(result['timings']['catalogue_provider_ms'],0)

    def test_all_filter_excludes_videos_without_replacing_them_with_songs(self):
        self.client.search.return_value=[SONG,{**SONG,'resultType':'video'},
            {'resultType':'artist','browseId':ARTIST_A,'artist':'Artist'}]
        result=self.catalogue.search('Test','all')
        self.assertEqual([r['ref']['kind'] for r in result['items']],['track','artist'])
        self.assertEqual(result['excluded'],1)

    def test_limit_and_partial_state_are_visible(self):
        self.client.search.return_value=[{**SONG,'videoId':f'{i:011d}'} for i in range(30)]
        result=self.catalogue.search('Test',limit=10)
        self.assertEqual(len(result['items']),10);self.assertEqual(result['received'],30)
        self.assertEqual(result['completeness'],'partial');self.assertIsNone(result['continuation']['cursor'])

    def test_duplicate_search_cards_are_deduplicated_by_id(self):
        self.client.search.return_value=[SONG,deepcopy(SONG)]
        self.assertEqual(len(self.catalogue.search('Test')['items']),1)

    def test_invalid_requests_do_not_call_provider(self):
        for arguments in [('',),('Test','videos'),('Test','track',0),('Test','track',101)]:
            with self.subTest(arguments=arguments),self.assertRaises(CatalogueError):self.catalogue.search(*arguments)
        self.client.search.assert_not_called()

    def test_artist_is_opened_by_selected_id_not_name_or_subscription_channel(self):
        self.client.get_artist.return_value={'name':'Same','channelId':ARTIST_B,
            'songs':{'browseId':'VLPLartist','results':[{k:v for k,v in SONG.items() if k!='resultType'}]}}
        result=self.catalogue.artist(EntityRef('artist',ARTIST_A))
        self.client.get_artist.assert_called_once_with(ARTIST_A);self.client.search.assert_not_called()
        self.assertEqual(result['ref']['provider_id'],ARTIST_A)
        self.assertEqual(result['sections']['songs']['scope'],'top')
        self.assertEqual(result['sections']['songs']['completeness'],'partial')
        self.assertEqual(result['sections']['songs']['collection_ref']['provider_id'],'PLartist')

    def test_playlist_preserves_order_duplicates_and_unavailable_slots(self):
        self.client.get_playlist.return_value={'id':'PLfixture','title':'Mix','trackCount':4,
            'tracks':[{**SONG,'videoType':'MUSIC_VIDEO_TYPE_ATV'}, {'title':'Unavailable','isAvailable':False},
                      {**SONG,'videoType':'MUSIC_VIDEO_TYPE_OMV'}, {**SONG,'videoType':'MUSIC_VIDEO_TYPE_ATV'}]}
        result=self.catalogue.collection(EntityRef('playlist','VLPLfixture'),10)
        self.client.get_playlist.assert_called_once_with('PLfixture',limit=10,related=False,suggestions_limit=0)
        self.assertEqual([r['position'] for r in result['entries']],[1,2,3,4])
        self.assertIsNone(result['entries'][1]['item']);self.assertIsNone(result['entries'][2]['item'])
        self.assertEqual(result['entries'][0]['item']['key'],result['entries'][3]['item']['key'])
        self.assertEqual(result['completeness'],'complete')

    def test_playlist_unknown_media_stays_visible_without_becoming_song(self):
        self.client.get_playlist.return_value={'tracks':[{k:v for k,v in SONG.items() if k!='resultType'}]}
        result=self.catalogue.collection(EntityRef('playlist','PLfixture'))
        self.assertEqual(result['entries'][0]['reason'],'unclassified_playlist_entry')

    def test_collection_total_is_not_inferred_from_short_response(self):
        self.client.get_album.return_value={'tracks':[SONG]}
        result=self.catalogue.collection(EntityRef('album','MPREb_fixture'))
        self.assertEqual(result['completeness'],'unknown');self.assertIsNone(result['reported_total'])

    def test_mismatched_collection_id_is_not_silently_accepted(self):
        self.client.get_playlist.return_value={'id':'PLother','tracks':[]}
        with self.assertRaises(CatalogueError):self.catalogue.collection(EntityRef('playlist','PLfixture'))


class ResearchIntegrationTests(unittest.TestCase):
    def test_process_disappearing_before_resource_attach_is_not_a_search_failure(self):
        class Gone(Exception): pass
        fake=SimpleNamespace(Process=Mock(side_effect=Gone),Error=Gone)
        with patch.dict('sys.modules',{'psutil':fake}):
            resources=run.Resources(999)
        self.assertFalse(resources.available)
        self.assertIsNone(resources.end()['rss_tree_sum_peak_bytes']);resources.close()

    def test_preview_claim_is_retained_without_becoming_full(self):
        item=normalize({**SONG,'isPreview':True}).to_dict()
        row=common.protocol_card('ytmusicapi.catalogue',{'source_id':item['ref']['provider_id'],
            'public_page':item['public_page'],'catalogue_item':item})
        self.assertEqual(row['full_or_preview'],'preview')

    def test_catalogue_is_opt_in_and_not_an_independent_audio_source(self):
        self.assertIn('ytmusicapi.catalogue',common.ADAPTERS)
        self.assertNotIn('ytmusicapi.catalogue',common.DEFAULT_ADAPTERS)
        self.assertEqual(common.SOURCE_FAMILY['ytmusicapi.catalogue'],common.SOURCE_FAMILY['yt-dlp.youtube'])
        self.assertEqual(common.DOWNLOADER_FAMILY['ytmusicapi.catalogue'],'none; metadata only')

    def test_research_protocol_keeps_id_links_and_discards_asset_claims(self):
        item=normalize(SONG).to_dict()
        row=common.protocol_card('ytmusicapi.catalogue',{
            'title':item['title'],'artist':'Same','source_id':item['ref']['provider_id'],
            'public_page':item['public_page'],'catalogue_item':item,'full_or_preview':'full'})
        self.assertEqual(row['artist_ids'],[ARTIST_A]);self.assertEqual(row['full_or_preview'],'unknown')
        self.assertNotIn('catalogue_item',row)

    def test_research_protocol_rejects_conflicting_typed_identity(self):
        item=normalize(SONG).to_dict()
        with self.assertRaises(common.Failure):common.protocol_card('ytmusicapi.catalogue',{
            'source_id':'12345678901','public_page':item['public_page'],'catalogue_item':item})


if __name__=='__main__':unittest.main()
