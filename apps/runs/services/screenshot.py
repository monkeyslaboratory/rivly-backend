"""
Playwright-based screenshot service.
Takes full-page screenshots of competitor websites (desktop + mobile).
"""
import os
import uuid
import logging
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

from apps.runs.models import RunScreenshot

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = Path(os.environ.get('SCREENSHOTS_DIR', 'media/screenshots'))
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

VIEWPORTS = {
    'desktop': {'width': 1440, 'height': 900},
    'mobile': {'width': 390, 'height': 844},
}

# Pages to capture per competitor
DEFAULT_PAGES = [
    {'name': 'homepage', 'path': '/'},
    {'name': 'pricing', 'path': '/pricing'},
]


def screenshot_competitor(run, competitor, device_types=None, pages=None):
    """
    Take screenshots of a competitor's website.
    Returns list of RunScreenshot objects.
    """
    if device_types is None:
        job = run.job
        if job.device_type == 'both':
            device_types = ['desktop', 'mobile']
        else:
            device_types = [job.device_type]

    if pages is None:
        pages = DEFAULT_PAGES

    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for device_type in device_types:
            viewport = VIEWPORTS[device_type]
            context = browser.new_context(
                viewport=viewport,
                user_agent=(
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                    if device_type == 'desktop'
                    else
                    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                    'AppleWebKit/605.1.15 (KHTML, like Gecko) '
                    'Version/17.0 Mobile/15E148 Safari/604.1'
                ),
            )
            page = context.new_page()

            for page_info in pages:
                page_url = competitor.url.rstrip('/') + page_info['path']
                file_id = str(uuid.uuid4())
                s3_key = f"runs/{run.id}/{competitor.id}/{device_type}_{page_info['name']}_{file_id}.png"
                local_path = SCREENSHOTS_DIR / f"{file_id}.png"

                status = 'success'
                error_message = ''
                dom_text = ''
                html_snippet = ''

                try:
                    response = page.goto(page_url, wait_until='networkidle', timeout=30000)

                    if response and response.status >= 400:
                        status = 'not_found' if response.status == 404 else 'error'
                        error_message = f'HTTP {response.status}'
                    else:
                        # Wait for content to settle
                        page.wait_for_timeout(2000)

                        # Take screenshot
                        page.screenshot(path=str(local_path), full_page=True)

                        # Extract text content for AI analysis
                        try:
                            dom_text = page.evaluate('() => document.body.innerText')
                            if len(dom_text) > 50000:
                                dom_text = dom_text[:50000]
                        except Exception:
                            dom_text = ''

                        # Extract HTML snippet
                        try:
                            html_snippet = page.evaluate('() => document.body.innerHTML')
                            if len(html_snippet) > 50000:
                                html_snippet = html_snippet[:50000]
                        except Exception:
                            html_snippet = ''

                except PlaywrightTimeout:
                    status = 'timeout'
                    error_message = f'Timeout loading {page_url}'
                    logger.warning(f'Timeout: {page_url}')
                except Exception as e:
                    status = 'error'
                    error_message = str(e)[:500]
                    logger.error(f'Screenshot error {page_url}: {e}')

                shot = RunScreenshot.objects.create(
                    run=run,
                    competitor=competitor,
                    page_url=page_url,
                    page_name=page_info['name'],
                    device_type=device_type,
                    s3_key=s3_key,
                    viewport_width=viewport['width'],
                    viewport_height=viewport['height'],
                    dom_text=dom_text,
                    html_snippet=html_snippet,
                    status=status,
                    error_message=error_message,
                )
                screenshots.append(shot)

            context.close()
        browser.close()

    return screenshots
