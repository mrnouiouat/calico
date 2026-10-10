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
    replay_generate = commands.add_parser("hosted-replay-generate", help="Project one validated private envelope into public evidence")
    replay_generate.add_argument("--envelope", type=Path, required=True)
    replay_generate.add_argument("--json-output", type=Path, required=True)
    replay_generate.add_argument("--markdown-output", type=Path, required=True)
    replay_check = commands.add_parser("hosted-replay-check", help="Check exact envelope/public/rendered evidence equality")
    replay_check.add_argument("--envelope", type=Path, required=True)
    replay_check.add_argument("--json", type=Path, required=True)
    replay_check.add_argument("--markdown", type=Path, required=True)
    gate_check = commands.add_parser("gate-e-check", help="Validate the ten-condition authority and optional rendering")
    gate_check.add_argument("--authority", type=Path, required=True)
    gate_check.add_argument("--markdown", type=Path)
    gate_generate = commands.add_parser("gate-e-generate", help="Render the validated ten-condition authority")
    gate_generate.add_argument("--authority", type=Path, required=True)
    gate_generate.add_argument("--output", type=Path, required=True)
    disposition_finalize = commands.add_parser("condition-four-finalize", help="Add explicitly supplied approval to the validated private draft")
    disposition_finalize.add_argument("--draft", type=Path, required=True)
    disposition_finalize.add_argument("--final", type=Path, required=True)
    disposition_finalize.add_argument("--approved-at")
    disposition_finalize.add_argument("--approved-by-role", choices=("repository-owner",))
    for name in ("condition-four-check", "condition-four-render"):
        command = commands.add_parser(name, help="Validate exact draft/final meaning against measured evidence")
        for key in ("draft", "final", "hosted", "real"):
            command.add_argument("--" + key, type=Path, required=True)
        if name.endswith("check"):
            command.add_argument("--public", type=Path)
        else:
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = Path.cwd()
        if args.command == "inputs":
            capture_inputs(root, args.published_ref, write=True)
        elif args.command == "generate":
            generate_readme(root, write=True)
            from .gate_e import AUTHORITY_PATH, MARKDOWN_PATH, generate_gate_e
            if (root / AUTHORITY_PATH).exists():
                generate_gate_e(root / AUTHORITY_PATH, root / MARKDOWN_PATH, root=root)
        elif args.command == "check":
            check_readme(root)
            from .hosted_replay import check_repository_hosted_replay
            check_repository_hosted_replay(root)
            from .gate_e import AUTHORITY_PATH, MARKDOWN_PATH, check_gate_e
            if (root / AUTHORITY_PATH).exists():
                check_gate_e(root / AUTHORITY_PATH, root / MARKDOWN_PATH, root=root)
        elif args.command == "gate-e-check":
            from .gate_e import check_gate_e
            check_gate_e(args.authority, args.markdown, root=root)
        elif args.command == "gate-e-generate":
            from .gate_e import generate_gate_e
            generate_gate_e(args.authority, args.output, root=root)
        elif args.command == "condition-four-finalize":
            from .gate_e import _read, _write, _encode, finalize_condition_four_disposition
            from .hosted_replay import _path
            if _path(args.draft) == _path(args.final):
                from .gate_e import GateEEvidenceError
                raise GateEEvidenceError("gate_e.unsafe_path")
            document = finalize_condition_four_disposition(draft=_read(args.draft),
                approved_at=args.approved_at,
                approved_by_role="repository owner" if args.approved_by_role == "repository-owner" else None)
            _write(args.final, _encode(document))
        elif args.command in {"condition-four-check", "condition-four-render"}:
            from .gate_e import _read, _write, render_condition_four_public_decision, GateEEvidenceError
            from .hosted_replay import _path
            inputs = {"draft": _read(args.draft), "final": _read(args.final),
                      "hosted_replay": _read(args.hosted), "real_republish": _read(args.real)}
            rendered = render_condition_four_public_decision(**inputs).encode()
            if args.command == "condition-four-render":
                if _path(args.output) in {_path(path) for path in (args.draft, args.final, args.hosted, args.real)}:
                    raise GateEEvidenceError("gate_e.unsafe_path")
                _write(args.output, rendered)
            elif args.public is not None and _read(args.public) != rendered:
                raise GateEEvidenceError("gate_e.generated_drift")
        elif args.command == "hosted-replay-generate":
            from .hosted_replay import generate_hosted_replay_pair
            generate_hosted_replay_pair(args.envelope, args.json_output, args.markdown_output, root=root)
        else:
            from .hosted_replay import check_hosted_replay_against_envelope
            check_hosted_replay_against_envelope(args.envelope, args.json, args.markdown, root=root)
    except ReadmeInputError as exc:
        print("docs-public: " + exc.category, file=sys.stderr)
        return 1
    except Exception as exc:
        from .hosted_replay import HostedReplayPublicError
        from .gate_e import GateEEvidenceError
        if isinstance(exc, (HostedReplayPublicError, GateEEvidenceError)):
            print("docs-public: " + exc.category, file=sys.stderr)
            return 1
        print("docs-public: readme.internal_error", file=sys.stderr)
        return 1
    print("docs-public: " + args.command + " passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
