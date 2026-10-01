"""Closed fixture architecture projection; raw dbt artifacts never leave the runner.

Only first-order manifest relationships are retained. Source SQL hashes bind
the graph to governed model definitions, without hashing volatile metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA_VERSION = "dbt-lineage-v1"
_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,127}\Z", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_PACKAGES = frozenset({"calico_registry", "calico_dbt_test_project"})
_RESOURCE_TYPES = frozenset({"model", "test", "seed", "snapshot", "analysis", "sql_operation"})
_SOURCE_NAMES = frozenset({
    "charities_may_operate", "charities_not_operating", "charities_undetermined_status",
    "charities_may_not_operate", "revision_catalog", "promotion_catalog", "capture_attempts",
    "public_eligibility_classifications",
})
_KEYS = frozenset({"schema_version", "manifest_schema_version", "mode", "nodes", "sources",
                   "edges", "model_hashes", "projection_sha256"})
_REQUIRED_PATHS = (
    ("int_promoted_releases", "int_adjacent_release_pairs"),
    ("int_adjacent_release_pairs", "int_entity_transitions"),
    ("int_promoted_releases", "int_delinquency_spells"),
    ("int_entity_transitions", "mart_adjacent_pair_metrics"),
    ("int_delinquency_spells", "mart_spell_censoring_summary"),
    ("int_promoted_releases", "mart_registry_population_coverage"),
    ("int_promoted_releases", "fct_public_status_observations"),
    ("int_promoted_releases", "dim_public_organizations"),
)


class LineageProjectionError(Exception):
    """Fixed, value-free category only; never echo input or a filesystem path."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def _fail(category: str = "lineage.invalid_graph"):
    raise LineageProjectionError(category)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("lineage.duplicate_key")
        result[key] = value
    return result


def _canonical(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def encode_projection(document: dict) -> bytes:
    validate_projection(document)
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def _identifier(identifier, name, kind):
    if not isinstance(identifier, str) or not isinstance(name, str) or not _TOKEN.fullmatch(name):
        _fail("lineage.invalid_identifier")
    parts = identifier.split(".")
    length = 3 if kind == "model" else 4
    if (len(parts) != length or parts[0] != kind or parts[1] not in _PACKAGES
            or any(not _TOKEN.fullmatch(part) for part in parts[1:]) or parts[-1] != name):
        _fail("lineage.invalid_identifier")
    if kind == "source" and (parts[2] != "runtime_input" or name not in _SOURCE_NAMES):
        _fail("lineage.invalid_identifier")


def _model_files(project_dir: Path) -> dict[str, Path]:
    try:
        if project_dir.is_symlink():
            _fail("lineage.invalid_source")
        root = project_dir.resolve(strict=True)
        model_root = root / "models"
        if model_root.is_symlink():
            _fail("lineage.invalid_source")
        files = {}
        for path in sorted(model_root.rglob("*.sql")):
            if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
                _fail("lineage.invalid_source")
            path.resolve(strict=True).relative_to(root)
            if not _TOKEN.fullmatch(path.stem) or path.stem in files:
                _fail("lineage.invalid_source")
            files[path.stem] = path
        if not files:
            _fail("lineage.invalid_source")
        return files
    except (OSError, ValueError, RuntimeError):
        _fail("lineage.invalid_source")


def _required_paths(document: dict):
    identifiers = {n["name"]: n["id"] for n in document["nodes"]}
    children = {n["id"]: [] for n in document["nodes"] + document["sources"]}
    for edge in document["edges"]:
        children[edge["from"]].append(edge["to"])
    for start, end in _REQUIRED_PATHS:
        if start not in identifiers or end not in identifiers:
            _fail("lineage.required_path_missing")
        seen, pending = set(), [identifiers[start]]
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(children[current])
        if identifiers[end] not in seen:
            _fail("lineage.required_path_missing")


def validate_projection(document: object, *, project_dir: Path | None = None,
                        require_paths: bool = False) -> None:
    """Reject malformed/stale safe output before rendering or publication."""
    if (not isinstance(document, dict) or set(document) != _KEYS
            or document["schema_version"] != SCHEMA_VERSION
            or type(document["manifest_schema_version"]) is not int
            or document["manifest_schema_version"] != 12 or document["mode"] != "fixture"):
        _fail("lineage.invalid_schema")
    ids, names = set(), set()
    for field, kind in (("nodes", "model"), ("sources", "source")):
        records = document[field]
        if not isinstance(records, list) or (field == "nodes" and not records):
            _fail()
        ordering = []
        for row in records:
            if not isinstance(row, dict) or set(row) != {"id", "name"}:
                _fail("lineage.invalid_schema")
            _identifier(row["id"], row["name"], kind)
            if row["id"] in ids or (kind, row["name"]) in names:
                _fail("lineage.duplicate_node")
            ids.add(row["id"])
            names.add((kind, row["name"]))
            ordering.append(row["id"])
        if ordering != sorted(ordering):
            _fail("lineage.invalid_order")
    edges, ordering = set(), []
    if not isinstance(document["edges"], list):
        _fail()
    for edge in document["edges"]:
        if (not isinstance(edge, dict) or set(edge) != {"from", "to"}
                or not isinstance(edge["from"], str) or not isinstance(edge["to"], str)):
            _fail()
        pair = (edge["from"], edge["to"])
        if pair in edges:
            _fail("lineage.duplicate_edge")
        if any(endpoint not in ids for endpoint in pair) or not pair[1].startswith("model."):
            _fail("lineage.unknown_endpoint")
        edges.add(pair)
        ordering.append(pair)
    if ordering != sorted(ordering):
        _fail("lineage.invalid_order")
    # A manifest lineage must be a DAG, including disconnected components.
    incoming = {identifier: 0 for identifier in ids}
    children = {identifier: [] for identifier in ids}
    for start, end in edges:
        incoming[end] += 1
        children[start].append(end)
    pending = [identifier for identifier in ids if not incoming[identifier]]
    visited = 0
    while pending:
        start = pending.pop()
        visited += 1
        for end in children[start]:
            incoming[end] -= 1
            if not incoming[end]:
                pending.append(end)
    if visited != len(ids):
        _fail("lineage.cyclic_graph")
    hashes = document["model_hashes"]
    if not isinstance(hashes, list):
        _fail("lineage.invalid_hash")
    hash_ids = []
    for row in hashes:
        if (not isinstance(row, dict) or set(row) != {"id", "sha256"}
                or not isinstance(row["sha256"], str) or not _HASH.fullmatch(row["sha256"])):
            _fail("lineage.invalid_hash")
        hash_ids.append(row["id"])
    if hash_ids != [node["id"] for node in document["nodes"]]:
        _fail("lineage.invalid_hash")
    digest = document["projection_sha256"]
    content = {key: value for key, value in document.items() if key != "projection_sha256"}
    if (not isinstance(digest, str) or not _HASH.fullmatch(digest)
            or digest != hashlib.sha256(_canonical(content)).hexdigest()):
        _fail("lineage.content_drift")
    if project_dir is not None:
        files = _model_files(project_dir)
        if {node["name"] for node in document["nodes"]} != set(files):
            _fail("lineage.source_drift")
        for node, record in zip(document["nodes"], hashes):
            try:
                digest = hashlib.sha256(files[node["name"]].read_bytes()).hexdigest()
            except OSError:
                _fail("lineage.invalid_source")
            if digest != record["sha256"]:
                _fail("lineage.source_drift")
    if require_paths:
        _required_paths(document)


def project_manifest(raw: bytes, *, project_dir: Path, package: str = "calico_registry",
                     require_paths: bool = True) -> dict:
    """Project pinned dbt 1.10/v12 bytes while their runner root still exists.

    Synthetic project arguments are Python test seams, never CLI options.
    Tests and other known non-model resources are omitted from the diagram.
    Unknown resource types and every non-closed model parent fail closed.
    """
    if not isinstance(package, str) or package not in _PACKAGES:
        _fail("lineage.invalid_identifier")
    try:
        manifest = json.loads(raw, object_pairs_hook=_unique)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("lineage.invalid_manifest")
    if not isinstance(manifest, dict):
        _fail("lineage.invalid_manifest")
    metadata = manifest.get("metadata")
    if (not isinstance(metadata, dict)
            or metadata.get("dbt_schema_version") != "https://schemas.getdbt.com/dbt/manifest/v12.json"
            or not isinstance(metadata.get("dbt_version"), str)
            or not re.fullmatch(r"1\.10\.[0-9]+", metadata["dbt_version"])):
        _fail("lineage.invalid_manifest_version")
    if any(not isinstance(manifest.get(key), dict) for key in ("nodes", "sources", "parent_map")):
        _fail("lineage.invalid_manifest")
    files = _model_files(project_dir)
    nodes, sources = [], []
    for identifier, row in manifest["nodes"].items():
        if (not isinstance(row, dict) or not isinstance(row.get("resource_type"), str)
                or row["resource_type"] not in _RESOURCE_TYPES):
            _fail("lineage.unknown_resource")
        if row["resource_type"] != "model" or row.get("package_name") != package:
            continue
        name = row.get("name")
        _identifier(identifier, name, "model")
        if row.get("unique_id") != identifier or identifier.split(".")[1] != package or name not in files:
            _fail("lineage.invalid_identifier")
        # A manifest cannot substitute another file for an otherwise safe model name.
        if row.get("original_file_path") != files[name].relative_to(project_dir.resolve()).as_posix():
            _fail("lineage.invalid_source")
        nodes.append({"id": identifier, "name": name})
    for identifier, row in manifest["sources"].items():
        if not isinstance(row, dict) or row.get("resource_type") != "source":
            _fail("lineage.unknown_resource")
        if row.get("package_name") != package:
            continue
        _identifier(identifier, row.get("name"), "source")
        if row.get("unique_id") != identifier or identifier.split(".")[1] != package:
            _fail("lineage.invalid_identifier")
        sources.append({"id": identifier, "name": row["name"]})
    nodes.sort(key=lambda row: row["id"])
    sources.sort(key=lambda row: row["id"])
    edges = []
    for node in nodes:
        parents = manifest["parent_map"].get(node["id"])
        if not isinstance(parents, list) or any(not isinstance(parent, str) for parent in parents):
            _fail("lineage.invalid_graph")
        for parent in parents:
            edges.append({"from": parent, "to": node["id"]})
    edges.sort(key=lambda row: (row["from"], row["to"]))
    try:
        hashes = [{"id": node["id"], "sha256": hashlib.sha256(files[node["name"]].read_bytes()).hexdigest()}
                  for node in nodes]
    except OSError:
        _fail("lineage.invalid_source")
    document = {"schema_version": SCHEMA_VERSION, "manifest_schema_version": 12, "mode": "fixture",
                "nodes": nodes, "sources": sources, "edges": edges, "model_hashes": hashes}
    document["projection_sha256"] = hashlib.sha256(_canonical(document)).hexdigest()
    validate_projection(document, project_dir=project_dir, require_paths=require_paths)
    return document


def render_mermaid(document: dict) -> str:
    """Render only validated immediate edges; bind the source hash in a comment."""
    validate_projection(document)
    records = sorted(document["nodes"] + document["sources"], key=lambda row: row["id"])
    aliases = {record["id"]: f"n{index}" for index, record in enumerate(records)}
    lines = ["flowchart LR", f"  %% projection_sha256 {document['projection_sha256']}"]
    for record in records:
        # Identifiers are ASCII tokens; encoding also closes Mermaid metacharacters.
        label = record["name"].replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f'  {aliases[record["id"]]}["{label}"]')
    for edge in document["edges"]:
        lines.append(f'  {aliases[edge["from"]]} --> {aliases[edge["to"]]}')
    return "\n".join(lines) + "\n"
