"""Parser tests for MomondoSpider.parse_result().

Tests are unit tests only — they load a saved fixture (tests/fixtures/poll_response.json)
and verify that FlightItem extraction produces the correct shape and field types.

All tests run offline — no internet connection required.

Run: pytest tests/test_parser.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup: add actor root so we can import src.*
# ---------------------------------------------------------------------------

_ACTOR_ROOT = Path(__file__).parent.parent
if str(_ACTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_ACTOR_ROOT))

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent / 'fixtures' / 'poll_response.json'


@pytest.fixture(scope='module')
def poll_data() -> dict:
    """Load the saved poll response fixture."""
    if not FIXTURE_PATH.exists():
        pytest.skip(
            f'Fixture not found: {FIXTURE_PATH}. '
            'Run scripts/capture_poll_fixture2.py first to generate it.'
        )
    with open(FIXTURE_PATH, encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture(scope='module')
def spider():
    """Create a minimal MomondoSpider instance for calling parse_result."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError as e:
        pytest.skip(f'Could not import MomondoSpider: {e}')
    return MomondoSpider(trip_type='one-way', origin='JFK', destination='LHR', departure_date='2026-09-01')


@pytest.fixture(scope='module')
def first_valid_result(poll_data) -> dict:
    """Return the first result from the fixture that has legs (not an inline ad)."""
    for r in poll_data.get('results', []):
        if r.get('legs') and r.get('resultId') and r.get('tripId'):
            return r
    pytest.skip('No valid result (with legs+resultId+tripId) found in fixture.')


@pytest.fixture(scope='module')
def extracted_item(spider, first_valid_result, poll_data):
    """Run parse_result() on the first valid result and return the FlightItem."""
    items = list(spider.parse_result(
        raw_result=first_valid_result,
        full_response=poll_data,
    ))
    assert len(items) == 1, f'Expected 1 item, got {len(items)}'
    return items[0]


# ---------------------------------------------------------------------------
# Top-level FlightItem shape tests
# ---------------------------------------------------------------------------

EXPECTED_TOP_LEVEL_FIELDS = {
    'result_id', 'trip_id', 'origin', 'destination', 'departure_time',
    'arrival_time', 'duration_minutes', 'stops', 'legs', 'booking_options',
    'scraped_at',
}


def test_flight_item_has_all_top_level_fields(extracted_item):
    """All 11 approved top-level fields must be present in the item."""
    item_keys = set(extracted_item.keys())
    missing = EXPECTED_TOP_LEVEL_FIELDS - item_keys
    assert not missing, f'Missing fields: {missing}'
    print(f'\n[PASS] All {len(EXPECTED_TOP_LEVEL_FIELDS)} top-level fields present')


def test_result_id_is_non_empty_string(extracted_item):
    """result_id must be a non-empty string."""
    assert isinstance(extracted_item['result_id'], str)
    assert extracted_item['result_id'], 'result_id must not be empty'
    print(f'\n[PASS] result_id = {extracted_item["result_id"]!r}')


def test_trip_id_is_string(extracted_item):
    """trip_id must be a string (can be empty for edge cases, but must be present)."""
    assert isinstance(extracted_item['trip_id'], str)
    print(f'\n[PASS] trip_id = {extracted_item["trip_id"]!r}')


def test_origin_is_correct_shape(extracted_item):
    """origin must be a dict with 'code' and 'name' keys."""
    origin = extracted_item['origin']
    assert isinstance(origin, dict), f'origin should be dict, got {type(origin).__name__}'
    assert 'code' in origin, "origin must have 'code' key"
    assert 'name' in origin, "origin must have 'name' key"
    assert isinstance(origin['code'], str), 'origin.code must be str'
    assert isinstance(origin['name'], str), 'origin.name must be str'
    assert len(origin['code']) == 3, f'origin.code should be 3-letter IATA, got {origin["code"]!r}'
    print(f'\n[PASS] origin = {origin}')


def test_destination_is_correct_shape(extracted_item):
    """destination must be a dict with 'code' and 'name' keys."""
    dest = extracted_item['destination']
    assert isinstance(dest, dict), f'destination should be dict, got {type(dest).__name__}'
    assert 'code' in dest, "destination must have 'code' key"
    assert 'name' in dest, "destination must have 'name' key"
    assert isinstance(dest['code'], str), 'destination.code must be str'
    assert isinstance(dest['name'], str), 'destination.name must be str'
    print(f'\n[PASS] destination = {dest}')


def test_departure_time_is_iso_string(extracted_item):
    """departure_time must be a non-empty ISO 8601 string."""
    dt = extracted_item['departure_time']
    assert isinstance(dt, str), f'departure_time must be str, got {type(dt).__name__}'
    assert dt, 'departure_time must not be empty'
    assert 'T' in dt, f'departure_time should be ISO 8601 with T separator, got {dt!r}'
    print(f'\n[PASS] departure_time = {dt!r}')


def test_arrival_time_is_iso_string(extracted_item):
    """arrival_time must be a non-empty ISO 8601 string."""
    at = extracted_item['arrival_time']
    assert isinstance(at, str), f'arrival_time must be str, got {type(at).__name__}'
    assert at, 'arrival_time must not be empty'
    assert 'T' in at, f'arrival_time should be ISO 8601 with T separator, got {at!r}'
    print(f'\n[PASS] arrival_time = {at!r}')


def test_duration_minutes_is_positive_integer(extracted_item):
    """duration_minutes must be a positive integer."""
    dur = extracted_item['duration_minutes']
    assert isinstance(dur, int), f'duration_minutes must be int, got {type(dur).__name__}'
    assert dur > 0, f'duration_minutes must be positive, got {dur}'
    print(f'\n[PASS] duration_minutes = {dur}')


def test_stops_is_non_negative_integer(extracted_item):
    """stops must be a non-negative integer."""
    stops = extracted_item['stops']
    assert isinstance(stops, int), f'stops must be int, got {type(stops).__name__}'
    assert stops >= 0, f'stops must be >= 0, got {stops}'
    print(f'\n[PASS] stops = {stops}')


def test_scraped_at_is_utc_iso_string(extracted_item):
    """scraped_at must be a non-empty UTC ISO 8601 string (contains timezone offset or Z)."""
    scraped = extracted_item['scraped_at']
    assert isinstance(scraped, str), f'scraped_at must be str, got {type(scraped).__name__}'
    assert scraped, 'scraped_at must not be empty'
    assert 'T' in scraped, f'scraped_at should be ISO 8601, got {scraped!r}'
    # Should contain timezone info: either 'Z' or '+00:00'
    assert '+' in scraped or scraped.endswith('Z'), (
        f'scraped_at should include UTC timezone info, got {scraped!r}'
    )
    print(f'\n[PASS] scraped_at = {scraped!r}')


# ---------------------------------------------------------------------------
# Legs array tests
# ---------------------------------------------------------------------------

def test_legs_is_non_empty_list(extracted_item):
    """legs must be a non-empty list."""
    legs = extracted_item['legs']
    assert isinstance(legs, list), f'legs must be list, got {type(legs).__name__}'
    assert len(legs) > 0, 'legs must not be empty'
    print(f'\n[PASS] legs is a list with {len(legs)} element(s)')


EXPECTED_LEG_FIELDS = {
    'origin', 'destination', 'departure_time', 'arrival_time',
    'duration_minutes', 'stops', 'segments',
}


def test_each_leg_has_all_required_fields(extracted_item):
    """Every leg dict must have all 7 required fields."""
    legs = extracted_item['legs']
    for i, leg in enumerate(legs):
        assert isinstance(leg, dict), f'leg[{i}] must be dict, got {type(leg).__name__}'
        missing = EXPECTED_LEG_FIELDS - set(leg.keys())
        assert not missing, f'leg[{i}] missing fields: {missing}'
    print(f'\n[PASS] All {len(legs)} leg(s) have all 7 required fields')


def test_leg_origin_destination_shape(extracted_item):
    """Each leg's origin/destination must be {code, name} dicts."""
    for i, leg in enumerate(extracted_item['legs']):
        for field in ('origin', 'destination'):
            val = leg[field]
            assert isinstance(val, dict), f'leg[{i}].{field} must be dict'
            assert 'code' in val, f'leg[{i}].{field} must have code'
            assert 'name' in val, f'leg[{i}].{field} must have name'
            assert isinstance(val['code'], str), f'leg[{i}].{field}.code must be str'
            assert len(val['code']) == 3, f'leg[{i}].{field}.code should be 3-letter IATA'
    print('\n[PASS] All leg origin/destination are correct {code, name} dicts')


def test_leg_duration_and_stops_are_integers(extracted_item):
    """Each leg's duration_minutes and stops must be integers."""
    for i, leg in enumerate(extracted_item['legs']):
        assert isinstance(leg['duration_minutes'], int), (
            f'leg[{i}].duration_minutes must be int, got {type(leg["duration_minutes"]).__name__}'
        )
        assert leg['duration_minutes'] > 0, f'leg[{i}].duration_minutes must be positive'
        assert isinstance(leg['stops'], int), (
            f'leg[{i}].stops must be int, got {type(leg["stops"]).__name__}'
        )
        assert leg['stops'] >= 0, f'leg[{i}].stops must be >= 0'
    print('\n[PASS] All leg duration_minutes/stops are correct integers')


def test_leg_segments_is_list(extracted_item):
    """Each leg's segments must be a non-empty list."""
    for i, leg in enumerate(extracted_item['legs']):
        segs = leg['segments']
        assert isinstance(segs, list), f'leg[{i}].segments must be list'
        assert len(segs) > 0, f'leg[{i}].segments must not be empty'
    print('\n[PASS] All legs have non-empty segments lists')


def test_stops_equals_segments_minus_one(extracted_item):
    """Each leg's stops count must equal len(segments) - 1."""
    for i, leg in enumerate(extracted_item['legs']):
        expected_stops = len(leg['segments']) - 1
        assert leg['stops'] == expected_stops, (
            f'leg[{i}].stops={leg["stops"]} but len(segments)-1={expected_stops}'
        )
    print('\n[PASS] leg.stops == len(segments) - 1 for all legs')


# ---------------------------------------------------------------------------
# Segment tests
# ---------------------------------------------------------------------------

EXPECTED_SEGMENT_FIELDS = {
    'airline_code', 'airline_name', 'flight_number', 'aircraft_type',
    'origin', 'destination', 'departure_time', 'arrival_time', 'duration_minutes',
}


def test_each_segment_has_all_required_fields(extracted_item):
    """Every segment dict must have all 9 required fields."""
    for i, leg in enumerate(extracted_item['legs']):
        for j, seg in enumerate(leg['segments']):
            assert isinstance(seg, dict), f'legs[{i}].segments[{j}] must be dict'
            missing = EXPECTED_SEGMENT_FIELDS - set(seg.keys())
            assert not missing, f'legs[{i}].segments[{j}] missing fields: {missing}'
    print('\n[PASS] All segments have all 9 required fields')


def test_segment_airline_code_is_string(extracted_item):
    """Segment airline_code must be a non-empty string (IATA code)."""
    for i, leg in enumerate(extracted_item['legs']):
        for j, seg in enumerate(leg['segments']):
            assert isinstance(seg['airline_code'], str), (
                f'legs[{i}].segments[{j}].airline_code must be str'
            )
            assert seg['airline_code'], f'legs[{i}].segments[{j}].airline_code must not be empty'
    print('\n[PASS] All segment airline_codes are non-empty strings')


def test_segment_flight_number_contains_airline_code(extracted_item):
    """flight_number must start with the airline_code (e.g. 'BA177')."""
    for i, leg in enumerate(extracted_item['legs']):
        for j, seg in enumerate(leg['segments']):
            ac = seg['airline_code']
            fn = seg['flight_number']
            if ac and fn:
                assert fn.startswith(ac), (
                    f'legs[{i}].segments[{j}].flight_number={fn!r} should start with '
                    f'airline_code={ac!r}'
                )
    print('\n[PASS] All segment flight_numbers start with airline_code')


def test_segment_origin_destination_shape(extracted_item):
    """Segment origin/destination must be {code, name} dicts with 3-letter IATA codes."""
    for i, leg in enumerate(extracted_item['legs']):
        for j, seg in enumerate(leg['segments']):
            for field in ('origin', 'destination'):
                val = seg[field]
                assert isinstance(val, dict), f'legs[{i}].segments[{j}].{field} must be dict'
                assert 'code' in val
                assert 'name' in val
                assert len(val['code']) == 3, (
                    f'legs[{i}].segments[{j}].{field}.code should be 3-letter IATA, '
                    f'got {val["code"]!r}'
                )
    print('\n[PASS] All segment origin/destination are correct {code, name} dicts')


def test_segment_duration_is_positive_integer(extracted_item):
    """Segment duration_minutes must be a positive integer."""
    for i, leg in enumerate(extracted_item['legs']):
        for j, seg in enumerate(leg['segments']):
            dur = seg['duration_minutes']
            assert isinstance(dur, int), (
                f'legs[{i}].segments[{j}].duration_minutes must be int'
            )
            assert dur > 0, f'legs[{i}].segments[{j}].duration_minutes must be positive'
    print('\n[PASS] All segment duration_minutes are positive integers')


# ---------------------------------------------------------------------------
# Booking options tests
# ---------------------------------------------------------------------------

EXPECTED_BOOKING_OPTION_FIELDS = {
    'provider_code', 'provider_name', 'price', 'currency', 'booking_url', 'cabin_class',
}


def test_booking_options_is_list(extracted_item):
    """booking_options must be a list (can be empty for some results)."""
    bos = extracted_item['booking_options']
    assert isinstance(bos, list), f'booking_options must be list, got {type(bos).__name__}'
    print(f'\n[PASS] booking_options is a list with {len(bos)} element(s)')


def test_each_booking_option_has_all_required_fields(extracted_item):
    """Every booking option dict must have all 6 required fields."""
    bos = extracted_item['booking_options']
    for i, bo in enumerate(bos):
        assert isinstance(bo, dict), f'booking_options[{i}] must be dict'
        missing = EXPECTED_BOOKING_OPTION_FIELDS - set(bo.keys())
        assert not missing, f'booking_options[{i}] missing fields: {missing}'
    print(f'\n[PASS] All {len(bos)} booking option(s) have all 6 required fields')


def test_booking_option_price_is_positive_number(extracted_item):
    """Booking option price must be a positive number."""
    bos = extracted_item['booking_options']
    for i, bo in enumerate(bos):
        price = bo['price']
        assert isinstance(price, (int, float)), (
            f'booking_options[{i}].price must be numeric, got {type(price).__name__}'
        )
        assert price > 0, f'booking_options[{i}].price must be positive, got {price}'
    if bos:
        print(f'\n[PASS] All booking option prices are positive numbers (first: {bos[0]["price"]})')
    else:
        print('\n[PASS] booking_options is empty (no options to validate prices for)')


def test_booking_option_url_is_absolute(extracted_item):
    """Booking option booking_url must be an absolute HTTPS URL."""
    bos = extracted_item['booking_options']
    for i, bo in enumerate(bos):
        url = bo['booking_url']
        assert isinstance(url, str), f'booking_options[{i}].booking_url must be str'
        if url:  # empty is allowed if Momondo doesn't provide one
            assert url.startswith('https://'), (
                f'booking_options[{i}].booking_url should be absolute HTTPS URL, got {url[:60]!r}'
            )
    if bos:
        print(f'\n[PASS] All booking_url values are absolute HTTPS URLs')


def test_booking_option_currency_is_three_chars(extracted_item):
    """Currency must be a 3-character ISO 4217 code."""
    bos = extracted_item['booking_options']
    for i, bo in enumerate(bos):
        currency = bo['currency']
        assert isinstance(currency, str), f'booking_options[{i}].currency must be str'
        if currency:
            assert len(currency) == 3, (
                f'booking_options[{i}].currency should be 3-char ISO code, got {currency!r}'
            )
    if bos:
        print(f'\n[PASS] All booking option currencies are valid 3-char codes (first: {bos[0]["currency"]})')


# ---------------------------------------------------------------------------
# Empty result handling: result without legs should produce 0 items
# ---------------------------------------------------------------------------

def test_result_without_legs_produces_no_items(spider, poll_data):
    """A result dict with empty/absent legs must be skipped (yields nothing)."""
    empty_result = {'resultId': 'test-no-legs', 'tripId': 'test-trip', 'legs': [], 'bookingOptions': []}
    items = list(spider.parse_result(raw_result=empty_result, full_response=poll_data))
    assert len(items) == 0, f'Expected 0 items for legless result, got {len(items)}'
    print('\n[PASS] Result with no legs produces 0 items (correctly skipped)')


def test_result_without_trip_id_still_extracts(spider, poll_data, first_valid_result):
    """A result with no tripId should still extract; trip_id may be absent or empty.

    When tripId is absent, add_value('trip_id', '') is called. TakeFirst() treats
    the empty string as falsy and the field is absent from the loaded item (then
    NullStripPipeline would drop it). This is acceptable — trip_id is optional.
    The important thing is that 1 item is still yielded.
    """
    r = dict(first_valid_result)
    r.pop('tripId', None)
    items = list(spider.parse_result(raw_result=r, full_response=poll_data))
    assert len(items) == 1, f'Expected 1 item even without tripId, got {len(items)}'
    # trip_id may be absent (empty string is dropped by TakeFirst) — that is acceptable
    if 'trip_id' in items[0]:
        assert isinstance(items[0]['trip_id'], str), 'trip_id must be str if present'
    print('\n[PASS] Result without tripId still extracts 1 item (trip_id may be absent)')


# ---------------------------------------------------------------------------
# Multiple results: process all 3 fixture results and check shapes
# ---------------------------------------------------------------------------

def test_all_fixture_results_extract_correctly(spider, poll_data):
    """Process all results in the fixture and verify each valid one produces 1 FlightItem."""
    results = poll_data.get('results', [])
    valid_count = 0
    skipped_count = 0
    for r in results:
        items = list(spider.parse_result(raw_result=r, full_response=poll_data))
        if r.get('legs') and r.get('resultId') and r.get('tripId'):
            assert len(items) == 1, (
                f'Valid result {r.get("resultId")!r} should produce 1 item, got {len(items)}'
            )
            item = items[0]
            # Quick shape check
            assert EXPECTED_TOP_LEVEL_FIELDS.issubset(set(item.keys())), (
                f'Item for {r.get("resultId")!r} missing fields: '
                f'{EXPECTED_TOP_LEVEL_FIELDS - set(item.keys())}'
            )
            valid_count += 1
        else:
            # Should produce 0 items (no legs / placeholder)
            assert len(items) == 0, (
                f'Non-valid result {r.get("resultId")!r} should produce 0 items, got {len(items)}'
            )
            skipped_count += 1

    print(f'\n[PASS] Processed {len(results)} fixture results: {valid_count} extracted, {skipped_count} skipped')


# ---------------------------------------------------------------------------
# Client-side filter tests: cabinClass and maxStops (BLOCKER fix 2026-05-14)
# ---------------------------------------------------------------------------

def _make_minimal_poll_data(leg_segment_count: int = 1, cabin_display: str = 'Economy') -> tuple[dict, dict]:
    """Build a minimal synthetic poll response + result dict for filter testing.

    Args:
        leg_segment_count: Number of segments in the single leg (stops = leg_segment_count - 1).
        cabin_display: The cabinDisplay string for the single booking option.

    Returns:
        (poll_data, raw_result) tuple suitable for passing to spider.parse_result().
    """
    segments = {}
    seg_refs = []
    for i in range(leg_segment_count):
        seg_id = f'seg_{i}'
        segments[seg_id] = {
            'airline': 'BA',
            'flightNumber': str(100 + i),
            'origin': 'JFK',
            'destination': 'LHR',
            'departure': '2026-08-01T10:00:00',
            'arrival': '2026-08-01T22:00:00',
            'duration': 420,
            'equipmentTypeName': 'Boeing 777',
        }
        seg_refs.append({'id': seg_id})

    leg_id = 'leg_0'
    legs_data = {
        leg_id: {
            'departure': '2026-08-01T10:00:00',
            'arrival': '2026-08-01T22:00:00',
            'duration': 420,
            'segments': seg_refs,
        }
    }

    raw_result = {
        'resultId': 'test-result-001',
        'tripId': 'test-trip-001',
        'legs': [{'id': leg_id}],
        'bookingOptions': [
            {
                'providerCode': 'BA',
                'bookingUrl': {'url': 'https://www.momondo.com/booking/test', 'urlType': 'absolute'},
                'displayPrice': {'price': 650.0},
                'currency': 'USD',
                'legFarings': [
                    {
                        'segmentFarings': [
                            {'cabinDisplay': cabin_display}
                        ]
                    }
                ],
            }
        ],
    }

    poll_data = {
        'results': [raw_result],
        'legs': legs_data,
        'segments': segments,
        'providers': {'BA': {'displayName': 'British Airways'}},
        'airports': {
            'JFK': {'displayName': 'John F. Kennedy International'},
            'LHR': {'displayName': 'London Heathrow'},
        },
        'filterData': {
            'airlines': {'items': [{'id': 'BA', 'displayValue': 'British Airways'}]},
        },
    }

    return poll_data, raw_result


def test_cabin_filter_drops_economy_when_business_requested(poll_data):
    """parse_result() must drop a result with only Economy cabin when BUSINESS is requested.

    BLOCKER fix (2026-05-14): cabinClass filtering is now done client-side.
    """
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(
        trip_type='one-way',
        origin='JFK',
        destination='LHR',
        departure_date='2026-09-01',
        cabin_class='BUSINESS',
    )

    synthetic_poll, raw_result = _make_minimal_poll_data(
        leg_segment_count=1,
        cabin_display='Economy',  # only Economy booking option
    )

    items = list(spider.parse_result(raw_result=raw_result, full_response=synthetic_poll))
    assert len(items) == 0, (
        f'Expected 0 items — Economy-only result should be dropped when BUSINESS requested, '
        f'got {len(items)} items'
    )
    assert spider.skipped_filtered == 1, (
        f'skipped_filtered counter should be 1, got {spider.skipped_filtered}'
    )
    print('\n[PASS] Cabin filter: Economy-only result dropped when BUSINESS requested')


def test_cabin_filter_keeps_business_when_business_requested(poll_data):
    """parse_result() must keep a result with Business cabin when BUSINESS is requested."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(
        trip_type='one-way',
        origin='JFK',
        destination='LHR',
        departure_date='2026-09-01',
        cabin_class='BUSINESS',
    )

    synthetic_poll, raw_result = _make_minimal_poll_data(
        leg_segment_count=1,
        cabin_display='Business',
    )

    items = list(spider.parse_result(raw_result=raw_result, full_response=synthetic_poll))
    assert len(items) == 1, (
        f'Expected 1 item — Business result should be kept when BUSINESS requested, '
        f'got {len(items)}'
    )
    assert spider.skipped_filtered == 0
    print('\n[PASS] Cabin filter: Business result kept when BUSINESS requested')


def test_cabin_filter_no_filtering_when_economy_requested(poll_data):
    """parse_result() must NOT filter any result when ECONOMY is the requested class.

    Rationale: Momondo returns economy by default; filtering would over-drop
    mixed-class results that include economy options.
    """
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(
        trip_type='one-way',
        origin='JFK',
        destination='LHR',
        departure_date='2026-09-01',
        cabin_class='ECONOMY',  # default — no filtering
    )

    # Even a Business cabin result should NOT be dropped for ECONOMY request
    synthetic_poll, raw_result = _make_minimal_poll_data(
        leg_segment_count=1,
        cabin_display='Business',
    )

    items = list(spider.parse_result(raw_result=raw_result, full_response=synthetic_poll))
    assert len(items) == 1, (
        f'Expected 1 item — no cabin filtering when ECONOMY requested, got {len(items)}'
    )
    print('\n[PASS] Cabin filter: no filtering applied when ECONOMY is requested (correct)')


def test_stops_filter_drops_flight_exceeding_max_stops(poll_data):
    """parse_result() must drop a flight with 3 stops when max_stops=1.

    BLOCKER fix (2026-05-14): maxStops filtering is now done client-side.
    """
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(
        trip_type='one-way',
        origin='JFK',
        destination='LHR',
        departure_date='2026-09-01',
        max_stops=1,
    )

    # 4 segments = 3 stops
    synthetic_poll, raw_result = _make_minimal_poll_data(leg_segment_count=4)

    items = list(spider.parse_result(raw_result=raw_result, full_response=synthetic_poll))
    assert len(items) == 0, (
        f'Expected 0 items — 3-stop flight must be dropped when max_stops=1, '
        f'got {len(items)} items'
    )
    assert spider.skipped_filtered == 1, (
        f'skipped_filtered counter should be 1, got {spider.skipped_filtered}'
    )
    print('\n[PASS] Stops filter: 3-stop flight dropped when max_stops=1')


def test_stops_filter_keeps_flight_within_max_stops(poll_data):
    """parse_result() must keep a 1-stop flight when max_stops=1."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(
        trip_type='one-way',
        origin='JFK',
        destination='LHR',
        departure_date='2026-09-01',
        max_stops=1,
    )

    # 2 segments = 1 stop — exactly at the limit
    synthetic_poll, raw_result = _make_minimal_poll_data(leg_segment_count=2)

    items = list(spider.parse_result(raw_result=raw_result, full_response=synthetic_poll))
    assert len(items) == 1, (
        f'Expected 1 item — 1-stop flight should be kept when max_stops=1, '
        f'got {len(items)}'
    )
    assert spider.skipped_filtered == 0
    print('\n[PASS] Stops filter: 1-stop flight kept when max_stops=1')


def test_stops_filter_no_filtering_when_max_stops_none(poll_data):
    """parse_result() must not apply stops filtering when max_stops is None."""
    try:
        from src.spiders.momondo import MomondoSpider
    except ImportError:
        pytest.skip('Scrapy not installed — skipping unit import test')

    spider = MomondoSpider(
        trip_type='one-way',
        origin='JFK',
        destination='LHR',
        departure_date='2026-09-01',
        max_stops=None,  # no stops filter
    )

    # 4 segments = 3 stops — should NOT be dropped when max_stops=None
    synthetic_poll, raw_result = _make_minimal_poll_data(leg_segment_count=4)

    items = list(spider.parse_result(raw_result=raw_result, full_response=synthetic_poll))
    assert len(items) == 1, (
        f'Expected 1 item — no stops filtering when max_stops=None, got {len(items)}'
    )
    print('\n[PASS] Stops filter: no filtering when max_stops=None')
