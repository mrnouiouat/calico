"""Closed Gate E evidence authority; recorded observations are not live checks."""
from __future__ import annotations

import ast
import copy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

SCHEMA_PATH = "docs/evidence/gate-e/portfolio-ready-v1.schema.json"
AUTHORITY_PATH = "docs/evidence/gate-e/portfolio-ready-v1.json"
MARKDOWN_PATH = "docs/provenance/GATE-E-EVIDENCE.md"
EVIDENCE_CLASSES = ("fixture_hosted_replay", "live_source_observation",
                    "actual_schedule_observation", "real_restore_republish_observation")
CONDITION_TEXTS = (
    "a public fixture build passes dbt build in CI",
    "a local real-data build passes the same models and tests",
    "release admission rejects the known structural failure cases",
    "scheduled and manually dispatched workflows prove accepted, no_new_release, and rejected without exposing raw source records",
    "an accepted release automatically rebuilds DuckDB/dbt and atomically updates public manifests and approved aggregate and named lookup outputs",
    "the Power BI semantic model refreshes from stable published URLs and the public report reflects the new accepted release, with one manual refresh as documented fallback",
    "a publication gate proves only allowlisted aggregate fields and approved organization-history fields enter the public report model",
    "the README explains question, data grain, lineage, limitations, refresh procedure, and findings",
    "a concise written walkthrough exists",
    "core analytical logic is implemented and tested in DuckDB/dbt SQL with lineage and representative SQL techniques visible in the README, and Python and Power BI do not duplicate those calculations",
)
_PRODUCT = Path(__file__).resolve().parents[2]
_SHA256 = r"[0-9a-f]{64}"
_SHA1 = r"[0-9a-f]{40}"


class GateEEvidenceError(Exception):
    """Only fixed category diagnostics cross the evidence boundary."""
    def __init__(self, category="gate_e.invalid_evidence"):
        self.category = category
        super().__init__(category)


def _fail(category="gate_e.invalid_evidence"):
    raise GateEEvidenceError(category)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("gate_e.duplicate_key")
        result[key] = value
    return result


def _decode(raw):
    if type(raw) not in (str, bytes) or len(raw) > 2_000_000:
        _fail()
    try:
        return json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: _fail())
    except (ValueError, UnicodeError, TypeError, RecursionError):
        _fail()


def _encode(document):
    return (json.dumps(document, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def _shape(value, schema):
    if "oneOf" in schema:
        matches = 0
        for choice in schema["oneOf"]:
            try:
                _shape(value, choice)
                matches += 1
            except GateEEvidenceError:
                pass
        if matches != 1:
            _fail()
        return
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None)}
    kind = schema["type"]
    if type(value) is not types[kind]:
        _fail()
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        _fail()
    if "enum" in schema and value not in schema["enum"]:
        _fail()
    if kind == "object":
        if set(value) != set(schema["required"]) or schema["additionalProperties"] is not False:
            _fail()
        for key in schema["required"]:
            _shape(value[key], schema["properties"][key])
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 1000):
            _fail()
        for item in value:
            _shape(item, schema["items"])
    elif kind == "integer":
        if not schema.get("minimum", 0) <= value <= schema.get("maximum", 10**18):
            _fail()
    elif kind == "string":
        if not schema.get("minLength", 1) <= len(value) <= schema.get("maxLength", 4096):
            _fail()
        if re.fullmatch(schema.get("pattern", r"[^\r\n]+"), value) is None:
            _fail()


def _date(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail("gate_e.invalid_date")
    try:
        observed = date.fromisoformat(value)
    except ValueError:
        _fail("gate_e.invalid_date")
    if observed > datetime.now(timezone.utc).date():
        _fail("gate_e.invalid_date")
    return observed


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _fail()


def _safe_text(value):
    from tools.privacy_scan.scanner import scan_text
    if (type(value) is not str or not value or len(value) > 4096 or
            any(c in value for c in "\r\n|<>`") or "CALICO_REPLAY_CANARY_" in value or
            "PRIVATE_SENTINEL" in value or "calico-build" in value or ".planning" in value):
        _fail("gate_e.unsafe_public_text")
    try:
        if scan_text("gate_e", value):
            _fail("gate_e.unsafe_public_text")
    except ValueError:
        _fail("gate_e.unsafe_public_text")


def _file(root, locator):
    from .hosted_replay import _path, HostedReplayPublicError
    _safe_text(locator)
    relative, _, anchor = locator.partition("#")
    parts = PurePosixPath(relative).parts
    if (not parts or PurePosixPath(relative).is_absolute() or "\\" in relative or
            any(p in (".", "..") or p.startswith(".") and p != ".github" for p in parts) or
            str(PurePosixPath(relative)) != relative or
            not (relative in {"README.md", "AGENTS.md", "LICENSE"} or parts[0] in {
                "docs", "contracts", "tests", "tools", "calico_capture", "calico_publish", "calico_dbt", "dbt", ".github"}) or
            relative in {AUTHORITY_PATH, MARKDOWN_PATH}):
        _fail("gate_e.unsafe_locator")
    try:
        path = _path(Path(root) / relative)
        if not path.is_file() or not path.is_relative_to(Path(root).resolve()):
            _fail("gate_e.unresolved_locator")
        raw = path.read_bytes()
        if anchor:
            from tools.citation_scan.scanner import _anchors
            if anchor not in _anchors(raw.decode("utf-8")):
                _fail("gate_e.unresolved_anchor")
        return raw
    except (OSError, ValueError, UnicodeError, HostedReplayPublicError):
        _fail("gate_e.unresolved_locator")


def _commit(root, sha):
    if type(sha) is not str or not re.fullmatch(_SHA1, sha):
        _fail("gate_e.invalid_commit")
    for command in (["git", "cat-file", "-e", sha + "^{commit}"],
                    ["git", "merge-base", "--is-ancestor", sha, "HEAD"]):
        if subprocess.run(command, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            _fail("gate_e.unresolved_commit")


def _test_id(root, value):
    if type(value) is not str or not re.fullmatch(r"tests(?:\.[A-Za-z_][A-Za-z_0-9]*){3,}", value):
        _fail("gate_e.invalid_test_id")
    parts = value.split(".")
    path = "/".join(parts[:-2]) + ".py"
    try:
        tree = ast.parse(_file(root, path))
        found = any(isinstance(node, ast.ClassDef) and node.name == parts[-2] and
                    any(isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name == parts[-1]
                        for method in node.body) for node in tree.body)
    except (SyntaxError, ValueError):
        _fail("gate_e.unresolved_test_id")
    if not found or not parts[-1].startswith("test_"):
        _fail("gate_e.unresolved_test_id")


def validate_gate_e_document(document, *, root=_PRODUCT):
    schema = _decode((_PRODUCT / SCHEMA_PATH).read_bytes())
    _shape(document, schema)
    recorded = _date(document["recorded_at"])
    for number, row in enumerate(document["conditions"], 1):
        if row["condition"] != number or row["text"] != CONDITION_TEXTS[number - 1]:
            _fail("gate_e.condition_order_or_text")
        evidence = row["evidence"]
        order = [(item["type"], item["locator"]) for item in evidence]
        if order != sorted(set(order)):
            _fail("gate_e.evidence_order")
        for item in evidence:
            _safe_text(item["claim"])
            kind, locator = item["type"], item["locator"]
            if kind in {"file_sha256", "manifest", "owner_attestation"}:
                raw = _file(root, locator)
                if kind in {"file_sha256", "manifest"} and hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    _fail("gate_e.stale_hash")
                if kind == "manifest":
                    manifest = _decode(raw)
                    if type(manifest) is not dict or not manifest:
                        _fail()
            elif kind == "commit":
                _commit(root, locator)
            elif kind == "test_id":
                _test_id(root, locator)
            elif kind == "ci_run":
                if not re.fullmatch(r"https://github.com/mrnouiouat/calico/actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*", locator):
                    _fail("gate_e.invalid_run_locator")
                _commit(root, item["head_sha"])
                if item["evidence_class"] == "actual_schedule_observation" and item["event"] != "schedule":
                    _fail("gate_e.mislabeled_schedule")
                if item["evidence_class"] == "fixture_hosted_replay" and item["event"] != "workflow_dispatch":
                    _fail("gate_e.mislabeled_replay")
            elif kind == "service_observation":
                if not re.fullmatch(r"https://app\.powerbi\.com/(?:view\?r=|groups/)[A-Za-z0-9_/?=&.%-]+", locator):
                    _fail("gate_e.invalid_service_locator")
            if "recorded_at" in item and _date(item["recorded_at"]) > recorded:
                _fail("gate_e.invalid_date")
        amendment = row["amendment"]
        if row["status"] == "pass":
            if amendment is not None:
                _fail("gate_e.unexpected_amendment")
        else:
            if amendment is None:
                _fail("gate_e.missing_amendment")
            if _date(amendment["recorded_at"]) > recorded or hashlib.sha256(_file(root, amendment["locator"])).hexdigest() != amendment["sha256"]:
                _fail("gate_e.invalid_amendment")


def decode_gate_e_document(document, *, root=_PRODUCT):
    if type(document) in (str, bytes):
        document = _decode(document)
    validate_gate_e_document(document, root=root)
    return copy.deepcopy(document)


def render_gate_e_markdown(document, *, root=_PRODUCT):
    document = decode_gate_e_document(document, root=root)
    parts = ["# Gate E portfolio evidence", "", "Generated from the strict ten-condition authority.",
             "CI and Service entries are recorded observations; offline validation does not contact those services.",
             "Recorded: " + document["recorded_at"] + ".", ""]
    for row in document["conditions"]:
        parts.extend([f'## Condition {row["condition"]}', "", row["text"] + ".", "", "Status: `" + row["status"] + "`.", "",
                      "| Evidence type | Openable locator | Claim |", "| --- | --- | --- |"])
        for item in row["evidence"]:
            locator = item["locator"]
            link = ("https://github.com/mrnouiouat/calico/commit/" + locator if item["type"] == "commit" else
                    "https://github.com/mrnouiouat/calico/blob/main/" + "/".join(locator.split(".")[:-2]) + ".py" if item["type"] == "test_id" else
                    locator if locator.startswith("https://") else "../../../" + locator)
            claim = item["claim"]
            for key in ("recorded_at", "conclusion", "head_sha", "sha256", "event", "evidence_class"):
                if key in item:
                    claim += "; " + key + ": " + item[key]
            parts.append(f'| {item["type"]} | [{locator}]({link}) | {claim} |')
        if row["amendment"]:
            amendment = row["amendment"]
            parts.extend(["", f'Public amendment: [{amendment["recorded_at"]}](../../../{amendment["locator"]}); SHA-256: `{amendment["sha256"]}`.'])
        parts.append("")
    return "\n".join(parts)


def _read(path):
    from .hosted_replay import _path, HostedReplayPublicError
    try:
        return _path(path).read_bytes()
    except (OSError, HostedReplayPublicError):
        _fail("gate_e.unreadable")


def _write(path, raw):
    from .hosted_replay import _path, _pair_lock, _write_pair, HostedReplayPublicError
    try:
        path = _path(path)
        with _pair_lock(path, path):
            _write_pair(((path, raw),))
    except (OSError, HostedReplayPublicError):
        _fail("gate_e.write_failed")


def check_gate_e(authority_path, markdown_path=None, *, root=_PRODUCT):
    document = decode_gate_e_document(_read(authority_path), root=root)
    rendered = render_gate_e_markdown(document, root=root).encode()
    if markdown_path is not None and _read(markdown_path) != rendered:
        _fail("gate_e.generated_drift")
    return document


def generate_gate_e(authority_path, output, *, root=_PRODUCT):
    from .hosted_replay import _path
    if _path(authority_path) == _path(output):
        _fail("gate_e.unsafe_path")
    document = check_gate_e(authority_path, root=root)
    _write(output, render_gate_e_markdown(document, root=root).encode())
