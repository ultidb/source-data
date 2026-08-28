"""End-to-end pipeline test for sources/example/, the fixture-backed
reference Source (sources/README.md).

Runs the entire plugin path offline: discover -> fetch_event -> parse_event
-> tournament_to_document -> pydantic validation -> write_document -> read
the file back -> re-validate -- asserting the JSON on disk is exactly what
the schema produces. Everything is written under tmp_path, never into the
repo's real data/ or cache/ directories.
"""
import json

from core.cache import FileCache
from core.emit import write_document
from core.schema import Document
from core.serialize import tournament_to_document
from sources.example.source import ExampleSource

YEAR = 2026


def _unused_transport(url: str) -> bytes:
    raise AssertionError(f"transport should not be called for a fixture-backed source, got url={url!r}")


def test_full_pipeline_discover_to_disk_and_back(tmp_path):
    source = ExampleSource()

    # discover
    refs = source.discover(YEAR)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.division == "Club - Mixed"
    assert ref.city == "Example City"

    # fetch_event (through a real FileCache, rooted at tmp_path so nothing
    # touches the repo's real cache/ directory)
    key = source.event_key(ref)
    assert key == f"{YEAR}/tiny-invite"
    cache = FileCache("example", YEAR, key, _unused_transport, base_dir=tmp_path)
    pages = source.fetch_event(ref, cache)
    assert "event" in pages
    # fetch_event warms the cache even though it didn't need to fetch
    # over the network -- confirm that actually happened.
    assert cache.get("event") == pages["event"]

    # parse_event
    tournament = source.parse_event(pages, ref, YEAR)
    assert tournament is not None
    assert len(tournament.teams) == 2
    assert len(tournament.stages) == 2  # pools + brackets

    # tournament_to_document (-> pydantic validation happens at construction)
    doc = tournament_to_document(
        tournament,
        source=source.id,
        source_event_id=key,
        source_url=ref.url,
    )
    assert isinstance(doc, Document)
    assert doc.source == "example"
    assert doc.source_event_id == f"{YEAR}/tiny-invite"

    # explicit re-validation of the dumped payload, independent of the
    # validation that already happened at construction time
    dumped = doc.model_dump(by_alias=True, mode="json")
    Document.model_validate(dumped)  # must not raise

    # write_document, rooted at tmp_path -- never the repo's real data/
    written_path = write_document(doc, base_dir=tmp_path)
    assert written_path == tmp_path / "data" / "example" / str(YEAR) / "2026__tiny-invite.json"
    assert written_path.exists()

    # read the file back and re-validate
    on_disk = json.loads(written_path.read_text(encoding="utf-8"))
    assert on_disk == dumped  # exactly what the schema produced, byte for byte in content
    Document.model_validate(on_disk)  # must not raise

    # spot-check a few fields survived the whole trip
    team_names = sorted(t["name"] for t in on_disk["teams"])
    assert team_names == ["Lakeside Legends", "Riverside Rovers"]

    stage_types = sorted(s["type"] for s in on_disk["stages"])
    assert stage_types == ["brackets", "pools"]

    pool_stage = next(s for s in on_disk["stages"] if s["type"] == "pools")
    assert pool_stage["groups"][0]["games"][0]["score1"] == 13

    bracket_stage = next(s for s in on_disk["stages"] if s["type"] == "brackets")
    assert bracket_stage["groups"][0]["games"][0]["round"] == "Final"


def test_discover_unknown_year_returns_empty_list():
    source = ExampleSource()
    assert source.discover(1999) == []
