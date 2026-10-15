"""Unit tests for v1.1: bag-inclusive pricing, phase polling, browser transport, failure exit.

Offline only — the poll fixture plus hand-built fee blocks shaped like live 2026-10-15 responses.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from scrapy.http import Request, TextResponse

from src.browser import PageResult
from src.middlewares import BrowserTransportMiddleware
from src.spiders.momondo import (
    POLL_URL,
    MomondoSpider,
    _baggage_policy_links,
    _bag,
    _fee_fields,
)

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'poll_response.json').read_text(encoding='utf-8'))

# Fee block of a fare priced with 1 checked bag (Booking.com, LHR-FAO, 2 adults).
FEES_WITH_BAG = {
    'basePrice': {'price': 371, 'currency': 'USD'},
    'totalPrice': {'price': 847, 'currency': 'USD'},
    'carryOnBagData': {'status': 'INCLUDED', 'displayPrice': {'price': 0, 'currency': 'USD'}, 'numSelected': 0},
    'checkedBagData': {'status': 'INCLUDED', 'displayPrice': {'price': 53, 'currency': 'USD'}, 'numSelected': 1},
}


def _spider(**kwargs) -> MomondoSpider:
    defaults = {'trip_type': 'one-way', 'origin': 'LHR', 'destination': 'FAO', 'departure_date': '2026-11-20'}
    return MomondoSpider(**{**defaults, **kwargs})


def _poll_response(data: dict, **cb_kwargs) -> tuple[TextResponse, dict]:
    request = Request(POLL_URL, method='POST')
    response = TextResponse(url=POLL_URL, status=200, body=json.dumps(data), encoding='utf-8', request=request)
    kwargs = {'legs': [], 'page': 1, 'flex_date': None, 'search_id': None, 'page1_retry_count': 0, **cb_kwargs}
    return response, kwargs


# --------------------------------------------------------------------------- bag filter


@pytest.mark.parametrize(
    ('checked', 'carry_on', 'expected'),
    [(0, False, {}), (1, False, {'fs': 'bfc=1'}), (2, False, {'fs': 'bfc=2'}),
     (0, True, {'fs': 'cfc=1'}), (1, True, {'fs': 'cfc=1;bfc=1'})],
)
def test_filter_params_carry_bag_selection(checked, carry_on, expected):
    spider = _spider(checked_bags=checked, carry_on_bag=carry_on)
    assert spider._build_filter_params() == expected
    assert spider._build_poll_body(legs=[], page=1)['filterParams'] == expected


def test_checked_bags_clamped_to_site_range():
    assert _spider(checked_bags=5).checked_bags == 2
    assert _spider(checked_bags=-1).checked_bags == 0


# --------------------------------------------------------------------------- fee parsing


def test_fee_fields_with_requested_bag():
    fields = _fee_fields(FEES_WITH_BAG)
    assert fields['base_price'] == 371
    assert fields['total_price_all_passengers'] == 847
    assert fields['checked_bag'] == {'status': 'included', 'fee': 53, 'bags_in_price': 1}
    assert fields['carry_on_bag'] == {'status': 'included', 'fee': 0, 'bags_in_price': 0}


def test_fee_fields_without_bag_selection_from_fixture():
    statuses = {_fee_fields(bo['fees'])['checked_bag']['status']
                for r in FIXTURE['results'] for bo in r.get('bookingOptions') or [] if bo.get('fees')}
    assert statuses == {'unknown', 'fee', 'included'}
    fee_bag = next(_fee_fields(bo['fees'])['checked_bag'] for r in FIXTURE['results']
                   for bo in r.get('bookingOptions') or [] if bo.get('fees', {}).get('checkedBagData', {}).get('status') == 'FEE')
    assert fee_bag['fee'] > 0 and fee_bag['bags_in_price'] == 0


def test_missing_fee_data_is_omitted_not_null():
    assert _fee_fields(None) == {}
    assert _bag(None) == {'status': 'unknown'}
    assert _bag({'status': 'UNKNOWN', 'numSelected': 0}) == {'status': 'unknown', 'bags_in_price': 0}


def test_baggage_policy_links_one_per_airline_in_flight_order():
    legs = [{'segments': [{'airline_code': 'LH', 'airline_name': 'Lufthansa'}, {'airline_code': 'TP', 'airline_name': 'TAP'}]},
            {'segments': [{'airline_code': 'TP', 'airline_name': 'TAP'}, {'airline_code': 'XX', 'airline_name': 'No URL'}]}]
    airlines = {'LH': {'airlineFeeUrl': {'url': 'https://lh.example/bags'}}, 'TP': {'airlineFeeUrl': {'url': 'https://tp.example/bags'}}}
    assert _baggage_policy_links(legs, airlines) == [
        {'airline_code': 'LH', 'airline_name': 'Lufthansa', 'url': 'https://lh.example/bags'},
        {'airline_code': 'TP', 'airline_name': 'TAP', 'url': 'https://tp.example/bags'},
    ]


def test_parsed_item_carries_bag_fields():
    spider = _spider(checked_bags=1)
    result = next(r for r in FIXTURE['results'] if r.get('legs') and r.get('bookingOptions'))
    item = next(spider.parse_result(raw_result=result, full_response=FIXTURE))
    assert item['bags_requested'] == {'checked_bags': 1, 'carry_on_bag': False}
    assert all(link['url'].startswith('http') for link in item['baggage_policy_links'])
    option = item['booking_options'][0]
    assert {'base_price', 'total_price_all_passengers', 'checked_bag', 'carry_on_bag'} <= option.keys()


# --------------------------------------------------------------------------- phase polling


def test_incomplete_search_is_re_polled_without_emitting_items():
    spider = _spider()
    response, kwargs = _poll_response({**FIXTURE, 'status': 'first-phase', 'searchId': 'S1'})
    out = list(spider.parse_poll(response, **kwargs))
    assert len(out) == 1 and isinstance(out[0], Request)
    assert out[0].cb_kwargs['phase_polls'] == 1 and out[0].cb_kwargs['search_id'] == 'S1'
    assert out[0].meta['delay_seconds'] > 0


def test_complete_search_emits_items():
    spider = _spider(max_results=50)
    response, kwargs = _poll_response({**FIXTURE, 'status': 'complete'})
    items = [o for o in spider.parse_poll(response, **kwargs) if not isinstance(o, Request)]
    assert len(items) >= 2  # every parseable fixture result


def test_phase_poll_cap_emits_what_was_found():
    spider = _spider(max_results=50)
    response, kwargs = _poll_response({**FIXTURE, 'status': 'first-phase'}, phase_polls=15)
    items = [o for o in spider.parse_poll(response, **kwargs) if not isinstance(o, Request)]
    assert len(items) >= 2


# --------------------------------------------------------------------------- entry point + failure


def test_start_opens_search_page_in_browser():
    async def collect():
        return [r async for r in _spider().start()]

    requests = asyncio.run(collect())
    assert len(requests) == 1 and requests[0].meta['browser'] == 'navigate'


def test_bootstrap_failure_marks_crawl_failed():
    spider = _spider()
    failure = MagicMock()
    failure.getErrorMessage.return_value = 'Ignoring non-200 response'
    spider.errback_bootstrap(failure)
    assert MomondoSpider.crawl_failed is True
    MomondoSpider.crawl_failed = False


def test_fail_sets_process_exit_code(monkeypatch):
    from src import main as main_module

    monkeypatch.setattr(main_module.Actor, 'fail', AsyncMock())
    monkeypatch.setattr(main_module, 'EXIT_CODE', 0)
    asyncio.run(main_module._fail('boom'))
    assert main_module.EXIT_CODE == 1


# --------------------------------------------------------------------------- browser middleware


def _middleware_with(session: MagicMock) -> BrowserTransportMiddleware:
    middleware = BrowserTransportMiddleware()
    middleware.session = session
    return middleware


def test_middleware_navigates_for_bootstrap_requests():
    session = MagicMock(navigate=AsyncMock(return_value=PageResult(200, '<html>formToken</html>')), fetch=AsyncMock())
    request = Request('https://www.momondo.com/flight-search/LHR-FAO/2026-11-20/', meta={'browser': 'navigate'})
    response = asyncio.run(_middleware_with(session).process_request(request))
    session.navigate.assert_awaited_once_with(request.url)
    session.fetch.assert_not_awaited()
    assert response.status == 200 and 'formToken' in response.text


def test_middleware_fetches_poll_in_page_with_csrf_only():
    session = MagicMock(fetch=AsyncMock(return_value=PageResult(200, '{"results": []}')))
    request = _spider()._make_poll_request(legs=[], page=1)
    request.headers['x-csrf'] = 'tok'
    response = asyncio.run(_middleware_with(session).process_request(request))
    url, method, headers, body = session.fetch.await_args.args
    assert (url, method) == (POLL_URL, 'POST')
    assert headers['x-csrf'] == 'tok' and 'Referer' not in headers and 'referer' not in headers
    assert json.loads(body)['searchMetaData']['pageNumber'] == 1
    assert response.status == 200


def test_middleware_passes_bot_wall_through_as_error_status():
    session = MagicMock(navigate=AsyncMock(return_value=PageResult(403, '{"error": "bot wall"}')))
    request = Request('https://www.momondo.com/flight-search/LHR-FAO/2026-11-20/', meta={'browser': 'navigate'})
    assert asyncio.run(_middleware_with(session).process_request(request)).status == 403


# --------------------------------------------------------------------------- dates


def test_past_and_malformed_dates_are_rejected():
    from datetime import date, timedelta

    from src.main import _past_date

    assert _past_date((date.today() - timedelta(days=1)).isoformat()).endswith('is in the past. Use today or a later date.')
    assert 'not a valid date' in _past_date('2026-13-40')
    assert _past_date(date.today().isoformat()) is None


# --------------------------------------------------------------------------- session refresh


def test_bot_captcha_on_poll_opens_fresh_session_then_gives_up():
    from scrapy.exceptions import CloseSpider

    spider = _spider()
    captcha = {'errors': [{'code': 'BOT_CAPTCHA_REDIRECT'}]}

    def poll_403():
        request = Request(POLL_URL, method='POST')
        response = TextResponse(url=POLL_URL, status=403, body=json.dumps(captcha), encoding='utf-8', request=request)
        return list(spider.parse_poll(response, legs=[], page=1, search_id='OLD'))

    for attempt in (1, 2):
        out = poll_403()
        assert len(out) == 1 and out[0].meta['browser'] == 'navigate'
        assert out[0].meta['retry_poll']['search_id'] is None
        json.dumps(out[0].meta)  # must survive the Apify request-queue round trip
        assert spider._session_refreshes == attempt
    with pytest.raises(CloseSpider):
        poll_403()
    assert MomondoSpider.auth_failed is True
    MomondoSpider.auth_failed = False


def test_poll_requests_let_parse_poll_handle_auth_statuses():
    request = _spider()._make_poll_request(legs=[], page=1)
    assert {401, 403, 429} <= set(request.meta['handle_httpstatus_list'])


def test_main_raises_system_exit_when_run_failed(monkeypatch):
    from src import main as main_module

    async def failing_run():
        main_module.EXIT_CODE = 1

    monkeypatch.setattr(main_module, '_run', failing_run)
    with pytest.raises(SystemExit) as exc:
        asyncio.run(main_module.main())
    assert exc.value.code == 1
    main_module.EXIT_CODE = 0


def test_price_currency_comes_from_display_price():
    import copy

    data = copy.deepcopy(FIXTURE)
    result = next(r for r in data['results'] if r.get('legs') and r.get('bookingOptions'))
    for bo in result['bookingOptions']:
        bo['currency'] = 'USD'  # provider currency
        bo['displayPrice'] = {'price': 101, 'currency': 'EUR'}  # selected display currency
    item = next(_spider(currency='EUR').parse_result(raw_result=result, full_response=data))
    assert {o['currency'] for o in item['booking_options']} == {'EUR'}


def test_flexible_search_splits_max_results_across_dates():
    spider = _spider(trip_type='flexible', departure_date_flex_days=1, max_results=3)  # 3 dates -> 1 each
    assert spider._flex_date_quota() == 1
    first, kwargs = _poll_response({**FIXTURE, 'status': 'complete'}, flex_date='2026-11-19')
    second, kwargs2 = _poll_response({**FIXTURE, 'status': 'complete'}, flex_date='2026-11-20')
    items = [o for o in spider.parse_poll(first, **kwargs) if not isinstance(o, Request)]
    items += [o for o in spider.parse_poll(second, **kwargs2) if not isinstance(o, Request)]
    assert len(items) == 2  # one per date, not two from the first date


# --------------------------------------------------------------------------- cabin + no-match


@pytest.mark.parametrize(
    ('cabin', 'param'),
    [('ECONOMY', 'economy'), ('PREMIUM_ECONOMY', 'premium-economy'), ('BUSINESS', 'business'), ('FIRST', 'first')],
)
def test_cabin_is_sent_on_every_leg(cabin, param):
    spider = _spider(trip_type='round-trip', return_date='2026-11-27', cabin_class=cabin)
    assert [leg['cabinClass'] for leg in spider._build_legs()] == [param, param]


def test_prem_economy_label_matches_premium_filter():
    from src.spiders.momondo import _cabin_matches

    assert _cabin_matches('Prem Economy', 'PREMIUM_ECONOMY')
    assert not _cabin_matches('Prem Economy', 'ECONOMY')


def test_zero_results_after_user_filters_is_not_a_failure():
    spider = _spider(max_stops=0)
    spider.observed_filtered_count, spider.skipped_filtered = 43, 43
    spider.closed('finished')
    assert MomondoSpider.no_matches is True and MomondoSpider.crawl_failed is False
    MomondoSpider.no_matches = False


def test_zero_results_without_filters_is_still_a_failure():
    spider = _spider()
    spider.observed_filtered_count = 43
    spider.closed('finished')
    assert MomondoSpider.crawl_failed is True and MomondoSpider.no_matches is False
    MomondoSpider.crawl_failed = False


def test_limit_pipeline_reads_limit_from_crawler_settings():
    from scrapy.exceptions import DropItem
    from scrapy.settings import Settings

    from src.pipelines import LimitItemsNumberPipeline

    pipeline = LimitItemsNumberPipeline.from_crawler(MagicMock(settings=Settings({'CLOSESPIDER_ITEMCOUNT': 1})))
    assert pipeline.process_item({'a': 1}) == {'a': 1}
    with pytest.raises(DropItem):
        pipeline.process_item({'a': 2})


def test_refresh_rebuilds_poll_with_new_token():
    spider = _spider()
    spider.csrf_token = 'NEW'
    page = Request('https://www.momondo.com/flight-search/LHR-FAO/2026-11-20/',
                   meta={'retry_poll': {'legs': [], 'page': 1, 'flex_date': None, 'search_id': None}})
    response = TextResponse(url=page.url, status=200, body=b"window.R9.formToken = 'NEW';", encoding='utf-8', request=page)
    out = list(spider.parse_bootstrap_refresh(response))
    assert len(out) == 1 and out[0].headers['x-csrf'] == b'NEW'


def test_completed_search_without_fares_is_a_clean_no_flights_result():
    spider = _spider(checked_bags=2)
    response, kwargs = _poll_response({'status': 'complete', 'results': [], 'filteredCount': 0, 'searchId': 'S'})
    assert list(spider.parse_poll(response, **kwargs)) == []
    assert MomondoSpider.no_flights is True and MomondoSpider.crawl_failed is False
    MomondoSpider.no_flights = False


def test_non_economy_search_keeps_only_matching_cabin_options():
    import copy

    data = copy.deepcopy(FIXTURE)
    result = next(r for r in data['results'] if r.get('legs') and len(r.get('bookingOptions') or []) >= 2)
    cabins = ['Business'] + ['Economy'] * (len(result['bookingOptions']) - 1)
    for bo, cabin in zip(result['bookingOptions'], cabins):
        bo['legFarings'] = [{'segmentFarings': [{'cabinDisplay': cabin}]}]
    item = next(_spider(cabin_class='BUSINESS').parse_result(raw_result=result, full_response=data))
    assert [o['cabin_class'] for o in item['booking_options']] == ['Business']
