"""Safe, side-effect-free Gate A evidence-repair derivation package (D-11-D-13).

Reads only admitted release manifests and their canonical Parquet from an
external store, recomputes every affected structural claim in bundled,
fixed DuckDB SQL, and emits four closed-schema additive successor
artifacts. This package never mutates admission state, never opens raw
CSV, and never imports the read-for-ideas-only historical research
programs (mirrors `tools/privacy_scan/__init__.py`'s side-effect-free
package boundary).
"""

from __future__ import annotations

from tools.evidence_repair.__main__ import main

__all__ = ["main"]
