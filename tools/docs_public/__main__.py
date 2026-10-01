"""Explicit immutable refresh and offline generation/check commands."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .readme import ReadmeInputError, capture_inputs, check_readme, generate_readme


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal error would reflect arbitrary command-line input.
        self.exit(2, "docs-public: readme.invalid_usage\n")


def main(argv=None) -> int:
    parser = _Parser(prog="tools.docs_public", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    inputs = commands.add_parser("inputs", help="Pin already-fetched publication evidence")
    inputs.add_argument("--published-ref", required=True)
    commands.add_parser("generate", help="Write README and MIT license from pinned evidence")
    commands.add_parser("check", help="Offline drift verification without writes")
    args = parser.parse_args(argv)
    try:
        root = Path.cwd()
        if args.command == "inputs":
            capture_inputs(root, args.published_ref, write=True)
        elif args.command == "generate":
            generate_readme(root, write=True)
        else:
            check_readme(root)
    except ReadmeInputError as exc:
        print("docs-public: " + exc.category, file=sys.stderr)
        return 1
    except Exception:
        print("docs-public: readme.internal_error", file=sys.stderr)
        return 1
    print("docs-public: " + args.command + " passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
