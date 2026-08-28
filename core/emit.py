"""Writes a `Document` to `data/<source>/<year>/<key>.json`.

Files here are reviewed as git diffs (see MULTI-SOURCE-REDESIGN.md's
"Repo layout" -- `data/<source>/<year>/*.json` is the git-archived half of
"Versioned JSON, git-archived and POSTed"), so formatting stability matters:
2-space indent, unicode left as-is (no \\uXXXX escaping), insertion-order
keys (i.e. schema field order, not alphabetical), and a trailing newline.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from core.schema import Document

# source-data/ repo root, computed from this file's location rather than
# hardcoded, so this works regardless of where the repo is checked out.
_REPO_ROOT = Path(__file__).resolve().parent.parent

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_key(source_event_id: str) -> str:
    """sourceEventId's are slash-namespaced (e.g. "Swan-Boat-2025/Club-Men");
    turn that into a single safe filename component."""
    key = source_event_id.replace("/", "__")
    key = _UNSAFE_CHARS.sub("", key)
    if not key:
        raise ValueError(f"sourceEventId sanitizes to an empty key: {source_event_id!r}")
    return key


def write_document(doc: Document, *, base_dir: Optional[Path] = None) -> Path:
    """Write `doc` to data/<source>/<year>/<key>.json under `base_dir`
    (defaults to the source-data repo root) and return the path written."""
    base = Path(base_dir) if base_dir is not None else _REPO_ROOT
    year = doc.event.start_date.year
    key = _sanitize_key(doc.source_event_id)

    path = base / "data" / doc.source / str(year) / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = doc.model_dump(by_alias=True, mode="json")
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    path.write_text(text, encoding="utf-8")
    return path
