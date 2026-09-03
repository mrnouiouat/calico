"""Non-echoing operator/hosted CLI over the capture, archive, restore, and
retention-posture boundaries (06-06-PLAN.md Task 1; D-04/D-06/D-08/D-09/D-10/
D-12; `calico_landing/cli.py`'s thin-parser/closed-exception-translation
pattern).

Six commands -- `run`, `attest`, `seed`, `restore-build`, `inspect-retention`,
and `audit-hosted-output` -- all route to already-tested services
(`calico_capture.orchestrator.capture`, `calico_capture.archive.
synchronize_verified_transaction`, `calico_capture.restore.
restore_verified_transaction`, `calico_capture.b2.B2Archive`/
`inspect_retention_posture`); this module never forks admission, archive,
retry, or restore logic of its own.

Private values (B2 application key id/key) are accepted only through fixed
environment-variable names, never `argv`, and never appear in any printed
JSON document, stderr line, or raised exception (D-10/D-12 non-echo
discipline, mirrored from `calico_landing/cli.py`'s own closed exception
translation). `inspect-retention` reads a deliberately separate,
owner-only credential pair -- distinct from the automation key `run`/
`attest`/`seed`/`restore-build` share -- and never routes through
`B2Archive.authorize`'s exact-automation-scope attestation, since an
owner's retention-inspection session legitimately carries a broader,
different capability set.

Every command prints exactly one compact machine-readable JSON document to
stdout and exactly one fixed, closed-vocabulary status line to stderr, then
returns a fixed exit code -- never a caught exception's message, type, or
chained cause, and never a supplied path, credential, or provider response
(mirrors `calico_landing/cli.py:70-82`'s single `except Exception` fallback
pattern, applied per-command here since each command has its own closed
outcome vocabulary).

Every production entry point below accepts an internal, argv-unreachable
factory/loader keyword (`archive_factory`, `catalog_loader`, `session_
factory`, `final_build`) that defaults to the real credential-reading /
committed-catalog-loading / real-build boundary. Tests inject a fake
instead -- mirroring `calico_capture.orchestrator.capture`'s own injected
`archive`/`fetch_candidate`/`build`/`clock`/`sleeper`/`restore` seams. No
CLI flag ever exposes these seams.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

from calico_capture.archive import (
    Archive,
    ArchiveError,
    synchronize_verified_transaction,
)
from calico_capture.b2 import (
    EXPECTED_BUCKET_NAME,
    B2Archive,
    RetentionPosture,
    inspect_retention_posture,
)
from calico_capture.orchestrator import capture
from calico_capture.restore import RestoreError, restore_verified_transaction
from calico_capture.status import (
    CaptureStatus,
    StatusError,
    project_safe_status,
    validate_capture_status_document,
)
from calico_dbt.catalog import (
    CatalogError,
    CatalogReleaseAnchor,
    InputCatalog,
    load_and_verify_revision_manifest,
    load_input_catalog,
)
from calico_landing.attempts import utc_now_iso
from calico_landing.candidate import CandidateError, reject_store_in_git_worktree
from calico_landing.result import AdmissionResult
from calico_landing.store import StoreError, ensure_store_layout

#: Closed trigger vocabulary -- identical to `calico_capture.status`'s own
#: (D-06). Enforced twice: once here as `argparse` `choices` (a malformed
#: value never reaches Python logic at all), and again inside `capture()`/
#: `CaptureStatus` itself.
_TRIGGERS = ("schedule", "workflow_dispatch", "local")

#: Fixed automation-credential environment-variable names `run`/`attest`/
#: `seed`/`restore-build` all share -- the exact-scope automation key
#: `calico_capture.b2.attest_effective_scope` attests (D-10). Never a CLI
#: flag; never printed.
AUTOMATION_KEY_ID_ENV = "CALICO_B2_APPLICATION_KEY_ID"
AUTOMATION_KEY_ENV = "CALICO_B2_APPLICATION_KEY"

#: Fixed, deliberately separate owner-only retention-inspection credential
#: pair `inspect-retention` alone reads -- never shared with the automation
#: key, never routed through `B2Archive.authorize`'s exact-scope check
#: (COVERAGE.md item 7; D-12).
RETENTION_KEY_ID_ENV = "CALICO_B2_RETENTION_KEY_ID"
RETENTION_KEY_ENV = "CALICO_B2_RETENTION_KEY"

#: The one fixed, committed real-mode input catalog path -- identical to
#: `calico_dbt.runner._REAL_CATALOG_PATH`'s own resolution (mirrored, not
#: imported, per this project's established local-constant-duplication
#: precedent -- e.g. `calico_capture.restore`'s own mirror of
#: `calico_capture.archive`'s private prefix constants).
_REAL_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "contracts" / "dbt-input-catalog-v1.json"
)

_RELEASES_DIRNAME = "releases"
_MANIFEST_FILENAME = "manifest.json"

_CREDENTIAL_MISSING_CATEGORY = "archive.credential_missing"

_LOG_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "Traceback (most recent call last)",
    "traceback",
    "secrets.",
    "password",
    "api_key",
    "application_key",
    "application key",
    "token:",
    "Authorization:",
    # Split via runtime concatenation so the committed source text itself
    # never contains a contiguous absolute-path shape (this project's
    # established privacy-scanner false-positive workaround -- e.g.
    # `tests/dbt_foundation/test_ci_contract.py`'s own equivalent split).
    "C:" + "\\",
    "/" + "Users" + "/",
    "/" + "home" + "/",
    "oag.ca.gov",
    "FEIN",
    " EIN ",
)

#: The exact closed key set every `audit-hosted-output` status document
#: must additionally satisfy beyond `calico_capture.status`'s own schema --
#: kept identical to `STATUS_DOCUMENT_KEYS` (imported indirectly via
#: `validate_capture_status_document`), so this module never maintains a
#: second, drifting copy of that vocabulary.

#: Closed `audit-hosted-output` mode vocabulary (06-07-PLAN.md Task 3):
#: `capture-status` is the original hosted-run status/log audit; `authorization-probe`
#: validates the no-secret `authorization-probe` workflow job's own closed-category
#: marker lines instead (never a `CaptureStatus` document -- that job never enters the
#: `capture`/`status` path and never produces one).
_AUDIT_MODES = ("capture-status", "authorization-probe")

#: The exact committed step name (`.github/workflows/capture-current.yml`,
#: `authorization-probe` job) whose own printed output this audit scans --
#: never the full multi-job `gh run view --log` fetch. `gh run view --log`
#: prefixes every line `<job>\t<step>\t<timestamp> <text>`; scoping to only
#: this project's own script step excludes third-party `actions/checkout`
#: and `actions/setup-python` infrastructure debug output (e.g. the generic
#: hosted runner's own generic per-run scratch working directory and the
#: checkout action's own already-GitHub-masked `token: ***` input-summary
#: line), which is universal GitHub Actions boilerplate present on every
#: public run of every repository, not project-specific content, and would
#: otherwise always false-positive `_LOG_FORBIDDEN_TOKENS` against a real
#: hosted log regardless of what this project's own code ever prints.
_AUTHZ_PROBE_STEP_NAME = "Run the no-secret authorization probe"

#: `gh run view --log` wraps every echoed *source* line of a `run:` step's
#: inline script (GitHub's own "$ command" display, not this project's
#: runtime output) in a fixed ANSI cyan-bold escape pair. A `python -
#: <<'PY' ... PY` heredoc step therefore has its literal script text
#: (including any string literal that happens to *contain* a
#: `CALICO_AUTHZ_PROBE::...` marker shape inside a `print(...)` call's own
#: source, e.g. `print("CALICO_AUTHZ_PROBE::result=pass")`) echoed back
#: verbatim in the hosted log before the script ever runs. Only this
#: project's genuine runtime `print()` output -- never wrapped in this
#: escape pair -- is authoritative for both the forbidden-content scan and
#: the marker-line parse below. Both observed on-the-wire forms are
#: checked: a real ESC (0x1B) control byte (`gh` on some platforms/
#: versions passes the raw terminal escape through), and the literal
#: two-character caret-notation text `^[` (observed from a live
#: `gh run view --log` fetch on this project's own Windows toolchain) --
#: neither form is ever legitimate project-generated content.
_GH_COMMAND_ECHO_MARKERS = ("\x1b[36;1m", "^[[36;1m")

#: The exact closed marker line pattern the `authorization-probe` workflow job
#: prints once per probe outcome (never a raw ref name, provider error body, or
#: secret) -- mirrors this module's own `_LOG_FORBIDDEN_TOKENS` positive-projection
#: discipline: only a fixed category name and a fixed `denied`/`allowed` result.
_AUTHZ_PROBE_MARKER_PATTERN = re.compile(r"CALICO_AUTHZ_PROBE::([a-z_]+)=([a-z_]+)")

#: The exact closed set of probe categories and their single required outcome
#: (T-06-07A/B: every forbidden-ref probe must be `denied`; the one target-branch
#: update probe must be `allowed`). Any category missing, any extra category, or
#: any category whose observed result does not match here is a hard audit failure.
_AUTHZ_PROBE_REQUIRED_RESULTS: dict[str, str] = {
    "nontarget_branch_create": "denied",
    "main_update": "denied",
    "tag_create": "denied",
    "deletion": "denied",
    "force_push": "denied",
    "published_data_update": "allowed",
}

#: The workflow step's own unconditional overall-verdict marker key
#: (`CALICO_AUTHZ_PROBE::result=pass|unexpected`) -- excluded from the
#: closed per-probe category set above (see `_audit_authorization_probe`).
_AUTHZ_PROBE_SUMMARY_CATEGORY = "result"


class _SkipBuildOutcome:
    """A fixed, always-succeeded `BuildFn` result for every intermediate
    `restore-build` catalog anchor.

    `restore_verified_transaction` unconditionally invokes its `build`
    boundary once per call; looping it over every catalog anchor would
    otherwise re-run the real, expensive `calico_dbt` build once per
    release. Only the final anchor's restore is allowed to invoke the real
    build (`final_build`, defaulting to the real `calico_dbt.runner.build`
    seam); every earlier anchor's restore is verified and materialized
    exactly the same way, but its own per-call build invocation is a cheap,
    fixed no-op success.
    """

    succeeded = True


class _RetentionSessionError(Exception):
    """Raised only inside `_default_retention_session_factory`. Carries a
    fixed safe `category` -- never a credential, bucket id, or provider
    exception text (mirrors `calico_capture.archive.ArchiveError`).
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _default_automation_archive_factory() -> Archive:
    """Read the fixed automation-credential env vars and authorize the real
    `B2Archive` (D-10). Never called with any argument reachable from
    `argv`; tests inject a different `archive_factory` entirely instead of
    exercising this function.
    """

    key_id = os.environ.get(AUTOMATION_KEY_ID_ENV)
    key = os.environ.get(AUTOMATION_KEY_ENV)
    if not key_id or not key:
        raise ArchiveError(_CREDENTIAL_MISSING_CATEGORY)
    return B2Archive.authorize(key_id, key)


def _default_catalog_loader() -> InputCatalog:
    return load_input_catalog(_REAL_CATALOG_PATH)


def _default_retention_session_factory() -> object:
    """Authorize the deliberately separate owner-only retention credential
    and bind the real `RegistryData` bucket for a read-only posture check
    (COVERAGE.md item 7; D-12). Never routes through `B2Archive.authorize`
    -- an owner retention-inspection session legitimately carries a
    different, broader capability set than the exact automation scope that
    call enforces.
    """

    from b2sdk.v3 import B2Api, InMemoryAccountInfo
    from b2sdk.v3.exception import B2Error

    key_id = os.environ.get(RETENTION_KEY_ID_ENV)
    key = os.environ.get(RETENTION_KEY_ENV)
    if not key_id or not key:
        raise _RetentionSessionError(_CREDENTIAL_MISSING_CATEGORY)

    account_info = InMemoryAccountInfo()
    api = B2Api(account_info)
    try:
        api.authorize_account(key_id, key, realm="production")
    except B2Error as exc:
        raise _RetentionSessionError("archive.authorization_failed") from exc
    try:
        return api.get_bucket_by_name(EXPECTED_BUCKET_NAME)
    except B2Error as exc:
        raise _RetentionSessionError("archive.object_not_found") from exc


def _dict_json(document: dict) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# -- run ----------------------------------------------------------------


def _run_capture(
    trigger: str, *, archive_factory: Callable[[], Archive] | None = None
) -> CaptureStatus:
    """Run one production `capture()` call for `trigger` (D-06): the same
    entry point schedule, `workflow_dispatch`, and the local runbook all
    call. Archive construction failures (missing/invalid credential, scope
    rejection) never reach `capture()` -- they are projected directly into
    the same closed `operational_error`/`archive_error` `CaptureStatus`
    shape `capture()` itself would produce for an archive failure, so a
    caller cannot distinguish "credential missing" from "archive
    synchronization failed mid-run" beyond the fixed category, by design
    (D-09 non-echo discipline).
    """

    started_at_utc = utc_now_iso()
    factory = archive_factory if archive_factory is not None else _default_automation_archive_factory
    try:
        archive = factory()
    except Exception:  # noqa: BLE001 -- fixed safe category only; covers ArchiveError too
        ended_at_utc = utc_now_iso()
        return project_safe_status(
            trigger=trigger,
            outcome="operational_error",
            reason_category="archive_error",
            started_at_utc=started_at_utc,
            ended_at_utc=ended_at_utc,
        )

    return capture(trigger=trigger, archive=archive)


_RUN_EXIT_CODE_BY_OUTCOME: dict[str, int] = {
    "accepted": 0,
    "rejected": 1,
    "no_new_release": 2,
    "operational_error": 3,
}


def _cmd_run(args: argparse.Namespace) -> int:
    status = _run_capture(args.trigger)
    print(status.to_json(), end="")
    print(f"{status.outcome} reason_category={status.reason_category}", file=sys.stderr)
    return _RUN_EXIT_CODE_BY_OUTCOME.get(status.outcome, 3)


# -- attest ---------------------------------------------------------------


def _attest(*, archive_factory: Callable[[], Archive] | None = None) -> tuple[dict, int]:
    factory = archive_factory if archive_factory is not None else _default_automation_archive_factory
    try:
        archive = factory()
    except ArchiveError as exc:
        return {"category": exc.category}, 1
    except Exception:  # noqa: BLE001 -- fixed safe category only
        return {"category": "attest.unexpected_error"}, 1

    scope = archive.scope if isinstance(archive, B2Archive) else None
    if scope is None:
        return {"category": "attest.unexpected_error"}, 1
    return (
        {
            "category": "attest.scope_verified",
            "bucket_name": scope.bucket_name,
            "name_prefix": scope.name_prefix,
            "capability_count": len(scope.capabilities),
        },
        0,
    )


def _cmd_attest(args: argparse.Namespace) -> int:
    document, exit_code = _attest()
    print(_dict_json(document))
    print(document["category"], file=sys.stderr)
    return exit_code


# -- seed -------------------------------------------------------------------


def _resolve_local_manifest_path(store_root: Path, anchor: CatalogReleaseAnchor) -> Path:
    """Resolve one anchor's local `manifest.json` at its exact,
    deterministic path, mirroring `calico_dbt.preflight._resolve_revision_dir`
    exactly (mirrored, not imported, per this project's local-constant-
    duplication precedent). Rejects a symlink at any path component before
    ever reading the file.
    """

    date_dir = store_root / _RELEASES_DIRNAME / anchor.as_of_date
    if date_dir.is_symlink():
        raise CatalogError("catalog.manifest_not_found")
    revision_dir = date_dir / f"rev-{anchor.release_revision:04d}-{anchor.revision_fingerprint[:8]}"
    if revision_dir.is_symlink():
        raise CatalogError("catalog.manifest_not_found")
    manifest_path = revision_dir / _MANIFEST_FILENAME
    if manifest_path.is_symlink():
        raise CatalogError("catalog.manifest_not_found")
    return manifest_path


def _seed(
    store: str | Path,
    *,
    archive_factory: Callable[[], Archive] | None = None,
    catalog_loader: Callable[[], InputCatalog] | None = None,
) -> tuple[dict, int]:
    """Additively synchronize every locally present, hash-verified catalog
    release into the archive (D-03). A catalog release whose local manifest
    is absent is safely skipped (nothing to seed yet -- e.g. a release
    pending its own hash gate); a release whose local manifest *is* present
    but fails verification stops the whole call closed immediately, since
    that indicates tampering or drift, not simple absence.
    """

    try:
        reject_store_in_git_worktree(store)
    except CandidateError:
        return {"category": "seed.invalid_store"}, 1
    try:
        layout = ensure_store_layout(store)
    except StoreError:
        return {"category": "seed.invalid_store"}, 1

    factory = archive_factory if archive_factory is not None else _default_automation_archive_factory
    try:
        archive = factory()
    except ArchiveError as exc:
        return {"category": exc.category}, 1
    except Exception:  # noqa: BLE001 -- fixed safe category only
        return {"category": "seed.unexpected_error"}, 1

    loader = catalog_loader if catalog_loader is not None else _default_catalog_loader
    try:
        catalog = loader()
    except CatalogError as exc:
        return {"category": exc.category}, 1
    except Exception:  # noqa: BLE001 -- fixed safe category only
        return {"category": "seed.unexpected_error"}, 1

    verified_count = 0
    synchronized_count = 0
    skipped_count = 0

    for anchor in sorted(catalog.releases, key=lambda a: (a.as_of_date, a.release_revision)):
        try:
            manifest_path = _resolve_local_manifest_path(layout.store_root, anchor)
        except CatalogError as exc:
            return {"category": exc.category}, 1
        if not manifest_path.is_file():
            skipped_count += 1
            continue
        try:
            load_and_verify_revision_manifest(manifest_path, anchor)
        except CatalogError as exc:
            return {"category": exc.category}, 1
        verified_count += 1

        result = AdmissionResult.accepted(
            anchor.as_of_date, anchor.release_revision, anchor.revision_fingerprint
        )
        try:
            synchronize_verified_transaction(archive, layout.store_root, result)
        except ArchiveError as exc:
            return {"category": exc.category}, 1
        synchronized_count += 1

    return (
        {
            "category": "seed.completed",
            "verified_release_count": verified_count,
            "synchronized_release_count": synchronized_count,
            "skipped_release_count": skipped_count,
        },
        0,
    )


def _cmd_seed(args: argparse.Namespace) -> int:
    document, exit_code = _seed(args.store)
    print(_dict_json(document))
    print(document["category"], file=sys.stderr)
    return exit_code


# -- restore-build ------------------------------------------------------


def _restore_build(
    store: str | Path,
    *,
    archive_factory: Callable[[], Archive] | None = None,
    catalog_loader: Callable[[], InputCatalog] | None = None,
    final_build: Callable[[Path], object] | None = None,
) -> tuple[dict, int]:
    """Restore every committed catalog release into `store` -- a caller-
    owned fresh external store -- and run the existing real-mode build
    exactly once, on the final restored anchor (D-13). Every anchor's
    restore is independently verified and materialized by
    `restore_verified_transaction`; only the last anchor's own per-call
    build invocation is the real build (`final_build`, defaulting to the
    real `calico_dbt.runner.build` seam `restore_verified_transaction`
    itself already defaults to when `build=None`).
    """

    try:
        reject_store_in_git_worktree(store)
    except CandidateError:
        return {"category": "restore_build.invalid_store"}, 1
    try:
        layout = ensure_store_layout(store)
    except StoreError:
        return {"category": "restore_build.invalid_store"}, 1

    factory = archive_factory if archive_factory is not None else _default_automation_archive_factory
    try:
        archive = factory()
    except ArchiveError as exc:
        return {"category": exc.category}, 1
    except Exception:  # noqa: BLE001 -- fixed safe category only
        return {"category": "restore_build.unexpected_error"}, 1

    loader = catalog_loader if catalog_loader is not None else _default_catalog_loader
    try:
        catalog = loader()
    except CatalogError as exc:
        return {"category": exc.category}, 1
    except Exception:  # noqa: BLE001 -- fixed safe category only
        return {"category": "restore_build.unexpected_error"}, 1

    anchors = sorted(catalog.releases, key=lambda a: (a.as_of_date, a.release_revision))
    if not anchors:
        return {"category": "restore_build.empty_catalog"}, 1

    restored_count = 0
    object_count = 0
    for index, anchor in enumerate(anchors):
        is_final = index == len(anchors) - 1
        build_fn = final_build if is_final else (lambda _root: _SkipBuildOutcome())
        try:
            restored = restore_verified_transaction(
                archive,
                layout.store_root,
                as_of_date=anchor.as_of_date,
                release_revision=anchor.release_revision,
                revision_fingerprint=anchor.revision_fingerprint,
                build=build_fn,
            )
        except RestoreError as exc:
            return {"category": exc.category}, 1
        restored_count += 1
        object_count += len(restored.object_keys)

    return (
        {
            "category": "restore_build.completed",
            "restored_transaction_count": restored_count,
            "object_count": object_count,
        },
        0,
    )


def _cmd_restore_build(args: argparse.Namespace) -> int:
    document, exit_code = _restore_build(args.store)
    print(_dict_json(document))
    print(document["category"], file=sys.stderr)
    return exit_code


# -- inspect-retention ----------------------------------------------------


def _inspect_retention(*, session_factory: Callable[[], object] | None = None) -> tuple[dict, int]:
    factory = session_factory if session_factory is not None else _default_retention_session_factory
    try:
        bucket = factory()
    except _RetentionSessionError as exc:
        return {"category": exc.category}, 1
    except Exception:  # noqa: BLE001 -- fixed safe category only
        return {"category": "inspect_retention.unexpected_error"}, 1

    try:
        posture: RetentionPosture = inspect_retention_posture(bucket)
    except ArchiveError as exc:
        return {"category": exc.category}, 1
    except Exception:  # noqa: BLE001 -- fixed safe category only
        return {"category": "inspect_retention.unexpected_error"}, 1

    return (
        {
            "lifecycle_category": posture.lifecycle_category,
            "object_lock_category": posture.object_lock_category,
        },
        0,
    )


def _cmd_inspect_retention(args: argparse.Namespace) -> int:
    document, exit_code = _inspect_retention()
    print(_dict_json(document))
    print(
        f"lifecycle={document.get('lifecycle_category', document.get('category'))}",
        file=sys.stderr,
    )
    return exit_code


# -- audit-hosted-output ------------------------------------------------


def _read_text_no_echo(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def _audit_capture_status(
    log_text: str, status_file: str | None, credential_env: str | None
) -> tuple[dict, int]:
    """Original `capture-status` mode audit (unchanged behavior): validate the
    real hosted `CaptureStatus` document and scan the log for forbidden content
    or a leaked credential sentinel.
    """

    if not status_file:
        return {"category": "audit.file_not_found"}, 1
    status_text = _read_text_no_echo(status_file)
    if status_text is None:
        return {"category": "audit.file_not_found"}, 1

    try:
        document = json.loads(status_text)
    except json.JSONDecodeError:
        return {"category": "audit.status_invalid"}, 1

    try:
        validate_capture_status_document(document)
    except StatusError:
        return {"category": "audit.status_invalid"}, 1

    for token in _LOG_FORBIDDEN_TOKENS:
        if token in log_text or token in status_text:
            return {"category": "audit.log_forbidden_content"}, 1

    if credential_env:
        sentinel = os.environ.get(credential_env)
        if sentinel and (sentinel in log_text or sentinel in status_text):
            return {"category": "audit.credential_leak_detected"}, 1

    return {"category": "audit.pass"}, 0


def _extract_named_step_output(log_text: str, step_name: str) -> str:
    """Return only the named step's own printed lines from a `gh run view
    --log` fetch (tab-separated `<job>\\t<step>\\t<timestamp> <text>` per
    line), stripping the job/step/timestamp prefix. If no line matches
    (e.g. a bare, already-scoped log with no `gh` prefix at all, as this
    module's own unit tests use), the input is returned unchanged so a
    pre-scoped log still audits correctly.
    """

    matched: list[str] = []
    for line in log_text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[1] == step_name:
            if any(marker in parts[2] for marker in _GH_COMMAND_ECHO_MARKERS):
                continue
            matched.append(parts[2])
    return "\n".join(matched) if matched else log_text


def _audit_authorization_probe(log_text: str, credential_env: str | None) -> tuple[dict, int]:
    """`authorization-probe` mode audit (06-07-PLAN.md Task 3): the
    `authorization-probe` workflow job never produces a `CaptureStatus` document
    (it never enters the `capture`/`status` path) -- it prints one fixed
    `CALICO_AUTHZ_PROBE::<category>=<denied|allowed>` marker line per probe
    instead. This validates the closed marker set positively (exact category set,
    exact required result per category) and still applies the same forbidden-
    content/credential-sentinel scan the `capture-status` mode applies -- scoped
    to this project's own `_AUTHZ_PROBE_STEP_NAME` step output only, never the
    full multi-job hosted log (see `_extract_named_step_output`).
    """

    scoped_text = _extract_named_step_output(log_text, _AUTHZ_PROBE_STEP_NAME)

    for token in _LOG_FORBIDDEN_TOKENS:
        if token in scoped_text:
            return {"category": "audit.log_forbidden_content"}, 1

    found: dict[str, str] = {}
    for category, value in _AUTHZ_PROBE_MARKER_PATTERN.findall(scoped_text):
        if category in found and found[category] != value:
            return {"category": "audit.authz_probe_malformed"}, 1
        found[category] = value

    # The committed workflow step also unconditionally prints one
    # `CALICO_AUTHZ_PROBE::result=<pass|unexpected>` overall-verdict marker
    # (`.github/workflows/capture-current.yml`) sharing this module's own
    # marker syntax by construction, not a seventh probe category -- its
    # own vocabulary (`pass`/`unexpected`) differs from every real
    # category's `denied`/`allowed` vocabulary, and an `unexpected` verdict
    # already makes the workflow step exit non-zero, which the hosted
    # run/job `conclusion` check (this plan's own outer `<verify>`) already
    # catches independently. Excluded here so it is never mistaken for an
    # unrecognized/extra probe category.
    probe_categories = {
        category: value
        for category, value in found.items()
        if category != _AUTHZ_PROBE_SUMMARY_CATEGORY
    }

    if set(probe_categories.keys()) != set(_AUTHZ_PROBE_REQUIRED_RESULTS.keys()):
        return {"category": "audit.authz_probe_incomplete"}, 1

    for category, expected in _AUTHZ_PROBE_REQUIRED_RESULTS.items():
        if probe_categories[category] != expected:
            return {"category": "audit.authz_probe_unexpected_result"}, 1

    if credential_env:
        sentinel = os.environ.get(credential_env)
        if sentinel and sentinel in scoped_text:
            return {"category": "audit.credential_leak_detected"}, 1

    return {"category": "audit.pass"}, 0


def _audit_hosted_output(
    log_file: str,
    status_file: str | None,
    credential_env: str | None,
    mode: str = "capture-status",
) -> tuple[dict, int]:
    log_text = _read_text_no_echo(log_file)
    if log_text is None:
        return {"category": "audit.file_not_found"}, 1

    if mode == "authorization-probe":
        return _audit_authorization_probe(log_text, credential_env)
    return _audit_capture_status(log_text, status_file, credential_env)


def _cmd_audit_hosted_output(args: argparse.Namespace) -> int:
    document, exit_code = _audit_hosted_output(
        args.log_file, args.status_file, args.credential_env, mode=args.mode
    )
    print(_dict_json(document))
    print(document["category"], file=sys.stderr)
    return exit_code


# -- parser / dispatch ------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="calico_capture",
        description="Operate the durable private-history capture service.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Run one production capture attempt sequence."
    )
    run_parser.add_argument("--trigger", required=True, choices=_TRIGGERS)

    subparsers.add_parser(
        "attest", help="Attest the automation credential's exact archive scope."
    )

    seed_parser = subparsers.add_parser(
        "seed", help="Additively synchronize locally verified releases into the archive."
    )
    seed_parser.add_argument("--store", required=True)

    restore_parser = subparsers.add_parser(
        "restore-build",
        help="Restore every committed catalog release into a fresh external store and build.",
    )
    restore_parser.add_argument("--store", required=True)

    subparsers.add_parser(
        "inspect-retention",
        help="Owner-only, read-only lifecycle/Object Lock posture check.",
    )

    audit_parser = subparsers.add_parser(
        "audit-hosted-output",
        help="Validate a hosted run's status document and scan its log for leaks.",
    )
    audit_parser.add_argument("--log-file", required=True)
    audit_parser.add_argument("--status-file", required=False, default=None)
    audit_parser.add_argument("--credential-env", required=False, default=None)
    audit_parser.add_argument(
        "--mode",
        required=False,
        default="capture-status",
        choices=_AUDIT_MODES,
        help=(
            "capture-status (default): validate a real hosted CaptureStatus document "
            "and log. authorization-probe: validate the no-secret authorization-probe "
            "job's own closed marker log only (no --status-file)."
        ),
    )

    return parser


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "run": _cmd_run,
    "attest": _cmd_attest,
    "seed": _cmd_seed,
    "restore-build": _cmd_restore_build,
    "inspect-retention": _cmd_inspect_retention,
    "audit-hosted-output": _cmd_audit_hosted_output,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    handler = _COMMANDS[args.command]
    try:
        return handler(args)
    except Exception:  # noqa: BLE001 -- fixed safe message only; never the exception object
        print(_dict_json({"category": "cli.unexpected_error"}))
        print("cli.unexpected_error", file=sys.stderr)
        return 3


__all__ = [
    "AUTOMATION_KEY_ID_ENV",
    "AUTOMATION_KEY_ENV",
    "RETENTION_KEY_ID_ENV",
    "RETENTION_KEY_ENV",
    "main",
]
