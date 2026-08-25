"""Headless Chromium computer-use via Playwright. Optional — degrades if missing."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

_playwright = None
_browser = None
_context = None
_page = None
_lock = asyncio.Lock()
LAST_URL = ""
LAST_ERROR = ""


def enabled() -> bool:
    flag = (os.environ.get("SWARM_BROWSER") or "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def _unavailable(detail: str = "") -> str:
    extra = f" {detail}" if detail else ""
    return (
        "(browser-use unavailable — pip install playwright && playwright install chromium."
        f"{extra})"
    )


def status() -> dict[str, Any]:
    return {
        "enabled": enabled(),
        "ready": _page is not None,
        "url": LAST_URL,
        "error": LAST_ERROR,
        "engine": "playwright-chromium",
    }


async def _ensure_page():
    global _playwright, _browser, _context, _page, LAST_ERROR
    if not enabled():
        raise RuntimeError("SWARM_BROWSER=0")
    if _page is not None:
        return _page
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        LAST_ERROR = "playwright not installed"
        raise RuntimeError("playwright not installed") from exc
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=True)
    _context = await _browser.new_context(viewport={"width": 1280, "height": 800})
    _page = await _context.new_page()
    LAST_ERROR = ""
    return _page


async def close() -> str:
    global _playwright, _browser, _context, _page, LAST_URL
    async with _lock:
        try:
            if _context:
                await _context.close()
            if _browser:
                await _browser.close()
            if _playwright:
                await _playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        _playwright = _browser = _context = _page = None
        LAST_URL = ""
    return "browser closed"


async def navigate(url: str) -> str:
    global LAST_URL, LAST_ERROR
    target = (url or "").strip()
    if not target.startswith(("http://", "https://")):
        return "(url must start with http:// or https://)"
    async with _lock:
        try:
            page = await _ensure_page()
            await page.goto(target, wait_until="domcontentloaded", timeout=20_000)
            LAST_URL = page.url
            title = await page.title()
            return f"opened {LAST_URL} — {title}"
        except Exception as exc:  # noqa: BLE001
            LAST_ERROR = str(exc)
            if "playwright" in str(exc).lower() or "SWARM_BROWSER" in str(exc):
                return _unavailable(str(exc))
            return f"(browser navigate error: {exc})"


def navigate_sync(url: str) -> str:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(navigate(url))
    # Already inside the agent event loop — schedule and wait via a helper future.
    fut = asyncio.ensure_future(navigate(url), loop=loop)
    # Can't block the running loop; return a note that the caller should use browser_navigate.
    if not fut.done():
        return (
            f"(queued browser open of {url} — use browser_navigate from the agent loop; "
            "computer_open of http URLs is best-effort here)"
        )
    return fut.result()


async def snapshot() -> str:
    async with _lock:
        try:
            page = await _ensure_page()
            LAST_URL = page.url
            title = await page.title()
            text = await page.inner_text("body")
            links = await page.eval_on_selector_all(
                "a[href]",
                "els => els.slice(0, 30).map(a => `${a.innerText.trim() || '(link)'} -> ${a.href}`)",
            )
            body = (text or "").strip()[:4000]
            link_lines = "\n".join(links or [])
            return (
                f"url: {LAST_URL}\ntitle: {title}\n\n"
                f"{body or '(empty page)'}\n\nlinks:\n{link_lines or '(none)'}"
            )
        except Exception as exc:  # noqa: BLE001
            return _unavailable(str(exc)) if "playwright" in str(exc).lower() else f"(browser snapshot error: {exc})"


async def click(selector: str) -> str:
    sel = (selector or "").strip()
    if not sel:
        return "(need a CSS selector)"
    async with _lock:
        try:
            page = await _ensure_page()
            await page.click(sel, timeout=8_000)
            LAST_URL = page.url
            return f"clicked {sel} — now {LAST_URL}"
        except Exception as exc:  # noqa: BLE001
            return f"(browser click error: {exc})"


async def type_text(selector: str, text: str) -> str:
    sel = (selector or "").strip()
    if not sel:
        return "(need a CSS selector)"
    async with _lock:
        try:
            page = await _ensure_page()
            await page.fill(sel, text or "")
            return f"typed {len(text or '')} chars into {sel}"
        except Exception as exc:  # noqa: BLE001
            return f"(browser type error: {exc})"


async def press(key: str) -> str:
    name = (key or "").strip() or "Enter"
    async with _lock:
        try:
            page = await _ensure_page()
            await page.keyboard.press(name)
            return f"pressed {name}"
        except Exception as exc:  # noqa: BLE001
            return f"(browser press error: {exc})"


async def wait(ms: int = 1000) -> str:
    delay = max(0, min(int(ms or 0), 10_000))
    await asyncio.sleep(delay / 1000)
    return f"waited {delay}ms"


async def screenshot() -> str:
    from .computer import sandbox_dir
    async with _lock:
        try:
            page = await _ensure_page()
            folder = sandbox_dir() / "screenshots"
            folder.mkdir(parents=True, exist_ok=True)
            name = f"browser-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.png"
            dest = folder / name
            await page.screenshot(path=str(dest), full_page=False)
            LAST_URL = page.url
            return f"saved screenshots/{name} ({dest.stat().st_size} bytes) of {LAST_URL}"
        except Exception as exc:  # noqa: BLE001
            return _unavailable(str(exc)) if "playwright" in str(exc).lower() else f"(browser screenshot error: {exc})"


def screenshot_if_open() -> str | None:
    if _page is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(screenshot())
    if loop.is_running():
        return None
    return loop.run_until_complete(screenshot())
