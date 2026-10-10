"""Immutable preapproval inspection and exact postapproval report diff.

Public checklists describe an earlier snapshot. Live origin, visibility, owner
approval and later CI observations belong to the separate private seal.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile

from . import gate_e
from tools.privacy_scan.git_objects import list_tree, iter_target_entries, BatchBlobReader

JSON_PATH = "docs/evidence/gate-e/final-inspection-checklist-v1.json"
MARKDOWN_PATH = "docs/provenance/final-inspection-checklist.md"
SCHEMA_PATH = "docs/evidence/gate-e/final-inspection-checklist-v1.schema.json"
REPORT_PATHS = frozenset({"README.md", gate_e.AUTHORITY_PATH, gate_e.MARKDOWN_PATH})
FREEZE_OUTPUTS = frozenset({JSON_PATH, MARKDOWN_PATH, "docs/provenance/citation-inventory-v1.json",
    "docs/provenance/citation-transitions-v2.json", "docs/decisions/register.md"})
CHECKS = ("tree_privacy", "reachable_history_privacy", "migration_redaction", "generated_docs",
          "citations", "decision_register", "semantic_inventory", "published_manifest")
MIGRATION_PATH = "docs/ag-registry-migration-2026-08.md"
MIGRATION_RECORD = "docs/redactions/ag-registry-migration-2026-08.json"
# Immutable original-byte identity only; the private original is never opened.
MIGRATION_ORIGINAL_SHA256 = "30b2a386c290a809a540eb8884d643d506f99569ba5bc502ddb6f8fe44a5522e"
MIGRATION_SUCCESSOR_SHA256 = "10b198474c80e7d3ee5726ff8643d9a94d661cac6c0e10b6cf0bf12eb363ac0f"
_PRODUCT = Path(__file__).resolve().parents[2]


class InspectionContractError(Exception):
    def __init__(self, category="inspection.invalid_contract"):
        self.category = category
        super().__init__(category)


def _fail(category="inspection.invalid_contract"):
    raise InspectionContractError(category)


def _git(root, *args):
    try:
        completed = subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=120)
        if completed.returncode:
            _fail("inspection.git_failure")
        return completed.stdout
    except (OSError, subprocess.TimeoutExpired):
        _fail("inspection.git_failure")


def _commit(root, value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        _fail("inspection.immutable_commit_required")
    if _git(root, "rev-parse", value + "^{commit}").decode().strip() != value:
        _fail("inspection.immutable_commit_required")
    return value


def _root(root):
    try:
        root = Path(root)
        if root.is_symlink() or root.resolve(strict=True) != root.absolute():
            _fail("inspection.unsafe_root")
        if Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()) != root:
            _fail("inspection.unsafe_root")
        return root
    except (TypeError, ValueError, OSError):
        _fail("inspection.unsafe_root")


def _refs(root):
    return _git(root, "show-ref", "--head")


@contextmanager
def _snapshot(root, commit):
    """Checkout immutable objects in a disposable local repository, never main."""
    _commit(root, commit)
    with tempfile.TemporaryDirectory(prefix="calico-inspection-", dir=Path(tempfile.gettempdir()).resolve()) as directory:
        snapshot = Path(directory) / "snapshot"
        _git(root, "clone", "--quiet", "--shared", "--no-checkout", "--", str(root), str(snapshot))
        # A local clone omits the source's remote-tracking refs. Preserve their
        # reachability in this disposable repository for historical evidence.
        for index, oid in enumerate(_git(root, "for-each-ref", "--format=%(objectname)").decode().splitlines()):
            _git(snapshot, "update-ref", "refs/inspection-source/" + str(index), oid)
        _git(snapshot, "checkout", "--quiet", "--detach", commit)
        yield snapshot


def _blob(root, commit, path):
    return _git(root, "show", commit + ":" + path)


def build_substantive_input_manifest(snapshot, *, root=_PRODUCT):
    """Hash every tracked substantive input, including code/tests/policies.

    Only the five generated freeze outputs are omitted: they cannot hash
    themselves or their own future commit. No second product allowlist exists.
    """
    root = _root(root)
    _commit(root, snapshot)
    rows = []
    with BatchBlobReader(root) as reader:
        for entry in list_tree(snapshot, root):
            if entry.path in FREEZE_OUTPUTS:
                continue
            if entry.mode not in {"100644", "100755"} or entry.obj_type != "blob":
                _fail("inspection.unsupported_input")
            gate_e._safe_text(entry.path)
            data = b"".join(reader.iter_chunks(entry.oid))
            rows.append({"path": entry.path, "sha256": hashlib.sha256(data).hexdigest()})
    if not rows:
        _fail("inspection.empty_manifest")
    return sorted(rows, key=lambda row: row["path"])


def check_migration_redaction(snapshot, *, root=_PRODUCT):
    root = _root(root)
    _commit(root, snapshot)
    try:
        record = gate_e._decode(_blob(root, snapshot, MIGRATION_RECORD))
        expected = record["successor_sha256"]
        if (set(record) != {"schema_version", "artifact", "owner", "executed_at", "disposition",
                "replacement_categories", "private_source_provenance", "supersedes", "successor_sha256"}
                or record["artifact"] != MIGRATION_PATH or expected != MIGRATION_SUCCESSOR_SHA256):
            _fail("inspection.migration_redaction_failed")
        if re.fullmatch(r"[0-9a-f]{64}", expected) is None or hashlib.sha256(
                _blob(root, snapshot, MIGRATION_PATH)).hexdigest() != expected:
            _fail("inspection.migration_redaction_failed")
        from .provenance import CORE_SPECS
        originals = {MIGRATION_ORIGINAL_SHA256, *(row[1] for row in CORE_SPECS.values())}
        hashes = {}
        with BatchBlobReader(root) as reader:
            for entry in iter_target_entries(treeish=snapshot, history_all=True, repo_dir=root):
                if entry.obj_type != "blob" or entry.mode not in {"100644", "100755"}:
                    _fail("inspection.migration_redaction_failed")
                if entry.oid not in hashes:
                    digest = hashlib.sha256()
                    for chunk in reader.iter_chunks(entry.oid):
                        digest.update(chunk)
                    hashes[entry.oid] = digest.hexdigest()
                digest = hashes[entry.oid]
                if digest in originals or (entry.path == MIGRATION_PATH and digest != expected):
                    _fail("inspection.migration_redaction_failed")
    except InspectionContractError:
        raise
    except Exception:
        _fail("inspection.migration_redaction_failed")


def _run_checks(root, snapshot):
    from tools.privacy_scan.policy import load_policy
    from tools.privacy_scan.scanner import scan
    from tools.citation_scan.scanner import check_repository, check_decision_register
    from .readme import check_readme
    from .hosted_replay import check_repository_hosted_replay
    from .provenance import validate_complete_index
    from calico_publish.allowlist import load_allowlist
    from calico_publish.inventory import check_inventory
    from calico_publish.tmdl_inventory import generate_inventory
    from calico_publish.gate import verify
    try:
        with _snapshot(root, snapshot) as tree:
            policy = load_policy(tree / "policies/publishable-tree.json")
            if scan(treeish=snapshot, history_all=True, repo_dir=root, policy=policy):
                _fail("inspection.privacy_failed")
            check_migration_redaction(snapshot, root=root)
            check_readme(tree)
            check_repository_hosted_replay(tree)
            gate_e.check_gate_e(tree / gate_e.AUTHORITY_PATH, tree / gate_e.MARKDOWN_PATH, root=tree)
            validate_complete_index(tree)
            check_repository(tree)
            check_decision_register(tree)
            allowlist = load_allowlist(tree / "contracts/publication-exports-v3.json")
            inventory = gate_e._decode(gate_e._read(tree / "powerbi/semantic-model-inventory-v1.json"))
            if inventory != generate_inventory(tree / "powerbi/Calico.SemanticModel") or check_inventory(inventory, allowlist):
                _fail("inspection.semantic_inventory_failed")
            published = gate_e.HISTORICAL_REAL_OBSERVATION["published_data_commit"]
            _commit(root, published)
            with _snapshot(root, published) as publication:
                manifest = publication / "manifest/published-manifest-v1.json"
                if hashlib.sha256(manifest.read_bytes()).hexdigest() != gate_e.HISTORICAL_REAL_OBSERVATION["published_manifest_sha256"]:
                    _fail("inspection.published_manifest_failed")
                if not verify(publication, allowlist, manifest).passed:
                    _fail("inspection.published_manifest_failed")
        return {name: "pass" for name in CHECKS}
    except InspectionContractError:
        raise
    except Exception:
        _fail("inspection.existing_gate_failed")


def build_public_inspection(snapshot, *, root=_PRODUCT, recorded_at=None):
    root = _root(root)
    _commit(root, snapshot)
    before = _refs(root)
    if _git(root, "rev-parse", "HEAD").decode().strip() != snapshot or _git(root, "status", "--porcelain", "--untracked-files=all"):
        _fail("inspection.snapshot_tree_mismatch")
    if JSON_PATH in {entry.path for entry in list_tree(snapshot, root)}:
        _fail("inspection.self_snapshot")
    document = {"schema_version": "final-inspection-checklist-v1", "inspected_snapshot": snapshot,
        "recorded_at": recorded_at or datetime.now(timezone.utc).date().isoformat(),
        "substantive_inputs": build_substantive_input_manifest(snapshot, root=root),
        "semantic_inventory_sha256": hashlib.sha256(_blob(root, snapshot, "powerbi/semantic-model-inventory-v1.json")).hexdigest(),
        "published_data_commit": gate_e.HISTORICAL_REAL_OBSERVATION["published_data_commit"],
        "published_manifest_sha256": gate_e.HISTORICAL_REAL_OBSERVATION["published_manifest_sha256"],
        "checks": _run_checks(root, snapshot), "result": "pass"}
    validate_public_inspection(document, root=root)
    if before != _refs(root) or _git(root, "status", "--porcelain", "--untracked-files=all"):
        _fail("inspection.concurrent_change")
    return document


def validate_public_inspection(document, *, root=_PRODUCT):
    root = _root(root)
    try:
        if type(document) in (str, bytes):
            document = gate_e._decode(document)
        gate_e._shape(document, gate_e._decode(gate_e._read(_PRODUCT / SCHEMA_PATH)))
        gate_e._date(document["recorded_at"])
        snapshot = _commit(root, document["inspected_snapshot"])
        if JSON_PATH in {entry.path for entry in list_tree(snapshot, root)}:
            _fail("inspection.self_snapshot")
        if document["substantive_inputs"] != build_substantive_input_manifest(snapshot, root=root):
            _fail("inspection.input_manifest_drift")
        expected = hashlib.sha256(_blob(root, snapshot, "powerbi/semantic-model-inventory-v1.json")).hexdigest()
        if (document["semantic_inventory_sha256"] != expected or document["checks"] != {name: "pass" for name in CHECKS}
                or document["published_data_commit"] != gate_e.HISTORICAL_REAL_OBSERVATION["published_data_commit"]
                or document["published_manifest_sha256"] != gate_e.HISTORICAL_REAL_OBSERVATION["published_manifest_sha256"]):
            _fail("inspection.identity_or_check_mismatch")
        return document
    except InspectionContractError:
        raise
    except Exception:
        _fail()


def render_public_inspection(document, *, root=_PRODUCT):
    document = validate_public_inspection(document, root=root)
    parts = ["# Final public inspection checklist", "", "Inspected prior immutable snapshot: `" + document["inspected_snapshot"] + "`.",
        "Recorded: " + document["recorded_at"] + ". Results describe that earlier snapshot; live origin and later CI observations are sealed separately.",
        "", "| Check | Result |", "| --- | --- |"]
    parts.extend("| " + key + " | " + value + " |" for key, value in document["checks"].items())
    parts.extend(["", "Named migration successor: [redacted investigation](../ag-registry-migration-2026-08.md), bound by its [redaction record](../redactions/ag-registry-migration-2026-08.json).",
        "Published-data commit: `" + document["published_data_commit"] + "`; manifest SHA-256: `" + document["published_manifest_sha256"] + "`.",
        "Semantic inventory SHA-256: `" + document["semantic_inventory_sha256"] + "`.", "", "## Substantive inputs", "",
        "| Public input | SHA-256 |", "| --- | --- |"])
    parts.extend("| [" + row["path"] + "](../../" + row["path"] + ") | `" + row["sha256"] + "` |" for row in document["substantive_inputs"])
    return "\n".join(parts) + "\n"


def _report_changes(base, final):
    """Validate exact canonical ledger bytes and exact generated regions."""
    from .readme import generate_readme, report_block
    try:
        old_raw, new_raw = gate_e._read(base / gate_e.AUTHORITY_PATH), gate_e._read(final / gate_e.AUTHORITY_PATH)
        old, new = gate_e._decode(old_raw), gate_e._decode(new_raw)
        if gate_e.validate_report_state(old) is not None or gate_e.validate_report_state(new) is None:
            _fail("inspection.report_transition_required")
        expected = {**old, "report": new["report"]}
        if old_raw != gate_e._encode(old) or new_raw != gate_e._encode(expected):
            _fail("inspection.non_report_ledger_change")
        previous, current = (gate_e._read(tree / "README.md").decode("utf-8") for tree in (base, final))
        for content in (previous, current):
            if any(content.count("<!-- calico:report:" + edge + " -->") != 1 for edge in ("start", "end")):
                _fail("inspection.report_marker_drift")
        before_block, after_block = report_block(base), report_block(final)
        if previous != generate_readme(base) or current != generate_readme(final) or previous.replace(before_block, after_block, 1) != current:
            _fail("inspection.non_report_readme_change")
        for tree, document in ((base, old), (final, new)):
            if gate_e._read(tree / gate_e.MARKDOWN_PATH) != gate_e.render_gate_e_markdown(document, root=tree).encode():
                _fail("inspection.generated_report_drift")
    except InspectionContractError:
        raise
    except Exception:
        _fail("inspection.invalid_report_change")


def check_final_url_diff(candidate_x, final_commit=None, *, root=_PRODUCT):
    """Exact commits, or the current worktree against X before staging."""
    root = _root(root)
    _commit(root, candidate_x)
    before = _refs(root)
    before_diff = _git(root, "diff", "--binary", candidate_x) if final_commit is None else None
    if final_commit is not None:
        _commit(root, final_commit)
        _git(root, "merge-base", "--is-ancestor", candidate_x, final_commit)
        changed = set(_git(root, "diff", "--name-only", candidate_x, final_commit).decode().splitlines())
    else:
        if _git(root, "rev-parse", "HEAD").decode().strip() != candidate_x:
            _fail("inspection.candidate_head_mismatch")
        changed = set(_git(root, "diff", "--name-only", candidate_x).decode().splitlines())
        changed.update(_git(root, "ls-files", "--others", "--exclude-standard").decode().splitlines())
    if not changed:
        if before != _refs(root):
            _fail("inspection.concurrent_change")
        return {"result": "no_change"}
    if changed != REPORT_PATHS:
        _fail("inspection.unexpected_final_paths")
    with _snapshot(root, candidate_x) as base:
        if final_commit is None:
            if any((root / name).is_symlink() or not (root / name).is_file() or
                   (root / name).stat().st_mode & 0o111 != (base / name).stat().st_mode & 0o111 for name in changed):
                _fail("inspection.unexpected_final_mode")
            before_bytes = {name: gate_e._read(root / name) for name in changed}
            _report_changes(base, root)
            if before_bytes != {name: gate_e._read(root / name) for name in changed}:
                _fail("inspection.concurrent_change")
        else:
            modes = lambda sha: {e.path: e.mode for e in list_tree(sha, root)}
            old_modes, new_modes = modes(candidate_x), modes(final_commit)
            if any(new_modes[name] != old_modes[name] or new_modes[name] not in {"100644", "100755"} for name in changed):
                _fail("inspection.unexpected_final_mode")
            with _snapshot(root, final_commit) as final:
                _report_changes(base, final)
    if (before != _refs(root) or final_commit is None and (before_diff != _git(root, "diff", "--binary", candidate_x)
            or _git(root, "ls-files", "--others", "--exclude-standard"))):
        _fail("inspection.concurrent_change")
    return {"result": "pass"}


def generate_public_inspection(snapshot, *, root=_PRODUCT):
    root = _root(root)
    document = build_public_inspection(snapshot, root=root)
    rendered = render_public_inspection(document, root=root)
    if _git(root, "rev-parse", "HEAD").decode().strip() != snapshot or _git(root, "status", "--porcelain", "--untracked-files=all"):
        _fail("inspection.concurrent_change")
    # Existing common-directory exchange is one atomic visibility operation.
    gate_e._generate_common_pair(document, root / JSON_PATH, root / MARKDOWN_PATH, rendered)
    return document


def check_public_inspection(*, root=_PRODUCT):
    root = _root(root)
    before = _refs(root)
    raw = gate_e._read(root / JSON_PATH)
    document = validate_public_inspection(raw, root=root)
    if raw != gate_e._encode(document) or gate_e._read(root / MARKDOWN_PATH) != render_public_inspection(document, root=root).encode():
        _fail("inspection.generated_drift")
    current = _git(root, "rev-parse", "HEAD").decode().strip()
    dirty = set(_git(root, "diff", "--name-only", current).decode().splitlines())
    dirty.update(_git(root, "ls-files", "--others", "--exclude-standard").decode().splitlines())
    if dirty - FREEZE_OUTPUTS:
        if dirty != REPORT_PATHS:
            _fail("inspection.snapshot_tree_mismatch")
        check_final_url_diff(current, root=root)
    _git(root, "merge-base", "--is-ancestor", document["inspected_snapshot"], current)
    prior = {row["path"]: row["sha256"] for row in document["substantive_inputs"]}
    now = {row["path"]: row["sha256"] for row in build_substantive_input_manifest(current, root=root)}
    changes = {key for key in prior.keys() | now.keys() if prior.get(key) != now.get(key)}
    if changes:
        if changes != REPORT_PATHS:
            _fail("inspection.substantive_input_change")
        with _snapshot(root, document["inspected_snapshot"]) as base, _snapshot(root, current) as final:
            _report_changes(base, final)
    _run_checks(root, current)
    from tools.citation_scan.scanner import check_repository
    check_repository(root)
    if before != _refs(root) or raw != gate_e._read(root / JSON_PATH):
        _fail("inspection.concurrent_change")
    return document
