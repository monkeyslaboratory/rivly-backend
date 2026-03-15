"""
Preflight check service.
Validates that competitor URLs are accessible before running the full pipeline.
"""
import logging
from dataclasses import dataclass
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)


@dataclass
class PreflightResult:
    competitor: object
    success: bool
    status_code: int = 0
    error_type: str = ''
    error_message: str = ''
    recoverable: bool = False


def preflight_check(run, competitors):
    """
    Quick check that each competitor URL is reachable.
    Returns list of PreflightResult.
    """
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1440, 'height': 900},
            user_agent=(
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
        )
        page = context.new_page()

        for competitor in competitors:
            try:
                response = page.goto(competitor.url, wait_until='domcontentloaded', timeout=15000)

                if response is None:
                    results.append(PreflightResult(
                        competitor=competitor,
                        success=False,
                        error_type='error',
                        error_message='No response received',
                    ))
                elif response.status == 404:
                    results.append(PreflightResult(
                        competitor=competitor,
                        success=False,
                        status_code=404,
                        error_type='not_found_404',
                        error_message=f'{competitor.url} returned 404',
                    ))
                elif response.status == 403:
                    results.append(PreflightResult(
                        competitor=competitor,
                        success=False,
                        status_code=403,
                        error_type='blocked',
                        error_message=f'{competitor.url} blocked (403)',
                        recoverable=True,
                    ))
                elif response.status >= 500:
                    results.append(PreflightResult(
                        competitor=competitor,
                        success=False,
                        status_code=response.status,
                        error_type='error',
                        error_message=f'{competitor.url} server error ({response.status})',
                        recoverable=True,
                    ))
                else:
                    results.append(PreflightResult(
                        competitor=competitor,
                        success=True,
                        status_code=response.status,
                    ))

            except PlaywrightTimeout:
                results.append(PreflightResult(
                    competitor=competitor,
                    success=False,
                    error_type='timeout',
                    error_message=f'Timeout loading {competitor.url}',
                    recoverable=True,
                ))
            except Exception as e:
                results.append(PreflightResult(
                    competitor=competitor,
                    success=False,
                    error_type='error',
                    error_message=str(e)[:300],
                ))

        context.close()
        browser.close()

    return results
