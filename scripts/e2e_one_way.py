"""End-to-end test: one-way JFK->LHR, 2026-08-01, maxResults=5.

Uses requests (not Scrapy) to avoid needing a running reactor.
Reproduces the exact flow: bootstrap GET -> poll POST -> parse_result().

Expected result: 5 FlightItems yielded.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

# Add actor root to path
ACTOR_ROOT = Path(__file__).parent.parent
if str(ACTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ACTOR_ROOT))

from src.spiders.momondo import MomondoSpider, VALID_SEARCH_ID

BOOTSTRAP_URL = 'https://www.momondo.com/flight-search/JFK-LHR/2026-08-01/'
POLL_URL = 'https://www.momondo.com/i/api/search/dynamic/flights/poll'

HTML_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

POLL_HEADERS_BASE = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Content-Type': 'application/json',
    'x-requested-with': 'XMLHttpRequest',
}


def main():
    max_results = 5
    spider = MomondoSpider(
        trip_type='one-way',
        origin='JFK',
        destination='LHR',
        departure_date='2026-08-01',
        max_results=max_results,
    )

    # --- Step 1: Bootstrap ---
    session = requests.Session()
    print(f'[1] Bootstrap GET -> {BOOTSTRAP_URL}')
    resp = session.get(BOOTSTRAP_URL, headers=HTML_HEADERS, timeout=30)
    assert resp.status_code == 200, f'Bootstrap returned {resp.status_code}'

    match = re.search(r"window\.R9\.formToken\s*=\s*'([^']+)'", resp.text)
    assert match, 'formToken not found in bootstrap HTML'
    form_token = match.group(1)
    print(f'    formToken extracted (len={len(form_token)})')

    # --- Step 2: Build poll body (same logic as spider, but using corrected body) ---
    legs = spider._build_legs()
    body = spider._build_poll_body(legs=legs, page=1)

    # Confirm the poll body is clean (no rejected fields)
    user_params = body.get('userSearchParams', {})
    filter_params = body.get('filterParams', {})
    assert 'cabinClass' not in user_params, 'BUG: cabinClass still in poll body!'
    assert 'currency' not in user_params, 'BUG: currency still in poll body!'
    assert 'stops' not in filter_params, 'BUG: stops still in filterParams!'
    print(f'    Poll body clean: no cabinClass/currency/stops')

    poll_headers = {**POLL_HEADERS_BASE, 'x-csrf': form_token}
    poll_headers['Referer'] = BOOTSTRAP_URL

    # --- Step 3: Poll ---
    print(f'[2] Poll POST -> {POLL_URL}')
    poll_resp = session.post(
        POLL_URL,
        headers=poll_headers,
        data=json.dumps(body),
        timeout=30,
    )
    print(f'    Status: {poll_resp.status_code}')
    assert poll_resp.status_code == 200, (
        f'Poll returned {poll_resp.status_code}. Body: {poll_resp.text[:500]}'
    )

    data = poll_resp.json()
    results = data.get('results') or []
    print(f'    filteredCount={data.get("filteredCount")}, pageSize={data.get("pageSize")}, '
          f'results_in_page={len(results)}')

    # --- Step 4: parse_result on first max_results valid results ---
    items_yielded = 0
    items = []
    skipped = 0

    for raw_result in results:
        if items_yielded >= max_results:
            break
        for item in spider.parse_result(raw_result=raw_result, full_response=data):
            items.append(item)
            items_yielded += 1
            if items_yielded >= max_results:
                break

    # Account for skipped results
    skipped = spider.skipped_filtered

    print(f'\n[3] parse_result results:')
    print(f'    items_yielded={items_yielded}, skipped_filtered={skipped}')
    print()

    for i, item in enumerate(items):
        origin = item.get('origin', {})
        dest = item.get('destination', {})
        stops = item.get('stops', '?')
        dur = item.get('duration_minutes', '?')
        dep = item.get('departure_time', '?')
        bos = item.get('booking_options', [])
        price = bos[0].get('price', '?') if bos else '?'
        currency = bos[0].get('currency', '?') if bos else '?'
        cabin = bos[0].get('cabin_class', '?') if bos else '?'
        print(
            f'  [{i+1}] {origin.get("code","?")} -> {dest.get("code","?")} '
            f'| stops={stops} | {dur}min | dep={dep[:16]} '
            f'| {price} {currency} | cabin={cabin!r}'
        )

    assert items_yielded == max_results, (
        f'FAIL: Expected {max_results} items, got {items_yielded}. '
        f'Check if results had enough valid entries.'
    )

    print(f'\n[PASS] E2E one-way JFK->LHR: {items_yielded}/{max_results} items produced correctly')
    return items_yielded


if __name__ == '__main__':
    count = main()
    sys.exit(0 if count == 5 else 1)
