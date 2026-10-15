"""Scrapy item pipelines module.

For detailed information on creating and utilizing item pipelines, refer to the official
documentation: http://doc.scrapy.org/en/latest/topics/item-pipeline.html
"""
# ruff: noqa: ARG002, D102

from __future__ import annotations

from typing import TYPE_CHECKING

from scrapy.exceptions import DropItem

if TYPE_CHECKING:
    from scrapy.crawler import Crawler


class NullStripPipeline:
    """Remove None values from items before they reach the Apify dataset push pipeline.

    Apify's dataset schema validation rejects null values for fields typed as
    integer/number/string/array — but the schema can't use union types like
    ["integer", "null"] because Apify rejects those too. Stripping None fields
    makes them absent, which passes schema validation for optional fields.
    """

    def process_item(self, item):
        null_keys = [k for k, v in item.items() if v is None]
        for key in null_keys:
            del item[key]
        return item


class LimitItemsNumberPipeline:
    """Enforce a hard item count limit via CLOSESPIDER_ITEMCOUNT."""

    def __init__(self, items_limit: int | None = None):
        self.items_scraped_count = 0
        self.items_limit = items_limit

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> LimitItemsNumberPipeline:
        return cls(crawler.settings.getint('CLOSESPIDER_ITEMCOUNT') or None)

    def process_item(self, item):
        self.items_scraped_count += 1

        items_limit = self.items_limit
        if items_limit and self.items_scraped_count > items_limit:
            raise DropItem(f'Strict limit reached: Exceeded {items_limit} items.')

        return item
