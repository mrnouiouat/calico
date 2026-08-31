"""One closed, optional, private exact-key eligibility sidecar boundary
(04-05-PLAN.md D-16/D-18, T-04-05A/T-04-05B).

`load_eligibility_classifications` is the sole path from a caller-supplied
admitted store root to a safe, structural tuple of already-validated
`EligibilityClassification` rows. The sidecar lives at exactly one fixed
direct child of the store (`public-eligibility-v1.json`), mirroring
`calico_landing.store`'s own `promoted-releases.json` convention -- never a
subdirectory, never a glob, and never resolved through a symlink or reparse
alias at any path component.

The sidecar is optional in real mode: a store with no sidecar file returns
an empty tuple, which `calico_dbt.preflight` binds into the identical fixed
`runtime_input.public_eligibility_classifications` schema fixture mode uses.
An absent sidecar is a valid state, never a failure -- SQL's own left join
downstream normalizes every unmatched key to `'unclassified'`, so a missing
real classification input fails closed to zero public rows rather than an
implicit admission (T-04-05B).

This module performs no fuzzy matching, no name heuristic, and no score
(D-18, T-04-05F) -- it only validates document structure and returns
already-reviewed classification facts. All value binding into SQL happens
exclusively through `calico_dbt.preflight`'s parameterized `INSERT`; this
module itself never touches a database connection.

Every failure crosses this module's boundary as an `EligibilityError`
carrying only a fixed safe `category` -- never an offending path, byte, or
parsed value (mirrors `calico_landing.attempts.AttemptError` and
`calico_dbt.catalog.CatalogError`'s non-echo exception discipline).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

#: The one fixed direct-child filename every admitted store may optionally
#: carry (mirrors `calico_landing.store`'s own `promoted-releases.json`
#: fixed-child convention). Never a subdirectory, never a glob.
_ELIGIBILITY_FILENAME = "public-eligibility-v1.json"

_SUPPORTED_SCHEMA_VERSION = 1

_TOP_LEVEL_KEYS = frozenset({"schema_version", "classification_version", "classifications"})
_CLASSIFICATION_ENTRY_KEYS = frozenset({"registration_number", "classification"})

#: Closed three-value vocabulary (D-18). Only `"eligible"` may ever reach a
#: public relation; the other two states remain privately auditable only.
_CLOSED_CLASSIFICATIONS = frozenset({"eligible", "ambiguous_natural_person", "unclassified"})


class EligibilityError(Exception):
    """Raised on any containment, schema, version, or vocabulary failure.
    Carries only a fixed safe `category` -- never an offending path, byte,
    or parsed value.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class EligibilityClassification:
    """One already-validated closed classification entry, carrying the
    document's own `classification_version` so every row can be traced back
    to the reviewed pass that produced it (D-022 audit discipline).
    """

    registration_number: str
    classification: str
    classification_version: str


def _resolve_eligibility_path(store_root: Path) -> Path | None:
    """Resolve the sidecar at its exact, deterministic direct-child path.

    Returns `None` when no sidecar exists at all -- a legitimate, expected
    real-mode state (T-04-05B), never a failure. Rejects a symlinked or
    reparse-point alias at the candidate path itself, and rejects any
    resolution that would escape the store root or land on a different
    filename (T-04-05A).
    """

    candidate = store_root / _ELIGIBILITY_FILENAME
    if candidate.is_symlink():
        raise EligibilityError("eligibility.link_rejected")
    if not candidate.exists():
        return None

    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise EligibilityError("eligibility.link_rejected") from exc

    try:
        resolved_store_root = store_root.resolve(strict=True)
    except OSError as exc:
        raise EligibilityError("eligibility.link_rejected") from exc

    if resolved.parent != resolved_store_root or resolved.name != _ELIGIBILITY_FILENAME:
        raise EligibilityError("eligibility.link_rejected")
    if not resolved.is_file():
        raise EligibilityError("eligibility.link_rejected")

    return resolved


def _parse_classification_entry(raw_entry: object, *, seen_keys: set[str], classification_version: str) -> EligibilityClassification:
    if not isinstance(raw_entry, dict) or set(raw_entry.keys()) != _CLASSIFICATION_ENTRY_KEYS:
        raise EligibilityError("eligibility.invalid_document_schema")

    registration_number = raw_entry.get("registration_number")
    if not isinstance(registration_number, str) or not registration_number:
        raise EligibilityError("eligibility.invalid_document_schema")
    if registration_number != registration_number.strip():
        raise EligibilityError("eligibility.invalid_document_schema")
    if registration_number in seen_keys:
        raise EligibilityError("eligibility.duplicate_registration_key")
    seen_keys.add(registration_number)

    classification = raw_entry.get("classification")
    if classification not in _CLOSED_CLASSIFICATIONS:
        raise EligibilityError("eligibility.invalid_document_schema")

    return EligibilityClassification(
        registration_number=registration_number,
        classification=classification,
        classification_version=classification_version,
    )


def load_eligibility_classifications(store_root: Path) -> tuple[EligibilityClassification, ...]:
    """Load and strictly validate the optional private eligibility sidecar.

    Returns an empty tuple when no sidecar exists (valid real-mode state).
    Fails closed with `EligibilityError` on any symlink/alias, unknown or
    missing top-level/entry key, unsupported `schema_version`, blank
    `classification_version`, duplicate or blank `registration_number`, or
    any classification value outside the closed three-value vocabulary.
    Never echoes an offending path, byte, or value.
    """

    path = _resolve_eligibility_path(store_root)
    if path is None:
        return ()

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise EligibilityError("eligibility.read_failed") from exc

    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EligibilityError("eligibility.invalid_document_schema") from exc

    if not isinstance(document, dict) or set(document.keys()) != _TOP_LEVEL_KEYS:
        raise EligibilityError("eligibility.invalid_document_schema")

    schema_version = document.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise EligibilityError("eligibility.invalid_document_schema")
    if schema_version != _SUPPORTED_SCHEMA_VERSION:
        raise EligibilityError("eligibility.unsupported_schema_version")

    classification_version = document.get("classification_version")
    if not isinstance(classification_version, str) or not classification_version:
        raise EligibilityError("eligibility.invalid_document_schema")

    raw_classifications = document.get("classifications")
    if not isinstance(raw_classifications, list):
        raise EligibilityError("eligibility.invalid_document_schema")

    seen_keys: set[str] = set()
    return tuple(
        _parse_classification_entry(
            raw_entry, seen_keys=seen_keys, classification_version=classification_version
        )
        for raw_entry in raw_classifications
    )


__all__ = [
    "EligibilityError",
    "EligibilityClassification",
    "load_eligibility_classifications",
]
