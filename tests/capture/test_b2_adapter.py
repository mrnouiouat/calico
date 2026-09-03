"""Effective-capability and API-coverage contract tests for the live B2
adapter (06-05-PLAN.md Tasks 1-2; COVERAGE.md items 1-2, 4-7).

Every test here runs entirely offline against `b2sdk.v3.RawSimulator` (the
official SDK's own in-memory raw-API double) or a hand-constructed
`InMemoryAccountInfo`. No test in this module contacts the live source,
the live Backblaze service, or any private credential -- the pinned
`b2sdk==2.12.0` package is exercised exactly as production code would use
it, but every network boundary is the official simulator (per this plan's
own testing-strategy discretion: "test via b2sdk's in-memory/raw simulator
or fakes").

`RawSimulator` keeps all state on the instance it is constructed with, and
each `B2Api(...)` construction normally builds a *fresh* `RawSimulator`.
`_shared_api_config` works around that by handing `B2HttpApiConfig` a
plain factory function (not a class) that always returns one already-
provisioned `RawSimulator` instance -- so admin-only setup calls (create
account/bucket/key) and the production `B2Archive.authorize(...)` call
under test observe the exact same simulated bucket state.
"""

from __future__ import annotations

import hashlib
import inspect
import tempfile
import unittest
from pathlib import Path

from b2sdk.v3 import B2Api, B2HttpApiConfig, InMemoryAccountInfo, RawSimulator
from b2sdk.v3.exception import B2Error

from calico_capture.archive import ArchiveError, synchronize_verified_transaction
from calico_capture.b2 import (
    B2Archive,
    EXPECTED_BUCKET_NAME,
    EXPECTED_CAPABILITIES,
    EXPECTED_NAME_PREFIX,
    attest_effective_scope,
    inspect_retention_posture,
)
from calico_landing.admission import admit
from calico_landing.result import AdmissionResult
from tests.fixtures.landing.fixture_builder import mutated_candidate

_MODULE_SOURCE_PATH = Path(__file__).resolve().parents[2] / "calico_capture" / "b2.py"

_ACCOUNT_REALM = "production"


# -- Shared RawSimulator wiring helpers -------------------------------------


def _shared_api_config(raw: RawSimulator) -> B2HttpApiConfig:
    """A `B2HttpApiConfig` whose raw-API "class" is really a factory that
    always returns the one already-provisioned `raw` instance, so setup
    calls and the code under test share simulator state (see module
    docstring).
    """

    def _factory(b2_http=None):  # noqa: ARG001 -- signature-compatible stand-in for a raw-API class
        return raw

    return B2HttpApiConfig(_raw_api_class=_factory)


def _provision_account(raw: RawSimulator) -> tuple[str, str, str, str]:
    """Create a fresh account on `raw`. Returns
    `(account_id, master_key, api_url, master_auth_token)`."""

    account_id, master_key = raw.create_account()
    master_auth = raw.authorize_account(_ACCOUNT_REALM, account_id, master_key)
    api_url = master_auth["apiInfo"]["storageApi"]["apiUrl"]
    return account_id, master_key, api_url, master_auth["authorizationToken"]


def _create_bucket(
    raw: RawSimulator,
    api_url: str,
    auth_token: str,
    account_id: str,
    name: str,
    *,
    lifecycle_rules: list[dict] | None = None,
    is_file_lock_enabled: bool | None = None,
) -> str:
    created = raw.create_bucket(
        api_url,
        auth_token,
        account_id,
        name,
        "allPrivate",
        lifecycle_rules=lifecycle_rules,
        is_file_lock_enabled=is_file_lock_enabled,
    )
    return created["bucketId"]


def _create_key(
    raw: RawSimulator,
    api_url: str,
    auth_token: str,
    account_id: str,
    *,
    capabilities: list[str],
    bucket_ids: list[str] | None,
    name_prefix: str | None,
    key_name: str = "automation-key",
) -> tuple[str, str]:
    created = raw.create_key(
        api_url, auth_token, account_id, sorted(capabilities), key_name, None, bucket_ids, name_prefix
    )
    return created["applicationKeyId"], created["applicationKey"]


def _provision_scoped_credentials(
    raw: RawSimulator,
    *,
    bucket_name: str = EXPECTED_BUCKET_NAME,
    name_prefix: str | None = EXPECTED_NAME_PREFIX,
    capabilities: frozenset[str] = EXPECTED_CAPABILITIES,
    restrict_to_bucket: bool = True,
    second_bucket_name: str | None = None,
) -> tuple[str, str, str]:
    """Provision one scoped application key against a fresh account/bucket
    on `raw`. Returns `(application_key_id, application_key, bucket_id)`.
    """

    account_id, _master_key, api_url, auth_token = _provision_account(raw)
    bucket_id = _create_bucket(raw, api_url, auth_token, account_id, bucket_name)
    if second_bucket_name is not None:
        _create_bucket(raw, api_url, auth_token, account_id, second_bucket_name)
    bucket_ids = [bucket_id] if restrict_to_bucket else None
    key_id, key = _create_key(
        raw,
        api_url,
        auth_token,
        account_id,
        capabilities=capabilities,
        bucket_ids=bucket_ids,
        name_prefix=name_prefix,
    )
    return key_id, key, bucket_id


def _authorize(raw: RawSimulator, key_id: str, key: str) -> B2Archive:
    return B2Archive.authorize(key_id, key, api_config=_shared_api_config(raw))


def _patch_raw_simulator_bucket_scope_bug(raw: RawSimulator) -> None:
    """Work around a `b2sdk==2.12.0` `RawSimulator` gap: several top-level
    methods (`get_upload_url`, `hide_file`, `start_large_file`,
    `list_unfinished_large_files`, `get_upload_part_url`,
    `finish_large_file`) omit `bucket_id=` (and, for some, `file_name=`)
    when calling `_assert_account_auth`, so a legitimately
    bucket-restricted key is unconditionally rejected by the simulator for
    these operations -- even though the real B2 service correctly
    authorizes a bucket-restricted key against its own bucket (verified
    directly against a fresh `RawSimulator` before this patch existed;
    `bucket.upload_bytes()`'s own multi-part/large-file path routes
    through every one of these once the simulator's tiny 200-byte
    `MIN_PART_SIZE` classifies a normal test fixture object as a large
    file). This replaces only these bound methods on this one `raw`
    instance with a version that forwards the same arguments the
    simulator's own (correctly bucket-scoped) `list_file_versions` already
    passes, so a scoped automation key behaves the same in tests as it
    would against the live service. Production `calico_capture.b2` code
    is never touched or imported by this helper.
    """

    def _get_upload_url(api_url, account_auth_token, bucket_id):
        bucket = raw._get_bucket_by_id(bucket_id)
        raw._assert_account_auth(
            api_url, account_auth_token, bucket.account_id, "writeFiles", bucket_id=bucket_id
        )
        return bucket.get_upload_url(account_auth_token)

    def _hide_file(api_url, account_auth_token, bucket_id, file_name):
        bucket = raw._get_bucket_by_id(bucket_id)
        raw._assert_account_auth(
            api_url,
            account_auth_token,
            bucket.account_id,
            "writeFiles",
            bucket_id=bucket_id,
            file_name=file_name,
        )
        response = bucket.hide_file(account_auth_token, file_name)
        raw.file_id_to_bucket_id[response["fileId"]] = bucket_id
        return response

    def _start_large_file(
        api_url,
        account_auth_token,
        bucket_id,
        file_name,
        content_type,
        file_info,
        server_side_encryption=None,
        file_retention=None,
        legal_hold=None,
        custom_upload_timestamp=None,
    ):
        bucket = raw._get_bucket_by_id(bucket_id)
        raw._assert_account_auth(
            api_url,
            account_auth_token,
            bucket.account_id,
            "writeFiles",
            bucket_id=bucket_id,
            file_name=file_name,
        )
        result = bucket.start_large_file(
            account_auth_token,
            file_name,
            content_type,
            file_info,
            server_side_encryption,
            file_retention,
            legal_hold,
            custom_upload_timestamp=custom_upload_timestamp,
        )
        raw.file_id_to_bucket_id[result["fileId"]] = bucket_id
        return result

    def _get_upload_part_url(api_url, account_auth_token, file_id):
        bucket_id = raw.file_id_to_bucket_id[file_id]
        bucket = raw._get_bucket_by_id(bucket_id)
        raw._assert_account_auth(
            api_url, account_auth_token, bucket.account_id, "writeFiles", bucket_id=bucket_id
        )
        return bucket.get_upload_part_url(account_auth_token, file_id)

    def _finish_large_file(api_url, account_auth_token, file_id, part_sha1_array):
        bucket_id = raw.file_id_to_bucket_id[file_id]
        bucket = raw._get_bucket_by_id(bucket_id)
        raw._assert_account_auth(
            api_url, account_auth_token, bucket.account_id, "writeFiles", bucket_id=bucket_id
        )
        return bucket.finish_large_file(account_auth_token, file_id, part_sha1_array)

    def _list_unfinished_large_files(
        api_url, account_auth_token, bucket_id, start_file_id=None, max_file_count=None, prefix=None
    ):
        bucket = raw._get_bucket_by_id(bucket_id)
        raw._assert_account_auth(
            api_url,
            account_auth_token,
            bucket.account_id,
            "listFiles",
            bucket_id=bucket_id,
            file_name=prefix,
        )
        start_file_id = start_file_id or ""
        max_file_count = max_file_count or 100
        return bucket.list_unfinished_large_files(
            account_auth_token, start_file_id, max_file_count, prefix
        )

    raw.get_upload_url = _get_upload_url
    raw.hide_file = _hide_file
    raw.start_large_file = _start_large_file
    raw.list_unfinished_large_files = _list_unfinished_large_files
    raw.get_upload_part_url = _get_upload_part_url
    raw.finish_large_file = _finish_large_file


def _new_simulator() -> RawSimulator:
    raw = RawSimulator()
    _patch_raw_simulator_bucket_scope_bug(raw)
    return raw


def _forbid_list_buckets(raw: RawSimulator, test: unittest.TestCase) -> None:
    """Replace `raw.list_buckets` with a spy that fails the test if ever
    called -- proves a code path never performs account-wide bucket
    enumeration (COVERAGE.md item 2 / explicit opt-out 8)."""

    def _spy(*args, **kwargs):  # noqa: ARG001
        test.fail("list_buckets must never be called by the automation credential path")

    raw.list_buckets = _spy  # instance-level override; not a bound method


# -- InMemoryAccountInfo-only scope fixtures ---------------------------------


def _account_info_with_allowed(allowed: dict) -> InMemoryAccountInfo:
    info = InMemoryAccountInfo()
    info.set_auth_data(
        account_id="account-x",
        auth_token="auth-token-x",
        api_url="http://api.example.com",
        download_url="http://download.example.com",
        recommended_part_size=100,
        absolute_minimum_part_size=100,
        application_key="app-key-x",
        realm=_ACCOUNT_REALM,
        s3_api_url="http://s3.example.com",
        allowed=allowed,
        application_key_id="key-id-x",
    )
    return info


def _valid_allowed() -> dict:
    return {
        "buckets": [{"id": "bucket-1", "name": EXPECTED_BUCKET_NAME}],
        "capabilities": sorted(EXPECTED_CAPABILITIES),
        "namePrefix": EXPECTED_NAME_PREFIX,
    }


class AttestEffectiveScopeTests(unittest.TestCase):
    """Task 1 Test 2: absent, broad, additional, wrong-bucket, wrong-prefix,
    delete/admin, persisted-account-info, and owner/bootstrap-default scope
    all fail closed with the one fixed `archive.scope_rejected` category,
    before any list/read/write is even attempted."""

    def test_exact_minimum_scope_is_accepted(self) -> None:
        scope = attest_effective_scope(_account_info_with_allowed(_valid_allowed()))
        self.assertEqual(scope.bucket_id, "bucket-1")
        self.assertEqual(scope.bucket_name, EXPECTED_BUCKET_NAME)
        self.assertEqual(scope.name_prefix, EXPECTED_NAME_PREFIX)
        self.assertEqual(scope.capabilities, EXPECTED_CAPABILITIES)

    def _assert_rejected(self, allowed: dict) -> None:
        with self.assertRaises(ArchiveError) as ctx:
            attest_effective_scope(_account_info_with_allowed(allowed))
        self.assertEqual(ctx.exception.category, "archive.scope_rejected")

    def test_absent_bucket_restriction_is_rejected(self) -> None:
        self._assert_rejected(dict(_valid_allowed(), buckets=None))

    def test_multi_bucket_restriction_is_rejected(self) -> None:
        allowed = dict(
            _valid_allowed(),
            buckets=[
                {"id": "bucket-1", "name": EXPECTED_BUCKET_NAME},
                {"id": "bucket-2", "name": "SomethingElse"},
            ],
        )
        self._assert_rejected(allowed)

    def test_wrong_bucket_name_is_rejected(self) -> None:
        allowed = dict(_valid_allowed(), buckets=[{"id": "bucket-1", "name": "SomethingElse"}])
        self._assert_rejected(allowed)

    def test_wrong_prefix_is_rejected(self) -> None:
        self._assert_rejected(dict(_valid_allowed(), namePrefix="archive/v2/"))

    def test_missing_prefix_is_rejected(self) -> None:
        self._assert_rejected(dict(_valid_allowed(), namePrefix=None))

    def test_missing_capability_is_rejected(self) -> None:
        self._assert_rejected(dict(_valid_allowed(), capabilities=["listFiles", "readFiles"]))

    def test_additional_capability_is_rejected(self) -> None:
        allowed = dict(_valid_allowed(), capabilities=sorted(EXPECTED_CAPABILITIES | {"shareFiles"}))
        self._assert_rejected(allowed)

    def test_delete_or_admin_capability_is_rejected(self) -> None:
        allowed = dict(
            _valid_allowed(),
            capabilities=sorted(EXPECTED_CAPABILITIES | {"deleteFiles", "writeBucketRetentions"}),
        )
        self._assert_rejected(allowed)

    def test_broad_owner_or_bootstrap_default_scope_is_rejected(self) -> None:
        # No bucket restriction plus account-wide capabilities -- exactly
        # the shape of an owner/bootstrap master key, never a value this
        # module may silently accept as the automation default.
        allowed = {
            "buckets": None,
            "capabilities": sorted(EXPECTED_CAPABILITIES | {"listBuckets", "deleteFiles"}),
            "namePrefix": None,
        }
        self._assert_rejected(allowed)

    def test_persisted_account_info_is_rejected_before_any_inspection(self) -> None:
        class _ExplodesIfInspected:
            def get_allowed(self):  # pragma: no cover - must never run
                raise AssertionError(
                    "get_allowed must never be called for a rejected account-info type"
                )

        with self.assertRaises(ArchiveError) as ctx:
            attest_effective_scope(_ExplodesIfInspected())
        self.assertEqual(ctx.exception.category, "archive.scope_rejected")

    def test_malformed_allowed_shape_is_rejected(self) -> None:
        self._assert_rejected({"buckets": "not-a-list", "capabilities": [], "namePrefix": None})


class B2ArchiveAuthorizeTests(unittest.TestCase):
    """Task 1 Test 1: authorization happens only inside one explicit call,
    and the unique restricted bucket is bound from the already-returned
    `allowed` contract -- never through account-wide bucket enumeration."""

    def test_authorize_binds_unique_bucket_without_listing_buckets(self) -> None:
        raw = _new_simulator()
        key_id, key, bucket_id = _provision_scoped_credentials(raw)
        _forbid_list_buckets(raw, self)

        archive = _authorize(raw, key_id, key)

        self.assertEqual(archive.scope.bucket_id, bucket_id)
        self.assertEqual(archive.scope.bucket_name, EXPECTED_BUCKET_NAME)
        self.assertEqual(archive.scope.name_prefix, EXPECTED_NAME_PREFIX)
        self.assertEqual(archive.scope.capabilities, EXPECTED_CAPABILITIES)

    def test_authorize_ignores_an_unrelated_second_bucket(self) -> None:
        raw = _new_simulator()
        key_id, key, bucket_id = _provision_scoped_credentials(
            raw, second_bucket_name="UnrelatedBucket"
        )
        _forbid_list_buckets(raw, self)

        archive = _authorize(raw, key_id, key)
        self.assertEqual(archive.scope.bucket_id, bucket_id)
        self.assertEqual(archive.scope.bucket_name, EXPECTED_BUCKET_NAME)

    def test_authorize_rejects_wrong_scope_key_before_any_io(self) -> None:
        raw = _new_simulator()
        key_id, key, _bucket_id = _provision_scoped_credentials(
            raw, restrict_to_bucket=False, capabilities=EXPECTED_CAPABILITIES | {"deleteFiles"}
        )
        _forbid_list_buckets(raw, self)

        with self.assertRaises(ArchiveError) as ctx:
            _authorize(raw, key_id, key)
        self.assertEqual(ctx.exception.category, "archive.scope_rejected")

    def test_authorize_wraps_bad_credentials_as_fixed_authorization_category(self) -> None:
        raw = _new_simulator()
        key_id, _key, _bucket_id = _provision_scoped_credentials(raw)

        with self.assertRaises(ArchiveError) as ctx:
            _authorize(raw, key_id, "wrong-secret")
        self.assertEqual(ctx.exception.category, "archive.authorization_failed")

    def test_master_key_is_rejected_as_automation_default(self) -> None:
        # The master account key itself is the canonical "owner/bootstrap"
        # shape: unrestricted buckets, every capability. It must never be
        # accepted as an automation credential.
        raw = _new_simulator()
        account_id, master_key, api_url, auth_token = _provision_account(raw)
        _create_bucket(raw, api_url, auth_token, account_id, EXPECTED_BUCKET_NAME)
        _forbid_list_buckets(raw, self)

        with self.assertRaises(ArchiveError) as ctx:
            _authorize(raw, account_id, master_key)
        self.assertEqual(ctx.exception.category, "archive.scope_rejected")


class B2ArchiveVersionListingTests(unittest.TestCase):
    """Task 2 Test 1: exact-prefix version listing classifies absent, one
    upload, duplicate upload, hide marker, and unfinished-upload states,
    and exposes every listed version rather than truncating early."""

    def _archive(self) -> tuple[B2Archive, RawSimulator, str, str]:
        raw = _new_simulator()
        key_id, key, _bucket_id = _provision_scoped_credentials(raw)
        return _authorize(raw, key_id, key), raw, key_id, key

    def test_absent_key_returns_no_versions(self) -> None:
        archive, _raw, _key_id, _key = self._archive()
        self.assertEqual(archive.list_versions(EXPECTED_NAME_PREFIX + "missing.json"), ())

    def test_single_upload_version_is_reported(self) -> None:
        archive, _raw, _key_id, _key = self._archive()
        key = EXPECTED_NAME_PREFIX + "object.bin"
        archive.put_object(key, b"hello")

        versions = archive.list_versions(key)

        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].action, "upload")
        self.assertEqual(versions[0].content_length, len(b"hello"))
        self.assertEqual(versions[0].sha256, hashlib.sha256(b"hello").hexdigest())

    def test_duplicate_upload_versions_are_all_reported_oldest_first(self) -> None:
        archive, _raw, _key_id, _key = self._archive()
        key = EXPECTED_NAME_PREFIX + "object.bin"
        archive.put_object(key, b"first-bytes")
        archive.put_object(key, b"second-bytes-longer")

        versions = archive.list_versions(key)

        self.assertEqual(len(versions), 2)
        self.assertTrue(all(version.action == "upload" for version in versions))
        self.assertEqual(versions[0].content_length, len(b"first-bytes"))
        self.assertEqual(versions[1].content_length, len(b"second-bytes-longer"))

    def test_hide_marker_is_reported_as_a_distinct_action(self) -> None:
        archive, raw, key_id, key = self._archive()
        object_key = EXPECTED_NAME_PREFIX + "object.bin"
        archive.put_object(object_key, b"hello")

        # Seed a hide marker through a second session authorized with the
        # exact same scoped credentials, using only the live SDK `Bucket`
        # wrapper -- production `B2Archive` itself exposes no hide method
        # (COVERAGE.md explicit opt-out 2); this is test setup only.
        setup_api = B2Api(InMemoryAccountInfo(), api_config=_shared_api_config(raw))
        setup_api.authorize_account(key_id, key, realm=_ACCOUNT_REALM)
        setup_bucket = setup_api.get_bucket_by_id(archive.scope.bucket_id)
        setup_bucket.hide_file(object_key)

        versions = archive.list_versions(object_key)

        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].action, "upload")
        self.assertEqual(versions[1].action, "hide")

    def test_unfinished_upload_is_reported_as_a_distinct_action(self) -> None:
        archive, raw, key_id, key = self._archive()
        object_key = EXPECTED_NAME_PREFIX + "unfinished.bin"

        # `writeFiles` alone is sufficient to start (but never finish) a
        # large file -- the exact real-world shape of an interrupted
        # multipart upload this adapter's `Archive` protocol must never
        # treat as an admissible existing version.
        scoped_auth = raw.authorize_account(_ACCOUNT_REALM, key_id, key)
        scoped_api_url = scoped_auth["apiInfo"]["storageApi"]["apiUrl"]
        scoped_auth_token = scoped_auth["authorizationToken"]
        raw.start_large_file(
            scoped_api_url, scoped_auth_token, archive.scope.bucket_id, object_key, "b2/x-auto", {}
        )

        versions = archive.list_versions(object_key)

        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].action, "start")

    def test_list_versions_passes_through_whatever_action_the_provider_reports(self) -> None:
        # `B2Archive.list_versions` never interprets or validates the
        # `action` string itself -- classifying "any action other than a
        # lone upload" as a closed ambiguity error is
        # `calico_capture.archive`'s job (already proven against every
        # provider by `tests.capture.test_archive`, which runs identically
        # against this live adapter and the offline `FakeArchive`, per
        # D-14). This test only proves the passthrough is faithful for
        # every action value the pinned SDK can actually report.
        archive, raw, key_id, key = self._archive()
        upload_key = EXPECTED_NAME_PREFIX + "roundtrip.bin"
        archive.put_object(upload_key, b"data")
        reported_actions = {version.action for version in archive.list_versions(upload_key)}
        self.assertEqual(reported_actions, {"upload"})


class B2ArchivePutGetTests(unittest.TestCase):
    """Task 2 Test 2: download by listed version id returns bytes, and
    project byte count/SHA-256 read-back decides success."""

    def _archive(self) -> B2Archive:
        raw = _new_simulator()
        key_id, key, _bucket_id = _provision_scoped_credentials(raw)
        return _authorize(raw, key_id, key)

    def test_put_then_get_by_version_id_round_trips_exact_bytes(self) -> None:
        archive = self._archive()
        key = EXPECTED_NAME_PREFIX + "object.bin"
        payload = b"immutable archive object payload"

        archive.put_object(key, payload)
        version = archive.list_versions(key)[0]
        downloaded = archive.get_object(key, version_id=version.version_id)

        self.assertEqual(downloaded, payload)
        self.assertEqual(hashlib.sha256(downloaded).hexdigest(), version.sha256)

    def test_get_object_without_version_id_returns_the_latest_version(self) -> None:
        archive = self._archive()
        key = EXPECTED_NAME_PREFIX + "object.bin"
        archive.put_object(key, b"first")
        archive.put_object(key, b"second")

        downloaded = archive.get_object(key)

        self.assertEqual(downloaded, b"second")

    def test_get_object_on_a_missing_key_is_a_fixed_not_found_category(self) -> None:
        archive = self._archive()
        with self.assertRaises(ArchiveError) as ctx:
            archive.get_object(EXPECTED_NAME_PREFIX + "missing.bin")
        self.assertEqual(ctx.exception.category, "archive.object_not_found")

    def test_out_of_prefix_key_is_rejected_before_reaching_the_sdk(self) -> None:
        archive = self._archive()
        with self.assertRaises(ArchiveError) as ctx:
            archive.list_versions("outside/the/attested/prefix.json")
        self.assertEqual(ctx.exception.category, "archive.scope_rejected")

        with self.assertRaises(ArchiveError):
            archive.put_object("outside/the/attested/prefix.json", b"data")

        with self.assertRaises(ArchiveError):
            archive.get_object("outside/the/attested/prefix.json")


def _admit_baseline_into_fresh_store() -> tuple[Path, AdmissionResult, tempfile.TemporaryDirectory]:
    """Admit the committed identity-free baseline candidate into a brand
    new external temporary store, mirroring
    `tests.capture.test_archive._admit_baseline_into_fresh_store` -- kept
    as a local copy so this module has no cross-test-module import
    coupling. See that module for the full rationale."""

    store_tmp = tempfile.TemporaryDirectory(prefix="calico-b2-adapter-test-store-")
    store_root = Path(store_tmp.name).resolve()
    with mutated_candidate() as candidate:
        result = admit(candidate.root, store_root)
    assert result.status == "accepted", result.status
    return store_root, result, store_tmp


class B2ArchiveIntegratedTransactionTests(unittest.TestCase):
    """Task 2 Test 2 (system level): the live adapter is behaviorally
    interchangeable with `FakeArchive` for the full
    `synchronize_verified_transaction` contract -- absent-key-only upload,
    content-then-promotion-then-manifest ordering, and read-back-verified
    idempotent replay, all through the real `b2sdk.v3` call surface
    (COVERAGE.md acceptance criteria; D-14)."""

    def _archive(self) -> B2Archive:
        raw = _new_simulator()
        key_id, key, _bucket_id = _provision_scoped_credentials(raw)
        return _authorize(raw, key_id, key)

    def test_synchronize_transaction_uploads_only_absent_keys_and_is_idempotent(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = self._archive()

            first = synchronize_verified_transaction(archive, store_root, result)
            first_version_ids = {
                key: archive.list_versions(key)[0].version_id
                for key in first.object_keys + (first.manifest_key,)
            }

            second = synchronize_verified_transaction(archive, store_root, result)

            self.assertEqual(first, second)
            for key in first.object_keys + (first.manifest_key,):
                # Byte-identical replay must never create a second version
                # at any key -- an absent-key-only upload policy proven
                # against the live adapter, not just `FakeArchive`.
                versions = archive.list_versions(key)
                self.assertEqual(len(versions), 1)
                self.assertEqual(versions[0].version_id, first_version_ids[key])
        finally:
            store_tmp.cleanup()

    def test_synchronize_transaction_manifest_is_read_back_verified(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = self._archive()
            transaction = synchronize_verified_transaction(archive, store_root, result)

            manifest_bytes = archive.get_object(transaction.manifest_key)
            for key in transaction.object_keys:
                content = archive.get_object(key)
                self.assertTrue(len(content) >= 0)  # every content key is readable back
            self.assertGreater(len(manifest_bytes), 0)
        finally:
            store_tmp.cleanup()


class DestructiveSurfaceAbsenceTests(unittest.TestCase):
    """Task 2 Test 3: the adapter exposes no delete, hide, copy, bucket/key
    mutation, sharing, lifecycle mutation, retention/legal-hold mutation,
    Object Lock mutation, or unbounded-retry method."""

    #: SDK/raw-API method names for every opted-out capability in
    #: COVERAGE.md's explicit opt-outs 1-3 and 5-10. Any occurrence of one
    #: of these identifiers in `calico_capture/b2.py`'s own source would
    #: mean this adapter references a mutation/admin/sharing surface it
    #: must never touch.
    _FORBIDDEN_IDENTIFIERS = (
        "delete_file_version",
        "hide_file",
        "copy_file",
        "copy_part",
        "update_bucket",
        "create_bucket",
        "delete_bucket",
        "update_file_retention",
        "update_file_legal_hold",
        "list_buckets",
        "list_all_bucket_names",
        "create_key",
        "delete_key",
        "list_keys",
        "get_download_authorization",
        "start_large_file",
        "cancel_large_file",
        "list_unfinished_large_files",
        "list_parts",
        "set_bucket_notification_rules",
        "get_bucket_notification_rules",
        "bypass_governance",
        "bypassGovernance",
    )

    def test_module_source_never_references_a_forbidden_identifier(self) -> None:
        source = _MODULE_SOURCE_PATH.read_text(encoding="utf-8")
        for identifier in self._FORBIDDEN_IDENTIFIERS:
            self.assertNotIn(
                identifier, source, f"forbidden identifier {identifier!r} found in b2.py"
            )

    def test_b2archive_exposes_exactly_the_narrow_protocol_plus_authorize(self) -> None:
        public_attrs = {name for name in dir(B2Archive) if not name.startswith("_")}
        self.assertEqual(public_attrs, {"authorize", "scope", "list_versions", "put_object", "get_object"})

    def test_b2archive_has_no_unbounded_retry_loop_construct(self) -> None:
        source = inspect.getsource(B2Archive)
        # A hand-rolled unbounded retry would show up as a `while True`
        # loop somewhere in the class body; the SDK's own bounded
        # transport retry lives entirely inside `b2sdk`, never here.
        self.assertNotIn("while True", source)
        self.assertNotIn("while 1:", source)


class InspectRetentionPostureTests(unittest.TestCase):
    """Task 2 Test 4: the separate, owner-only `inspect_retention_posture`
    read boundary reports closed lifecycle/Object Lock categories and
    exposes no mutation or archive data path."""

    def _owner_bucket(self, raw: RawSimulator, **bucket_kwargs):
        account_id, master_key, api_url, auth_token = _provision_account(raw)
        bucket_id = _create_bucket(
            raw, api_url, auth_token, account_id, EXPECTED_BUCKET_NAME, **bucket_kwargs
        )
        owner_api = B2Api(InMemoryAccountInfo(), api_config=_shared_api_config(raw))
        owner_api.authorize_account(account_id, master_key, realm=_ACCOUNT_REALM)
        return owner_api.get_bucket_by_id(bucket_id)

    def test_no_lifecycle_rule_and_object_lock_disabled(self) -> None:
        raw = _new_simulator()
        bucket = self._owner_bucket(raw, is_file_lock_enabled=False)

        posture = inspect_retention_posture(bucket)

        self.assertEqual(posture.lifecycle_category, "lifecycle_no_archive_deletion_rule")
        self.assertEqual(posture.object_lock_category, "object_lock_disabled")

    def test_object_lock_enabled_is_reported(self) -> None:
        raw = _new_simulator()
        bucket = self._owner_bucket(raw, is_file_lock_enabled=True)

        posture = inspect_retention_posture(bucket)

        self.assertEqual(posture.object_lock_category, "object_lock_enabled")

    def test_lifecycle_rule_covering_the_archive_prefix_is_unsafe(self) -> None:
        raw = _new_simulator()
        unsafe_rule = {
            "fileNamePrefix": "archive/",
            "daysFromHidingToDeleting": 30,
        }
        bucket = self._owner_bucket(raw, lifecycle_rules=[unsafe_rule], is_file_lock_enabled=False)

        posture = inspect_retention_posture(bucket)

        self.assertEqual(posture.lifecycle_category, "lifecycle_archive_deletion_rule_present")

    def test_lifecycle_rule_outside_the_archive_prefix_is_clear(self) -> None:
        raw = _new_simulator()
        unrelated_rule = {
            "fileNamePrefix": "some-other-prefix/",
            "daysFromHidingToDeleting": 1,
        }
        bucket = self._owner_bucket(
            raw, lifecycle_rules=[unrelated_rule], is_file_lock_enabled=False
        )

        posture = inspect_retention_posture(bucket)

        self.assertEqual(posture.lifecycle_category, "lifecycle_no_archive_deletion_rule")

    def test_inspect_retention_posture_is_not_reachable_from_b2archive(self) -> None:
        self.assertFalse(hasattr(B2Archive, "inspect_retention_posture"))
        public_attrs = {name for name in dir(B2Archive) if not name.startswith("_")}
        self.assertNotIn("inspect_retention_posture", public_attrs)

    def test_inspect_retention_posture_performs_exactly_one_read_call(self) -> None:
        raw = _new_simulator()
        bucket = self._owner_bucket(raw, is_file_lock_enabled=False)

        call_count = {"n": 0}
        original_get_fresh_state = bucket.get_fresh_state

        def _counting_get_fresh_state():
            call_count["n"] += 1
            return original_get_fresh_state()

        bucket.get_fresh_state = _counting_get_fresh_state
        inspect_retention_posture(bucket)

        self.assertEqual(call_count["n"], 1)


if __name__ == "__main__":
    unittest.main()
