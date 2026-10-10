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
                "docs", "contracts", "tests", "tools", "powerbi", "calico_capture", "calico_publish", "calico_dbt", "dbt", ".github"}) or
            relative in {AUTHORITY_PATH, MARKDOWN_PATH, SPIKE_AUTHORITY_PATH, SPIKE_MARKDOWN_PATH}):
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
    from .hosted_replay import _pair_lock, HostedReplayPublicError
    if Path(authority_path).resolve() != (Path(root) / AUTHORITY_PATH).resolve():
        return _check_gate_e_unlocked(authority_path, markdown_path, root=root)
    try:
        with _pair_lock(Path(root) / "gate-e-json", Path(root) / "gate-e-markdown"):
            return _check_gate_e_unlocked(authority_path, markdown_path, root=root)
    except HostedReplayPublicError:
        _fail("gate_e.lock_busy")


def _check_gate_e_unlocked(authority_path, markdown_path=None, *, root=_PRODUCT):
    raw = _read(authority_path)
    document = decode_gate_e_document(raw, root=root)
    if Path(authority_path).resolve() == (Path(root) / AUTHORITY_PATH).resolve():
        validate_portfolio_authority(document, root=root)
        if raw != _encode(document):
            _fail("gate_e.generated_drift")
        if (Path(root) / SPIKE_AUTHORITY_PATH).exists():
            _check_spike_audit_unlocked(Path(root) / SPIKE_AUTHORITY_PATH,
                                       Path(root) / SPIKE_MARKDOWN_PATH, root=root)
    rendered = render_gate_e_markdown(document, root=root).encode()
    if markdown_path is not None and _read(markdown_path) != rendered:
        _fail("gate_e.generated_drift")
    return document


def generate_gate_e(authority_path, output, *, root=_PRODUCT):
    from .hosted_replay import _path
    if _path(authority_path) == _path(output):
        _fail("gate_e.unsafe_path")
    document = check_gate_e(authority_path, root=root)
    if Path(authority_path).resolve() == (Path(root) / AUTHORITY_PATH).resolve():
        generate_gate_e_pair(document, authority_path, output, root=root)
        if (Path(root) / SPIKE_AUTHORITY_PATH).exists():
            generate_spike_audit_pair(_decode(_read(Path(root) / SPIKE_AUTHORITY_PATH)),
                                     Path(root) / SPIKE_AUTHORITY_PATH,
                                     Path(root) / SPIKE_MARKDOWN_PATH, root=root)
    else:
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

SPIKE_AUTHORITY_PATH = "docs/evidence/gate-e/spike-era-requirements-v1.json"
SPIKE_MARKDOWN_PATH = "docs/provenance/spike-era-requirements-audit.md"
SPIKE_REQUIREMENT_TEXTS = (
    "automated first/third-Wednesday capture",
    "four-file atomic admission",
    "accepted/no_new_release/rejected run recording",
    "visible release-integrity and bulk-movement flags",
    "scheduled automation is not the sole preservation mechanism",
    "historical release identity includes source URL, archive timestamp, as-of date, list, revision, byte count, row count, and SHA-256",
    "full nonblank State Charity Reg# is the longitudinal key with keyless rows visible as coverage and EIN never a fallback identity",
    "snapshots immutable, transitions and interval-censored spells derived",
    "disappearance is not cure",
    "duration metrics retain left, right, and interval censoring and never annualize a raw gap proportion",
    "the exclusion list (stakeholder interviews, internal organization-level tooling, investigation queues, email notifications, predictions, causal explanations, per-organization recommendations) holds",
    "published artifacts are aggregate-only and contain no organization identity fields",
    "one BI implementation ships: Power BI if Publish to web works, Evidence otherwise",
    "the archive-census precondition",
    "seeking one external test of whether the finished monitor catches a real release problem — founder action #5",
)
_GENERATED_AUTHORITIES = frozenset({AUTHORITY_PATH, MARKDOWN_PATH,
                                    SPIKE_AUTHORITY_PATH, SPIKE_MARKDOWN_PATH})
_HOSTED_PATH = "docs/evidence/gate-e/hosted-replay-v1.json"
_AMENDMENT_PATH = "docs/decisions/condition-4-hosted-outcomes.md"
_IMMUTABLE_HASHES = {
    _HOSTED_PATH: "7a5abc056144409c7efd29eafcff6dffa7babf7d05169f0487e788bb824136f6",
    _AMENDMENT_PATH: "444ade2ad968bb570d66ec5ac775fa96666212758ca2879dc52e7cc893dda635",
}


def _bound_file(root, locator, claim, *, manifest=False):
    raw = _file(root, locator)
    digest = hashlib.sha256(raw).hexdigest()
    relative = locator.partition("#")[0]
    if relative in _IMMUTABLE_HASHES and digest != _IMMUTABLE_HASHES[relative]:
        _fail("gate_e.changed_prior_authority")
    return {"type": "manifest" if manifest else "file_sha256", "locator": locator,
            "sha256": digest, "claim": claim}


def _bound_test(root, locator, claim):
    _test_id(root, locator)
    return {"type": "test_id", "locator": locator, "claim": claim}


def _ci(run, *, recorded_at, claim, evidence_class):
    return {"type": "ci_run", "locator": run["run_url"], "claim": claim,
            "recorded_at": recorded_at, "conclusion": run["conclusion"],
            "head_sha": run["head_sha"], "event": run["event"],
            "evidence_class": evidence_class}


def _ordered(items):
    return sorted(items, key=lambda e: (e["type"], e["locator"]))


def build_portfolio_document(*, root=_PRODUCT):
    """Bind the ten real requirements to earlier evidence, never to this output.

    This is an offline projection. Hosted conclusions remain at their recorded
    SHAs; the manual Service observation does not become a new lookup or embed.
    """
    root = Path(root).resolve()
    hosted = _decode(_file(root, _HOSTED_PATH))
    _hosted_observation(hosted)
    h = _bound_file(root, _HOSTED_PATH,
        "Actual Jobs API conclusions, reconstruction digest equality, fixture SQL and bytes-derived privacy audits; isolated atomic publication", manifest=True)
    amendment = _bound_file(root, _AMENDMENT_PATH,
        "Approved 2026-10-10 six-residual amendment; historical log limits are disclosed without a privacy waiver")
    fixture_ci = _ci(hosted["run"], recorded_at="2026-10-09",
        claim="Recorded fixture-hosted replay: 32 dbt models and 228 tests passed on its historical head",
        evidence_class="fixture_hosted_replay")
    jobs = {j["job_name"]: j for j in hosted["jobs"]}
    if jobs["publish-accepted"]["conclusion"] != "success" or any(
        jobs["publish-" + name]["conclusion"] != "skipped"
        for name in ("repeat", "rejected", "calendar-refused", "failure", "cancelled")):
        _fail("gate_e.missing_actual_worker_proof")
    real = HISTORICAL_REAL_OBSERVATION
    real_ci = _ci(dict(real, run_url=f'https://github.com/mrnouiouat/calico/actions/runs/{real["run_id"]}/attempts/1'),
        recorded_at="2026-10-08", evidence_class="real_restore_republish_observation",
        claim="Immutable real B2 restore/build/publication mechanism; exact no_change; published manifest " + real["published_manifest_sha256"] + "; policy " + real["policy_sha256"] + "; published commit " + real["published_data_commit"])
    f = lambda path, claim: _bound_file(root, path, claim)
    t = lambda path, claim: _bound_test(root, path, claim)
    rows = [
        [fixture_ci, h, f(".github/workflows/dbt-fixture.yml", "The normal CI workflow runs the public fixture dbt build and its tests")],
        [_bound_file(root, "docs/evidence/gate-b/real-build-proof-v3.json", "Recorded local real-mode SQL models/tests, exact source binding and zero reconciliation mismatches", manifest=True), real_ci,
         t("tests.dbt_metrics.test_reconciliation.RealProofProvenanceTests.test_verify_proof_rejects_fixture_mode_when_real_required", "Real evidence cannot substitute fixture mode")],
        [h, t("tests.landing.test_admission.RejectionMatrixTests.test_missing_logical_file_rejected_as_invalid_mapping_pointer_unchanged", "Incomplete four-file admission preserves the prior release"),
         t("tests.landing.test_admission.RejectionMatrixTests.test_wrong_arity_rejected_with_no_raw_row_in_output", "Structural failures reject without printing raw rows")],
        [fixture_ci, h, amendment],
        [h, real_ci, f("docs/provenance/HOSTED-REPLAY-EVIDENCE.md", "Accepted publisher succeeded; repeat/rejected/calendar/failure/cancel publishers skipped; isolated transaction and exact unchanged negative boundaries")],
        [f("docs/powerbi-refresh-runbook.md", "Stable published Web sources; existing manual Refresh now fallback; scheduled refresh reliability is not proved"),
         {"type": "owner_attestation", "locator": "docs/powerbi-refresh-runbook.md#power-bi-refresh-and-owner-acceptance-runbook",
          "claim": "Recorded owner Service observation 2026-09-22: native Refresh now completed; accepted 2026-08-19 revision 1; latest attempt rejected; source retired. No new Service lookup or public embed",
          "recorded_at": "2026-09-22", "conclusion": "observed"}],
        [f("contracts/publication-exports-v3.json", "Approved aggregate and bounded named-history fields"),
         f("powerbi/semantic-model-inventory-v1.json", "Complete visible, hidden, calculated, measure and relationship model surface"),
         t("tests.publish.test_inventory.InventoryTests.test_v3_control_source_and_each_cross_class_relationship", "Semantic-model source and relationships remain within the v3 boundary"),
         t("tests.publish.test_gate.PublicationGateFixtureTests.test_01_committed_baseline_loads_and_passes", "Committed fixture publication passes the field gate")],
        [f("README.md", "Question, grains, SQL lineage, limitations, refresh procedure and findings"),
         t("tests.docs_public.test_readme.ReadmeContracts.test_complete_readme_has_all_decided_topics_and_separate_refresh", "README content and refresh dates are independently enforced")],
        [f("docs/walkthrough.md", "Concise written finding, SQL transformation, source defect and deliberate non-claim"),
         t("tests.docs_public.test_walkthrough.WrittenWalkthroughContracts.test_four_topics_exist_without_recording_or_hosting_prerequisites", "Written walkthrough has all four decided topics")],
        [f("AGENTS.md", "Python landing/admission; DuckDB/dbt analytical calculations; Power BI presentation"),
         f("docs/evidence/dbt-lineage-v1.json", "SQL model lineage"), f("docs/evidence/sql-excerpts-v1.json", "Representative SQL excerpts bound to source hashes"),
         t("tests.docs_public.test_walkthrough.WrittenWalkthroughContracts.test_sql_excerpt_is_exact_and_bound_to_source_hashes", "Walkthrough SQL remains byte-exact and bound to model sources")],
    ]
    proof = _decode(_file(root, "docs/evidence/gate-b/real-build-proof-v3.json"))
    if (proof.get("mode") != "real" or proof.get("status") != "success" or
            proof.get("verified_input_binding") is not True or
            proof.get("reconciliation", {}).get("status") != "reconciled" or
            type(proof.get("reconciliation", {}).get("mismatch_row_count")) is not int or
            proof["reconciliation"]["mismatch_row_count"] != 0 or
            any(type(proof.get(key)) is not int or proof[key] <= 0 for key in
                ("dbt_model_count", "dbt_test_count", "verified_object_count", "verified_release_count"))):
        _fail("gate_e.invalid_real_build_proof")
    for name in ("test_truncated_payload_rejected_with_deterministic_transfer_code",
                 "test_wrong_header_rejected_with_safe_logical_location_only",
                 "test_blank_date_rejected_with_ordered_date_reason",
                 "test_mismatched_date_rejected_with_ordered_date_reason",
                 "test_duplicate_key_within_list_rejected_with_duplicate_category",
                 "test_duplicate_key_across_lists_rejected_with_duplicate_category",
                 "test_unknown_registration_family_rejected_blank_keys_stay_accepted",
                 "test_invalid_same_date_revision_rejected_preserves_prior_promotion"):
        rows[2].append(t("tests.landing.test_admission.RejectionMatrixTests." + name,
                         "Known structural rejection preserves the prior admitted release"))
    # Recorded live and schedule observations are distinct from dispatched replay.
    for run_id, event, sha, claim in (
        (36812428040, "workflow_dispatch", "9297d1db4e381f3da7ae03ba7e9ed57812d12a9b", "Live source rejected; 66 historical ordinary log absolute_local_path locations; no clean-log claim"),
        (36779395262, "schedule", "9297d1db4e381f3da7ae03ba7e9ed57812d12a9b", "Actual schedule outside capture window; 22 historical ordinary log absolute_local_path locations; no accepted claim"),
        (37693376162, "schedule", real["head_sha"], "Actual schedule rejected retired source; 66 historical ordinary log absolute_local_path locations; no clean-log claim"),
    ):
        rows[3].append(_ci({"run_url": f"https://github.com/mrnouiouat/calico/actions/runs/{run_id}/attempts/1",
            "conclusion": "success", "head_sha": sha, "event": event}, recorded_at="2026-10-10", claim=claim,
            evidence_class="actual_schedule_observation" if event == "schedule" else "live_source_observation"))
    document = {"schema_version": "portfolio-ready-v1", "recorded_at": "2026-10-10", "conditions": []}
    for number, evidence in enumerate(rows, 1):
        document["conditions"].append({"condition": number, "text": CONDITION_TEXTS[number - 1],
            "status": "pass_with_disclosed_deviation" if number == 4 else "pass", "evidence": _ordered(evidence),
            "amendment": {"locator": _AMENDMENT_PATH, "sha256": amendment["sha256"], "recorded_at": "2026-10-10"} if number == 4 else None})
    return document


def validate_portfolio_authority(document, *, root=_PRODUCT):
    document = decode_gate_e_document(document, root=root)
    for row in document["conditions"]:
        for item in row["evidence"]:
            if item["locator"].partition("#")[0] in _GENERATED_AUTHORITIES:
                _fail("gate_e.self_or_future_reference")
    if document != build_portfolio_document(root=root):
        _fail("gate_e.condition_evidence_mismatch")
    return copy.deepcopy(document)


def build_spike_audit_document(*, root=_PRODUCT):
    """The 11 current clauses, three superseded clauses and sole deferred action."""
    portfolio = build_portfolio_document(root=root)
    f = lambda path, claim: _bound_file(root, path, claim)
    t = lambda path, claim: _bound_test(root, path, claim)
    calendar = [f("calico_capture/calendar.py", "Exact UTC first/third-Wednesday authority: allow days 1/7/15/21; refuse days 8/22"),
                t("tests.capture.test_schedule_contract.CalendarBoundaryDateTests.test_exact_wednesday_boundaries", "All six calendar boundaries are independently tested"),
                f(".github/workflows/capture-current.yml", "Wednesday schedule plus shared calendar gate and manual capture fallback"),
                *portfolio["conditions"][3]["evidence"]]
    evidence = [calendar, portfolio["conditions"][2]["evidence"],
        [f("contracts/capture-status-v3.schema.json", "Closed accepted/no_new_release/rejected run recording"), *portfolio["conditions"][3]["evidence"]],
        [f("docs/walkthrough.md", "Visible release-integrity and bulk movement descriptions; no inferred cause"),
         f("dbt/models/intermediate/int_release_flags.sql", "Governed SQL derives integrity and visible bulk-movement flags"),
         f("dbt/models/marts/mart_release_quality.sql", "Published release-quality mart exposes the governed flag results")],
        [f("docs/capture-runbook.md", "Manual preservation fallback remains mandatory after scheduled cutover"),
         t("tests.capture.test_archive.SynchronizeTransactionTests.test_byte_identical_replay_is_an_idempotent_no_op", "Immutable private preservation has an independent idempotent mechanism")],
        [f("docs/evidence/gate-b/real-input-catalog-v1.json", "Three accepted date/revision identities bind their private revision manifests through exact hashes"),
         f("docs/provenance/spikes/001-archive-sample-validation/archive-sample-manifest.json.md", "Recorded historical original URLs, archive timestamps, source dates/lists, byte counts and hashes; predecessor row counts carry visible additive corrections"),
         f("docs/evidence/gate-a/spike-001-successor-v1.json", "Corrected historical parsed counts supersede retained predecessor counts"),
         t("tests.landing.test_admission.AcceptedRevisionTests.test_baseline_candidate_admits_revision_one_with_full_provenance", "Admission persists exact date/revision, four source hashes, bytes and reconciled row counts")],
        [t("tests.dbt_longitudinal.test_transitions.KeyedSnapshotSqlShapeTests.test_keyed_snapshots_uses_exact_eligible_predicate", "Full registration keys; no excluded identifier fallback"),
         t("tests.dbt_longitudinal.test_transitions.KeyedSnapshotSqlShapeTests.test_unkeyed_coverage_groups_the_row_level_relation", "Keyless records remain visible coverage")],
        [t("tests.dbt_longitudinal.test_spells.DelinquencySpellsSqlShapeTests.test_delinquency_spells_never_coalesces_a_missing_bound", "Derived interval-censored spells retain absent bounds"),
         t("tests.capture.test_archive.SynchronizeTransactionTests.test_different_bytes_at_existing_key_is_a_deterministic_collision", "Immutable source snapshots cannot overwrite an existing key")],
        [f("docs/walkthrough.md", "Disappearance is not cure"),
         t("tests.dbt_longitudinal.test_transitions.EntityTransitionsSqlShapeTests.test_entity_transitions_never_coalesces_missing_status", "Missing status stays distinct from observed exit")],
        [f("contracts/metric-denominators-v1.json", "Date-pair denominators and non-annualized gap proportions"),
         t("tests.dbt_longitudinal.test_spells.DelinquencySpellsSqlShapeTests.test_delinquency_spells_never_coalesces_a_missing_bound", "Left/right/interval censoring remains explicit")],
        [f("docs/provenance/spikes/005-project-recommendation/README.md#usefulness-boundary", "Exact still-in-force exclusion list; other superseded predecessor recommendations do not govern this row"),
         f("README.md#limitations", "Current outside-in non-claims retain no internal characterization, cause, scores, rankings or partner recommendation")],
        [f("docs/decisions/register.md#d-007", "D-007 intentionally permits bounded approved named organization history")],
        [f("docs/decisions/register.md#d-008", "D-008 settles one Power BI implementation")],
        [f("docs/decisions/register.md#d-012", "D-012 excludes broader archive census from v1")],
        [f("README.md#limitations", "Founder action #5 external test remains deferred beyond v1")],
    ]
    rows = []
    for index, (text, items) in enumerate(zip(SPIKE_REQUIREMENT_TEXTS, evidence)):
        decision = ("D-007", "D-008", "D-012")[index - 11] if 11 <= index < 14 else None
        rows.append({"id": text, "text": text, "disposition": "satisfied" if index < 11 else "superseded" if index < 14 else "deferred_not_v1",
            "evidence": _ordered(items), "decision": decision,
            "note": "Source retirement ended new releases; dispatched fixture calendar cases and actual schedule observations remain distinct; the approved six residuals apply" if index == 0 else
                    "Founder action #5 alone is deferred_not_v1" if index == 14 else "Requirement-derived clause"})
    return {"schema_version": "spike-era-requirements-v1", "recorded_at": "2026-10-10", "rows": rows}


def validate_spike_audit_document(document, *, root=_PRODUCT):
    if type(document) in (str, bytes):
        document = _decode(document)
    _keys(document, ("schema_version", "recorded_at", "rows"))
    if document["schema_version"] != "spike-era-requirements-v1" or type(document["rows"]) is not list or len(document["rows"]) != 15:
        _fail("gate_e.spike_enumeration")
    _date(document["recorded_at"])
    expected = build_spike_audit_document(root=root)
    for row, wanted in zip(document["rows"], expected["rows"]):
        _keys(row, wanted)
        if type(row["id"]) is not str or row != wanted:
            _fail("gate_e.spike_requirement_mismatch")
        # Reuse the closed typed evidence validator, including date/hash/test checks.
        template = {"schema_version": "portfolio-ready-v1", "recorded_at": document["recorded_at"],
            "conditions": [{"condition": n, "text": text, "status": "pass", "amendment": None,
                            "evidence": row["evidence"]} for n, text in enumerate(CONDITION_TEXTS, 1)]}
        validate_gate_e_document(template, root=root)
        if row["decision"]:
            _file(root, "docs/decisions/register.md#" + row["decision"].lower())
    if document != expected:
        _fail("gate_e.spike_requirement_mismatch")
    return copy.deepcopy(document)


def render_spike_audit_markdown(document, *, root=_PRODUCT):
    document = validate_spike_audit_document(document, root=root)
    parts = ["# Spike-era requirements audit", "", "Generated from the exact predecessor requirement enumeration.",
        "Recorded: " + document["recorded_at"] + ". Eleven clauses remain in force, three are superseded, and founder action #5 alone is deferred.", ""]
    for row in document["rows"]:
        parts.extend(["## " + row["text"], "", "Disposition: `" + row["disposition"] + "`. " + row["note"] + ".", ""])
        if row["decision"]:
            parts.extend(["Superseding decision: [" + row["decision"] + "](../decisions/register.md#" + row["decision"].lower() + ").", ""])
        # Share the typed-evidence renderer without treating this audit as a condition.
        template = {"schema_version": "portfolio-ready-v1", "recorded_at": document["recorded_at"],
            "conditions": [{"condition": n, "text": text, "status": "pass", "amendment": None,
                            "evidence": row["evidence"]} for n, text in enumerate(CONDITION_TEXTS, 1)]}
        rendered = render_gate_e_markdown(template, root=root)
        parts.extend(["| Evidence type" + rendered.split("| Evidence type", 1)[1].split("## Condition 2", 1)[0].rstrip(), ""])
    return "\n".join(parts)


def _exchange_directories(previous, replacement):
    """One OS namespace operation: even process termination cannot split a pair."""
    import ctypes
    import sys
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = libc.renameatx_np
        function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        result = function(-2, os.fsencode(previous), -2, os.fsencode(replacement), 2)
    elif sys.platform.startswith("linux"):
        function = libc.renameat2
        function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        result = function(-100, os.fsencode(previous), -100, os.fsencode(replacement), 2)
    else:
        _fail("gate_e.atomic_exchange_unavailable")
    if result:
        _fail("gate_e.atomic_exchange_failed")


def _generate_common_pair(document, json_path, markdown_path, markdown):
    """Stage a complete common-directory successor; preserve every sibling byte.

    The shared lock lives outside the exchanged directory. A competing writer
    fails closed. The exchange is the only mutation of the visible authority.
    """
    import shutil
    import tempfile
    from .hosted_replay import _path, _pair_lock, HostedReplayPublicError
    json_path, markdown_path = _path(json_path), _path(markdown_path)
    if json_path == markdown_path:
        _fail("gate_e.unsafe_path")
    common = Path(os.path.commonpath((json_path.parent, markdown_path.parent)))
    if common == common.parent or common.name != "docs":
        _fail("gate_e.unsafe_common_directory")
    common.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _pair_lock(common.parent / "gate-e-json", common.parent / "gate-e-markdown"):
            if common.exists():
                for path in common.rglob("*"):
                    if path.is_symlink() or path.is_file() and path.stat().st_nlink != 1:
                        _fail("gate_e.unsafe_path")
            with tempfile.TemporaryDirectory(prefix=".gate-e-", dir=common.parent) as directory:
                candidate = Path(directory) / "docs"
                if common.exists():
                    shutil.copytree(common, candidate)
                else:
                    candidate.mkdir()
                for target, raw in ((json_path, _encode(document)), (markdown_path, markdown.encode())):
                    path = candidate / target.relative_to(common)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("wb") as handle:
                        handle.write(raw); handle.flush(); os.fsync(handle.fileno())
                    if path.read_bytes() != raw:
                        _fail("gate_e.staged_drift")
                if common.exists():
                    _exchange_directories(common, candidate)
                else:
                    os.replace(candidate, common)
    except HostedReplayPublicError as error:
        _fail("gate_e.lock_busy" if error.category == "hosted_replay.lock_busy" else "gate_e.write_failed")
    except OSError:
        _fail("gate_e.write_failed")


def generate_gate_e_pair(document, json_path, markdown_path, *, root=_PRODUCT):
    document = validate_portfolio_authority(document, root=root)
    _pair_destinations(json_path, markdown_path, AUTHORITY_PATH, MARKDOWN_PATH)
    _generate_common_pair(document, json_path, markdown_path, render_gate_e_markdown(document, root=root))


def generate_spike_audit_pair(document, json_path, markdown_path, *, root=_PRODUCT):
    document = validate_spike_audit_document(document, root=root)
    _pair_destinations(json_path, markdown_path, SPIKE_AUTHORITY_PATH, SPIKE_MARKDOWN_PATH)
    _generate_common_pair(document, json_path, markdown_path, render_spike_audit_markdown(document, root=root))


def check_spike_audit_pair(json_path, markdown_path, *, root=_PRODUCT):
    from .hosted_replay import _pair_lock, HostedReplayPublicError
    try:
        with _pair_lock(Path(root) / "gate-e-json", Path(root) / "gate-e-markdown"):
            return _check_spike_audit_unlocked(json_path, markdown_path, root=root)
    except HostedReplayPublicError:
        _fail("gate_e.lock_busy")


def _check_spike_audit_unlocked(json_path, markdown_path, *, root=_PRODUCT):
    document = validate_spike_audit_document(_read(json_path), root=root)
    if _read(json_path) != _encode(document) or _read(markdown_path) != render_spike_audit_markdown(document, root=root).encode():
        _fail("gate_e.generated_drift")
    return document


def _pair_destinations(json_path, markdown_path, authority, rendering):
    from .hosted_replay import _path, HostedReplayPublicError
    try:
        paths = (_path(json_path), _path(markdown_path))
        common = Path(os.path.commonpath((paths[0].parent, paths[1].parent)))
        if tuple(path.relative_to(common).as_posix() for path in paths) != (
                str(PurePosixPath(authority).relative_to("docs")),
                str(PurePosixPath(rendering).relative_to("docs"))):
            _fail("gate_e.unsafe_pair_destinations")
    except (ValueError, HostedReplayPublicError):
        _fail("gate_e.unsafe_pair_destinations")


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
