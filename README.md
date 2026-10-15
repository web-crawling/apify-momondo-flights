# momondo-flights — Developer Documentation

Scrapy-based Apify actor that scrapes flight search results from Momondo.com. For end-user documentation see [`.actor/README.md`](.actor/README.md) or the [Apify Store page](https://apify.com/extractify-labs/momondo-flights).

## Project structure

```
actors/momondo-flights/
├── .actor/                    # Apify platform config (input/dataset/output schemas, changelog, Store README)
├── src/
│   ├── __main__.py            # Entry point — installs the asyncio reactor, calls run_scrapy_actor()
│   ├── main.py                # Reads + validates input, runs the spider, fails the run (exit 1) on errors
│   ├── browser.py             # BrowserSession — headful Chromium page that owns the Momondo session
│   ├── middlewares.py         # BrowserTransportMiddleware — routes every spider request through that page
│   ├── settings.py            # Scrapy settings (browser middleware, sequential requests)
│   ├── items.py               # FlightItem
│   ├── itemloaders.py         # FlightItemLoader
│   ├── pipelines.py           # NullStripPipeline + LimitItemsNumberPipeline
│   └── spiders/momondo.py     # MomondoSpider (see Request flow)
├── tests/                     # Offline unit tests (fixtures/poll_response.json); `live` tests are legacy
├── Dockerfile                 # FROM apify/actor-python-playwright:3.13, runs under xvfb-run
└── requirements.txt           # Pinned: apify[scrapy], scrapy, playwright (must match the base image)
```

## Why a browser

Requests go through a headful Chromium page (on a virtual display on Apify); the poll API is
called with `fetch()` inside that page.

## Running locally

Playwright cannot start a browser under the selector event loop Scrapy uses on **Windows**
(`NotImplementedError`), so full runs need Linux (Apify, WSL or Docker). Unit tests run anywhere:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest   # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest tests -m "not live"
```

For an end-to-end check, push a copy under a private staging name and run it on Apify.

## Request flow

1. **Search page** — the spider's bootstrap request (`meta['browser'] = 'navigate'`) opens
   `/flight-search/{origin}-{destination}/{date}/` in a fresh browser context. The session then:
   waits for the session token, selects the currency, and waits for the page's own first poll.
2. **Poll POST(s)** — `POST /i/api/search/dynamic/flights/poll`, sent with in-page `fetch()`:
   - `legs[].cabinClass` carries the cabin (`economy`, `premium-economy`, `business`, `first`).
   - `filterParams.fs` carries the bag selection (`cfc=1;bfc=N`) — the site's own fee filter.
   - Passengers: `ADT`, `CHD`, `INL` (Momondo rejects `CNN` / `INF`).
   - `flexible` fans out 2N+1 searches; `maxResults` is split evenly across the dates.
3. **Search phases** — page 1 is re-polled every 2 s (max 15) until `status == "complete"` before
   any result is emitted; then pagination follows `filteredCount` / `pageSize`.
4. **Refusals** — a 401 or 403 on a poll re-opens the search page in a fresh context
   (max 2 per run), then the run fails.

## Filtering

Cabin is sent to Momondo per leg, and `parse_result()` still keeps only results with a matching
cabin (`_cabin_matches`; note Momondo's "Prem Economy" label). `maxStops` is client-side. When
fares exist but none pass these filters, the run succeeds with 0 items and a status message.

## Failing a run

Under Scrapy the SDK never calls `sys.exit()`, and `run_scrapy_actor()` drives `main()` through
Twisted's `react()`, which exits with the code of a `SystemExit` raised by the coroutine. `main()`
therefore raises `SystemExit(EXIT_CODE)` after `_fail()` — `Actor.fail()` alone exits 0 (SUCCEEDED).

## Adding a new output field

1. Add the field to `src/items.py` (`FlightItem`).
2. Extract it in `src/spiders/momondo.py` → `parse_result()`.
3. Declare it in `.actor/dataset_schema.json` (a test enforces this).
4. Add a unit test.

## Deployment

Apify builds the actor from this repository (GIT_REPO source). Push to `main`, then trigger a
build. Do not use `apify push` for production.
