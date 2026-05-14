"""Item loaders module.

Defines FlightItemLoader for FlightItem.

Output processor rules:
  - `legs` and `booking_options` use Identity() — these fields are pre-assembled
    as Python lists in the spider before being passed to the loader. Identity()
    passes the list through unchanged as a single value.
  - All other fields use TakeFirst() (the default) — they are scalar values
    (string, integer, dict). Using Identity() on a scalar would wrap it in a list.

Input processor:
  - Default is MapCompose(str.strip) for string cleaning.
  - Fields that are dicts or ints bypass the default input processor via
    a per-field _in override that uses MapCompose() with no transforms (passthrough).
"""

from __future__ import annotations

from itemloaders import ItemLoader
from itemloaders.processors import Identity, MapCompose, TakeFirst


def _passthrough(value):
    """Return value unchanged. Used as input processor for non-string fields."""
    return value


class FlightItemLoader(ItemLoader):
    # Default: strip strings on input, take first value on output.
    default_input_processor = MapCompose(str.strip)
    default_output_processor = TakeFirst()

    # --- legs: pre-assembled list of dicts ---
    # Input: pass the list through unchanged (don't map over it).
    legs_in = MapCompose(_passthrough)
    legs_out = Identity()

    # --- booking_options: pre-assembled list of dicts ---
    booking_options_in = MapCompose(_passthrough)
    booking_options_out = Identity()

    # --- dict fields (origin, destination): passthrough input processor ---
    # TakeFirst() output (default) is correct — these are scalar dicts, not lists.
    origin_in = MapCompose(_passthrough)
    destination_in = MapCompose(_passthrough)

    # --- integer fields: passthrough input processor ---
    duration_minutes_in = MapCompose(_passthrough)
    stops_in = MapCompose(_passthrough)

    # --- string fields with no strip needed (IDs, ISO datetimes) ---
    result_id_in = MapCompose(str.strip)
    trip_id_in = MapCompose(str.strip)
    departure_time_in = MapCompose(str.strip)
    arrival_time_in = MapCompose(str.strip)
    scraped_at_in = MapCompose(str.strip)
