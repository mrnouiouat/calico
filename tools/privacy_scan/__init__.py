"""Publishable-tree privacy scanner.

Stable command contract:

    py -V:3.13 -m tools.privacy_scan --tree <treeish> [--history-all]

This package performs no I/O on import; all filesystem/Git access happens
inside explicit function calls.
"""

from __future__ import annotations

from tools.privacy_scan.git_objects import GitObjectError
from tools.privacy_scan.policy import Policy, PolicyError, load_policy
from tools.privacy_scan.scanner import Finding, scan

__all__ = [
    "Finding",
    "GitObjectError",
    "Policy",
    "PolicyError",
    "load_policy",
    "scan",
]
