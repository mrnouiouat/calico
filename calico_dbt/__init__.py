"""calico_dbt: the non-echoing, manifest-anchored dbt input boundary and
stable two-mode build command (D-01..D-16), plus the closed fixture-only
docs proof (D-20).
"""

from __future__ import annotations

from calico_dbt.runner import (
    BuildOutcome,
    DocsOutcome,
    FixtureBuildInspection,
    SafeBuildProof,
    SafeDocsProof,
    build,
    docs,
)

__all__ = [
    "BuildOutcome",
    "DocsOutcome",
    "FixtureBuildInspection",
    "SafeBuildProof",
    "SafeDocsProof",
    "build",
    "docs",
]
