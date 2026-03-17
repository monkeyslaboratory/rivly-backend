import asyncio
import base64
import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

SESSION_TIMEOUT = 300  # 5 minutes
FRAME_INTERVAL = 0.3  # 300ms between frames
VIEWPORT = {"width": 1280, "height": 720}


class BrowserSessionConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer that streams a live Playwright browser session.
    The frontend sends mouse/keyboard events; the backend streams JPEG frames.
    On "done", cookies are captured and saved for headless re-crawling.
    """

    async def connect(self):
        self.run_id = str(self.scope["url_route"]["kwargs"]["run_id"])
        self._running = False
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._screenshot_task = None
        self._timeout_task = None

        await self.accept()

        try:
            run_data = await self._get_run_data(self.run_id)
        except Exception as e:
            await self.send_json({"type": "error", "message": f"Run not found: {e}"})
            await self.close()
            return

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._context = await self._browser.new_context(
                viewport=VIEWPORT,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            self._page = await self._context.new_page()

            start_url = run_data.get("login_url") or run_data["competitor_url"]
            await self._page.goto(start_url, wait_until="domcontentloaded", timeout=30000)

            self._running = True
            self._screenshot_task = asyncio.create_task(self._screenshot_loop())
            self._timeout_task = asyncio.create_task(self._session_timeout())

        except Exception as e:
            logger.exception("Failed to launch browser session")
            await self.send_json({"type": "error", "message": str(e)[:500]})
            await self._cleanup()
            await self.close()

    async def receive_json(self, content, **kwargs):
        if not self._page or not self._running:
            return

        msg_type = content.get("type")

        try:
            if msg_type == "click":
                await self._page.mouse.click(content["x"], content["y"])
            elif msg_type == "dblclick":
                await self._page.mouse.dblclick(content["x"], content["y"])
            elif msg_type == "type":
                await self._page.keyboard.type(content["text"])
            elif msg_type == "keydown":
                await self._page.keyboard.press(content["key"])
            elif msg_type == "scroll":
                await self._page.mouse.wheel(
                    content.get("deltaX", 0),
                    content.get("deltaY", 0),
                )
            elif msg_type == "done":
                await self._finish_session()
        except Exception as e:
            logger.warning(f"Browser action failed ({msg_type}): {e}")

    async def disconnect(self, close_code):
        await self._cleanup()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _screenshot_loop(self):
        """Continuously capture and stream JPEG frames."""
        try:
            while self._running and self._page:
                try:
                    screenshot_bytes = await self._page.screenshot(
                        type="jpeg", quality=60,
                    )
                    frame_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
                    current_url = self._page.url
                    await self.send_json({
                        "type": "frame",
                        "data": frame_b64,
                        "url": current_url,
                    })
                except Exception:
                    # Page might be navigating; skip this frame
                    pass
                await asyncio.sleep(FRAME_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def _session_timeout(self):
        """Auto-close after SESSION_TIMEOUT seconds."""
        try:
            await asyncio.sleep(SESSION_TIMEOUT)
            if self._running:
                await self.send_json({
                    "type": "error",
                    "message": "Session timed out after 5 minutes.",
                })
                await self._finish_session()
        except asyncio.CancelledError:
            pass

    async def _finish_session(self):
        """Capture cookies, save to run, trigger recrawl, notify client."""
        if not self._running:
            return
        self._running = False

        cookies = []
        if self._context:
            try:
                cookies = await self._context.cookies()
            except Exception as e:
                logger.warning(f"Failed to capture cookies: {e}")

        await self._save_cookies(self.run_id, cookies)

        await self.send_json({
            "type": "session_complete",
            "cookie_count": len(cookies),
        })

        # Trigger recrawl in background
        await self._trigger_recrawl(self.run_id)

        await self._cleanup()
        await self.close()

    async def _cleanup(self):
        """Close browser and cancel background tasks."""
        self._running = False

        if self._screenshot_task and not self._screenshot_task.done():
            self._screenshot_task.cancel()
            try:
                await self._screenshot_task
            except asyncio.CancelledError:
                pass

        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
            try:
                await self._timeout_task
            except asyncio.CancelledError:
                pass

        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    # ------------------------------------------------------------------
    # Database helpers (sync → async bridge)
    # ------------------------------------------------------------------

    @database_sync_to_async
    def _get_run_data(self, run_id):
        from apps.runs.models import Run

        run = Run.objects.select_related("job").get(id=run_id)
        competitor = run.job.competitors.first()
        competitor_url = competitor.url if competitor else ""

        # Check if a login_url was stored in auth_credentials
        login_url = (run.auth_credentials or {}).get("login_url", "")

        return {
            "competitor_url": competitor_url,
            "login_url": login_url,
        }

    @database_sync_to_async
    def _save_cookies(self, run_id, cookies):
        from apps.runs.models import Run

        run = Run.objects.get(id=run_id)
        run.auth_cookies = cookies
        run.save(update_fields=["auth_cookies"])

    @database_sync_to_async
    def _trigger_recrawl(self, run_id):
        import threading
        from apps.runs.services.screenshot import recrawl_with_cookies

        thread = threading.Thread(target=recrawl_with_cookies, args=(run_id,))
        thread.daemon = True
        thread.start()
