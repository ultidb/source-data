"""core/registry.py: registration, lookup, unknown-id error."""
from datetime import date

import pytest

from core.registry import get_source, list_sources, register
from core.source import EventRef, Source


class _FakeSource(Source):
    id = "fake"

    def discover(self, year):
        return [EventRef(url="https://example.com", start_date=date(year, 1, 1))]

    def event_key(self, ref):
        return "fake-key"

    def fetch_event(self, ref, cache):
        return {"event": b"{}"}

    def parse_event(self, pages, ref, year):
        return None


class _OtherFakeSource(Source):
    id = "other-fake"

    def discover(self, year):
        return []

    def event_key(self, ref):
        return "other-key"

    def fetch_event(self, ref, cache):
        return {}

    def parse_event(self, pages, ref, year):
        return None


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """Every test gets an isolated registry so tests don't leak state into
    each other (or depend on sources/__init__.py having been imported)."""
    import core.registry as registry_module

    monkeypatch.setattr(registry_module, "_registry", {})
    yield


def test_register_and_get_source():
    source = _FakeSource()
    register(source)

    assert get_source("fake") is source


def test_list_sources_sorted_by_id():
    register(_OtherFakeSource())
    register(_FakeSource())

    ids = [s.id for s in list_sources()]
    assert ids == ["fake", "other-fake"]


def test_list_sources_empty_when_nothing_registered():
    assert list_sources() == []


def test_get_unknown_source_raises_with_available_ids_listed():
    register(_FakeSource())
    register(_OtherFakeSource())

    with pytest.raises(KeyError) as excinfo:
        get_source("nonexistent")

    message = str(excinfo.value)
    assert "nonexistent" in message
    assert "fake" in message
    assert "other-fake" in message


def test_get_unknown_source_when_registry_empty_says_none_registered():
    with pytest.raises(KeyError) as excinfo:
        get_source("anything")

    assert "none registered" in str(excinfo.value)


def test_register_without_id_raises():
    class _NoId(Source):
        id = ""

        def discover(self, year):
            return []

        def event_key(self, ref):
            return ""

        def fetch_event(self, ref, cache):
            return {}

        def parse_event(self, pages, ref, year):
            return None

    with pytest.raises(ValueError):
        register(_NoId())


def test_re_registering_same_id_overwrites():
    first = _FakeSource()
    register(first)

    class _FakeSourceV2(Source):
        id = "fake"

        def discover(self, year):
            return []

        def event_key(self, ref):
            return ""

        def fetch_event(self, ref, cache):
            return {}

        def parse_event(self, pages, ref, year):
            return None

    second = _FakeSourceV2()
    register(second)

    assert get_source("fake") is second
