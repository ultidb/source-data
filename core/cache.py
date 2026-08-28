"""FileCache: the `core.source.Cache` protocol implementation, storing raw
page bytes under `cache/<source>/<year>/<event_key>/<page>.{html,json}`.

The extension is picked per entry by sniffing the content -- a source like
WFDF hands back JSON API responses, not markup, and writing those out as
`.html` misdescribes what's on disk. `.json` is used when the content
parses as JSON, `.html` otherwise (which covers USAU's real HTML pages).

The actual HTTP fetching is delegated to a `transport` callable
(`fetch(url) -> bytes`) supplied at construction time, so the transport
stays swappable (see core/fetch.py) and this class can be exercised in tests
with a fake transport and no network access.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Anything other than a conservative filename-safe set gets stripped, and
# ".."/absolute-path segments are dropped entirely below -- this is what
# keeps a malicious or buggy `key` from escaping the cache root.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]")

_EXTENSIONS = ("json", "html")


def _extension_for(content: bytes) -> str:
    """Returns "json" if `content` parses as JSON, "html" otherwise
    (extension only, no leading dot)."""
    try:
        json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "html"
    return "json"


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
    (cache/<source>/<year>/<event_key>/*.{html,json})."""

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
        self._root = base / "cache" / source / str(year) / _sanitize_key(event_key)

    def _path_for(self, key: str) -> Path:
        """Path to `key`'s cached entry. Without knowing the content, this
        is a lookup: the extension is whichever of `.json`/`.html` actually
        exists on disk, preferring `.json` if (implausibly) both do, and
        falling back to the `.html` path -- possibly nonexistent -- when
        neither is present."""
        safe = _sanitize_key(key)
        for ext in _EXTENSIONS:
            candidate = self._root / f"{safe}.{ext}"
            if candidate.exists():
                return candidate
        return self._root / f"{safe}.html"

    def get(self, key: str) -> Optional[bytes]:
        path = self._path_for(key)
        if not path.exists():
            return None
        return path.read_bytes()

    def put(self, key: str, content: bytes) -> None:
        safe = _sanitize_key(key)
        ext = _extension_for(content)
        path = self._root / f"{safe}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        # Drop a stale entry under the other extension, e.g. a key that
        # used to hold HTML now holding JSON (or vice versa) -- otherwise
        # `_path_for`'s existence-based lookup could resurrect old content.
        for other_ext in _EXTENSIONS:
            if other_ext == ext:
                continue
            stale = self._root / f"{safe}.{other_ext}"
            if stale.exists():
                stale.unlink()

    def age(self, key: str) -> Optional[float]:
        """Seconds since `key` was last written, or None if there is no
        cached entry."""
        path = self._path_for(key)
        if not path.exists():
            return None
        return time.time() - path.stat().st_mtime

    def fetch(
        self, key: str, url: str, *, refresh: bool = False, max_age: Optional[float] = None
    ) -> bytes:
        """Serve `key` from cache unless `refresh` is set or the cached
        entry is older than `max_age` seconds (None means "no staleness
        limit" -- any cached entry is fresh enough, matching the pre-TTL
        behaviour)."""
        if not refresh:
            age = self.age(key)
            if age is not None and (max_age is None or age <= max_age):
                cached = self.get(key)
                if cached is not None:
                    return cached

        content = self._transport(url)
        self.put(key, content)
        return content
