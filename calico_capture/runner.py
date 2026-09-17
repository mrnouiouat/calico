"""Closed publication-result status writer for the existing capture workflow.

The capture orchestrator owns admission. This module only finishes its safe
status projection after the independent publication outcome is known.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from calico_capture.status import StatusError, project_publication_status


def write_publication_status(path: Path, document: object, *, publication_succeeded: bool) -> None:
    status = project_publication_status(document, publication_succeeded=publication_succeeded)
    # A fresh fixed staging file prevents following an existing final symlink.
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(status.to_json())


def main() -> int:
    try:
        result = os.environ.get("PUBLICATION_SUCCEEDED")
        if result not in {"true", "false"}:
            raise StatusError("status.invalid_publication_result")
        document = json.loads(os.environ["CAPTURE_STATUS_JSON"])
        write_publication_status(Path("capture-status-validated.json"), document,
                                 publication_succeeded=result == "true")
    except (StatusError, OSError, ValueError, KeyError, TypeError):
        print("status.publication_projection_failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
