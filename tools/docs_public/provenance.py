"""Scanned, byte-preserving successors to historical investigation records.

Only the prefix is authored here. Historical bodies are binary data; every
public record binds their exact bytes to an independently recorded anchor.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from tools.privacy_scan.policy import Policy
from tools.privacy_scan.scanner import scan_paths

START_MARKER = b"<!-- calico-provenance-v1:start -->\n"
END_MARKER = b"<!-- calico-provenance-v1:end -->\n"
MARKER_VERSION = "calico-provenance-v1"
INDEX_PATH = "docs/provenance/index-v1.json"
AUTHORITY_LINKS = ("docs/evidence/gate-a/correction-index-v1.json", "docs/evidence/gate-a/spike-002-successor-v1.json", "docs/decisions/register.md")


class ProvenanceError(Exception):
    """Fixed categories only; no source content or exception details."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _destination(path: str) -> None:
    if not isinstance(path, str):
        raise ProvenanceError("provenance.destination")
    pure = PurePosixPath(path)
    if (pure.is_absolute() or ".." in pure.parts or "\\" in path
            or pure.as_posix() != path or not path.startswith("docs/provenance/")
            or path == INDEX_PATH or len(pure.parts) < 3):
        raise ProvenanceError("provenance.destination")


@dataclass(frozen=True)
class PredecessorAnchor:
    source_label: str
    predecessor_sha256: str
    destination: str

    def __post_init__(self):
        _destination(self.destination)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", self.source_label):
            raise ProvenanceError("provenance.source_label")
        if not re.fullmatch(r"[0-9a-f]{64}", self.predecessor_sha256):
            raise ProvenanceError("provenance.anchor")


@dataclass(frozen=True)
class SuccessorRecord:
    source_label: str
    predecessor_sha256: str
    destination: str
    prefix_bytes: int
    prefix_sha256: str
    body_sha256: str
    successor_sha256: str
    marker_version: str
    authority_links: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["authority_links"] = list(self.authority_links)
        return result

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "SuccessorRecord":
        try:
            if set(item) != set(cls.__dataclass_fields__):
                raise ValueError()
            return cls(**{**item, "authority_links": tuple(item["authority_links"])})
        except (ValueError, TypeError, KeyError):
            raise ProvenanceError("provenance.index_schema") from None


def validate_successor(data: bytes, record: SuccessorRecord) -> bytes:
    """Return the exact body only after the complete recorded chain verifies."""
    try:
        PredecessorAnchor(record.source_label, record.predecessor_sha256, record.destination)
        if (type(record.prefix_bytes) is not int or record.prefix_bytes <= 0
                or record.marker_version != MARKER_VERSION
                or record.authority_links != AUTHORITY_LINKS):
            raise ProvenanceError("provenance.record_schema")
        if (not data.startswith(START_MARKER) or data.count(START_MARKER) != 1
                or data.count(END_MARKER) != 1
                or data.find(END_MARKER) + len(END_MARKER) != record.prefix_bytes):
            raise ProvenanceError("provenance.markers")
        prefix, body = data[:record.prefix_bytes], data[record.prefix_bytes:]
        if (_hash(prefix) != record.prefix_sha256 or _hash(body) != record.body_sha256
                or record.body_sha256 != record.predecessor_sha256
                or _hash(data) != record.successor_sha256):
            raise ProvenanceError("provenance.hash_chain")
        return body
    except (TypeError, ValueError, AttributeError):
        raise ProvenanceError("provenance.record_schema") from None


def _json(path: Path) -> Any:
    def closed(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProvenanceError("provenance.duplicate_key")
            result[key] = value
        return result
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=closed)
    except (OSError, ValueError, UnicodeError):
        raise ProvenanceError("provenance.evidence_schema") from None


def derive_corrections(body: bytes, root: Path, historical_evidence: Path) -> list[tuple[str, str, str]]:
    """Bind numeric rows to both committed repair evidence and its predecessor."""
    index = _json(root / AUTHORITY_LINKS[0])
    successor_path = root / AUTHORITY_LINKS[1]
    successor = _json(successor_path)
    try:
        matches = [row for row in index["corrections"] if row["successor_file"] == successor_path.name]
        if len(matches) != 1:
            raise ProvenanceError("provenance.evidence_anchor")
        row = matches[0]
        if (_sha256_file(successor_path) != row["successor_sha256"]
                or _sha256_file(historical_evidence) != row["supersedes"]["predecessor_sha256"]):
            raise ProvenanceError("provenance.evidence_anchor")
        historical = _json(historical_evidence)
        corrections = []
        for current in successor["coverage"]:
            prior = historical["release_coverage"].get(current["as_of_date"])
            if prior is None:
                continue
            for old_key, new_key in (("rows", "total_row_count"), ("keyless_rows", "keyless_row_count")):
                old, new = prior[old_key], current[new_key]
                if old != new and (str(old).encode() in body or f"{old:,}".encode() in body):
                    corrections.append((f"{current['as_of_date']} {old_key}: {old:,}", f"{new:,}", AUTHORITY_LINKS[1]))
        return corrections
    except (KeyError, TypeError, OSError):
        raise ProvenanceError("provenance.evidence_schema") from None


def _prefix(anchor: PredecessorAnchor, import_date: str, corrections: list[tuple[str, str, str]]) -> bytes:
    try:
        if date.fromisoformat(import_date).isoformat() != import_date:
            raise ValueError()
    except (ValueError, TypeError):
        raise ProvenanceError("provenance.import_date") from None
    decisions = "docs/decisions/register.md"
    rows = [*corrections,
        ("Public Registry Operations Monitor name", "California Charity Registry Monitor (D-001)", decisions),
        ("Aggregate-only publication", "Bounded named organization history alongside aggregates (D-007)", decisions),
        ("Power BI with an Evidence fallback", "One Power BI implementation; documented manual refresh fallback (D-008)", decisions),
        ("Release-count readiness", "Five simultaneous estimability conditions; no formal survival analysis in v1 (D-010)", decisions),
        ("Archive census as a prerequisite", "Archive census is outside v1 (D-012)", decisions),
        ("Newline-aware default CSV interpretation", "CP1252 with QUOTE_NONE; there are no embedded record newlines (D-003)", decisions),
        ("Strict-cure diagnostic denominators", "All observed exits are the diagnostic target (D-020)", "contracts/metric-denominators-v1.json")]
    parent = PurePosixPath(anchor.destination).parent.as_posix()
    def link(path):
        return os.path.relpath(path, parent).replace(os.sep, "/")
    text = ["**Historical record — contains superseded figures and guidance.**", "",
        f"Imported {import_date}. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.", "",
        "The original body below is preserved byte for byte. Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.", "",
        "| Superseded claim | Corrected successor | Evidence or decision |", "|---|---|---|"]
    text.extend(f"| {old} | {new} | [Authority]({link(evidence)}) |" for old, new, evidence in rows)
    text.extend(["", f"Current authority: [Gate A correction index]({link(AUTHORITY_LINKS[0])}), [recomputed spike evidence]({link(AUTHORITY_LINKS[1])}), and [controlling decisions]({link(decisions)}).", ""])
    return START_MARKER + ("\n".join(text) + "\n").encode("utf-8") + END_MARKER


def _confined(root: Path, path: Path) -> None:
    if not path.resolve().is_relative_to(root):
        raise ProvenanceError("provenance.destination")
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ProvenanceError("provenance.non_regular_file")


def _entries(root: Path) -> list[SuccessorRecord]:
    path = root / INDEX_PATH
    if not path.exists():
        if (root / "docs/provenance").exists():
            raise ProvenanceError("provenance.missing_index")
        return []
    payload = _json(path)
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "entries"} or payload["schema_version"] != "provenance-index-v1" or not isinstance(payload["entries"], list):
        raise ProvenanceError("provenance.index_schema")
    records = [SuccessorRecord.from_dict(item) for item in payload["entries"]]
    destinations = [item.destination for item in records]
    labels = [item.source_label for item in records]
    if destinations != sorted(set(destinations)) or len(labels) != len(set(labels)):
        raise ProvenanceError("provenance.duplicate_slot")
    for item in records:
        path = root / item.destination
        _confined(root, path)
        try:
            validate_successor(path.read_bytes(), item)
        except OSError:
            raise ProvenanceError("provenance.missing_successor") from None
    return records


def build_successor(source: Path, anchor: PredecessorAnchor, root: Path, policy: Policy, *, import_date: str,
                    historical_evidence: Path | None = None, replace_existing: bool = False) -> SuccessorRecord:
    """Verify, scan, and publish a complete directory containing body and index.

    Both files are prepared off to the side. Directory renames expose the old
    complete pair, no pair, or the new complete pair, never a mixed pair. A
    catchable interruption restores the old directory. Uncatchable termination
    between renames leaves the complete old tree at the fixed backup path for
    recovery on the next invocation. No private bytes enter that staging tree.
    """
    root = Path(root).resolve(strict=True)
    source = Path(source)
    target_dir = root / "docs/provenance"
    backup = root / "docs/.provenance-last-complete"
    _confined(root, target_dir)
    _confined(root, backup)
    try:
        if source.is_symlink() or not source.is_file():
            raise ProvenanceError("provenance.non_regular_file")
        if _sha256_file(source) != anchor.predecessor_sha256:
            raise ProvenanceError("provenance.predecessor_hash")
        body = source.read_bytes()
        if _hash(body) != anchor.predecessor_sha256:
            raise ProvenanceError("provenance.predecessor_hash")
        if START_MARKER in body or END_MARKER in body:
            raise ProvenanceError("provenance.markers")
        corrections = derive_corrections(body, root, historical_evidence) if historical_evidence is not None else []
        prefix = _prefix(anchor, import_date, corrections)
        data = prefix + body
        record = SuccessorRecord(anchor.source_label, anchor.predecessor_sha256, anchor.destination,
            len(prefix), _hash(prefix), _hash(body), _hash(data), MARKER_VERSION, AUTHORITY_LINKS)
        validate_successor(data, record)
        if backup.exists():
            if not target_dir.exists():
                os.replace(backup, target_dir)
            else:
                _entries(root)
                shutil.rmtree(backup)
        entries = _entries(root)
        duplicates = [item for item in entries if item.destination == anchor.destination or item.source_label == anchor.source_label]
        if duplicates:
            if not replace_existing or len(duplicates) != 1 or duplicates[0].source_label != anchor.source_label or duplicates[0].destination != anchor.destination or duplicates[0].predecessor_sha256 != anchor.predecessor_sha256:
                raise ProvenanceError("provenance.duplicate_slot")
            entries.remove(duplicates[0])
        entries.append(record)
        entries.sort(key=lambda item: item.destination)
        with tempfile.TemporaryDirectory(prefix="calico-provenance-") as directory:
            staging = Path(directory).resolve()
            candidate = staging / anchor.destination
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(data)
            index_path = staging / INDEX_PATH
            index_path.write_text(json.dumps({"schema_version": "provenance-index-v1", "entries": [item.to_dict() for item in entries]}, indent=2) + "\n", encoding="utf-8")
            findings = scan_paths(staging, sorted([anchor.destination, INDEX_PATH]), policy)
            if findings:
                raise ProvenanceError("provenance.unsafe_candidate")
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".provenance-candidate-", dir=target_dir.parent) as local_dir:
                replacement = Path(local_dir) / "tree"
                if target_dir.exists():
                    for path in target_dir.rglob("*"):
                        _confined(root, path)
                    shutil.copytree(target_dir, replacement)
                else:
                    replacement.mkdir()
                for relative in (anchor.destination, INDEX_PATH):
                    new_path = replacement / PurePosixPath(relative).relative_to("docs/provenance")
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(staging / relative, new_path)
                    with new_path.open("rb") as handle:
                        os.fsync(handle.fileno())
                try:
                    if target_dir.exists():
                        os.replace(target_dir, backup)
                    os.replace(replacement, target_dir)
                except BaseException:
                    if backup.exists() and not target_dir.exists():
                        os.replace(backup, target_dir)
                    raise
                if backup.exists():
                    shutil.rmtree(backup)
        return record
    except OSError:
        raise ProvenanceError("provenance.io") from None
