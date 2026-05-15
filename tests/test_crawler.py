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
  (f) _build_poll_body page 1 omits searchId; page 2+ includes server-returned searchId.
  (g) parse_poll captures server-issued searchId and stores it for subsequent requests.
  (h) crawl_failed is set on HTTP 4xx in errback_poll.

Authentication note (discovered during implementation):
  - The CSRF token is NOT returned in Set-Cookie on the first poll response
    (contrary to earlier research notes).
  - The correct approach: GET the flight-search results page and extract
    window.R9.formToken = '<value>' from the HTML.

searchId note (fix for issue #8):
  - searchId is server-issued, NOT a client constant.
  - The first poll omits searchId; server returns a fresh one in the response.
  - Subsequent polls in the same session include the server-returned searchId.
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


def make_one_way_body(page: int = 1, search_id: str | None = None) -> dict:
    """Build a one-way poll body.

    Page 1 (search_id=None): omit searchId (server-issued on first response).
    Page 2+ (search_id='...'): include server-returned searchId.
    """
    user_params: dict = {
        'legs': [
            {
                'origin': {'airports': ['JFK'], 'locationType': 'airports'},
                'destination': {'airports': ['LHR'], 'locationType': 'airports'},
                'date': '2026-09-01',
                'flex': 'exact',
            },
        ],
        'passengers': ['ADT'],
        'passengerDetails': [{'ptc': 'ADT'}],
        'sortMode': 'bestflight_a',
    }
    if search_id is not None:
        user_params['searchId'] = search_id
    return {
        'filterParams': {},
        'userSearchParams': user_params,
        'searchMetaData': {
            'pageNumber': page,
            'searchTypes': [],
            'skipResultsInSecondPhase': False,
        },
    }


def make_round_trip_body(page: int = 1, search_id: str | None = None) -> dict:
    """Build a round-trip poll body.

    Page 1 (search_id=None): omit searchId.
    Page 2+ (search_id='...'): include server-returned searchId.
    """
    user_params: dict = {
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
        'passengers': ['ADT'],
        'passengerDetails': [{'ptc': 'ADT'}],
        'sortMode': 'bestflight_a',
    }
    if search_id is not None:
        user_params['searchId'] = search_id
    return {
        'filterParams': {},
        'userSearchParams': user_params,
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
    """(b) One-way poll POST (page 1, no searchId) returns HTTP 200 with a results key
    and a server-issued searchId in the response."""
    session, form_token = bootstrap_session()

    poll_headers = dict(POLL_HEADERS_BASE)
    poll_headers['x-csrf'] = form_token
    poll_headers['Referer'] = BOOTSTRAP_URL_TEMPLATE.format(origin='JFK', destination='LHR', date='2026-09-01')

    # Page 1: no searchId in body (server issues one in the response)
    resp = session.post(
        POLL_URL,
        headers=poll_headers,
        data=json.dumps(make_one_way_body(page=1, search_id=None)),
        timeout=30,
    )
    assert resp.status_code == 200, (
        f'One-way poll returned {resp.status_code}. Body: {resp.text[:500]}'
    )

    data = resp.json()
    assert 'results' in data, f'"results" key missing from poll response. Keys: {list(data.keys())}'

    # Server must return a searchId in the response
    server_search_id = data.get('searchId')
    assert server_search_id, f'Server did not return a searchId in first poll response. Keys: {list(data.keys())}'
    print(f'\n[PASS] One-way poll -> {resp.status_code}, server searchId={server_search_id!r}')

    results = data['results']
    print(f'       {len(results)} results returned, filteredCount={data.get("filteredCount")}, pageSize={data.get("pageSize")}')

    # Verify pagination keys are present
    assert 'filteredCount' in data
    assert 'pageSize' in data
    assert 'pageNumber' in data


@pytest.mark.live
def test_round_trip_poll():
    """(c) Round-trip poll POST (page 1, no searchId) returns HTTP 200 with a "results" key."""
    session, form_token = bootstrap_session()

    poll_headers = dict(POLL_HEADERS_BASE)
    poll_headers['x-csrf'] = form_token
    poll_headers['Referer'] = BOOTSTRAP_URL_TEMPLATE.format(origin='JFK', destination='LHR', date='2026-09-01')

    # Page 1: no searchId
    resp = session.post(
        POLL_URL,
        headers=poll_headers,
        data=json.dumps(make_round_trip_body(page=1, search_id=None)),
        timeout=30,
    )
    assert resp.status_code == 200, (
        f'Round-trip poll returned {resp.status_code}. Body: {resp.text[:500]}'
    )

    data = resp.json()
    assert 'results' in data, f'"results" key missing. Keys: {list(data.keys())}'
    server_search_id = data.get('searchId')
    assert server_search_id, 'Server did not return searchId in round-trip first poll'

    results = data['results']
    print(f'\n[PASS] Round-trip poll -> {resp.status_code}, {len(results)} results returned')
    print(f'       filteredCount={data.get("filteredCount")}, pageSize={data.get("pageSize")}')
    print(f'       server searchId={server_search_id!r}')


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


# ---------------------------------------------------------------------------
# New unit tests for dynamic searchId fix (issue #8)
# ---------------------------------------------------------------------------

def test_build_poll_body_page1_omits_searchid():
    """BLOCKER fix #8: page 1 poll body must NOT contain searchId in userSearchParams."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(trip_type='one-way', origin='JFK', destination='LHR', departure_date='2026-09-01')
    legs = spider._build_legs()

    # Page 1 with no search_id (first poll, server not yet queried)
    body = spider._build_poll_body(legs=legs, page=1, search_id=None)

    user_params = body.get('userSearchParams', {})
    assert 'searchId' not in user_params, (
        f'Page 1 must NOT include searchId in userSearchParams. '
        f'Got keys: {list(user_params.keys())}'
    )
    print('\n[PASS] Page 1 poll body correctly omits searchId (BLOCKER fix #8)')


def test_build_poll_body_pageN_includes_searchid():
    """BLOCKER fix #8: page 2+ poll body MUST include the server-returned searchId."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(trip_type='one-way', origin='JFK', destination='LHR', departure_date='2026-09-01')
    legs = spider._build_legs()
    server_search_id = 'sgFCJ_ktTO'  # example server-issued value

    body = spider._build_poll_body(legs=legs, page=2, search_id=server_search_id)

    user_params = body.get('userSearchParams', {})
    assert 'searchId' in user_params, (
        f'Page 2 must include searchId in userSearchParams. '
        f'Got keys: {list(user_params.keys())}'
    )
    assert user_params['searchId'] == server_search_id, (
        f'Expected searchId={server_search_id!r}, got {user_params["searchId"]!r}'
    )
    print(f'\n[PASS] Page 2 poll body includes searchId={server_search_id!r} (BLOCKER fix #8)')


def test_parse_poll_captures_searchid():
    """BLOCKER fix #8: parse_poll must capture searchId from first response and thread it forward."""
    try:
        from scrapy.http import TextResponse, Request
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(trip_type='one-way', origin='JFK', destination='LHR', departure_date='2026-09-01')
    spider.csrf_token = 'test-token'

    # Build a minimal mock response body that simulates a first poll response
    # with a server-issued searchId and empty results (search still initialising)
    mock_body = json.dumps({
        'searchId': 'sgFiCFS7bb',
        'results': [],
        'filteredCount': 10,
        'pageSize': 50,
        'pageNumber': 1,
    })

    legs = spider._build_legs()
    mock_url = 'https://www.momondo.com/i/api/search/dynamic/flights/poll'
    response = TextResponse(
        url=mock_url,
        body=mock_body.encode('utf-8'),
        encoding='utf-8',
        status=200,
        request=Request(url=mock_url),
    )

    # Run parse_poll (page 1, no prior searchId) — should trigger long-poll retry
    requests_fired = list(spider.parse_poll(
        response=response,
        legs=legs,
        page=1,
        flex_date=None,
        search_id=None,
        page1_retry_count=0,
    ))

    # Should have fired exactly one retry request (empty first page, filteredCount > 0)
    assert len(requests_fired) == 1, (
        f'Expected 1 retry request for empty page 1 with filteredCount=10, '
        f'got {len(requests_fired)}'
    )

    # The retry request body must include the captured searchId
    retry_body = json.loads(requests_fired[0].body)
    user_params = retry_body.get('userSearchParams', {})
    assert 'searchId' in user_params, (
        f'Retry request must include the captured searchId. '
        f'userSearchParams keys: {list(user_params.keys())}'
    )
    assert user_params['searchId'] == 'sgFiCFS7bb', (
        f'Expected searchId="sgFiCFS7bb", got {user_params.get("searchId")!r}'
    )
    print('\n[PASS] parse_poll captures searchId from first response and threads it to retry request')


def test_crawl_failed_class_attribute_observable_from_outside():
    """BLOCKER fix (main.py): crawl_failed set inside spider must be readable via
    MomondoSpider.crawl_failed AFTER the spider exits (CrawlerRunner.crawl() returns
    None, not the spider instance, so getattr(spider, ...) always sees None and falls
    back to the default — the class attribute is the only reliable read path).
    """
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    # Start clean
    MomondoSpider.crawl_failed = False
    MomondoSpider.auth_failed = False

    spider = MomondoSpider(trip_type='one-way', origin='JFK', destination='LHR',
                           departure_date='2026-09-01')

    # Simulate what the spider does on a 403 error (type(self).crawl_failed = True)
    type(spider).crawl_failed = True

    # main.py reads the class attribute AFTER the deferred resolves, where it only
    # has the class reference (not the instance).  Verify the mutation is visible.
    assert MomondoSpider.crawl_failed is True, (
        'MomondoSpider.crawl_failed should be True after type(self).crawl_failed = True '
        'inside the spider — the class attribute must reflect the spider-set value'
    )

    # Also verify auth_failed works the same way
    assert MomondoSpider.auth_failed is False
    type(spider).auth_failed = True
    assert MomondoSpider.auth_failed is True, (
        'MomondoSpider.auth_failed should be True after type(self).auth_failed = True'
    )

    # Verify __init__ resets both to False for the next run
    spider2 = MomondoSpider(trip_type='one-way', origin='JFK', destination='LHR',
                            departure_date='2026-09-01')
    assert MomondoSpider.crawl_failed is False, (
        '__init__ must reset crawl_failed=False via type(self) for the next run'
    )
    assert MomondoSpider.auth_failed is False, (
        '__init__ must reset auth_failed=False via type(self) for the next run'
    )

    print('\n[PASS] crawl_failed/auth_failed class-attribute pattern: set inside spider, '
          'readable via MomondoSpider.X from outside, reset by __init__')


def test_crawl_failed_set_on_4xx():
    """BLOCKER fix #8 / issue #6: crawl_failed must be set to True when poll returns 4xx."""
    try:
        from scrapy.http import TextResponse, Request, Response
        from twisted.python.failure import Failure
        from scrapy.spidermiddlewares.httperror import HttpError
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(trip_type='one-way', origin='JFK', destination='LHR', departure_date='2026-09-01')
    assert not spider.crawl_failed, 'crawl_failed should start False'

    # Build a mock 400 response and a Failure wrapping an HttpError
    mock_url = 'https://www.momondo.com/i/api/search/dynamic/flights/poll'
    bad_response = TextResponse(
        url=mock_url,
        body=b'{"code": "INVALID_SEARCH_ID", "description": "Could not find search"}',
        encoding='utf-8',
        status=400,
        request=Request(url=mock_url),
    )

    try:
        raise HttpError(bad_response, 'HTTP 400')
    except HttpError as e:
        failure = Failure(e)

    spider.errback_poll(failure)

    assert spider.crawl_failed, (
        'crawl_failed should be True after errback_poll with HTTP 400'
    )
    print('\n[PASS] crawl_failed=True after errback_poll with HTTP 400 (BLOCKER fix #8 / issue #6)')


def test_long_poll_retries_when_first_poll_returns_zero_results_and_zero_filtered_count():
    """Follow-up fix for issue #8: on Apify datacenter IPs, the first poll may return
    0 results AND filteredCount=0 even when results would materialise on retry. The
    retry condition must NOT require filteredCount > 0 — it should retry whenever
    page 1 returns 0 results.
    """
    try:
        from scrapy.http import TextResponse, Request
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(trip_type='one-way', origin='JFK', destination='LHR',
                           departure_date='2026-09-01')
    spider.csrf_token = 'test-token'

    # First poll response — server-issued searchId but EMPTY filteredCount (the
    # case that was previously not retried)
    mock_body = json.dumps({
        'searchId': 'sgFiCFS7bb',
        'results': [],
        'filteredCount': 0,
        'pageSize': 50,
        'pageNumber': 1,
    })

    mock_url = 'https://www.momondo.com/i/api/search/dynamic/flights/poll'
    response = TextResponse(
        url=mock_url,
        body=mock_body.encode('utf-8'),
        encoding='utf-8',
        status=200,
        request=Request(url=mock_url),
    )

    legs = spider._build_legs()
    requests_fired = list(spider.parse_poll(
        response=response,
        legs=legs,
        page=1,
        flex_date=None,
        search_id=None,
        page1_retry_count=0,
    ))

    assert len(requests_fired) == 1, (
        f'Expected 1 retry request when page 1 returns 0 results AND filteredCount=0; '
        f'got {len(requests_fired)}'
    )
    retry_body = json.loads(requests_fired[0].body)
    assert retry_body['userSearchParams']['searchId'] == 'sgFiCFS7bb', (
        'Retry must reuse the server-issued searchId from the first response'
    )
    print('\n[PASS] long-poll retry fires even when filteredCount=0 (issue #8 follow-up fix)')
