"""Stable local and CI privacy-gate entry point.

    py -V:3.13 -m tools.privacy_scan --tree HEAD --history-all

Exit code contract:
    0   -- zero findings on a clean candidate tree/history.
    1   -- any finding, invalid policy, unreadable/unsupported Git object,
           Git error, or internal error.
    2   -- invalid CLI usage (neither --tree nor --history-all supplied).

Never renders an exception object, Git stderr, or a matched value -- only a
fixed safe diagnostic line per finding/failure (D-10).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.privacy_scan.git_objects import GitObjectError
from tools.privacy_scan.policy import PolicyError, load_policy
from tools.privacy_scan.scanner import scan

#: Fixed default policy location, relative to this package's repository root.
_DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "publishable-tree.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools.privacy_scan",
        description="Publishable-tree privacy gate: scans a candidate Git tree and/or "
        "reachable history for D-007-excluded content.",
    )
    parser.add_argument(
        "--tree",
        default=None,
        help="Treeish to scan (e.g. HEAD or a `git write-tree` output).",
    )
    parser.add_argument(
        "--history-all",
        action="store_true",
        help="Additionally scan every commit reachable from any ref.",
    )
    parser.add_argument(
        "--policy",
        default=None,
        help=argparse.SUPPRESS,  # test-only explicit policy override
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.tree is None and not args.history_all:
        print("privacy gate: usage error: no candidate tree or history specified", file=sys.stderr)
        return 2

    policy_path = Path(args.policy) if args.policy is not None else _DEFAULT_POLICY_PATH

    try:
        policy = load_policy(policy_path)
        findings = scan(
            treeish=args.tree,
            history_all=args.history_all,
            repo_dir=Path.cwd(),
            policy=policy,
        )
    except PolicyError as exc:
        print(f"privacy gate: policy error: {exc.category}", file=sys.stderr)
        return 1
    except GitObjectError as exc:
        print(f"privacy gate: git error: {exc.category}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 -- fixed safe message only; never the exception object
        print("privacy gate: internal error", file=sys.stderr)
        return 1

    if not findings:
        return 0

    for finding in findings:
        print(finding.render())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
