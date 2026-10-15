"""Apify Actor integration for Scrapy projects.

This module transforms a Scrapy project into an Apify Actor, handling the configuration of logging,
patching Scrapy's logging system, and establishing the required environment to run the Scrapy spider
within the Apify platform.

We recommend you do not modify this file unless you really know what you are doing.
"""

# ruff: noqa: E402
from __future__ import annotations

from scrapy.utils.reactor import install_reactor

# Install Twisted's asyncio reactor before importing any other Twisted or
# Scrapy components.
install_reactor('twisted.internet.asyncioreactor.AsyncioSelectorReactor')

import logging
import os

from apify.scrapy import initialize_logging, run_scrapy_actor

# Import your main Actor coroutine here.
from .main import main

# Ensure the location to the Scrapy settings module is defined.
os.environ['SCRAPY_SETTINGS_MODULE'] = 'src.settings'


if __name__ == '__main__':
    initialize_logging()
    # The SDK announces its browser-sandbox default on every platform run; the browser here
    # is launched by Playwright directly, so the warning is noise in the user's log.
    logging.getLogger('apify._configuration').setLevel(logging.ERROR)
    run_scrapy_actor(main())
