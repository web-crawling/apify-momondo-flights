# Changelog

All notable changes to the Momondo Flight Scraper actor are documented in this file.

## [1.1.0] - 2026-10-15

### Added

- **Bag-inclusive prices**: new `checkedBags` (0–2 per passenger) and `carryOnBag` inputs. Every price includes the requested bags, using Momondo's own bag-fee filter.
- **Price breakdown** on each booking option: `base_price` (fare before bags), `total_price_all_passengers`, and `checked_bag` / `carry_on_bag` with status, fee and bags included.
- **`baggage_policy_links`**: each airline's baggage policy page, for special items such as sports equipment.
- **`bags_requested`** on every result, recording the bag selection that was priced.

### Removed

- The `proxyConfiguration` input. The actor needs no proxy.

### Fixed

- Searches work again. Momondo had started refusing them, so runs finished with no results.
- Runs that fail now show as failed on Apify instead of succeeding with no data.
- `currency` now returns prices in the chosen currency. The `currency` field shows the currency of the price.
- Premium Economy, Business and First are now searched as such on Momondo, and Premium Economy fares are recognised.
- Children and infants work. Searches with them were previously rejected by Momondo.
- Results are collected once Momondo has finished searching (up to about 30 seconds), so most late-answering providers are included.
- Flexible-date searches share `maxResults` evenly across the dates.
- `departureDate` defaults to 30 days from today. Past dates are rejected with a clear message.
- When Momondo has no flights for a search, or none match `cabinClass` or `maxStops`, the run succeeds and its status message says so.
- Searches in Premium Economy, Business or First list only the booking options in that cabin.

## [1.0.0] - 2026-05-14

### Added

- **One-way flight search** — search for outbound flights between any two IATA airport codes with configurable date and passenger count.
- **Round-trip flight search** — search for paired outbound and return flights with separate departure and return dates.
- **Multi-city flight search** — search itineraries with two or more independent legs via structured `legs` array input.
- **Flexible date-range search** — fan out across ±1–7 days around a target departure date in a single run.
- **Cabin class filtering** — filter results to Economy, Premium Economy, Business, or First class.
- **Stop count filtering** — limit results to nonstop (`maxStops: 0`) or a maximum number of connections.
- **Multi-passenger support** — configure adults, children, and infants independently.
- **Structured output** — each result includes nested leg, segment, and booking option objects with airline, flight number, aircraft type, per-provider price, and direct booking URLs.
- **Currency preference** — best-effort currency selection (USD reliable; other currencies best-effort).
- **`maxResults` cap** — configurable 1–500 results per run with automatic pagination.
