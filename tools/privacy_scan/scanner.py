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

import codecs
import itertools
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable
from urllib.parse import parse_qsl, urlsplit

from tools.privacy_scan.git_objects import (
    BatchBlobReader,
    GitObjectError,
    classify_mode,
    iter_target_entries,
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

_STREAM_CHUNK_BYTES = 65536
# A publication record may be large, but it must not make scanner memory
# unbounded. Records beyond this fixed ceiling fail closed instead of being
# skipped or scanned with an insufficient overlap window.
_MAX_STREAM_RECORD_CHARS = 1048576
_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec"


class ScanPathError(Exception):
    """A fixed, value-free failure at the explicit-path scan boundary."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


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


def _scan_record(path: str, record: str, line_number: int) -> list[Finding]:
    """Scan one complete record while preserving its absolute line locator."""

    findings: list[Finding] = []
    for item in scan_text(path, record):
        relative_line = int(item.locator.removeprefix("line "))
        findings.append(
            Finding(item.category, item.path, f"line {line_number + relative_line - 1}")
        )
    return findings


def _scan_utf8_chunks(path: str, chunks: Iterable[bytes]) -> list[Finding]:
    """Incrementally decode and scan complete logical records.

    CSV paths retain RFC-style quote state so embedded physical newlines stay
    in the same logical record. Other text paths retain the original physical
    line boundary. Detector tokens may be arbitrarily split across byte chunks.
    A deliberately overlong logical record is drained to its real boundary and
    reported once with a value-free finding, preserving bounded memory.
    """

    iterator = iter(chunks)
    prefix_parts: list[bytes] = []
    prefix_size = 0
    while prefix_size < len(_LFS_POINTER_PREFIX):
        try:
            part = next(iterator)
        except StopIteration:
            break
        prefix_parts.append(part)
        prefix_size += len(part)
    initial = b"".join(prefix_parts)
    if initial.startswith(_LFS_POINTER_PREFIX):
        # Exhaust the source so a Git batch reader consumes its protocol
        # trailer before another object is requested.
        for _ in iterator:
            pass
        return [Finding("lfs_pointer", path, "blob")]

    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    findings: list[Finding] = []
    record = ""
    line_number = 1
    record_line_number = 1
    discarding_overlong = False
    csv_mode = PurePosixPath(path).suffix.lower() == ".csv"
    in_quotes = False
    pending_quote = False

    def consume_text(text: str) -> None:
        nonlocal record, line_number, record_line_number
        nonlocal discarding_overlong, in_quotes, pending_quote
        for character in text:
            logical_boundary = False

            if csv_mode:
                if in_quotes:
                    if pending_quote:
                        if character == '"':
                            pending_quote = False
                        else:
                            pending_quote = False
                            in_quotes = False
                            if character == '"':
                                in_quotes = True
                            elif character == "\n":
                                logical_boundary = True
                    elif character == '"':
                        pending_quote = True
                elif character == '"':
                    in_quotes = True
                elif character == "\n":
                    logical_boundary = True
            elif character == "\n":
                logical_boundary = True

            if not discarding_overlong:
                if len(record) >= _MAX_STREAM_RECORD_CHARS:
                    findings.append(
                        Finding("oversize_record", path, f"line {record_line_number}")
                    )
                    record = ""
                    discarding_overlong = True
                else:
                    record += character

            if character == "\n":
                line_number += 1

            if logical_boundary:
                if not discarding_overlong:
                    findings.extend(_scan_record(path, record, record_line_number))
                record = ""
                discarding_overlong = False
                record_line_number = line_number

    try:
        for chunk in itertools.chain((initial,), iterator):
            if b"\x00" in chunk:
                # Drain before returning when this is a batch Git object.
                for _ in iterator:
                    pass
                return [Finding("binary_content", path, "blob")]
            consume_text(decoder.decode(chunk, final=False))
        consume_text(decoder.decode(b"", final=True))
    except UnicodeDecodeError:
        for _ in iterator:
            pass
        return [Finding("invalid_utf8", path, "blob")]

    if record:
        findings.extend(_scan_record(path, record, record_line_number))
    return findings


def _validated_relative_paths(relative_paths: Iterable[str]) -> tuple[str, ...]:
    try:
        paths = tuple(relative_paths)
    except TypeError as exc:
        raise ScanPathError("privacy_scan.invalid_path_list") from exc
    if (
        not paths
        or not all(isinstance(path, str) and path for path in paths)
        or tuple(sorted(paths)) != paths
        or len(paths) != len(set(paths))
    ):
        raise ScanPathError("privacy_scan.invalid_path_list")
    for raw in paths:
        pure = PurePosixPath(raw)
        if (
            "\\" in raw
            or re.match(r"^[A-Za-z]:", raw)
            or pure.is_absolute()
            or ".." in pure.parts
            or pure.as_posix() != raw
        ):
            raise ScanPathError("privacy_scan.invalid_path_list")
    return paths


def scan_paths(
    root_dir: str | Path,
    relative_paths: Iterable[str],
    policy: Policy,
) -> list[Finding]:
    """Stream only the caller's explicit, sorted publication path list."""

    try:
        root = Path(root_dir)
        if root.is_symlink():
            raise ScanPathError("privacy_scan.invalid_root")
        resolved_root = root.resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise ScanPathError("privacy_scan.invalid_root") from exc
    if not resolved_root.is_dir():
        raise ScanPathError("privacy_scan.invalid_root")

    paths = _validated_relative_paths(relative_paths)
    findings: list[Finding] = []
    for relative_path in paths:
        findings.extend(check_path_rules(relative_path, policy))
        candidate = resolved_root.joinpath(*PurePosixPath(relative_path).parts)
        try:
            if candidate.is_symlink() or not candidate.is_file():
                raise ScanPathError("privacy_scan.non_regular_file")
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(resolved_root):
                raise ScanPathError("privacy_scan.invalid_path_list")
            with resolved.open("rb") as handle:
                chunks = iter(lambda: handle.read(_STREAM_CHUNK_BYTES), b"")
                findings.extend(_scan_utf8_chunks(relative_path, chunks))
        except ScanPathError:
            raise
        except OSError as exc:
            raise ScanPathError("privacy_scan.unreadable_file") from exc
    return sorted(findings, key=lambda item: (item.path, item.locator, item.category))


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

    try:
        with BatchBlobReader(repo_dir) as reader:
            for entry in entries:
                category = classify_mode(entry.mode, entry.obj_type)
                if category is not None:
                    findings.append(Finding(category=category, path=entry.path, locator="blob"))
                    continue
                findings.extend(_scan_utf8_chunks(entry.path, reader.iter_chunks(entry.oid)))
    except GitObjectError:
        raise

    return sorted(findings, key=lambda finding: (finding.path, finding.locator, finding.category))
