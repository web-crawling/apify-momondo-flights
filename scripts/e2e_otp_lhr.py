"""End-to-end test: one-way OTP->LHR, 2026-09-01, maxResults=5.

This is the EXACT failing input from the user's run 0bR5hfyzVj7P7bo0D.
Uses requests (not Scrapy) to reproduce the full flow with the dynamic searchId fix:
  bootstrap GET -> first poll (no searchId) -> capture server searchId -> parse_result()

Expected result: at least 1 FlightItem yielded (5 requested).
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

from src.spiders.momondo import MomondoSpider

BOOTSTRAP_URL = 'https://www.momondo.com/flight-search/OTP-LHR/2026-09-01/'
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
        origin='OTP',
        destination='LHR',
        departure_date='2026-09-01',
        max_results=max_results,
        max_stops=1,
        currency='EUR',
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

    # --- Step 2: Build page-1 poll body (NO searchId) ---
    legs = spider._build_legs()
    body = spider._build_poll_body(legs=legs, page=1, search_id=None)

    # Confirm the poll body has no searchId on page 1
    user_params = body.get('userSearchParams', {})
    assert 'searchId' not in user_params, 'BUG: searchId present on page 1 body!'
    assert 'cabinClass' not in user_params, 'BUG: cabinClass in poll body!'
    assert 'currency' not in user_params, 'BUG: currency in poll body!'
    print(f'    Page 1 body clean: no searchId/cabinClass/currency')

    poll_headers = {**POLL_HEADERS_BASE, 'x-csrf': form_token, 'Referer': BOOTSTRAP_URL}

    # --- Step 3: First poll (page 1) ---
    print(f'[2] First poll POST (no searchId) -> {POLL_URL}')
    poll_resp = session.post(
        POLL_URL, headers=poll_headers, data=json.dumps(body), timeout=30,
    )
    print(f'    Status: {poll_resp.status_code}')
    assert poll_resp.status_code == 200, (
        f'First poll returned {poll_resp.status_code}. Body: {poll_resp.text[:500]}'
    )

    data = poll_resp.json()
    server_search_id = data.get('searchId')
    results = data.get('results') or []
    filtered_count = data.get('filteredCount', 0)
    print(f'    filteredCount={filtered_count}, pageSize={data.get("pageSize")}, '
          f'results_in_page={len(results)}, server searchId={server_search_id!r}')

    assert server_search_id, 'Server did not return a searchId in first poll response!'

    # --- Step 4: If first page empty, retry with searchId (long-poll style) ---
    max_retries = 3
    retry = 0
    while not results and filtered_count > 0 and retry < max_retries:
        retry += 1
        print(f'    Page 1 empty, long-poll retry {retry}/{max_retries} with searchId={server_search_id!r}')
        retry_body = spider._build_poll_body(legs=legs, page=1, search_id=server_search_id)
        assert 'searchId' in retry_body['userSearchParams'], 'Retry body must include searchId!'
        poll_resp = session.post(
            POLL_URL, headers=poll_headers, data=json.dumps(retry_body), timeout=30,
        )
        assert poll_resp.status_code == 200, (
            f'Retry poll returned {poll_resp.status_code}. Body: {poll_resp.text[:500]}'
        )
        data = poll_resp.json()
        results = data.get('results') or []
        server_search_id = data.get('searchId') or server_search_id
        print(f'    After retry {retry}: results={len(results)}, filteredCount={data.get("filteredCount")}')

    # --- Step 5: parse_result ---
    items_yielded = 0
    items = []

    for raw_result in results:
        if items_yielded >= max_results:
            break
        for item in spider.parse_result(raw_result=raw_result, full_response=data):
            items.append(item)
            items_yielded += 1
            if items_yielded >= max_results:
                break

    print(f'\n[3] parse_result results:')
    print(f'    items_yielded={items_yielded}, skipped_filtered={spider.skipped_filtered}')

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

    assert items_yielded >= 1, (
        f'FAIL: Expected at least 1 item for OTP->LHR, got {items_yielded}. '
        f'filteredCount={filtered_count}, results_in_page={len(results)}'
    )

    print(f'\n[PASS] E2E one-way OTP->LHR 2026-09-01: {items_yielded} items produced')
    return items_yielded


if __name__ == '__main__':
    count = main()
    sys.exit(0 if count >= 1 else 1)
