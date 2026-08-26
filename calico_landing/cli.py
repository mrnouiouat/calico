"""Stable local operator-facing `admit` module CLI (D-04/D-05/D-06).

Thin argument-parsing and exit-code-translation controller over the public
`calico_landing.admit()` service (`tools/privacy_scan/__main__.py`'s
controller pattern). Accepts explicit candidate-input and external-store
paths, prints exactly one compact machine-readable JSON result to stdout
and exactly one concise fixed-vocabulary human status line to stderr, and
exits `0` accepted, `1` rejected, `2` no_new_release, `3` operational_error
-- the locked D-04 exit-code contract later Phase 6 adapters must also
honor unchanged.

`admit()` already resolves every recoverable structural, transfer,
validation, and store failure into a safe `AdmissionResult` -- this module
never performs its own path or content I/O. The one remaining boundary is
an exception `admit()` itself does not expect; that is caught here first as
the typed `Exception` fallback and translated to the fixed
`operational_error` status without ever rendering the exception object, an
argument value, or an absolute path (D-05/D-10 non-echo discipline).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from calico_landing.admission import admit
from calico_landing.result import AdmissionResult

#: Fixed, safe status line printed only when `admit()` itself raises an
#: exception this module did not expect -- never the exception object, its
#: message, or any argument value.
_UNEXPECTED_ERROR_STATUS = "operational_error reasons=0"

#: Locked D-04 exit code for an unexpected internal failure -- identical to
#: `AdmissionResult.operational_error(()).exit_code`, kept as a literal so
#: this fallback path never has to construct a result object.
_UNEXPECTED_ERROR_EXIT_CODE = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="calico_landing",
        description="Admit one local candidate registry release into an external store.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    admit_parser = subparsers.add_parser(
        "admit",
        help="Admit one candidate release directory into an external store.",
    )
    admit_parser.add_argument(
        "--candidate-input",
        required=True,
        help="Path to the local candidate directory containing candidate-set.json.",
    )
    admit_parser.add_argument(
        "--store",
        required=True,
        help="Path to the external admitted-release store root.",
    )

    return parser


def _run_admit(candidate_input: str, store: str) -> AdmissionResult:
    return admit(candidate_input=Path(candidate_input), store=Path(store))


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = _run_admit(args.candidate_input, args.store)
    except Exception:  # noqa: BLE001 -- fixed safe message only; never the exception object
        print(_UNEXPECTED_ERROR_STATUS, file=sys.stderr)
        return _UNEXPECTED_ERROR_EXIT_CODE

    print(result.to_json())
    print(result.render_status(), file=sys.stderr)
    return result.exit_code


__all__ = ["main"]
