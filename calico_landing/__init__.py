"""Public Calico landing package API (D-04/D-06).

Package import performs no filesystem, Git, or subprocess I/O -- every
side effect happens only inside an explicit call to the exported names
below. Exposes exactly the stable public entry point that later Phase 6
network-download and private object-store adapters call unchanged: the
side-effect-free `admit()` service and its immutable `AdmissionResult`
value (mirrors `tools/privacy_scan/__init__.py`'s side-effect-free import
pattern).
"""

from __future__ import annotations

from calico_landing.admission import admit
from calico_landing.result import AdmissionResult

__all__ = ["AdmissionResult", "admit"]
