"""
Deep site crawler + screenshot service.
Automatically discovers and captures ALL important pages of a competitor site.
Discovery priority: sitemap.xml → robots.txt → DOM links → common path probing.
"""
import os
import re
import uuid
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
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

MAX_PAGES = 15

# Skip patterns — URLs that are not useful to screenshot
SKIP_PATTERNS = [
    '#', 'javascript:', 'mailto:', 'tel:', '.pdf', '.zip', '.exe',
    '/cdn-cgi/', '/wp-json/', '/feed/', '/rss/', 'facebook.com',
    'twitter.com', 'instagram.com', 'linkedin.com', 'youtube.com',
    'google.com', 'apple.com/app', 'play.google.com',
]


def discover_from_sitemap(origin: str) -> list:
    """
    Parse sitemap.xml (and sitemaps referenced in robots.txt) to find all pages.
    Returns list of {'name': ..., 'url': ...}.
    """
    sitemap_urls = []

    # Step 1: Check robots.txt for sitemap references
    try:
        resp = requests.get(f"{origin}/robots.txt", timeout=10,
                           headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                line = line.strip()
                if line.lower().startswith('sitemap:'):
                    sitemap_url = line.split(':', 1)[1].strip()
                    if sitemap_url.startswith('//'):
                        sitemap_url = 'https:' + sitemap_url
                    sitemap_urls.append(sitemap_url)
    except Exception as e:
        logger.debug(f"robots.txt fetch failed: {e}")

    # Step 2: Try standard sitemap locations
    if not sitemap_urls:
        sitemap_urls = [
            f"{origin}/sitemap.xml",
            f"{origin}/sitemap_index.xml",
            f"{origin}/sitemap/sitemap.xml",
        ]

    # Step 3: Parse sitemaps
    all_urls = []
    parsed_sitemaps = set()

    def parse_sitemap(url: str, depth: int = 0):
        if depth > 2 or url in parsed_sitemaps:
            return
        parsed_sitemaps.add(url)

        try:
            resp = requests.get(url, timeout=10,
                               headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code != 200:
                return

            # Remove XML namespace for easier parsing
            content = re.sub(r'\sxmlns="[^"]+"', '', resp.text, count=1)

            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                return

            tag = root.tag.split('}')[-1] if '}' in root.tag else root.tag

            if tag == 'sitemapindex':
                # Sitemap index — recurse into child sitemaps
                for sitemap in root.findall('.//sitemap') or root.findall('.//{*}sitemap'):
                    loc = sitemap.findtext('loc') or sitemap.findtext('{*}loc', '')
                    if not loc:
                        for child in sitemap:
                            if 'loc' in child.tag.lower():
                                loc = child.text
                                break
                    if loc:
                        parse_sitemap(loc.strip(), depth + 1)

            elif tag == 'urlset':
                # URL list
                for url_el in root.findall('.//url') or root.findall('.//{*}url'):
                    loc = url_el.findtext('loc') or url_el.findtext('{*}loc', '')
                    if not loc:
                        for child in url_el:
                            if 'loc' in child.tag.lower():
                                loc = child.text
                                break
                    if loc:
                        all_urls.append(loc.strip())

        except Exception as e:
            logger.debug(f"Sitemap parse failed for {url}: {e}")

    for smap_url in sitemap_urls:
        parse_sitemap(smap_url)

    # Step 4: Deduplicate and classify
    seen_paths = set()
    discovered = []
    origin_host = urlparse(origin).hostname

    for url in all_urls:
        try:
            parsed = urlparse(url)
            if parsed.hostname != origin_host:
                continue
        except Exception:
            continue

        path = parsed.path.rstrip('/')
        if not path or path == '/' or path in seen_paths:
            continue
        if _should_skip(url):
            continue

        seen_paths.add(path)
        category = _classify_page(url, '')
        discovered.append({'name': category, 'url': url})

    logger.info(f"Sitemap discovery: found {len(all_urls)} URLs, {len(discovered)} unique pages")
    return discovered


def _is_same_domain(url: str, origin: str) -> bool:
    """Check if URL belongs to the same domain."""
    try:
        parsed = urlparse(url)
        origin_parsed = urlparse(origin)
        return parsed.hostname == origin_parsed.hostname
    except Exception:
        return False


def _should_skip(url: str) -> bool:
    """Check if URL should be skipped."""
    lower = url.lower()
    return any(skip in lower for skip in SKIP_PATTERNS)


def _classify_page(url: str, text: str) -> str:
    """Classify a page by its URL path and link text."""
    path = urlparse(url).path.lower().strip('/')
    text_lower = text.lower()
    combined = f"{path} {text_lower}"

    classifications = {
        'pricing': ['pricing', 'plans', 'tariff', 'price', 'subscription', 'cost'],
        'features': ['features', 'product', 'solutions', 'capabilities', 'platform', 'tools'],
        'about': ['about', 'company', 'team', 'story', 'mission', 'who-we-are'],
        'contact': ['contact', 'support', 'help', 'demo', 'book-a-demo', 'get-in-touch'],
        'signup': ['signup', 'sign-up', 'register', 'get-started', 'start-free', 'trial', 'free-trial'],
        'login': ['login', 'signin', 'sign-in', 'account', 'dashboard', 'app'],
        'blog': ['blog', 'news', 'articles', 'resources', 'insights', 'learn'],
        'docs': ['docs', 'documentation', 'api', 'developers', 'guides', 'knowledge'],
        'integrations': ['integrations', 'apps', 'marketplace', 'plugins', 'connect'],
        'customers': ['customers', 'case-studies', 'testimonials', 'reviews', 'success-stories'],
        'security': ['security', 'privacy', 'compliance', 'trust', 'gdpr'],
        'careers': ['careers', 'jobs', 'hiring', 'join-us', 'work-with-us'],
        'changelog': ['changelog', 'releases', 'updates', 'whats-new', 'release-notes'],
        'partners': ['partners', 'affiliate', 'referral', 'reseller'],
        'promotions': ['promo', 'offer', 'discount', 'deal', 'sale', 'bonus', 'reward', 'loyalty'],
        'catalog': ['catalog', 'catalogue', 'products', 'shop', 'store', 'collection', 'category'],
        'faq': ['faq', 'frequently-asked', 'help-center', 'knowledge-base'],
    }

    for category, keywords in classifications.items():
        if any(kw in combined for kw in keywords):
            return category

    # Fallback — use first path segment
    segments = path.split('/')
    if segments and segments[0]:
        return segments[0][:30]

    return 'page'


def discover_all_pages(page, base_url: str, origin: str) -> list:
    """
    Deep page discovery — finds ALL navigable links on the page.
    Collects from nav, header, footer, main content, buttons, CTAs.
    """
    try:
        links = page.evaluate('''(args) => {
            const [baseUrl, origin] = args;
            const results = [];
            const seen = new Set();

            // Collect ALL links on the page
            document.querySelectorAll('a[href]').forEach(a => {
                let href = a.href;
                if (!href) return;

                // Normalize
                try {
                    const url = new URL(href, baseUrl);
                    href = url.href;
                } catch(e) { return; }

                const text = (a.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 80);
                if (!text || text.length < 2) return;
                if (seen.has(href)) return;
                seen.add(href);

                // Check same origin
                try {
                    const parsed = new URL(href);
                    const originParsed = new URL(origin);
                    if (parsed.hostname !== originParsed.hostname) return;
                } catch(e) { return; }

                // Get context — is it in nav, header, footer, main?
                let context = 'body';
                let el = a;
                while (el.parentElement) {
                    el = el.parentElement;
                    const tag = el.tagName.toLowerCase();
                    if (tag === 'nav' || el.getAttribute('role') === 'navigation') { context = 'nav'; break; }
                    if (tag === 'header') { context = 'header'; break; }
                    if (tag === 'footer') { context = 'footer'; break; }
                    if (tag === 'main') { context = 'main'; break; }
                }

                // Priority: nav > header > main > footer > body
                const priority = context === 'nav' ? 1 : context === 'header' ? 2 : context === 'main' ? 3 : context === 'footer' ? 4 : 5;

                results.push({
                    url: href,
                    text: text,
                    context: context,
                    priority: priority,
                    isButton: a.closest('button') !== null || a.className.toLowerCase().includes('btn') || a.className.toLowerCase().includes('cta'),
                });
            });

            // Sort by priority (nav first)
            results.sort((a, b) => a.priority - b.priority);
            return results;
        }''', [base_url, origin])
    except Exception as e:
        logger.warning(f"Link discovery failed: {e}")
        return []

    # Deduplicate by path and classify
    seen_paths = set()
    seen_categories = set()
    discovered = []

    for link in links:
        url = link['url']
        text = link.get('text', '')

        if _should_skip(url):
            continue

        # Normalize path for dedup
        path = urlparse(url).path.rstrip('/')
        if path in seen_paths or path == '' or path == '/':
            continue
        seen_paths.add(path)

        category = _classify_page(url, text)

        # Allow multiple pages of same category (e.g., multiple product pages)
        # but limit to 2 per category
        cat_count = sum(1 for d in discovered if d['name'] == category)
        if cat_count >= 2:
            category = f"{category}_{cat_count + 1}"

        discovered.append({
            'name': category,
            'url': url,
            'text': text,
            'context': link.get('context', 'body'),
        })

    logger.info(f"Discovered {len(discovered)} unique pages: {[d['name'] for d in discovered[:MAX_PAGES]]}")
    return discovered[:MAX_PAGES - 1]


def _capture_page(pw_page, page_url: str, page_name: str, device_type: str,
                   run, competitor, viewport: dict) -> RunScreenshot:
    """Capture a single page — screenshot + DOM text extraction."""
    file_id = str(uuid.uuid4())
    s3_key = f"runs/{run.id}/{competitor.id}/{device_type}_{page_name}_{file_id}.png"
    local_path = SCREENSHOTS_DIR / f"{file_id}.png"

    status = 'success'
    error_message = ''
    dom_text = ''
    html_snippet = ''

    try:
        response = pw_page.goto(page_url, wait_until='domcontentloaded', timeout=20000)

        if response and response.status >= 400:
            status = 'error' if response.status != 404 else 'not_found'
            error_message = f'HTTP {response.status}'
        else:
            # Wait for content to render
            pw_page.wait_for_timeout(2000)

            # Scroll down to trigger lazy loading
            pw_page.evaluate('window.scrollTo(0, document.body.scrollHeight / 2)')
            pw_page.wait_for_timeout(500)
            pw_page.evaluate('window.scrollTo(0, 0)')
            pw_page.wait_for_timeout(500)

            # Screenshot
            pw_page.screenshot(path=str(local_path), full_page=True)

            # Extract text
            try:
                dom_text = pw_page.evaluate('() => document.body.innerText') or ''
                dom_text = dom_text[:50000]
            except Exception:
                pass

    except PlaywrightTimeout:
        status = 'timeout'
        error_message = f'Timeout loading {page_url}'
    except Exception as e:
        status = 'error'
        error_message = str(e)[:500]
        logger.warning(f"Capture failed {page_url}: {e}")

    return RunScreenshot.objects.create(
        run=run,
        competitor=competitor,
        page_url=page_url,
        page_name=page_name,
        device_type=device_type,
        s3_key=s3_key,
        viewport_width=viewport['width'],
        viewport_height=viewport['height'],
        dom_text=dom_text,
        html_snippet='',  # Skip HTML to save space
        status=status,
        error_message=error_message,
    )


def screenshot_competitor(run, competitor, device_types=None, pages=None):
    """
    Deep crawl: auto-discovers ALL important pages, screenshots each one.
    """
    job = run.job
    if device_types is None:
        if job.device_type == 'both':
            device_types = ['desktop', 'mobile']
        else:
            device_types = [job.device_type]

    screenshots = []
    base_url = competitor.url.rstrip('/')
    if not base_url.startswith('http'):
        base_url = f'https://{base_url}'
    parsed = urlparse(base_url)
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

            # Step 1: Sitemap discovery (fastest, most reliable)
            pages_to_capture = [{'name': 'homepage', 'url': base_url}]
            sitemap_pages = discover_from_sitemap(origin)
            if sitemap_pages:
                # Prioritize: take up to MAX_PAGES from sitemap
                seen_names = {'homepage'}
                for sp in sitemap_pages:
                    if sp['name'] not in seen_names and len(pages_to_capture) < MAX_PAGES:
                        pages_to_capture.append(sp)
                        seen_names.add(sp['name'])
                logger.info(f"[{competitor.name}] Sitemap: added {len(pages_to_capture) - 1} pages")

            # Step 2: Load homepage + DOM discovery (for pages not in sitemap)
            try:
                pw_page.goto(base_url, wait_until='domcontentloaded', timeout=25000)
                pw_page.wait_for_timeout(3000)

                if len(pages_to_capture) < 5:
                    # Sitemap was small or empty — supplement with DOM links
                    discovered = discover_all_pages(pw_page, base_url, origin)
                    existing_urls = {p['url'] for p in pages_to_capture}
                    for dp in discovered:
                        if dp['url'] not in existing_urls and len(pages_to_capture) < MAX_PAGES:
                            pages_to_capture.append(dp)
                            existing_urls.add(dp['url'])

            except Exception as e:
                logger.warning(f"Homepage load failed for {competitor.name}: {e}")

            # Step 2b: Fallback — if discovery found nothing, probe common paths
            if len(pages_to_capture) <= 1:
                logger.info(f"[{competitor.name}] No links discovered, probing common paths...")
                common_paths = [
                    ('pricing', '/pricing'), ('features', '/features'), ('product', '/product'),
                    ('about', '/about'), ('about', '/about-us'), ('contact', '/contact'),
                    ('blog', '/blog'), ('docs', '/docs'), ('faq', '/faq'),
                    ('signup', '/signup'), ('signup', '/sign-up'), ('signup', '/register'),
                    ('login', '/login'), ('login', '/sign-in'),
                    ('terms', '/terms'), ('privacy', '/privacy'),
                    ('integrations', '/integrations'), ('customers', '/customers'),
                    ('careers', '/careers'), ('partners', '/partners'),
                    ('promotions', '/promotions'), ('promotions', '/offers'),
                    ('catalog', '/catalog'), ('catalog', '/products'),
                    ('bonus', '/bonus'), ('loyalty', '/loyalty'),
                    ('live', '/live'), ('sports', '/sports'), ('casino', '/casino'),
                    ('games', '/games'), ('slots', '/slots'),
                ]
                probed_names = set()
                for name, path in common_paths:
                    if name in probed_names:
                        continue
                    probe_url = f"{origin}{path}"
                    try:
                        resp = pw_page.goto(probe_url, wait_until='domcontentloaded', timeout=8000)
                        if resp and resp.status < 400:
                            pages_to_capture.append({'name': name, 'url': probe_url})
                            probed_names.add(name)
                            logger.info(f"[{competitor.name}] Probed OK: {path}")
                            if len(pages_to_capture) >= MAX_PAGES:
                                break
                    except Exception:
                        pass

            logger.info(f"[{competitor.name}] Will capture {len(pages_to_capture)} pages: "
                       f"{[p['name'] for p in pages_to_capture]}")

            # Step 3: Capture ALL pages
            for i, page_info in enumerate(pages_to_capture):
                logger.info(f"[{competitor.name}] Capturing {i+1}/{len(pages_to_capture)}: "
                           f"{page_info['name']} ({page_info['url'][:60]})")

                shot = _capture_page(
                    pw_page, page_info['url'], page_info['name'],
                    device_type, run, competitor, viewport
                )
                screenshots.append(shot)

            context.close()
        browser.close()

    logger.info(f"[{competitor.name}] Total: {len(screenshots)} screenshots "
               f"({sum(1 for s in screenshots if s.status == 'success')} success)")
    return screenshots
