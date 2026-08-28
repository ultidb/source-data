"""Source registry: source id -> `core.source.Source` instance.

Discovery is explicit, not filesystem magic: `sources/__init__.py` imports
each source module and calls `register()` on it. Nothing here scans
directories or imports by convention -- see `sources/README.md`.
"""
from __future__ import annotations

from typing import Dict, List

from core.source import Source

_registry: Dict[str, Source] = {}


def register(source: Source) -> None:
    """Register `source` under `source.id`. Re-registering the same id
    overwrites the previous entry (useful for tests); production code should
    only ever register each id once, from `sources/__init__.py`."""
    source_id = getattr(source, "id", None)
    if not source_id:
        raise ValueError(f"source {source!r} has no non-empty `id` attribute")
    _registry[source_id] = source


def get_source(source_id: str) -> Source:
    """Look up a registered source by id.

    Raises KeyError naming the available ids if `source_id` isn't
    registered -- callers (CLI, tests) can surface that message directly.
    """
    try:
        return _registry[source_id]
    except KeyError:
        available = ", ".join(sorted(_registry)) or "(none registered)"
        raise KeyError(
            f"unknown source id {source_id!r}; available sources: {available}"
        ) from None


def list_sources() -> List[Source]:
    """All registered sources, sorted by id."""
    return [s for _, s in sorted(_registry.items())]
