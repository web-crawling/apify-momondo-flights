# Momondo Flight Scraper

Extract live flight prices, routes and booking options from Momondo.com, the metasearch engine that compares airlines and online travel agencies. Search one-way, round-trip, multi-city or flexible dates, and get prices **with checked and carry-on bag fees already included**, using the same bag filter travellers use on Momondo. Every result comes back as structured JSON with legs, segments, per-provider prices and booking links.

## ✈️ What you get

- **Bag-inclusive prices**: ask for 0, 1 or 2 checked bags and/or a carry-on per passenger, and every price already includes those fees.
- **A price breakdown for each booking option**: the fare before bags, the bag fees, the price per passenger and the total for the whole party.
- Four search modes: one-way, round-trip, multi-city and flexible dates (±1–7 days).
- Economy, Premium Economy, Business or First, searched as such on Momondo.
- Prices in the currency you choose, for example USD or EUR.
- Adults, children and lap infants, up to 9 passengers.
- A nonstop-only or maximum-connections filter.
- Each airline's baggage policy page, for special items such as sports equipment.
- The actor waits for Momondo to finish collecting fares (up to about 30 seconds), so most late-answering providers are included.

## 🧳 Who uses it

Travel agencies and CRMs use it to price customer offers with the bags people will actually bring, not the bare fare. It also works for tracking fares on specific routes over time, collecting deals across routes and dates, building historical fare datasets, and checking quoted fares against live Momondo prices.

## 🚀 Getting started

1. Open the actor in Apify Store and click **Try for free**.
2. Set `origin` and `destination` (3-letter airport codes) and a `departureDate`.
3. Optionally set `checkedBags`, `tripType`, `adults`, `cabinClass` or `maxResults`.
4. Click **Start** and find the flights in the **Dataset** tab.

Leave `departureDate` empty to search 30 days from today.

One-way search, two adults, one checked bag each:

```json
{
  "origin": "LHR",
  "destination": "FAO",
  "departureDate": "2027-03-12",
  "adults": 2,
  "checkedBags": 1,
  "maxResults": 20
}
```

## ⚙️ Input parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tripType` | string | `one-way` | `one-way`, `round-trip`, `multi-city` or `flexible` |
| `origin` | string (IATA) | `JFK` | Departure airport code, e.g. `JFK`, `LHR`, `CDG` (not used for multi-city) |
| `destination` | string (IATA) | `LHR` | Arrival airport code (not used for multi-city) |
| `departureDate` | `YYYY-MM-DD` | 30 days from today | Outbound date, today or later |
| `returnDate` | `YYYY-MM-DD` | — | Return date, required for round-trip |
| `departureDateFlexDays` | integer 1–7 | `1` | Flexible mode: search ±N days around `departureDate` |
| `legs` | array | — | Multi-city: `{origin, destination, departureDate}` per leg, at least 2 |
| `adults` | integer | `1` | Adult passengers |
| `children` | integer | `0` | Child passengers, 2–11 years |
| `infants` | integer | `0` | Infants under 2, on an adult's lap |
| `cabinClass` | string | `ECONOMY` | `ECONOMY`, `PREMIUM_ECONOMY`, `BUSINESS` or `FIRST` |
| `currency` | string | `USD` | 3-letter currency code for all prices, e.g. `EUR` |
| `maxStops` | integer 0–3 | — | Maximum connections; `0` = nonstop only |
| `checkedBags` | integer 0–2 | `0` | Checked bags per passenger to include in every price |
| `carryOnBag` | boolean | `false` | Include one carry-on bag per passenger in every price |
| `maxResults` | integer 1–500 | `50` | Maximum number of flights to return |

A search can have up to 9 passengers in total.

## 💶 Bags and pricing

When you set `checkedBags` or `carryOnBag`, the actor uses Momondo's own bag-fee filter. A few things follow from that.

Only fares whose bag cost is known are returned, so you may get fewer results than without bags. `price` is the price per passenger **including** the requested bags, while `base_price` is the fare before any bag fees. `checked_bag.fee` and `carry_on_bag.fee` show the bag fee per passenger, and `total_price_all_passengers` is what the whole party pays, bags included, as Momondo rounds it.

Some fares include more bags than you asked for, such as a fare tier with two checked bags. `bags_in_price` tells you how many.

Without bags, each booking option still tells you whether a checked bag is `included` at no extra cost, costs a known `fee`, or is `unknown` for that provider.

**Sports equipment** such as golf bags, skis or bikes is not priced on Momondo. Use `baggage_policy_links` to open each airline's baggage rules for special items.

**Booking links** open the selected fare at the provider. Check the bags there before paying.

## 📊 Output

A real result from a London–Faro round trip with one checked bag (shortened):

```json
{
  "result_id": "5318fef408ffd8ea02de99dedabe867b",
  "trip_id": "5318fef408ffd8ea02de99dedabe867b",
  "origin": { "code": "LHR", "name": "London Heathrow" },
  "destination": { "code": "LHR", "name": "London Heathrow" },
  "departure_time": "2026-11-20T18:30:00",
  "arrival_time": "2026-11-27T11:25:00",
  "duration_minutes": 640,
  "stops": 2,
  "legs": [
    {
      "origin": { "code": "LHR", "name": "London Heathrow" },
      "destination": { "code": "FAO", "name": "Faro" },
      "departure_time": "2026-11-20T18:30:00",
      "arrival_time": "2026-11-20T23:50:00",
      "duration_minutes": 320,
      "stops": 1,
      "segments": [
        {
          "airline_code": "TP",
          "airline_name": "TAP AIR PORTUGAL",
          "flight_number": "TP1357",
          "aircraft_type": "Airbus A320",
          "origin": { "code": "LHR", "name": "London Heathrow" },
          "destination": { "code": "LIS", "name": "Lisbon Humberto Delgado" },
          "departure_time": "2026-11-20T18:30:00",
          "arrival_time": "2026-11-20T21:20:00",
          "duration_minutes": 170
        }
      ]
    }
  ],
  "booking_options": [
    {
      "provider_code": "BOOKINGFLIGHTS",
      "provider_name": "Booking.com",
      "price": 424,
      "currency": "USD",
      "booking_url": "https://www.momondo.com/book/flight?code=...",
      "cabin_class": "Economy",
      "base_price": 371,
      "total_price_all_passengers": 847,
      "checked_bag": { "status": "included", "fee": 53, "bags_in_price": 1 },
      "carry_on_bag": { "status": "included", "fee": 0, "bags_in_price": 0 }
    }
  ],
  "bags_requested": { "checked_bags": 1, "carry_on_bag": false },
  "baggage_policy_links": [
    { "airline_code": "TP", "airline_name": "TAP AIR PORTUGAL", "url": "https://www.flytap.com/en-us/baggage/hold-baggage?tabid=..." }
  ],
  "scraped_at": "2026-10-15T07:37:58+00:00"
}
```

Here the fare is 371 per person, the checked bag adds 53 (so the price is 424), and the carry-on comes free with the fare.

### Field reference

| Field | Type | Description |
|---|---|---|
| `result_id` | string | Unique identifier for this flight result |
| `trip_id` | string | Momondo trip grouping identifier |
| `origin` / `destination` | `{code, name}` | Where the itinerary starts and finally ends (for a round trip, both are the home airport) |
| `departure_time` / `arrival_time` | ISO 8601, local | First departure and last arrival, airport local time |
| `duration_minutes` | integer | Total flying and connection time across all legs |
| `stops` | integer | Total connections across all legs |
| `legs` | array | One object per leg, each with its `segments` |
| `booking_options` | array | One object per booking provider (see below) |
| `bags_requested` | object | The bag selection priced into `price`: `checked_bags`, `carry_on_bag` |
| `baggage_policy_links` | array | Each airline's baggage policy page: `airline_code`, `airline_name`, `url` |
| `scraped_at` | ISO 8601, UTC | When the result was collected |

**Leg fields:** `origin`, `destination`, `departure_time`, `arrival_time`, `duration_minutes`, `stops`, `segments[]`

**Segment fields:** `airline_code`, `airline_name`, `flight_number`, `aircraft_type`, `origin`, `destination`, `departure_time`, `arrival_time`, `duration_minutes`

**Booking option fields:**

| Field | Description |
|---|---|
| `provider_code`, `provider_name` | Airline or travel agency offering the fare |
| `price` | Price per passenger, including the requested bags |
| `currency` | Currency of all prices in this option |
| `base_price` | Fare per passenger before bag fees |
| `total_price_all_passengers` | Price for the whole party, bags included |
| `checked_bag`, `carry_on_bag` | `status` (`included`, `fee` or `unknown`), `fee` per passenger, `bags_in_price` |
| `booking_url` | Link to book this fare with the provider |
| `cabin_class` | Cabin of the fare, e.g. Economy, Prem Economy, Business |

Bag and price-breakdown fields are left out when the provider does not publish them.

## 🔁 Search modes

### Round trip

```json
{
  "tripType": "round-trip",
  "origin": "LHR",
  "destination": "FAO",
  "departureDate": "2027-03-12",
  "returnDate": "2027-03-19",
  "adults": 2,
  "checkedBags": 1,
  "maxResults": 30
}
```

Each result is one complete round trip: `legs` holds the outbound and the return flight.

### Multi-city

```json
{
  "tripType": "multi-city",
  "legs": [
    { "origin": "JFK", "destination": "LHR", "departureDate": "2027-03-01" },
    { "origin": "LHR", "destination": "CDG", "departureDate": "2027-03-05" },
    { "origin": "CDG", "destination": "JFK", "departureDate": "2027-03-10" }
  ],
  "maxResults": 20
}
```

### Flexible dates

```json
{
  "tripType": "flexible",
  "origin": "JFK",
  "destination": "LHR",
  "departureDate": "2027-03-12",
  "departureDateFlexDays": 3,
  "maxResults": 70
}
```

This searches 2027-03-09 to 2027-03-15 (7 dates). `maxResults` is shared evenly across the dates (here up to 10 flights per date), so one busy date can't fill the whole run.

## ❓ FAQ

### Can I get prices that include checked baggage?

Yes. Set `checkedBags` to 1 or 2 (per passenger) and/or `carryOnBag` to `true`. Every returned price already includes those bags, and each booking option shows the fare before bags and the bag fee separately.

### Can I price golf bags or other sports equipment?

Momondo does not price special items such as golf bags, skis or bikes. Each result includes `baggage_policy_links` so you can check each airline's rules and fees for them.

### Does the price cover all passengers?

`price` is per passenger. `total_price_all_passengers` is the total for everyone in the search, bags included.

### Do I need a proxy?

No. The actor needs no proxy and has no proxy setting.

### What currency are prices in?

The currency you set in `currency` (default USD). The `currency` field on each booking option confirms it.

### I searched Business class. Why are there fewer results?

Only flights with a booking option in the requested cabin are returned, and only those booking options are listed. Check `cabin_class` on each booking option.

### Why do I get fewer results than `maxResults`?

Momondo may have fewer flights for that route and date. Bag pricing returns only fares with a known bag cost, and `cabinClass` or `maxStops` can leave fewer matches. If Momondo has no flights, or none match your filters, the run still succeeds and its status message says so.

### What timezone are the times in?

Departure and arrival times are local to each airport, without a UTC offset. `scraped_at` is UTC.

## 💳 Pricing

You pay $2 per 1,000 flight results returned. `maxResults` caps each run at 1–500 results (default 50), and flexible mode searches 2N+1 dates in one run. New Apify accounts get free trial credits, so click **Try for free** to start.

## 🛠️ Troubleshooting

**0 results, with a message about filters.** Momondo found flights, but none matched your `cabinClass` or `maxStops`. Relax those filters.

**0 results, with a message that Momondo found no flights.** There are no fares for that route and date, or none with a published bag price when bags are selected. Try another date or fewer bags.

**"Departure date ... is in the past".** Use today or a later date for the departure, the return and every multi-city leg.

**"Momondo refused this search" or "The search could not be completed".** Momondo occasionally turns a search away. Wait a few minutes and run it again.

**Invalid airports.** `origin` and `destination` must be 3-letter IATA airport codes (for example `LHR`), not city names.

## Disclaimer

This actor is not affiliated with, endorsed by, or sponsored by Momondo. Momondo is a trademark of its owner.
