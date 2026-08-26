"""Module entry point for `python -m calico_landing` (D-04).

Only delegates to `calico_landing.cli.main()` through `SystemExit` -- no
argument parsing, exit-code translation, or admission logic lives here
(mirrors `tools/privacy_scan/__main__.py`'s thin-entry-point convention).
"""

from __future__ import annotations

from calico_landing.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
