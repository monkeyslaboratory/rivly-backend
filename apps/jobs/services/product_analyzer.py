"""Extract product info from URL using Playwright."""
import os
import logging
from playwright.sync_api import sync_playwright

os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
logger = logging.getLogger(__name__)


def analyze_product_url(url: str) -> dict:
    """Load URL, extract title, description, detect industry."""
    result = {
        'url': url,
        'name': '',
        'description': '',
        'industry': 'SaaS',
        'favicon_url': '',
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=15000)
            page.wait_for_timeout(2000)

            # Extract title
            title = page.title() or ''
            result['name'] = title.split('|')[0].split('-')[0].split('\u2014')[0].strip()
            if not result['name']:
                # Fallback to domain
                from urllib.parse import urlparse
                domain = urlparse(url).hostname or ''
                result['name'] = domain.replace('www.', '').split('.')[0].capitalize()

            # Extract meta description
            desc = page.evaluate('''() => {
                const meta = document.querySelector('meta[name="description"]') ||
                             document.querySelector('meta[property="og:description"]');
                return meta ? meta.getAttribute('content') : '';
            }''')
            result['description'] = (desc or '')[:500]

            # Extract favicon
            favicon = page.evaluate('''() => {
                const link = document.querySelector('link[rel="icon"]') ||
                             document.querySelector('link[rel="shortcut icon"]');
                return link ? link.getAttribute('href') : '';
            }''')
            if favicon:
                if favicon.startswith('//'):
                    favicon = 'https:' + favicon
                elif favicon.startswith('/'):
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    favicon = f"{parsed.scheme}://{parsed.hostname}{favicon}"
                result['favicon_url'] = favicon

            browser.close()
    except Exception as e:
        logger.error(f"Product analysis failed: {e}")
        # Fallback
        from urllib.parse import urlparse
        domain = urlparse(url).hostname or ''
        result['name'] = domain.replace('www.', '').split('.')[0].capitalize()
        result['description'] = f'Product at {domain}'

    return result
