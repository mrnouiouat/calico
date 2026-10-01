"""Value-free CLI; argument errors never echo attacker-controlled tokens."""

from __future__ import annotations

import sys
from pathlib import Path

from .scanner import CitationError


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in (["--check"], ["--write"]):
        print("citation gate: usage error", file=sys.stderr)
        return 2
    try:
        from .scanner import check_repository, write_repository
        result = write_repository(Path.cwd()) if args == ["--write"] else check_repository(Path.cwd())
        print(f"citation gate: passed; occurrences={result['occurrences']} unresolved={result['unresolved']} transitions={result['transitions']} decisions={result['decisions']}")
        return 0
    except CitationError:
        print("citation gate: contract error", file=sys.stderr)
        return 1
    except Exception:
        print("citation gate: internal error", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
