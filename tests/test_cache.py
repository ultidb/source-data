"""Tests for core.cache.FileCache -- staleness policy (max_age), refresh,
and age(). All offline: mtimes are controlled explicitly via os.utime
rather than sleeping, keeping the suite fast.
"""
from __future__ import annotations

import os
import time

import pytest

from core.cache import FileCache


def _transport_counting(calls):
    def transport(url: str) -> bytes:
        calls.append(url)
        return f"content-for-{url}".encode("utf-8")

    return transport


def _make_cache(tmp_path, calls):
    return FileCache("testsrc", 2026, "event", _transport_counting(calls), base_dir=tmp_path)


def _age_key(cache, key: str, seconds: float) -> None:
    """Backdate the cached entry for `key` by `seconds`, so age() reports
    roughly that value."""
    path = cache._path_for(key)
    now = time.time()
    os.utime(path, (now - seconds, now - seconds))


class TestNoCachedEntry:
    def test_fetches_when_nothing_cached(self, tmp_path):
        calls = []
        cache = _make_cache(tmp_path, calls)

        content = cache.fetch("k", "http://example/k")

        assert content == b"content-for-http://example/k"
        assert calls == ["http://example/k"]
        assert cache.get("k") == content

    def test_age_is_none_when_absent(self, tmp_path):
        calls = []
        cache = _make_cache(tmp_path, calls)
        assert cache.age("missing") is None


class TestMaxAge:
    def test_fresh_entry_served_from_cache(self, tmp_path):
        calls = []
        cache = _make_cache(tmp_path, calls)
        cache.put("k", b"cached-bytes")
        _age_key(cache, "k", seconds=10)

        content = cache.fetch("k", "http://example/k", max_age=3600)

        assert content == b"cached-bytes"
        assert calls == []  # no network call

    def test_stale_entry_is_refetched_and_rewritten(self, tmp_path):
        calls = []
        cache = _make_cache(tmp_path, calls)
        cache.put("k", b"old-bytes")
        _age_key(cache, "k", seconds=7200)  # 2h old

        content = cache.fetch("k", "http://example/k", max_age=3600)  # 1h TTL

        assert content == b"content-for-http://example/k"
        assert calls == ["http://example/k"]
        assert cache.get("k") == content  # rewritten on disk

    def test_age_just_under_max_age_is_still_fresh(self, tmp_path):
        # A few seconds' margin below the exact boundary, since age is
        # computed from wall-clock time between os.utime() and fetch() --
        # an exact age == max_age assertion would be racy.
        calls = []
        cache = _make_cache(tmp_path, calls)
        cache.put("k", b"cached-bytes")
        _age_key(cache, "k", seconds=3595)

        content = cache.fetch("k", "http://example/k", max_age=3600)

        assert content == b"cached-bytes"
        assert calls == []

    def test_age_just_over_max_age_is_stale(self, tmp_path):
        calls = []
        cache = _make_cache(tmp_path, calls)
        cache.put("k", b"cached-bytes")
        _age_key(cache, "k", seconds=3605)

        content = cache.fetch("k", "http://example/k", max_age=3600)

        assert content == b"content-for-http://example/k"
        assert calls == ["http://example/k"]

    def test_no_max_age_means_any_cached_entry_is_fresh(self, tmp_path):
        calls = []
        cache = _make_cache(tmp_path, calls)
        cache.put("k", b"cached-bytes")
        _age_key(cache, "k", seconds=10_000_000)  # ancient

        content = cache.fetch("k", "http://example/k")  # max_age=None (default)

        assert content == b"cached-bytes"
        assert calls == []


class TestRefresh:
    def test_refresh_true_always_fetches_even_if_fresh(self, tmp_path):
        calls = []
        cache = _make_cache(tmp_path, calls)
        cache.put("k", b"cached-bytes")
        _age_key(cache, "k", seconds=1)

        content = cache.fetch("k", "http://example/k", refresh=True, max_age=3600)

        assert content == b"content-for-http://example/k"
        assert calls == ["http://example/k"]

    def test_refresh_true_with_no_cached_entry_still_fetches(self, tmp_path):
        calls = []
        cache = _make_cache(tmp_path, calls)

        content = cache.fetch("k", "http://example/k", refresh=True)

        assert content == b"content-for-http://example/k"
        assert calls == ["http://example/k"]


class TestAge:
    def test_age_reflects_mtime(self, tmp_path):
        calls = []
        cache = _make_cache(tmp_path, calls)
        cache.put("k", b"bytes")
        _age_key(cache, "k", seconds=42)

        age = cache.age("k")

        assert age == pytest.approx(42, abs=1.0)

    def test_age_after_put_is_near_zero(self, tmp_path):
        calls = []
        cache = _make_cache(tmp_path, calls)
        cache.put("k", b"bytes")

        assert cache.age("k") == pytest.approx(0, abs=1.0)
