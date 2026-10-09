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
    args = parser.parse_args(argv)
    try:
        root = Path.cwd()
        if args.command == "inputs":
            capture_inputs(root, args.published_ref, write=True)
        elif args.command == "generate":
            generate_readme(root, write=True)
        elif args.command == "check":
            check_readme(root)
            from .hosted_replay import check_repository_hosted_replay
            check_repository_hosted_replay(root)
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
        if isinstance(exc, HostedReplayPublicError):
            print("docs-public: " + exc.category, file=sys.stderr)
            return 1
        print("docs-public: readme.internal_error", file=sys.stderr)
        return 1
    print("docs-public: " + args.command + " passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
