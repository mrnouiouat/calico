"""Closed Gate E evidence authority; recorded observations are not live checks."""
from __future__ import annotations

import ast
import copy
from datetime import date, datetime, timezone
import hashlib
import json
import os
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
        raw = _read(path)
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
        if number != 4 and row["status"] != "pass":
            _fail("gate_e.unapproved_condition_deviation")
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
                    locator if locator.startswith("https://") else "../../" + locator)
            claim = item["claim"]
            for key in ("recorded_at", "conclusion", "head_sha", "sha256", "event", "evidence_class"):
                if key in item:
                    claim += "; " + key + ": " + item[key]
            parts.append(f'| {item["type"]} | [{locator}]({link}) | {claim} |')
        if row["amendment"]:
            amendment = row["amendment"]
            parts.extend(["", f'Public amendment: [{amendment["recorded_at"]}](../../{amendment["locator"]}); SHA-256: `{amendment["sha256"]}`.'])
        parts.append("")
    return "\n".join(parts)


def _read(path):
    from .hosted_replay import _path, HostedReplayPublicError
    try:
        path = _path(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(2_000_001)
        if len(raw) > 2_000_000:
            _fail("gate_e.oversize")
        return raw
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


# Safe projection of the immutable historical real observation. Its scope and
# dates never advance when later product commits change HEAD.
HISTORICAL_REAL_OBSERVATION = {
    "evidence_class": "real_restore_republish_observation",
    "run_id": 37726333085, "run_attempt": 1, "event": "workflow_dispatch",
    "head_sha": "ae8a612b0e0be8b47b1a15d366b1a41eb3433677",
    "completed_at": "2026-10-08T04:14:36Z",
    "published_data_commit": "45cd920e3087a3f14d6bb196d15c49abc980f665",
    "published_manifest_sha256": "da4f4a3385f674adc4aea66044ad977a8f8dea86fdc10a7ca5607f918755e7ed",
    "policy_sha256": "91ab3bf914ef26102d48e44d44446fd02a4048440760bc64e9c8b95ce6f3aada",
    "transaction_status": "no_change", "conclusion": "success",
}
_REAL_SEMANTIC_SHA256 = "c8ef0fc48b12ba2d96625ec7ae499c26cedd145f13236961cd9d8171942920da"
_DRAFT_KEYS = ("schema_version", "derived_at", "evidence_classes", "proved_outcomes",
               "residual_gaps", "recommended_status", "amendment_required", "supersedes")
_APPROVAL_KEYS = ("approval_required", "approved_at", "approved_by_role")
_OUTCOMES = ("accepted", "no_new_release", "rejected")
_SUPERSEDES = [{"superseded": "D-04 offline-only outcome fallback and original 10-07 ordering",
                "successor": "D-14 measured hosted replay before residual approval"}]
_OBSERVATION_KEYS = ("evidence_class", "run_id", "run_attempt", "event", "head_sha",
    "created_at", "completed_at", "recorded_at", "conclusion", "outcome", "outcome_basis",
    "log_privacy", "log_finding_count")


def _semantic_bytes(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                          separators=(",", ":")).encode()
    except (TypeError, ValueError, RecursionError):
        _fail()


def _real_observation(document):
    if type(document) in (str, bytes):
        document = _decode(document)
    if type(document) is not dict:
        _fail("gate_e.invalid_real_observation")
    if set(document) == {"seed", "baseline", "mac_restore_build", "hosted_republish"}:
        if hashlib.sha256(_semantic_bytes(document)).hexdigest() != _REAL_SEMANTIC_SHA256:
            _fail("gate_e.changed_historical_real_record")
    elif _semantic_bytes(document) != _semantic_bytes(HISTORICAL_REAL_OBSERVATION):
        _fail("gate_e.changed_historical_real_tuple")
    return copy.deepcopy(HISTORICAL_REAL_OBSERVATION)


def _hosted_observation(document):
    from .hosted_replay import (decode_hosted_replay_envelope, project_hosted_replay_public,
                                decode_hosted_replay_public, HostedReplayPublicError, JSON_PATH)
    try:
        if type(document) in (str, bytes):
            document = _decode(document)
        if type(document) is not dict:
            _fail()
        public = (project_hosted_replay_public(decode_hosted_replay_envelope(document))
                  if document.get("schema_version") == "hosted-replay-envelope-v1"
                  else decode_hosted_replay_public(document))
        measured = decode_hosted_replay_public(_read(_PRODUCT / JSON_PATH))
        if public != measured:
            _fail("gate_e.changed_measured_hosted_record")
        source = measured.to_dict()
        historic = source["historical_real_republish"]
        if any(historic[key] != HISTORICAL_REAL_OBSERVATION[key] for key in
               ("head_sha", "run_id", "published_data_commit", "published_manifest_sha256")):
            _fail("gate_e.changed_historical_real_tuple")
        return {"evidence_class": "fixture_hosted_replay", "run": source["run"],
                "recorded_at": "2026-10-09",
                "public_sha256": hashlib.sha256(measured.encoded).hexdigest(),
                "proved_outcomes": list(_OUTCOMES)}
    except (HostedReplayPublicError, OSError, KeyError, TypeError, ValueError):
        _fail("gate_e.invalid_hosted_observation")


def _timestamp(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        _fail("gate_e.invalid_date")
    try:
        observed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail("gate_e.invalid_date")
    if observed > datetime.now(timezone.utc):
        _fail("gate_e.invalid_date")
    return observed


def _observations(rows, evidence_class):
    if type(rows) is not list or not 1 <= len(rows) <= 100:
        _fail("gate_e.missing_observations")
    identities = []
    result = []
    for row in rows:
        _keys(row, _OBSERVATION_KEYS)
        if row["evidence_class"] != evidence_class or row["event"] != (
                "schedule" if evidence_class == "actual_schedule_observation" else "workflow_dispatch"):
            _fail("gate_e.mislabeled_observation")
        if any(type(row[key]) is not int or row[key] <= 0 for key in ("run_id", "run_attempt")):
            _fail("gate_e.invalid_run_identity")
        _commit(_PRODUCT, row["head_sha"])
        started, completed = _timestamp(row["created_at"]), _timestamp(row["completed_at"])
        if completed < started or _date(row["recorded_at"]) < completed.date():
            _fail("gate_e.invalid_date")
        if type(row["conclusion"]) is not str or row["conclusion"] not in {"success", "failure", "cancelled"}:
            _fail("gate_e.incomplete_observation")
        if type(row["outcome"]) is not str or row["outcome"] not in (*_OUTCOMES, "not_observed") or row["outcome_basis"] != (
                "no_capture_status_observed" if row["outcome"] == "not_observed" else "recorded_capture_status"):
            _fail("gate_e.unproved_outcome")
        if row["log_privacy"] == "no_findings":
            if type(row["log_finding_count"]) is not int or row["log_finding_count"] != 0:
                _fail("gate_e.invalid_privacy_observation")
        elif row["log_privacy"] == "absolute_local_path_findings":
            if type(row["log_finding_count"]) is not int or row["log_finding_count"] <= 0:
                _fail("gate_e.invalid_privacy_observation")
        else:
            _fail("gate_e.invalid_privacy_observation")
        identities.append((row["run_id"], row["run_attempt"]))
        result.append(copy.deepcopy(row))
    if identities != sorted(set(identities)):
        _fail("gate_e.ambiguous_observations")
    return result


def _derive(hosted, real, live, scheduled):
    gaps = []
    for evidence_class, rows in (("live_source_observation", live), ("actual_schedule_observation", scheduled)):
        observed = {row["outcome"] for row in rows}
        gaps.extend({"evidence_class": evidence_class, "outcome": outcome, "reason": "not_observed"}
                    for outcome in _OUTCOMES if outcome not in observed)
        if any(row["log_privacy"] != "no_findings" for row in rows):
            gaps.append({"evidence_class": evidence_class, "outcome": "privacy_boundary",
                         "reason": "historical_log_privacy_not_clean"})
    return {"schema_version": "condition-four-disposition-v1",
            "derived_at": max([hosted["recorded_at"], real["completed_at"][:10],
                               *(row["recorded_at"] for row in (*live, *scheduled))]),
            "evidence_classes": {"fixture_hosted_replay": hosted, "live_source_observation": live,
                "actual_schedule_observation": scheduled, "real_restore_republish_observation": real},
            "proved_outcomes": list(_OUTCOMES), "residual_gaps": gaps,
            "recommended_status": "pass_with_disclosed_deviation" if gaps else "pass",
            "amendment_required": bool(gaps), "supersedes": copy.deepcopy(_SUPERSEDES)}


def derive_condition_four_disposition(*, hosted_replay, real_republish, live_observations, scheduled_observations):
    """Pure derivation from validated measured proof and bounded dated records.

    No network requests, inferred admission from a green run, or owner approval.
    Historical log findings stay visible and are never waived by fixture proof.
    """
    return _derive(_hosted_observation(hosted_replay), _real_observation(real_republish),
        _observations(live_observations, "live_source_observation"),
        _observations(scheduled_observations, "actual_schedule_observation"))


def decode_condition_four_draft(document):
    if type(document) in (str, bytes):
        document = _decode(document)
    _keys(document, _DRAFT_KEYS)
    _date(document["derived_at"])
    classes = document["evidence_classes"]
    _keys(classes, EVIDENCE_CLASSES)
    if tuple(classes) != EVIDENCE_CLASSES:
        _fail("gate_e.evidence_class_order")
    from .hosted_replay import JSON_PATH
    expected_hosted = _hosted_observation(_decode(_read(_PRODUCT / JSON_PATH)))
    if _semantic_bytes(classes["fixture_hosted_replay"]) != _semantic_bytes(expected_hosted):
        _fail("gate_e.changed_measured_hosted_record")
    expected = _derive(expected_hosted, _real_observation(classes["real_restore_republish_observation"]),
        _observations(classes["live_source_observation"], "live_source_observation"),
        _observations(classes["actual_schedule_observation"], "actual_schedule_observation"))
    if _semantic_bytes(document) != _semantic_bytes(expected):
        _fail("gate_e.disposition_not_evidence_derived")
    return expected


def finalize_condition_four_disposition(*, draft, approved_at=None, approved_by_role=None):
    """Add only approval fields; canonical role is the literal repository owner.

    The CLI's repository-owner token maps to this spelling at the CLI boundary.
    This operation records supplied approval; it never solicits or infers it.
    """
    draft = decode_condition_four_draft(draft)
    required = bool(draft["residual_gaps"])
    if required:
        if approved_by_role != "repository owner" or _date(approved_at) < _date(draft["derived_at"]):
            _fail("gate_e.invalid_approval")
    elif approved_at is not None or approved_by_role is not None:
        _fail("gate_e.unexpected_approval")
    return {**draft, "approval_required": required, "approved_at": approved_at, "approved_by_role": approved_by_role}


def decode_condition_four_final(document):
    if type(document) in (str, bytes):
        document = _decode(document)
    _keys(document, (*_DRAFT_KEYS, *_APPROVAL_KEYS))
    expected = finalize_condition_four_disposition(draft={key: document[key] for key in _DRAFT_KEYS},
        approved_at=document["approved_at"], approved_by_role=document["approved_by_role"])
    if _semantic_bytes(document) != _semantic_bytes(expected):
        _fail("gate_e.changed_final_disposition")
    return expected


def validate_final_condition_four_disposition(*, draft, final, hosted_replay, real_republish):
    draft = decode_condition_four_draft(draft)
    final = decode_condition_four_final(final)
    expected = derive_condition_four_disposition(hosted_replay=hosted_replay, real_republish=real_republish,
        live_observations=draft["evidence_classes"]["live_source_observation"],
        scheduled_observations=draft["evidence_classes"]["actual_schedule_observation"])
    if (_semantic_bytes(draft) != _semantic_bytes(expected) or
            _semantic_bytes({key: final[key] for key in _DRAFT_KEYS}) != _semantic_bytes(draft)):
        _fail("gate_e.draft_final_mismatch")


def render_condition_four_public_decision(*, draft, final, hosted_replay, real_republish):
    validate_final_condition_four_disposition(draft=draft, final=final,
        hosted_replay=hosted_replay, real_republish=real_republish)
    final = decode_condition_four_final(final)
    classes = final["evidence_classes"]
    parts = ["# Condition 4 evidence disposition", "", "Status: `" + final["recommended_status"] + "`.",
        "Derived from recorded observations dated " + final["derived_at"] + ".",
        "Fixture acceptance is not live-source acceptance. Dispatched calendar cases are not actual scheduled observations.",
        "The historical real restore/republish proves its recorded mechanism; it is not current-head accepted-trigger proof.", "",
        "Hosted fixture outcomes: accepted, no_new_release, rejected.", ""]
    if final["approved_at"]:
        parts.extend(["Public amendment recorded " + final["approved_at"] + ".", ""])
    parts.extend(["| Evidence class | Outcome | Residual reason |", "| --- | --- | --- |"])
    parts.extend(f'| {row["evidence_class"]} | {row["outcome"]} | {row["reason"]} |' for row in final["residual_gaps"])
    if not final["residual_gaps"]:
        parts.append("No residual gaps; no amendment required.")
    parts.extend(["", "## Recorded evidence", ""])
    for evidence_class in EVIDENCE_CLASSES:
        source = classes[evidence_class]
        records = source if type(source) is list else [source]
        for record in records:
            run = record["run"] if evidence_class == "fixture_hosted_replay" else record
            url = f'https://github.com/mrnouiouat/calico/actions/runs/{run["run_id"]}/attempts/{run["run_attempt"]}'
            when = record.get("recorded_at", record.get("completed_at", ""))
            detail = ("hosted fixture outcomes" if evidence_class == "fixture_hosted_replay" else
                      record.get("outcome", "historical real no_change"))
            parts.append(f'- {evidence_class}: [recorded attempt]({url}), head `{run["head_sha"]}`, {when}, {detail}.')
            if record.get("log_finding_count", 0):
                parts.append(f'  Historical log privacy: {record["log_privacy"]}, {record["log_finding_count"]} locations; no clean claim or waiver.')
            if evidence_class == "fixture_hosted_replay":
                parts.append(f'  Validated public evidence SHA-256: `{record["public_sha256"]}`; actual event `{run["event"]}`; conclusion `{run["conclusion"]}`.')
            elif evidence_class == "real_restore_republish_observation":
                parts.append(f'  Historical published commit `{record["published_data_commit"]}`; manifest SHA-256 `{record["published_manifest_sha256"]}`; policy SHA-256 `{record["policy_sha256"]}`; transaction `{record["transaction_status"]}`.')
            else:
                parts.append(f'  Actual event `{record["event"]}`; conclusion `{record["conclusion"]}`; outcome basis `{record["outcome_basis"]}`; created {record["created_at"]}; completed {record["completed_at"]}.')
    parts.extend(["", "## Additive successor", ""])
    parts.extend(f'{pair["superseded"]} → {pair["successor"]}.' for pair in final["supersedes"])
    parts.append("")
    return "\n".join(parts)
