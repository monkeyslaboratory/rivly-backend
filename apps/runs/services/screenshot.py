"""
Smart screenshot service with automatic page discovery.
Crawls competitor sites, discovers key pages, captures everything.
"""
import os
import uuid
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse
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

# Keywords to identify important pages from nav links
PAGE_PATTERNS = {
    'pricing': ['pricing', 'plans', 'price', 'tariff', 'subscription'],
    'features': ['features', 'product', 'solutions', 'capabilities', 'platform'],
    'about': ['about', 'company', 'team', 'story'],
    'contact': ['contact', 'support', 'help', 'demo', 'get-started', 'book'],
    'blog': ['blog', 'news', 'articles', 'resources', 'insights'],
    'login': ['login', 'signin', 'sign-in', 'account', 'dashboard', 'app'],
    'signup': ['signup', 'sign-up', 'register', 'get-started', 'start', 'trial', 'free'],
    'careers': ['careers', 'jobs', 'hiring'],
    'docs': ['docs', 'documentation', 'api', 'developers', 'guides'],
    'integrations': ['integrations', 'apps', 'marketplace', 'plugins'],
    'customers': ['customers', 'case-studies', 'testimonials', 'reviews'],
    'security': ['security', 'privacy', 'compliance', 'trust'],
    'changelog': ['changelog', 'releases', 'updates', 'whats-new'],
}

MAX_PAGES = 8  # Max pages to screenshot per competitor


def discover_pages(page, base_url: str) -> list:
    """Extract nav links and discover important pages automatically."""
    try:
        links = page.evaluate('''(baseUrl) => {
            const results = [];
            const seen = new Set();

            // Get links from nav, header, footer
            const selectors = ['nav a', 'header a', '[role="navigation"] a', 'footer a'];
            for (const sel of selectors) {
                document.querySelectorAll(sel).forEach(a => {
                    const href = a.href;
                    const text = (a.textContent || '').trim().toLowerCase();
                    if (href && !seen.has(href) && href.startsWith(baseUrl) && text.length < 50) {
                        seen.add(href);
                        results.push({ url: href, text: text });
                    }
                });
            }

            // Also get prominent CTA links
            document.querySelectorAll('a[class*="cta"], a[class*="btn"], a[class*="button"], .hero a, main a').forEach(a => {
                const href = a.href;
                const text = (a.textContent || '').trim().toLowerCase();
                if (href && !seen.has(href) && href.startsWith(baseUrl) && text.length < 50) {
                    seen.add(href);
                    results.push({ url: href, text: text, isCta: true });
                }
            });

            return results;
        }''', base_url)
    except Exception as e:
        logger.warning(f"Link discovery failed: {e}")
        return []

    # Classify discovered links
    discovered = []
    seen_categories = set()

    for link in links:
        url = link['url']
        text = link.get('text', '')
        path = urlparse(url).path.lower().strip('/')

        # Match against patterns
        for category, keywords in PAGE_PATTERNS.items():
            if category in seen_categories:
                continue
            if any(kw in path or kw in text for kw in keywords):
                discovered.append({'name': category, 'url': url})
                seen_categories.add(category)
                break

    return discovered[:MAX_PAGES - 1]  # Leave room for homepage


def screenshot_competitor(run, competitor, device_types=None, pages=None):
    """
    Smart screenshot: auto-discovers key pages, captures homepage + discovered pages.
    """
    job = run.job
    if device_types is None:
        if job.device_type == 'both':
            device_types = ['desktop', 'mobile']
        else:
            device_types = [job.device_type]

    screenshots = []
    base_url = competitor.url.rstrip('/')
    parsed = urlparse(base_url if base_url.startswith('http') else f'https://{base_url}')
    origin = f"{parsed.scheme}://{parsed.hostname}"

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
                    if device_type == 'desktop' else
                    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                    'AppleWebKit/605.1.15 (KHTML, like Gecko) '
                    'Version/17.0 Mobile/15E148 Safari/604.1'
                ),
            )
            pw_page = context.new_page()

            # --- Step 1: Homepage ---
            pages_to_capture = [{'name': 'homepage', 'url': base_url}]

            try:
                pw_page.goto(base_url, wait_until='networkidle', timeout=30000)
                pw_page.wait_for_timeout(2000)

                # Auto-discover pages from homepage navigation
                discovered = discover_pages(pw_page, origin)
                pages_to_capture.extend(discovered)
                logger.info(f"Discovered {len(discovered)} pages for {competitor.name}: {[p['name'] for p in discovered]}")
            except Exception as e:
                logger.warning(f"Homepage load/discovery failed for {competitor.name}: {e}")

            # --- Step 2: Capture all pages ---
            for page_info in pages_to_capture:
                page_url = page_info['url']
                page_name = page_info['name']
                file_id = str(uuid.uuid4())
                s3_key = f"runs/{run.id}/{competitor.id}/{device_type}_{page_name}_{file_id}.png"
                local_path = SCREENSHOTS_DIR / f"{file_id}.png"

                status = 'success'
                error_message = ''
                dom_text = ''
                html_snippet = ''

                try:
                    if page_url != base_url:  # Don't re-navigate for homepage
                        pw_page.goto(page_url, wait_until='networkidle', timeout=20000)
                        pw_page.wait_for_timeout(1500)

                    pw_page.screenshot(path=str(local_path), full_page=True)

                    try:
                        dom_text = pw_page.evaluate('() => document.body.innerText') or ''
                        if len(dom_text) > 50000:
                            dom_text = dom_text[:50000]
                    except Exception:
                        pass

                    try:
                        html_snippet = pw_page.evaluate('() => document.body.innerHTML') or ''
                        if len(html_snippet) > 50000:
                            html_snippet = html_snippet[:50000]
                    except Exception:
                        pass

                except PlaywrightTimeout:
                    status = 'timeout'
                    error_message = f'Timeout loading {page_url}'
                except Exception as e:
                    status = 'error'
                    error_message = str(e)[:500]

                shot = RunScreenshot.objects.create(
                    run=run,
                    competitor=competitor,
                    page_url=page_url,
                    page_name=page_name,
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
