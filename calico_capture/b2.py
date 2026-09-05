"""Narrow `b2sdk.v3` adapter implementing the provider-neutral `Archive`
protocol against Backblaze B2 (06-05-PLAN.md; COVERAGE.md items 1-2, 4-7;
06-CONTEXT.md D-02/D-10/D-12).

`B2Archive` is the only production caller of `b2sdk`. It authorizes in
memory (`b2sdk.v3.InMemoryAccountInfo` -- no account state ever touches
disk), then fails closed before any list/read/write unless the effective
`allowed` contract returned by authorization proves *exactly* the minimum
required scope: the single private `RegistryData` bucket, the exact
`archive/v1/` name prefix, and the exact three file capabilities
`listFiles`, `readFiles`, `writeFiles` -- nothing broader, nothing fewer
(D-10). This is the "dedicated automation storage credential" contract:
an owner/bootstrap key with no bucket restriction, an extra capability, a
persisted (non-in-memory) account-info implementation, or any prefix/
bucket mismatch is rejected by `attest_effective_scope` before it ever
reaches provider I/O.

`B2Archive` never resolves its bucket via an account-wide `listBuckets`
call (COVERAGE.md item 2 / explicit opt-out 8): it binds the SDK `Bucket`
object directly from the unique bucket id the authorization response
already named. Every `Archive` method call re-validates the requested key
against the attested exact prefix before touching the SDK.

B2 exposes only a transport SHA-1 for uploaded objects, never SHA-256 --
this project's sole content-identity digest (PROJECT.md "Core Value";
`calico_capture.archive`'s exclusive use of `hashlib.sha256`). `put_object`
therefore stores the project SHA-256 as a small custom `file_info` value at
upload time, and `list_versions` reads it back; the returned digest is
never trusted from provider transport metadata alone.

Every raised error is one of the same fixed, value-free `ArchiveError`
categories `tests.capture.fakes.FakeArchive` already raises for the same
condition (`archive.list_failed` / `archive.write_failed` /
`archive.read_failed` / `archive.object_not_found`), so
`calico_capture.archive.synchronize_verified_transaction` and every other
provider-neutral caller behave identically against the live adapter and
the offline fake (D-14). No caught SDK exception, provider response,
account identity, key fragment, or authorization token is ever persisted,
printed, or re-raised as anything but a fixed category (D-12).

`inspect_retention_posture` is a deliberately separate, owner-authorized,
read-only boundary (COVERAGE.md item 7). It is never reachable from
`B2Archive`, never part of the attested automation-credential scope, and
performs no write/delete/hide/retention/Object-Lock mutation -- it only
reports whether an existing lifecycle rule could hide or delete
`archive/v1/` objects and whether Object Lock is already enabled, as two
closed pass/fail categories.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from b2sdk.v3 import AbstractAccountInfo, B2Api, Bucket, InMemoryAccountInfo
from b2sdk.v3 import B2HttpApiConfig
from b2sdk.v3.exception import B2Error, FileNotPresent

from calico_capture.archive import Archive, ArchiveError, ArchiveObjectVersion

#: Realm passed to `B2Api.authorize_account` -- always the live production
#: realm; no other realm is ever used by this adapter.
_AUTHORIZATION_REALM = "production"

#: The exact, fixed private bucket/prefix/capability contract every
#: automation credential must attest before any archive I/O (D-02/D-10;
#: COVERAGE.md item 1). These are safe, already-documented fixed values --
#: not secrets -- and never change without a new versioned archive prefix.
EXPECTED_BUCKET_NAME = "RegistryData"
EXPECTED_NAME_PREFIX = "archive/v1/"
EXPECTED_CAPABILITIES: frozenset[str] = frozenset({"listFiles", "readFiles", "writeFiles"})
EXPECTED_READ_ONLY_CAPABILITIES: frozenset[str] = frozenset({"listFiles", "readFiles"})

#: The custom `file_info` key this adapter uses to carry the project
#: SHA-256 identity alongside every uploaded object, since B2 itself only
#: exposes a transport SHA-1.
_SHA256_FILE_INFO_KEY = "calico-archive-sha256"

#: One fixed safe category for every authorization-scope rejection
#: (absent/broad/additional/wrong-bucket/wrong-prefix/delete-or-admin/
#: persisted-account-info/owner-bootstrap-default) -- never a description
#: of which check failed, since that would echo credential shape.
_SCOPE_REJECTED_CATEGORY = "archive.scope_rejected"

_AUTHORIZATION_FAILED_CATEGORY = "archive.authorization_failed"
_LIST_FAILED_CATEGORY = "archive.list_failed"
_WRITE_FAILED_CATEGORY = "archive.write_failed"
_READ_FAILED_CATEGORY = "archive.read_failed"
_OBJECT_NOT_FOUND_CATEGORY = "archive.object_not_found"

_RETENTION_READ_FAILED_CATEGORY = "archive.retention_read_failed"
_LIFECYCLE_CLEAR_CATEGORY = "lifecycle_no_archive_deletion_rule"
_LIFECYCLE_UNSAFE_CATEGORY = "lifecycle_archive_deletion_rule_present"
_OBJECT_LOCK_ENABLED_CATEGORY = "object_lock_enabled"
_OBJECT_LOCK_DISABLED_CATEGORY = "object_lock_disabled"
_OBJECT_LOCK_UNKNOWN_CATEGORY = "object_lock_unknown"


@dataclass(frozen=True)
class B2Scope:
    """The exact, safe (non-secret) effective scope `attest_effective_scope`
    proved before any archive I/O is permitted. Carries only the already-
    fixed bucket id/name, prefix, and capability set -- never a credential,
    token, or account identity.
    """

    bucket_id: str
    bucket_name: str
    name_prefix: str
    capabilities: frozenset[str]


def _attest_scope(account_info: AbstractAccountInfo, expected_capabilities: frozenset[str]) -> B2Scope:
    """Fail closed unless `account_info` proves exactly the minimum
    automation scope (D-10; COVERAGE.md item 1).

    Rejects, all with the single fixed `archive.scope_rejected` category,
    before ever inspecting provider I/O:

    - any account-info implementation other than `InMemoryAccountInfo`
      (defends the "never persist account state" rule even if a caller
      ever passed a persisted implementation -- D-12);
    - a missing or multi-bucket restriction (an owner/bootstrap-default key
      with account-wide access, or an ambiguous multi-bucket key);
    - a bucket name other than exactly `RegistryData`;
    - a name prefix other than exactly `archive/v1/`;
    - any capability set other than exactly `{listFiles, readFiles,
      writeFiles}` -- missing, additional, or destructive/administrative
      (`deleteFiles`, `writeBucketRetentions`, ...).
    """

    if not isinstance(account_info, InMemoryAccountInfo):
        raise ArchiveError(_SCOPE_REJECTED_CATEGORY)

    try:
        allowed = account_info.get_allowed()
    except Exception as exc:  # noqa: BLE001 -- fixed safe category only
        raise ArchiveError(_SCOPE_REJECTED_CATEGORY) from exc

    if not isinstance(allowed, dict):
        raise ArchiveError(_SCOPE_REJECTED_CATEGORY)

    buckets = allowed.get("buckets")
    if not isinstance(buckets, list) or len(buckets) != 1:
        raise ArchiveError(_SCOPE_REJECTED_CATEGORY)

    bucket_entry = buckets[0]
    if not isinstance(bucket_entry, dict):
        raise ArchiveError(_SCOPE_REJECTED_CATEGORY)

    bucket_name = bucket_entry.get("name")
    bucket_id = bucket_entry.get("id")
    if bucket_name != EXPECTED_BUCKET_NAME or not isinstance(bucket_id, str) or not bucket_id:
        raise ArchiveError(_SCOPE_REJECTED_CATEGORY)

    name_prefix = allowed.get("namePrefix")
    if name_prefix != EXPECTED_NAME_PREFIX:
        raise ArchiveError(_SCOPE_REJECTED_CATEGORY)

    capabilities = allowed.get("capabilities")
    if not isinstance(capabilities, list):
        raise ArchiveError(_SCOPE_REJECTED_CATEGORY)
    capability_set = frozenset(capabilities)
    if capability_set != expected_capabilities:
        raise ArchiveError(_SCOPE_REJECTED_CATEGORY)

    return B2Scope(
        bucket_id=bucket_id,
        bucket_name=bucket_name,
        name_prefix=name_prefix,
        capabilities=capability_set,
    )


def attest_effective_scope(account_info: AbstractAccountInfo) -> B2Scope:
    """Attest the capture adapter's exact list/read/write capability set."""

    return _attest_scope(account_info, EXPECTED_CAPABILITIES)


def attest_read_only_effective_scope(account_info: AbstractAccountInfo) -> B2Scope:
    """Attest the publication adapter's exact list/read capability set."""

    return _attest_scope(account_info, EXPECTED_READ_ONLY_CAPABILITIES)


def _content_length(value: object) -> int:
    return value if isinstance(value, int) else 0


class B2Archive:
    """Live `Archive` implementation backed by a narrow, exact-scope
    `b2sdk.v3` adapter (D-01/D-02/D-10; COVERAGE.md items 1-2, 4-6).

    Construct only through `B2Archive.authorize` -- the sole entry point
    that performs authorization inside one explicit call, attests exact
    effective scope, and binds the SDK bucket object without any
    account-wide bucket enumeration. Exposes exactly the three narrow
    `Archive` protocol methods; no delete, hide, copy, bucket/key
    mutation, sharing, lifecycle mutation, retention/legal-hold mutation,
    Object Lock mutation, or unbounded-retry method exists on this class
    (COVERAGE.md explicit opt-outs 1-3, 5-7, 9).
    """

    def __init__(self, api: B2Api, bucket: Bucket, scope: B2Scope) -> None:
        self._api = api
        self._bucket = bucket
        self._scope = scope

    @classmethod
    def authorize(
        cls,
        application_key_id: str,
        application_key: str,
        *,
        api_config: "B2HttpApiConfig | None" = None,
    ) -> "B2Archive":
        """Authorize `application_key_id`/`application_key` inside this one
        explicit call using a fresh, never-persisted `InMemoryAccountInfo`,
        attest exact effective scope, and bind the SDK bucket by the
        already-attested unique bucket id (never `listBuckets`).

        `api_config` is test-only discretion (an injected in-memory raw API
        simulator); production callers never supply it and get the real
        `b2sdk` HTTP transport.
        """

        account_info = InMemoryAccountInfo()
        api = B2Api(account_info, api_config=api_config) if api_config is not None else B2Api(account_info)
        try:
            # `B2Api.authorize_account`'s positional order is
            # `(application_key_id, application_key, realm=...)` in b2sdk.v3
            # -- the opposite order from the deprecated v1/v2 session-level
            # `authorize_account(realm, key_id, key)`. Passing the realm
            # first here would silently swap credential values into the
            # wrong parameters.
            api.authorize_account(
                application_key_id, application_key, realm=_AUTHORIZATION_REALM
            )
        except B2Error as exc:
            raise ArchiveError(_AUTHORIZATION_FAILED_CATEGORY) from exc

        scope = attest_effective_scope(account_info)

        try:
            bucket = api.get_bucket_by_id(scope.bucket_id)
        except B2Error as exc:
            raise ArchiveError(_AUTHORIZATION_FAILED_CATEGORY) from exc

        return cls(api, bucket, scope)

    @property
    def scope(self) -> B2Scope:
        return self._scope

    def _require_in_scope(self, key: str) -> None:
        """Defense-in-depth client-side check: refuse any key outside the
        already-attested exact prefix before ever calling the SDK, rather
        than relying solely on server-side enforcement (D-10)."""

        if not key.startswith(self._scope.name_prefix):
            raise ArchiveError(_SCOPE_REJECTED_CATEGORY)

    # -- Archive protocol ---------------------------------------------------

    def list_versions(self, key: str) -> tuple[ArchiveObjectVersion, ...]:
        self._require_in_scope(key)
        try:
            versions = list(self._bucket.list_file_versions(key))
        except B2Error as exc:
            raise ArchiveError(_LIST_FAILED_CATEGORY) from exc

        # Deterministic oldest-first order (Archive protocol contract),
        # independent of the SDK/provider's own listing order.
        versions.sort(key=lambda version: (version.upload_timestamp or 0, version.id_))

        results: list[ArchiveObjectVersion] = []
        for version in versions:
            file_info = version.file_info or {}
            sha256 = file_info.get(_SHA256_FILE_INFO_KEY, "")
            results.append(
                ArchiveObjectVersion(
                    version_id=version.id_,
                    sha256=sha256,
                    content_length=_content_length(version.size),
                    action=version.action,
                )
            )
        return tuple(results)

    def put_object(self, key: str, data: bytes) -> None:
        self._require_in_scope(key)
        digest = hashlib.sha256(data).hexdigest()
        try:
            self._bucket.upload_bytes(data, key, file_info={_SHA256_FILE_INFO_KEY: digest})
        except B2Error as exc:
            raise ArchiveError(_WRITE_FAILED_CATEGORY) from exc

    def get_object(self, key: str, *, version_id: str | None = None) -> bytes:
        self._require_in_scope(key)
        try:
            if version_id is not None:
                # Prefer file-id download so the verified version cannot
                # change between an earlier list_versions call and this
                # read (Archive protocol docstring; COVERAGE.md item 5).
                downloaded = self._api.download_file_by_id(version_id)
            else:
                downloaded = self._bucket.download_file_by_name(key)
        except FileNotPresent as exc:
            raise ArchiveError(_OBJECT_NOT_FOUND_CATEGORY) from exc
        except B2Error as exc:
            raise ArchiveError(_READ_FAILED_CATEGORY) from exc

        buffer = io.BytesIO()
        try:
            downloaded.save(buffer)
        except B2Error as exc:
            raise ArchiveError(_READ_FAILED_CATEGORY) from exc
        return buffer.getvalue()


class B2ReadOnlyArchive:
    """Publication-only archive reader with an exactly read-only B2 key.

    This is intentionally separate from ``B2Archive`` so capture's exact
    list/read/write contract remains unchanged.  A capture-capable key is
    rejected at authorization, and the protocol-shaped ``put_object`` method
    fails locally before provider I/O.
    """

    def __init__(self, api: B2Api, bucket: Bucket, scope: B2Scope) -> None:
        self._api = api
        self._bucket = bucket
        self._scope = scope

    @classmethod
    def authorize(
        cls,
        application_key_id: str,
        application_key: str,
        *,
        api_config: "B2HttpApiConfig | None" = None,
    ) -> "B2ReadOnlyArchive":
        account_info = InMemoryAccountInfo()
        api = B2Api(account_info, api_config=api_config) if api_config is not None else B2Api(account_info)
        try:
            api.authorize_account(application_key_id, application_key, realm=_AUTHORIZATION_REALM)
        except B2Error as exc:
            raise ArchiveError(_AUTHORIZATION_FAILED_CATEGORY) from exc
        scope = attest_read_only_effective_scope(account_info)
        try:
            bucket = api.get_bucket_by_id(scope.bucket_id)
        except B2Error as exc:
            raise ArchiveError(_AUTHORIZATION_FAILED_CATEGORY) from exc
        return cls(api, bucket, scope)

    @property
    def scope(self) -> B2Scope:
        return self._scope

    def _require_in_scope(self, key: str) -> None:
        if not key.startswith(self._scope.name_prefix):
            raise ArchiveError(_SCOPE_REJECTED_CATEGORY)

    def list_versions(self, key: str) -> tuple[ArchiveObjectVersion, ...]:
        self._require_in_scope(key)
        try:
            versions = list(self._bucket.list_file_versions(key))
        except B2Error as exc:
            raise ArchiveError(_LIST_FAILED_CATEGORY) from exc
        versions.sort(key=lambda version: (version.upload_timestamp or 0, version.id_))
        return tuple(
            ArchiveObjectVersion(
                version_id=version.id_,
                sha256=(version.file_info or {}).get(_SHA256_FILE_INFO_KEY, ""),
                content_length=_content_length(version.size),
                action=version.action,
            )
            for version in versions
        )

    def get_object(self, key: str, *, version_id: str | None = None) -> bytes:
        self._require_in_scope(key)
        try:
            downloaded = (
                self._api.download_file_by_id(version_id)
                if version_id is not None
                else self._bucket.download_file_by_name(key)
            )
        except FileNotPresent as exc:
            raise ArchiveError(_OBJECT_NOT_FOUND_CATEGORY) from exc
        except B2Error as exc:
            raise ArchiveError(_READ_FAILED_CATEGORY) from exc
        buffer = io.BytesIO()
        try:
            downloaded.save(buffer)
        except B2Error as exc:
            raise ArchiveError(_READ_FAILED_CATEGORY) from exc
        return buffer.getvalue()

    def put_object(self, key: str, data: bytes) -> None:
        self._require_in_scope(key)
        raise ArchiveError(_SCOPE_REJECTED_CATEGORY)


@dataclass(frozen=True)
class RetentionPosture:
    """Two closed, safe pass/fail categories describing whether an
    existing lifecycle rule could hide or delete `archive/v1/` objects and
    whether Object Lock is already enabled (COVERAGE.md item 7). Never
    carries a raw provider response, rule detail, or account identity.
    """

    lifecycle_category: str
    object_lock_category: str


def _lifecycle_rule_covers_archive_prefix(rule: object) -> bool:
    """True if `rule`'s `fileNamePrefix` overlaps `EXPECTED_NAME_PREFIX`'s
    object-key space in *either* direction: a rule prefix that is an
    ancestor of `archive/v1/` (e.g. `""`, `"archive/"`) covers every
    archived object, and a rule prefix that is a *descendant* of
    `archive/v1/` (e.g. `"archive/v1/store/"`) still covers whichever
    archived objects fall under that narrower sub-path. Two prefix-based
    rules overlap exactly when either string is a prefix of the other."""

    if not isinstance(rule, dict):
        return False
    rule_prefix = rule.get("fileNamePrefix") or ""
    return EXPECTED_NAME_PREFIX.startswith(rule_prefix) or rule_prefix.startswith(
        EXPECTED_NAME_PREFIX
    )


def _lifecycle_rule_can_hide_or_delete(rule: object) -> bool:
    if not isinstance(rule, dict):
        return False
    return (
        rule.get("daysFromHidingToDeleting") is not None
        or rule.get("daysFromUploadingToHiding") is not None
    )


def inspect_retention_posture(bucket: Bucket) -> RetentionPosture:
    """Owner-authorized, read-only lifecycle/Object Lock posture check
    (COVERAGE.md item 7; D-12). Deliberately separate from `B2Archive`:
    the caller must supply a `Bucket` bound to a distinct owner-authorized
    session that carries bucket-configuration read capability (e.g.
    `listBuckets`/`readBuckets`) -- capabilities the automation credential
    attested by `attest_effective_scope` never carries, so this function
    is structurally unreachable from the automation credential's scope.

    Performs exactly one read call (`Bucket.get_fresh_state`) and no
    write, delete, hide, retention-mutation, legal-hold-mutation, or
    Object-Lock-mutation call. Returns only the two closed categories
    below -- never a raw lifecycle rule, bucket id, or provider response.
    """

    try:
        fresh = bucket.get_fresh_state()
    except B2Error as exc:
        raise ArchiveError(_RETENTION_READ_FAILED_CATEGORY) from exc

    lifecycle_rules = fresh.lifecycle_rules or []
    unsafe = any(
        _lifecycle_rule_covers_archive_prefix(rule) and _lifecycle_rule_can_hide_or_delete(rule)
        for rule in lifecycle_rules
    )
    lifecycle_category = _LIFECYCLE_UNSAFE_CATEGORY if unsafe else _LIFECYCLE_CLEAR_CATEGORY

    if fresh.is_file_lock_enabled is True:
        object_lock_category = _OBJECT_LOCK_ENABLED_CATEGORY
    elif fresh.is_file_lock_enabled is False:
        object_lock_category = _OBJECT_LOCK_DISABLED_CATEGORY
    else:
        object_lock_category = _OBJECT_LOCK_UNKNOWN_CATEGORY

    return RetentionPosture(
        lifecycle_category=lifecycle_category, object_lock_category=object_lock_category
    )


# Structural proof (also mechanically tested) that this module never
# imports or references a delete/hide/copy/bucket-mutation/sharing/
# lifecycle-mutation/retention-mutation/Object-Lock-mutation SDK method
# name anywhere in its own source (COVERAGE.md explicit opt-outs 1-3,
# 5-7, 9) -- see tests.capture.test_b2_adapter.DestructiveSurfaceAbsenceTests.
__all__ = [
    "B2Archive",
    "B2Scope",
    "EXPECTED_BUCKET_NAME",
    "EXPECTED_CAPABILITIES",
    "EXPECTED_READ_ONLY_CAPABILITIES",
    "EXPECTED_NAME_PREFIX",
    "RetentionPosture",
    "attest_effective_scope",
    "attest_read_only_effective_scope",
    "B2ReadOnlyArchive",
    "inspect_retention_posture",
]
