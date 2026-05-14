# apify-momondo-flights — Developer Documentation

Scrapy-based Apify actor that scrapes flight search results from Momondo.com. For end-user documentation see [`.actor/README.md`](.actor/README.md) or the [Apify Store page](https://apify.com/extractify-labs/momondo-flights).

## Project structure

```
apify-momondo-flights/
├── .actor/                    # Apify platform config (input schema, dataset schema, changelog, marketplace README)
├── src/
│   ├── __init__.py
│   ├── __main__.py            # Entry point — installs Twisted reactor, calls run_scrapy_actor()
│   ├── main.py                # Actor coroutine — reads input, validates params, wires spider, checks auth_failed
│   ├── settings.py            # Scrapy settings (COOKIES_ENABLED, USER_AGENT, DEFAULT_REQUEST_HEADERS, ITEM_PIPELINES)
│   ├── items.py               # FlightItem (11 top-level fields)
│   ├── itemloaders.py         # FlightItemLoader — Identity() on legs/booking_options, TakeFirst() elsewhere
│   ├── pipelines.py           # NullStripPipeline + LimitItemsNumberPipeline
│   ├── spiders/
│   │   └── momondo.py         # MomondoSpider — main spider (see Request Flow below)
│   └── helpers/
│       ├── jmes.py            # Jmes — robust JSON path helper
│       └── re.py              # re.first() — regex extraction helper
├── tests/
│   ├── fixtures/
│   │   └── poll_response.json # Live-captured poll response (3 results, used by test_parser.py)
│   ├── test_crawler.py        # 11 tests (8 unit, 3 live)
│   ├── test_parser.py         # 35 unit tests against fixture
│   └── test_e2e.py            # 34 tests (unit + live + e2e)
├── scripts/                   # One-off utility scripts (not part of actor runtime)
├── Dockerfile                 # FROM apify/actor-python:3.14
├── pyproject.toml             # Project metadata + pytest marks
└── requirements.txt           # apify[scrapy]<4, scrapy<3, brotli
```

## Running locally

```bash
# Create and activate venv (once per machine)
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt

# Create the input file
mkdir -p storage/key_value_stores/default
```

Create `storage/key_value_stores/default/INPUT.json`:

```json
{
  "tripType": "one-way",
  "origin": "JFK",
  "destination": "LHR",
  "departureDate": "2026-08-01",
  "adults": 1,
  "maxResults": 10
}
```

Run the actor:

```bash
python -m src
```

Output is written to `storage/datasets/default/`.

## Running tests

```bash
# Unit tests only (no network)
pytest tests/ -v -m "not live and not e2e"

# All tests including live requests (requires network access to Momondo)
pytest tests/ -v
```

Test marks: `live` (hits Momondo network), `e2e` (full actor run). Unit tests run offline using `tests/fixtures/poll_response.json`.

## Request flow

The spider (``src/spiders/momondo.py``) follows this request sequence:

1. **Bootstrap GET** — `GET https://www.momondo.com/flight-search/{origin}-{destination}/{date}/`
   Extracts the CSRF token from `window.R9.formToken = '<value>';` in the HTML.
   Also sets `currency` preference via Cookie header on this request.

2. **Poll POST(s)** — `POST https://www.momondo.com/i/api/search/dynamic/flights/poll`
   JSON body contains `legs`, `passengers`, and `userSearchParams`. The CSRF token is sent as `x-csrf` header.

   - `one-way` / `round-trip` / `multi-city`: one poll request (page 1), with automatic pagination.
   - `flexible`: fans out `(2 * departureDateFlexDays) + 1` poll requests, one per date in the ±N window. Each poll uses `flex: "exact"` — the API's `flex: "range"` mode is non-functional (confirmed in `02b-flex-api.md`).

3. **Pagination** — poll responses include `filteredCount` and `pageSize`. The spider fetches subsequent pages while `items_yielded < maxResults`.

4. **Auth refresh** — on HTTP 401, the spider re-fetches the bootstrap page once to get a fresh session token and retries the failed poll. If it fails again, `auth_failed = True` is set and the spider closes. `main.py` calls `Actor.fail()` in that case.

## How the four modes are wired

All modes share the same `parse_poll` → `parse_result` path. The difference is in `_build_poll_requests()` in `MomondoSpider`:

- `one-way`: builds one leg `[{origin, destination, date}]`
- `round-trip`: builds two legs `[outbound, return]`
- `multi-city`: passes `legs_input` directly (N legs from actor input)
- `flexible`: calls `_build_flexible_poll_requests()`, which iterates over `[date - N, ..., date + N]` and fires one poll per date

## Client-side filtering

The Momondo poll API does not accept `cabinClass` or `maxStops` in the request body (returns HTTP 400). Both filters are applied in `parse_result()`:

- **`cabinClass`**: `_cabin_matches(cabin_display, requested_class)` checks `booking_options[i]["cabin_class"]` string for Economy / Premium Economy / Business / First keywords. Results are dropped if no booking option matches. Default ECONOMY does no filtering (Momondo returns Economy by default).
- **`maxStops`**: `total_stops = sum(len(segments) - 1 for leg in legs)`. Results where `total_stops > max_stops` are dropped.

Because filtering is post-fetch, `maxResults` reflects the number of items actually yielded after filtering — not the number fetched from the API.

## Adding a new output field

1. Add the field to `src/items.py` (`FlightItem`).
2. Add extraction in `src/spiders/momondo.py` → `parse_result()`.
3. Add the field to `.actor/dataset_schema.json` (all `items.py` fields must be declared here).
4. Add a unit test to `tests/test_parser.py` using the fixture.

## Deployment

Deployment is triggered automatically when a PR is merged to `main` via GitHub → Apify CI. Do not use `apify push`.

After deploying, run the post-deploy checklist:
- Set `exampleRunInput` via `PUT /v2/acts/{id}` (separate from schema `prefill`; Apify QA requires this).
- If the actor was previously flagged "Under maintenance", clear the `notice` field via `PUT /v2/acts/{id}` with `{"notice": null}`.
