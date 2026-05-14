"""Scrapy settings module for the momondo-flights actor.

For more comprehensive details on Scrapy settings, refer to the official documentation:
http://doc.scrapy.org/en/latest/topics/settings.html
"""

BOT_NAME = 'momondo-flights'

LOG_LEVEL = 'INFO'

NEWSPIDER_MODULE = 'src.spiders'
SPIDER_MODULES = ['src.spiders']
ROBOTSTXT_OBEY = False

TELNETCONSOLE_ENABLED = False

# Do not change the Twisted reactor unless you really know what you are doing.
TWISTED_REACTOR = 'twisted.internet.asyncioreactor.AsyncioSelectorReactor'

# Session cookies are the primary auth mechanism — must be enabled.
COOKIES_ENABLED = True

# Brotli decompression: requires the `brotli` package (in requirements.txt).
# Scrapy auto-detects and uses it via Accept-Encoding header.
DEFAULT_REQUEST_HEADERS = {
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept': 'application/json',
    'x-requested-with': 'XMLHttpRequest',
}

# Browser-like User-Agent to avoid trivial UA-based blocks.
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)

ITEM_PIPELINES = {
    'src.pipelines.NullStripPipeline': 50,
    'src.pipelines.LimitItemsNumberPipeline': 100,
}

SPIDER_MIDDLEWARES = {}
DOWNLOADER_MIDDLEWARES = {}
