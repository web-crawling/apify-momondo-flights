# Momondo Flights Actor — Project Knowledge

## Overview

This actor scrapes flight search results from Momondo.com, a Kayak-owned metasearch engine. Supports one-way, round-trip, multi-city, and flexible date-range searches. Returns structured JSON with per-provider pricing, airline details, and booking URLs.

**Actor slug:** `momondo-flights` (account: `extractify-labs`)  
**Repository:** https://github.com/web-crawling/apify-momondo-flights  
**Issue tracker:** https://github.com/orgs/web-crawling/projects/1 (use label `momondo-flights`)

---

## Endpoint and Authentication Chain

### Bootstrap Flow

1. **GET `/flight-search/{origin}-{destination}/{date}/`** (HTML page)
   - Extracts CSRF token from HTML: `window.R9.formToken = '<value>';` (regex: `_FORM_TOKEN_RE`)
   - **Token is session-bound** — tied to the Apache + mst_* cookies returned by this GET
   - Token does NOT rotate between poll requests; single token reused for all pages within a session
   - On HTTP 401: re-run this GET once to refresh session (flag `_session_refreshed` prevents loops)
   - Optionally send `Cookie: currency=<code>; kyk_curr=<code>` to influence pricing currency (best-effort; USD always reliable)

### Poll Request Flow

2. **POST `/i/api/search/dynamic/flights/poll`** (JSON API)
   - Required headers: `Content-Type: application/json`, `x-csrf: <token>`, `x-requested-with: XMLHttpRequest`, `User-Agent: <browser string>`
   - Required cookies: All cookies from bootstrap GET (Scrapy's CookieMiddleware handles automatically)
   - Request body structure (all trip types use same endpoint):
     ```json
     {
       "filterParams": {},
       "userSearchParams": {
         "legs": [...],
         "searchId": "<generated or constant>",
         "passengers": ["ADT", ...],
         "passengerDetails": [{"ptc": "ADT"}, ...],
         "sortMode": "bestflight_a"
       },
       "searchMetaData": {"pageNumber": 1, "searchTypes": []}
     }
     ```
   - **searchId**: Server-issued per session. The **first poll request per session OMITS `searchId`** from `userSearchParams`. The response includes a fresh `searchId` (e.g., `sgFiCFS7bb`), which is captured and reused on all subsequent polls in that session. Each flexible-date fan-out branch gets its own searchId (do not share across different `legs` payloads).
   - **Pagination**: Response includes `filteredCount` and `pageSize`. Repeat with `pageNumber: 2, 3, ...` while `(pageNumber - 1) * pageSize < filteredCount`. If page 1 returns 0 results, re-poll page 1 with the captured `searchId` regardless of `filteredCount` value (long-poll retry, capped at 5 attempts with exponential backoff 1s/2s/3s/5s/8s). On Apify datacenter IPs Momondo can return `filteredCount=0` AND `results=[]` on the first poll even when the search is alive server-side — the retry is what materialises results. After exhausting retries with no results, set `type(self).crawl_failed = True` so `main.py` calls `Actor.fail()`.

---

## API Discoveries & Corrections to Efficiency Research

### CSRF Token Source (Correction)

The efficiency research (`02-efficiency.md`) **INCORRECTLY** stated that the CSRF token is returned in `Set-Cookie` headers on the first poll response with an empty/placeholder token in the request. **ACTUAL BEHAVIOR:**

- The CSRF token (`formToken`) is embedded in the **flight-search HTML page** itself: `window.R9.formToken = '<value>';`
- Poll requests with empty, placeholder, or random `x-csrf` header values return HTTP 401
- The token is **session-bound** (tied to the Apache + session cookies from the same bootstrap GET)
- Token does NOT rotate; same token is reused across all poll requests in a session
- Bootstrap must GET the **flight-search page** (not just homepage `/`), not a bare homepage GET

### Momondo API Validation Rejections (Critical)

The poll API **REJECTS** the following fields with HTTP 400 `VALIDATION_ERROR: Unrecognized field`:

1. **`userSearchParams.cabinClass`** — Any cabin class parameter in the request body is rejected
   - **Workaround**: Apply client-side filtering in `parse_result()` via `_cabin_matches()` helper
   - For ECONOMY (default), no filtering applied (Momondo returns economy by default)
   - For non-ECONOMY, results are dropped if no booking option matches the requested class
   
2. **`userSearchParams.currency`** — Currency parameter in the request body is rejected
   - **Workaround**: Attempt via `Cookie` header on bootstrap GET (`currency=<code>; kyk_curr=<code>`)
   - For USD (default), prices reliably returned in USD
   - For non-USD, Momondo may ignore the preference and return USD regardless
   - Document as "best-effort; actual currency in each booking option reflects what Momondo returned"

3. **`filterParams.stops`** — Stop count filter in request body is rejected
   - **Workaround**: Apply client-side filtering in `parse_result()`
   - Compute `total_stops = sum(len(segments) - 1 for leg in legs)`
   - Drop results where `total_stops > max_stops`

### Response Structure Differences

The architecture document described one structure; the actual response has subtle differences:

1. **`result["legs"]` is NOT a list of IDs** — it's a list of `{"id": str, "segments": [{"id": str}]}` dicts (each contains segment IDs, not the full segment objects)
2. **`data["airports"]` is top-level** (NOT under `filterData.airports`) — keyed by IATA code → `{"displayName", "fullDisplayName", "cityCode", "cityName"}`
3. **Price path**: `bookingOptions[i]["displayPrice"]["price"]` (NOT `bookingOptions[i]["price"]`)
4. **Booking URL**: `bookingOptions[i]["bookingUrl"]` is a dict `{"url": "/...", "urlType": "relative"}` — relative URLs must be prefixed with `https://www.momondo.com`
5. **Cabin class path**: `bookingOptions[i]["legFarings"][0]["segmentFarings"][0]["cabinDisplay"]`

---

## Search Mode Implementations

### One-Way

```json
{
  "legs": [
    {
      "origin": {"airports": ["JFK"], "locationType": "airports"},
      "destination": {"airports": ["LHR"], "locationType": "airports"},
      "date": "2026-08-01",
      "flex": "exact"
    }
  ]
}
```

### Round-Trip

Two legs: outbound (origin → destination, departureDate) and return (destination → origin, returnDate).

**IMPORTANT NOTE**: Momondo's poll API returns **one result entry per outbound leg**, even for round-trip queries. The return leg is not bundled in the same result object. This matches Momondo's own website behavior.

### Multi-City

N legs from actor input `legs` array, each `{origin, destination, date, flex: "exact"}`.

### Flexible (Date Range)

Fan out: for `departureDateFlexDays: 3`, generate 7 requests (2026-07-29 through 2026-08-04).

**IMPORTANT DISCOVERY**: The Momondo API's `flex: "range"` parameter is **non-functional**. Live browser testing shows that flexible-date URL patterns (e.g., `/2026-08-01~3/`) still generate poll requests with `flex: "exact"`. The workaround is **client-side fan-out**: generate one exact-date poll per date in the range.

---

## Open Issues & Known Limitations

### Issue #2: items_yielded Counter (FIXED)

**Fixed in 08-crawling.md / Fix Log section:** The counter now increments only on actual yield, not on API fetch.

### Issue #3: Scrapy Pipeline Deprecation Warning

**Status**: OPEN (non-blocking, documented in GitHub)

Scrapy 2.15 warns that pipeline `process_item(item, spider)` signature is deprecated. Workaround: use `from_crawler(cls, crawler)` pattern instead. Low priority; does not block functionality.

### Issue #4: Non-USD Currency Handling

**Status**: DOCUMENTED LIMITATION

Currency cookies are sent but may not be honored by Momondo. Only USD (default) is reliably returned. Non-USD currencies are best-effort. Document in README: "Currency parameter is best-effort; Momondo may return prices in USD regardless."

### Issue #6: Sponsored Results in Poll Response

**Status**: EXPECTED API BEHAVIOR

Momondo injects sponsored/promoted flight results at the top of the `results[]` array, often from alternative airports (e.g., OTP, BBU for Bucharest when JFK was requested). This is not a spider bug; it's Momondo's ranking algorithm. When `maxResults` is low, users may see sponsored results instead of the requested origin. Document in README if needed.

---

## Helpers & Reusable Code

- **`Jmes` class** (`src/helpers/jmes.py`) — Available but NOT used in this actor. Momondo response is clean JSON; direct dict access is preferred.
- **`re.first()` helper** (`src/helpers/re.py`) — Available for regex extraction if needed in future features.

---

## Client-Side Filter Implementation Notes

### Cabin Class Filtering (`_cabin_matches()`)

Checks `cabin_display` (lowercased) string from booking options:

- **ECONOMY**: contains `"economy"` AND does NOT contain `"premium"` (excludes Premium Economy)
- **PREMIUM_ECONOMY**: contains `"premium"`
- **BUSINESS**: contains `"business"`
- **FIRST**: contains `"first"`

For `cabinClass == ECONOMY` (default), **NO filtering** is applied in `parse_result()` — Momondo returns economy by default and filtering would over-drop results.

### Stop Count Filtering

`total_stops = sum(len(leg["segments"]) - 1 for leg in legs_list)` per result. Drop if `total_stops > max_stops`.

---

## Post-Deploy Checklist

After successful GitHub merge and Apify CI deployment:

1. **Set `exampleRunInput`** via `PUT /v2/acts/{id}` with a valid one-way input:
   ```json
   {"tripType": "one-way", "origin": "JFK", "destination": "LHR", "departureDate": "2026-08-01"}
   ```
   This is separate from schema `prefill` and is REQUIRED. Without it, Apify's automated QA fails the actor with "Under maintenance" status.

2. If the actor was previously flagged "Under maintenance" due to the cabinClass/currency/stops HTTP 400 bugs (now fixed), clear the `notice` field:
   ```json
   {"notice": null}
   ```
   The field does not auto-clear; it must be manually cleared via the API.

3. Monitor the first QA run. If prefill validation or example run input fails, diagnose via the run's error logs and re-run the deploy.

---

## Testing Notes

- **Live tests** hit Momondo.com; mark with `@pytest.mark.live`
- **Unit tests** use `tests/fixtures/poll_response.json` (3 results, live-captured)
- **E2E tests** run the full actor via `python -m src` with local storage

All tests pass as of 2026-05-14. MINOR #3 (Scrapy deprecation warning) is a future cleanup item, not a blocker.

---

## Version History

- **v1.0.0 (2026-05-14)** — Initial release. Supports one-way, round-trip, multi-city, and flexible date-range searches. Client-side cabin class and stop count filtering. No proxy required. Direct requests succeed on Momondo. Known limitation: currency parameter best-effort (USD reliable, others may fall back to USD).
