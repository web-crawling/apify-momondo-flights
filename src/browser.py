"""Headful-Chromium session that carries every Momondo request.

Momondo (KAYAK) serves a "What is a bot?" wall to plain HTTP clients and to headless
browsers, from datacenter AND residential IPs alike (verified on Apify 2026-10-15). A
headful Chromium (on Apify: under xvfb, see Dockerfile CMD) gets a normal page and a
valid session. The session is bound to the browser, so the poll API is called from
inside the page with fetch() rather than by replaying cookies from another client.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

logger = logging.getLogger(__name__)

BOT_WALL_TITLE = 'What is a bot?'
BASE_URL = 'https://www.momondo.com'

# Images, media and fonts are never needed for the session; skipping them keeps the
# per-run bandwidth and CPU down. Scripts must load: the formToken is set by page JS.
_BLOCKED_RESOURCE_TYPES = {'image', 'media', 'font'}

_FETCH_JS = """async ([url, method, headers, body]) => {
  const r = await fetch(url, {method, headers, body: body || undefined, credentials: 'include'});
  return {status: r.status, body: await r.text()};
}"""

_TOKEN_JS = '() => (window.R9 && window.R9.formToken) || ""'

_OWN_POLL_WAIT_SECONDS = 15


@dataclass
class PageResult:
    """Outcome of a navigation or an in-page fetch."""

    status: int
    body: str


class BrowserSession:
    """Lazily-started browser with one page bound to a Momondo session."""

    def __init__(self, currency: str = 'USD', nav_attempts: int = 2) -> None:
        self.currency = currency
        self.nav_attempts = nav_attempts
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()

    def _launch_options(self) -> dict:
        # No proxy: under pay-per-event, proxy traffic on users' runs is billed to the developer,
        # and the headful browser does not need one.
        return {'headless': False, 'args': ['--disable-blink-features=AutomationControlled']}

    async def _new_page(self) -> Page:
        if self._browser is None:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(**self._launch_options())
        if self._context is not None:
            await self._context.close()
        self._context = await self._browser.new_context(locale='en-US', viewport={'width': 1366, 'height': 900})
        await self._context.route(
            '**/*',
            lambda route: route.abort() if route.request.resource_type in _BLOCKED_RESOURCE_TYPES else route.continue_(),
        )
        self._page = await self._context.new_page()
        return self._page

    async def navigate(self, url: str) -> PageResult:
        """Open a search page in a fresh context and wait until the session token exists.

        Returns status 403 when every attempt lands on the bot wall (or never gets a token),
        so the spider's normal HTTP-error handling marks the run as failed.
        """
        async with self._lock:
            title = ''
            for attempt in range(1, self.nav_attempts + 1):
                page = await self._new_page()
                own_poll_done = asyncio.Event()
                page.on('response', lambda r, ev=own_poll_done: ev.set() if '/flights/poll' in r.url else None)
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=60_000)
                    await page.wait_for_function(_TOKEN_JS, timeout=30_000)
                    await self._set_currency(page)
                    # Polling before the page's own first search has run gets a bot captcha
                    # (seen on Apify 2026-10-15); let the page's scripts finish first.
                    try:
                        await asyncio.wait_for(own_poll_done.wait(), timeout=_OWN_POLL_WAIT_SECONDS)
                    except asyncio.TimeoutError:
                        logger.debug('Search page made no poll of its own within %ds.', _OWN_POLL_WAIT_SECONDS)
                    return PageResult(status=200, body=await page.content())
                except Exception as exc:  # noqa: BLE001 - any failure here means "no session"
                    try:
                        title = await page.title()
                    except Exception:  # noqa: BLE001
                        title = ''
                    logger.warning('Momondo did not load the search (attempt %d/%d).', attempt, self.nav_attempts)
                    logger.debug('Page title %r: %s', title, str(exc).splitlines()[0][:160])
            reason = 'bot wall' if title == BOT_WALL_TITLE else 'no session token'
            return PageResult(status=403, body=f'{{"error": "{reason}"}}')

    async def _set_currency(self, page: Page) -> None:
        """Select the display currency the way the site's currency picker does.

        Momondo otherwise prices in the visitor's local currency (GBP from Apify's
        servers, 2026-10-15); the old currency cookies are ignored.
        """
        token = await page.evaluate(_TOKEN_JS)
        result = await page.evaluate(_FETCH_JS, [
            '/i/api/account/currency/v1/set', 'POST',
            {'content-type': 'application/json', 'x-csrf': token, 'x-requested-with': 'XMLHttpRequest'},
            json.dumps({'currencyCode': self.currency}),
        ])
        if int(result['status']) >= 300:
            logger.warning('Could not select currency %s (HTTP %s); prices may be in another currency.',
                           self.currency, result['status'])

    async def fetch(self, url: str, method: str, headers: dict[str, str], body: str | None) -> PageResult:
        """Run a same-origin fetch() inside the session page."""
        if self._page is None:
            return PageResult(status=401, body='{"error": "no browser session"}')
        result = await self._page.evaluate(_FETCH_JS, [url, method, headers, body])
        return PageResult(status=int(result['status']), body=result['body'])

    async def close(self) -> None:
        """Shut the browser down; safe to call more than once."""
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:  # noqa: BLE001
                    pass
        if self._pw is not None:
            await self._pw.stop()
        self._pw = self._browser = self._context = self._page = None
