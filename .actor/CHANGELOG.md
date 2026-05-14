# Changelog

All notable changes to the Momondo Flight Scraper actor are documented in this file.

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
- **Currency preference** — best-effort currency selection via bootstrap cookie (USD reliable; other currencies best-effort).
- **`maxResults` cap** — configurable 1–500 results per run with automatic pagination.
