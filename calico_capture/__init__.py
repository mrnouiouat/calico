"""Public Calico capture package API (06-01-PLAN.md D-01/D-02/D-06/D-07/D-13/D-14).

Package import performs no filesystem, Git, subprocess, or network I/O --
every side effect happens only inside an explicit call to the exported
names below. Mirrors `calico_landing/__init__.py`'s side-effect-free
import contract.
"""

from __future__ import annotations

from calico_capture.orchestrator import CaptureError, capture
from calico_capture.status import CaptureStatus

__all__ = ["CaptureError", "CaptureStatus", "capture"]
