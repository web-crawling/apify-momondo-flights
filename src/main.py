"""Module defines the main entry point for the Apify Actor.

Processes the Actor's input, validates it, and executes the MomondoSpider.
"""

from __future__ import annotations

from apify import Actor
from apify.scrapy import apply_apify_settings
from scrapy.crawler import CrawlerRunner
from scrapy.utils.defer import deferred_to_future

from .spiders.momondo import MomondoSpider

# Valid trip types accepted by the spider.
VALID_TRIP_TYPES = {'one-way', 'round-trip', 'multi-city', 'flexible'}

# Valid cabin class values.
VALID_CABIN_CLASSES = {'ECONOMY', 'PREMIUM_ECONOMY', 'BUSINESS', 'FIRST'}


async def main() -> None:
    """Apify Actor main coroutine for executing the Momondo flights spider."""
    async with Actor:
        actor_input = await Actor.get_input() or {}

        # --- tripType ---
        trip_type = str(actor_input.get('tripType', 'one-way')).lower()
        # Normalise: accept "oneway", "roundtrip" etc. from legacy callers
        trip_type = trip_type.replace('_', '-')
        if trip_type not in VALID_TRIP_TYPES:
            Actor.log.warning(
                'Invalid tripType %r — defaulting to "one-way". '
                'Valid values: %s.',
                trip_type,
                ', '.join(sorted(VALID_TRIP_TYPES)),
            )
            trip_type = 'one-way'

        # --- origin / destination ---
        origin = str(actor_input.get('origin', 'JFK')).strip().upper()
        destination = str(actor_input.get('destination', 'LHR')).strip().upper()

        # --- departureDate ---
        departure_date = str(actor_input.get('departureDate', '2026-08-01')).strip()

        # --- returnDate (required for round-trip) ---
        return_date_raw = actor_input.get('returnDate')
        return_date: str | None = str(return_date_raw).strip() if return_date_raw else None

        if trip_type == 'round-trip' and not return_date:
            await Actor.fail(
                status_message=(
                    'tripType is "round-trip" but returnDate is not set. '
                    'Please provide a returnDate (YYYY-MM-DD).'
                )
            )
            return

        # --- departureDateFlexDays ---
        flex_days_raw = actor_input.get('departureDateFlexDays')
        departure_date_flex_days: int | None = None
        if flex_days_raw is not None:
            try:
                departure_date_flex_days = int(flex_days_raw)
                if not (1 <= departure_date_flex_days <= 7):
                    Actor.log.warning(
                        'departureDateFlexDays=%d is out of range [1, 7] — clamping.',
                        departure_date_flex_days,
                    )
                    departure_date_flex_days = max(1, min(7, departure_date_flex_days))
            except (ValueError, TypeError):
                Actor.log.warning(
                    'Invalid departureDateFlexDays value %r — ignoring.', flex_days_raw
                )

        # --- legs (multi-city) ---
        legs_raw = actor_input.get('legs') or []
        legs: list[dict] = []
        if trip_type == 'multi-city':
            if not legs_raw:
                await Actor.fail(
                    status_message=(
                        'tripType is "multi-city" but no legs were provided. '
                        'Please supply the "legs" input parameter with at least 2 legs.'
                    )
                )
                return
            for i, leg in enumerate(legs_raw):
                if not isinstance(leg, dict):
                    Actor.log.warning('Ignoring invalid leg at index %d: %r', i, leg)
                    continue
                if not leg.get('origin') or not leg.get('destination') or not leg.get('departureDate'):
                    Actor.log.warning(
                        'Leg %d is missing required fields (origin/destination/departureDate): %r',
                        i, leg,
                    )
                    continue
                legs.append({
                    'origin': str(leg['origin']).strip().upper(),
                    'destination': str(leg['destination']).strip().upper(),
                    'departureDate': str(leg['departureDate']).strip(),
                })
            if not legs:
                await Actor.fail(
                    status_message='No valid legs found in the "legs" input. Each leg must have origin, destination, and departureDate.'
                )
                return

        # --- passengers ---
        adults_raw = actor_input.get('adults', 1)
        children_raw = actor_input.get('children', 0)
        infants_raw = actor_input.get('infants', 0)

        try:
            adults = max(1, int(adults_raw))
        except (ValueError, TypeError):
            Actor.log.warning('Invalid adults value %r — defaulting to 1.', adults_raw)
            adults = 1

        try:
            children = max(0, int(children_raw))
        except (ValueError, TypeError):
            Actor.log.warning('Invalid children value %r — defaulting to 0.', children_raw)
            children = 0

        try:
            infants = max(0, int(infants_raw))
        except (ValueError, TypeError):
            Actor.log.warning('Invalid infants value %r — defaulting to 0.', infants_raw)
            infants = 0

        total_passengers = adults + children + infants
        if total_passengers > 9:
            Actor.log.warning(
                'Total passengers (%d) exceeds maximum of 9. '
                'Adjusting: keeping %d adults and reducing others.',
                total_passengers, adults,
            )
            # Reduce proportionally: adults stay, reduce children then infants
            excess = total_passengers - 9
            children_trim = min(excess, children)
            children -= children_trim
            excess -= children_trim
            infants = max(0, infants - excess)

        # --- cabinClass ---
        cabin_class = str(actor_input.get('cabinClass', 'ECONOMY')).strip().upper()
        if cabin_class not in VALID_CABIN_CLASSES:
            Actor.log.warning(
                'Invalid cabinClass %r — defaulting to "ECONOMY". '
                'Valid values: %s.',
                cabin_class,
                ', '.join(sorted(VALID_CABIN_CLASSES)),
            )
            cabin_class = 'ECONOMY'

        # --- currency ---
        currency = str(actor_input.get('currency', 'USD')).strip().upper()
        if len(currency) != 3:
            Actor.log.warning(
                'currency %r does not look like a valid ISO 4217 code — defaulting to "USD".',
                currency,
            )
            currency = 'USD'

        # --- maxStops ---
        max_stops_raw = actor_input.get('maxStops')
        max_stops: int | None = None
        if max_stops_raw is not None:
            try:
                max_stops = int(max_stops_raw)
                if max_stops < 0:
                    Actor.log.warning(
                        'maxStops must be >= 0; got %r — ignoring.', max_stops_raw
                    )
                    max_stops = None
            except (ValueError, TypeError):
                Actor.log.warning(
                    'Invalid maxStops value %r — ignoring (no stop restriction).', max_stops_raw
                )

        # --- maxResults ---
        max_results_raw = actor_input.get('maxResults', 50)
        max_results = 50
        try:
            max_results = int(max_results_raw)
            if max_results < 1:
                Actor.log.warning(
                    'maxResults must be >= 1; got %r — defaulting to 50.', max_results_raw
                )
                max_results = 50
        except (ValueError, TypeError):
            Actor.log.warning(
                'Invalid maxResults value %r — defaulting to 50.', max_results_raw
            )

        # --- proxyConfiguration ---
        proxy_configuration = actor_input.get('proxyConfiguration') or None

        Actor.log.info(
            'Actor input parsed: trip_type=%s origin=%s destination=%s '
            'departure_date=%s return_date=%s flex_days=%s '
            'adults=%d children=%d infants=%d cabin=%s currency=%s '
            'max_stops=%s max_results=%d proxy=%s',
            trip_type, origin, destination,
            departure_date, return_date, departure_date_flex_days,
            adults, children, infants, cabin_class, currency,
            max_stops, max_results,
            'configured' if proxy_configuration else 'none',
        )

        # --- Build Scrapy settings ---
        settings = apply_apify_settings()
        settings.set('CLOSESPIDER_ITEMCOUNT', max_results)

        # --- Run spider ---
        MomondoSpider.auth_failed = False  # reset before each run
        crawler_runner = CrawlerRunner(settings)
        crawl_deferred = crawler_runner.crawl(
            MomondoSpider,
            trip_type=trip_type,
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            departure_date_flex_days=departure_date_flex_days,
            legs=legs,
            adults=adults,
            children=children,
            infants=infants,
            cabin_class=cabin_class,
            currency=currency,
            max_stops=max_stops,
            max_results=max_results,
            proxy_configuration=proxy_configuration,
        )
        spider = await deferred_to_future(crawl_deferred)

        # --- auth_failed pattern: report failure to Apify platform ---
        # Scrapy returns the spider instance from crawl_deferred in some versions;
        # check both the class attribute (set in __init__) and MomondoSpider.auth_failed.
        if MomondoSpider.auth_failed:
            await Actor.fail(
                status_message=(
                    'Spider authentication failed. CSRF token or session cookies '
                    'could not be established. Check logs for details.'
                )
            )
