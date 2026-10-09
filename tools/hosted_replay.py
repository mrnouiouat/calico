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
_ROUTES = ("accepted", "repeat", "rejected", "calendar-refused", "failure", "cancelled")
_JOBS = (*("prepare-" + scenario for scenario in _SCENARIOS),
         *("route-" + scenario for scenario in _ROUTES),
         *("publish-" + scenario for scenario in _ROUTES), "calendar-matrix", "audit-safe-evidence")
_CATEGORIES = ("excluded_identifier", "street_address", "contact", "private_path", "exception")
_SURFACES = ("stdout", "stderr", "status", "summary", "publication")
_MAX_JSON = 65536
_BLOCKED_GIT_CONFIG = r"^(credential\.|url\.|http\.|include\.|includeIf\.|init\.templateDir|core\.(sshCommand|hooksPath|gitProxy)|remote\..*\.(uploadpack|receivepack|proxy))"


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
        except OSError:
            _fail("replay.isolation_rejected")
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
        if key in {"GIT_TEMPLATE_DIR", "GIT_EXEC_PATH"}:
            _fail("replay.credentials_rejected")
    completed = subprocess.run(["git", "config", "--get-regexp",
        _BLOCKED_GIT_CONFIG],
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
    if root not in _OWNED_ROOTS or not root.is_dir():
        _fail("replay.isolation_rejected")
    if _OWNED_ROOTS[root] != (root.stat().st_dev, root.stat().st_ino):
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
        if not (workspace.repo / ".git").is_dir():
            _fail("replay.isolation_rejected")
        completed = subprocess.run(["git", "config", "--local", "--get-regexp",
            _BLOCKED_GIT_CONFIG],
            cwd=workspace.repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        if completed.returncode != 1:
            _fail("replay.credentials_rejected")
        if _git(workspace.repo, "remote", "get-url", "origin") != str(workspace.remote):
            _fail("replay.isolation_rejected")
        if _git(workspace.repo, "remote", "get-url", "--push", "origin") != str(workspace.remote):
            _fail("replay.isolation_rejected")
    if workspace.remote.exists() and _git(workspace.remote, "rev-parse", "--is-bare-repository") != "true":
        _fail("replay.isolation_rejected")
    for repository, git_dir in ((workspace.repo, workspace.repo / ".git"), (workspace.remote, workspace.remote)):
        if repository.exists():
            configured = subprocess.run(["git", "config", "--local", "--get-regexp", _BLOCKED_GIT_CONFIG],
                cwd=repository, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
            if configured.returncode != 1:
                _fail("replay.credentials_rejected")
            if any(path.is_file() and not path.name.endswith(".sample") for path in (git_dir / "hooks").glob("*")):
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
    if not isinstance(run_tuple, ReplayRunTuple):
        _fail()
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
    if any(type(token) is not str or re.fullmatch(r"CALICO_REPLAY_CANARY_[a-p]{64}", token) is None for token in canaries):
        _fail("replay.invalid_audit")
    records = []
    for surface in sorted(surfaces):
        value = surfaces[surface]
        if isinstance(value, Path):
            _unlinked(value)
            try:
                raw = value.read_bytes()
            except OSError:
                _fail("replay.invalid_audit")
        elif type(value) is bytes:
            raw = value
        else:
            _fail("replay.invalid_audit")
        hits = sum(raw.count(token.encode()) for token in canaries)
        if hits:
            _fail("replay.canary_found")
        from tools.privacy_scan.scanner import _scan_utf8_chunks
        if _scan_utf8_chunks(surface, [raw]):
            _fail("replay.privacy_failed")
        if surface in {"status", "summary", "publication"} and not raw:
            _fail("replay.empty_audit")
        records.append({"surface": surface, "byte_length": len(raw), "sha256": _digest(raw), "canary_hits": 0})
    return records


def _outcome(state, scenario, baseline):
    return {"scenario": scenario, "outcome": state.status.outcome, "reason_category": state.status.reason_category,
        "build_calls": state.build_calls, "publication_calls": state.publication_calls,
        "status_sha256": _digest(state.status.to_json().encode()), "analytical_sha256": state.analytical,
        "prior_analytical_sha256": baseline.analytical}


def _published_bytes(workspace):
    """Read every actual recursive published blob, including carried controls."""
    validate_replay_workspace(workspace)
    tip = _git(workspace.repo, "ls-remote", "origin", "refs/heads/published-data").split()[0]
    paths = _git(workspace.repo, "ls-tree", "-r", "--name-only", tip).splitlines()
    allowlist = load_allowlist(_PRODUCT / "contracts/publication-exports-v3.json")
    expected = sorted([*CARRIED_FORWARD_PATHS, "manifest/published-manifest-v1.json",
                       *("exports/" + entry.file_name for entry in allowlist.exports)])
    if paths != expected:
        _fail("replay.invalid_publication_tree")
    chunks = []
    for name in paths:
        completed = subprocess.run(["git", "show", tip + ":" + name], cwd=workspace.repo,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        if completed.returncode or not completed.stdout:
            _fail("replay.invalid_publication_tree")
        chunks.append(completed.stdout)
    return b"".join(chunks)


def _checkpoint(workspace, fixture, baseline, state, run_tuple, scenario, evidence, outcomes, stdout, stderr, authorized):
    parent = baseline.commit
    commit = _git(workspace.repo, "ls-remote", "origin", "refs/heads/published-data").split()[0]
    tree = _git(workspace.repo, "rev-parse", commit + "^{tree}")
    status = state.status.to_json().encode()
    summary = _json({"scenario": scenario, "input_profile": "fixture", "evidence_class": evidence}).encode()
    # Every actual publication file is audited; no raw/source bytes are selected.
    files = sorted([*state.staging.glob("exports/*.csv"), state.staging / "manifest/published-manifest-v1.json",
                    state.staging / "capture-status.json"])
    raw_publication = b"".join(path.read_bytes() for path in files) + _published_bytes(workspace)
    canaries = tuple(derive_replay_canary(run_tuple, name, category) for name in _JOBS for category in _CATEGORIES)
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
    identity = ReplayRunTuple(**document["run_tuple"])
    fixture = _fixture_for(identity)
    if document["input_digest"] != _input_digest(fixture) or document["policy_sha256"] != _digest(fixture.policy):
        _fail()
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
    if document["provenance_digest"] != _digest(_json(catalog).encode() + fixture.policy):
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
    status_raw = (_json(document["capture_status"]) + "\n").encode()
    if next(row["sha256"] for row in audits if row["surface"] == "status") != _digest(status_raw):
        _fail()
    final = document["outcomes"][-1]
    if final["status_sha256"] != _digest(status_raw) or final["outcome"] != document["capture_status"]["outcome"]:
        _fail()
    if final["analytical_sha256"] != document["analytical_sha256"]:
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
    derive_replay_canary(run_tuple, "publish-accepted", _CATEGORIES[0])
    if _input_digest(_fixture_for(run_tuple)) != expected_input_digest:
        _fail("replay.reconstruction_mismatch")
    return _run(runner_temp=runner_temp, run_tuple=run_tuple, scenario=scenario, worker=True,
        expected_input_digest=expected_input_digest, expected_provenance_digest=expected_provenance_digest)


def _fixture_for(run_tuple):
    # Committed fixture fields are empty. Private runtime bytes deliberately
    # carry domain-separated text, unchanged across preparation/reconstruction.
    excluded = " ".join(derive_replay_canary(run_tuple, "prepare-accepted", category) for category in _CATEGORIES)
    return replace(hosted_replay_fixture(), excluded_value=excluded)


def run_replay_sequence(*, runner_temp, scenario_set="accepted-repeat-rejected-v1"):
    if scenario_set != "accepted-repeat-rejected-v1":
        _fail("replay.invalid_scenario")
    run_tuple = ReplayRunTuple("fixture/supporting-sequence", 1, 1, "a" * 40)
    fixture = _fixture_for(run_tuple)
    output, errors = io.StringIO(), io.StringIO()
    with replay_workspace(runner_temp) as workspace:
        with redirect_stdout(output), redirect_stderr(errors):
            original = _seed(workspace, fixture, len(fixture.releases)-1)
            state = _candidate(workspace, fixture, original, "accepted")
            publication = _publish(workspace, state.staging)
            state.publication_calls = 1
            outcomes = [_outcome(state, "accepted", original)]
            print(state.status.to_json())
            manifest = state.staging / "manifest/published-manifest-v1.json"
            seed_private_policy_bundle(workspace.archive, original.store,
                published_manifest_path=manifest, published_data_commit=publication.commit_sha)
            baseline = SimpleNamespace(store=original.store, staging=state.staging, catalog=state.catalog,
                proof=state.proof, exports=state.exports, commit=publication.commit_sha, tree=publication.tree_sha,
                analytical=state.analytical, binding=(_digest(manifest.read_bytes()), publication.commit_sha),
                lineage=state.lineage)
            for scenario in ("repeat", "rejected"):
                state = _candidate(workspace, fixture, baseline, scenario)
                outcomes.append(_outcome(state, scenario, baseline))
                print(state.status.to_json())
                if _git(workspace.repo, "ls-remote", "origin", "refs/heads/published-data").split()[0] != baseline.commit:
                    _fail("replay.analytical_drift")
            return _checkpoint(workspace, fixture, original, state, run_tuple, scenario_set,
                "supporting_local_sequence", outcomes, output.getvalue().encode(), errors.getvalue().encode(), False)


@dataclass(frozen=True)
class HostedEnvelope:
    document: dict

    def to_dict(self):
        return validate_envelope(self.document)

    def to_json(self):
        return _json(self.to_dict())

    def __getitem__(self, key):
        return self.document[key]


def _api_list(document, key):
    if not isinstance(document, dict) or type(document.get("total_count")) is not int:
        _fail("replay.invalid_api_payload")
    rows = document.get(key)
    if type(rows) is not list or document["total_count"] != len(rows):
        _fail("replay.invalid_api_payload")
    return rows


def _job_key(name):
    if type(name) is not str or len(name) > 160:
        _fail("replay.invalid_api_payload")
    # Reusable workflow jobs have a host-supplied caller / callee name.
    key = name.split(" / ")[0]
    if key not in _JOBS:
        _fail("replay.invalid_api_payload")
    return key


def _run_identity(run):
    if not isinstance(run, dict) or run.get("status") != "completed" or run.get("conclusion") != "success" or run.get("event") != "workflow_dispatch":
        _fail("replay.invalid_api_payload")
    repository = run.get("repository")
    if not isinstance(repository, dict):
        _fail("replay.invalid_api_payload")
    identity = ReplayRunTuple(repository.get("full_name"), run.get("id"), run.get("run_attempt"), run.get("head_sha"))
    derive_replay_canary(identity, "audit-safe-evidence", _CATEGORIES[0])
    return identity


def validate_envelope(document):
    if isinstance(document, (str, bytes)):
        document = _decode(document)
    schema = _decode((_PRODUCT / "contracts/hosted-replay-envelope-v1.schema.json").read_bytes())
    _schema(document, schema)
    validate_live_boundaries(document["live_before"], document["live_after"])
    jobs = document["jobs"]
    if [row["job_name"] for row in jobs] != sorted(_JOBS) or len({row["job_id"] for row in jobs}) != len(jobs):
        _fail()
    for row in jobs:
        expected = "skipped" if row["job_name"].startswith("publish-") and row["job_name"] != "publish-accepted" else "success"
        if row["conclusion"] != expected:
            _fail()
    matrix = document["worker_matrix"]
    if [row["scenario"] for row in matrix] != list(_ROUTES):
        _fail()
    for row in matrix:
        accepted = row["scenario"] == "accepted"
        if row["should_publish"] is not accepted or row["worker_conclusion"] != ("success" if accepted else "skipped"):
            _fail()
    checkpoints = document["checkpoints"]
    if [row["job_name"] for row in checkpoints] != ["prepare-accepted", "prepare-repeat", "prepare-rejected", "publish-accepted"]:
        _fail()
    for row in checkpoints:
        driver = validate_driver(row["driver"])
        if driver["run_tuple"] != document["run_tuple"] or driver["evidence_class"] == "supporting_local_sequence":
            _fail()
        expected_evidence = "fresh_job_reconstruction" if row["job_name"] == "publish-accepted" else "staged_prepare"
        if driver["evidence_class"] != expected_evidence or row["job_name"].split("-")[-1] != driver["scenario"]:
            _fail()
    before, after = checkpoints[0]["driver"], checkpoints[-1]["driver"]
    for key in ("input_digest", "provenance_digest", "source_catalog", "exports", "analytical_sha256"):
        if before[key] != after[key]:
            _fail("replay.reconstruction_mismatch")
    logs = document["raw_log_audits"]
    executed = sorted(name for name in _JOBS if not name.startswith("publish-") or name == "publish-accepted")
    if [row["job_name"] for row in logs] != executed:
        _fail()
    if any(row["audit"]["surface"] != "raw_log" or row["audit"]["byte_length"] <= 0 for row in logs):
        _fail()
    return document


def validate_live_boundary(document):
    """Require complete, canonical safe measurements of the live Git boundary.

    Hashes describe raw manifest/export/control bytes, not JSON reserialization.
    The isolated replay does not measure the private archive inventory; its
    digest remains explicitly unobserved rather than borrowing a Git digest.
    Object key order is immaterial in the envelope, but inventories are sorted.
    """
    schema = _decode((_PRODUCT / "contracts/hosted-replay-envelope-v1.schema.json").read_bytes())
    _schema(document, schema["properties"]["live_before"])
    refs = document["protected_refs"]
    names = [row["ref"] for row in refs]
    if names != sorted(set(names)) or not {"refs/heads/main", "refs/heads/published-data"} <= set(names):
        _fail("replay.invalid_live_boundary")
    for name in names:
        # This positive ASCII subset is narrower than git-check-ref-format.
        # Reject its remaining ambiguous/path-like component forms explicitly.
        parts = name.split("/")[2:]
        if (".." in name or "//" in name or any(not part or part.startswith(".") or
                part.endswith((".", ".lock")) for part in parts)):
            _fail("replay.invalid_live_boundary")
        from tools.privacy_scan.scanner import scan_text
        if "CALICO_REPLAY_CANARY_" in name or scan_text("protected_ref", name):
            _fail("replay.invalid_live_boundary")
    published_ref = next(row for row in refs if row["ref"] == "refs/heads/published-data")
    if published_ref["sha"] != document["published_data_commit"]:
        _fail("replay.invalid_live_boundary")
    names = sorted(entry.export_name for entry in load_allowlist(
        _PRODUCT / "contracts/publication-exports-v3.json").exports)
    if [row["export_name"] for row in document["exports"]] != names:
        _fail("replay.invalid_live_boundary")
    if [row["file_name"] for row in document["controls"]] != sorted(CARRIED_FORWARD_PATHS):
        _fail("replay.invalid_live_boundary")
    return document


def validate_live_boundaries(before, after):
    validate_live_boundary(before)
    validate_live_boundary(after)
    for key in before:
        if before[key] != after[key]:
            _fail("replay.live_boundary_changed")


def collect_hosted_envelope(*, run, jobs, artifacts, raw_logs, safe_job_outputs,
                            live_before, live_after, historical_real_republish):
    # Reject incomplete measurements or drift before inspecting API outputs,
    # deriving byte commitments or allowing any downstream public projection.
    validate_live_boundaries(live_before, live_after)
    identity = _run_identity(run)
    rows = _api_list(jobs, "jobs")
    job_records, job_ids = {}, set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] < 1 or row["id"] in job_ids:
            _fail("replay.invalid_api_payload")
        key = _job_key(row.get("name"))
        if key in job_records or row.get("status") != "completed":
            _fail("replay.invalid_api_payload")
        if type(row.get("run_id")) is not int or row["run_id"] != identity.run_id or row.get("head_sha") != identity.head_sha:
            _fail("replay.invalid_api_payload")
        if "run_attempt" in row and (type(row["run_attempt"]) is not int or row["run_attempt"] != identity.run_attempt):
            _fail("replay.invalid_api_payload")
        steps = row.get("steps")
        if type(steps) is not list or len(steps) > 40:
            _fail("replay.invalid_api_payload")
        numbers = set()
        for step in steps:
            if (type(step) is not dict or type(step.get("number")) is not int or step["number"] <= 0 or
                step["number"] in numbers or step.get("status") != "completed" or step.get("conclusion") not in
                {"success", "skipped"} or type(step.get("name")) is not str or not 0 < len(step["name"]) <= 256):
                _fail("replay.invalid_api_payload")
            numbers.add(step["number"])
        job_ids.add(row["id"])
        expected = "skipped" if key.startswith("publish-") and key != "publish-accepted" else "success"
        if row.get("conclusion") != expected:
            _fail("replay.job_conclusion_mismatch")
        if expected == "success" and not steps:
            _fail("replay.invalid_api_payload")
        job_records[key] = row
    if set(job_records) != set(_JOBS) or _api_list(artifacts, "artifacts"):
        _fail("replay.invalid_api_payload")
    output_names = {*("prepare-" + scenario for scenario in _SCENARIOS), "publish-accepted",
                    *("route-" + scenario for scenario in _ROUTES), "audit-safe-evidence"}
    if not isinstance(safe_job_outputs, dict) or set(safe_job_outputs) != output_names:
        _fail("replay.invalid_job_output")
    canaries = tuple(derive_replay_canary(identity, job, category) for job in _JOBS for category in _CATEGORIES)
    documents = {}
    for name, raw in safe_job_outputs.items():
        if not isinstance(raw, (str, bytes)):
            _fail("replay.invalid_job_output")
        encoded = raw.encode() if isinstance(raw, str) else raw
        audit_bytes({"stdout": encoded}, canaries)
        doc = _decode(encoded)
        if name.startswith("prepare-") or name == "publish-accepted":
            documents[name] = validate_driver(doc)
        elif name.startswith("route-"):
            if type(doc) is not dict or set(doc) != {"should_publish"} or type(doc["should_publish"]) is not bool:
                _fail("replay.invalid_job_output")
            if doc["should_publish"] is not (name == "route-accepted"):
                _fail("replay.job_conclusion_mismatch")
            documents[name] = doc
        else:
            if doc != {"cleanup_verified": True} or type(doc.get("cleanup_verified")) is not bool:
                _fail("replay.invalid_job_output")
    executed = sorted(name for name in _JOBS if job_records[name]["conclusion"] != "skipped")
    if not isinstance(raw_logs, dict) or sorted(raw_logs) != executed:
        _fail("replay.invalid_audit")
    audits = []
    for name in executed:
        raw = raw_logs[name]
        if type(raw) is not bytes or not raw:
            _fail("replay.invalid_audit")
        record = audit_bytes({"raw_log": raw}, canaries)[0]
        audits.append({"job_name": name, "audit": record})
    document = {"schema_version": HOSTED_ENVELOPE_SCHEMA_VERSION, "evidence_class": "hosted_fixture_replay",
        "run_tuple": identity.to_dict(), "event": "workflow_dispatch", "conclusion": "success",
        "artifact_count": 0, "cleanup_verified": True, "result": "pass",
        "jobs": [{"job_name": name, "job_id": job_records[name]["id"], "status": "completed",
                  "conclusion": job_records[name]["conclusion"]} for name in sorted(_JOBS)],
        "worker_matrix": [{"scenario": scenario, "should_publish": documents["route-" + scenario]["should_publish"],
            "route_conclusion": job_records["route-" + scenario]["conclusion"],
            "worker_conclusion": job_records["publish-" + scenario]["conclusion"]} for scenario in _ROUTES],
        "checkpoints": [{"job_name": name, "driver": documents[name]} for name in
            ("prepare-accepted", "prepare-repeat", "prepare-rejected", "publish-accepted")],
        "raw_log_audits": audits, "live_before": live_before, "live_after": live_after,
        "historical_real_republish": historical_real_republish,
        "evidence_classes": {"hosted_fixture_replay": "measured", "live_source": "not_observed_by_replay",
            "actual_schedule": "not_observed_by_replay", "historical_real_restore_republish": "preserved"}}
    return HostedEnvelope(validate_envelope(document))


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        _fail("replay.invalid_arguments")


def main(argv=None):
    parser = _SafeParser(prog="tools.hosted_replay")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_SafeParser)
    for name in ("prepare-checkpoint", "publication-worker", "supporting-sequence"):
        command = commands.add_parser(name)
        command.add_argument("--runner-temp", type=Path,
            default=Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())).resolve())
        if name != "supporting-sequence":
            command.add_argument("--repository", required=True)
            command.add_argument("--run-id", type=int, required=True)
            command.add_argument("--run-attempt", type=int, required=True)
            command.add_argument("--head-sha", required=True)
            command.add_argument("--scenario", choices=_SCENARIOS, required=True)
        if name == "publication-worker":
            command.add_argument("--expected-input-digest", required=True)
            command.add_argument("--expected-provenance-digest", required=True)
            command.add_argument("--route-authorized", choices=("true", "false"), required=True)
    for name in ("validate-driver", "validate-envelope", "collect-envelope"):
        command = commands.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
        if name == "collect-envelope":
            command.add_argument("--logs-dir", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "supporting-sequence":
            result = run_replay_sequence(runner_temp=args.runner_temp)
        elif args.command in {"prepare-checkpoint", "publication-worker"}:
            identity = ReplayRunTuple(args.repository, args.run_id, args.run_attempt, args.head_sha)
            kwargs = dict(runner_temp=args.runner_temp, run_tuple=identity, scenario=args.scenario)
            if args.command == "prepare-checkpoint":
                result = prepare_hosted_checkpoint(**kwargs)
            else:
                result = run_publication_worker(**kwargs, route_authorized=args.route_authorized == "true",
                    expected_input_digest=args.expected_input_digest, expected_provenance_digest=args.expected_provenance_digest)
        else:
            _unlinked(args.input)
            raw = args.input.read_bytes()
            if args.command == "validate-driver":
                validate_driver(raw)
                print(_json({"category": "replay.driver_verified"}))
                return 0
            if args.command == "validate-envelope":
                validate_envelope(raw)
                print(_json({"category": "replay.envelope_verified"}))
                return 0
            bundle = _decode(raw)
            keys = {"run", "jobs", "artifacts", "safe_job_outputs", "live_before", "live_after", "historical_real_republish"}
            if type(bundle) is not dict or set(bundle) != keys:
                _fail()
            validate_live_boundaries(bundle["live_before"], bundle["live_after"])
            directory = _runner_parent(args.logs_dir)
            executed = [name for name in _JOBS if not name.startswith("publish-") or name == "publish-accepted"]
            logs = {}
            for name in executed:
                path = _unlinked(directory / (name + ".log"))
                logs[name] = path.read_bytes()
            result = collect_hosted_envelope(**bundle, raw_logs=logs)
        print(result.to_json())
        return 0
    except (ReplayError, OSError, ValueError, TypeError, KeyboardInterrupt):
        print("replay.failed", file=sys.stderr)
        return 1
    except Exception:
        # Production adapters have value-free errors, but an unexpected provider
        # or test hook must also never reveal its exception or path here.
        print("replay.failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
