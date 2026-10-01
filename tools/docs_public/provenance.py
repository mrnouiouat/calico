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
REDACTION_PATH = "docs/redactions/phase-09-provenance-paths-v1.json"
LOCAL_PATH_TOKEN = b"[LOCAL_PATH_REDACTED]"
# Safe coordinates and digests, never the removed workstation values.
CORE_SPECS = {
    "migration-report": (8808, "450f0ad3ca822a8e7c5debab336783203687f2f280e948b714658763a418c1a0", "2cc8ec64050bd6c10c72b09dbc313d5a23458d95d300d22b582dfe878e2a6ae7", (
        (50, 82, 3, "d57492413ec0278442afa8a90148d1bae62303af5726916672c165fb05bb4f6f"),
        (102, 149, 4, "23851e7c0a0c34909ca782085efd1adbbf06f0a652379a4f716ac1cb22a521d0"),
        (7197, 7211, 104, "6edcedf2486800881f3ea27c5e6e1b5edc2061ff0fea711839c8d8e902be0373"),
        (8624, 8659, 117, "3ba7c097807ea772135216b74f50b5f9c60c67dcdec81c7f8f965a1cf0557392"))),
    "gate-a-evidence": (11086, "3c7943ad82184cd3e54ab0fd844c2b3ec2732fc63eb05395bd53d9662890cf62", "998dcb90fc82ff3bb1b8ea5d8ec574a1f215631297c8b6855d1437f1a7778152", (
        (8089, 8124, 152, "3ba7c097807ea772135216b74f50b5f9c60c67dcdec81c7f8f965a1cf0557392"),)),
}
# The source labels in index-v1 resolve through this closed canonical inventory.
# These paths identify sources; public checks never open the private originals.
SOURCE_SPECS = {
    "migration-report": ("MIGRATION-REPORT.md", "450f0ad3ca822a8e7c5debab336783203687f2f280e948b714658763a418c1a0", "docs/provenance/MIGRATION-REPORT.md"),
    "gate-a-evidence": ("GATE-A-EVIDENCE.md", "3c7943ad82184cd3e54ab0fd844c2b3ec2732fc63eb05395bd53d9662890cf62", "docs/provenance/GATE-A-EVIDENCE.md"),
    "spike-manifest": (".planning/spikes/MANIFEST.md", "8004a7891823c98f7ee17c4332190cd217d558e11097ea846c40615d048226f3", "docs/provenance/spikes/MANIFEST.md"),
    "spike-conventions": (".planning/spikes/CONVENTIONS.md", "5d69a9d50c1eaa9d8fb4335617ea065409df92aa94c375d207bc3f2857edd144", "docs/provenance/spikes/CONVENTIONS.md"),
    "spike-001-readme": (".planning/spikes/001-archive-sample-validation/README.md", "53d3b172da598ff1bc4df6ecefd40e31ab91b39773b6aff894f40d746c8d4edb", "docs/provenance/spikes/001-archive-sample-validation/README.md"),
    "spike-001-json": (".planning/spikes/001-archive-sample-validation/archive-sample-manifest.json", "3e70afb91b97a6ab2458ef8fbef870e203298b9bce0a5752d48e39e4f0df03b6", "docs/provenance/spikes/001-archive-sample-validation/archive-sample-manifest.json.md"),
    "spike-002-readme": (".planning/spikes/002-entity-change-validation/README.md", "349b619aa6f6111f7ec9b3e4dbb38e44d1b95934cabdc2ff86a6f96c60fa2e5e", "docs/provenance/spikes/002-entity-change-validation/README.md"),
    "spike-002-json": (".planning/spikes/002-entity-change-validation/entity-changes.json", "b6877890c5961664d4579bac1cd17b3149b2b983dc9819b6a0bc26bee2f238c2", "docs/provenance/spikes/002-entity-change-validation/entity-changes.json.md"),
    "spike-003-readme": (".planning/spikes/003-duration-metrics/README.md", "09bb320d02b31820a4caed7805e2c5903f5785e556126167c63540ceb39fab97", "docs/provenance/spikes/003-duration-metrics/README.md"),
    "spike-005-readme": (".planning/spikes/005-project-recommendation/README.md", "edb47f988e573c913dbfe95d07f6701f73ed217430ff1ecdcf6d6c4537e5818c", "docs/provenance/spikes/005-project-recommendation/README.md"),
}


def canonical_source(record):
    return SOURCE_SPECS.get(record.source_label, (record.destination,))[0]


@dataclass(frozen=True)
class RedactionRegion:
    start: int
    end: int
    line: int
    original_sha256: str
    category: str = "absolute_local_path"


@dataclass(frozen=True)
class RedactionChain:
    original_bytes: int
    original_sha256: str
    body_sha256: str
    regions: tuple[RedactionRegion, ...]
    preserved_sha256: tuple[str, ...]

    @classmethod
    def from_dict(cls, item):
        try:
            if not isinstance(item, dict) or set(item) != set(cls.__dataclass_fields__):
                raise ValueError()
            if any(set(row) != set(RedactionRegion.__dataclass_fields__) for row in item["regions"]):
                raise ValueError()
            regions = tuple(RedactionRegion(**row) for row in item["regions"])
            return cls(item["original_bytes"], item["original_sha256"], item["body_sha256"], regions, tuple(item["preserved_sha256"]))
        except (ValueError, TypeError, KeyError):
            raise ProvenanceError("provenance.redaction_schema") from None


def redact_core_body(body: bytes, anchor: "PredecessorAnchor") -> tuple[bytes, RedactionChain]:
    """Replace only the closed D-04 regions after verifying the whole original."""
    spec = CORE_SPECS.get(anchor.source_label)
    if spec is None or len(body) != spec[0] or _hash(body) != spec[1] or anchor.predecessor_sha256 != spec[1]:
        raise ProvenanceError("provenance.predecessor_hash")
    regions = tuple(RedactionRegion(*item) for item in spec[3])
    parts, preserved, cursor = [], [], 0
    for region in regions:
        if (_hash(body[region.start:region.end]) != region.original_sha256
                or body[:region.start].count(b"\n") + 1 != region.line):
            raise ProvenanceError("provenance.redaction_region")
        chunk = body[cursor:region.start]
        parts.extend((chunk, LOCAL_PATH_TOKEN))
        preserved.append(_hash(chunk))
        cursor = region.end
    parts.append(body[cursor:])
    preserved.append(_hash(body[cursor:]))
    safe = b"".join(parts)
    chain = RedactionChain(len(body), _hash(body), _hash(safe), regions, tuple(preserved))
    validate_redaction_chain(safe, anchor, chain)
    return safe, chain


def validate_redaction_chain(body: bytes, anchor: "PredecessorAnchor", chain: RedactionChain) -> None:
    spec = CORE_SPECS.get(anchor.source_label)
    if (spec is None or type(chain.original_bytes) is not int or chain.original_bytes != spec[0]
            or anchor.predecessor_sha256 != spec[1] or chain.original_sha256 != spec[1]
            or chain.body_sha256 != spec[2] or _hash(body) != spec[2]
            or chain.regions != tuple(RedactionRegion(*row) for row in spec[3])
            or len(chain.preserved_sha256) != len(chain.regions) + 1):
        raise ProvenanceError("provenance.redaction_chain")
    cursor, shift = 0, 0
    for number, region in enumerate(chain.regions):
        start = region.start + shift
        end = start + len(LOCAL_PATH_TOKEN)
        if (_hash(body[cursor:start]) != chain.preserved_sha256[number]
                or body[start:end] != LOCAL_PATH_TOKEN):
            raise ProvenanceError("provenance.redaction_chain")
        cursor = end
        shift += len(LOCAL_PATH_TOKEN) - (region.end - region.start)
    if len(body) != chain.original_bytes + shift or _hash(body[cursor:]) != chain.preserved_sha256[-1]:
        raise ProvenanceError("provenance.redaction_chain")


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
    redaction_chain: RedactionChain | None = None

    def to_dict(self) -> dict[str, Any]:
        result = json.loads(json.dumps(asdict(self)))
        result["authority_links"] = list(self.authority_links)
        if self.redaction_chain is None:
            result.pop("redaction_chain")
        return result

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "SuccessorRecord":
        try:
            if set(item) not in (set(cls.__dataclass_fields__), set(cls.__dataclass_fields__) - {"redaction_chain"}):
                raise ValueError()
            values = {**item, "authority_links": tuple(item["authority_links"])}
            if "redaction_chain" in item:
                values["redaction_chain"] = RedactionChain.from_dict(item["redaction_chain"])
            return cls(**values)
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
        if (not body or _hash(prefix) != record.prefix_sha256 or _hash(body) != record.body_sha256
                or _hash(data) != record.successor_sha256):
            raise ProvenanceError("provenance.hash_chain")
        if record.redaction_chain is None:
            if record.body_sha256 != record.predecessor_sha256:
                raise ProvenanceError("provenance.hash_chain")
        else:
            validate_redaction_chain(body, PredecessorAnchor(record.source_label, record.predecessor_sha256, record.destination), record.redaction_chain)
        if record.destination.endswith(".json.md"):
            try:
                json.loads(body)
            except (ValueError, UnicodeError):
                raise ProvenanceError("provenance.json_envelope") from None
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


@dataclass(frozen=True)
class CorrectionRow:
    claim: str
    superseded: str
    corrected: str
    authority: str
    body_lines: tuple[int, ...]

    def render(self):
        lines = ", ".join(str(number) for number in self.body_lines)
        return (f"{self.claim} (body lines {lines}): {self.superseded}", self.corrected, self.authority)


GUIDANCE_RULES = (
    (rb"Public Registry Operations Monitor|California Charity Observatory", "Predecessor project name", "California Charity Registry Monitor (D-001)", "D-001"),
    (rb"aggregate[- ]only|aggregate[- ]level|publish[^\r\n]*aggregate|aggregate[^\r\n]*(?:public|only)|org.level[^\r\n]*private", "Aggregate-only publication", "Bounded named organization history alongside aggregates (D-007)", "D-007"),
    (rb"(?:Power BI[^\r\n]*Evidence|Evidence[^\r\n]*Power BI)", "Conditional BI choice", "One Power BI implementation; documented manual refresh fallback (D-008)", "D-008"),
    (rb"(?:8|12)[^\r\n]{0,35}releases|release.count[^\r\n]*readiness", "Release-count readiness", "Five simultaneous estimability conditions; no formal survival analysis in v1 (D-010)", "D-010"),
    (rb"archive[^\r\n]*census|census[^\r\n]*archive", "Archive census prerequisite", "Archive census is outside v1 (D-012)", "D-012"),
    (rb"newline.aware|embedded[^\r\n]*newline|real[^\r\n]*CSV reader", "Default CSV interpretation", "CP1252 with QUOTE_NONE; no embedded record newlines (D-003)", "D-003"),
)
SPIKE_GUIDANCE_RULES = (
    (rb"Turnbull|restricted.mean|constant.hazard|survival|180[^\r\n]*365|365[^\r\n]*730", "Deferred duration analysis", "No Turnbull survival, restricted mean duration, constant-hazard equivalent or standardized 30-day risk in v1; final panel has three releases spanning 35 days. Separate evaluation requires all five estimability conditions (D-010)", "D-010"),
    (rb"strict[^\r\n]*cur|conditional.precision|registry.wide.precision|unconditional.sensitivity", "Historical diagnostic denominator", "Historical strict-cure percentages are not current governed metrics; the current last-renewal diagnostic uses all observed exits independently of parser repair (D-006)", "D-006"),
)


def derive_guidance(body: bytes, *, extended: bool = False) -> list[CorrectionRow]:
    """Retain only supersession rules actually observed in this body."""
    rows = []
    for pattern, claim, corrected, decision in (*GUIDANCE_RULES, *(SPIKE_GUIDANCE_RULES if extended else ())):
        locations = tuple(i for i, line in enumerate(body.splitlines(), 1) if re.search(pattern, line, re.I))
        if locations:
            rows.append(CorrectionRow(claim, claim, corrected, AUTHORITY_LINKS[2], locations))
    return rows


def validate_correction_rows(body: bytes, rows: list[CorrectionRow]) -> None:
    """Reject unsupported occurrences without reflecting any historical text."""
    lines = body.splitlines()
    for row in rows:
        if (not row.body_lines or row.body_lines != tuple(sorted(set(row.body_lines)))
                or any(type(i) is not int or i < 1 or i > len(lines) for i in row.body_lines)):
            raise ProvenanceError("provenance.correction_occurrence")
        guidance = [entry for entry in (*GUIDANCE_RULES, *SPIKE_GUIDANCE_RULES) if entry[1] == row.claim]
        if guidance:
            expected = next((candidate for candidate in derive_guidance(body, extended=True) if candidate.claim == row.claim), None)
            if row != expected:
                raise ProvenanceError("provenance.correction_occurrence")
        else:
            allowed = {
                "published delinquent population transition definition": ("7,733", "7,737; delinquency definition (D-006), independent of parser repair", AUTHORITY_LINKS[2]),
                "2026-07-15 total rows": ("557,065", "557,067", AUTHORITY_LINKS[1]),
                "2026-08-05 total rows": ("557,289", "557,291", AUTHORITY_LINKS[1]),
                "2026-08-05 to 2026-08-19 entries": ("6", "2", AUTHORITY_LINKS[2]),
                "Newly delinquent definition": ("7,758", "7,750 entries + 8 delinquent-on-new-keys", AUTHORITY_LINKS[2]),
            }
            if allowed.get(row.claim) != (row.superseded, row.corrected, row.authority):
                raise ProvenanceError("provenance.correction_authority")
            token = re.compile(rb"(?<![0-9,])" + re.escape(row.superseded.encode()) + rb"(?![0-9,])")
            if any(not token.search(lines[i - 1]) for i in row.body_lines):
                raise ProvenanceError("provenance.correction_occurrence")
            if row.superseded == "6" and any(not (b"2026-08-05" in lines[i-1] and b"2026-08-19" in lines[i-1] and re.search(rb"entr", lines[i-1], re.I)) for i in row.body_lines):
                raise ProvenanceError("provenance.correction_occurrence")


def derive_core_corrections(body: bytes, root: Path, project: Path) -> list[CorrectionRow]:
    """Format the decided supersedes pairs; analytical counts are evidence inputs."""
    index = _json(root / AUTHORITY_LINKS[0])
    evidence_path = root / AUTHORITY_LINKS[1]
    evidence = _json(evidence_path)
    try:
        records = [row for row in index["corrections"] if row["successor_file"] == evidence_path.name]
        if len(records) != 1 or _sha256_file(evidence_path) != records[0]["successor_sha256"]:
            raise ProvenanceError("provenance.evidence_anchor")
        if index["gate_a_evidence"]["sha256"] != CORE_SPECS["gate-a-evidence"][1]:
            raise ProvenanceError("provenance.evidence_anchor")
        project_body = project.read_bytes()
        table = project_body.split(b"## Superseded Figures and Claims", 1)[1].split(b"\n## ", 1)[0]
        project_rows = [line.split(b"|") for line in table.splitlines() if line.startswith(b"|")]
        pairs = (
            ("published delinquent population transition definition", "7,733", "7,737", "D-006", AUTHORITY_LINKS[2]),
            ("2026-07-15 total rows", "557,065", "557,067", None, AUTHORITY_LINKS[1]),
            ("2026-08-05 total rows", "557,289", "557,291", None, AUTHORITY_LINKS[1]),
            ("2026-08-05 to 2026-08-19 entries", "6", "2", None, AUTHORITY_LINKS[2]),
            ("Newly delinquent definition", "7,758", "7,750 entries + 8 delinquent-on-new-keys", None, AUTHORITY_LINKS[2]),
        )
        result = []
        for claim, old, new, decision, authority in pairs:
            # The single-digit correction needs its transition and entry context.
            token = re.compile(rb"(?<![0-9,])" + re.escape(old.encode()) + rb"(?![0-9,])")
            locations = tuple(i for i, line in enumerate(body.splitlines(), 1)
                if token.search(line) and (old != "6" or (b"2026-08-05" in line and b"2026-08-19" in line and re.search(rb"entr", line, re.I))))
            if not locations:
                continue
            pair_rows = [row for row in project_rows if len(row) >= 4 and old.encode() in row[1] and new.split(" entries")[0].encode() in row[2]]
            if len(pair_rows) != 1:
                raise ProvenanceError("provenance.correction_authority")
            if old in ("557,065", "557,289"):
                as_of = claim[:10]
                values = [item["total_row_count"] for item in evidence["coverage"] if item["as_of_date"] == as_of]
                if values != [int(new.replace(",", ""))]:
                    raise ProvenanceError("provenance.correction_authority")
            corrected = new + (f"; delinquency definition ({decision}), independent of parser repair" if decision else "")
            result.append(CorrectionRow(claim, old, corrected, authority, locations))
        validate_correction_rows(body, result)
        return result
    except (KeyError, IndexError, TypeError, OSError):
        raise ProvenanceError("provenance.evidence_schema") from None


def derive_spike_corrections(body: bytes, root: Path, historical_evidence: Path,
                             kind: str, project: Path | None = None) -> list[CorrectionRow]:
    """Project already computed evidence, retaining each source occurrence.

    No analytical totals or membership sets are recomputed here. The correction
    index authenticates both the predecessor and the public successor bytes.
    """
    if kind not in ("001", "002"):
        raise ProvenanceError("provenance.evidence_schema")
    evidence = f"docs/evidence/gate-a/spike-{kind}-successor-v1.json"
    index = _json(root / AUTHORITY_LINKS[0])
    current = _json(root / evidence)
    historical = _json(historical_evidence)
    try:
        anchors = [row for row in index["corrections"] if row["successor_file"] == Path(evidence).name]
        if (len(anchors) != 1 or _sha256_file(root / evidence) != anchors[0]["successor_sha256"]
                or _sha256_file(historical_evidence) != anchors[0]["supersedes"]["predecessor_sha256"]):
            raise ProvenanceError("provenance.evidence_anchor")
        pairs = []
        if kind == "001":
            release = current["as_of_date"]
            for item in historical["entries"]:
                if item["logical_release"] == release:
                    old, new = item["parsed_rows"], current["logical_list_totals"][item["list"]]
                    if old != new:
                        pairs.append((f"{release} {item['list']} parsed rows (parser repair)", old, new, "parsed_rows"))
            # PROJECT records this supersedes pair; never sum source rows here.
            if project is None:
                raise ProvenanceError("provenance.correction_authority")
            table = project.read_bytes().split(b"## Superseded Figures and Claims", 1)[1].split(b"\n## ", 1)[0]
            match = [line.split(b"|") for line in table.splitlines()
                     if line.startswith(b"|") and f"{current['release_total']:,}".encode() in line.split(b"|")[2]]
            if len(match) != 1:
                raise ProvenanceError("provenance.correction_authority")
            old_values = re.findall(rb"[0-9]+(?:,[0-9]{3})+", match[0][1])
            new_values = re.findall(rb"[0-9]+(?:,[0-9]{3})+", match[0][2])
            if (f"{current['release_total']:,}".encode() not in new_values
                    or new_values.index(f"{current['release_total']:,}".encode()) >= len(old_values)):
                raise ProvenanceError("provenance.correction_authority")
            old = old_values[new_values.index(f"{current['release_total']:,}".encode())]
            pairs.append((f"{release} total rows (parser repair)", int(old.replace(b",", b"")), current["release_total"], None))
        else:
            for item in current["coverage"]:
                release = item["as_of_date"]
                prior = historical["release_coverage"].get(release)
                if prior is None:
                    continue
                for old_key, new_key in (("rows", "total_row_count"), ("keyless_rows", "keyless_row_count")):
                    old, new = prior[old_key], item[new_key]
                    if old != new:
                        pairs.append((f"{release} {old_key} (parser repair)", old, new, old_key))
        result = []
        is_json = body.lstrip().startswith((b"{", b"["))
        contexts = {}
        release_context, list_context = None, None
        for number, line in enumerate(body.splitlines(), 1):
            release_match = re.search(rb'"logical_release"\s*:\s*"([0-9-]{10})"|"([0-9-]{10})"\s*:\s*\{', line)
            if release_match:
                release_context = next(group for group in release_match.groups() if group is not None).decode()
            list_match = re.search(rb'"list"\s*:\s*"([a-z-]+)"', line)
            if list_match:
                list_context = list_match.group(1).decode()
            contexts[number] = (release_context, list_context)
        for claim, old, new, field in pairs:
            token = re.compile(rb"(?<![0-9])" + re.escape(str(old).encode()) + rb"(?![0-9])|(?<![0-9,])" + re.escape(f"{old:,}".encode()) + rb"(?![0-9,])")
            for number, line in enumerate(body.splitlines(), 1):
                release_context, list_context = contexts[number]
                correct_context = (not is_json or field is None or
                    (release_context == claim[:10] and f'"{field}"'.encode() in line
                     and (kind != "001" or list_context in claim)))
                if token.search(line) and correct_context:
                    result.append(CorrectionRow(claim, f"{old:,}", f"{new:,}", evidence, (number,)))
        if kind == "002":
            for field, date_key in (("from_keyed", "from"), ("to_keyed", "to")):
                release = historical["comparison"][date_key]
                prior_hash = historical["membership_sets"][field]["sha256"]
                matches = [row for row in current["keyed_membership"] if row["as_of_date"] == release]
                if len(matches) != 1 or prior_hash != matches[0]["sha256"]:
                    raise ProvenanceError("provenance.correction_authority")
                for number, line in enumerate(body.splitlines(), 1):
                    if prior_hash.encode() in line:
                        result.append(CorrectionRow(f"{release} canonical keyed membership", prior_hash,
                            f"{matches[0]['sha256']}; confirmed by committed recomputation, no recalculation here", evidence, (number,)))
        return result
    except (KeyError, IndexError, TypeError, OSError, ValueError):
        raise ProvenanceError("provenance.evidence_schema") from None


def _prefix(anchor: PredecessorAnchor, import_date: str, corrections: list[tuple[str, str, str]], *, guidance=(), redacted=False) -> bytes:
    try:
        if date.fromisoformat(import_date).isoformat() != import_date:
            raise ValueError()
    except (ValueError, TypeError):
        raise ProvenanceError("provenance.import_date") from None
    decisions = "docs/decisions/register.md"
    rows = [*corrections, *guidance]
    parent = PurePosixPath(anchor.destination).parent.as_posix()
    def link(path):
        return os.path.relpath(path, parent).replace(os.sep, "/")
    text = ["**Historical record — contains superseded figures and guidance.**", "",
        f"Imported {import_date}. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.", "",
        ("The body below preserves every original byte outside the recorded local-path regions. The D-04 redaction record links the original, redacted body and bannered successor. " if redacted else "The original body below is preserved byte for byte. ") + "Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.", "",
        "| Superseded claim | Corrected successor | Evidence or decision |", "|---|---|---|"]
    text.extend(f"| {old} | {new} | [Authority]({link(evidence)}) |" for old, new, evidence in rows)
    if redacted:
        text.extend(["", f"Redaction lineage: [five-location D-04 record]({link(REDACTION_PATH)})."])
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
    if (records != sorted(records, key=canonical_source) or len(destinations) != len(set(destinations))
            or len(labels) != len(set(labels))):
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
                    historical_evidence: Path | None = None, replace_existing: bool = False,
                    observed_only: bool = True, project: Path | None = None,
                    spike_kind: str | None = None) -> SuccessorRecord:
    """Verify, scan, and publish a complete directory containing body and index.

    Both files are prepared off to the side. Directory renames expose the old
    complete pair, no pair, or the new complete pair, never a mixed pair. A
    catchable interruption restores the old directory. Uncatchable termination
    between renames leaves the complete old tree at the fixed backup path for
    recovery on the next invocation. No private bytes enter that staging tree.
    """
    root = Path(root).resolve(strict=True)
    if observed_only is not True:
        raise ProvenanceError("provenance.unobserved_guidance")
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
        if not body:
            raise ProvenanceError("provenance.empty_body")
        if START_MARKER in body or END_MARKER in body:
            raise ProvenanceError("provenance.markers")
        redaction_chain = None
        if anchor.source_label in CORE_SPECS:
            body, redaction_chain = redact_core_body(body, anchor)
        corrections = derive_corrections(body, root, historical_evidence) if historical_evidence is not None and spike_kind is None else []
        if spike_kind is not None:
            if historical_evidence is None:
                raise ProvenanceError("provenance.evidence_schema")
            corrections = [row.render() for row in derive_spike_corrections(body, root, historical_evidence, spike_kind, project)]
        if project is not None and spike_kind is None:
            corrections = [row.render() for row in derive_core_corrections(body, root, project)]
        guidance = derive_guidance(body, extended=anchor.source_label in ("spike-001-readme", "spike-001-json", "spike-002-json", "spike-003-readme", "spike-005-readme"))
        validate_correction_rows(body, guidance)
        prefix = _prefix(anchor, import_date, corrections,
            guidance=[row.render() for row in guidance], redacted=redaction_chain is not None)
        data = prefix + body
        record = SuccessorRecord(anchor.source_label, anchor.predecessor_sha256, anchor.destination,
            len(prefix), _hash(prefix), _hash(body), _hash(data), MARKER_VERSION, AUTHORITY_LINKS, redaction_chain)
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
        entries.sort(key=canonical_source)
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


def validate_index(root: Path, expected: tuple[PredecessorAnchor, ...]) -> list[SuccessorRecord]:
    """Require exact source slots, not merely a valid subset of records."""
    records = _entries(root)
    slots = {(row.source_label, row.destination, row.predecessor_sha256) for row in records}
    wanted = {(row.source_label, row.destination, row.predecessor_sha256) for row in expected}
    if not expected or len(wanted) != len(expected) or slots != wanted or len(records) != len(expected):
        raise ProvenanceError("provenance.index_completeness")
    actual_paths = {path.relative_to(root).as_posix() for path in (root / "docs/provenance").rglob("*") if path.is_file() and path.relative_to(root).as_posix() != INDEX_PATH}
    if actual_paths != {row.destination for row in records}:
        raise ProvenanceError("provenance.index_completeness")
    return records


def validate_complete_index(root: Path) -> list[SuccessorRecord]:
    """Enforce ten locked slots and resolve evidence within the public root.

    The decision register is the explicit next-plan (09-04) dependency. This
    check verifies every evidence target now; citation completeness verifies
    the register once that separately owned output is produced.
    """
    expected = tuple(PredecessorAnchor(label, digest, destination)
                     for label, (_, digest, destination) in SOURCE_SPECS.items())
    records = validate_index(root, expected)
    for record in records:
        prefix = (root / record.destination).read_bytes()[:record.prefix_bytes]
        for target in re.findall(rb"\[[^\]]+\]\(([^)]+)\)", prefix):
            path = (root / record.destination).parent / target.decode("utf-8")
            _confined(root.resolve(), path)
            if not path.is_file() and path.resolve() != (root / AUTHORITY_LINKS[2]).resolve():
                raise ProvenanceError("provenance.missing_authority")
    return records


def redaction_record(records: list[SuccessorRecord]) -> dict[str, Any]:
    core = sorted((record for record in records if record.redaction_chain is not None), key=lambda row: row.destination)
    if (len(core) != 2 or {record.source_label for record in core} != set(CORE_SPECS)
            or sum(len(record.redaction_chain.regions) for record in core) != 5):
        raise ProvenanceError("provenance.redaction_cardinality")
    return {"schema_version": "phase-09-provenance-paths-v1", "decision": "D-04", "category": "absolute_local_path",
        "replacement": LOCAL_PATH_TOKEN.decode(), "count": 5, "entries": [record.to_dict() for record in core]}


def validate_redaction_record(payload: dict[str, Any], records: list[SuccessorRecord]) -> None:
    if payload != redaction_record(records):
        raise ProvenanceError("provenance.redaction_record")


def build_core_successors(sources: dict[str, Path], root: Path, policy: Policy, *, project: Path,
                          import_date: str) -> list[SuccessorRecord]:
    """Validate both originals and all five replacements before publishing either.

    The common docs directory is prepared from public bytes and swapped as one
    complete unit so the external redaction record travels with both successors.
    """
    root = Path(root).resolve(strict=True)
    if set(sources) != set(CORE_SPECS):
        raise ProvenanceError("provenance.redaction_cardinality")
    anchors = {}
    for label, source in sources.items():
        if source.is_symlink() or not source.is_file():
            raise ProvenanceError("provenance.non_regular_file")
        spec = CORE_SPECS[label]
        anchor = PredecessorAnchor(label, spec[1], "docs/provenance/" + ("MIGRATION-REPORT.md" if label == "migration-report" else "GATE-A-EVIDENCE.md"))
        try:
            redact_core_body(source.read_bytes(), anchor)
        except OSError:
            raise ProvenanceError("provenance.io") from None
        anchors[label] = anchor
    docs, backup = root / "docs", root / ".docs-provenance-last-complete"
    _confined(root, docs)
    _confined(root, backup)
    if backup.exists():
        raise ProvenanceError("provenance.pending_recovery")
    for path in docs.rglob("*"):
        _confined(root, path)
    try:
        with tempfile.TemporaryDirectory(prefix=".docs-provenance-candidate-", dir=root) as directory:
            staging = Path(directory).resolve()
            shutil.copytree(docs, staging / "docs")
            produced = [build_successor(sources[label], anchors[label], staging, policy, import_date=import_date,
                observed_only=True, project=project) for label in sorted(sources)]
            records = _entries(staging)
            payload = redaction_record(records)
            validate_redaction_record(payload, records)
            redactions = staging / REDACTION_PATH
            redactions.parent.mkdir(parents=True, exist_ok=True)
            redactions.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            candidates = sorted([row.destination for row in produced] + [INDEX_PATH, REDACTION_PATH])
            if scan_paths(staging, candidates, policy):
                raise ProvenanceError("provenance.unsafe_candidate")
            for relative in candidates:
                with (staging / relative).open("rb") as handle:
                    os.fsync(handle.fileno())
            try:
                os.replace(docs, backup)
                os.replace(staging / "docs", docs)
            except BaseException:
                if backup.exists() and not docs.exists():
                    os.replace(backup, docs)
                raise
            shutil.rmtree(backup)
            return produced
    except OSError:
        raise ProvenanceError("provenance.io") from None
