"""Transport policies: callables `fetch(url) -> bytes` usable as the
`transport` argument to `core.cache.FileCache`, per the "fetch_event being
source-owned" decision in MULTI-SOURCE-REDESIGN.md.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Tuple

import requests

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; UltiverseBot/1.0; +https://ulti-verse.com)"

# Randomised pause between consecutive requests, in seconds. A full WFDF
# scrape is ~138 requests (reference + games + one roster call per team), and
# issuing those back to back is both a needless burst of load on someone
# else's server and trivially identifiable as automated. Pacing them out with
# jitter keeps the crawl gentle and unremarkable.
DEFAULT_DELAY_RANGE: Tuple[float, float] = (0.0, 0.0)


class RequestsTransport:
    """Plain `requests`-based transport for sources that don't need to defeat
    bot protection (e.g. WFDF). `fetch(url) -> bytes`.

    `delay_range` is a `(min, max)` pause in seconds applied *between*
    requests, drawn uniformly at random each time. The wait is measured from
    the end of the previous request, so a slow response counts toward it
    rather than being added to it, and the very first request is never
    delayed. Defaults to no delay so existing callers are unaffected;
    individual sources opt in via `Source.make_transport()`.
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 30,
        max_retries: int = 3,
        delay_range: Tuple[float, float] = DEFAULT_DELAY_RANGE,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        low, high = delay_range
        if low < 0 or high < 0:
            raise ValueError(f"delay_range must be non-negative, got {delay_range!r}")
        if low > high:
            raise ValueError(f"delay_range min must be <= max, got {delay_range!r}")

        self._headers = {"User-Agent": user_agent}
        self._timeout = timeout
        self._max_retries = max_retries
        self._delay_range = (low, high)
        # Injectable so tests can assert on pacing without actually sleeping.
        self._sleep = sleep
        self._clock = clock
        self._last_request_at = None

    def _throttle(self) -> float:
        """Pause so that at least a random delay has elapsed since the last
        request. Returns the number of seconds actually slept."""
        low, high = self._delay_range
        if high <= 0 or self._last_request_at is None:
            return 0.0

        target = random.uniform(low, high)
        remaining = target - (self._clock() - self._last_request_at)
        if remaining <= 0:
            return 0.0

        log.debug("throttling %.2fs before next request", remaining)
        self._sleep(remaining)
        return remaining

    def __call__(self, url: str) -> bytes:
        last_exc: Exception = RuntimeError(f"no attempts made fetching {url}")
        for attempt in range(1, self._max_retries + 1):
            self._throttle()
            try:
                response = requests.get(url, headers=self._headers, timeout=self._timeout)
                # Back off politely when explicitly asked to, rather than
                # burning the remaining retries at full speed.
                if response.status_code in (429, 503):
                    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                    if retry_after is not None and attempt < self._max_retries:
                        log.warning(
                            "%s returned %d, honouring Retry-After: %.1fs",
                            url,
                            response.status_code,
                            retry_after,
                        )
                        self._sleep(retry_after)
                        continue
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
                    self._sleep(2 ** (attempt - 1))
            finally:
                self._last_request_at = self._clock()
        raise last_exc


def _parse_retry_after(value):
    """Retry-After as delta-seconds. HTTP-date form is ignored (returns None)
    -- servers that rate limit overwhelmingly use the numeric form."""
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, seconds)


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
