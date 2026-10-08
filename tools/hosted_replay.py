"""Bounded actual-SQL replay. Local sequence evidence never proves hosted routing.

All external seams are finite fixture inputs, an in-memory archive and a local
bare repository owned by this invocation. No live adapter is instantiated.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from dataclasses import dataclass, asdict, replace
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from calico_capture.archive import Archive, synchronize_verified_transaction
from calico_capture.orchestrator import capture
from calico_capture.private_policy import seed_private_policy_bundle
from calico_capture.restore import restore_catalog_with_private_policy
from calico_capture.status import project_safe_status, validate_capture_status_document
from calico_dbt.catalog import build_catalog_from_manifests
from calico_dbt.eligibility import load_eligibility_classifications
from calico_dbt.preflight import prepare_runtime_input
from calico_dbt.runner import build
from calico_landing.admission import admit, load_default_status_contract
from calico_publish.allowlist import load_allowlist
from calico_publish.cli import _accepted_releases, _TOOLCHAIN
from calico_publish.export import export_all
from calico_publish.gate import verify
from calico_publish.manifest import project_published_manifest
from calico_publish.transaction import publish_tree, CARRIED_FORWARD_PATHS
from tests.capture.fakes import FakeArchive
from tests.fixtures.hosted_replay import hosted_replay_fixture
from tools.privacy_scan.policy import load_policy
from tools.privacy_scan.scanner import scan_paths

DRIVER_SCHEMA_VERSION = "hosted-replay-driver-v1"
HOSTED_ENVELOPE_SCHEMA_VERSION = "hosted-replay-envelope-v1"
_PRODUCT = Path(__file__).resolve().parents[1]
_WORKSHOP = _PRODUCT.parent / "calico-build"
_OWNED_ROOTS = {}
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SCENARIOS = ("accepted", "repeat", "rejected")
_JOBS = ("prepare-accepted", "prepare-repeat", "prepare-rejected", "publication-accepted")
_CATEGORIES = ("excluded_identifier", "street_address", "contact", "private_path", "exception")
_SURFACES = ("stdout", "stderr", "status", "summary", "publication")
_MAX_JSON = 65536


class ReplayError(Exception):
    """Only a fixed category crosses the public boundary."""
    def __init__(self, category):
        self.category = category
        super().__init__(category)


def _fail(category="replay.invalid_evidence"):
    raise ReplayError(category)


def _json(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _decode(raw):
    if not isinstance(raw, (bytes, str)) or len(raw) > _MAX_JSON:
        _fail()
    try:
        return json.loads(raw, object_pairs_hook=_unique,
                          parse_constant=lambda _: _fail())
    except (ValueError, UnicodeError, TypeError):
        _fail()


def _schema(value, schema):
    """Validate the finite stdlib subset used by these closed contracts.

    The schemas contain no remote references or executable extensions.
    Unsupported keywords fail closed rather than being ignored.
    """
    supported = {"$schema", "title", "description", "type", "properties", "required",
        "additionalProperties", "const", "enum", "pattern", "minimum", "maximum",
        "minItems", "maxItems", "items", "minLength", "maxLength"}
    if set(schema) - supported:
        _fail()
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None)}
    if isinstance(kind, list):
        if not any(type(value) is types[item] for item in kind):
            _fail()
        kind = next(item for item in kind if type(value) is types[item])
    if kind and (type(value) is not types[kind]):
        _fail()
    if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
        _fail()
    if "enum" in schema and value not in schema["enum"]:
        _fail()
    if kind == "object":
        if set(value) != set(schema["required"]) or schema["additionalProperties"] is not False:
            _fail()
        for key in schema["required"]:
            _schema(value[key], schema["properties"][key])
    if kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 1000):
            _fail()
        for item in value:
            _schema(item, schema["items"])
    if kind == "integer" and not schema.get("minimum", 0) <= value <= schema.get("maximum", 10**18):
        _fail()
    if kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", _MAX_JSON):
            _fail()
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            _fail()


@dataclass(frozen=True)
class ReplayRunTuple:
    repository: str
    run_id: int
    run_attempt: int
    head_sha: str

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class DriverCheckpoint:
    document: dict

    def to_dict(self):
        # Serialization always independently validates mutable nested input.
        return validate_driver(self.document)

    def to_json(self):
        return _json(self.to_dict())

    def __getitem__(self, key):
        return self.document[key]


@dataclass(frozen=True)
class ReplayWorkspace:
    root: Path
    archive: Archive
    remote: Path
    repo: Path
    target_ref: str


def _unlinked(path):
    raw = Path(os.path.abspath(path))
    current = Path(raw.anchor)
    for part in raw.parts[1:]:
        current /= part
        try:
            meta = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(meta.st_mode) or getattr(meta, "st_file_attributes", 0) & 0x400:
            _fail("replay.isolation_rejected")
    if raw != raw.resolve():
        _fail("replay.isolation_rejected")
    return raw


def _credentials():
    for key, value in os.environ.items():
        if not value:
            continue
        if (key.startswith(("CALICO_B2_", "B2_", "AWS_", "AZURE_", "GOOGLE_APPLICATION_"))
            or key in {"GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "GIT_SSH", "GIT_SSH_COMMAND",
                       "GIT_ASKPASS", "SSH_ASKPASS", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
                       "GIT_DIR", "GIT_WORK_TREE",
                       "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE"}):
            _fail("replay.credentials_rejected")
        if key in {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"} and value != os.devnull:
            _fail("replay.credentials_rejected")
    completed = subprocess.run(["git", "config", "--get-regexp",
        r"^(credential\.|url\.|http\.|core\.(sshCommand|hooksPath))"],
        cwd=Path(tempfile.gettempdir()).resolve(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if completed.returncode != 1:
        _fail("replay.credentials_rejected")


def _runner_parent(path):
    path = _unlinked(path)
    allowed = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())).resolve()
    if not path.is_dir() or not path.is_relative_to(allowed):
        _fail("replay.isolation_rejected")
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            _fail("replay.isolation_rejected")
    if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
        _fail("replay.isolation_rejected")
    return path


def validate_replay_workspace(workspace):
    if not isinstance(workspace, ReplayWorkspace) or type(workspace.archive) is not FakeArchive:
        _fail("replay.isolation_rejected")
    root = _unlinked(workspace.root)
    if _OWNED_ROOTS.get(root) != (root.stat().st_dev, root.stat().st_ino):
        _fail("replay.isolation_rejected")
    if root.is_relative_to(_PRODUCT) or root.is_relative_to(_WORKSHOP):
        _fail("replay.isolation_rejected")
    if workspace.target_ref != "published-data":
        _fail("replay.isolation_rejected")
    if not isinstance(workspace.remote, Path) or workspace.remote != root / "remote.git":
        _fail("replay.isolation_rejected")
    if workspace.repo != root / "repository":
        _fail("replay.isolation_rejected")
    _unlinked(workspace.remote)
    _unlinked(workspace.repo)
    for path in root.rglob("*"):
        _unlinked(path)
    if workspace.repo.exists():
        completed = subprocess.run(["git", "config", "--local", "--get-regexp",
            r"^(credential\.|url\.|http\.|core\.(sshCommand|hooksPath))"],
            cwd=workspace.repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        if completed.returncode != 1:
            _fail("replay.credentials_rejected")
        if _git(workspace.repo, "remote", "get-url", "origin") != str(workspace.remote):
            _fail("replay.isolation_rejected")
    if workspace.remote.exists() and _git(workspace.remote, "rev-parse", "--is-bare-repository") != "true":
        _fail("replay.isolation_rejected")


@contextmanager
def _environment(root):
    # All production temporary roots remain under this owned root, including
    # capture, archive restoration, dbt and Git's private index/cache.
    keys = {"TMPDIR": str(root), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_DATE": "2031-05-01T00:00:00Z", "GIT_COMMITTER_DATE": "2031-05-01T00:00:00Z"}
    old = {key: os.environ.get(key) for key in keys}
    prior_temp = tempfile.tempdir
    os.environ.update(keys)
    tempfile.tempdir = str(root)
    try:
        yield
    finally:
        tempfile.tempdir = prior_temp
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _git(cwd, *args):
    result = subprocess.run(["git", *args], cwd=cwd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, text=True)
    if result.returncode:
        _fail("replay.git_failed")
    return result.stdout.strip()


@contextmanager
def replay_workspace(runner_temp):
    _credentials()
    parent = _runner_parent(runner_temp)
    with tempfile.TemporaryDirectory(prefix="calico-hosted-replay-", dir=parent) as temporary:
        root = Path(temporary).resolve()
        _OWNED_ROOTS[root] = (root.stat().st_dev, root.stat().st_ino)
        workspace = ReplayWorkspace(root, FakeArchive(), root / "remote.git", root / "repository", "published-data")
        try:
            validate_replay_workspace(workspace)
            with _environment(root):
                _git(root, "init", "--bare", str(workspace.remote))
                _git(root, "init", "--initial-branch=main", str(workspace.repo))
                _git(workspace.repo, "config", "user.name", "Replay Fixture")
                _git(workspace.repo, "config", "user.email", "replay" + "@" + "example.invalid")
                _git(workspace.repo, "remote", "add", "origin", str(workspace.remote))
                status = project_safe_status(trigger="local", outcome="accepted", reason_category="none",
                    started_at_utc="2031-03-18T00:00:00Z", ended_at_utc="2031-03-18T00:00:00Z",
                    last_accepted_as_of_date="2031-03-18", last_accepted_release_revision=2)
                for name in CARRIED_FORWARD_PATHS:
                    (workspace.repo / name).write_text(status.to_json() if name == "capture-status.json" else "{}", encoding="utf-8")
                _git(workspace.repo, "add", *CARRIED_FORWARD_PATHS)
                _git(workspace.repo, "commit", "-m", "Seed isolated fixture control parent")
                _git(workspace.repo, "push", "origin", "HEAD:refs/heads/published-data")
                validate_replay_workspace(workspace)
                yield workspace
        finally:
            _OWNED_ROOTS.pop(root, None)


def _catalog(store):
    manifests = []
    for path in sorted((store / "releases").glob("*/rev-*/manifest.json")):
        raw = path.read_bytes()
        document = _decode(raw)
        manifests.append((document["as_of_date"], document["release_revision"],
                          document["revision_fingerprint"], raw))
    return build_catalog_from_manifests(manifests)


def _lineage(catalog, store):
    releases, _ = _accepted_releases("real", store, lambda: catalog)
    sources = {(row.as_of_date, row.release_revision): [source.to_dict() for source in row.source_objects]
               for row in releases}
    return [{**asdict(anchor), "source_objects": sources[(anchor.as_of_date, anchor.release_revision)]}
            for anchor in sorted(catalog.releases, key=lambda r: (r.as_of_date, r.release_revision))]


def _input_digest(fixture):
    # Source facts are generated from finite fixture bytes, never supplied by a caller.
    return _digest(_json([{"date": revision.as_of_date, "label": revision.revision_label,
        "sources": [(name, _digest(raw), len(raw)) for name, raw in sorted(fixture.source_payloads(index).items())]}
        for index, revision in enumerate(fixture.releases)]).encode())


def _preflight(store, catalog, root):
    load_eligibility_classifications(store, require_document=True)
    with tempfile.TemporaryDirectory(prefix="required-policy-", dir=root) as temp:
        prepare_runtime_input(store_root=store, catalog=catalog, temp_root=Path(temp),
                              require_eligibility_sidecar=True)


def _build_exports(workspace, store, staging):
    validate_replay_workspace(workspace)
    catalog = _catalog(store)
    _preflight(store, catalog, workspace.root)
    allowlist = load_allowlist(_PRODUCT / "contracts/publication-exports-v3.json")
    staging.mkdir()
    staged = ()

    @contextmanager
    def captured_store():
        admissions = tuple(SimpleNamespace(result=SimpleNamespace(
            as_of_date=anchor.as_of_date, release_revision=anchor.release_revision,
            revision_fingerprint=anchor.revision_fingerprint)) for anchor in catalog.releases)
        yield SimpleNamespace(store_root=store, admissions=admissions)

    def export_hook(database):
        nonlocal staged
        staged = export_all(database, allowlist, staging)

    result = build(mode="fixture", fixture_store_factory=captured_store, export=export_hook)
    if not result.succeeded or not result.proof or min(result.proof.dbt_model_count, result.proof.dbt_test_count) <= 0:
        _fail("replay.dbt_failed")
    releases, parser = _accepted_releases("real", store, lambda: catalog)
    named = [entry for entry in staged if entry.export_name in {
        "dim_public_organizations", "fct_public_status_observations"}]
    if len(named) != 2 or any(entry.row_count <= 0 for entry in named):
        _fail("replay.empty_named_output")
    eligible = next(entry.row_count for entry in named if entry.export_name == "dim_public_organizations")
    manifest = project_published_manifest(allowlist=allowlist, staged_exports=staged,
        accepted_releases=releases, eligible_key_count=eligible, parser_contract_version=parser, toolchain=_TOOLCHAIN)
    (staging / "manifest").mkdir()
    (staging / "manifest/published-manifest-v1.json").write_text(manifest.to_json(), encoding="utf-8")
    control = _git(workspace.repo, "show", "refs/remotes/origin/published-data:capture-status.json")
    (staging / "capture-status.json").write_text(control, encoding="utf-8")
    return catalog, result.proof, staged, result


def _analytical(staging):
    paths = sorted([*staging.glob("exports/*.csv"), staging / "manifest/published-manifest-v1.json"])
    return _digest(_json([(path.relative_to(staging).as_posix(), _digest(path.read_bytes())) for path in paths]).encode())


def _publish(workspace, staging, *, failure_hook=lambda _: None):
    validate_replay_workspace(workspace)
    allowlist = load_allowlist(_PRODUCT / "contracts/publication-exports-v3.json")
    paths = tuple(sorted(["manifest/published-manifest-v1.json",
                          *("exports/" + entry.file_name for entry in allowlist.exports)]))
    expected = {name: _digest((staging / name).read_bytes()) for name in paths}
    if not verify(staging, allowlist, staging / "manifest/published-manifest-v1.json").passed:
        _fail("replay.publication_gate_failed")
    if scan_paths(staging, paths, load_policy(_PRODUCT / "policies/publishable-tree.json")):
        _fail("replay.privacy_failed")
    if expected != {name: _digest((staging / name).read_bytes()) for name in paths}:
        _fail("replay.publication_mutated")
    return publish_tree(repo_dir=workspace.repo, staging_dir=staging, staged_files=paths,
        remote="origin", target_ref=workspace.target_ref, commit_subject="Publish isolated fixture release",
        author_name="Replay Fixture", author_email="replay" + "@" + "example.invalid",
        expected_sha256=expected, allowlist=allowlist, failure_hook=failure_hook)


def _seed(workspace, fixture, count):
    store = workspace.root / "baseline-store"
    store.mkdir()
    for index in range(count):
        candidate = fixture.materialize(workspace.root / ("baseline-source-" + str(index)), index)
        result = admit(candidate, store, status_contract=load_default_status_contract())
        if result.status != "accepted":
            _fail("replay.fixture_rejected")
        synchronize_verified_transaction(workspace.archive, store, result)
    (store / "public-eligibility-v1.json").write_bytes(fixture.policy)
    staging = workspace.root / "baseline-publication"
    catalog, proof, exports, _ = _build_exports(workspace, store, staging)
    publication = _publish(workspace, staging)
    manifest_path = staging / "manifest/published-manifest-v1.json"
    seed_private_policy_bundle(workspace.archive, store, published_manifest_path=manifest_path,
                              published_data_commit=publication.commit_sha)
    return SimpleNamespace(store=store, staging=staging, catalog=catalog, proof=proof, exports=exports,
        commit=publication.commit_sha, tree=publication.tree_sha, analytical=_analytical(staging),
        binding=(_digest(manifest_path.read_bytes()), publication.commit_sha), lineage=_lineage(catalog, store))


def _candidate(workspace, fixture, baseline, scenario):
    validate_replay_workspace(workspace)
    candidate = fixture.materialize(workspace.root / ("candidate-" + scenario), len(fixture.releases)-1,
                                    invalid=scenario == "rejected")
    state = SimpleNamespace(build_calls=0, publication_calls=0, catalog=baseline.catalog,
        proof=baseline.proof, exports=baseline.exports, staging=baseline.staging, lineage=baseline.lineage)

    def restore(root):
        restore_catalog_with_private_policy(workspace.archive, root, catalog=baseline.catalog,
            binding_loader=lambda: baseline.binding, expected_classification_version="hosted-fixture-v1")

    def actual_build(store):
        state.build_calls += 1
        state.staging = workspace.root / "candidate-publication"
        state.catalog, state.proof, state.exports, result = _build_exports(workspace, store, state.staging)
        # Preserve the actual captured input before the capture-owned store is removed.
        state.lineage = _lineage(state.catalog, store)
        state.provenance = _digest(_json(state.lineage).encode() + fixture.policy)
        return result

    status = capture(trigger="workflow_dispatch", archive=workspace.archive, fetch_candidate=lambda: candidate,
        build=actual_build, restore=restore, clock=lambda: "2031-05-01T00:00:00Z", sleeper=lambda _: None)
    expected = {"accepted": "accepted", "repeat": "no_new_release", "rejected": "rejected"}[scenario]
    if status.outcome != expected:
        _fail("replay.capture_failed")
    state.status = status
    state.analytical = _analytical(state.staging)
    state.provenance = _digest(_json(state.lineage).encode() + fixture.policy)
    if scenario != "accepted" and (state.build_calls or state.analytical != baseline.analytical):
        _fail("replay.analytical_drift")
    return state


def derive_replay_canary(run_tuple, job_name, category):
    schema = _decode((_PRODUCT / "contracts/hosted-replay-driver-v1.schema.json").read_bytes())
    _schema(run_tuple.to_dict(), schema["properties"]["run_tuple"])
    if job_name not in _JOBS or category not in _CATEGORIES:
        _fail()
    raw = _json({"domain": "calico-hosted-replay-canary-v1", "run": run_tuple.to_dict(),
                 "job": job_name, "category": category}).encode()
    # Hex is translated to letters: no realistic excluded digit sequence exists.
    token = _digest(raw).translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))
    return "CALICO_REPLAY_CANARY_" + token


def audit_bytes(surfaces, canaries):
    if not isinstance(surfaces, dict) or not surfaces or set(surfaces) - {*_SURFACES, "raw_log"}:
        _fail("replay.invalid_audit")
    if not isinstance(canaries, (tuple, list)) or not canaries:
        _fail("replay.invalid_audit")
    records = []
    for surface in sorted(surfaces):
        value = surfaces[surface]
        if isinstance(value, Path):
            _unlinked(value)
            raw = value.read_bytes()
        elif type(value) is bytes:
            raw = value
        else:
            _fail("replay.invalid_audit")
        hits = sum(raw.count(token.encode()) for token in canaries)
        if hits:
            _fail("replay.canary_found")
        if surface in {"status", "summary", "publication"} and not raw:
            _fail("replay.empty_audit")
        records.append({"surface": surface, "byte_length": len(raw), "sha256": _digest(raw), "canary_hits": 0})
    return records


def _outcome(state, scenario, baseline):
    return {"scenario": scenario, "outcome": state.status.outcome, "reason_category": state.status.reason_category,
        "build_calls": state.build_calls, "publication_calls": state.publication_calls,
        "status_sha256": _digest(state.status.to_json().encode()), "analytical_sha256": state.analytical,
        "prior_analytical_sha256": baseline.analytical}


def _checkpoint(workspace, fixture, baseline, state, run_tuple, scenario, evidence, outcomes, stdout, stderr, authorized):
    parent = baseline.commit
    commit = _git(workspace.repo, "ls-remote", "origin", "refs/heads/published-data").split()[0]
    tree = _git(workspace.repo, "rev-parse", commit + "^{tree}")
    status = state.status.to_json().encode()
    summary = _json({"scenario": scenario, "input_profile": "fixture", "evidence_class": evidence}).encode()
    # Every actual publication file is audited; no raw/source bytes are selected.
    files = sorted([*state.staging.glob("exports/*.csv"), state.staging / "manifest/published-manifest-v1.json",
                    state.staging / "capture-status.json"])
    raw_publication = b"".join(path.read_bytes() for path in files)
    job = "publication-accepted" if authorized else "prepare-" + (scenario if scenario in _SCENARIOS else "accepted")
    canaries = tuple(derive_replay_canary(run_tuple, job, category) for category in _CATEGORIES)
    document = {"schema_version": DRIVER_SCHEMA_VERSION, "evidence_class": evidence, "input_profile": "fixture",
        "run_tuple": run_tuple.to_dict(), "scenario": scenario, "input_digest": _input_digest(fixture),
        "provenance_digest": state.provenance, "policy_sha256": _digest(fixture.policy),
        "reconstructed": evidence == "fresh_job_reconstruction", "baseline_label": "isolated_fixture_baseline",
        "route_authorized": authorized, "dbt_model_count": state.proof.dbt_model_count,
        "dbt_test_count": state.proof.dbt_test_count, "source_catalog": state.lineage,
        "exports": [{"export_name": entry.export_name, "sha256": entry.sha256, "row_count": entry.row_count}
                    for entry in sorted(state.exports, key=lambda item: item.export_name)],
        "local_parent": parent, "local_commit": commit, "local_tree": tree,
        "baseline_analytical_sha256": baseline.analytical, "analytical_sha256": state.analytical,
        "capture_status": state.status.to_dict(), "outcomes": outcomes, "byte_audits": audit_bytes({"stdout": stdout, "stderr": stderr,
                    "status": status, "summary": summary, "publication": raw_publication}, canaries)}
    return DriverCheckpoint(validate_driver(document))


def validate_driver(document):
    if isinstance(document, (bytes, str)):
        document = _decode(document)
    schema = _decode((_PRODUCT / "contracts/hosted-replay-driver-v1.schema.json").read_bytes())
    _schema(document, schema)
    validate_capture_status_document(document["capture_status"])
    catalog = document["source_catalog"]
    identities = [(row["as_of_date"], row["release_revision"]) for row in catalog]
    exports = document["exports"]
    names = [row["export_name"] for row in exports]
    if identities != sorted(set(identities)) or names != sorted(set(names)):
        _fail()
    from calico_landing.contracts import LOGICAL_LIST_ORDER
    from calico_publish.manifest import compute_revision_fingerprint
    for release in catalog:
        sources = release["source_objects"]
        if [row["source_list"] for row in sources] != sorted(LOGICAL_LIST_ORDER):
            _fail()
        if compute_revision_fingerprint({row["source_list"]: row["sha256"] for row in sources}) != release["revision_fingerprint"]:
            _fail()
    expected_names = sorted(entry.export_name for entry in load_allowlist(_PRODUCT / "contracts/publication-exports-v3.json").exports)
    if names != expected_names or any(row["row_count"] <= 0 for row in exports if row["export_name"] in {
            "dim_public_organizations", "fct_public_status_observations"}):
        _fail()
    if min(document["dbt_model_count"], document["dbt_test_count"]) <= 0:
        _fail()
    audits = document["byte_audits"]
    if [row["surface"] for row in audits] != sorted(_SURFACES):
        _fail()
    for row in audits:
        if row["surface"] in {"status", "summary", "publication"} and row["byte_length"] <= 0:
            _fail()
        if row["byte_length"] == 0 and row["sha256"] != _digest(b""):
            _fail()
    for row in document["outcomes"]:
        expected = {"accepted": ("accepted", "none"), "repeat": ("no_new_release", "source_not_advanced"),
                    "rejected": ("rejected", "source_contract_mismatch")}[row["scenario"]]
        if (row["outcome"], row["reason_category"]) != expected:
            _fail()
        if row["scenario"] != "accepted" and (row["build_calls"] or row["publication_calls"] or
            row["analytical_sha256"] != row["prior_analytical_sha256"]):
            _fail()
        if row["scenario"] == "accepted" and row["build_calls"] != 1:
            _fail()
    evidence = document["evidence_class"]
    if evidence == "supporting_local_sequence":
        if document["scenario"] != "accepted-repeat-rejected-v1" or [row["scenario"] for row in document["outcomes"]] != list(_SCENARIOS):
            _fail()
    elif len(document["outcomes"]) != 1 or document["outcomes"][0]["scenario"] != document["scenario"]:
        _fail()
    if evidence == "staged_prepare" and (document["route_authorized"] or any(row["publication_calls"] for row in document["outcomes"])):
        _fail()
    if evidence == "fresh_job_reconstruction" and (not document["route_authorized"] or not document["reconstructed"] or
            document["scenario"] != "accepted" or document["outcomes"][0]["publication_calls"] != 1):
        _fail()
    return document


def _run(*, runner_temp, run_tuple, scenario, worker=False, expected_input_digest=None, expected_provenance_digest=None):
    fixture = _fixture_for(run_tuple)
    output, errors = io.StringIO(), io.StringIO()
    with replay_workspace(runner_temp) as workspace:
        with redirect_stdout(output), redirect_stderr(errors):
            baseline = _seed(workspace, fixture, len(fixture.releases) - (scenario == "accepted"))
            state = _candidate(workspace, fixture, baseline, scenario)
            if worker:
                if state.provenance != expected_provenance_digest or _input_digest(fixture) != expected_input_digest:
                    _fail("replay.reconstruction_mismatch")
                _publish(workspace, state.staging)
                state.publication_calls = 1
            print(state.status.to_json())
        return _checkpoint(workspace, fixture, baseline, state, run_tuple, scenario,
            "fresh_job_reconstruction" if worker else "staged_prepare", [_outcome(state, scenario, baseline)],
            output.getvalue().encode(), errors.getvalue().encode(), worker)


def prepare_hosted_checkpoint(*, runner_temp, run_tuple, scenario):
    if scenario not in _SCENARIOS:
        _fail("replay.invalid_scenario")
    # Validate the public tuple before any filesystem work.
    derive_replay_canary(run_tuple, "prepare-" + scenario, _CATEGORIES[0])
    return _run(runner_temp=runner_temp, run_tuple=run_tuple, scenario=scenario)


def run_publication_worker(*, runner_temp, run_tuple, scenario, expected_input_digest, expected_provenance_digest, route_authorized):
    if route_authorized is not True or scenario != "accepted":
        _fail("replay.route_not_authorized")
    if not all(type(value) is str and _SHA256.fullmatch(value) for value in (expected_input_digest, expected_provenance_digest)):
        _fail("replay.reconstruction_mismatch")
    derive_replay_canary(run_tuple, "publication-accepted", _CATEGORIES[0])
    if _input_digest(_fixture_for(run_tuple)) != expected_input_digest:
        _fail("replay.reconstruction_mismatch")
    return _run(runner_temp=runner_temp, run_tuple=run_tuple, scenario=scenario, worker=True,
        expected_input_digest=expected_input_digest, expected_provenance_digest=expected_provenance_digest)


def _fixture_for(run_tuple):
    # Committed fixture fields are empty. Private runtime bytes deliberately
    # carry domain-separated text, unchanged across preparation/reconstruction.
    excluded = " ".join(derive_replay_canary(run_tuple, "prepare-accepted", category) for category in _CATEGORIES)
    return replace(hosted_replay_fixture(), excluded_value=excluded)
