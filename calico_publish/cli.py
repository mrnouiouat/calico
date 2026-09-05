"""Non-echoing CLI over the single publication verification boundary.

Successful commands print one compact machine-readable JSON document to
stdout. Closed failures print only fixed categories or value-free finding
locators: never an exception message or type, a path, a credential, a cell
value, or a data row. Internal loaders are keyword-only seams unreachable
from argv. Plan 07-05 extends the same dispatch table with publication work.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from calico_publish.allowlist import Allowlist, AllowlistError, load_allowlist
from calico_publish.gate import GateError, verify

_REAL_ALLOWLIST_PATH = (
    Path(__file__).resolve().parent.parent / "contracts" / "publication-exports-v1.json"
)
_MANIFEST_RELATIVE_PATH = Path("manifest") / "published-manifest-v1.json"
_MODES = ("fixture", "real")


class _SafeArgumentParser(argparse.ArgumentParser):
    """Argparse parser whose diagnostics never repeat untrusted argv."""

    def error(self, message: str) -> None:
        del message
        self.exit(2, "calico_publish: usage error\n")


def _dict_json(document: dict[str, object]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _allowlist_path(args: argparse.Namespace) -> Path:
    if args.mode == "real":
        return _REAL_ALLOWLIST_PATH
    fixture_path = Path(args.staging) / "publication-exports-v1.json"
    if fixture_path.is_symlink() or (fixture_path.exists() and not fixture_path.is_file()):
        raise AllowlistError("allowlist.invalid_schema")
    return fixture_path if fixture_path.exists() else _REAL_ALLOWLIST_PATH


def _run_verify(
    args: argparse.Namespace,
    *,
    allowlist_loader: Callable[[str | Path], Allowlist],
) -> int:
    allowlist = allowlist_loader(_allowlist_path(args))
    staging = Path(args.staging)
    result = verify(staging, allowlist, staging / _MANIFEST_RELATIVE_PATH)
    if result.passed:
        print(_dict_json({"category": "gate.verified", "violation_count": 0}))
        return 0
    for finding in result.violations:
        print(finding.render())
    print("gate.violations_found", file=sys.stderr)
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="calico_publish",
        description="Verify staged publication bytes against closed authority.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser(
        "verify", help="Fail closed unless staged publication bytes are approved."
    )
    verify_parser.add_argument("--mode", required=True, choices=_MODES)
    verify_parser.add_argument("--staging", required=True)
    return parser


_COMMANDS = {"verify": _run_verify}


def main(
    argv: list[str] | None = None,
    *,
    allowlist_loader: Callable[[str | Path], Allowlist] = load_allowlist,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _COMMANDS[args.command]
    try:
        return handler(args, allowlist_loader=allowlist_loader)
    except (GateError, AllowlistError) as exc:
        print(_dict_json({"category": exc.category}))
        print(exc.category, file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 -- fixed safe category only
        print(_dict_json({"category": "cli.unexpected_error"}))
        print("cli.unexpected_error", file=sys.stderr)
        return 3


__all__ = ["main"]
