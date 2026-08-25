"""Content and path scanner for the publishable-tree privacy gate.

Combines the strict path policy (`policy.py`) with contextual content
detectors over Git object bytes (`git_objects.py`) to produce immutable,
value-free `Finding` objects (D-08 through D-10).

D-007 scope (corrected 2026-08-24): organization names and `State Charity
Reg#` values (all three classified families -- bare digits, `CT`-prefixed,
`EX`-prefixed) are ALLOWED published fields. No detector in this module
scans for organization names or registration numbers; doing so would fail
closed against the project's own REQ-org-lookup deliverable (Phase 8).

No `Finding` ever carries a matched value, decoded source line, or blob
content -- only `category`, POSIX `path`, and a safe `locator` (D-10).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from tools.privacy_scan.git_objects import (
    ObjectSkip,
    ScannableBlob,
    iter_target_entries,
    load_scannable_objects,
)
from tools.privacy_scan.policy import Policy

# --- Detector patterns -------------------------------------------------
#
# All patterns are deliberately shape/context based. Organization identity
# (name, registration number) is never a detector target under D-007.

_FEIN_LABEL_RE = re.compile(
    r"(?:FEIN|EIN|Federal\s+(?:Employer|Tax)\s+ID(?:entification)?(?:\s+Number)?)",
    re.IGNORECASE,
)
_FEIN_CANONICAL_RE = re.compile(r"\b\d{2}-\d{7}\b")
_NINE_DIGIT_RE = re.compile(r"\b\d{9}\b")
_FEIN_LABEL_WINDOW = 40

_STREET_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,3}\s+"
    r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Ct|Court)\b",
    re.IGNORECASE,
)

_PHONE_RE = re.compile(r"\b\d{3}-\d{3}-\d{4}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

_WINDOWS_ABS_PATH_RE = re.compile(r"\b[A-Za-z]:[\\/][^\s\"'<>|]*")
_POSIX_ABS_PATH_RE = re.compile(r"(?<![\w/])/(?:Users|home)/[^\s\"'<>|]*")

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

#: The official Registry Search Tool verification link is an explicitly
#: allowed published field under D-007 and must never be flagged.
_ALLOWED_VERIFICATION_HOST = "rct.doj.ca.gov"
_ALLOWED_VERIFICATION_PATH_PREFIX = "/verification/"

#: Query-string keys that indicate an unapproved external join (D-007
#: excluded field), regardless of host.
_UNAPPROVED_JOIN_QUERY_KEYS = frozenset({"fein", "ein", "ssn"})


@dataclass(frozen=True)
class Finding:
    category: str
    path: str
    locator: str

    def render(self) -> str:
        return f"{self.path}:{self.locator}: {self.category}"


def _locator_for_offset(text: str, offset: int) -> str:
    line = text.count("\n", 0, offset) + 1
    return f"line {line}"


def _find_fein_offsets(text: str) -> list[int]:
    offsets: list[int] = []
    for match in _FEIN_CANONICAL_RE.finditer(text):
        offsets.append(match.start())
    for match in _NINE_DIGIT_RE.finditer(text):
        window_start = max(0, match.start() - _FEIN_LABEL_WINDOW)
        window = text[window_start : match.start()]
        if _FEIN_LABEL_RE.search(window):
            offsets.append(match.start())
    return offsets


def _is_unapproved_join_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    path = parsed.path.lower()
    if host.lower() == _ALLOWED_VERIFICATION_HOST and path.startswith(_ALLOWED_VERIFICATION_PATH_PREFIX):
        return False
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in _UNAPPROVED_JOIN_QUERY_KEYS:
            return True
    return False


def scan_text(path: str, text: str) -> list[Finding]:
    """Run every contextual content detector over decoded blob text."""

    findings: list[Finding] = []

    for offset in _find_fein_offsets(text):
        findings.append(Finding("fein", path, _locator_for_offset(text, offset)))

    for match in _STREET_ADDRESS_RE.finditer(text):
        findings.append(Finding("street_address", path, _locator_for_offset(text, match.start())))

    for match in _PHONE_RE.finditer(text):
        findings.append(Finding("contact_info", path, _locator_for_offset(text, match.start())))

    for match in _EMAIL_RE.finditer(text):
        findings.append(Finding("contact_info", path, _locator_for_offset(text, match.start())))

    for match in _WINDOWS_ABS_PATH_RE.finditer(text):
        findings.append(Finding("absolute_local_path", path, _locator_for_offset(text, match.start())))

    for match in _POSIX_ABS_PATH_RE.finditer(text):
        findings.append(Finding("absolute_local_path", path, _locator_for_offset(text, match.start())))

    for match in _URL_RE.finditer(text):
        if _is_unapproved_join_url(match.group(0)):
            findings.append(Finding("unapproved_join_field", path, _locator_for_offset(text, match.start())))

    return findings


def check_path_rules(path: str, policy: Policy) -> list[Finding]:
    """Apply the strict exact/prefix/suffix path policy to a candidate path."""

    findings: list[Finding] = []
    for rule in policy.forbidden_paths:
        if rule.kind == "exact":
            matched = path == rule.value
        elif rule.kind == "prefix":
            matched = path.startswith(rule.value)
        else:  # "suffix" -- load_policy restricts kind to this closed set
            matched = path.endswith(rule.value)
        if matched:
            findings.append(Finding(category=rule.category, path=path, locator="path"))
    return findings


def scan(
    *,
    treeish: str | None,
    history_all: bool,
    repo_dir: str | Path,
    policy: Policy,
) -> list[Finding]:
    """Scan the candidate tree and/or reachable history for policy violations.

    Deterministic: findings are sorted by (path, locator, category) so local
    and CI output is stable across runs.
    """

    entries = iter_target_entries(treeish=treeish, history_all=history_all, repo_dir=repo_dir)

    findings: list[Finding] = []
    for entry in entries:
        findings.extend(check_path_rules(entry.path, policy))

    objects = load_scannable_objects(entries, repo_dir, policy.max_blob_bytes)
    for obj in objects:
        if isinstance(obj, ObjectSkip):
            findings.append(Finding(category=obj.category, path=obj.path, locator="blob"))
        elif isinstance(obj, ScannableBlob):
            findings.extend(scan_text(obj.path, obj.text))

    return sorted(findings, key=lambda finding: (finding.path, finding.locator, finding.category))
