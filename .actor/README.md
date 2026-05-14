# Momondo Flight Scraper

Extract real-time flight prices, routes, and booking options from Momondo.com, a metasearch engine aggregating results from major airlines and online travel agencies. Supports one-way, round-trip, multi-city, and flexible date-range searches. Returns structured JSON with departure/arrival times, stops, duration, and per-provider pricing.

## Key Features

- **Four search modes** — one-way, round-trip, multi-city, and flexible date-range (±1–7 days)
- **Structured JSON output** — nested legs, segments, and booking options per result
- **Multi-passenger support** — configure adults, children, and infants independently
- **Cabin class filtering** — Economy, Premium Economy, Business, First (applied client-side)
- **Stop count filtering** — limit results to nonstop or max N connections
- **No proxy required** — direct requests to Momondo from datacenter IPs succeed without residential proxy configuration
- **Scalable** — configure `maxResults` up to 500 per run; pagination handled automatically

## Use Cases

- **Price monitoring dashboards** — schedule daily runs and track fare changes over time for specific routes
- **Travel deal aggregation** — collect hundreds of flight options across multiple routes and surface the cheapest combinations
- **ML training datasets** — build predictive pricing models using historical price snapshots scraped at scale
- **Booking engine comparison** — source live per-provider pricing for your own travel comparison product
- **Corporate travel auditing** — verify quoted fares against live Momondo results for policy compliance

## Getting Started

1. Open the actor in Apify Store and click **Try for free**.
2. Set `origin`, `destination`, and `departureDate`.
3. Optionally set `tripType`, `adults`, `cabinClass`, or `maxResults`.
4. Click **Start** and find structured flight records in the **Dataset** tab.

Minimal one-way input:

```json
{
  "tripType": "one-way",
  "origin": "JFK",
  "destination": "LHR",
  "departureDate": "2026-08-01",
  "maxResults": 20
}
```

## Input Parameters

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `tripType` | string | No | `one-way` | Search mode: `one-way`, `round-trip`, `multi-city`, or `flexible` |
| `origin` | string (IATA) | Yes (non multi-city) | `JFK` | Departure airport code, e.g. `JFK`, `LHR`, `CDG` |
| `destination` | string (IATA) | Yes (non multi-city) | `LHR` | Arrival airport code |
| `departureDate` | string `YYYY-MM-DD` | Yes (non multi-city) | `2026-08-01` | Outbound departure date |
| `returnDate` | string `YYYY-MM-DD` | When round-trip | `null` | Return departure date (round-trip only) |
| `departureDateFlexDays` | integer 1–7 | When flexible | `null` | Search ±N days around `departureDate` |
| `legs` | array of objects | When multi-city | `[]` | Array of `{origin, destination, departureDate}` leg objects |
| `adults` | integer 1–9 | No | `1` | Number of adult passengers |
| `children` | integer 0–8 | No | `0` | Number of child passengers |
| `infants` | integer 0–4 | No | `0` | Number of infant passengers |
| `cabinClass` | string | No | `ECONOMY` | `ECONOMY`, `PREMIUM_ECONOMY`, `BUSINESS`, or `FIRST` |
| `currency` | string ISO 4217 | No | `USD` | Requested display currency (best-effort; see Limitations) |
| `maxStops` | integer 0–3 | No | `null` | Maximum total connections across all legs |
| `maxResults` | integer 1–500 | No | `50` | Maximum number of flight results to return |
| `proxyConfiguration` | object | No | `null` | Optional Apify proxy configuration (not required for most runs) |

## Output Data

Each flight result is a JSON object with the following shape:

```json
{
  "result_id": "BH8T2JFT3P7GBMXZKS_0",
  "trip_id": "BH8T2JFT3P7GBMXZKS",
  "origin": { "code": "JFK", "name": "New York John F. Kennedy" },
  "destination": { "code": "LHR", "name": "London Heathrow" },
  "departure_time": "2026-08-01T09:25:00",
  "arrival_time": "2026-08-01T21:35:00",
  "duration_minutes": 430,
  "stops": 0,
  "legs": [
    {
      "origin": { "code": "JFK", "name": "New York John F. Kennedy" },
      "destination": { "code": "LHR", "name": "London Heathrow" },
      "departure_time": "2026-08-01T09:25:00",
      "arrival_time": "2026-08-01T21:35:00",
      "duration_minutes": 430,
      "stops": 0,
      "segments": [
        {
          "airline_code": "BA",
          "airline_name": "British Airways",
          "flight_number": "BA177",
          "aircraft_type": "Boeing 777",
          "origin": { "code": "JFK", "name": "New York John F. Kennedy" },
          "destination": { "code": "LHR", "name": "London Heathrow" },
          "departure_time": "2026-08-01T09:25:00",
          "arrival_time": "2026-08-01T21:35:00",
          "duration_minutes": 430
        }
      ]
    }
  ],
  "booking_options": [
    {
      "provider_code": "EXPEDIAAIR",
      "provider_name": "Expedia",
      "price": 498,
      "currency": "USD",
      "booking_url": "https://www.momondo.com/s/clickthrough.jsp?...",
      "cabin_class": "Economy"
    }
  ],
  "scraped_at": "2026-08-01T12:00:00+00:00"
}
```

### Output Field Reference

| Field | Type | Always Present | Description |
|---|---|---|---|
| `result_id` | string | Yes | Unique identifier for this flight result |
| `trip_id` | string | No | Group identifier shared by results in the same fare family |
| `origin` | `{code, name}` | Yes | Overall trip departure airport |
| `destination` | `{code, name}` | Yes | Overall trip arrival airport |
| `departure_time` | ISO 8601 local | Yes | First leg departure time (local time, no timezone suffix) |
| `arrival_time` | ISO 8601 local | Yes | Last leg arrival time (local time, no timezone suffix) |
| `duration_minutes` | integer | Yes | Total trip duration in minutes across all legs |
| `stops` | integer | Yes | Total number of connections across all legs |
| `legs` | array | Yes | One object per flight leg (see Leg fields below) |
| `booking_options` | array | Yes | One object per booking provider with price and URL |
| `scraped_at` | ISO 8601 UTC | Yes | Timestamp when this result was scraped (UTC, with `+00:00`) |

**Leg fields:** `origin`, `destination`, `departure_time`, `arrival_time`, `duration_minutes`, `stops`, `segments[]`

**Segment fields:** `airline_code`, `airline_name`, `flight_number`, `aircraft_type`, `origin`, `destination`, `departure_time`, `arrival_time`, `duration_minutes`

**Booking option fields:** `provider_code`, `provider_name`, `price`, `currency`, `booking_url`, `cabin_class`

## Search Modes Deep Dive

### One-Way Searches

Search for a single outbound flight between two airports.

```json
{
  "tripType": "one-way",
  "origin": "JFK",
  "destination": "LHR",
  "departureDate": "2026-08-01",
  "adults": 2,
  "cabinClass": "ECONOMY",
  "maxResults": 50
}
```

### Round-Trip Searches

Search for paired outbound and return flights. Both `departureDate` and `returnDate` are required.

```json
{
  "tripType": "round-trip",
  "origin": "JFK",
  "destination": "LHR",
  "departureDate": "2026-08-01",
  "returnDate": "2026-08-10",
  "adults": 1,
  "maxResults": 30
}
```

**Note:** Momondo's poll API returns one result entry per outbound leg. The return leg is not bundled in the same result object — this matches how Momondo's own website presents round-trip results.

### Multi-City Searches

Search an itinerary with two or more independent legs (open-jaw, multi-stop). Use the `legs` array instead of `origin`/`destination`/`departureDate`.

```json
{
  "tripType": "multi-city",
  "legs": [
    { "origin": "JFK", "destination": "LHR", "departureDate": "2026-08-01" },
    { "origin": "LHR", "destination": "CDG", "departureDate": "2026-08-05" },
    { "origin": "CDG", "destination": "JFK", "departureDate": "2026-08-10" }
  ],
  "adults": 1,
  "maxResults": 20
}
```

### Flexible Date Searches

Search across a range of departure dates (±1–7 days) in a single run. The actor fans out one poll request per date and collects up to `maxResults` items across all dates combined.

```json
{
  "tripType": "flexible",
  "origin": "JFK",
  "destination": "LHR",
  "departureDate": "2026-08-01",
  "departureDateFlexDays": 3,
  "adults": 1,
  "maxResults": 50
}
```

With `departureDateFlexDays: 3`, the actor searches 2026-07-29 through 2026-08-04 (7 dates total). This is useful for finding the cheapest day to travel within a window without running separate queries.

## FAQ

### Does Momondo Flight Scraper require a proxy?

No proxy is required for standard use. The actor makes direct HTTP requests to Momondo from Apify datacenter IPs, which succeed without residential or rotating proxy configuration. An optional `proxyConfiguration` input is available as a fallback if you observe 429 rate-limiting on high-frequency runs.

### What search modes are supported?

Four modes: `one-way`, `round-trip`, `multi-city`, and `flexible` date-range. All four are confirmed working and return structured results. Multi-city accepts an unlimited number of legs via the `legs` array. Flexible mode fans out one request per date across the specified ±N day window.

### What fields are returned in the output?

Each result includes: `result_id`, `trip_id`, `origin`, `destination`, `departure_time`, `arrival_time`, `duration_minutes`, `stops`, `legs` (with nested `segments`), `booking_options` (with price and booking URL per provider), and `scraped_at`. See the Output Data section above for the full field reference and a complete JSON example.

### Does cabinClass filtering work server-side?

No. Momondo's poll API does not accept cabin class or stop count as filter parameters. Both `cabinClass` and `maxStops` filters are applied client-side after results are fetched. This means the actor may return fewer results than `maxResults` when these filters are active, because some results are dropped after retrieval. Setting `cabinClass: BUSINESS` with `maxResults: 20` will return up to 20 Business-class results, but may require fetching more than 20 from the API first.

### How do I search flexible dates?

Set `tripType: "flexible"`, provide `departureDate` as the centre of the range, and set `departureDateFlexDays` to 1–7. The actor generates one search per date in `[departureDate - N, ..., departureDate + N]` and aggregates results up to `maxResults`. The return date (for round-trip flexible searches) is kept fixed.

### What currency are prices returned in?

The actor sends currency preference cookies (`currency` and `kyk_curr`) on the bootstrap request. For USD (the default), prices are reliably returned in USD. For other currencies, Momondo may ignore the preference and return prices in USD regardless. The `currency` input is kept for forward compatibility but is best-effort only. The `currency` field in each booking option always reflects the actual currency returned by Momondo.

### Which airlines and booking partners does Momondo cover?

Momondo is a Kayak-owned metasearch engine that aggregates prices from major airlines (British Airways, American Airlines, Delta, Lufthansa, and others) and online travel agencies (Expedia, Booking.com, Kiwi.com, and others). Coverage depends on the route and date queried. The `booking_options` array in each result lists all providers Momondo found for that flight.

### What timezone are departure and arrival times in?

Times are in the local timezone of the departure or arrival airport, with no UTC offset suffix. For example, `2026-08-01T09:25:00` means 9:25 AM local time at the airport. The `scraped_at` field is always UTC with `+00:00`.

## Pricing and Limits

This actor runs on Apify's platform using standard compute units. Costs depend on the number of results requested and the number of search modes used.

- **Flexible mode** fans out (2N+1) poll requests per run — factor this into your compute budget when setting `departureDateFlexDays`.
- **`maxResults`** caps output at 1–500 items per run. Default is 50.
- For pricing details see the [Apify pricing page](https://apify.com/pricing).
- **Trial credits** are available to new Apify accounts — click "Try for free" to start at no cost.

## Troubleshooting

**No results returned**
The actor returned zero items. Most likely causes:
- Invalid IATA codes: verify `origin` and `destination` are valid 3-letter IATA airport codes (not city names).
- No flights found by Momondo for the requested route and date: try a different date or route.
- `cabinClass` or `maxStops` filters are too restrictive: try removing them to confirm base results exist before filtering.

**Fewer results than `maxResults`**
When `cabinClass` (non-ECONOMY) or `maxStops` is set, results are filtered client-side. Momondo may not have enough matching flights for the route and date to fill `maxResults` after filtering. This is expected behaviour.

**Prices returned in USD when I set a different currency**
Momondo's API may ignore the currency cookie for certain routes or regions and return USD regardless. The `currency` field in each booking option shows the actual currency returned. This is a known limitation — see the FAQ above.

**HTTP 429 / rate limited**
If you run many searches in rapid succession, Momondo may throttle requests with HTTP 429. Add an `proxyConfiguration` (Apify residential proxy) to route requests through different IPs, or reduce run frequency.

**Actor fails with `auth_failed`**
The actor refreshes its session token automatically on HTTP 401. If two consecutive refreshes fail, the actor exits with `auth_failed`. This is rare; retrying the run usually resolves it.

## See Also

- [Google Flights Scraper](https://apify.com/apify/google-flights-scraper) — extract flight data directly from Google Flights
- [Web Scraper](https://apify.com/apify/web-scraper) — general-purpose browser-based scraper for sites not covered by dedicated actors
