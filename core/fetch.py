"""Transport policies: callables `fetch(url) -> bytes` usable as the
`transport` argument to `core.cache.FileCache`, per the "fetch_event being
source-owned" decision in MULTI-SOURCE-REDESIGN.md.
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; UltiverseBot/1.0; +https://ulti-verse.com)"


class RequestsTransport:
    """Plain `requests`-based transport for sources that don't need to defeat
    bot protection (e.g. WFDF). `fetch(url) -> bytes`."""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        self._headers = {"User-Agent": user_agent}
        self._timeout = timeout
        self._max_retries = max_retries

    def __call__(self, url: str) -> bytes:
        last_exc: Exception = RuntimeError(f"no attempts made fetching {url}")
        for attempt in range(1, self._max_retries + 1):
            try:
                response = requests.get(url, headers=self._headers, timeout=self._timeout)
                response.raise_for_status()
                return response.content
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                log.warning(
                    "RequestsTransport fetch failed (attempt %d/%d) for %s: %s",
                    attempt,
                    self._max_retries,
                    url,
                    exc,
                )
                if attempt < self._max_retries:
                    time.sleep(2 ** (attempt - 1))
        raise last_exc


class SeleniumTorTransport:
    """Adapter over `scrape.py`'s singleton headless-Chrome-through-Tor stack.

    `scrape.py` owns module-level singleton state (`getSeleniumDriver` /
    `cleanupSeleniumDriver`, an `atexit`-registered Tor process started via
    `tor.py`) that exists purely to defeat USAU's bot protection, plus
    `makeProxiedRequestSelenium(url)`, which drives that singleton, retries
    up to 3 times, validates the loaded page's tournament slug against the
    requested URL, and returns page bytes.

    That stack is real, working, USAU-specific process/global state --
    decoupling it into something reusable would mean editing scrape.py
    (splitting driver lifecycle from the USAU-slug-validation retry loop),
    which is out of scope for this phase (scrape.py is untouched until
    Phase 3). Since `fetch_event` is source-owned per the Source contract,
    this class is a thin, lazily-importing adapter that delegates the actual
    work to the existing function unchanged -- no logic is duplicated here.
    """

    def __call__(self, url: str) -> bytes:
        from scrape import makeProxiedRequestSelenium

        return makeProxiedRequestSelenium(url)
