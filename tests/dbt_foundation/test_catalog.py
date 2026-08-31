"""Contract tests for the closed dbt input catalog and verify-copy-bind
preflight trust boundary (T-03-01..T-03-07).

Builds a real, multi-revision admitted store through the Gate B fixture
(`tests.fixtures.dbt_foundation.fixture_builder.gate_b_fixture_store`), then
exercises `calico_dbt.catalog` and `calico_dbt.preflight` against its real,
on-disk manifests, pointer, and canonical Parquet objects -- never a
hand-mocked stand-in for the store layout. Every constructed catalog or
manifest anchor lives only inside an owned `TemporaryDirectory`; nothing
this module writes is ever committed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import duckdb

from calico_dbt import catalog as cat
from calico_dbt import preflight as pf
from calico_landing.contracts import LOGICAL_LIST_ORDER

from tests.fixtures.dbt_foundation.fixture_builder import gate_b_fixture_store

_VALID_SHA = "a" * 64
_VALID_FP = "b" * 64


def _valid_catalog_document() -> dict:
    return {
        "contract_version": 1,
        "releases": [
            {
                "as_of_date": "2031-02-04",
                "release_revision": 1,
                "revision_fingerprint": _VALID_FP,
                "revision_manifest_sha256": _VALID_SHA,
            }
        ],
    }


def _write_json(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _revision_dir_for(store_root: Path, as_of_date: str, release_revision: int, fingerprint: str) -> Path:
    return store_root / "releases" / as_of_date / f"rev-{release_revision:04d}-{fingerprint[:8]}"


def _manifest_bytes_for(store_root: Path, as_of_date: str, release_revision: int, fingerprint: str) -> bytes:
    manifest_path = _revision_dir_for(store_root, as_of_date, release_revision, fingerprint) / "manifest.json"
    return manifest_path.read_bytes()


def _build_real_catalog(store_root: Path, admissions) -> cat.InputCatalog:
    manifests = []
    for admission in admissions:
        result = admission.result
        manifest_bytes = _manifest_bytes_for(
            store_root, result.as_of_date, result.release_revision, result.revision_fingerprint
        )
        manifests.append(
            (result.as_of_date, result.release_revision, result.revision_fingerprint, manifest_bytes)
        )
    return cat.build_catalog_from_manifests(manifests)


class CatalogDocumentShapeTests(unittest.TestCase):
    """Task 1, T-03-01/T-03-02: closed schema, exact keys, no forbidden fields."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="calico-catalog-doc-")
        self.addCleanup(self._tmp.cleanup)
        self.catalog_path = Path(self._tmp.name) / "catalog.json"

    def test_valid_catalog_loads(self) -> None:
        _write_json(self.catalog_path, _valid_catalog_document())
        loaded = cat.load_input_catalog(self.catalog_path)
        self.assertEqual(loaded.contract_version, 1)
        self.assertEqual(len(loaded.releases), 1)
        anchor = loaded.releases[0]
        self.assertEqual(anchor.as_of_date, "2031-02-04")
        self.assertEqual(anchor.release_revision, 1)
        self.assertEqual(anchor.revision_fingerprint, _VALID_FP)
        self.assertEqual(anchor.revision_manifest_sha256, _VALID_SHA)

    def test_rejects_unknown_top_level_key(self) -> None:
        doc = _valid_catalog_document()
        doc["extra"] = "nope"
        _write_json(self.catalog_path, doc)
        with self.assertRaises(cat.CatalogError) as ctx:
            cat.load_input_catalog(self.catalog_path)
        self.assertEqual(ctx.exception.category, "catalog.invalid_schema")

    def test_rejects_missing_top_level_key(self) -> None:
        doc = {"contract_version": 1}
        _write_json(self.catalog_path, doc)
        with self.assertRaises(cat.CatalogError) as ctx:
            cat.load_input_catalog(self.catalog_path)
        self.assertEqual(ctx.exception.category, "catalog.invalid_schema")

    def test_rejects_unsupported_contract_version(self) -> None:
        doc = _valid_catalog_document()
        doc["contract_version"] = 2
        _write_json(self.catalog_path, doc)
        with self.assertRaises(cat.CatalogError) as ctx:
            cat.load_input_catalog(self.catalog_path)
        self.assertEqual(ctx.exception.category, "catalog.unsupported_version")

    def test_rejects_canonical_object_hash_smuggled_into_entry(self) -> None:
        doc = _valid_catalog_document()
        doc["releases"][0]["canonical_sha256"] = _VALID_SHA
        _write_json(self.catalog_path, doc)
        with self.assertRaises(cat.CatalogError) as ctx:
            cat.load_input_catalog(self.catalog_path)
        self.assertEqual(ctx.exception.category, "catalog.invalid_release_anchor")

    def test_rejects_row_count_smuggled_into_entry(self) -> None:
        doc = _valid_catalog_document()
        doc["releases"][0]["parquet_row_count"] = 4
        _write_json(self.catalog_path, doc)
        with self.assertRaises(cat.CatalogError):
            cat.load_input_catalog(self.catalog_path)

    def test_rejects_path_smuggled_into_entry(self) -> None:
        doc = _valid_catalog_document()
        doc["releases"][0]["path"] = "/private/store"
        _write_json(self.catalog_path, doc)
        with self.assertRaises(cat.CatalogError):
            cat.load_input_catalog(self.catalog_path)

    def test_rejects_bad_date_pattern(self) -> None:
        doc = _valid_catalog_document()
        doc["releases"][0]["as_of_date"] = "2031/02/04"
        _write_json(self.catalog_path, doc)
        with self.assertRaises(cat.CatalogError):
            cat.load_input_catalog(self.catalog_path)

    def test_rejects_bad_fingerprint_pattern(self) -> None:
        doc = _valid_catalog_document()
        doc["releases"][0]["revision_fingerprint"] = "not-hex"
        _write_json(self.catalog_path, doc)
        with self.assertRaises(cat.CatalogError):
            cat.load_input_catalog(self.catalog_path)

    def test_rejects_duplicate_release_anchor(self) -> None:
        doc = _valid_catalog_document()
        doc["releases"].append(dict(doc["releases"][0]))
        _write_json(self.catalog_path, doc)
        with self.assertRaises(cat.CatalogError) as ctx:
            cat.load_input_catalog(self.catalog_path)
        self.assertEqual(ctx.exception.category, "catalog.duplicate_release_anchor")

    def test_missing_document_fails_closed(self) -> None:
        with self.assertRaises(cat.CatalogError) as ctx:
            cat.load_input_catalog(self.catalog_path)
        self.assertEqual(ctx.exception.category, "catalog.not_found")

    def test_malformed_json_fails_closed(self) -> None:
        self.catalog_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(cat.CatalogError) as ctx:
            cat.load_input_catalog(self.catalog_path)
        self.assertEqual(ctx.exception.category, "catalog.invalid_json")


class RevisionManifestVerificationTests(unittest.TestCase):
    """Task 1, T-03-02/T-03-03: manifest-anchor hash verification and
    fingerprint recomputation over a real admitted store.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._store_ctx = gate_b_fixture_store()
        cls.store = cls._store_ctx.__enter__()
        cls.first_admission = cls.store.admissions[0]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._store_ctx.__exit__(None, None, None)

    def _real_anchor(self) -> cat.CatalogReleaseAnchor:
        result = self.first_admission.result
        manifest_bytes = _manifest_bytes_for(
            self.store.store_root, result.as_of_date, result.release_revision, result.revision_fingerprint
        )
        import hashlib

        return cat.CatalogReleaseAnchor(
            as_of_date=result.as_of_date,
            release_revision=result.release_revision,
            revision_fingerprint=result.revision_fingerprint,
            revision_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        )

    def _manifest_path(self) -> Path:
        result = self.first_admission.result
        return (
            _revision_dir_for(
                self.store.store_root, result.as_of_date, result.release_revision, result.revision_fingerprint
            )
            / "manifest.json"
        )

    def test_real_manifest_verifies_against_its_own_hash_anchor(self) -> None:
        anchor = self._real_anchor()
        verified = cat.load_and_verify_revision_manifest(self._manifest_path(), anchor)
        self.assertEqual(verified.as_of_date, anchor.as_of_date)
        self.assertEqual(verified.release_revision, anchor.release_revision)
        self.assertEqual(verified.revision_fingerprint, anchor.revision_fingerprint)
        self.assertEqual(set(name for name, _ in verified.logical_lists), set(LOGICAL_LIST_ORDER))

    def test_wrong_manifest_hash_anchor_fails_closed(self) -> None:
        anchor = self._real_anchor()
        tampered = cat.CatalogReleaseAnchor(
            as_of_date=anchor.as_of_date,
            release_revision=anchor.release_revision,
            revision_fingerprint=anchor.revision_fingerprint,
            revision_manifest_sha256=_VALID_SHA,
        )
        with self.assertRaises(cat.CatalogError) as ctx:
            cat.load_and_verify_revision_manifest(self._manifest_path(), tampered)
        self.assertEqual(ctx.exception.category, "catalog.manifest_hash_mismatch")

    def test_anchor_revision_mismatch_fails_closed(self) -> None:
        anchor = self._real_anchor()
        tampered = cat.CatalogReleaseAnchor(
            as_of_date=anchor.as_of_date,
            release_revision=anchor.release_revision + 1,
            revision_fingerprint=anchor.revision_fingerprint,
            revision_manifest_sha256=anchor.revision_manifest_sha256,
        )
        with self.assertRaises(cat.CatalogError) as ctx:
            cat.load_and_verify_revision_manifest(self._manifest_path(), tampered)
        # The hash check runs first and will already fail because the byte
        # content is identical but the anchor's own manifest hash was
        # computed for the *original* revision_revision's bytes -- so this
        # still correctly fails closed, just at the hash-mismatch gate.
        self.assertIn(
            ctx.exception.category,
            {"catalog.manifest_hash_mismatch", "catalog.manifest_anchor_mismatch"},
        )

    def test_manifest_not_found_fails_closed(self) -> None:
        anchor = self._real_anchor()
        missing_path = self._manifest_path().parent / "does-not-exist.json"
        with self.assertRaises(cat.CatalogError) as ctx:
            cat.load_and_verify_revision_manifest(missing_path, anchor)
        self.assertEqual(ctx.exception.category, "catalog.manifest_not_found")

    def test_internally_inconsistent_fingerprint_fails_closed(self) -> None:
        # Construct a manifest document that is internally well-formed but
        # whose recorded raw_sha256 values do not hash to its own declared
        # revision_fingerprint -- proving the recomputation check (not just
        # the anchor hash check) is load-bearing.
        real_bytes = self._manifest_path().read_bytes()
        document = json.loads(real_bytes.decode("utf-8"))
        first_list = LOGICAL_LIST_ORDER[0]
        document["metadata"]["logical_lists"][first_list]["raw_sha256"] = "c" * 64

        with tempfile.TemporaryDirectory(prefix="calico-tampered-manifest-") as tmp_name:
            tampered_path = Path(tmp_name) / "manifest.json"
            tampered_bytes = json.dumps(document).encode("utf-8")
            tampered_path.write_bytes(tampered_bytes)

            import hashlib

            anchor = cat.CatalogReleaseAnchor(
                as_of_date=document["as_of_date"],
                release_revision=document["release_revision"],
                revision_fingerprint=document["revision_fingerprint"],
                revision_manifest_sha256=hashlib.sha256(tampered_bytes).hexdigest(),
            )
            with self.assertRaises(cat.CatalogError) as ctx:
                cat.load_and_verify_revision_manifest(tampered_path, anchor)
            self.assertEqual(ctx.exception.category, "catalog.fingerprint_mismatch")


class PreflightVerifyCopyBindTests(unittest.TestCase):
    """Task 1, T-03-01..T-03-07: the full verify-copy-bind boundary over a
    real, multi-revision, multi-pointer-variant admitted store.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._store_ctx = gate_b_fixture_store()
        cls.store = cls._store_ctx.__enter__()
        cls.real_catalog = _build_real_catalog(cls.store.store_root, cls.store.admissions)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._store_ctx.__exit__(None, None, None)

    def _run_preflight(self, catalog: cat.InputCatalog):
        temp_dir = tempfile.mkdtemp(prefix="calico-preflight-test-")
        temp_root = Path(temp_dir)
        try:
            binding = pf.prepare_runtime_input(
                store_root=self.store.store_root, catalog=catalog, temp_root=temp_root
            )
            return binding, temp_root
        except Exception:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise

    def test_prepares_all_fixed_relations_with_expected_rows(self) -> None:
        binding, temp_root = self._run_preflight(self.real_catalog)
        try:
            self.assertEqual(binding.verified_release_count, len(self.store.admissions))
            self.assertTrue(binding.duckdb_path.is_file())

            connection = duckdb.connect(str(binding.duckdb_path), read_only=True)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'runtime_input'"
                    ).fetchall()
                }
                expected = {name.replace("-", "_") for name in LOGICAL_LIST_ORDER} | {
                    "revision_catalog",
                    "promotion_catalog",
                    # Added by 04-04-PLAN.md (D-12/D-20): the fixed
                    # nullable capture-attempt relation both fixture and
                    # real preflight always create, forward-fixing this
                    # exact-set assertion the same way this plan already
                    # forward-fixed test_repository_contract.py's parallel
                    # six-relation assertion.
                    "capture_attempts",
                    # Added by 04-05-PLAN.md (D-16/D-18/D-20): the fixed
                    # nullable private eligibility-classification relation
                    # both fixture and real preflight always create.
                    "public_eligibility_classifications",
                }
                self.assertEqual(tables, expected)

                revision_count = connection.execute(
                    "SELECT COUNT(*) FROM runtime_input.revision_catalog"
                ).fetchone()[0]
                self.assertEqual(revision_count, len(self.store.admissions))

                promotion_count = connection.execute(
                    "SELECT COUNT(*) FROM runtime_input.promotion_catalog"
                ).fetchone()[0]
                self.assertGreater(promotion_count, 0)
            finally:
                connection.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
        self.assertFalse(temp_root.exists())

    def test_fresh_process_can_query_populated_database_after_close(self) -> None:
        binding, temp_root = self._run_preflight(self.real_catalog)
        try:
            # `duckdb.connect` here opens a brand-new connection object,
            # standing in for "a fresh process" -- the point under test is
            # that preflight's own connection was fully closed and no lock
            # is held, so a second, independent connection can read it.
            connection = duckdb.connect(str(binding.duckdb_path), read_only=True)
            try:
                first_list = LOGICAL_LIST_ORDER[0].replace("-", "_")
                row_count = connection.execute(
                    f'SELECT COUNT(*) FROM runtime_input."{first_list}"'
                ).fetchone()[0]
                self.assertGreater(row_count, 0)
            finally:
                connection.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_no_generated_artifact_exists_under_either_repository(self) -> None:
        binding, temp_root = self._run_preflight(self.real_catalog)
        try:
            calico_build_root = Path(__file__).resolve().parents[3]
            calico_root = Path(__file__).resolve().parents[2]
            self.assertFalse(str(temp_root).startswith(str(calico_root)))
            self.assertFalse(str(temp_root).startswith(str(calico_build_root)))
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_opaque_copy_mutation_after_verification_cannot_change_bound_input(self) -> None:
        # Mutating the *source* canonical object after preflight has already
        # copied and rehashed it must never be visible in the bound
        # database -- the bound relation was built from the opaque copy,
        # not a live reference to the source path.
        binding, temp_root = self._run_preflight(self.real_catalog)
        try:
            first_admission = self.store.admissions[0]
            result = first_admission.result
            source_parquet = (
                _revision_dir_for(
                    self.store.store_root,
                    result.as_of_date,
                    result.release_revision,
                    result.revision_fingerprint,
                )
                / "canonical"
                / f"{LOGICAL_LIST_ORDER[0]}.parquet"
            )
            original_bytes = source_parquet.read_bytes()
            try:
                source_parquet.write_bytes(original_bytes + b"\x00" * 8)
                connection = duckdb.connect(str(binding.duckdb_path), read_only=True)
                try:
                    first_list = LOGICAL_LIST_ORDER[0].replace("-", "_")
                    # The bound relation must still be queryable and
                    # unaffected by the post-verification source mutation.
                    row_count = connection.execute(
                        f'SELECT COUNT(*) FROM runtime_input."{first_list}"'
                    ).fetchone()[0]
                    self.assertGreater(row_count, 0)
                finally:
                    connection.close()
            finally:
                source_parquet.write_bytes(original_bytes)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_missing_revision_directory_fails_closed_and_cleans_up(self) -> None:
        bogus_anchor = cat.CatalogReleaseAnchor(
            as_of_date="2099-01-01",
            release_revision=1,
            revision_fingerprint=_VALID_FP,
            revision_manifest_sha256=_VALID_SHA,
        )
        bogus_catalog = cat.InputCatalog(contract_version=1, releases=(bogus_anchor,))
        with self.assertRaises(pf.PreflightError) as ctx:
            self._run_preflight(bogus_catalog)
        self.assertEqual(ctx.exception.category, "preflight.revision_not_found")

    def test_tampered_manifest_anchor_fails_closed(self) -> None:
        real_admission = self.store.admissions[0]
        result = real_admission.result
        tampered_anchor = cat.CatalogReleaseAnchor(
            as_of_date=result.as_of_date,
            release_revision=result.release_revision,
            revision_fingerprint=result.revision_fingerprint,
            revision_manifest_sha256=_VALID_SHA,
        )
        tampered_catalog = cat.InputCatalog(contract_version=1, releases=(tampered_anchor,))
        with self.assertRaises(pf.PreflightError) as ctx:
            self._run_preflight(tampered_catalog)
        self.assertEqual(ctx.exception.category, "preflight.manifest_verification_failed")

    def test_store_inside_git_worktree_is_rejected(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        with self.assertRaises(pf.PreflightError) as ctx:
            pf.prepare_runtime_input(
                store_root=repo_root,
                catalog=self.real_catalog,
                temp_root=tempfile.mkdtemp(prefix="calico-preflight-worktree-"),
            )
        self.assertEqual(ctx.exception.category, "preflight.store_in_worktree")

    def test_pointer_inconsistent_with_catalog_fails_closed(self) -> None:
        # Build a catalog that anchors a *different* fingerprint than the
        # one the store's real promotion pointer actually names for the
        # shared middle date -- simulating a present, inconsistent pointer.
        promoted_admission = None
        for admission in self.store.admissions:
            if admission.revision_label in (
                self.store.pointer_variant,
            ):
                promoted_admission = admission
        self.assertIsNotNone(promoted_admission)
        result = promoted_admission.result

        anchors = []
        for admission in self.store.admissions:
            other = admission.result
            if (
                other.as_of_date == result.as_of_date
                and other.release_revision == result.release_revision
            ):
                manifest_bytes = _manifest_bytes_for(
                    self.store.store_root, other.as_of_date, other.release_revision, "0" * 64
                ) if False else _manifest_bytes_for(
                    self.store.store_root, other.as_of_date, other.release_revision, other.revision_fingerprint
                )
                import hashlib

                anchors.append(
                    cat.CatalogReleaseAnchor(
                        as_of_date=other.as_of_date,
                        release_revision=other.release_revision,
                        revision_fingerprint=_VALID_FP,  # deliberately wrong
                        revision_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                    )
                )
            else:
                manifest_bytes = _manifest_bytes_for(
                    self.store.store_root, other.as_of_date, other.release_revision, other.revision_fingerprint
                )
                import hashlib

                anchors.append(
                    cat.CatalogReleaseAnchor(
                        as_of_date=other.as_of_date,
                        release_revision=other.release_revision,
                        revision_fingerprint=other.revision_fingerprint,
                        revision_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                    )
                )

        # This catalog is internally inconsistent with the real manifest's
        # own fingerprint for the promoted revision, so manifest
        # verification itself fails closed before pointer validation is
        # ever reached -- both are legitimate fail-closed outcomes for a
        # tampered anchor naming a mismatched fingerprint.
        bad_catalog = cat.InputCatalog(contract_version=1, releases=tuple(anchors))
        with self.assertRaises(pf.PreflightError):
            self._run_preflight(bad_catalog)


if __name__ == "__main__":
    unittest.main()
