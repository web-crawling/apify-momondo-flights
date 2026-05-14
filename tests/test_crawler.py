"""Crawler tests for MomondoSpider.

Tests are split into two categories:

LIVE tests (marked @pytest.mark.live):
  - Require an internet connection to momondo.com.
  - Only tests (a), (b), (c) are live to avoid hammering the API.
  - Run with: pytest -m live tests/test_crawler.py

UNIT tests (no marker):
  - Fully offline; verify payload construction and fan-out logic.
  - Run with: pytest tests/test_crawler.py

The test suite verifies:
  (a) Bootstrap GET to flight-search page returns 200 and yields formToken.
  (b) One-way poll POST returns 200 with a results key.
  (c) Round-trip poll POST returns 200 with a results key.
  (d) Multi-city legs are built correctly (unit).
  (e) Flexible fan-out generates 2N+1 requests (unit).

Authentication note (discovered during implementation):
  - The CSRF token is NOT returned in Set-Cookie on the first poll response
    (contrary to earlier research notes).
  - The correct approach: GET the flight-search results page and extract
    window.R9.formToken = '<value>' from the HTML.
  - 'saECWKdkIP' is the validated constant searchId that works universally
    across all routes, dates, and session tokens.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BOOTSTRAP_URL_TEMPLATE = 'https://www.momondo.com/flight-search/{origin}-{destination}/{date}/'
POLL_URL = 'https://www.momondo.com/i/api/search/dynamic/flights/poll'
VALID_SEARCH_ID = 'saECWKdkIP'

HTML_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
}

POLL_HEADERS_BASE = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Encoding': 'gzip, deflate',
    'x-requested-with': 'XMLHttpRequest',
    'Content-Type': 'application/json',
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def bootstrap_session(origin='JFK', destination='LHR', dep_date='2026-09-01') -> tuple[requests.Session, str]:
    """Bootstrap a session: GET flight-search page, extract formToken.

    Returns (session, form_token).
    The session has all required cookies set.
    The form_token is extracted from window.R9.formToken in the page HTML.
    """
    session = requests.Session()
    url = BOOTSTRAP_URL_TEMPLATE.format(origin=origin, destination=destination, date=dep_date)
    resp = session.get(url, headers=HTML_HEADERS, timeout=30)
    assert resp.status_code == 200, f'Bootstrap GET returned {resp.status_code} for {url}'

    # Extract formToken from HTML
    match = re.search(r"window\.R9\.formToken\s*=\s*'([^']+)'", resp.text)
    assert match, f'window.R9.formToken not found in bootstrap response from {url}'
    form_token = match.group(1)
    assert form_token, 'Extracted formToken is empty'

    return session, form_token


def make_one_way_body(page: int = 1) -> dict:
    return {
        'filterParams': {},
        'userSearchParams': {
            'legs': [
                {
                    'origin': {'airports': ['JFK'], 'locationType': 'airports'},
                    'destination': {'airports': ['LHR'], 'locationType': 'airports'},
                    'date': '2026-09-01',
                    'flex': 'exact',
                },
            ],
            'searchId': VALID_SEARCH_ID,
            'passengers': ['ADT'],
            'passengerDetails': [{'ptc': 'ADT'}],
            'sortMode': 'bestflight_a',
        },
        'searchMetaData': {
            'pageNumber': page,
            'searchTypes': [],
            'skipResultsInSecondPhase': False,
        },
    }


def make_round_trip_body(page: int = 1) -> dict:
    return {
        'filterParams': {},
        'userSearchParams': {
            'legs': [
                {
                    'origin': {'airports': ['JFK'], 'locationType': 'airports'},
                    'destination': {'airports': ['LHR'], 'locationType': 'airports'},
                    'date': '2026-09-01',
                    'flex': 'exact',
                },
                {
                    'origin': {'airports': ['LHR'], 'locationType': 'airports'},
                    'destination': {'airports': ['JFK'], 'locationType': 'airports'},
                    'date': '2026-09-08',
                    'flex': 'exact',
                },
            ],
            'searchId': VALID_SEARCH_ID,
            'passengers': ['ADT'],
            'passengerDetails': [{'ptc': 'ADT'}],
            'sortMode': 'bestflight_a',
        },
        'searchMetaData': {
            'pageNumber': page,
            'searchTypes': [],
            'skipResultsInSecondPhase': False,
        },
    }


# ---------------------------------------------------------------------------
# Live tests
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_bootstrap_get():
    """(a) Bootstrap GET to flight-search page returns 200 and yields formToken."""
    session, form_token = bootstrap_session()
    assert form_token, 'formToken should not be empty'
    print(f'\n[PASS] Bootstrap GET -> 200, formToken extracted (len={len(form_token)})')
    print(f'       formToken prefix: {form_token[:30]}...')


@pytest.mark.live
def test_one_way_poll():
    """(b) One-way poll POST returns HTTP 200 with a non-empty "results" key."""
    session, form_token = bootstrap_session()

    poll_headers = dict(POLL_HEADERS_BASE)
    poll_headers['x-csrf'] = form_token
    poll_headers['Referer'] = BOOTSTRAP_URL_TEMPLATE.format(origin='JFK', destination='LHR', date='2026-09-01')

    resp = session.post(
        POLL_URL,
        headers=poll_headers,
        data=json.dumps(make_one_way_body()),
        timeout=30,
    )
    assert resp.status_code == 200, (
        f'One-way poll returned {resp.status_code}. Body: {resp.text[:500]}'
    )

    data = resp.json()
    assert 'results' in data, f'"results" key missing from poll response. Keys: {list(data.keys())}'

    results = data['results']
    print(f'\n[PASS] One-way poll -> {resp.status_code}, {len(results)} results returned')
    print(f'       filteredCount={data.get("filteredCount")}, pageSize={data.get("pageSize")}')

    # Verify pagination keys are present
    assert 'filteredCount' in data
    assert 'pageSize' in data
    assert 'pageNumber' in data


@pytest.mark.live
def test_round_trip_poll():
    """(c) Round-trip poll POST returns HTTP 200 with a "results" key."""
    session, form_token = bootstrap_session()

    poll_headers = dict(POLL_HEADERS_BASE)
    poll_headers['x-csrf'] = form_token
    poll_headers['Referer'] = BOOTSTRAP_URL_TEMPLATE.format(origin='JFK', destination='LHR', date='2026-09-01')

    resp = session.post(
        POLL_URL,
        headers=poll_headers,
        data=json.dumps(make_round_trip_body()),
        timeout=30,
    )
    assert resp.status_code == 200, (
        f'Round-trip poll returned {resp.status_code}. Body: {resp.text[:500]}'
    )

    data = resp.json()
    assert 'results' in data, f'"results" key missing. Keys: {list(data.keys())}'

    results = data['results']
    print(f'\n[PASS] Round-trip poll -> {resp.status_code}, {len(results)} results returned')
    print(f'       filteredCount={data.get("filteredCount")}, pageSize={data.get("pageSize")}')


# ---------------------------------------------------------------------------
# Unit tests (offline — no internet required)
# ---------------------------------------------------------------------------

# Add actor src to path so we can import MomondoSpider directly without
# installing the package.
_ACTOR_ROOT = Path(__file__).parent.parent
if str(_ACTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_ACTOR_ROOT))


def test_multi_city_legs_built_correctly():
    """(d) Multi-city _build_legs() produces one dict per input leg."""
    # We import here (after path manipulation above) to avoid a hard dependency
    # on Scrapy being installed when running in offline environments.
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(
        trip_type='multi-city',
        legs=[
            {'origin': 'JFK', 'destination': 'LHR', 'departureDate': '2026-09-01'},
            {'origin': 'LHR', 'destination': 'CDG', 'departureDate': '2026-09-05'},
            {'origin': 'CDG', 'destination': 'NYC', 'departureDate': '2026-09-10'},
        ],
    )
    legs = spider._build_legs()

    assert len(legs) == 3, f'Expected 3 legs, got {len(legs)}'

    assert legs[0]['origin']['airports'] == ['JFK']
    assert legs[0]['destination']['airports'] == ['LHR']
    assert legs[0]['date'] == '2026-09-01'
    assert legs[0]['flex'] == 'exact'

    assert legs[2]['origin']['airports'] == ['CDG']
    assert legs[2]['destination']['airports'] == ['NYC']
    assert legs[2]['date'] == '2026-09-10'

    print(f'\n[PASS] Multi-city legs: {len(legs)} legs built correctly')


def test_flexible_fan_out_generates_correct_number_of_requests():
    """(e) Flexible fan-out produces exactly (2*N + 1) requests."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    n = 3
    spider = MomondoSpider(
        trip_type='flexible',
        origin='JFK',
        destination='LHR',
        departure_date='2026-09-01',
        departure_date_flex_days=n,
    )

    # _build_flexible_poll_requests is a generator; collect into a list.
    # We need to mock _make_poll_request so we don't need a running Scrapy engine.
    requests_built = []

    def mock_make_poll_request(legs, page, flex_date=None, **kw):
        requests_built.append({'legs': legs, 'flex_date': flex_date, 'page': page})
        return MagicMock()  # Scrapy Request mock

    spider._make_poll_request = mock_make_poll_request

    list(spider._build_flexible_poll_requests())  # exhaust generator

    expected = 2 * n + 1
    assert len(requests_built) == expected, (
        f'Expected {expected} requests for N={n}, got {len(requests_built)}'
    )

    # Verify dates span [base - N, base + N]
    base = date.fromisoformat('2026-09-01')
    expected_dates = [(base + timedelta(days=d)).isoformat() for d in range(-n, n + 1)]
    actual_dates = [r['flex_date'] for r in requests_built]
    assert actual_dates == expected_dates, (
        f'Flex dates mismatch.\nExpected: {expected_dates}\nActual:   {actual_dates}'
    )

    print(f'\n[PASS] Flexible fan-out: {len(requests_built)} requests for N={n} (expected {expected})')
    for r in requests_built:
        print(f'       flex_date={r["flex_date"]} page={r["page"]}')


def test_one_way_legs():
    """One-way _build_legs() returns a single leg."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(trip_type='one-way', origin='CDG', destination='NRT', departure_date='2026-10-01')
    legs = spider._build_legs()
    assert len(legs) == 1
    assert legs[0]['origin']['airports'] == ['CDG']
    assert legs[0]['destination']['airports'] == ['NRT']
    assert legs[0]['date'] == '2026-10-01'
    print('\n[PASS] One-way legs: 1 leg built correctly')


def test_round_trip_legs():
    """Round-trip _build_legs() returns two legs with swapped origin/destination."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(
        trip_type='round-trip',
        origin='JFK',
        destination='LHR',
        departure_date='2026-09-01',
        return_date='2026-09-10',
    )
    legs = spider._build_legs()
    assert len(legs) == 2
    # Outbound
    assert legs[0]['origin']['airports'] == ['JFK']
    assert legs[0]['destination']['airports'] == ['LHR']
    assert legs[0]['date'] == '2026-09-01'
    # Return (swapped)
    assert legs[1]['origin']['airports'] == ['LHR']
    assert legs[1]['destination']['airports'] == ['JFK']
    assert legs[1]['date'] == '2026-09-10'
    print('\n[PASS] Round-trip legs: 2 legs built correctly with swapped route')


def test_passenger_build():
    """_build_passengers() returns correct PTC arrays for mixed pax."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(adults=2, children=1, infants=1)
    passengers, details = spider._build_passengers()
    assert passengers == ['ADT', 'ADT', 'CNN', 'INF']
    assert details == [{'ptc': 'ADT'}, {'ptc': 'ADT'}, {'ptc': 'CNN'}, {'ptc': 'INF'}]
    print('\n[PASS] Passenger build: correct PTC arrays for 2 adults + 1 child + 1 infant')


def test_max_stops_filter_params():
    """_build_filter_params() always returns empty dict (QA-confirmed: stops rejected by API).

    maxStops filtering is now applied client-side in parse_result().
    The poll body must NOT contain filterParams.stops — the API returns HTTP 400.
    """
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(max_stops=1)
    fp = spider._build_filter_params()
    assert fp == {}, f'filterParams must be empty dict (API rejects stops key), got {fp!r}'
    print('\n[PASS] _build_filter_params() returns empty dict (stops removed — client-side filtered)')

    spider0 = MomondoSpider(max_stops=0)
    fp0 = spider0._build_filter_params()
    assert fp0 == {}, f'filterParams must be empty dict even for maxStops=0, got {fp0!r}'
    print('[PASS] _build_filter_params() returns empty dict for maxStops=0')


# ---------------------------------------------------------------------------
# Regression tests: poll body must NOT contain rejected fields (BLOCKER fix)
# ---------------------------------------------------------------------------

def test_poll_body_does_not_contain_cabinClass():
    """REGRESSION: _build_poll_body() must NOT include cabinClass (causes HTTP 400)."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(cabin_class='BUSINESS')
    legs = spider._build_legs()
    body = spider._build_poll_body(legs=legs, page=1)

    # Check at top level and inside userSearchParams
    assert 'cabinClass' not in body, 'cabinClass must not appear at top level of poll body'
    user_params = body.get('userSearchParams', {})
    assert 'cabinClass' not in user_params, (
        f'cabinClass must not appear in userSearchParams (causes HTTP 400). '
        f'userSearchParams keys: {list(user_params.keys())}'
    )
    print('\n[PASS] Poll body does NOT contain cabinClass (BLOCKER regression)')


def test_poll_body_does_not_contain_currency():
    """REGRESSION: _build_poll_body() must NOT include currency in userSearchParams (causes HTTP 400)."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(currency='EUR')
    legs = spider._build_legs()
    body = spider._build_poll_body(legs=legs, page=1)

    user_params = body.get('userSearchParams', {})
    assert 'currency' not in user_params, (
        f'currency must not appear in userSearchParams (causes HTTP 400). '
        f'userSearchParams keys: {list(user_params.keys())}'
    )
    print('\n[PASS] Poll body does NOT contain currency in userSearchParams (BLOCKER regression)')


def test_poll_body_does_not_contain_stops():
    """REGRESSION: _build_poll_body() must NOT include stops in filterParams (causes HTTP 400)."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(max_stops=1)
    legs = spider._build_legs()
    body = spider._build_poll_body(legs=legs, page=1)

    filter_params = body.get('filterParams', {})
    assert 'stops' not in filter_params, (
        f'stops must not appear in filterParams (causes HTTP 400). '
        f'filterParams keys: {list(filter_params.keys())}'
    )
    assert filter_params == {}, (
        f'filterParams must be empty dict, got: {filter_params!r}'
    )
    print('\n[PASS] Poll body does NOT contain stops in filterParams (BLOCKER regression)')


def test_csrf_extracted_from_bootstrap_html():
    """formToken is extracted from window.R9.formToken = '...' in bootstrap HTML."""
    try:
        from src.spiders.momondo import _FORM_TOKEN_RE
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    sample_html = """
    <script>
    window.R9 = window.R9 || {};
    window.R9.formToken = 'testToken_abc123XYZ456789';
    window.R9.globals = window.R9.globals || {};
    </script>
    """
    match = _FORM_TOKEN_RE.search(sample_html)
    assert match, 'formToken regex should match sample HTML'
    token = match.group(1)
    assert token == 'testToken_abc123XYZ456789', f'Expected testToken_abc123XYZ456789, got {token!r}'
    print(f'\n[PASS] formToken regex: extracted {token!r}')

    # Verify it does NOT match missing formToken
    no_token_html = '<html><body>no token here</body></html>'
    assert _FORM_TOKEN_RE.search(no_token_html) is None
    print('[PASS] formToken regex: no false positive on missing token')


def test_valid_search_id_constant():
    """VALID_SEARCH_ID constant is the confirmed working searchId."""
    try:
        from src.spiders.momondo import VALID_SEARCH_ID
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    assert VALID_SEARCH_ID == 'saECWKdkIP', f'Unexpected VALID_SEARCH_ID: {VALID_SEARCH_ID!r}'
    assert len(VALID_SEARCH_ID) == 10, f'Expected 10 chars, got {len(VALID_SEARCH_ID)}'
    print(f'\n[PASS] VALID_SEARCH_ID = {VALID_SEARCH_ID!r} (confirmed working constant)')
