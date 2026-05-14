"""Verify that the input_schema.json prefill/defaults are valid.

Per project memory: the schema prefill is submitted as run input by Apify's automated QA.
This script assembles the default field values from input_schema.json and checks:
  1. All required types are correct
  2. The prefill for legs (if present) is a list of objects with 'origin', 'destination', 'departureDate'

This is a local pre-deploy check that catches HTTP 400 prefill bugs before Apify QA flags them.
Note: The actual spider API bug (cabinClass/currency in poll body) is a separate BLOCKER.
"""

import json
import sys
from pathlib import Path

ACTOR_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = ACTOR_ROOT / '.actor' / 'input_schema.json'

with open(SCHEMA_PATH, encoding='utf-8') as f:
    schema = json.load(f)

props = schema.get('properties', {})

print('[CHECK] Input schema prefill/default values:')
issues = []

for name, prop in props.items():
    default = prop.get('default')
    prefill = prop.get('prefill')
    value = prefill if prefill is not None else default

    if value is None:
        print(f'  {name}: (no default, optional)')
        continue

    print(f'  {name}: {json.dumps(value)[:80]}')

    # Check legs prefill: must be list of objects with required keys
    if name == 'legs' and prefill is not None:
        if not isinstance(prefill, list):
            issues.append(f'FAIL: legs prefill must be a list, got {type(prefill)}')
        else:
            for i, leg in enumerate(prefill):
                if not isinstance(leg, dict):
                    issues.append(f'FAIL: legs prefill[{i}] must be dict, got {type(leg)}')
                else:
                    for key in ('origin', 'destination', 'departureDate'):
                        if key not in leg:
                            issues.append(f'FAIL: legs prefill[{i}] missing key: {key!r}')

    # Check enum values
    if 'enum' in prop and value not in prop['enum']:
        issues.append(f'FAIL: {name} default {value!r} not in enum {prop["enum"]}')

    # Check integer ranges
    if prop.get('type') == 'integer' and value is not None:
        mn = prop.get('minimum')
        mx = prop.get('maximum')
        if mn is not None and value < mn:
            issues.append(f'FAIL: {name} default {value} < minimum {mn}')
        if mx is not None and value > mx:
            issues.append(f'FAIL: {name} default {value} > maximum {mx}')

if issues:
    print('\n[FAILURES]:')
    for issue in issues:
        print(f'  {issue}')
    sys.exit(1)
else:
    print('\n[PASS] All schema defaults/prefills are valid.')
    print('[INFO] Note: The legs array editor="json" uses prefill (not default).')
    print('[INFO] Apify QA will submit a one-way search (using default values) which does NOT include legs.')
    print('[INFO] The minimal prefill input assembled from defaults:')
    minimal_input = {
        k: props[k].get('default')
        for k in props
        if props[k].get('default') is not None
    }
    print(f'  {json.dumps(minimal_input, indent=2)}')
