"""Strict schema and normalization for the publishable-tree privacy policy.

Scope corrected 2026-08-24 (D-09): organization names and `State Charity Reg#`
values are D-007-allowed published fields. Organization-name fingerprinting is
dropped entirely -- this module stores no name digests and validates no
fingerprint block. The policy is limited to a versioned, typed set of
exact/prefix/suffix path rules.

Every failure raised here carries only a fixed safe category string. No
input value, file path, or JSON fragment is ever included in an exception
message (D-10).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

#: Rule kinds accepted for `forbidden_paths` entries.
ALLOWED_RULE_KINDS = frozenset({"exact", "prefix", "suffix"})

#: Categories accepted for `forbidden_paths` entries. Kept intentionally
#: narrow -- content-detector categories (fein, street_address, ...) belong
#: to the scanner, not the path policy, and are validated there instead.
ALLOWED_CATEGORIES = frozenset(
    {
        "raw_source_data",
        "database_file",
        "source_pdf",
        "private_database",
        "generated_diff_output",
        "forbidden_path",
    }
)

#: The only top-level keys a valid policy document may contain.
_REQUIRED_TOP_LEVEL_KEYS = frozenset({"policy_version", "max_blob_bytes", "forbidden_paths"})

#: The only keys a valid forbidden-path rule may contain.
_REQUIRED_RULE_KEYS = frozenset({"kind", "value", "category"})

_SUPPORTED_POLICY_VERSION = 1


class PolicyError(Exception):
    """Raised when a policy document is missing, malformed, or fails closed.

    Carries only a fixed safe `category`; never the offending value.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class PathRule:
    kind: str
    value: str
    category: str


@dataclass(frozen=True)
class Policy:
    policy_version: int
    max_blob_bytes: int
    forbidden_paths: tuple[PathRule, ...]


def _is_posix_path_value(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if "\\" in value:
        return False
    if value.startswith("/"):
        return False
    if "\x00" in value:
        return False
    segments = value.split("/")
    if any(segment == ".." for segment in segments):
        return False
    return True


def _parse_rule(raw_rule: object) -> PathRule:
    if not isinstance(raw_rule, dict):
        raise PolicyError("invalid_rule_schema")
    if set(raw_rule.keys()) != _REQUIRED_RULE_KEYS:
        raise PolicyError("invalid_rule_schema")

    kind = raw_rule["kind"]
    value = raw_rule["value"]
    category = raw_rule["category"]

    if not isinstance(kind, str) or kind not in ALLOWED_RULE_KINDS:
        raise PolicyError("invalid_rule_kind")
    if not isinstance(category, str) or category not in ALLOWED_CATEGORIES:
        raise PolicyError("invalid_rule_category")
    if not _is_posix_path_value(value):
        raise PolicyError("non_posix_path")

    return PathRule(kind=kind, value=value, category=category)


def load_policy(path: str | Path) -> Policy:
    """Load and strictly validate a publishable-tree policy document.

    Fails closed (raises `PolicyError`) on any missing, unknown, or
    malformed field, and never reflects the offending input in the raised
    exception.
    """

    policy_path = Path(path)
    try:
        raw_bytes = policy_path.read_bytes()
    except OSError as exc:
        raise PolicyError("policy_not_found") from exc

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError("invalid_policy_encoding") from exc

    try:
        document = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise PolicyError("invalid_policy_json") from exc

    if not isinstance(document, dict):
        raise PolicyError("invalid_policy_schema")
    if set(document.keys()) != _REQUIRED_TOP_LEVEL_KEYS:
        raise PolicyError("invalid_policy_schema")

    policy_version = document["policy_version"]
    if not isinstance(policy_version, int) or isinstance(policy_version, bool):
        raise PolicyError("invalid_policy_version")
    if policy_version != _SUPPORTED_POLICY_VERSION:
        raise PolicyError("invalid_policy_version")

    max_blob_bytes = document["max_blob_bytes"]
    if not isinstance(max_blob_bytes, int) or isinstance(max_blob_bytes, bool):
        raise PolicyError("invalid_max_blob_bytes")
    if max_blob_bytes <= 0:
        raise PolicyError("invalid_max_blob_bytes")

    raw_forbidden_paths = document["forbidden_paths"]
    if not isinstance(raw_forbidden_paths, list):
        raise PolicyError("invalid_policy_schema")

    rules = tuple(_parse_rule(raw_rule) for raw_rule in raw_forbidden_paths)
    sorted_rules = tuple(sorted(rules, key=lambda rule: (rule.kind, rule.value)))

    return Policy(
        policy_version=policy_version,
        max_blob_bytes=max_blob_bytes,
        forbidden_paths=sorted_rules,
    )
