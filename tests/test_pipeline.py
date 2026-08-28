"""Tests for core.pipeline.run_pipeline -- the shared discover -> fetch_event
-> parse_event -> tournament_to_document -> write_document -> post_documents
loop factored out of cli.py's _scrape_year_with_source so app.py's WFDF
scheduler jobs can drive it over a specific event subset too.

Uses sources/example (the fixture-backed reference Source, see
sources/README.md) rather than WFDF or a fake transport, since it needs no
network/cache plumbing at all -- keeping these tests focused on the loop
itself (event-failure isolation, post_documents wiring, refs override)
rather than re-proving WFDF's own fetch behaviour (that's tests/test_wfdf.py).
"""
from __future__ import annotations

import json
from typing import List, Optional

import pytest

import models
from core.pipeline import run_pipeline
from core.source import Cache, EventRef, FetchedPages, Source
from sources.example.source import ExampleSource

YEAR = 2026


class TestRunPipelineBasics:
    def test_writes_documents_under_out_dir(self, tmp_path):
        documents = run_pipeline(ExampleSource(), YEAR, out_dir=tmp_path)

        assert len(documents) == 1
        assert documents[0].source == "example"
        written = tmp_path / "data" / "example" / str(YEAR) / "2026__tiny-invite.json"
        assert written.exists()
        on_disk = json.loads(written.read_text(encoding="utf-8"))
        assert on_disk["source"] == "example"

    def test_refs_override_bypasses_discover(self, tmp_path):
        # Passing refs=[] short-circuits discover() entirely -- this is
        # what app.py uses to scope a run to e.g. "just ongoing events"
        # instead of a source's whole discover(year).
        documents = run_pipeline(ExampleSource(), YEAR, refs=[], out_dir=tmp_path)
        assert documents == []

    def test_refs_override_with_explicit_subset(self, tmp_path):
        source = ExampleSource()
        all_refs = source.discover(YEAR)
        assert len(all_refs) == 1

        documents = run_pipeline(source, YEAR, refs=all_refs, out_dir=tmp_path)
        assert len(documents) == 1


class TestRunPipelinePosting:
    def test_posts_documents_when_requested(self, tmp_path, monkeypatch):
        captured = {}

        def fake_post_documents(documents, *, source, api_url, token=None, **kwargs):
            captured["documents"] = list(documents)
            captured["source"] = source
            captured["api_url"] = api_url
            captured["token"] = token
            return {"runId": 1, "status": "queued", "documentCount": len(documents)}

        monkeypatch.setattr("core.pipeline.post_documents", fake_post_documents)

        documents = run_pipeline(
            ExampleSource(),
            YEAR,
            out_dir=tmp_path,
            post=True,
            api_url="http://api.example",
            ingest_token="secret-token",
        )

        assert len(documents) == 1
        assert captured["source"] == "example"
        assert captured["api_url"] == "http://api.example"
        assert captured["token"] == "secret-token"
        assert captured["documents"] == documents

    def test_post_without_api_url_raises(self, tmp_path):
        with pytest.raises(ValueError):
            run_pipeline(ExampleSource(), YEAR, out_dir=tmp_path, post=True)

    def test_does_not_post_when_not_requested(self, tmp_path, monkeypatch):
        def fail_post_documents(*args, **kwargs):
            raise AssertionError("post_documents should not be called")

        monkeypatch.setattr("core.pipeline.post_documents", fail_post_documents)

        run_pipeline(ExampleSource(), YEAR, out_dir=tmp_path, post=False)


class _FlakySource(Source):
    """A minimal Source whose fetch_event raises for one ref and succeeds
    for the rest, to prove run_pipeline isolates per-event failures."""

    id = "flaky"

    def __init__(self, refs: List[EventRef], failing_urls: set):
        self._refs = refs
        self._failing_urls = failing_urls

    def discover(self, year: int) -> List[EventRef]:
        return self._refs

    def event_key(self, ref: EventRef) -> str:
        return ref.url.rsplit("/", 1)[-1]

    def fetch_event(self, ref: EventRef, cache: Cache) -> FetchedPages:
        if ref.url in self._failing_urls:
            raise RuntimeError(f"simulated fetch failure for {ref.url}")
        return {"event": b"{}"}

    def parse_event(
        self, pages: FetchedPages, ref: EventRef, year: int
    ) -> Optional["models.Tournament"]:
        return models.Tournament(
            ref.name or "Untitled",
            ref.url,
            ref.city,
            ref.state,
            "2026-08-15",
            "2026-08-16",
            [],
            None,
            ref.division or "",
            [],
        )


class TestRunPipelineFailureIsolation:
    def test_one_event_failing_does_not_abort_the_others(self, tmp_path, caplog):
        good_ref = EventRef(url="http://example/good", name="Good Event", division="Open")
        bad_ref = EventRef(url="http://example/bad", name="Bad Event", division="Open")
        source = _FlakySource([bad_ref, good_ref], failing_urls={"http://example/bad"})

        with caplog.at_level("ERROR"):
            documents = run_pipeline(source, YEAR, out_dir=tmp_path)

        assert len(documents) == 1
        assert documents[0].source_event_id == "good"
        assert any("bad" in r.message for r in caplog.records)

    def test_parse_event_returning_none_is_skipped_not_fatal(self, tmp_path):
        class NoneParsingSource(_FlakySource):
            def parse_event(self, pages, ref, year):
                return None

        ref = EventRef(url="http://example/x", name="X", division="Open")
        source = NoneParsingSource([ref], failing_urls=set())

        documents = run_pipeline(source, YEAR, out_dir=tmp_path)
        assert documents == []
