"""Scrapy items module.

Defines FlightItem — the single top-level item yielded by the momondo spider.

Nested structures (legs, segments, booking options) are plain Python dicts,
not Scrapy items, to avoid serialisation issues with Apify's dataset pipeline.
Only FlightItem flows through the Scrapy item pipeline.
"""

import scrapy


class FlightItem(scrapy.Item):
    # ---------- identifiers ----------
    result_id = scrapy.Field()           # str: Momondo result ID (unique per offer)
    trip_id = scrapy.Field()             # str: Momondo trip grouping ID

    # ---------- top-level route ----------
    origin = scrapy.Field()              # dict: {"code": "JFK", "name": "John F. Kennedy International"}
    destination = scrapy.Field()         # dict: {"code": "LHR", "name": "London Heathrow"}
    departure_time = scrapy.Field()      # str: ISO 8601 local datetime of first leg departure
    arrival_time = scrapy.Field()        # str: ISO 8601 local datetime of last leg arrival

    # ---------- summary ----------
    duration_minutes = scrapy.Field()   # int: total trip duration in minutes (all legs combined)
    stops = scrapy.Field()              # int: total stop count (0 = nonstop)

    # ---------- nested structures (plain Python lists of dicts) ----------
    legs = scrapy.Field()               # list[dict]: per-leg details; see architecture for shape
    booking_options = scrapy.Field()    # list[dict]: booking partner options with prices + URLs

    # ---------- metadata ----------
    scraped_at = scrapy.Field()         # str: UTC ISO 8601 collection timestamp
