"""Downloader middleware: send every spider request through the headful browser session.

- meta['browser'] == 'navigate' -> open the URL as a page (bootstrap / session refresh);
  the response body is the page HTML, which carries window.R9.formToken.
- anything else                -> same-origin fetch() inside that page (poll API).
- meta['delay_seconds']        -> non-blocking wait before the request (empty-page long-poll).
"""
# ruff: noqa: ARG002

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from scrapy import signals
from scrapy.http import HtmlResponse, TextResponse

from src.browser import BrowserSession

if TYPE_CHECKING:
    from scrapy import Request, Spider
    from scrapy.crawler import Crawler

# Headers the page's fetch() may set; the browser owns cookies, UA, referer and encoding.
_FORWARDED_HEADERS = ('content-type', 'x-csrf', 'x-requested-with', 'accept')


class BrowserTransportMiddleware:
    """Replaces Scrapy's HTTP download with the browser session."""

    def __init__(self, crawler: Crawler | None = None) -> None:
        self.crawler = crawler
        self.session: BrowserSession | None = None

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> BrowserTransportMiddleware:
        middleware = cls(crawler)
        crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)
        return middleware

    def _session(self) -> BrowserSession:
        if self.session is None:
            spider = self.crawler.spider if self.crawler else None
            self.session = BrowserSession(
                currency=getattr(spider, 'currency', 'USD'),
            )
        return self.session

    async def process_request(self, request: Request) -> TextResponse:
        delay = request.meta.get('delay_seconds')
        if delay:
            await asyncio.sleep(delay)
        session = self._session()

        if request.meta.get('browser') == 'navigate':
            result = await session.navigate(request.url)
            return HtmlResponse(url=request.url, status=result.status, body=result.body, encoding='utf-8', request=request)

        headers = {}
        for name in _FORWARDED_HEADERS:
            value = request.headers.get(name)
            if value is not None:
                headers[name] = value.decode('utf-8')
        body = request.body.decode('utf-8') if request.body else None
        result = await session.fetch(request.url, request.method, headers, body)
        return TextResponse(url=request.url, status=result.status, body=result.body, encoding='utf-8', request=request)

    async def spider_closed(self, spider: Spider) -> None:
        if self.session is not None:
            await self.session.close()
