"""Validate the measured sample before calculating any search quality rate."""
import math
import re

if __package__:
    from .common import ADAPTERS, DATASET_SHA256, MAX_CARDS, Failure
    from .run import load_queries
else:
    from common import ADAPTERS, DATASET_SHA256, MAX_CARDS, Failure
    from run import load_queries


def require(condition, code):
    if not condition:
        raise Failure(code)


def boolean(value):
    if str(value).lower() == 'true':
        return True
    if str(value).lower() == 'false':
        return False
    require(value in ('', None), 'invalid_manual_boolean')
    return None


def selected_queries(manifest):
    require(manifest.get('dataset_sha256') == DATASET_SHA256, 'report_dataset_mismatch')
    profile = manifest.get('profile')
    require(profile in ('smoke', 'full'), 'report_profile_invalid')
    queries = load_queries()
    if profile == 'smoke':
        queries = [queries[i] for i in (0, 24, 57)]
    require(manifest.get('selected_queries') == len(queries), 'report_sample_size_mismatch')
    ids = [q['query_id'] for q in queries]
    # v0.1 did not record IDs explicitly; they are reconstructed from the pinned set.
    require(manifest.get('query_ids', ids) == ids, 'report_query_ids_mismatch')
    return queries


def validate(manifest, attempts, cards, reviews, availability):
    queries = selected_queries(manifest)
    require(isinstance(manifest.get('run_id'), str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', manifest['run_id']),
            'report_run_id_invalid')
    require(manifest.get('network_label') in ('home_owner', 'vps_ru', 'sandbox'), 'report_network_invalid')
    flights = manifest.get('preflight')
    require(isinstance(flights, list) and flights, 'report_adapters_invalid')
    require(all(isinstance(f, dict) for f in flights), 'report_adapters_invalid')
    adapters = [f.get('adapter') for f in flights]
    require(all(a in ADAPTERS for a in adapters) and len(set(adapters)) == len(adapters), 'report_adapters_invalid')
    query_by_id = {q['query_id']: q for q in queries}
    expected = {(a, q) for a in adapters for q in query_by_id}
    available_keys = []
    for row in availability:
        key = (row.get('adapter'), row.get('query_id'))
        require(key in expected, 'unexpected_availability')
        available_keys.append(key)
        negative = query_by_id[key[1]]['group'] == 'negative_control'
        require(boolean(row.get('negative_control')) is negative, 'negative_control_mismatch')
        value = boolean(row.get('available'))
        if value is not None:
            require(row.get('reviewer', '').strip() and row.get('reference_evidence', '').strip(),
                    'available_without_independent_evidence')
    require(len(available_keys) == len(set(available_keys)), 'duplicate_availability')
    require(set(available_keys) == expected, 'availability_coverage_mismatch')

    by_id = {}
    for attempt in attempts:
        adapter, qid, phase = (attempt.get(k) for k in ('adapter', 'query_id', 'phase'))
        require((adapter, qid) in expected and phase in ('cold', 'warm'), 'unexpected_search_attempt')
        identity = qid + '-' + adapter + '-' + phase
        require(attempt.get('attempt_id') == identity, 'search_identity_mismatch')
        require(identity not in by_id, 'duplicate_search_attempt')
        by_id[identity] = attempt
        status = attempt.get('status')
        require(status in ('ok', 'no_results', 'failed'), 'search_status_invalid')
        count = attempt.get('cards')
        require(type(count) is int and 0 <= count <= MAX_CARDS, 'search_card_count_invalid')
        require((status != 'ok' or count > 0) and (status != 'no_results' or count == 0), 'search_status_card_mismatch')
        require(type(attempt.get('technical_success')) is bool and attempt['technical_success'] == (status != 'failed'),
                'search_success_flag_mismatch')
        for name in ('startup_ms', 'first_card_ms', 'complete_ms', 'first_card_end_to_end_ms', 'total_ms',
                     'cpu_observed_tree_s', 'rss_tree_sum_peak_bytes', 'adapter_init_ms', 'adapter_search_ms',
                     'catalogue_provider_ms', 'catalogue_normalization_ms'):
            value = attempt.get(name)
            require(value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0),
                    'search_metric_invalid')
    for attempt in attempts:
        if attempt['phase'] == 'warm':
            require(attempt['query_id'] + '-' + attempt['adapter'] + '-cold' in by_id, 'warm_without_cold')
    planned = 0 if manifest.get('preflight_only') else len(expected) * 2
    require(manifest.get('search_attempts_planned') == planned, 'planned_attempts_mismatch')
    require(manifest.get('actual_search_attempts') == len(attempts), 'actual_attempts_mismatch')
    require(manifest.get('remaining_attempts_not_run') == planned - len(attempts), 'remaining_attempts_mismatch')

    card_keys = set()
    for item in cards:
        identity, rank = item.get('attempt_id'), item.get('rank')
        require(identity in by_id and type(rank) is int and 1 <= rank <= MAX_CARDS, 'orphan_card')
        require((identity, rank) not in card_keys, 'duplicate_card')
        card_keys.add((identity, rank))
        value = item.get('first_seen_end_to_end_ms')
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'card_time_invalid')
        for name in ('adapter', 'query_id', 'phase'):
            require(item.get(name) == by_id[identity][name], 'card_identity_mismatch')
    for identity, attempt in by_id.items():
        require({r for key, r in card_keys if key == identity} == set(range(1, attempt['cards'] + 1)),
                'search_card_count_mismatch')

    review_keys = set()
    for review in reviews:
        raw_rank = review.get('rank', '')
        require(isinstance(raw_rank, str) and raw_rank.isdecimal(), 'review_rank_invalid')
        key = (review.get('attempt_id'), int(raw_rank))
        require(key in card_keys, 'orphan_manual_review')
        require(key not in review_keys, 'duplicate_manual_review')
        review_keys.add(key)
        for name in ('relevant', 'artist_match', 'duration_match', 'version_match'):
            boolean(review.get(name))
        require(review.get('full_or_preview', 'unknown') in ('', 'unknown', 'full', 'preview'), 'preview_review_invalid')
    return queries, adapters
