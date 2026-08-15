"""FileCache: the `core.source.Cache` protocol implementation, storing raw
page bytes under `html/<source>/<year>/<event_key>/<page>.html`.

The actual HTTP fetching is delegated to a `transport` callable
(`fetch(url) -> bytes`) supplied at construction time, so the transport
stays swappable (see core/fetch.py) and this class can be exercised in tests
with a fake transport and no network access.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Anything other than a conservative filename-safe set gets stripped, and
# ".."/absolute-path segments are dropped entirely below -- this is what
# keeps a malicious or buggy `key` from escaping the cache root.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]")


def _sanitize_key(key: str) -> str:
    """Turn an arbitrary cache key into a safe relative path (no leading
    slash, no `..` traversal)."""
    key = key.replace("\\", "/")
    segments = [s for s in key.split("/") if s not in ("", ".", "..")]
    segments = [_UNSAFE_CHARS.sub("_", s) for s in segments]
    if not segments:
        raise ValueError(f"cache key sanitizes to an empty path: {key!r}")
    return "/".join(segments)


class FileCache:
    """Cache protocol implementation for one event
    (html/<source>/<year>/<event_key>/*.html)."""

    def __init__(
        self,
        source: str,
        year: int,
        event_key: str,
        transport: Callable[[str], bytes],
        *,
        base_dir: Optional[Path] = None,
    ):
        self._transport = transport
        base = Path(base_dir) if base_dir is not None else _REPO_ROOT
        self._root = base / "html" / source / str(year) / _sanitize_key(event_key)

    def _path_for(self, key: str) -> Path:
        return self._root / f"{_sanitize_key(key)}.html"

    def get(self, key: str) -> Optional[bytes]:
        path = self._path_for(key)
        if not path.exists():
            return None
        return path.read_bytes()

    def put(self, key: str, content: bytes) -> None:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def fetch(self, key: str, url: str, *, refresh: bool = False) -> bytes:
        if not refresh:
            cached = self.get(key)
            if cached is not None:
                return cached

        content = self._transport(url)
        self.put(key, content)
        return content
