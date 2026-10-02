"""Format public documentation from closed, immutable governed evidence.

No calculation of metrics occurs here. SQL owns every count and proportion;
this module pins sources, validates identity and formats approved wording.
"""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

from calico_capture.status import StatusError, validate_capture_status_document
from calico_dbt.runner import _sha256_file
from calico_publish.allowlist import AllowlistError, load_allowlist
from calico_publish.manifest import ManifestError, validate_published_manifest_document
from tools.citation_scan.scanner import CitationError, atomic_write
from tools.docs_public.lineage import LineageProjectionError, render_mermaid, validate_projection
from tools.privacy_scan.scanner import scan_text

INPUTS = "docs/evidence/public-readme-inputs-v1.json"
EXCERPTS = "docs/evidence/sql-excerpts-v1.json"
LINEAGE = "docs/evidence/dbt-lineage-v1.json"
MANIFEST = "manifest/published-manifest-v1.json"
STATUS = "capture-status.json"
CLAIM_EXPORT = "exports/mart_claim_support.csv"
QUALITY_EXPORT = "exports/mart_release_quality.csv"
CATALOG = "contracts/dbt-input-catalog-v1.json"
CLAIM = "contracts/claim-support-v1.json"
DENOMINATORS = "contracts/metric-denominators-v1.json"
LOCAL_AUTHORITIES = (
    CATALOG, CLAIM, DENOMINATORS, LINEAGE, "contracts/ag-registry-csv-v1.json",
    "contracts/publication-exports-v3.json", "dbt/models/marts/metrics.yml",
    "docs/model-grains.md", "docs/build-modes.md", "docs/capture-runbook.md",
    "docs/powerbi-refresh-runbook.md", "docs/evidence/gate-a/correction-index-v1.json",
)
PUBLISHED_PATHS = (MANIFEST, STATUS, CLAIM_EXPORT, QUALITY_EXPORT)
_COMMIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
_INPUT_KEYS = frozenset({"schema_version", "published_data_commit", "published_sources",
    "local_sources", "accepted_releases", "latest_attempt", "parser_contract_version",
    "allowlist_version", "claim", "denominators", "release_rules", "lineage_sha256"})
MANUAL_DISCLOSURE = "Report updates use a documented manual Power BI Refresh now step. The report is not fully automatic."


class ReadmeInputError(Exception):
    """Only a fixed category; never source text, values or local paths."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def _fail(category="readme.invalid_schema"):
    raise ReadmeInputError(category)


def _unique(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            _fail("readme.duplicate_key")
        document[key] = value
    return document


def decode_document(raw: bytes):
    try:
        return json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("readme.invalid_json")


def encode_document(document: dict) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def _root(root: Path) -> Path:
    try:
        if root.is_symlink():
            _fail("readme.unsafe_root")
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            _fail("readme.unsafe_root")
        return resolved
    except (OSError, ValueError, RuntimeError):
        _fail("readme.unsafe_root")


def _path(root: Path, name: str) -> Path:
    if (not isinstance(name, str) or not name or "\\" in name
            or PurePosixPath(name).is_absolute() or any(p in {".", ".."} for p in name.split("/"))):
        _fail("readme.unsafe_path")
    candidate = root.joinpath(name)
    if candidate.is_symlink() or any(p.is_symlink() for p in candidate.parents):
        _fail("readme.unsafe_path")
    try:
        candidate.resolve().relative_to(root)
    except (OSError, ValueError, RuntimeError):
        _fail("readme.unsafe_path")
    return candidate


def _read(root: Path, name: str) -> bytes:
    try:
        return _path(root, name).read_bytes()
    except OSError:
        _fail("readme.missing_source")


def _git(root: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(root), *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        _fail("readme.git_unavailable")
    if result.returncode:
        _fail("readme.missing_published_source")
    return result.stdout


def _explicit_commit(root: Path, commit: str, *, require_fetched: bool):
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        _fail("readme.explicit_commit_required")
    try:
        resolved = _git(root, "rev-parse", "--verify", commit + "^{commit}").decode().strip()
        if resolved != commit:
            _fail("readme.unfetched_commit")
        if require_fetched:
            _git(root, "merge-base", "--is-ancestor", commit, "refs/remotes/origin/published-data")
    except ReadmeInputError:
        _fail("readme.unfetched_commit")


def project_identity(manifest, status, catalog) -> tuple[list[dict], dict]:
    """Never read accepted identity from a rejected attempt's null fields."""
    if status is None:
        _fail("readme.missing_status")
    if manifest is None or not isinstance(manifest, dict) or not manifest.get("accepted_releases"):
        _fail("readme.missing_accepted_manifest")
    try:
        authority = load_allowlist(Path(__file__).resolve().parents[2].joinpath("contracts/publication-exports-v3.json"))
        validate_published_manifest_document(manifest, allowlist=authority)
        validate_capture_status_document(status)
    except (ManifestError, StatusError, AllowlistError):
        _fail("readme.invalid_authority")
    if (not isinstance(catalog, dict) or set(catalog) != {"contract_version", "releases"}
            or type(catalog["contract_version"]) is not int or catalog["contract_version"] != 1
            or not isinstance(catalog["releases"], list) or not catalog["releases"]):
        _fail("readme.invalid_catalog")
    anchors = {}
    for row in catalog["releases"]:
        if (not isinstance(row, dict) or set(row) != {"as_of_date", "release_revision",
                "revision_fingerprint", "revision_manifest_sha256"}
                or not isinstance(row["as_of_date"], str) or not _DATE.fullmatch(row["as_of_date"])
                or type(row["release_revision"]) is not int or row["release_revision"] < 1
                or any(not isinstance(row[field], str) or not _HASH.fullmatch(row[field])
                       for field in ("revision_fingerprint", "revision_manifest_sha256"))):
            _fail("readme.invalid_catalog")
        try:
            date.fromisoformat(row["as_of_date"])
        except ValueError:
            _fail("readme.invalid_catalog")
        key = (row["as_of_date"], row["release_revision"], row["revision_fingerprint"])
        if key in anchors:
            _fail("readme.invalid_catalog")
        anchors[key] = row
    releases = []
    for row in manifest["accepted_releases"]:
        key = (row["as_of_date"], row["release_revision"], row["revision_fingerprint"])
        if key not in anchors:
            _fail("readme.catalog_identity_drift")
        releases.append({**anchors[key], "source_hashes": [
            {"source_list": source["source_list"], "sha256": source["sha256"]}
            for source in row["source_objects"]]})
    if len(releases) != len(anchors):
        _fail("readme.catalog_identity_drift")
    if status["outcome"] in {"rejected", "operational_error"}:
        if (status["last_accepted_as_of_date"] is not None
                or status["last_accepted_release_revision"] is not None):
            _fail("readme.attempt_identity_conflict")
    else:
        latest = releases[-1]
        if (status["last_accepted_as_of_date"], status["last_accepted_release_revision"]) != (
                latest["as_of_date"], latest["release_revision"]):
            _fail("readme.attempt_identity_conflict")
    return releases, {field: status[field] for field in (
        "schema_version", "trigger", "started_at_utc", "ended_at_utc", "outcome", "reason_category",
        "newer_attempt_not_accepted", "source_publication_state", "source_publication_retired_on")}


def _csv(root: Path, raw: bytes, export_name: str) -> list[dict[str, str]]:
    authority = decode_document(_read(root, "contracts/publication-exports-v3.json"))
    try:
        export = next(row for row in authority["exports"] if row["export_name"] == export_name)
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
        names = export["columns"]
        if reader.fieldnames != names:
            _fail("readme.export_schema_drift")
        records = list(reader)
        if not records or any(None in r or any(v is None for v in r.values()) for r in records):
            _fail("readme.invalid_export")
        return records
    except (KeyError, TypeError, StopIteration, ValueError, UnicodeError, csv.Error):
        _fail("readme.invalid_export")


def _capture(root: Path, commit: str, *, require_fetched: bool) -> dict:
    _explicit_commit(root, commit, require_fetched=require_fetched)
    published = {path: _git(root, "show", commit + ":" + path) for path in PUBLISHED_PATHS}
    manifest = decode_document(published[MANIFEST])
    releases, latest = project_identity(manifest, decode_document(published[STATUS]),
                                        decode_document(_read(root, CATALOG)))
    graph = decode_document(_read(root, LINEAGE))
    try:
        validate_projection(graph, project_dir=root.joinpath("dbt"), require_paths=True)
    except LineageProjectionError:
        _fail("readme.lineage_drift")
    contract = decode_document(_read(root, CLAIM))
    if (set(contract) != {"approved_wording", "claim_contract_version", "governed_surfaces",
            "numeric_support", "prohibited_claims", "required_vocabulary"}
            or contract["claim_contract_version"] != 1
            or not isinstance(contract["approved_wording"], str)):
        _fail("readme.invalid_claim_contract")
    support = _csv(root, published[CLAIM_EXPORT], "mart_claim_support")
    if len(support) != 1 or support[0]["claim_support_version"] != contract["numeric_support"]["relation_version"]:
        _fail("readme.claim_evidence_drift")
    support_row = support[0]
    identities = {(r["as_of_date"], str(r["release_revision"]), r["revision_fingerprint"]) for r in releases}
    if any((support_row[prefix + "as_of_date"], support_row[prefix + "release_revision"],
            support_row[prefix + "revision_fingerprint"]) not in identities for prefix in ("from_", "to_")):
        _fail("readme.claim_evidence_drift")
    # Rendering separators is formatting only; these integer values are SQL outputs.
    for field in ("support_count", "total_matched_entry_count", "next_total_matched_entry_count"):
        value = support_row[field]
        if not re.fullmatch(r"[0-9]+", value) or format(int(value), ",") not in contract["approved_wording"]:
            _fail("readme.claim_evidence_drift")
    quality = _csv(root, published[QUALITY_EXPORT], "mart_release_quality")
    if {(r["as_of_date"], r["release_revision"], r["revision_fingerprint"]) for r in quality} != identities:
        _fail("readme.rule_identity_drift")
    rules = []
    for row in quality:
        value = row["release_flag_rule_versions"]
        if not re.fullmatch(r"[a-z0-9_:,-]+", value):
            _fail("readme.invalid_rule_version")
        rules.append({"as_of_date": row["as_of_date"], "revision_fingerprint": row["revision_fingerprint"],
                      "rule_versions": value, "parser_contract_version": row["parser_contract_version"]})
    denominators = decode_document(_read(root, DENOMINATORS))
    if (set(denominators) != {"contract_version", "denominator_definitions", "diagnostic_role", "measure_ids"}
            or denominators["contract_version"] != 1
            or any(set(row) != {"id", "definition"} for row in denominators["denominator_definitions"])):
        _fail("readme.invalid_denominator_contract")
    document = {
        "schema_version": "public-readme-inputs-v1", "published_data_commit": commit,
        "published_sources": [{"path": path, "sha256": hashlib.sha256(published[path]).hexdigest(),
            "blob_oid": _git(root, "rev-parse", commit + ":" + path).decode().strip()} for path in sorted(PUBLISHED_PATHS)],
        "local_sources": [{"path": path, "sha256": _sha256_file(_path(root, path))} for path in sorted(LOCAL_AUTHORITIES)],
        "accepted_releases": releases, "latest_attempt": latest,
        "parser_contract_version": manifest["parser_contract_version"], "allowlist_version": manifest["allowlist_version"],
        "claim": {"approved_wording": contract["approved_wording"], "claim_contract_version": contract["claim_contract_version"],
                  "relation_version": support_row["claim_support_version"], "sql_values": {
                      field: support_row[field] for field in ("support_count", "total_matched_entry_count", "next_total_matched_entry_count")}},
        "denominators": denominators, "release_rules": rules, "lineage_sha256": graph["projection_sha256"],
    }
    if scan_text(INPUTS, encode_document(document).decode()):
        _fail("readme.unsafe_evidence")
    return document


def capture_inputs(root: Path, published_ref: str, *, write: bool = False) -> dict:
    """Explicit refresh only; callers fetch once before supplying the full commit."""
    base = _root(root)
    document = _capture(base, published_ref, require_fetched=True)
    if write:
        _write(base, INPUTS, encode_document(document))
        _write(base, EXCERPTS, encode_document(excerpt_document(base)))
    return document


def validate_inputs(root: Path, document: object) -> None:
    base = _root(root)
    if (not isinstance(document, dict) or set(document) != _INPUT_KEYS
            or document["schema_version"] != "public-readme-inputs-v1"):
        _fail()
    expected = _capture(base, document["published_data_commit"], require_fetched=False)
    if document != expected:
        _fail("readme.input_drift")


def extract_sql_excerpt(root: Path, source_path: str, ranges: list[list[int]], annotation: str) -> dict:
    base = _root(root)
    if not source_path.startswith("dbt/models/") or not source_path.endswith(".sql"):
        _fail("readme.unsafe_excerpt")
    raw = _read(base, source_path)
    lines = raw.splitlines(keepends=True)
    if not isinstance(ranges, list) or not ranges or not isinstance(annotation, str) or not annotation:
        _fail("readme.invalid_excerpt")
    selected, last = [], 0
    for pair in ranges:
        if (not isinstance(pair, list) or len(pair) != 2 or any(type(n) is not int for n in pair)
                or pair[0] <= last or pair[1] < pair[0] or pair[1] > len(lines)):
            _fail("readme.invalid_excerpt")
        selected.extend(lines[pair[0] - 1:pair[1]])
        last = pair[1]
    if not 1 <= len(selected) <= 25:
        _fail("readme.excerpt_line_limit")
    selected_bytes = b"".join(selected)
    try:
        text = selected_bytes.decode("utf-8")
    except UnicodeError:
        _fail("readme.invalid_excerpt")
    if scan_text(EXCERPTS, text + annotation):
        _fail("readme.unsafe_excerpt")
    return {"source_path": source_path, "source_sha256": _sha256_file(_path(base, source_path)),
            "ranges": ranges, "selected_sha256": hashlib.sha256(selected_bytes).hexdigest(),
            "line_count": len(selected), "selected_text": text, "annotation": annotation}


def excerpt_document(root: Path) -> dict:
    selections = (
        ("dbt/models/intermediate/int_promoted_releases.sql", [[22, 46]],
         "A matching promotion pointer wins first; otherwise the highest accepted revision wins for that date. Revisions never create a new time point."),
        ("dbt/models/intermediate/int_entity_transitions.sql", [[114, 122], [153, 161]],
         "The first source range matches the full registration key and both release identities. The second uses an anti-join to retain a missing endpoint; absence never becomes an invented status."),
        ("dbt/models/intermediate/int_delinquency_spells.sql", [[54, 78]],
         "A gap or observed non-delinquent neighbor starts a new island. A cumulative window numbers observed spells; the full model keeps onset and exit bounded and censoring explicit."),
    )
    return {"schema_version": "sql-excerpts-v1", "excerpts": [
        extract_sql_excerpt(root, path, ranges, annotation) for path, ranges, annotation in selections]}


def _write(root: Path, name: str, data: bytes):
    try:
        atomic_write(_path(root, name), data)
    except CitationError:
        _fail("readme.write_error")


TOPICS = (
    "California Charity Registry Monitor", "Build, measure, retire", "Report",
    "Questions", "Deliberate non-claims", "SQL-first responsibility boundary",
    "Architecture", "Data grains", "Source path", "Build modes", "Metric definitions",
    "Limitations", "Accepted release and latest capture attempt", "dbt lineage",
    "Techniques demonstrated", "Annotated SQL excerpts", "Why the investigation trail is here",
    "How to reproduce",
)
README_TOPIC_ORDER = (0, 3, 2, 14, 11, 4, 5, 6, 7, 8, 9, 10, 12, 13, 15, 1, 16, 17)
BLOCK_NAMES = ("architecture", "grains", "metrics", "claims", "identity", "refresh", "lineage", "excerpts", "walkthrough")
WALKTHROUGH_LINK = "Walkthrough: [Read the written walkthrough](docs/walkthrough.md)."
MIT_LICENSE = '''MIT License

Copyright (c) 2026 mrnouiouat

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''


def validate_excerpts(root: Path, document: object) -> None:
    if (not isinstance(document, dict) or set(document) != {"schema_version", "excerpts"}
            or document["schema_version"] != "sql-excerpts-v1"
            or document != excerpt_document(root)):
        _fail("readme.excerpt_drift")


def _block(name: str, body: str) -> str:
    return "<!-- calico:" + name + ":start -->\n" + body.rstrip() + "\n<!-- calico:" + name + ":end -->"


def _walkthrough_block(root: Path) -> str:
    """Render the owner-selected written walkthrough as a fixed repository link."""
    return _block("walkthrough", WALKTHROUGH_LINK)


def _source_binding(document: dict, name: str) -> dict:
    return next(row for row in document["published_sources"] if row["path"] == name)


def _identity_block(document: dict) -> str:
    manifest = _source_binding(document, MANIFEST)
    body = ["Accepted identity is from the publication manifest and the committed input catalog, independently of capture outcome.",
        "", "Pinned published-data commit: `" + document["published_data_commit"] + "`.",
        "Publication manifest SHA-256: `" + manifest["sha256"] + "`.",
        "Parser: `" + document["parser_contract_version"] + "`; publication authority: `" + document["allowlist_version"] + "`.",
        "", "| Accepted as-of date | Revision | Source fingerprint SHA-256 | Revision manifest SHA-256 |",
        "|---|---|---|---|"]
    for row in document["accepted_releases"]:
        body.append("| " + row["as_of_date"] + " | " + str(row["release_revision"]) + " | `" +
            row["revision_fingerprint"] + "` | `" + row["revision_manifest_sha256"] + "` |")
    body.extend(["", "Exact source-object hashes are retained in the [pinned input evidence](" + INPUTS + ").",
                 "Release rule versions copied from governed SQL output:"])
    for row in document["release_rules"]:
        body.append("- " + row["as_of_date"] + ": `" + row["rule_versions"] + "`; source fingerprint `" + row["revision_fingerprint"] + "`.")
    return _block("identity", "\n".join(body))


def _refresh_block(document: dict) -> str:
    attempt = document["latest_attempt"]
    status = _source_binding(document, STATUS)
    body = ("Latest capture started: `" + attempt["started_at_utc"] + "`; ended: `" + attempt["ended_at_utc"] + "`.\n"
        "Outcome: `" + attempt["outcome"] + "`; reason: `" + attempt["reason_category"] + "`.\n"
        "Capture status SHA-256: `" + status["sha256"] + "`; contract: `capture-status-v" + str(attempt["schema_version"]) + "`.\n"
        "Source publication state: `" + attempt["source_publication_state"] + "`; retired on `" +
        str(attempt["source_publication_retired_on"]) + "`.\n\n"
        "A rejected or operationally failed attempt does not advance or erase accepted release identity.\n\n" + MANUAL_DISCLOSURE)
    return _block("refresh", body)


def _claims_block(document: dict) -> str:
    claim = document["claim"]
    binding = _source_binding(document, CLAIM_EXPORT)
    body = claim["approved_wording"] + "\n\n" + (
        "Evidence: [governed claim contract](" + CLAIM + "), claim contract version `" +
        str(claim["claim_contract_version"]) + "`, SQL relation version `" + claim["relation_version"] +
        "`; published export SHA-256 `" + binding["sha256"] + "`.\n"
        "Release source fingerprints: " + ", ".join("`" + row["revision_fingerprint"] + "`" for row in document["accepted_releases"]) +
        "; parser `" + document["parser_contract_version"] + "`. These are observed publication changes.")
    return _block("claims", body)


def _metrics_block(root: Path, document: dict) -> str:
    # Exact governed descriptions are selected from the pinned simple YAML subset.
    text = _read(root, "dbt/models/marts/metrics.yml").decode("utf-8")
    columns = dict(re.findall(r'      - name: ([a-z_]+)\n        description: "([^"\n]+)"', text))
    selected = ("starting_delinquent_count", "still_delinquent_count", "observed_exit_count", "not_observed_count")
    if any(name not in columns for name in selected):
        _fail("readme.metric_description_missing")
    body = ["The published delinquent population uses exactly `Delinquent` and `Delinquent - Late Fees Due`.\n",
            "The following descriptions are copied from [governed dbt metric definitions](dbt/models/marts/metrics.yml):"]
    for name in selected:
        body.append("- `" + name + "`: " + columns[name])
    body.extend(["", "The source-reported Last Renewal measures are release-quality diagnostics. Their denominator definitions are copied from the [denominator contract](" + DENOMINATORS + "):"])
    for row in document["denominators"]["denominator_definitions"]:
        body.append("- `" + row["id"] + "`: " + row["definition"])
    body.extend(["", "Conditional precision uses starting published delinquent population diagnostic clears; eligible-exit sensitivity uses observed exits with a populated start value; all-exit sensitivity uses all observed exits. A null ratio has a zero denominator, never an invented result.",
        "Starting-cohort persistence uses actual accepted-release endpoints; a not-observed endpoint stays separate and no missing horizon is bridged. Interval proportions retain their actual calendar gap and SQL-computed Wilson bounds. Spell bounds are observation bounds, with censoring shown explicitly."])
    return _block("metrics", "\n".join(body))


def _architecture_block(graph: dict) -> str:
    # Show an immediate-edge subset of the validated fixture graph, never drawn edges.
    names = frozenset({"revision_catalog", "promotion_catalog", "int_promoted_releases",
        "int_promoted_date_spine", "int_adjacent_release_pairs", "base_admitted_registry_records",
        "stg_registry_records", "int_promoted_registry_records", "int_keyed_snapshots",
        "int_entity_transitions", "int_entity_observation_sequence", "int_delinquency_spells",
        "mart_adjacent_pair_metrics", "mart_spell_censoring_summary"})
    selected = {row["id"] for row in graph["nodes"] + graph["sources"] if row["name"] in names}
    aliases = {record["id"]: "n" + str(index) for index, record in enumerate(sorted(graph["nodes"] + graph["sources"], key=lambda r: r["id"]))}
    lines = ["flowchart LR", "  %% fixture projection_sha256 " + graph["projection_sha256"]]
    for row in sorted(graph["nodes"] + graph["sources"], key=lambda r: r["id"]):
        if row["id"] in selected:
            lines.append('  ' + aliases[row["id"]] + '["' + row["name"] + '"]')
    for edge in graph["edges"]:
        if edge["from"] in selected and edge["to"] in selected:
            lines.append("  " + aliases[edge["from"]] + " --> " + aliases[edge["to"]])
    return _block("architecture", "```mermaid\n" + "\n".join(lines) + "\n```\n\nAn immediate-edge subset of the fixture graph. The full lineage below includes all source lists and publication paths. Fixture lineage proves architecture, not real-data figures.")


def _excerpts_block(document: dict) -> str:
    parts = []
    for row in document["excerpts"]:
        ranges = ", ".join(str(first) + "–" + str(last) for first, last in row["ranges"])
        parts.extend(["**" + PurePosixPath(row["source_path"]).stem + "** — " + row["annotation"], "",
            "Source: [model](" + row["source_path"] + "), ranges " + ranges + " (selections concatenated in source order). "
            "Source bytes SHA-256 `" + row["source_sha256"] + "`; selected bytes SHA-256 `" + row["selected_sha256"] + "`.",
            "", "```sql\n" + row["selected_text"] + "```", ""])
    return _block("excerpts", "\n".join(parts))


def generate_readme(root: Path, *, write: bool = False) -> str:
    """Generate only from committed, pinned evidence; never fetch or consult a clock."""
    base = _root(root)
    inputs = decode_document(_read(base, INPUTS))
    validate_inputs(base, inputs)
    excerpts = decode_document(_read(base, EXCERPTS))
    validate_excerpts(base, excerpts)
    graph = decode_document(_read(base, LINEAGE))
    grain_source = _read(base, "docs/model-grains.md").decode("utf-8")
    table = grain_source.split("| Grain | Owning relation |\n", 1)[1].split("\n\n", 1)[0]
    grains = _block("grains", "| Grain | Owning relation |\n" + table)
    sections = [
        "Calico preserves California's published charity registry releases and turns them into a reproducible history of reported status changes. It helps readers examine changes across the published registry population and inspect the recorded history of a selected organization. Python captures and verifies source files; DuckDB and dbt SQL model the history; Power BI presents the results.\n\n"
        "**Current scope:** historical analysis of three accepted releases: July 15, August 5 and August 19, 2026. California retired the original four-file publication on September 2. Automated capture continues checking the source; incompatible candidates are rejected without changing the accepted history. Adopting the successor publication is future work requiring new source and metric contracts.\n\n"
        "`calico` reads as **cali**fornia **c**harity **o**bservatory: a cat pun that also expands to the subject, following the same pattern as `retrocat`. The formal project name is California Charity Registry Monitor.",
        "Historical commercial investigation: built the system, then measured whether it should exist, and retired it on the evidence. The commercial investigation did not substantiate a forced platform event; amnesty response did not support intervention; observed unassisted resolutions weakened the value hypothesis; external segmentation did not establish a reliable targeting rule; and renewal costs constrained the commercial proposition. These are qualitative historical findings, not calculations from this monitor.\n\n"
        "Known limitation: no organization was interviewed about willingness to pay. The retained engineering artifact explains the measurements and their limits.\n\nClarification: the retirement described here concerns the commercial proposition. The registry pipeline and its longitudinal history are retained; the source publication retirement described above separately ended new observations under the v1 contract.",
        "Start with the written walkthrough: one observed finding, the SQL behind it, a source-reading correction and the limits of the data.\n\n"
        + _walkthrough_block(base) + "\n\n"
        "| Report page | What to explore |\n|---|---|\n"
        "| Published registry change | Population coverage and observed status changes between releases. |\n"
        "| Cohort persistence | What happened to a starting published delinquent population across observed endpoints. |\n"
        "| Release quality | Source coverage, release checks and capture status. |\n"
        "| Organization lookup | A selected organization's dated observations, with official verification. |\n\n"
        "**Interactive report:** public access is pending final publication approval. The walkthrough is available now; an approved report screenshot will be added when available. Report updates use manual Power BI Refresh now.\n\n"
        "<!-- [Phase 10 report URL slot] -->",
        "- How does the published registry population change between accepted releases?\n"
        "- Which observed cohorts remain in the published delinquent population?\n"
        "- What source-reported compliance history is available for a selected organization?\n"
        "- What do schema, capture and reconciliation checks show about each source release?",
        "This is not an Attorney General system. It does not measure internal workload, staffing, processing time, enforcement performance, intent or cause. A source-reported category is an observation, not a legal or compliance outcome; disappearance is not cure. No trust, risk, fraud, quality or robustness score, ranking, partner recommendation or worst-organization leaderboard is produced.\n\n"
        "The bounded history lookup intentionally permits organization name, exact full State Charity Reg#, city/state and observed categories. Excluded identifiers, street addresses, people, contacts, raw source columns and unapproved joins remain private.",
        "Python owns downloading, hashing, decoding, structural admission and provenance. DuckDB/dbt SQL own promotion, exact-key transitions, contiguous observed spells, cohorts, diagnostics, reconciliation and every published metric. Power BI renders the governed output; it does not recreate business logic. No LLM sits between a source row and a published number.",
        _architecture_block(graph),
        grains + "\n\nThe [model-grain contracts](docs/model-grains.md) explain each owner and helper relation. Exact registration keys and full release identity prevent substring matching and revision/time-point confusion.",
        "California Attorney General [Registry reports](https://oag.ca.gov/charities/reports) supply the contracted registry lists. Landing verifies bytes before admission; current registry CSV decodes as CP1252 with QUOTE_NONE. The files contain no embedded record newlines; a newline-aware reader reproduces quote fusion. See the [capture runbook](docs/capture-runbook.md).\n\n"
        "Accepted derived tables and provenance live on the [published-data branch](https://github.com/mrnouiouat/calico/tree/published-data). The current official Registry Search Tool linked by the Registry reports page is the verification authority; the monitor is an observation history.",
        "**Fixture:** public, offline and reproducible; synthetic inputs reproduce the defect shapes without real identities. **Real:** explicit owner-controlled admitted store outside Git, verified against committed catalog anchors; the same SQL DAG and tests run after preflight. The raw archive is private, so the public checkout cannot rerun all historical real-data findings. See [build modes](docs/build-modes.md).",
        _metrics_block(base, inputs) + "\n\n" + _claims_block(inputs),
        "The final three-release panel is bounded by the accepted identities below. It is a longitudinal history across those observations; continuing the series requires a compatible source. Source publication retirement is recorded by the pinned capture status below; future capture failures do not extend this panel. Official-portal staleness remains unresolved and pending verification; the historical approximate lag is not a settled current fact.\n\n"
        "Observed exit, not observed and right censoring remain distinct. Source-reported dates do not establish onset, filing time, continuous status, intent or cause. No annualized rate or formal survival estimate is inferred. Every identifiable registration key publishes by default; an explicit private sidecar entry is the exclusion mechanism. The exact inspected export/semantic inventory remains authoritative.",
        _identity_block(inputs) + "\n\n" + _refresh_block(inputs) + "\n\nSee the [Power BI refresh runbook](docs/powerbi-refresh-runbook.md) for native manual refresh and official verification.",
        _block("lineage", "```mermaid\n" + render_mermaid(graph) + "```\n\nGenerated from the [safe fixture projection](" + LINEAGE + "); source model hashes and immediate edges are checked offline. Raw dbt manifests, compiled SQL, profiles and runtime paths are not published."),
        "- **Source integrity:** verified source bytes, explicit parsing contracts and all-or-nothing release admission.\n"
        "- **Longitudinal SQL modeling:** full-key joins, revision handling, observed status transitions and cohorts, with missing observations kept distinct.\n"
        "- **Reproducibility:** a public synthetic fixture exercises the same analytical models and tests without exposing the private archive.\n"
        "- **Operational controls:** scheduled capture, durable private preservation, CI checks and atomic publication boundaries.\n\n"
        "**One observed finding:** " + inputs["claim"]["approved_wording"] + "\n\n"
        "The source change also exercised the admission boundary: incompatible candidates were rejected while accepted release identity was preserved. The walkthrough and evidence sections below explain the findings and their limits.",
        _excerpts_block(excerpts),
        "The [investigation trail](docs/provenance/) keeps the migration record, Gate A evidence and spikes because a reader should see which interpretations failed and which figures were corrected. Machine-parseable banners distinguish historical bodies from current authority; supersedes pairs preserve predecessors. The byte/hash chain is in the [provenance index](docs/provenance/index-v1.json), and cited authority IDs resolve through the [public decision register](docs/decisions/register.md).\n\n"
        "Private planning is excluded: the [boundary decision](docs/decisions/planning-directory-not-published.md) explains the curated public surface. The archive and personal working record are not reproduction inputs.",
        "From a clean product checkout, create and activate a Python environment, then install the pinned analytical requirements:\n\n"
        "```sh\npython -m venv .venv\npython -m pip install -r requirements-dbt.txt\npython -m calico_dbt build --mode fixture\npython -m calico_dbt docs --mode fixture\npython -m tools.docs_public check\npython -m tools.citation_scan --check\npython -m unittest tests.docs_public.test_readme tests.docs_public.test_lineage -q\npython -m tools.privacy_scan --tree HEAD --history-all\n```\n\n"
        "The docs check is offline and makes no edits. An intentional evidence refresh first fetches origin/published-data, then passes its full immutable commit to `python -m tools.docs_public inputs --published-ref <full-commit>`, followed by `python -m tools.docs_public generate`. Review, regenerate citations, test and privacy-scan before committing. See [build modes](docs/build-modes.md) for real-mode requirements.\n\n"
        "Code and documentation use the [MIT license](LICENSE). Registry-derived data comes from a California public record, attributed to the California Attorney General Registry of Charities and Fundraisers through the source link above. This does not assign a new data license or claim public-domain status. The code license provides no warranty for source-reported registry data; the monitor does not replace the current official record.",
    ]
    expandable = {6: "Model architecture", 7: "Data grains and owning relations",
                  10: "Metric definitions and finding provenance",
                  12: "Accepted identities, hashes and latest capture status",
                  13: "Complete dbt lineage", 15: "Annotated SQL and source hashes"}
    rendered = []
    for index in README_TOPIC_ORDER:
        body = sections[index]
        if index in expandable:
            body = "<details>\n<summary>" + expandable[index] + "</summary>\n\n" + body + "\n\n</details>"
        rendered.append(("# " if index == 0 else "## ") + TOPICS[index] + "\n\n" + body)
    text = "\n\n".join(rendered) + "\n"
    if scan_text("README.md", text):
        _fail("readme.unsafe_readme")
    if write:
        _write(base, "README.md", text.encode())
        _write(base, "LICENSE", MIT_LICENSE.encode())
    return text


def check_readme(root: Path) -> None:
    base = _root(root)
    expected = generate_readme(base).encode()
    if _read(base, "README.md") != expected:
        _fail("readme.generated_drift")
    if _read(base, "LICENSE") != MIT_LICENSE.encode():
        _fail("readme.license_drift")
    for name in BLOCK_NAMES:
        text = expected.decode()
        if (text.count("<!-- calico:" + name + ":start -->") != 1
                or text.count("<!-- calico:" + name + ":end -->") != 1):
            _fail("readme.marker_drift")
    for name in (INPUTS, EXCERPTS):
        raw = _read(base, name)
        if raw != encode_document(decode_document(raw)):
            _fail("readme.noncanonical_evidence")
