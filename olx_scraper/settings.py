# Scrapy settings for olx_scraper project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html
import os

redundancy = 0
redundancy_streak = 0
saved = 0

BOT_NAME = 'olx_scraper'

SPIDER_MODULES = ['olx_scraper.spiders']
NEWSPIDER_MODULE = 'olx_scraper.spiders'


# Crawl responsibly by identifying yourself (and your website) on the user-agent
#USER_AGENT = 'olx_scraper (+http://www.yourdomain.com)'

# Obey robots.txt rules
ROBOTSTXT_OBEY = False

DOWNLOADER_MIDDLEWARES = {
    'scrapy.downloadermiddlewares.useragent.UserAgentMiddleware': None,
    'scrapy.downloadermiddlewares.retry.RetryMiddleware': None,
    # 'scrapy_fake_useragent.middleware.RandomUserAgentMiddleware': 400,
    # 'scrapy_fake_useragent.middleware.RetryUserAgentMiddleware': 401,
    # 'olx_scraper.middlewares.FakeResponseMiddleware': 543,
}


# FAKEUSERAGENT_PROVIDERS = [
#     'scrapy_fake_useragent.providers.FakeUserAgentProvider',  # this is the first provider we'll try
#     'scrapy_fake_useragent.providers.FakerProvider',  # if FakeUserAgentProvider fails, we'll use faker to generate a user-agent string for us
#     'scrapy_fake_useragent.providers.FixedUserAgentProvider',  # fall back to USER_AGENT value
# ]

# Configure maximum concurrent requests performed by Scrapy (default: 16)
CONCURRENT_REQUESTS = 8

# Configure a delay for requests for the same website (default: 0)
# See https://docs.scrapy.org/en/latest/topics/settings.html#download-delay
# See also autothrottle settings and docs
#DOWNLOAD_DELAY = 3
# The download delay setting will honor only one of:
#CONCURRENT_REQUESTS_PER_DOMAIN = 16
#CONCURRENT_REQUESTS_PER_IP = 16

# Disable cookies (enabled by default)
#COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
#TELNETCONSOLE_ENABLED = False

# Override the default request headers:
#DEFAULT_REQUEST_HEADERS = {
#   'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
#   'Accept-Language': 'en',
#}

# Enable or disable spider middlewares
# See https://docs.scrapy.org/en/latest/topics/spider-middleware.html
#SPIDER_MIDDLEWARES = {
#    'olx_scraper.middlewares.RealestateScraperSpiderMiddleware': 543,
#}

# Enable or disable downloader middlewares
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#DOWNLOADER_MIDDLEWARES = {
#    'olx_scraper.middlewares.RealestateScraperDownloaderMiddleware': 543,
#}

DOWNLOADER_MIDDLEWAWARES = {
    'olx_scraper.middlewares.CloudScraperMiddleware': 543, # Choose a priority
    # Keep other middlewares as needed
}
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# Enable or disable extensions
# See https://docs.scrapy.org/en/latest/topics/extensions.html
#EXTENSIONS = {
#    'scrapy.extensions.telnet.TelnetConsole': None,
#}

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
# ITEM_PIPELINES = {
#    'olx_scraper.pipelines.SaveImoveisSCCatalogPipeline': 200,
#    'olx_scraper.pipelines.DuplicatesImoveisSCCatalogPipeline': 100,
# }

file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
# Create the 'scraped_data' directory if it doesn't exist
os.makedirs(os.path.join(file_path, 'scraped_data'), exist_ok=True)
CONNECTION_STRING = 'sqlite:///' + os.path.join(file_path, os.path.join('scraped_data', 'olx.sqlite'))
# CONNECTION_STRING = 'sqlite:///' + os.path.join(file_path, os.path.join('scraped_data', 'html_imoveis_sc.sqlite'))

# CONNECTION_STRING = 'sqlite:///imoveis_sc.db'

# Enable and configure the AutoThrottle extension (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/autothrottle.html
#AUTOTHROTTLE_ENABLED = True
# The initial download delay
#AUTOTHROTTLE_START_DELAY = 5
# The maximum download delay to be set in case of high latencies
#AUTOTHROTTLE_MAX_DELAY = 60
# The average number of requests Scrapy should be sending in parallel to
# each remote server
#AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# Enable showing throttling stats for every response received:
#AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
#HTTPCACHE_ENABLED = True
#HTTPCACHE_EXPIRATION_SECS = 0
#HTTPCACHE_DIR = 'httpcache'
#HTTPCACHE_IGNORE_HTTP_CODES = []
#HTTPCACHE_STORAGE = 'scrapy.extensions.httpcache.FilesystemCacheStorage'

# DOWNLOAD_HANDLERS = {
#     "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
#     "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
# }

# TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

# SAVE_RAW_HTML = True

# Enable fake scraping mode
# USE_FAKE_SCRAPING = True















DOWNLOADER_MIDDLEWARES = {
    'olx_scraper.middlewares.CloudScraperMiddleware': 543,
    # Ensure other default middlewares are not conflicting.
    # Scrapy's default HttpProxyMiddleware should be at a higher number (e.g., 750)
    # if you are using proxies.
}