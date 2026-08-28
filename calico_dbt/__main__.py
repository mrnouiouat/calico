"""Stable `python -m calico_dbt build` operator CLI (D-01/D-02/D-04).

Thin argparse controller over `calico_dbt.runner.build()`
(`calico_landing.cli`'s controller pattern). Exposes exactly `--mode`,
`--store`, `--select`, and `--proof-output` -- never a fixture-store
factory, an inspector, or a dbt-project-directory override, which are
test/integration-only seams `runner.build()` accepts but this module never
forwards. Prints one compact, closed, value-free JSON result to stdout and
one concise fixed-vocabulary status line to stderr; exits `0` on success,
`1` on any failure -- this module never renders a path, row, excluded
value, or raw child/exception output (D-15).
"""

from __future__ import annotations

import argparse
import sys

from calico_dbt.runner import SELECT_ALIASES, build

_UNEXPECTED_ERROR_STATUS = "failed category=runner.unexpected_error"
_UNEXPECTED_ERROR_EXIT_CODE = 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="calico_dbt",
        description="Prepare verified dbt input and run one full pinned dbt build.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build",
        help="Run the fixture-default or explicit real-mode dbt build.",
    )
    build_parser.add_argument(
        "--mode",
        choices=("fixture", "real"),
        default="fixture",
        help="fixture (safe, argument-free default) or real (requires --store).",
    )
    build_parser.add_argument(
        "--store",
        default=None,
        help="Path to an owner-controlled admitted-release store root (real mode only).",
    )
    build_parser.add_argument(
        "--select",
        choices=tuple(SELECT_ALIASES),
        default=None,
        help="One closed verification-only selector alias; omit for the full build.",
    )
    build_parser.add_argument(
        "--proof-output",
        action="store_true",
        help="Atomically write the fixed real-mode proof document (real mode only).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        outcome = build(
            mode=args.mode,
            store=args.store,
            select=args.select,
            proof_output=args.proof_output,
        )
    except Exception:  # noqa: BLE001 -- fixed safe message only; never the exception object
        print(_UNEXPECTED_ERROR_STATUS, file=sys.stderr)
        return _UNEXPECTED_ERROR_EXIT_CODE

    if outcome.succeeded:
        print(outcome.proof.to_json())
        print(f"success mode={outcome.proof.mode}", file=sys.stderr)
        return 0

    print(f'{{"status":"failed","category":"{outcome.category}"}}')
    print(f"failed category={outcome.category}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
