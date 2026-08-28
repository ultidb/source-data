"""Shared cross-language fixture corpus (CONTRACT.md section 8).

tests/fixtures/documents/valid/*.json must validate against core.schema.Document.
tests/fixtures/documents/invalid/*.json must fail validation, each with a
sibling <name>.reason.txt naming the defect (Go's mirror of this test lives
in api/etl/ and reaches this same directory via a relative path).
"""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.schema import Document

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "documents"
VALID_DIR = FIXTURES_DIR / "valid"
INVALID_DIR = FIXTURES_DIR / "invalid"


def _json_files(directory: Path):
    return sorted(directory.glob("*.json"))


VALID_FIXTURES = _json_files(VALID_DIR)
INVALID_FIXTURES = _json_files(INVALID_DIR)


def test_fixture_directories_are_populated():
    assert len(VALID_FIXTURES) >= 4, "expected at least 4 valid fixtures"
    assert len(INVALID_FIXTURES) >= 4, "expected at least 4 invalid fixtures"


@pytest.mark.parametrize("path", VALID_FIXTURES, ids=lambda p: p.stem)
def test_valid_fixture_validates(path: Path):
    data = json.loads(path.read_text())
    Document.model_validate(data)  # must not raise


@pytest.mark.parametrize("path", INVALID_FIXTURES, ids=lambda p: p.stem)
def test_invalid_fixture_raises(path: Path):
    data = json.loads(path.read_text())
    with pytest.raises(ValidationError):
        Document.model_validate(data)


@pytest.mark.parametrize("path", INVALID_FIXTURES, ids=lambda p: p.stem)
def test_invalid_fixture_has_reason_file(path: Path):
    reason_path = path.with_suffix("").with_suffix(".reason.txt")
    assert reason_path.exists(), f"missing {reason_path.name} for {path.name}"
    assert reason_path.read_text().strip(), f"{reason_path.name} is empty"
