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
    Handles: redirects, multiple sitemaps, sitemap indexes, cross-domain sitemaps.
    Returns list of {'name': ..., 'url': ...}.
    """
    sitemap_urls = []
    # Track the actual domain(s) we find in sitemaps (may differ from origin)
    allowed_hosts = set()
    origin_host = urlparse(origin).hostname
    allowed_hosts.add(origin_host)
    if origin_host.startswith('www.'):
        allowed_hosts.add(origin_host[4:])
    else:
        allowed_hosts.add(f'www.{origin_host}')

    # Step 1: Check robots.txt for sitemap references (follow redirects!)
    try:
        resp = requests.get(f"{origin}/robots.txt", timeout=10, allow_redirects=True,
                           headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                line = line.strip()
                if line.lower().startswith('sitemap:'):
                    # Extract URL — everything after "Sitemap:" (case-insensitive)
                    sitemap_url = re.sub(r'^sitemap:\s*', '', line, flags=re.IGNORECASE).strip()
                    if sitemap_url.startswith('//'):
                        sitemap_url = 'https:' + sitemap_url
                    if sitemap_url.startswith('http'):
                        sitemap_urls.append(sitemap_url)
                        # Allow the sitemap's domain too
                        smap_host = urlparse(sitemap_url).hostname
                        if smap_host:
                            allowed_hosts.add(smap_host)
                            if smap_host.startswith('www.'):
                                allowed_hosts.add(smap_host[4:])
                            else:
                                allowed_hosts.add(f'www.{smap_host}')
            logger.info(f"robots.txt sitemaps: {sitemap_urls}")
    except Exception as e:
        logger.debug(f"robots.txt fetch failed: {e}")

    # Step 2: Try standard sitemap locations if robots.txt had none
    if not sitemap_urls:
        sitemap_urls = [
            f"{origin}/sitemap.xml",
            f"{origin}/sitemap_index.xml",
        ]

    # Step 3: Parse all sitemaps recursively
    all_urls = []
    parsed_sitemaps = set()

    def _find_loc(element) -> str:
        """Extract <loc> text from an element, handling namespaces."""
        # Try direct children
        for child in element:
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if tag.lower() == 'loc' and child.text:
                return child.text.strip()
        return ''

    def parse_sitemap(url: str, depth: int = 0):
        if depth > 3 or url in parsed_sitemaps:
            return
        parsed_sitemaps.add(url)

        try:
            resp = requests.get(url, timeout=15, allow_redirects=True,
                               headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code != 200:
                logger.debug(f"Sitemap {url}: HTTP {resp.status_code}")
                return

            content = resp.text
            # Remove XML namespace declarations for simpler parsing
            content = re.sub(r'\sxmlns[^"]*"[^"]*"', '', content)

            try:
                root = ET.fromstring(content)
            except ET.ParseError as e:
                logger.debug(f"XML parse error for {url}: {e}")
                return

            tag_name = root.tag.split('}')[-1] if '}' in root.tag else root.tag

            if tag_name == 'sitemapindex':
                for child in root:
                    child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                    if child_tag == 'sitemap':
                        loc = _find_loc(child)
                        if loc:
                            parse_sitemap(loc, depth + 1)

            elif tag_name == 'urlset':
                for child in root:
                    child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                    if child_tag == 'url':
                        loc = _find_loc(child)
                        if loc:
                            all_urls.append(loc)

            logger.info(f"Parsed sitemap {url}: {len(all_urls)} total URLs so far")

        except Exception as e:
            logger.debug(f"Sitemap fetch failed for {url}: {e}")

    for smap_url in sitemap_urls:
        parse_sitemap(smap_url)

    # Step 4: Deduplicate, filter, classify
    seen_paths = set()
    seen_categories = {}
    discovered = []

    for url in all_urls:
        try:
            parsed = urlparse(url)
            host = parsed.hostname
            if host not in allowed_hosts:
                continue
        except Exception:
            continue

        path = parsed.path.rstrip('/')
        if not path or path == '/' or path in seen_paths:
            continue
        if _should_skip(url):
            continue
        # Skip very deep paths (likely individual items, not section pages)
        if path.count('/') > 3:
            continue

        seen_paths.add(path)
        category = _classify_page(url, '')

        # Limit 2 per category
        seen_categories[category] = seen_categories.get(category, 0) + 1
        if seen_categories[category] > 2:
            continue

        discovered.append({'name': category, 'url': url})

    logger.info(f"Sitemap discovery: {len(all_urls)} raw URLs → {len(discovered)} unique pages "
               f"(allowed hosts: {allowed_hosts})")
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
                   run, competitor, viewport: dict, skip_auth_check: bool = False) -> RunScreenshot:
    """Capture a single page. Set skip_auth_check=True for authenticated captures."""
    file_id = str(uuid.uuid4())
    s3_key = f"runs/{run.id}/{competitor.id}/{device_type}_{page_name}_{file_id}.png"
    local_path = SCREENSHOTS_DIR / f"{file_id}.png"

    status = 'success'
    error_message = ''
    dom_text = ''
    html_snippet = ''

    try:
        # Use networkidle to wait for all requests to finish
        response = pw_page.goto(page_url, wait_until='networkidle', timeout=30000)

        if response and response.status >= 400:
            status = 'error' if response.status != 404 else 'not_found'
            error_message = f'HTTP {response.status}'
        else:
            # Wait for JS rendering — SPA frameworks need time
            pw_page.wait_for_timeout(2000)

            # Wait for loaders/skeletons to disappear
            try:
                pw_page.evaluate('''() => {
                    return new Promise((resolve) => {
                        const check = () => {
                            const loaders = document.querySelectorAll(
                                '[class*="skeleton"], [class*="shimmer"], [class*="loader"], ' +
                                '[class*="loading"], [class*="spinner"], [class*="placeholder"], ' +
                                '[class*="Skeleton"], [class*="Shimmer"], [class*="Loader"], ' +
                                '[class*="Loading"], [class*="Spinner"], [class*="Placeholder"], ' +
                                '[role="progressbar"], .animate-pulse'
                            );
                            // Filter only visible ones
                            const visible = Array.from(loaders).filter(el => {
                                const rect = el.getBoundingClientRect();
                                return rect.width > 0 && rect.height > 0 &&
                                       window.getComputedStyle(el).display !== 'none';
                            });
                            if (visible.length === 0) {
                                resolve(true);
                            } else {
                                setTimeout(check, 500);
                            }
                        };
                        // Max wait 8 seconds for loaders to disappear
                        setTimeout(() => resolve(false), 8000);
                        check();
                    });
                }''')
            except Exception:
                pass

            # Additional wait after loaders gone
            pw_page.wait_for_timeout(1000)

            # Scroll down slowly to trigger lazy loading
            pw_page.evaluate('''() => {
                return new Promise((resolve) => {
                    const height = document.body.scrollHeight;
                    const step = Math.floor(height / 4);
                    let current = 0;
                    const scroll = () => {
                        current += step;
                        window.scrollTo(0, Math.min(current, height));
                        if (current < height) {
                            setTimeout(scroll, 400);
                        } else {
                            // Scroll back to top
                            setTimeout(() => {
                                window.scrollTo(0, 0);
                                setTimeout(resolve, 800);
                            }, 500);
                        }
                    };
                    scroll();
                });
            }''')

            # Final wait for any lazy images triggered by scroll
            pw_page.wait_for_timeout(1000)

            # Screenshot
            pw_page.screenshot(path=str(local_path), full_page=True)

            # Extract text
            try:
                dom_text = pw_page.evaluate('() => document.body.innerText') or ''
                dom_text = dom_text[:50000]
            except Exception:
                pass

            # Detect auth wall / login form (skip for authenticated recaptures)
            if not skip_auth_check:
                try:
                    has_auth_wall = pw_page.evaluate('''() => {
                        const body = document.body;
                        if (!body) return false;
                        const text = body.innerText.toLowerCase();
                        const html = body.innerHTML.toLowerCase();

                        const hasPasswordField = document.querySelector('input[type="password"]') !== null;
                        const hasLoginForm = document.querySelector('form[action*="login"], form[action*="signin"], form[action*="auth"]') !== null;

                        const loginPatterns = [
                            'sign in', 'log in', 'login', 'войти', 'авторизац',
                            'enter your password', 'enter your email',
                            'create an account', 'don\\'t have an account',
                            'forgot password', 'забыли пароль',
                            'access denied', 'unauthorized', '401',
                            'please log in', 'authentication required',
                        ];
                        const hasLoginText = loginPatterns.some(p => text.includes(p));

                        if (hasPasswordField && hasLoginText) return true;
                        if (hasLoginForm) return true;
                        if (hasLoginText && text.length < 500) return true;

                        return false;
                    }''')

                    if has_auth_wall:
                        status = 'auth_required'
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


def authenticated_crawl(run_id: str):
    """Interactive auth: login, detect captcha/2FA, report status."""
    from apps.runs.models import Run

    run = Run.objects.get(id=run_id)
    creds = run.auth_credentials
    if not creds:
        return

    email = creds.get('email', '')
    password = creds.get('password', '')
    login_url = creds.get('login_url', '')

    auth_shots = RunScreenshot.objects.filter(run=run, status='auth_required')
    if not auth_shots.exists():
        return

    competitor = auth_shots.first().competitor

    # Update status
    run.auth_status = 'logging_in'
    run.auth_message = 'Navigating to login page...'
    run.save(update_fields=['auth_status', 'auth_message'])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORTS['desktop'],
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')
        page = context.new_page()

        target_login = login_url or f"{competitor.url.rstrip('/')}/login"

        try:
            page.goto(target_login, wait_until='networkidle', timeout=25000)
            page.wait_for_timeout(3000)

            run.auth_message = 'Filling credentials...'
            run.save(update_fields=['auth_message'])

            # Fill email
            email_filled = False
            email_selectors = [
                'input[type="email"]', 'input[name="email"]', 'input[name="login"]',
                'input[name="username"]', 'input[name="phone"]',
                'input[id*="email" i]', 'input[id*="login" i]', 'input[id*="user" i]',
                'input[placeholder*="email" i]', 'input[placeholder*="логин" i]',
                'input[placeholder*="телефон" i]', 'input[placeholder*="phone" i]',
                'input[autocomplete="email"]', 'input[autocomplete="username"]',
            ]
            for sel in email_selectors:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click()
                        el.fill(email)
                        email_filled = True
                        break
                except Exception:
                    continue

            if not email_filled:
                # Try first visible text input
                try:
                    inputs = page.query_selector_all('input[type="text"], input:not([type])')
                    for inp in inputs:
                        if inp.is_visible():
                            inp.fill(email)
                            email_filled = True
                            break
                except Exception:
                    pass

            # Fill password
            pw_filled = False
            try:
                pw_el = page.query_selector('input[type="password"]')
                if pw_el and pw_el.is_visible():
                    pw_el.click()
                    pw_el.fill(password)
                    pw_filled = True
            except Exception:
                pass

            if not email_filled or not pw_filled:
                run.auth_status = 'auth_failed'
                run.auth_message = 'Could not find login form fields'
                run.save(update_fields=['auth_status', 'auth_message'])
                browser.close()
                return

            run.auth_message = 'Submitting login form...'
            run.save(update_fields=['auth_message'])

            # Submit
            submitted = False
            submit_selectors = [
                'button[type="submit"]', 'input[type="submit"]',
                'button:has-text("Log in")', 'button:has-text("Sign in")',
                'button:has-text("Войти")', 'button:has-text("Login")',
                'button:has-text("Submit")', 'button:has-text("Вход")',
                'button:has-text("Continue")', 'button:has-text("Продолжить")',
            ]
            for sel in submit_selectors:
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.click()
                        submitted = True
                        break
                except Exception:
                    continue

            if not submitted:
                # Try pressing Enter
                try:
                    page.keyboard.press('Enter')
                    submitted = True
                except Exception:
                    pass

            # Wait for response
            page.wait_for_timeout(5000)

            # Check what happened after submit

            # 1. Check for CAPTCHA
            try:
                has_captcha = page.evaluate('''() => {
                    const html = document.body.innerHTML.toLowerCase();
                    const text = document.body.innerText.toLowerCase();
                    return (
                        document.querySelector('iframe[src*="captcha"]') !== null ||
                        document.querySelector('iframe[src*="recaptcha"]') !== null ||
                        document.querySelector('[class*="captcha" i]') !== null ||
                        document.querySelector('[id*="captcha" i]') !== null ||
                        document.querySelector('.g-recaptcha') !== null ||
                        document.querySelector('[data-sitekey]') !== null ||
                        html.includes('captcha') ||
                        text.includes('i\\'m not a robot') ||
                        text.includes('verify you are human') ||
                        text.includes('введите код с картинки') ||
                        text.includes('подтвердите, что вы не робот')
                    );
                }''')

                if has_captcha:
                    # Screenshot the captcha
                    _capture_page(page, page.url, 'captcha_challenge', 'desktop',
                                  run, competitor, VIEWPORTS['desktop'])
                    run.auth_status = 'captcha_required'
                    run.auth_message = 'Captcha detected. Screenshot saved as captcha_challenge.'
                    run.save(update_fields=['auth_status', 'auth_message'])
                    context.close()
                    browser.close()
                    return
            except Exception:
                pass

            # 2. Check for 2FA/verification code
            try:
                has_code_input = page.evaluate('''() => {
                    const text = document.body.innerText.toLowerCase();
                    const inputs = document.querySelectorAll('input[type="text"], input[type="number"], input[type="tel"]');
                    const hasShortInput = Array.from(inputs).some(i => {
                        const ml = i.getAttribute('maxlength');
                        return i.offsetParent !== null && ml && parseInt(ml) <= 8;
                    });
                    return hasShortInput && (
                        text.includes('verification') || text.includes('code') ||
                        text.includes('confirm') || text.includes('подтверд') ||
                        text.includes('код') || text.includes('sms') ||
                        text.includes('one-time') || text.includes('otp') ||
                        text.includes('2fa') || text.includes('two-factor')
                    );
                }''')

                if has_code_input:
                    _capture_page(page, page.url, 'verification_code', 'desktop',
                                  run, competitor, VIEWPORTS['desktop'])
                    run.auth_status = 'code_required'
                    run.auth_message = 'Verification code required. Check your email/phone.'
                    run.save(update_fields=['auth_status', 'auth_message'])
                    context.close()
                    browser.close()
                    return
            except Exception:
                pass

            # 3. Check if still on login page (auth failed)
            try:
                still_login = page.evaluate('''() => {
                    const pw = document.querySelector('input[type="password"]');
                    if (pw && pw.offsetParent !== null) return true;
                    const text = document.body.innerText.toLowerCase();
                    return (text.includes('invalid') || text.includes('incorrect') ||
                            text.includes('wrong') || text.includes('неверн') ||
                            text.includes('ошибка') || text.includes('failed'));
                }''')

                if still_login:
                    # Try to get error message
                    error_msg = page.evaluate('''() => {
                        const selectors = ['[class*="error" i]', '[class*="alert" i]', '[role="alert"]',
                                          '[class*="message" i]'];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el && el.textContent.trim()) return el.textContent.trim().substring(0, 200);
                        }
                        return 'Login failed - credentials may be incorrect';
                    }''') or 'Login failed'

                    run.auth_status = 'auth_failed'
                    run.auth_message = error_msg
                    run.save(update_fields=['auth_status', 'auth_message'])
                    context.close()
                    browser.close()
                    return
            except Exception:
                pass

            # 4. Success! Logged in
            run.auth_status = 'logged_in'
            run.auth_message = f'Successfully logged in. Re-capturing {auth_shots.count()} pages...'
            run.save(update_fields=['auth_status', 'auth_message'])

            logger.info(f"Auth success for run {run_id}")

            # Re-capture auth pages
            captured = 0
            for shot in auth_shots:
                new_shot = _capture_page(page, shot.page_url, f"{shot.page_name}_authenticated",
                                         'desktop', run, competitor, VIEWPORTS['desktop'],
                                         skip_auth_check=True)
                captured += 1
                run.auth_message = f'Re-captured {captured}/{auth_shots.count()} pages...'
                run.save(update_fields=['auth_message'])

                if new_shot.status == 'auth_required':
                    new_shot.status = 'auth_failed'
                    new_shot.save(update_fields=['status'])

            run.auth_message = f'Done. {captured} authenticated pages captured.'
            run.save(update_fields=['auth_message'])

        except Exception as e:
            logger.error(f"Auth crawl error: {e}")
            run.auth_status = 'auth_failed'
            run.auth_message = f'Error: {str(e)[:300]}'
            run.save(update_fields=['auth_status', 'auth_message'])
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass


def submit_verification_code(run_id: str):
    """Submit a verification/2FA code or captcha text to continue authentication."""
    from apps.runs.models import Run

    run = Run.objects.get(id=run_id)
    creds = run.auth_credentials
    if not creds:
        return

    code = creds.get('verification_code', '')
    email = creds.get('email', '')
    password = creds.get('password', '')
    login_url = creds.get('login_url', '')

    if not code:
        return

    auth_shots = RunScreenshot.objects.filter(run=run, status='auth_required')
    if not auth_shots.exists():
        return

    competitor = auth_shots.first().competitor

    run.auth_status = 'logging_in'
    run.auth_message = 'Submitting verification code...'
    run.save(update_fields=['auth_status', 'auth_message'])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORTS['desktop'],
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')
        page = context.new_page()

        target_login = login_url or f"{competitor.url.rstrip('/')}/login"

        try:
            # Re-navigate and login again (session is not preserved between calls)
            page.goto(target_login, wait_until='networkidle', timeout=25000)
            page.wait_for_timeout(3000)

            # Fill email
            email_selectors = [
                'input[type="email"]', 'input[name="email"]', 'input[name="login"]',
                'input[name="username"]', 'input[name="phone"]',
                'input[id*="email" i]', 'input[id*="login" i]', 'input[id*="user" i]',
                'input[placeholder*="email" i]', 'input[placeholder*="логин" i]',
                'input[autocomplete="email"]', 'input[autocomplete="username"]',
            ]
            for sel in email_selectors:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click()
                        el.fill(email)
                        break
                except Exception:
                    continue

            # Fill password
            try:
                pw_el = page.query_selector('input[type="password"]')
                if pw_el and pw_el.is_visible():
                    pw_el.click()
                    pw_el.fill(password)
            except Exception:
                pass

            # Submit login
            submit_selectors = [
                'button[type="submit"]', 'input[type="submit"]',
                'button:has-text("Log in")', 'button:has-text("Sign in")',
                'button:has-text("Войти")', 'button:has-text("Login")',
                'button:has-text("Submit")', 'button:has-text("Вход")',
                'button:has-text("Continue")', 'button:has-text("Продолжить")',
            ]
            for sel in submit_selectors:
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.click()
                        break
                except Exception:
                    continue

            page.wait_for_timeout(5000)

            run.auth_message = 'Filling verification code...'
            run.save(update_fields=['auth_message'])

            # Find and fill the code/captcha input
            code_filled = False
            code_selectors = [
                'input[name*="code" i]', 'input[name*="otp" i]', 'input[name*="token" i]',
                'input[name*="captcha" i]', 'input[name*="verify" i]',
                'input[id*="code" i]', 'input[id*="otp" i]', 'input[id*="captcha" i]',
                'input[placeholder*="code" i]', 'input[placeholder*="код" i]',
                'input[autocomplete="one-time-code"]',
            ]
            for sel in code_selectors:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click()
                        el.fill(code)
                        code_filled = True
                        break
                except Exception:
                    continue

            if not code_filled:
                # Try short text/number/tel inputs
                try:
                    inputs = page.query_selector_all('input[type="text"], input[type="number"], input[type="tel"]')
                    for inp in inputs:
                        if inp.is_visible():
                            ml = inp.get_attribute('maxlength')
                            if ml and int(ml) <= 8:
                                inp.fill(code)
                                code_filled = True
                                break
                    # If still not filled, try any visible text input
                    if not code_filled:
                        for inp in inputs:
                            if inp.is_visible():
                                inp.fill(code)
                                code_filled = True
                                break
                except Exception:
                    pass

            if not code_filled:
                run.auth_status = 'auth_failed'
                run.auth_message = 'Could not find verification code input field'
                run.save(update_fields=['auth_status', 'auth_message'])
                browser.close()
                return

            # Submit the code
            run.auth_message = 'Submitting verification code...'
            run.save(update_fields=['auth_message'])

            for sel in submit_selectors:
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.click()
                        break
                except Exception:
                    continue
            else:
                try:
                    page.keyboard.press('Enter')
                except Exception:
                    pass

            page.wait_for_timeout(5000)

            # Check if login succeeded
            try:
                still_login = page.evaluate('''() => {
                    const pw = document.querySelector('input[type="password"]');
                    if (pw && pw.offsetParent !== null) return true;
                    const text = document.body.innerText.toLowerCase();
                    return (text.includes('invalid') || text.includes('incorrect') ||
                            text.includes('wrong') || text.includes('неверн') ||
                            text.includes('expired') || text.includes('истек'));
                }''')

                if still_login:
                    error_msg = page.evaluate('''() => {
                        const selectors = ['[class*="error" i]', '[class*="alert" i]', '[role="alert"]',
                                          '[class*="message" i]'];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el && el.textContent.trim()) return el.textContent.trim().substring(0, 200);
                        }
                        return 'Verification failed - code may be incorrect or expired';
                    }''') or 'Verification failed'

                    run.auth_status = 'auth_failed'
                    run.auth_message = error_msg
                    run.save(update_fields=['auth_status', 'auth_message'])
                    context.close()
                    browser.close()
                    return
            except Exception:
                pass

            # Success - re-capture pages
            run.auth_status = 'logged_in'
            run.auth_message = f'Verification successful. Re-capturing {auth_shots.count()} pages...'
            run.save(update_fields=['auth_status', 'auth_message'])

            logger.info(f"Verification success for run {run_id}")

            captured = 0
            for shot in auth_shots:
                new_shot = _capture_page(page, shot.page_url, f"{shot.page_name}_authenticated",
                                         'desktop', run, competitor, VIEWPORTS['desktop'],
                                         skip_auth_check=True)
                captured += 1
                run.auth_message = f'Re-captured {captured}/{auth_shots.count()} pages...'
                run.save(update_fields=['auth_message'])

                if new_shot.status == 'auth_required':
                    new_shot.status = 'auth_failed'
                    new_shot.save(update_fields=['status'])

            run.auth_message = f'Done. {captured} authenticated pages captured.'
            run.save(update_fields=['auth_message'])

        except Exception as e:
            logger.error(f"Verification code submit error: {e}")
            run.auth_status = 'auth_failed'
            run.auth_message = f'Error: {str(e)[:300]}'
            run.save(update_fields=['auth_status', 'auth_message'])
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass


def recrawl_with_cookies(run_id: str):
    """Re-crawl auth_required pages using cookies captured from interactive browser session."""
    from apps.runs.models import Run

    run = Run.objects.get(id=run_id)
    cookies = run.auth_cookies
    if not cookies:
        logger.warning(f"recrawl_with_cookies: no cookies for run {run_id}")
        return

    auth_shots = RunScreenshot.objects.filter(run=run, status='auth_required')
    if not auth_shots.exists():
        logger.info(f"recrawl_with_cookies: no auth_required pages for run {run_id}")
        return

    competitor = auth_shots.first().competitor

    run.auth_status = 'logging_in'
    run.auth_message = 'Re-crawling with browser session cookies...'
    run.save(update_fields=['auth_status', 'auth_message'])

    captured = 0

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport=VIEWPORTS['desktop'],
                user_agent=(
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
            )
            context.add_cookies(cookies)
            page = context.new_page()

            for shot in auth_shots:
                try:
                    page_url = shot.page_url
                    page_name = shot.page_name
                    device_type = shot.device_type
                    viewport = VIEWPORTS.get(device_type, VIEWPORTS['desktop'])
                    shot.delete()

                    new_shot = _capture_page(
                        page, page_url, page_name, device_type,
                        run, competitor, viewport, skip_auth_check=True,
                    )

                    if new_shot.status == 'success':
                        captured += 1
                    elif new_shot.status == 'auth_required':
                        new_shot.status = 'auth_failed'
                        new_shot.save(update_fields=['status'])

                except Exception as e:
                    logger.warning(f"recrawl_with_cookies failed for {shot.page_url}: {e}")

            run.auth_status = 'logged_in'
            run.auth_message = f'Done. {captured} authenticated pages captured via browser session.'
            run.save(update_fields=['auth_status', 'auth_message'])

            try:
                context.close()
                browser.close()
            except Exception:
                pass

    except Exception as e:
        logger.error(f"recrawl_with_cookies error: {e}")
        run.auth_status = 'auth_failed'
        run.auth_message = f'Cookie recrawl error: {str(e)[:300]}'
        run.save(update_fields=['auth_status', 'auth_message'])
