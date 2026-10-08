"""Closed public projection of validated hosted fixture evidence.

The private envelope and driver are input authorities, never public variants.
No raw job payload, source row, policy, local destination or owner locator is
projected. Run/job observations and byte commitments retain their provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from tools.hosted_replay import HostedEnvelope, ReplayError, validate_envelope

SCHEMA_VERSION = "hosted-replay-public-v1"
JSON_PATH = "docs/evidence/gate-e/hosted-replay-v1.json"
MARKDOWN_PATH = "docs/provenance/HOSTED-REPLAY-EVIDENCE.md"
_PRODUCT = Path(__file__).resolve().parents[2]
_MAX_BYTES = 65536
_SCENARIOS = ("accepted", "repeat", "rejected")
_ROUTES = (*_SCENARIOS, "calendar-refused", "failure", "cancelled")
_JOBS = tuple(sorted([*("prepare-" + name for name in _SCENARIOS),
    *("route-" + name for name in _ROUTES), *("publish-" + name for name in _ROUTES),
    "calendar-matrix", "audit-safe-evidence"]))
_CHECKPOINTS = ("prepare-accepted", "prepare-repeat", "prepare-rejected", "publish-accepted")
_FIELDS = ("evidence_class", "scenario", "input_digest", "provenance_digest", "reconstructed",
    "dbt_model_count", "dbt_test_count", "baseline_analytical_sha256", "analytical_sha256",
    "exports", "outcomes", "byte_audits")


class HostedReplayPublicError(Exception):
    """Diagnostics contain fixed categories only, never input or paths."""
    def __init__(self, category="hosted_replay.invalid_public"):
        self.category = category
        super().__init__(category)


def _fail(category="hosted_replay.invalid_public"):
    raise HostedReplayPublicError(category)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _decode(raw):
    if type(raw) not in {bytes, str} or len(raw) > _MAX_BYTES:
        _fail()
    try:
        return json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: _fail())
    except (ValueError, UnicodeError, TypeError, RecursionError):
        _fail()


def _encode(document):
    return (json.dumps(document, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def _schema():
    return _decode((_PRODUCT / "contracts/hosted-replay-public-v1.schema.json").read_bytes())


def _validate(value, schema):
    """Finite independent schema decoder, including canonical object order."""
    supported = {"$schema", "title", "description", "type", "additionalProperties", "required",
        "properties", "const", "enum", "pattern", "minimum", "maximum", "items", "minItems", "maxItems"}
    if set(schema) - supported:
        _fail()
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if kind and (kind not in types or type(value) is not types[kind]):
        _fail()
    if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
        _fail()
    if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
        _fail()
    if kind == "object":
        if list(value) != schema["required"] or schema["additionalProperties"] is not False:
            _fail()
        for key in schema["required"]:
            _validate(value[key], schema["properties"][key])
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 1000):
            _fail()
        for item in value:
            _validate(item, schema["items"])
    elif kind == "integer":
        if not schema.get("minimum", 0) <= value <= schema.get("maximum", 10**18):
            _fail()
    elif kind == "string" and (len(value) > 4096 or re.fullmatch(schema.get("pattern", ".*"), value) is None):
        _fail()


@dataclass(frozen=True)
class HostedReplayPublic:
    encoded: bytes

    def __post_init__(self):
        document = _decode(self.encoded)
        _validate(document, _schema())
        _semantics(document)
        if type(self.encoded) is not bytes or self.encoded != _encode(document):
            _fail()

    def to_dict(self):
        return _decode(self.encoded)

    def to_json(self):
        return self.encoded.decode("utf-8")


def decode_hosted_replay_envelope(document: object) -> HostedEnvelope:
    """Validate the complete private authority before selecting public fields."""
    try:
        if isinstance(document, HostedEnvelope):
            document = document.to_dict()
        validated = validate_envelope(document)
        # Snapshot the authority; output never aliases caller-owned containers.
        snapshot = json.loads(json.dumps(validated, allow_nan=False))
        return HostedEnvelope(validate_envelope(snapshot))
    except (ReplayError, ValueError, TypeError, KeyError, OSError, RecursionError):
        _fail("hosted_replay.invalid_envelope")


def _semantics(document):
    run = document["run"]
    prefix = "https://github.com/" + run["repository"]
    if (run["run_url"] != f'{prefix}/actions/runs/{run["run_id"]}/attempts/{run["run_attempt"]}'
            or run["commit_url"] != prefix + "/commit/" + run["head_sha"]):
        _fail("hosted_replay.invalid_locator")
    jobs = document["jobs"]
    if [row["job_name"] for row in jobs] != list(_JOBS) or len({row["job_id"] for row in jobs}) != len(jobs):
        _fail()
    for row in jobs:
        expected = "skipped" if row["job_name"].startswith("publish-") and row["job_name"] != "publish-accepted" else "success"
        if row["conclusion"] != expected:
            _fail()
    if [row["scenario"] for row in document["worker_matrix"]] != list(_ROUTES):
        _fail()
    for row in document["worker_matrix"]:
        accepted = row["scenario"] == "accepted"
        if row["should_publish"] is not accepted or row["worker_conclusion"] != ("success" if accepted else "skipped"):
            _fail()
    checkpoints = document["checkpoints"]
    if [row["job_name"] for row in checkpoints] != list(_CHECKPOINTS):
        _fail()
    from calico_publish.allowlist import load_allowlist
    names = sorted(entry.export_name for entry in load_allowlist(_PRODUCT / "contracts/publication-exports-v3.json").exports)
    for row in checkpoints:
        worker = row["job_name"] == "publish-accepted"
        scenario = row["job_name"].split("-")[-1]
        if (row["scenario"] != scenario or row["reconstructed"] is not worker or
                row["evidence_class"] != ("fresh_job_reconstruction" if worker else "staged_prepare")):
            _fail()
        if [item["export_name"] for item in row["exports"]] != names:
            _fail()
        if any(item["row_count"] <= 0 for item in row["exports"] if item["export_name"] in {
                "dim_public_organizations", "fct_public_status_observations"}):
            _fail()
        audits = row["byte_audits"]
        if [item["surface"] for item in audits] != ["publication", "status", "stderr", "stdout", "summary"]:
            _fail()
        for item in audits:
            if item["surface"] in {"publication", "status", "summary"} and item["byte_length"] <= 0:
                _fail()
            if item["byte_length"] == 0 and item["sha256"] != hashlib.sha256(b"").hexdigest():
                _fail()
        outcome = row["outcomes"][0]
        expected = {"accepted": ("accepted", "none", 1), "repeat": ("no_new_release", "source_not_advanced", 0),
                    "rejected": ("rejected", "source_contract_mismatch", 0)}[scenario]
        if ((outcome["outcome"], outcome["reason_category"], outcome["build_calls"]) != expected or
                outcome["publication_calls"] != int(worker) or outcome["scenario"] != scenario or
                outcome["analytical_sha256"] != row["analytical_sha256"]):
            _fail()
        status_audit = next(item for item in audits if item["surface"] == "status")
        if status_audit["sha256"] != outcome["status_sha256"]:
            _fail()
        if scenario != "accepted" and (outcome["analytical_sha256"] != outcome["prior_analytical_sha256"] or
                row["analytical_sha256"] != row["baseline_analytical_sha256"]):
            _fail()
    for key in ("input_digest", "provenance_digest", "exports", "analytical_sha256"):
        if checkpoints[0][key] != checkpoints[-1][key]:
            _fail("hosted_replay.reconstruction_mismatch")
    executed = [name for name in _JOBS if not name.startswith("publish-") or name == "publish-accepted"]
    if [row["job_name"] for row in document["log_audit_commitments"]] != executed:
        _fail()
    if any(row["audit"]["surface"] != "raw_log" or row["audit"]["byte_length"] <= 0
           for row in document["log_audit_commitments"]):
        _fail()


def decode_hosted_replay_public(document: object) -> HostedReplayPublic:
    try:
        if isinstance(document, HostedReplayPublic):
            document = document.encoded
        if isinstance(document, (bytes, str)):
            document = _decode(document)
        _validate(document, _schema())
        _semantics(document)
        encoded = _encode(document)
        if len(encoded) > _MAX_BYTES:
            _fail()
        return HostedReplayPublic(encoded)
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        _fail()


def project_hosted_replay_public(envelope: HostedEnvelope) -> HostedReplayPublic:
    source = decode_hosted_replay_envelope(envelope).to_dict()
    identity = source["run_tuple"]
    prefix = "https://github.com/" + identity["repository"]
    run = {**identity, "event": source["event"], "conclusion": source["conclusion"],
           "run_url": f'{prefix}/actions/runs/{identity["run_id"]}/attempts/{identity["run_attempt"]}',
           "commit_url": prefix + "/commit/" + identity["head_sha"]}
    # Envelope decoding permits any input object order. Public order is a
    # distinct contract, so every nested selection follows the public schema.
    def ordered(value, schema):
        if schema.get("type") == "object":
            return {key: ordered(value[key], schema["properties"][key]) for key in schema["required"]}
        if schema.get("type") == "array":
            return [ordered(item, schema["items"]) for item in value]
        return value
    document = {"schema_version": SCHEMA_VERSION, "evidence_class": "fixture_hosted_replay", "input_profile": "fixture",
        "run": run, "jobs": source["jobs"], "worker_matrix": source["worker_matrix"],
        "checkpoints": [{"job_name": row["job_name"], **{key: row["driver"][key] for key in _FIELDS}}
                        for row in source["checkpoints"]],
        "log_audit_commitments": source["raw_log_audits"], "live_boundary": source["live_before"],
        "live_boundary_unchanged": source["live_before"] == source["live_after"],
        "artifact_count": source["artifact_count"], "cleanup_verified": source["cleanup_verified"],
        "result": source["result"], "historical_real_republish": {
            **source["historical_real_republish"], "evidence_class": "real_restore_republish_observation"},
        "evidence_classes": {"fixture_hosted_replay": source["evidence_classes"]["hosted_fixture_replay"],
            "live_source_observation": source["evidence_classes"]["live_source"],
            "actual_schedule_observation": source["evidence_classes"]["actual_schedule"],
            "real_restore_republish_observation": source["evidence_classes"]["historical_real_restore_republish"]}}
    return decode_hosted_replay_public(ordered(document, _schema()))


def render_hosted_replay_markdown(document: object) -> str:
    public = decode_hosted_replay_public(document).to_dict()
    run = public["run"]
    historical = public["historical_real_republish"]
    prefix = "https://github.com/" + run["repository"]
    parts = ["# Hosted fixture replay evidence", "",
        "Generated from the closed public replay projection. Audit entries commit to actual scanned byte lengths and SHA-256 values.", "",
        f'Run: [GitHub attempt {run["run_attempt"]}]({run["run_url"]}); head: [deployment commit]({run["commit_url"]}).',
        f'Event: `{run["event"]}`; conclusion: `{run["conclusion"]}`; input profile: `fixture`.', "",
        "Checkpoint state was reconstructed across fresh jobs; matching input and provenance digests bind preparation to publication. "
        "Capture status timestamps are separate from analytical outputs. Controlled calendar/failure/cancel cases are host-evaluated negatives.", "",
        "Fixture acceptance is not live-source acceptance. Dispatched calendar inputs are not actual cron execution. "
        "Dated live-source and dated actual-schedule observations require their own evidence; neither is observed by this replay.", "",
        "| Evidence class | Observation |", "| --- | --- |"]
    parts.extend(f"| {key} | {value} |" for key, value in public["evidence_classes"].items())
    parts.extend(["", "| Job | Jobs API ID | Conclusion |", "| --- | --- | --- |"])
    parts.extend(f'| {row["job_name"]} | {row["job_id"]} | {row["conclusion"]} |' for row in public["jobs"])
    parts.extend(["", "| Route scenario | Should publish | Route | Worker |", "| --- | --- | --- | --- |"])
    parts.extend(f'| {row["scenario"]} | {str(row["should_publish"]).lower()} | {row["route_conclusion"]} | {row["worker_conclusion"]} |'
                 for row in public["worker_matrix"])
    for row in public["checkpoints"]:
        parts.extend(["", "## " + row["job_name"], "", f'Evidence: `{row["evidence_class"]}`; reconstructed: `{str(row["reconstructed"]).lower()}`.',
            f'Actual SQL: {row["dbt_model_count"]} models / {row["dbt_test_count"]} tests.',
            f'Input digest: `{row["input_digest"]}`; provenance digest: `{row["provenance_digest"]}`.',
            f'Analytical digest: `{row["analytical_sha256"]}`; baseline: `{row["baseline_analytical_sha256"]}`.', "",
            "| Outcome | Reason | Builds | Publications | Status digest |", "| --- | --- | --- | --- | --- |"])
        parts.extend(f'| {o["outcome"]} | {o["reason_category"]} | {o["build_calls"]} | {o["publication_calls"]} | {o["status_sha256"]} |'
                     for o in row["outcomes"])
        parts.extend(["", "| Export | Rows | SHA-256 |", "| --- | --- | --- |"])
        parts.extend(f'| {item["export_name"]} | {item["row_count"]} | {item["sha256"]} |' for item in row["exports"])
        parts.extend(["", "| Audited surface | Bytes | SHA-256 | Findings |", "| --- | --- | --- | --- |"])
        parts.extend(f'| {a["surface"]} | {a["byte_length"]} | {a["sha256"]} | {a["canary_hits"]} |' for a in row["byte_audits"])
    parts.extend(["", "## Log audit commitments", "", "| Executed job | Bytes | SHA-256 | Findings |", "| --- | --- | --- | --- |"])
    parts.extend(f'| {row["job_name"]} | {row["audit"]["byte_length"]} | {row["audit"]["sha256"]} | {row["audit"]["canary_hits"]} |'
                 for row in public["log_audit_commitments"])
    parts.extend(["", "## Preserved boundaries", "", "Live boundary equality: `true`; artifacts: `0`; owned-root cleanup: `true`; result: `pass`."])
    parts.extend(f'{key}: `{value}`.' for key, value in public["live_boundary"].items())
    parts.extend(["", "## Separate historical real restore/republish", "",
        "This immutable historical real proof is independent of the fixture-hosted replay above.",
        f'Run: [historical observation]({prefix}/actions/runs/{historical["run_id"]}); head: [historical commit]({prefix}/commit/{historical["head_sha"]}).',
        f'Published-data commit: `{historical["published_data_commit"]}`; manifest SHA-256: `{historical["published_manifest_sha256"]}`.', ""])
    return "\n".join(parts)
