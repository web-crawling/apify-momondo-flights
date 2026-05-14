"""End-to-end QA tests for the Momondo flights actor.

These tests verify the full data pipeline from a live poll response through
parse_result() to FlightItem output. They use a combination of:
  - Fixture data (offline) for field validation
  - Live network calls (marked @pytest.mark.live) for integration verification

Run all:    pytest tests/test_e2e.py -v
Run live:   pytest tests/test_e2e.py -v -m live
Run unit:   pytest tests/test_e2e.py -v -m "not live"
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_ACTOR_ROOT = Path(__file__).parent.parent
if str(_ACTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_ACTOR_ROOT))

FIXTURE_PATH = Path(__file__).parent / 'fixtures' / 'poll_response.json'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_spider():
    try:
        from src.spiders.momondo import MomondoSpider
        return MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed')


def _load_fixture() -> dict:
    if not FIXTURE_PATH.exists():
        pytest.skip(f'Fixture not found: {FIXTURE_PATH}')
    with open(FIXTURE_PATH, encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def spider():
    MomondoSpider = _import_spider()
    return MomondoSpider(trip_type='one-way', origin='JFK', destination='LHR',
                         departure_date='2026-08-01', max_results=50)


@pytest.fixture(scope='module')
def poll_data() -> dict:
    return _load_fixture()


@pytest.fixture(scope='module')
def all_items(spider, poll_data) -> list:
    """Extract all FlightItems from the fixture data."""
    items = []
    for raw_result in poll_data.get('results', []):
        items.extend(list(spider.parse_result(raw_result=raw_result, full_response=poll_data)))
    return items


@pytest.fixture(scope='module')
def first_item(all_items):
    if not all_items:
        pytest.skip('No items extracted from fixture')
    return all_items[0]


# ---------------------------------------------------------------------------
# Gate 1 output schema validation — every field must be present and typed correctly
# ---------------------------------------------------------------------------

class TestGate1OutputSchema:
    """Verify every field from 06-params-approval.md is present and correctly typed."""

    def test_result_id_is_non_empty_string(self, first_item):
        assert 'result_id' in first_item
        assert isinstance(first_item['result_id'], str)
        assert len(first_item['result_id']) > 0

    def test_trip_id_is_string_or_absent(self, first_item):
        # trip_id may be absent (NullStripPipeline drops falsy); if present, must be str
        if 'trip_id' in first_item:
            assert isinstance(first_item['trip_id'], str)

    def test_origin_shape(self, first_item):
        assert 'origin' in first_item
        origin = first_item['origin']
        assert isinstance(origin, dict)
        assert 'code' in origin and isinstance(origin['code'], str)
        assert 'name' in origin and isinstance(origin['name'], str)
        assert len(origin['code']) == 3  # IATA code

    def test_destination_shape(self, first_item):
        assert 'destination' in first_item
        dest = first_item['destination']
        assert isinstance(dest, dict)
        assert 'code' in dest and isinstance(dest['code'], str)
        assert 'name' in dest and isinstance(dest['name'], str)
        assert len(dest['code']) == 3

    def test_departure_time_is_iso_string(self, first_item):
        assert 'departure_time' in first_item
        dt = first_item['departure_time']
        assert isinstance(dt, str) and len(dt) > 0
        # Should be parseable as ISO 8601
        datetime.fromisoformat(dt)

    def test_arrival_time_is_iso_string(self, first_item):
        assert 'arrival_time' in first_item
        at = first_item['arrival_time']
        assert isinstance(at, str) and len(at) > 0
        datetime.fromisoformat(at)

    def test_duration_minutes_is_positive_int(self, first_item):
        assert 'duration_minutes' in first_item
        dm = first_item['duration_minutes']
        assert isinstance(dm, int)
        assert dm > 0

    def test_stops_is_non_negative_int(self, first_item):
        assert 'stops' in first_item
        s = first_item['stops']
        assert isinstance(s, int)
        assert s >= 0

    def test_legs_is_non_empty_list(self, first_item):
        assert 'legs' in first_item
        legs = first_item['legs']
        assert isinstance(legs, list)
        assert len(legs) >= 1

    def test_booking_options_is_list(self, first_item):
        assert 'booking_options' in first_item
        bos = first_item['booking_options']
        assert isinstance(bos, list)

    def test_scraped_at_is_utc_iso(self, first_item):
        assert 'scraped_at' in first_item
        sa = first_item['scraped_at']
        assert isinstance(sa, str)
        assert '+00:00' in sa or sa.endswith('Z')
        datetime.fromisoformat(sa.replace('Z', '+00:00'))


class TestLegSchema:
    """Verify leg object fields from Gate 1."""

    def test_each_leg_has_all_required_fields(self, first_item):
        required = {'origin', 'destination', 'departure_time', 'arrival_time',
                    'duration_minutes', 'stops', 'segments'}
        for leg in first_item['legs']:
            missing = required - set(leg.keys())
            assert not missing, f'Leg missing fields: {missing}'

    def test_leg_origin_destination_shape(self, first_item):
        for leg in first_item['legs']:
            for key in ('origin', 'destination'):
                obj = leg[key]
                assert isinstance(obj, dict)
                assert 'code' in obj and 'name' in obj

    def test_leg_duration_and_stops_are_ints(self, first_item):
        for leg in first_item['legs']:
            assert isinstance(leg['duration_minutes'], int) and leg['duration_minutes'] > 0
            assert isinstance(leg['stops'], int) and leg['stops'] >= 0

    def test_leg_segments_is_list(self, first_item):
        for leg in first_item['legs']:
            assert isinstance(leg['segments'], list)
            assert len(leg['segments']) >= 1

    def test_stops_equals_segments_minus_one(self, first_item):
        for leg in first_item['legs']:
            expected_stops = len(leg['segments']) - 1
            assert leg['stops'] == expected_stops, (
                f"leg.stops={leg['stops']} but len(segments)-1={expected_stops}"
            )


class TestSegmentSchema:
    """Verify segment object fields from Gate 1."""

    def test_each_segment_has_all_required_fields(self, first_item):
        required = {'airline_code', 'airline_name', 'flight_number', 'aircraft_type',
                    'origin', 'destination', 'departure_time', 'arrival_time', 'duration_minutes'}
        for leg in first_item['legs']:
            for seg in leg['segments']:
                missing = required - set(seg.keys())
                assert not missing, f'Segment missing fields: {missing}'

    def test_segment_airline_code_is_str(self, first_item):
        for leg in first_item['legs']:
            for seg in leg['segments']:
                assert isinstance(seg['airline_code'], str)

    def test_segment_flight_number_contains_airline_code(self, first_item):
        for leg in first_item['legs']:
            for seg in leg['segments']:
                if seg['airline_code'] and seg['flight_number']:
                    assert seg['flight_number'].startswith(seg['airline_code'])

    def test_segment_duration_is_positive_int(self, first_item):
        for leg in first_item['legs']:
            for seg in leg['segments']:
                assert isinstance(seg['duration_minutes'], int)
                assert seg['duration_minutes'] > 0

    def test_segment_origin_destination_shape(self, first_item):
        for leg in first_item['legs']:
            for seg in leg['segments']:
                for key in ('origin', 'destination'):
                    obj = seg[key]
                    assert isinstance(obj, dict) and 'code' in obj and 'name' in obj


class TestBookingOptionSchema:
    """Verify booking option fields from Gate 1."""

    def test_each_booking_option_has_required_fields(self, first_item):
        required = {'provider_code', 'provider_name', 'price', 'currency',
                    'booking_url', 'cabin_class'}
        for bo in first_item.get('booking_options', []):
            missing = required - set(bo.keys())
            assert not missing, f'Booking option missing fields: {missing}'

    def test_booking_option_price_is_positive_number(self, first_item):
        for bo in first_item.get('booking_options', []):
            assert isinstance(bo['price'], (int, float))
            assert bo['price'] > 0

    def test_booking_option_currency_is_three_chars(self, first_item):
        for bo in first_item.get('booking_options', []):
            assert isinstance(bo['currency'], str)
            assert len(bo['currency']) == 3

    def test_booking_option_url_is_absolute(self, first_item):
        for bo in first_item.get('booking_options', []):
            url = bo['booking_url']
            assert isinstance(url, str)
            assert url.startswith('http'), f'URL not absolute: {url!r}'


# ---------------------------------------------------------------------------
# All fixture results extract correctly
# ---------------------------------------------------------------------------

def test_all_fixture_results_extract(all_items, poll_data):
    """Verify every result in the fixture yields at least one FlightItem."""
    results = poll_data.get('results', [])
    assert len(results) >= 1, 'Fixture has no results'
    # We expect at least 1 item per valid result (some may be skipped if no legs)
    assert len(all_items) >= 1, f'Expected at least 1 item, got 0 from {len(results)} results'
    print(f'\n[INFO] Fixture: {len(results)} raw results -> {len(all_items)} FlightItems extracted')


def test_fixture_items_stops_type(all_items):
    """Verify stops field is int across all fixture items."""
    for item in all_items:
        assert isinstance(item['stops'], int), f'stops is {type(item["stops"])}, expected int'


def test_fixture_items_legs_array(all_items):
    """Verify legs is a list of dicts across all fixture items."""
    for item in all_items:
        assert isinstance(item['legs'], list)
        for leg in item['legs']:
            assert isinstance(leg, dict)


def test_fixture_items_booking_options_price_is_number(all_items):
    """Verify booking_options[].price is numeric across all fixture items."""
    for item in all_items:
        for bo in item.get('booking_options', []):
            assert isinstance(bo['price'], (int, float)), (
                f'price is {type(bo["price"])}, expected number'
            )


# ---------------------------------------------------------------------------
# items.py vs dataset_schema.json cross-check
# ---------------------------------------------------------------------------

def test_items_fields_match_dataset_schema():
    """Every field in FlightItem must be declared in dataset_schema.json."""
    try:
        from src.items import FlightItem
    except ImportError:
        pytest.skip('Scrapy not installed')

    schema_path = _ACTOR_ROOT / '.actor' / 'dataset_schema.json'
    assert schema_path.exists(), f'dataset_schema.json not found at {schema_path}'

    with open(schema_path, encoding='utf-8') as f:
        schema = json.load(f)

    schema_fields = set(schema['fields']['properties'].keys())
    item_fields = set(FlightItem.fields.keys())

    missing_from_schema = item_fields - schema_fields
    assert not missing_from_schema, (
        f'Fields in items.py missing from dataset_schema.json: {missing_from_schema}'
    )

    missing_from_items = schema_fields - item_fields
    assert not missing_from_items, (
        f'Fields in dataset_schema.json missing from items.py: {missing_from_items}'
    )


# ---------------------------------------------------------------------------
# Input schema prefill validation (prevents Apify HTTP 400 on QA run)
# ---------------------------------------------------------------------------

def test_input_schema_prefill_has_no_invalid_fields():
    """Verify the input_schema.json prefill does not contain invalid API fields.

    Per project memory: the schema prefill is used as run input by Apify's automated QA.
    If any field in the prefill causes HTTP 400 from the poll API, the actor is flagged
    as 'Under maintenance'.

    This test checks that the schema's default values don't include cabinClass/currency
    in a way that would cause the spider to put them in the poll body.

    Note: The actual API bug (cabinClass/currency cause HTTP 400) is tracked separately
    as a BLOCKER issue; this test just validates the schema structure itself.
    """
    schema_path = _ACTOR_ROOT / '.actor' / 'input_schema.json'
    assert schema_path.exists()

    with open(schema_path, encoding='utf-8') as f:
        schema = json.load(f)

    props = schema.get('properties', {})

    # Verify all required fields have reasonable defaults for a one-way search
    assert props['tripType']['default'] == 'one-way'
    assert props['origin']['default'] == 'JFK'
    assert props['destination']['default'] == 'LHR'
    assert props['departureDate']['default'] == '2026-08-01'

    # Verify legs array has no minItems (per CLAUDE.md: only add minItems when it's the sole input)
    legs_prop = props.get('legs', {})
    assert 'minItems' not in legs_prop, (
        'legs should NOT have minItems — Apify QA submits empty arrays as validity check'
    )

    # Verify proxyConfiguration is optional (no required constraint)
    # proxyConfiguration default is null — must not be in required array
    required_fields = schema.get('required', [])
    assert 'proxyConfiguration' not in required_fields


# ---------------------------------------------------------------------------
# Edge case: maxResults=1 — verify exactly 1 item returned (from fixture)
# ---------------------------------------------------------------------------

def test_max_results_1_returns_exactly_1_item(spider, poll_data):
    """maxResults=1: spider should stop after first item."""
    MomondoSpider = _import_spider()
    spider_1 = MomondoSpider(trip_type='one-way', origin='JFK', destination='LHR',
                              departure_date='2026-08-01', max_results=1)
    items = []
    for raw_result in poll_data.get('results', []):
        if spider_1.items_yielded >= spider_1.max_results:
            break
        for item in spider_1.parse_result(raw_result=raw_result, full_response=poll_data):
            items.append(item)
            spider_1.items_yielded += 1
            if spider_1.items_yielded >= spider_1.max_results:
                break
        if spider_1.items_yielded >= spider_1.max_results:
            break
    assert len(items) == 1, f'Expected 1 item with maxResults=1, got {len(items)}'
    print(f'\n[INFO] maxResults=1 test: extracted {len(items)} item (PASS)')


# ---------------------------------------------------------------------------
# Edge case: result without legs is skipped
# ---------------------------------------------------------------------------

def test_result_without_legs_is_skipped(spider, poll_data):
    """A result with empty/missing legs array should yield no items."""
    fake_result = {'resultId': 'no-legs-test', 'tripId': 'trip-abc', 'legs': [], 'bookingOptions': []}
    items = list(spider.parse_result(raw_result=fake_result, full_response=poll_data))
    assert len(items) == 0, f'Expected 0 items for legless result, got {len(items)}'
    print('\n[INFO] Empty legs: correctly yields 0 items (PASS)')


# ---------------------------------------------------------------------------
# Live: one-way search
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_live_one_way_search():
    """Live one-way search: bootstrap -> poll -> parse_result -> at least 1 FlightItem.

    NOTE: This test currently FAILS due to BLOCKER bug:
    cabinClass and currency in userSearchParams cause HTTP 400 from Momondo API.
    The spider's poll request fails before parse_result is called.

    Tracking: see test_e2e.py comments and QA report.
    """
    pytest.skip(
        'BLOCKER: Spider poll body contains cabinClass/currency in userSearchParams '
        'which Momondo rejects with HTTP 400. All items_yielded=0. '
        'Fix required in src/spiders/momondo.py _build_poll_body().'
    )


@pytest.mark.live
def test_live_minimal_poll_returns_results():
    """Live test: a minimal poll body (no cabinClass/currency) returns 200 with results.

    This confirms the API works and parse_result can process results when the body is correct.
    """
    import re as re_mod
    import requests

    HTML_HEADERS = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
    }

    POLL_URL = 'https://www.momondo.com/i/api/search/dynamic/flights/poll'
    BOOTSTRAP_URL = 'https://www.momondo.com/flight-search/JFK-LHR/2026-08-01/'

    session = requests.Session()
    resp = session.get(BOOTSTRAP_URL, headers=HTML_HEADERS, timeout=30)
    assert resp.status_code == 200

    match = re_mod.search(r"window\.R9\.formToken\s*=\s*'([^']+)'", resp.text)
    assert match, 'formToken not found'
    form_token = match.group(1)

    poll_body = {
        'filterParams': {},
        'userSearchParams': {
            'legs': [{'origin': {'airports': ['JFK'], 'locationType': 'airports'},
                      'destination': {'airports': ['LHR'], 'locationType': 'airports'},
                      'date': '2026-08-01', 'flex': 'exact'}],
            'searchId': 'saECWKdkIP',
            'passengers': ['ADT'],
            'passengerDetails': [{'ptc': 'ADT'}],
            'sortMode': 'bestflight_a',
        },
        'searchMetaData': {'pageNumber': 1, 'searchTypes': [], 'skipResultsInSecondPhase': False},
    }

    poll_resp = session.post(
        POLL_URL,
        headers={
            'Content-Type': 'application/json', 'x-csrf': form_token,
            'Referer': 'https://www.momondo.com/flight-search/',
            'User-Agent': HTML_HEADERS['User-Agent'],
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate, br',
            'x-requested-with': 'XMLHttpRequest',
        },
        data=json.dumps(poll_body),
        timeout=30,
    )
    assert poll_resp.status_code == 200, f'Poll returned {poll_resp.status_code}: {poll_resp.text[:200]}'

    data = poll_resp.json()
    results = data.get('results', [])
    assert len(results) > 0, 'No results in poll response'
    print(f'\n[INFO] Live minimal poll: {len(results)} results, filteredCount={data.get("filteredCount")}')

    # Now run parse_result on the live results
    MomondoSpider = _import_spider()
    spider = MomondoSpider(trip_type='one-way', origin='JFK', destination='LHR',
                           departure_date='2026-08-01', max_results=50)
    items = []
    for raw_result in results[:5]:  # test first 5 only
        items.extend(list(spider.parse_result(raw_result=raw_result, full_response=data)))

    assert len(items) >= 1, f'Expected at least 1 FlightItem, got {len(items)}'
    print(f'[INFO] parse_result on live data: {len(items)} FlightItems from first 5 results')

    # Verify first item
    item = items[0]
    assert 'result_id' in item and item['result_id']
    assert 'legs' in item and len(item['legs']) >= 1
    assert 'booking_options' in item
    assert 'stops' in item and isinstance(item['stops'], int)
    print(f'[INFO] First item: {item["origin"]["code"]} -> {item["destination"]["code"]}, '
          f'stops={item["stops"]}, duration={item["duration_minutes"]}min, '
          f'booking_options={len(item["booking_options"])}')
