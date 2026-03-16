"""Check if competitor URLs are accessible."""
import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import os

os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
logger = logging.getLogger(__name__)


def check_urls_access(urls: list) -> list:
    """Check accessibility of a list of URLs. Returns list of dicts."""
    results = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            for url in urls:
                full_url = url if url.startswith('http') else f'https://{url}'
                status = 'accessible'
                status_code = 0
                error = ''

                try:
                    response = page.goto(full_url, wait_until='domcontentloaded', timeout=10000)
                    if response:
                        status_code = response.status
                        if response.status == 403:
                            status = 'blocked'
                        elif response.status == 404:
                            status = 'not_found'
                        elif response.status >= 500:
                            status = 'error'
                except PlaywrightTimeout:
                    status = 'timeout'
                    error = 'Connection timed out'
                except Exception as e:
                    status = 'error'
                    error = str(e)[:200]

                results.append({
                    'url': full_url,
                    'status': status,
                    'status_code': status_code,
                    'error': error,
                })

            browser.close()
    except Exception as e:
        logger.error(f"Access check failed: {e}")
        for url in urls:
            results.append({
                'url': url,
                'status': 'error',
                'error': str(e)[:200],
            })

    return results
