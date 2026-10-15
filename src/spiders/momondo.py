"""Momondo flights spider.

Scrapes flight search results from Momondo's internal poll API:
  POST https://www.momondo.com/i/api/search/dynamic/flights/poll

Supports four trip types:
  - one-way:    Single outbound leg.
  - round-trip: Outbound + return leg.
  - multi-city: N legs from the `legs` input array.
  - flexible:   Fan-out of (2N+1) one-way/round-trip searches, one per date in
                [departureDate - N days ... departureDate + N days], where N =
                departureDateFlexDays.  The return date (if any) is kept fixed.

Request flow:
  1. start_requests() — GET the flight-search results page for the first leg
                        (bootstrap to obtain session cookies AND the CSRF token).
  2. parse_bootstrap() — extracts formToken from page HTML; fires one or more
                         poll POST requests (page 1).
  3. parse_poll() — extracts pagination metadata; passes raw JSON to
                   parse_result() stub; fires next-page request if needed.
  4. parse_result() — Extracts one FlightItem per result with full field population,
                      client-side cabin class filtering, and client-side stop filtering.

Authentication (CSRF):
  - The Momondo poll API requires an x-csrf header.
  - The CSRF token (called "formToken") is embedded in the flight-search results
    page HTML as: window.R9.formToken = '<value>';
  - Bootstrap: GET /flight-search/{origin}-{destination}/{date}/ to obtain
    session cookies AND extract formToken from the HTML.
  - formToken is sent as the x-csrf header on all poll POST requests.
  - On HTTP 401, or a 403 bot captcha on a poll: the search page is re-opened in a
    fresh browser context, at most _MAX_SESSION_REFRESHES times per run. If 401 persists after refresh, set auth_failed=True
    and raise CloseSpider('auth_failed').

searchId:
  - searchId is server-issued, NOT a client constant.
  - The FIRST poll for each session omits searchId from userSearchParams.
  - The server returns a fresh searchId in the first poll response (response["searchId"]).
  - All subsequent polls in the same session (page 2+) include the server-returned searchId.
  - For flexible date fan-out, each date variant is its own session with its own searchId.
    The searchId is threaded per-session through cb_kwargs.

Poll API field restrictions (QA confirmed 2026-05-14):
  - userSearchParams.cabinClass/currency and filterParams.stops are NOT accepted.
  - Sending them causes HTTP 400 VALIDATION_ERROR: Unrecognized field.
  - The cabin IS accepted per leg (legs[].cabinClass, verified 2026-10-15) and is
    sent there; parse_result() still keeps only results with a matching cabin.
  - maxStops filtering is applied client-side in parse_result().
  - currency: selected per browser session through the site's currency picker
    endpoint (src/browser.py); the price currency is displayPrice.currency.
  - Bags: the site's own filter string filterParams.fs ("cfc=1;bfc=N") IS accepted
    (verified 2026-10-15) and prices fares with the requested bags.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Generator

import scrapy
from scrapy.exceptions import CloseSpider
from scrapy.http import Request, Response

from src.items import FlightItem
from src.itemloaders import FlightItemLoader

logger = logging.getLogger(__name__)

# Poll endpoint
POLL_URL = 'https://www.momondo.com/i/api/search/dynamic/flights/poll'
# Bootstrap URL template — GET this to obtain session cookies + CSRF token.
# The flight-search results page embeds the formToken in its HTML.
BOOTSTRAP_URL_TEMPLATE = 'https://www.momondo.com/flight-search/{origin}-{destination}/{date}/'

# Passenger type codes
PTC_ADULT = 'ADT'
PTC_CHILD = 'CHD'  # CNN is rejected with HTTP 400 (seen 2026-10-15); the site sends CHD
PTC_INFANT = 'INL'  # infant on lap, as the site sends it

# Maximum retries when first poll returns 0 results (regardless of filteredCount).
# On Apify datacenter IPs, Momondo may return 0 results AND filteredCount=0 on the
# first poll even when results will materialise on subsequent polls with the searchId.
# Bumped from 3 to 5 to give more time for Momondo's server-side search initialisation.
_EMPTY_FIRST_PAGE_MAX_RETRIES = 5

# Exponential backoff delays (seconds) between page-1 retries.
# Index = retry attempt number (0-based); capped at last value if retries exceed list length.
# Pattern: 1s, 2s, 3s, 5s, 8s (Fibonacci-like growth).
_RETRY_BACKOFF_SECONDS = [1, 2, 3, 5, 8]

# Re-polls of page 1 while Momondo is still collecting fares (status != "complete").
# 15 x 2 s covers the ~10-25 s a search usually takes.
_MAX_PHASE_POLLS = 15
_PHASE_POLL_DELAY_SECONDS = 2

# Fresh browser sessions a run may open after a 401 / bot captcha on a poll.
_MAX_SESSION_REFRESHES = 2

# Poll statuses parse_poll handles itself instead of Scrapy's HttpError errback.
_POLL_HANDLED_STATUSES = [401, 403, 429]

# Cabin class client-side filter (QA-confirmed 2026-05-14):
# cabinClass is NOT accepted in the Momondo poll body (HTTP 400 VALIDATION_ERROR).
# Filtering is done post-fetch in parse_result() by inspecting each booking
# option's legFarings[0].segmentFarings[0].cabinDisplay string.
#
# Momondo display values observed in live responses:
#   Economy / Economy Light / Basic / Blue Basic  → maps to ECONOMY
#   Premium Economy                               → maps to PREMIUM_ECONOMY
#   Business / Business Class                     → maps to BUSINESS
#   First / First Class                           → maps to FIRST
#
# Matching is case-insensitive substring/keyword check (see _cabin_matches()).
# If cabin_class=ECONOMY, NO filtering is applied — Momondo returns economy
# results by default and filtering here would over-filter mixed-class results.

# Regex to extract the formToken from the flight-search page HTML.
# The page includes: window.R9.formToken = '<value>';
_FORM_TOKEN_RE = re.compile(r"window\.R9\.formToken\s*=\s*'([^']+)'")


# Per-leg cabin values the site sends (verified 2026-10-15).
_CABIN_PARAM = {
    'ECONOMY': 'economy',
    'PREMIUM_ECONOMY': 'premium-economy',
    'BUSINESS': 'business',
    'FIRST': 'first',
}


def _cabin_matches(cabin_display: str, requested_class: str) -> bool:
    """Return True if the booking option's cabin display value matches the requested class.

    Args:
        cabin_display: The cabinDisplay string from legFarings[0].segmentFarings[0].cabinDisplay,
                       e.g. "Economy", "Economy Light", "Premium Economy", "Business", "First".
        requested_class: The actor's cabinClass input enum: ECONOMY / PREMIUM_ECONOMY / BUSINESS / FIRST.

    Matching rules (case-insensitive):
      ECONOMY        → True if cabin contains "economy" (e.g. "Economy", "Economy Light", "Basic", "Blue Basic")
                       Note: "Premium Economy" / "Prem Economy" contain "economy" but NOT for
                       the ECONOMY filter — we exclude them by checking "prem" is absent.
      PREMIUM_ECONOMY → True if cabin contains "prem" (case-insensitive)
      BUSINESS        → True if cabin contains "business" (case-insensitive)
      FIRST           → True if cabin contains "first" (case-insensitive)
    """
    c = cabin_display.lower()
    if requested_class == 'ECONOMY':
        # "Economy" but NOT "Premium Economy"
        return 'economy' in c and 'prem' not in c
    if requested_class == 'PREMIUM_ECONOMY':
        return 'prem' in c  # Momondo displays "Prem Economy" as well as "Premium Economy"
    if requested_class == 'BUSINESS':
        return 'business' in c
    if requested_class == 'FIRST':
        return 'first' in c
    # Unknown class — do not filter
    return True


def _amount(price_obj: dict | None) -> float | None:
    """Numeric amount of a Momondo {"price": ..., "currency": ...} object."""
    if isinstance(price_obj, dict) and isinstance(price_obj.get('price'), (int, float)):
        return price_obj['price']
    return None


def _bag(bag_data: dict | None) -> dict:
    """Normalise a fees.carryOnBagData / fees.checkedBagData block.

    status: "included" — the bag is in `price` (free allowance, or its fee was added
            because the bag was requested), "fee" — costs extra (fee known, not in
            `price`), "unknown" — the provider publishes no bag price.
    fee: per-passenger bag fee; bags_in_price: how many of these bags `price` covers.
    """
    if not isinstance(bag_data, dict):
        return {'status': 'unknown'}
    bag = {'status': str(bag_data.get('status') or 'UNKNOWN').lower()}
    fee = _amount(bag_data.get('displayPrice'))
    if fee is not None:
        bag['fee'] = fee
    if isinstance(bag_data.get('numSelected'), int):
        bag['bags_in_price'] = bag_data['numSelected']
    return bag


def _fee_fields(fees: dict | None) -> dict:
    """Bag and price-breakdown fields for one booking option (absent keys = not published)."""
    if not isinstance(fees, dict):
        return {}
    fields: dict = {
        'carry_on_bag': _bag(fees.get('carryOnBagData')),
        'checked_bag': _bag(fees.get('checkedBagData')),
    }
    base = _amount(fees.get('basePrice'))
    total = _amount(fees.get('totalPrice'))
    if base is not None:
        fields['base_price'] = base
    if total is not None:
        fields['total_price_all_passengers'] = total
    return fields


def _baggage_policy_links(legs: list[dict], airlines: dict) -> list[dict]:
    """One baggage-policy link per airline flown in this itinerary, in flight order."""
    links: list[dict] = []
    seen: set[str] = set()
    for leg in legs:
        for seg in leg.get('segments') or []:
            code = seg.get('airline_code') or ''
            if not code or code in seen:
                continue
            seen.add(code)
            url = ((airlines.get(code) or {}).get('airlineFeeUrl') or {}).get('url')
            if url:
                links.append({'airline_code': code, 'airline_name': seg.get('airline_name') or code, 'url': url})
    return links


class MomondoSpider(scrapy.Spider):
    name = 'momondo'

    # Class-level flags: reset by main.py before each crawl; mutated via
    # type(self).attr = True inside the spider so that main.py can read the
    # current value after the Deferred resolves (CrawlerRunner.crawl() returns
    # None, not the spider instance, so instance attribute reads are impossible).
    auth_failed: bool = False
    crawl_failed: bool = False
    no_matches: bool = False  # fares existed but none passed the user's cabin/maxStops filters
    no_flights: bool = False  # Momondo finished the search and had no fares at all

    def __init__(
        self,
        trip_type: str = 'one-way',
        origin: str = 'JFK',
        destination: str = 'LHR',
        departure_date: str = '2026-08-01',
        return_date: str | None = None,
        departure_date_flex_days: int | None = None,
        legs: list[dict] | None = None,
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
        cabin_class: str = 'ECONOMY',
        currency: str = 'USD',
        max_stops: int | None = None,
        max_results: int = 50,
        checked_bags: int = 0,
        carry_on_bag: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self.trip_type = trip_type
        self.origin = origin.upper() if origin else 'JFK'
        self.destination = destination.upper() if destination else 'LHR'
        self.departure_date = departure_date
        self.return_date = return_date
        self.departure_date_flex_days = int(departure_date_flex_days) if departure_date_flex_days is not None else None
        self.legs_input = legs or []
        self.adults = int(adults)
        self.children = int(children)
        self.infants = int(infants)
        self.cabin_class = cabin_class
        self.currency = currency
        self.max_stops = int(max_stops) if max_stops is not None else None
        self.max_results = int(max_results)
        self.checked_bags = max(0, min(2, int(checked_bags or 0)))
        self.carry_on_bag = bool(carry_on_bag)

        # State
        self.csrf_token: str = ''
        self._session_refreshes: int = 0
        self.items_yielded: int = 0
        self._items_per_flex_date: dict[str, int] = {}
        self.skipped_filtered: int = 0  # results dropped by client-side cabin/stops filters
        # Reset class-level flags so a new spider instance in the same process
        # starts clean (class attributes persist between runs otherwise).
        type(self).auth_failed = False
        type(self).crawl_failed = False
        type(self).no_matches = False
        type(self).no_flights = False
        self.observed_filtered_count: int = 0  # highest filteredCount seen across all poll responses

        # Validate: flexible mode requires departureDateFlexDays.
        if self.trip_type == 'flexible' and not self.departure_date_flex_days:
            logger.warning(
                'tripType is "flexible" but departureDateFlexDays is not set. '
                'Defaulting to departureDateFlexDays=1 (searching ±1 day).'
            )
            self.departure_date_flex_days = 1

        # Warn if departureDateFlexDays is set but tripType is not flexible.
        if self.departure_date_flex_days and self.trip_type != 'flexible':
            logger.warning(
                'departureDateFlexDays=%d is set but tripType is %r — '
                'departureDateFlexDays is ignored for non-flexible searches.',
                self.departure_date_flex_days,
                self.trip_type,
            )

    # ------------------------------------------------------------------
    # Step 1 — Bootstrap (GET flight-search page to seed cookies + CSRF)
    # ------------------------------------------------------------------

    def _bootstrap_url(self) -> str:
        """Build the bootstrap URL for the primary search leg.

        For multi-city, uses the first leg's origin/destination/date.
        For flexible, uses the base departure date.
        """
        if self.trip_type == 'multi-city' and self.legs_input:
            leg = self.legs_input[0]
            return BOOTSTRAP_URL_TEMPLATE.format(
                origin=leg['origin'].upper(),
                destination=leg['destination'].upper(),
                date=leg['departureDate'],
            )
        return BOOTSTRAP_URL_TEMPLATE.format(
            origin=self.origin,
            destination=self.destination,
            date=self.departure_date,
        )

    async def start(self):
        """Scrapy >= 2.13 entry point (2.19 no longer falls back to start_requests())."""
        for request in self.start_requests():
            yield request

    def start_requests(self) -> Generator[Request, None, None]:
        """Open the flight-search results page in the browser session.

        This page:
          - Starts the browser session (cookies live in the browser, see src/browser.py)
          - Embeds the CSRF token as: window.R9.formToken = '<value>';

        Currency handling:
          The Momondo poll API does NOT accept a currency field in the POST body
          (QA-confirmed 2026-05-14: causes HTTP 400 VALIDATION_ERROR). The browser
          context sets currency=<code> and kyk_curr=<code> cookies before opening
          the page; if Momondo ignores them, prices come back in the default
          currency (usually USD) and a warning is logged in parse_poll.

        The formToken is then extracted in parse_bootstrap() and used as
        the x-csrf header on all poll POST requests.
        """
        bootstrap_url = self._bootstrap_url()
        logger.info(
            'Starting the Momondo search: %s (trip type %s, currency %s)',
            bootstrap_url,
            self.trip_type,
            self.currency,
        )
        yield scrapy.Request(
            url=bootstrap_url,
            callback=self.parse_bootstrap,
            errback=self.errback_bootstrap,
            meta={'phase': 'bootstrap', 'browser': 'navigate'},
            dont_filter=True,
        )

    def parse_bootstrap(self, response: Response) -> Generator[Request, None, None]:
        """Bootstrap callback: extract formToken from page HTML, then fire poll requests.

        The CSRF token is embedded in the page as:
            window.R9.formToken = '<value>';

        Falls back to an empty string if not found (server will return 401,
        which triggers a session refresh).
        """
        match = _FORM_TOKEN_RE.search(response.text)
        if match:
            self.csrf_token = match.group(1)
            logger.debug(
                'Bootstrap complete: formToken extracted (len=%d). '
                'Firing initial poll request(s).',
                len(self.csrf_token),
            )
        else:
            logger.warning('The search session is not ready; Momondo may refuse the search.')
            self.csrf_token = ''

        yield from self._build_poll_requests()

    def errback_bootstrap(self, failure: Any) -> None:
        """Handle a search page that never opened a session (bot wall, timeout, browser error)."""
        logger.error('Momondo did not open the search (%s).', failure.getErrorMessage())
        type(self).crawl_failed = True

    # ------------------------------------------------------------------
    # Step 2 — Poll requests (build payloads for each search target)
    # ------------------------------------------------------------------

    def _build_poll_requests(self) -> Generator[Request, None, None]:
        """Build and yield the initial poll POST request(s) based on trip type."""
        if self.trip_type == 'flexible':
            yield from self._build_flexible_poll_requests()
        else:
            legs = self._build_legs()
            yield self._make_poll_request(legs=legs, page=1)

    def _build_flexible_poll_requests(self) -> Generator[Request, None, None]:
        """Fan out a flexible search into (2N+1) separate exact-date poll requests.

        For round-trip flexible, outbound date is varied; return date is fixed.
        This is because the Momondo API does not honour flex:"range" in the POST
        body — the frontend parses flex URLs but always sends flex:"exact" to the
        API (confirmed in 02b-flex-api.md).

        Each fan-out date generates its own independent search (its own set of
        paginated poll requests).
        """
        n = self.departure_date_flex_days or 1
        base_date = date.fromisoformat(self.departure_date)
        dates = [base_date + timedelta(days=offset) for offset in range(-n, n + 1)]

        logger.info(
            'Flexible search: %d dates (%s to %s) for %s→%s',
            len(dates),
            dates[0].isoformat(),
            dates[-1].isoformat(),
            self.origin,
            self.destination,
        )

        for search_date in dates:
            legs = self._build_legs(override_departure_date=search_date.isoformat())
            yield self._make_poll_request(legs=legs, page=1, flex_date=search_date.isoformat())

    def _build_legs(self, override_departure_date: str | None = None) -> list[dict]:
        """Build the legs array for the poll request body.

        Args:
            override_departure_date: If set, replaces self.departure_date for
                the outbound leg (used by flexible fan-out).

        Returns:
            List of leg dicts in Momondo's expected format.
        """
        dep_date = override_departure_date or self.departure_date

        if self.trip_type in ('one-way', 'flexible'):
            return [
                self._leg(self.origin, self.destination, dep_date),
            ]

        if self.trip_type == 'round-trip':
            if not self.return_date:
                raise ValueError(
                    'tripType is "round-trip" but returnDate is not set. '
                    'Please provide a returnDate.'
                )
            return [
                self._leg(self.origin, self.destination, dep_date),
                self._leg(self.destination, self.origin, self.return_date),
            ]

        if self.trip_type == 'multi-city':
            if not self.legs_input:
                raise ValueError(
                    'tripType is "multi-city" but no legs were provided. '
                    'Please supply the "legs" input parameter.'
                )
            return [
                self._leg(
                    leg['origin'].upper(),
                    leg['destination'].upper(),
                    leg['departureDate'],
                )
                for leg in self.legs_input
            ]

        raise ValueError(f'Unknown tripType: {self.trip_type!r}')

    def _leg(self, origin: str, destination: str, dep_date: str) -> dict:
        """Construct a single leg dict in Momondo's expected format.

        The cabin goes on each leg (legs[].cabinClass), as the site sends it; the
        userSearchParams-level cabinClass is the field the API rejects.
        """
        return {
            'origin': {'airports': [origin], 'locationType': 'airports'},
            'destination': {'airports': [destination], 'locationType': 'airports'},
            'cabinClass': _CABIN_PARAM.get(self.cabin_class, 'economy'),
            'date': dep_date,
            'flex': 'exact',  # API always uses "exact"; flexible = fan-out (see _build_flexible_poll_requests)
        }

    def _build_passengers(self) -> tuple[list[str], list[dict]]:
        """Build passengers and passengerDetails arrays from adult/child/infant counts."""
        passengers: list[str] = (
            [PTC_ADULT] * self.adults
            + [PTC_CHILD] * self.children
            + [PTC_INFANT] * self.infants
        )
        passenger_details: list[dict] = [{'ptc': ptc} for ptc in passengers]
        return passengers, passenger_details

    def _bag_filter(self) -> str:
        """Momondo's fee-assistant filter string, e.g. 'cfc=1;bfc=2' (empty = no bags).

        Same value the site puts in its URL (?fs=bfc=1) and poll body. With it,
        Momondo returns only fares whose bag cost is known and prices them with the bags.
        """
        parts = []
        if self.carry_on_bag:
            parts.append('cfc=1')
        if self.checked_bags:
            parts.append(f'bfc={self.checked_bags}')
        return ';'.join(parts)

    def _build_filter_params(self) -> dict:
        """Build the filterParams section of the poll body.

        Structured filter keys are rejected with HTTP 400 VALIDATION_ERROR
        (QA-confirmed 2026-05-14: filterParams.stops, filterParams.cabin), so maxStops
        and cabin filtering stay client-side in parse_result(). The site's own filter
        string `fs` IS accepted (verified 2026-10-15) and carries the bag selection.
        """
        bag_filter = self._bag_filter()
        return {'fs': bag_filter} if bag_filter else {}

    def _build_poll_body(self, legs: list[dict], page: int, search_id: str | None = None) -> dict:
        """Construct the full poll request body.

        QA-confirmed 2026-05-14: the following fields are NOT accepted by
        Momondo's poll API and must NOT be sent (they cause HTTP 400):
          - userSearchParams.cabinClass  (filtered client-side in parse_result)
          - userSearchParams.currency    (attempted via bootstrap cookie instead)
          - filterParams.stops           (filtered client-side in parse_result)

        searchId handling (fix for issue #8):
          - On the FIRST poll for a session (page 1, no prior searchId), omit searchId
            entirely from userSearchParams. The server issues a fresh searchId in the response.
          - On subsequent polls (page 2+, or page-1 retry), include the server-returned searchId.
          - search_id=None means "first poll" and omits the key.
        """
        passengers, passenger_details = self._build_passengers()

        user_search_params: dict = {
            'legs': legs,
            'passengers': passengers,
            'passengerDetails': passenger_details,
            'sortMode': 'bestflight_a',
        }

        # Only include searchId when we have a server-issued one.
        # The first poll must omit it; the server returns a fresh searchId in that response.
        if search_id is not None:
            user_search_params['searchId'] = search_id

        return {
            'filterParams': self._build_filter_params(),
            'userSearchParams': user_search_params,
            'searchMetaData': {
                'pageNumber': page,
                'searchTypes': [],
                'skipResultsInSecondPhase': False,
            },
        }

    def _make_poll_request(
        self,
        legs: list[dict],
        page: int,
        flex_date: str | None = None,
        search_id: str | None = None,
        cb_kwargs: dict | None = None,
        page1_retry_count: int = 0,
        delay_seconds: float = 0,
        phase_polls: int = 0,
    ) -> Request:
        """Create a single poll POST Request object.

        Args:
            legs:              Legs array for the body.
            page:              pageNumber (1-indexed).
            flex_date:         For flexible fan-out — the specific departure date this
                               request covers (passed through cb_kwargs for tracing).
            search_id:         Server-issued searchId for this session. None on first poll
                               (server returns the ID in response); reused on subsequent polls.
            cb_kwargs:         Additional cb_kwargs to merge (e.g. for retry context).
            page1_retry_count: Number of page-1 retries already done for this session
                               (for the empty-first-page long-polling pattern).
            delay_seconds:     Non-blocking wait before sending (applied by the browser
                               middleware; never time.sleep — it would stall the browser).
            phase_polls:       Page-1 re-polls already made while the search was incomplete.
        """
        body = self._build_poll_body(legs=legs, page=page, search_id=search_id)
        extra_kwargs = dict(cb_kwargs or {})
        extra_kwargs.update({
            'legs': legs,
            'page': page,
            'flex_date': flex_date,
            'search_id': search_id,
            'page1_retry_count': page1_retry_count,
            'phase_polls': phase_polls,
        })

        return scrapy.Request(
            url=POLL_URL,
            method='POST',
            body=json.dumps(body),
            headers={
                'Content-Type': 'application/json',
                'x-csrf': self.csrf_token,  # "" on first request; real token on subsequent ones
                'Referer': 'https://www.momondo.com/flight-search/',
            },
            callback=self.parse_poll,
            errback=self.errback_poll,
            cb_kwargs=extra_kwargs,
            meta={'handle_httpstatus_list': _POLL_HANDLED_STATUSES, **({'delay_seconds': delay_seconds} if delay_seconds else {})},
            dont_filter=True,
        )

    # ------------------------------------------------------------------
    # Step 3 — Poll response: CSRF extraction + pagination
    # ------------------------------------------------------------------

    def parse_poll(
        self,
        response: Response,
        legs: list[dict],
        page: int,
        flex_date: str | None = None,
        search_id: str | None = None,
        page1_retry_count: int = 0,
        phase_polls: int = 0,
        **kwargs: Any,
    ) -> Generator[Any, None, None]:
        """Handle a poll response.

        1. Handle non-200 status codes.
        2. Capture server-issued searchId from first response.
        3. Long-poll retry if first page returns 0 results (regardless of filteredCount).
           On Apify datacenter IPs, filteredCount may also be 0 on the first poll even
           when results will materialise on subsequent polls with the captured searchId.
        4. Yield each result to parse_result().
        5. Fire next-page request if pagination continues.
        """
        # --- Handle non-200 status codes ---
        if response.status == 401:
            yield from self._handle_401(response, legs=legs, page=page, flex_date=flex_date,
                                        search_id=search_id)
            return

        if response.status == 429:
            logger.warning(
                'Momondo is rate-limiting the search (page %d, date %s).',
                page, flex_date,
            )
            # TODO: implement retry with back-off if this becomes frequent in production
            return

        if response.status == 403:
            if 'BOT_CAPTCHA' in response.text:
                yield from self._handle_401(response, legs=legs, page=page, flex_date=flex_date,
                                            search_id=search_id, reason='bot captcha')
                return
            logger.error('Momondo refused the search (HTTP 403).')
            logger.debug('403 body: %s', response.text[:200])
            type(self).crawl_failed = True
            return

        if response.status != 200:
            logger.error(
                'Momondo returned HTTP %d (page %d); skipping it.',
                response.status, page,
            )
            type(self).crawl_failed = True
            return

        # --- Extract CSRF token from Set-Cookie (once per session) ---
        self._extract_csrf_from_response(response)

        # --- Parse JSON body ---
        try:
            data = response.json()
        except Exception as exc:
            logger.error('Momondo returned an unreadable response (page %d); skipping it.', page)
            logger.debug('Decode error %s; body: %r', exc, response.text[:200])
            return

        results = data.get('results') or []
        filtered_count = data.get('filteredCount', 0)
        page_size = data.get('pageSize', 50)
        current_page = data.get('pageNumber', page)

        # --- Capture server-issued searchId (on first response for this session) ---
        server_search_id = data.get('searchId')
        if server_search_id and search_id is None:
            logger.debug(
                'Captured server-issued searchId=%r for session (flex_date=%s).',
                server_search_id, flex_date,
            )
            search_id = server_search_id
        elif server_search_id and search_id is not None and server_search_id != search_id:
            # Server rotated the searchId — use the new one
            logger.debug(
                'searchId rotated: %r -> %r (flex_date=%s)',
                search_id, server_search_id, flex_date,
            )
            search_id = server_search_id

        # --- Track observed filteredCount across sessions ---
        if filtered_count > self.observed_filtered_count:
            self.observed_filtered_count = filtered_count

        # --- Currency check (best-effort, on first page only) ---
        if page == 1 and results:
            # Inspect the first booking option currency to detect if our cookie
            # approach succeeded. If it differs from the requested currency, warn.
            first_result = results[0]
            first_bos = first_result.get('bookingOptions') or []
            if first_bos:
                response_currency = (first_bos[0].get('displayPrice') or {}).get('currency') or first_bos[0].get('currency') or ''
                if response_currency and response_currency.upper() != self.currency.upper():
                    logger.warning(
                        'Requested currency %r but Momondo returned prices in %r.',
                        self.currency, response_currency,
                    )

        logger.debug(
            'Poll page %d: %d results returned, filteredCount=%d, '
            'pageSize=%d, flex_date=%s, searchId=%r',
            current_page, len(results), filtered_count, page_size, flex_date, search_id,
        )

        # --- Wait for the search to finish before emitting page 1 ---
        # Momondo streams providers in phases ("first-phase" ... "complete"); emitting the
        # first batch returns ~10% of fares and misses late, often cheaper, providers.
        # A missing status (older responses / fixtures) is treated as complete.
        search_status = data.get('status')
        if page == 1 and search_status and search_status != 'complete':
            if phase_polls < _MAX_PHASE_POLLS:
                yield self._make_poll_request(
                    legs=legs,
                    page=1,
                    flex_date=flex_date,
                    search_id=search_id,
                    page1_retry_count=page1_retry_count,
                    delay_seconds=_PHASE_POLL_DELAY_SECONDS,
                    phase_polls=phase_polls + 1,
                )
                return
            logger.warning(
                'Momondo had not finished searching after %d checks; returning the flights found so far%s.',
                phase_polls, f' for {flex_date}' if flex_date else '',
            )

        if page == 1 and search_status == 'complete' and not results and not filtered_count:
            logger.info('Momondo finished the search and found no flights%s.', f' for {flex_date}' if flex_date else '')
            type(self).no_flights = True
            return

        if page == 1 and results:
            logger.info('Momondo found %d flights%s.', filtered_count, f' for {flex_date}' if flex_date else '')

        # --- Long-poll retry: page 1 returned 0 results ---
        # Retry regardless of filteredCount value.
        #
        # BLOCKER FIX (issue #8 follow-up): On Apify datacenter IPs, Momondo returns
        # 0 results AND filteredCount=0 on the first poll even though the search is
        # active (the server issues a searchId, proving the request was accepted).
        # The old condition "filtered_count > 0" caused the spider to give up immediately.
        # The fix: retry unconditionally when page 1 is empty, for up to
        # _EMPTY_FIRST_PAGE_MAX_RETRIES attempts with exponential backoff.
        if page == 1 and not results:
            if page1_retry_count < _EMPTY_FIRST_PAGE_MAX_RETRIES:
                # Exponential backoff: index into _RETRY_BACKOFF_SECONDS; cap at last value.
                backoff_idx = min(page1_retry_count, len(_RETRY_BACKOFF_SECONDS) - 1)
                delay = _RETRY_BACKOFF_SECONDS[backoff_idx]
                logger.debug(
                    'Page 1 empty (filteredCount=%d). Long-poll retry %d/%d after %ds '
                    '(flex_date=%s, searchId=%r).',
                    filtered_count, page1_retry_count + 1, _EMPTY_FIRST_PAGE_MAX_RETRIES,
                    delay, flex_date, search_id,
                )
                yield self._make_poll_request(
                    legs=legs,
                    page=1,
                    flex_date=flex_date,
                    search_id=search_id,
                    page1_retry_count=page1_retry_count + 1,
                    delay_seconds=delay,
                )
            else:
                logger.error(
                    'Momondo returned no flights after %d checks%s.',
                    _EMPTY_FIRST_PAGE_MAX_RETRIES, f' for {flex_date}' if flex_date else '',
                )
                type(self).crawl_failed = True
            return

        if not results:
            logger.debug('No results on page %d (flex_date=%s) — stopping pagination.', page, flex_date)
            return

        # --- Yield each result to parse_result ---
        # items_yielded is incremented ONLY when an item is actually yielded
        # (parse_result may yield 0 items if client-side filters drop the result).
        for raw_result in results:
            if self._quota_reached(flex_date):
                return
            for item in self.parse_result(raw_result=raw_result, full_response=data):
                yield item
                self.items_yielded += 1
                if flex_date:
                    self._items_per_flex_date[flex_date] = self._items_per_flex_date.get(flex_date, 0) + 1
                if self._quota_reached(flex_date):
                    return

        # --- Pagination: fire next page if more results exist ---
        # Continue paginating even when client-side filtering has reduced our
        # yield count, until we reach max_results OR pagination is exhausted.
        if self._quota_reached(flex_date):
            return

        results_fetched = current_page * page_size
        if results_fetched < filtered_count:
            next_page = current_page + 1
            logger.debug(
                'Paginating: fetched=%d < filtered=%d → requesting page %d '
                '(flex_date=%s, searchId=%r)',
                results_fetched, filtered_count, next_page, flex_date, search_id,
            )
            yield self._make_poll_request(
                legs=legs,
                page=next_page,
                flex_date=flex_date,
                search_id=search_id,
            )

    def _quota_reached(self, flex_date: str | None) -> bool:
        """True once max_results is reached — or, in flexible mode, this date's even share of it.

        Without the per-date share the first date to finish fills the whole quota and
        the other dates of a flexible search never reach the output.
        """
        if self.items_yielded >= self.max_results:
            logger.info('Reached maxResults (%d).', self.max_results)
            return True
        if flex_date and self._items_per_flex_date.get(flex_date, 0) >= self._flex_date_quota():
            return True
        return False

    def _flex_date_quota(self) -> int:
        dates = 2 * (self.departure_date_flex_days or 1) + 1
        return -(-self.max_results // dates)  # ceil

    def _extract_csrf_from_response(self, response: Response) -> None:
        """No-op: CSRF token is now extracted during bootstrap from page HTML.

        The formToken is embedded in the flight-search results page as:
            window.R9.formToken = '<value>';

        It does NOT appear in poll response Set-Cookie headers (contrary to
        earlier research notes).  Token extraction happens in parse_bootstrap().
        This method is kept as a hook in case token rotation is observed later.
        """
        pass

    def _handle_401(
        self,
        response: Response,
        legs: list[dict],
        page: int,
        flex_date: str | None,
        search_id: str | None = None,
        reason: str = 'session expired',
    ) -> Generator[Request, None, None]:
        """Re-open the search page in a fresh browser session and retry the poll (bounded)."""
        if self._session_refreshes >= _MAX_SESSION_REFRESHES:
            logger.error('Momondo still refuses the search after %d retries. Stopping.', self._session_refreshes)
            logger.debug('Last refusal: %s', reason)
            type(self).auth_failed = True
            raise CloseSpider('auth_failed')

        self._session_refreshes += 1
        logger.warning('Momondo refused the search; retrying (%d/%d).', self._session_refreshes, _MAX_SESSION_REFRESHES)
        logger.debug('Refusal: %s', reason)
        self.csrf_token = ''  # Force re-extraction during next bootstrap.
        if page == 1:
            search_id = None  # the old searchId belongs to the discarded session

        # Retry this poll after the new session opens. Only JSON-safe values go in meta:
        # the Apify scheduler serialises meta through the request queue, so a Request
        # object stored there would not survive the round trip.
        retry = {'legs': legs, 'page': page, 'flex_date': flex_date, 'search_id': search_id}
        bootstrap_url = self._bootstrap_url()
        yield scrapy.Request(
            url=bootstrap_url,
            callback=self.parse_bootstrap_refresh,
            errback=self.errback_bootstrap,
            meta={'phase': 'bootstrap_refresh', 'browser': 'navigate', 'retry_poll': retry},
            dont_filter=True,
        )

    def parse_bootstrap_refresh(self, response: Response) -> Generator[Request, None, None]:
        """Callback after a session-refresh bootstrap GET.

        Re-extracts the formToken from the refreshed page, then fires the
        retry request that was stored in meta.
        """
        logger.debug('Session refresh bootstrap complete (status=%d).', response.status)
        match = _FORM_TOKEN_RE.search(response.text)
        if match:
            self.csrf_token = match.group(1)
            logger.debug('Refreshed formToken extracted (len=%d).', len(self.csrf_token))
        else:
            logger.debug('formToken not found in refresh bootstrap response.')

        retry = response.meta.get('retry_poll')
        if retry:
            # Built now, so it carries the refreshed x-csrf token.
            yield self._make_poll_request(**retry)

    def errback_poll(self, failure: Any) -> None:
        """Handle network-level errors and HTTP error responses on poll requests.

        Scrapy's HttpErrorMiddleware intercepts non-2xx responses and routes them
        here as failures rather than to parse_poll. We set crawl_failed so that
        main.py can report Actor.fail() instead of SUCCEEDED with 0 items.
        """
        from scrapy.spidermiddlewares.httperror import HttpError
        from twisted.web.error import Error as TwistedError

        if failure.check(HttpError):
            response = failure.value.response
            status = response.status
            # 401 has its own session-refresh path; don't mark crawl_failed for it.
            if status == 401:
                logger.error('Momondo refused the search (HTTP 401).')
                type(self).crawl_failed = True
            elif 400 <= status < 500:
                try:
                    body_preview = response.text[:500]
                except Exception:
                    body_preview = '<unreadable>'
                logger.error('Momondo refused the search (HTTP %d).', status)
                logger.debug('Response body: %s', body_preview)
                type(self).crawl_failed = True
            else:
                logger.error('Momondo returned HTTP %d.', status)
                type(self).crawl_failed = True
        else:
            logger.error('Network error while searching Momondo: %s', failure.getErrorMessage())
            type(self).crawl_failed = True

    def closed(self, reason: str) -> None:
        """Spider closed hook: detect silent failure (0 items despite seeing filteredCount > 0)."""
        if (
            self.items_yielded == 0
            and self.observed_filtered_count > 0
            and not self.auth_failed
        ):
            if self.skipped_filtered:
                logger.warning(
                    'Momondo found %d fares, but none matched the cabin class / maximum stops filters.',
                    self.observed_filtered_count,
                )
                type(self).no_matches = True
                return
            logger.error(
                'Momondo reported %d flights, but none could be read from its response.',
                self.observed_filtered_count,
            )
            type(self).crawl_failed = True

    # ------------------------------------------------------------------
    # Step 4 — parse_result stub (parser-implementer fills this in)
    # ------------------------------------------------------------------

    def parse_result(
        self,
        raw_result: dict,
        full_response: dict,
    ) -> Generator[FlightItem, None, None]:
        """Extract one FlightItem from a single result dict.

        Args:
            raw_result:    One element from response["results"].
            full_response: The complete poll response dict (for lookup tables:
                           legs, segments, providers, filterData, airports, airlines).
        """
        result_id = raw_result.get('resultId', '')
        trip_id = raw_result.get('tripId') or ''

        # Skip results without legs (e.g. inline ad placeholders)
        result_leg_refs = raw_result.get('legs') or []
        if not result_leg_refs:
            logger.debug('Skipping result %s — no legs.', result_id)
            return

        # ----------------------------------------------------------------
        # Build lookup tables (once per result — cheap dict reads)
        # ----------------------------------------------------------------

        # Airport lookup: IATA code -> display name
        # data["airports"] is a top-level dict: {code: {"displayName": ..., ...}}
        raw_airports = full_response.get('airports') or {}
        airport_lookup: dict[str, str] = {
            code: info.get('displayName', code)
            for code, info in raw_airports.items()
            if isinstance(info, dict)
        }

        # Airline lookup: carrier code -> display name
        # filterData.airlines.items[] each has {"id": code, "displayValue": name}
        airline_items = (
            (full_response.get('filterData') or {})
            .get('airlines', {})
            .get('items', [])
        )
        airline_lookup: dict[str, str] = {
            item['id']: item.get('displayValue', item['id'])
            for item in airline_items
            if isinstance(item, dict) and 'id' in item
        }

        # Top-level lookup tables
        legs_data: dict = full_response.get('legs') or {}
        segments_data: dict = full_response.get('segments') or {}
        providers_data: dict = full_response.get('providers') or {}

        # ----------------------------------------------------------------
        # Assemble legs list
        # ----------------------------------------------------------------

        legs_list: list[dict] = []
        total_duration = 0
        total_stops = 0
        overall_departure_time: str | None = None
        overall_arrival_time: str | None = None
        overall_origin_code: str | None = None
        overall_destination_code: str | None = None

        for leg_ref in result_leg_refs:
            leg_id = leg_ref.get('id') if isinstance(leg_ref, dict) else leg_ref
            if not leg_id:
                continue

            leg_obj = legs_data.get(leg_id)
            if not leg_obj:
                logger.debug(
                    'Leg %r not found in top-level legs dict for result %s. Skipping leg.',
                    leg_id, result_id,
                )
                continue

            leg_departure = leg_obj.get('departure') or ''
            leg_arrival = leg_obj.get('arrival') or ''
            leg_duration = leg_obj.get('duration') or 0
            leg_seg_refs = leg_obj.get('segments') or []

            # ---- Assemble segments for this leg ----
            seg_list: list[dict] = []
            first_seg_origin: str | None = None
            last_seg_destination: str | None = None

            for seg_ref in leg_seg_refs:
                seg_id = seg_ref.get('id') if isinstance(seg_ref, dict) else seg_ref
                if not seg_id:
                    continue

                seg_obj = segments_data.get(seg_id)
                if not seg_obj:
                    logger.debug(
                        'Segment %r not found for leg %r in result %s. Skipping segment.',
                        seg_id, leg_id, result_id,
                    )
                    continue

                airline_code = seg_obj.get('airline') or ''
                flight_number_raw = seg_obj.get('flightNumber') or ''
                seg_origin = seg_obj.get('origin') or ''
                seg_destination = seg_obj.get('destination') or ''

                seg_dict = {
                    'airline_code': airline_code,
                    'airline_name': airline_lookup.get(airline_code, airline_code),
                    'flight_number': f'{airline_code}{flight_number_raw}' if airline_code and flight_number_raw else '',
                    'aircraft_type': seg_obj.get('equipmentTypeName') or '',
                    'origin': {
                        'code': seg_origin,
                        'name': airport_lookup.get(seg_origin, seg_origin),
                    },
                    'destination': {
                        'code': seg_destination,
                        'name': airport_lookup.get(seg_destination, seg_destination),
                    },
                    'departure_time': seg_obj.get('departure') or '',
                    'arrival_time': seg_obj.get('arrival') or '',
                    'duration_minutes': seg_obj.get('duration') or 0,
                }
                seg_list.append(seg_dict)

                if first_seg_origin is None:
                    first_seg_origin = seg_origin
                last_seg_destination = seg_destination

            leg_stops = max(len(seg_list) - 1, 0)
            total_stops += leg_stops
            total_duration += leg_duration

            # Overall trip departure = first leg's departure
            if overall_departure_time is None:
                overall_departure_time = leg_departure
                overall_origin_code = first_seg_origin

            # Overall trip arrival = last leg's arrival
            overall_arrival_time = leg_arrival
            overall_destination_code = last_seg_destination

            # Leg-level origin/destination from its first/last segment
            leg_origin_code = first_seg_origin or ''
            leg_dest_code = last_seg_destination or ''

            leg_dict = {
                'origin': {
                    'code': leg_origin_code,
                    'name': airport_lookup.get(leg_origin_code, leg_origin_code),
                },
                'destination': {
                    'code': leg_dest_code,
                    'name': airport_lookup.get(leg_dest_code, leg_dest_code),
                },
                'departure_time': leg_departure,
                'arrival_time': leg_arrival,
                'duration_minutes': leg_duration,
                'stops': leg_stops,
                'segments': seg_list,
            }
            legs_list.append(leg_dict)

        if not legs_list:
            logger.debug('No valid legs assembled for result %s. Skipping.', result_id)
            return

        # ----------------------------------------------------------------
        # Client-side maxStops filter (QA-confirmed: API rejects filterParams.stops)
        # total_stops = sum of (segments - 1) per leg
        # ----------------------------------------------------------------
        if self.max_stops is not None and total_stops > self.max_stops:
            logger.debug(
                'Dropping result %s — total_stops=%d exceeds max_stops=%d.',
                result_id, total_stops, self.max_stops,
            )
            self.skipped_filtered += 1
            return

        # ----------------------------------------------------------------
        # Assemble booking options
        # ----------------------------------------------------------------

        booking_options_list: list[dict] = []
        for bo in (raw_result.get('bookingOptions') or []):
            provider_code = bo.get('providerCode') or ''
            prov_obj = providers_data.get(provider_code) or {}

            # bookingUrl is a dict {"url": ..., "urlType": "relative"/"absolute"}
            booking_url_obj = bo.get('bookingUrl') or {}
            if isinstance(booking_url_obj, dict):
                raw_url = booking_url_obj.get('url') or ''
                url_type = booking_url_obj.get('urlType') or 'relative'
                if url_type == 'relative' and raw_url.startswith('/'):
                    booking_url = f'https://www.momondo.com{raw_url}'
                else:
                    booking_url = raw_url
            else:
                booking_url = str(booking_url_obj)

            # Price from displayPrice dict
            display_price = bo.get('displayPrice') or {}
            price = display_price.get('price') or 0
            # displayPrice.currency is the currency of `price`; the top-level bo.currency
            # can differ (provider's own currency) once a display currency is selected.
            currency = display_price.get('currency') or bo.get('currency') or ''

            # Cabin class: legFarings[0].segmentFarings[0].cabinDisplay
            cabin_class = ''
            leg_farings = bo.get('legFarings') or []
            if leg_farings:
                seg_farings = (leg_farings[0] or {}).get('segmentFarings') or []
                if seg_farings:
                    cabin_class = (seg_farings[0] or {}).get('cabinDisplay') or ''

            bo_dict = {
                'provider_code': provider_code,
                'provider_name': prov_obj.get('displayName', provider_code),
                'price': price,
                'currency': currency,
                'booking_url': booking_url,
                'cabin_class': cabin_class,
            }
            bo_dict.update(_fee_fields(bo.get('fees')))
            booking_options_list.append(bo_dict)

        # ----------------------------------------------------------------
        # Client-side cabinClass filter (QA-confirmed: API rejects userSearchParams.cabinClass)
        # Applied only when cabin_class != ECONOMY (Momondo defaults to economy,
        # filtering economy would incorrectly drop mixed-class results).
        # A result is kept if AT LEAST ONE booking option matches the requested class.
        # ----------------------------------------------------------------
        if self.cabin_class != 'ECONOMY' and booking_options_list:
            matching = [
                bo for bo in booking_options_list
                if _cabin_matches(bo.get('cabin_class', ''), self.cabin_class)
            ]
            # Only the booking options in the requested cabin are returned, so a Business
            # search does not list the same flight's Economy fares.
            booking_options_list = matching
            if not matching:
                logger.debug(
                    'Dropping result %s — no booking options match cabin_class=%r.',
                    result_id, self.cabin_class,
                )
                self.skipped_filtered += 1
                return

        # ----------------------------------------------------------------
        # Load into FlightItem via FlightItemLoader
        # ----------------------------------------------------------------

        loader = FlightItemLoader(item=FlightItem())
        loader.add_value('result_id', result_id)
        loader.add_value('trip_id', trip_id)
        loader.add_value('origin', {
            'code': overall_origin_code or '',
            'name': airport_lookup.get(overall_origin_code or '', overall_origin_code or ''),
        })
        loader.add_value('destination', {
            'code': overall_destination_code or '',
            'name': airport_lookup.get(overall_destination_code or '', overall_destination_code or ''),
        })
        loader.add_value('departure_time', overall_departure_time or '')
        loader.add_value('arrival_time', overall_arrival_time or '')
        loader.add_value('duration_minutes', total_duration)
        loader.add_value('stops', total_stops)
        loader.add_value('legs', legs_list or [])
        loader.add_value('booking_options', booking_options_list or [])
        loader.add_value('scraped_at', datetime.now(timezone.utc).isoformat())

        item = loader.load_item()
        item['bags_requested'] = {'checked_bags': self.checked_bags, 'carry_on_bag': self.carry_on_bag}
        item['baggage_policy_links'] = _baggage_policy_links(legs_list, full_response.get('airlines') or {})
        yield item
