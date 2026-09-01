"""Stable `python -m calico_dbt build`/`docs`/`verify-proof` operator CLI
(D-01/D-02/D-04/D-11..D-15/D-20).

Thin argparse controller over `calico_dbt.runner.build()`/`runner.docs()`/
`runner.verify_proof()` (`calico_landing.cli`'s controller pattern). `build`
exposes exactly `--mode`, `--store`, `--select`, and `--proof-output`;
`docs` exposes only `--mode`, closed to exactly `fixture` -- there is no
real-mode docs proof and no `--store` for `docs` (T-04-06F). `verify-proof`
exposes `--proof` plus one boolean flag per independent, additive
requirement (`--require-mode`, `--require-current-run`,
`--require-verified-binding`, `--require-exact-reconciliation`,
`--require-diagnostics`, `--require-claim-support`, `--verify-hashes`) --
every flag is closed and additive; none of them ever accept a path, row, or
excluded value as their own argument. No subcommand ever forwards a
fixture-store factory, an inspector, a dbt-project-directory override, or a
`now` override, which are test/integration-only seams the runner functions
accept but this module never exposes. Prints one compact, closed,
value-free JSON result to stdout and one concise fixed-vocabulary status
line to stderr; exits `0` on success, `1` on any failure -- this module
never renders a path, row, excluded value, or raw child/exception output
(D-15).
"""

from __future__ import annotations

import argparse
import sys

from calico_dbt.runner import SELECT_ALIASES, build, docs, verify_proof

_UNEXPECTED_ERROR_STATUS = "failed category=runner.unexpected_error"
_UNEXPECTED_ERROR_EXIT_CODE = 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="calico_dbt",
        description="Prepare verified dbt input and run one full pinned dbt build, or the fixture-only docs proof.",
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

    docs_parser = subparsers.add_parser(
        "docs",
        help="Run the closed fixture-only full build plus dbt docs generate proof.",
    )
    docs_parser.add_argument(
        "--mode",
        choices=("fixture",),
        default="fixture",
        help="Always 'fixture' -- the docs proof never runs against a real store.",
    )

    verify_proof_parser = subparsers.add_parser(
        "verify-proof",
        help="Verify a closed Gate B proof document against explicit, additive requirements.",
    )
    verify_proof_parser.add_argument(
        "--proof",
        required=True,
        help="Path to the proof JSON document to verify.",
    )
    verify_proof_parser.add_argument(
        "--require-mode",
        choices=("fixture", "real"),
        default=None,
        help="Reject unless the proof's mode field is exactly this value.",
    )
    verify_proof_parser.add_argument(
        "--require-current-run",
        action="store_true",
        help="Reject a proof whose run_id/generated_at_utc is malformed or stale.",
    )
    verify_proof_parser.add_argument(
        "--require-verified-binding",
        action="store_true",
        help="Reject unless verified_input_binding is exactly true.",
    )
    verify_proof_parser.add_argument(
        "--require-exact-reconciliation",
        action="store_true",
        help="Reject unless the Gate A reconciliation section reports zero mismatches.",
    )
    verify_proof_parser.add_argument(
        "--require-diagnostics",
        action="store_true",
        help="Reject unless all three Last Renewal diagnostic measures are attested complete.",
    )
    verify_proof_parser.add_argument(
        "--require-claim-support",
        action="store_true",
        help="Reject unless the claim-support relationship is attested supported.",
    )
    verify_proof_parser.add_argument(
        "--verify-hashes",
        action="store_true",
        help="Recompute and compare every recorded hash against current disk state.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            outcome = build(
                mode=args.mode,
                store=args.store,
                select=args.select,
                proof_output=args.proof_output,
            )
        elif args.command == "docs":
            outcome = docs()
        elif args.command == "verify-proof":
            outcome = verify_proof(
                proof_path=args.proof,
                require_mode=args.require_mode,
                require_current_run=args.require_current_run,
                require_verified_binding=args.require_verified_binding,
                require_exact_reconciliation=args.require_exact_reconciliation,
                require_diagnostics=args.require_diagnostics,
                require_claim_support=args.require_claim_support,
                verify_hashes=args.verify_hashes,
            )
        else:
            raise AssertionError(f"unreachable: unknown subcommand {args.command!r}")
    except Exception:  # noqa: BLE001 -- fixed safe message only; never the exception object
        print(_UNEXPECTED_ERROR_STATUS, file=sys.stderr)
        return _UNEXPECTED_ERROR_EXIT_CODE

    if args.command == "verify-proof":
        if outcome.verified:
            print('{"status":"verified"}')
            print("verified", file=sys.stderr)
            return 0
        print(f'{{"status":"failed","category":"{outcome.category}"}}')
        print(f"failed category={outcome.category}", file=sys.stderr)
        return 1

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
