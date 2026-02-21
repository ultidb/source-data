import pytest
from pathlib import Path


@pytest.fixture
def fixtures_path():
    """Return path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def html_fixtures_path(fixtures_path):
    """Return path to HTML fixtures directory."""
    return fixtures_path / "html"
