"""`fetch_url` is allowed, so its destination check is the security boundary."""
from __future__ import annotations

import urllib.error

import pytest

from backend.tools import registry


def _addr(ip: str, port=None):
    return [(2, 1, 6, "", (ip, port or 80))]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/status",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://localhost:8000/",
        "http://user:pass@example.com/",
    ],
)
def test_non_public_destinations_are_refused(url):
    assert registry._is_public_http_url(url) is False
    assert "public" in registry._fetch_url_text(url)


@pytest.mark.parametrize("url", ["ftp://example.com/file", "file:///etc/passwd", ""])
def test_non_http_schemes_are_refused(url):
    assert registry._is_public_http_url(url) is False
    assert "http" in registry._fetch_url_text(url)


def test_a_public_host_is_allowed(monkeypatch):
    monkeypatch.setattr(
        registry.socket, "getaddrinfo", lambda *_a, **_kw: _addr("93.184.216.34")
    )
    assert registry._is_public_http_url("https://example.com/page") is True


def test_one_private_answer_rejects_the_whole_name(monkeypatch):
    answers = _addr("93.184.216.34") + _addr("10.1.2.3")
    monkeypatch.setattr(registry.socket, "getaddrinfo", lambda *_a, **_kw: answers)
    assert registry._is_public_http_url("https://mixed.example/") is False


def test_a_public_url_that_redirects_private_is_refused(monkeypatch):
    handler = registry._PublicRedirectHandler()
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(
            None, None, 302, "Found", {}, "http://169.254.169.254/latest/",
        )


def test_redirect_to_public_is_followed(monkeypatch):
    monkeypatch.setattr(
        registry.socket, "getaddrinfo", lambda *_a, **_kw: _addr("93.184.216.34")
    )
    handler = registry._PublicRedirectHandler()
    # `super()` needs a real Request to build the redirect; any object with the
    # fields HTTPRedirectHandler reads is enough for the URL check itself.
    req = registry.urllib.request.Request("https://example.com/")
    result = handler.redirect_request(
        req, None, 302, "Found", {}, "https://example.com/next",
    )
    assert result.full_url == "https://example.com/next"


# --------------------------------------------------------- browser surface --
# `browser_navigate` refuses a private target, but a click or a form submit
# can still navigate afterwards. The read surfaces re-check the live URL.


class _FakePage:
    def __init__(self, url: str, *, next_url: str | None = None):
        self.url = url
        self.next_url = next_url
        self.goto_calls: list[str] = []

    async def goto(self, url: str, **_kw) -> None:
        self.goto_calls.append(url)
        self.url = url

    async def click(self, _selector: str, **_kw) -> None:
        if self.next_url is not None:
            self.url = self.next_url

    async def title(self) -> str:
        raise AssertionError("a private page must never be read")

    async def inner_text(self, _selector: str) -> str:
        raise AssertionError("a private page must never be read")


def _install(monkeypatch, page):
    import asyncio

    from backend.tools import browser

    async def fake_page():
        return page

    monkeypatch.setattr(browser, "_ensure_page", fake_page)
    # A lock created outside the loop is fine: it never has to wait here.
    monkeypatch.setattr(browser, "_lock", asyncio.Lock())
    return browser


def test_a_private_page_cannot_be_snapshotted(monkeypatch):
    import asyncio

    page = _FakePage("http://169.254.169.254/latest/meta-data/")
    browser = _install(monkeypatch, page)

    out = asyncio.run(browser.snapshot())

    assert "blocked" in out.lower()
    assert page.goto_calls == ["about:blank"], "the private page is unloaded"
    assert page.url == "about:blank"


def test_a_click_that_lands_on_a_private_page_is_closed(monkeypatch):
    import asyncio

    page = _FakePage("https://example.com/", next_url="http://127.0.0.1:8000/api/status")
    browser = _install(monkeypatch, page)

    out = asyncio.run(browser.click("#continue"))

    assert "left the public" in out.lower()
    assert page.url == "about:blank"


def test_a_private_page_cannot_be_screenshotted(monkeypatch):
    import asyncio

    page = _FakePage("http://10.0.0.5/admin")
    browser = _install(monkeypatch, page)

    out = asyncio.run(browser.screenshot())

    assert "blocked" in out.lower()
    assert page.url == "about:blank"


def test_about_blank_is_not_treated_as_private():
    from backend.tools import browser

    assert browser._url_is_public("about:blank") is True
    assert browser._url_is_public("") is True
