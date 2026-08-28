"""Format equivalence (MULTI-SOURCE-REDESIGN.md, testing strategy item 2 --
"the property that makes Phase 3/4 safe"):

    CSV --(legacy reader)--------------------------------------> Document
                                                                     ||  deep-equal
    CSV --(legacy reader)--> Document --(document_to_tournament)--> Tournament
                                       --(tournament_to_document)--> Document

`core/legacy.py`'s `read_legacy_csv` builds a wire-format `Document` directly
from CSV rows -- a translator that never touches `models.Tournament`. A live
scrape, by contrast, always goes CSV-shaped-source -> `models.Tournament` ->
`core.serialize.tournament_to_document` -> `Document`. This test proves those
two roads agree: round-tripping a legacy-derived `Document` through
`document_to_tournament` and back through `tournament_to_document` must
reproduce it exactly. If it didn't, retiring `core/legacy.py` in favor of the
model-based path (Phase 4) could silently change historical data.

`source`/`source_event_id`/`source_url`/`scraped_at` aren't recoverable from
`models.Tournament` alone (see core/serialize.py) -- they're threaded through
from the original document on the forward call, same as any real caller of
tournament_to_document would supply them. Every other field must match
exactly; core/serialize.py's module docstring covers why nothing else is
lossy for legacy-shaped input (no True is_championship, no blank Team.id/url,
in `models.py`'s vocabulary -- the legacy format can't express those in the
first place).

Runs over the entire csv/2014..2026 corpus, offline, no database. A CSV that
fails to parse at all (a pre-existing corpus defect -- see test_legacy.py's
TestRealCorpusSample) is skipped here, not a failure of this property: there
is no Document to round-trip in that case.
"""
from pathlib import Path

import pytest

from core.legacy import iter_legacy_csv_files, read_legacy_csv
from core.serialize import document_to_tournament, tournament_to_document

REPO_ROOT = Path(__file__).parent.parent
CSV_ROOT = REPO_ROOT / "csv"


def _round_trip(doc):
    tournament = document_to_tournament(doc)
    return tournament_to_document(
        tournament,
        source=doc.source,
        source_event_id=doc.source_event_id,
        source_url=doc.source_url,
        scraped_at=doc.scraped_at,
    )


@pytest.mark.skipif(not CSV_ROOT.exists(), reason="csv/ corpus not present")
def test_legacy_document_survives_model_round_trip_across_full_corpus():
    paths = [p for p in iter_legacy_csv_files(CSV_ROOT) if p.name != "_calendar.csv"]
    assert len(paths) > 1000, "expected the full multi-year corpus, not a subset"

    parsed = 0
    unparseable = 0
    mismatches = []

    for path in paths:
        try:
            doc = read_legacy_csv(path)
        except Exception:  # noqa: BLE001 -- pre-existing corpus defects, not this property
            unparseable += 1
            continue
        parsed += 1

        back = _round_trip(doc)
        if back != doc:
            mismatches.append(path)

    assert parsed > 1000, f"expected most of {len(paths)} files to parse; only {parsed} did"
    assert not mismatches, (
        f"{len(mismatches)}/{parsed} legacy documents did not survive the "
        f"models.Tournament round trip: {[str(p) for p in mismatches[:10]]}"
        + (" ..." if len(mismatches) > 10 else "")
    )
