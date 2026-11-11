"""Optional, read-only catalogue adapter shared by research and future clients."""
from time import perf_counter
import re
from backend.catalogue_contract import (CatalogueError, EntityRef, clean_text, normalize,
                                         optional_ref, request_key)

FILTERS = {'all': None, 'track': 'songs', 'artist': 'artists', 'album': 'albums', 'playlist': 'playlists'}
MAX_ITEMS = 100


def limit_value(value):
    if type(value) is not int or not 1 <= value <= MAX_ITEMS: raise CatalogueError('invalid_limit')
    return value


class YTMusicCatalogue:
    def __init__(self, client, language='en', location='', route='direct'):
        self.client, self.language, self.location, self.route = client, language, location, route

    def search(self, query, kind='track', limit=10):
        if not isinstance(query, str) or not query.strip() or len(query) > 200 or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in query):
            raise CatalogueError('invalid_query')
        if kind not in FILTERS: raise CatalogueError('invalid_filter')
        if re.search(r'(?i)(?:https?|file|data|ftp):', query): raise CatalogueError('query_urls_not_allowed')
        limit_value(limit)
        started = perf_counter()
        rows = self.client.search(query.strip(), filter=FILTERS[kind], limit=limit, ignore_spelling=True)
        provider_ms = (perf_counter() - started) * 1000
        normalized = perf_counter()
        if not isinstance(rows, list): raise CatalogueError('invalid_catalogue_response')
        items, seen, excluded = [], set(), 0
        # The library may return more than limit. Expose the bounded nature of this result.
        for row in rows[:500]:
            try: item = normalize(row)
            except CatalogueError: excluded += 1; continue
            if kind != 'all' and item.ref.kind != kind: excluded += 1; continue
            if item.ref.key in seen: excluded += 1; continue
            seen.add(item.ref.key)
            if len(items) < limit: items.append(item.to_dict())
        return {'schema_version': 1, 'items': items, 'received': len(rows), 'excluded': excluded,
                'uninspected': max(0,len(rows)-500),
                'completeness': 'partial' if len(rows) >= limit else 'unknown',
                'continuation': {'mode': 'not_exposed', 'cursor': None}, 'requested_limit': limit,
                'timings': {'catalogue_provider_ms': provider_ms, 'catalogue_normalization_ms': (perf_counter()-normalized)*1000},
                'request_identity': request_key(query.strip(), kind, self.language, self.location, self.route)}

    def _members(self, rows, limit, total=None, context='track', scope='collection'):
        if not isinstance(rows, list): raise CatalogueError('invalid_catalogue_response')
        limit_value(limit); entries = []
        for index, row in enumerate(rows[:limit], 1):
            try:
                # A playlist can mix videos, unavailable entries and audio. Preserve their positions.
                if scope == 'playlist' and isinstance(row, dict) and not row.get('resultType') and row.get('videoType') is None and row.get('isAvailable') is not False:
                    raise CatalogueError('unclassified_playlist_entry')
                item = normalize(row, context)
                entries.append({'position': index, 'item': item.to_dict(), 'label': item.title, 'reason': None})
            except CatalogueError as error:
                entries.append({'position': index, 'item': None,
                    'label': clean_text(row.get('title')) if isinstance(row,dict) else None,
                    'availability': 'reported_unavailable' if isinstance(row,dict) and row.get('isAvailable') is False else 'unknown',
                    'reason': str(error)})
        total = total if type(total) is int and total >= 0 else None
        completeness = 'complete' if total is not None and len(rows) == total and len(rows) <= limit else 'partial' if len(rows) > limit or total is not None and total > len(entries) else 'unknown'
        if scope == 'top': completeness = 'partial'
        return {'entries': entries, 'reported_total': total, 'received': len(rows),
                'requested_limit': limit, 'completeness': completeness, 'scope': scope,
                'continuation': {'mode': 'not_exposed', 'cursor': None}}

    def collection(self, ref, limit=50):
        limit_value(limit)
        if ref.kind == 'playlist':
            data = self.client.get_playlist(ref.provider_id, limit=limit, related=False, suggestions_limit=0)
        elif ref.kind == 'album': data = self.client.get_album(ref.provider_id)
        else: raise CatalogueError('not_a_collection')
        if not isinstance(data, dict): raise CatalogueError('invalid_catalogue_response')
        # A response is never allowed to redirect a selected entity to a namesake.
        returned = data.get('id') if ref.kind == 'playlist' else data.get('browseId')
        if returned and EntityRef(ref.kind, returned) != ref: raise CatalogueError('entity_identity_mismatch')
        return {'schema_version': 1, 'ref': {'provider': ref.provider, 'kind': ref.kind, 'provider_id': ref.provider_id},
                'title': clean_text(data.get('title')), 'public_page': ref.public_page,
                **self._members(data.get('tracks', []), limit, data.get('trackCount'), scope=ref.kind)}

    def artist(self, ref, limit=20):
        if ref.kind != 'artist': raise CatalogueError('not_an_artist')
        limit_value(limit); data = self.client.get_artist(ref.provider_id)
        if not isinstance(data, dict): raise CatalogueError('invalid_catalogue_response')
        result = {'schema_version': 1, 'ref': {'provider': ref.provider, 'kind': ref.kind, 'provider_id': ref.provider_id},
                  'title': clean_text(data.get('name')), 'public_page': ref.public_page, 'sections': {}}
        # Returned channelId may be the subscription channel, NOT the selected browseId.
        for section in ('songs', 'albums', 'singles'):
            value = data.get(section)
            if not isinstance(value, dict): continue
            linked = optional_ref('playlist' if section == 'songs' else 'artist', value.get('browseId'))
            result['sections'][section] = {**self._members(value.get('results', []), limit,
                context='track' if section == 'songs' else 'album', scope='top'),
                'collection_ref': {'provider': linked.provider, 'kind': linked.kind, 'provider_id': linked.provider_id} if linked and section == 'songs' else None}
        return result
