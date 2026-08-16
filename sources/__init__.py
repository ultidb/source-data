"""Explicit source registration (see sources/README.md).

Importing this package registers every known source with core.registry.
This is deliberately explicit rather than filesystem-magic (e.g.
auto-importing every sources/<id>/ directory found on disk) -- this
codebase favors code you can grep for over import-time discovery.

To add a new source:
  1. Write sources/<id>/source.py implementing core.source.Source.
  2. Import it and call register() below.

That's it -- cli.py's `scrape --source=<id>` and `sources` commands work
against whatever is registered here without further changes.
"""
from core.registry import register
from sources.example.source import ExampleSource
from sources.wfdf.source import WfdfSource

register(ExampleSource())
register(WfdfSource())
