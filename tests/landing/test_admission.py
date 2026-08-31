"""Full synthetic admission matrix for `calico_landing.admission.admit()` (D-05-D-09).

Exercises the complete public admission transaction end to end against
Plan 08's committed identity-free fixtures and their deterministic mutation
builder: accepted revision 1, identical-set no-new-release, the unescaped
quote and CP1252 high-byte round-trip cases, every required rejection
fixture (truncation, missing file, wrong header, wrong arity, blank/
mismatched date, duplicate key within/across lists, unknown registration
family), the valid/invalid same-date revision pair, candidate-boundary
defenses (path traversal, symlink alias, duplicate resolved target, XLSX
dispatch, resource ceiling, store-in-worktree, in-repository candidate
outside the fixture prefix), injected parser/Parquet/store failures
proving no partial canonical output is ever exposed, and non-echo output
across every rejection path.

No real organization identity or excluded value is used -- only reserved
synthetic sentinels reused from `tests.fixtures.landing.fixture_builder`,
per D-10.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import calico_landing.admission as admission_module
from calico_landing.admission import admit, load_default_status_contract
from calico_landing.candidate import CandidateError, resolve_and_stage_candidate
from calico_landing.contracts import LOGICAL_LIST_ORDER, CsvContract
from calico_landing.parquet import CanonicalSerializationError
from calico_landing.parser import StructuralReject
from calico_landing.result import AdmissionResult
from calico_landing.store import StoreError, read_promoted_releases
from tests.fixtures.landing import fixture_builder as fb

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_CANDIDATE_ROOT = REPO_ROOT / "tests" / "fixtures" / "landing" / "valid"

_EXPECTED_HEADERS = (
    "Registry Status",
    "State Charity Reg#",
    "FEIN",
    "SOS/FTB#",
    "Name",
    "City",
    "State",
    "Issue Date",
    "Last Renewal",
    "Date Status Set",
    "As-of Date",
)

#: Every synthetic sentinel this suite's rejecting fixtures embed in a
#: field value -- none may ever appear in admission output.
SYNTHETIC_PRIVATE_MARKERS = (
    fb.SENTINEL_DUPLICATE_KEY,
    fb.SENTINEL_UNKNOWN_FAMILY_KEY,
    fb.SENTINEL_MISMATCHED_DATE,
    fb.CP1252_HIGH_BYTE_NAME,
)


def _recompute_content_length(candidate_root: Path) -> None:
    """Resynchronize a mutated candidate's manifest `content_length` fields
    with the mutated CSVs' actual on-disk byte counts.

    `fixture_builder`'s mutation helpers (Plan 08, already committed and
    out of this plan's file ownership) rewrite CSV bytes without updating
    the copied baseline manifest's declared `content_length`. Every test
    below that targets a check *other* than D-05 `transfer.length_mismatch`
    calls this first so admission reaches the specific rule the fixture
    exists to exercise, rather than failing early on a manifest that was
    never meant to describe the mutated bytes. `test_truncated_payload_*`
    deliberately omits this call -- the stale manifest length is exactly
    what proves the transfer-completion check.
    """

    manifest_path = candidate_root / fb.MANIFEST_FILENAME
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in document["objects"].values():
        csv_path = candidate_root / entry["relative_path"]
        entry["content_length"] = csv_path.stat().st_size
    manifest_path.write_text(json.dumps(document), encoding="utf-8")


def _staging_leftovers(store_root: Path) -> list[Path]:
    staging_root = store_root / ".staging"
    if not staging_root.exists():
        return []
    return list(staging_root.iterdir())


def _reason_codes(result: AdmissionResult) -> list[str]:
    return [reason.code for reason in result.reasons]


def _tiny_ceiling_contract(**overrides: object) -> CsvContract:
    fields: dict[str, object] = {
        "contract_version": 1,
        "logical_lists": LOGICAL_LIST_ORDER,
        "headers": _EXPECTED_HEADERS,
        "encoding": "cp1252",
        "quoting": "QUOTE_NONE",
        "canonical_exchange_format": "parquet",
        "max_compressed_payload_bytes": 1_000_000,
        "max_decompressed_payload_bytes": 1_000_000,
        "max_physical_line_bytes": 1_000_000,
    }
    fields.update(overrides)
    return CsvContract(**fields)


class AcceptedRevisionTests(unittest.TestCase):
    """The complete positive path: revision 1, identical rerun, the
    unescaped-quote and CP1252 high-byte regressions, and a valid same-date
    revision 2.
    """

    def test_baseline_candidate_admits_revision_one_with_full_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            store_root = Path(store_dir)

            result = admit(BASELINE_CANDIDATE_ROOT, store_root)

            self.assertEqual(result.status, "accepted")
            self.assertEqual(result.release_revision, 1)
            self.assertEqual(result.as_of_date, "2020-01-15")
            self.assertRegex(result.revision_fingerprint, r"^[0-9a-f]{64}$")
            self.assertEqual(result.reasons, ())
            self.assertEqual(_staging_leftovers(store_root), [])

            promoted = read_promoted_releases(store_root)
            self.assertEqual(set(promoted.keys()), {"2020-01-15"})
            revision_dir = store_root / promoted["2020-01-15"].revision_dir

            raw_files = sorted((revision_dir / "raw").glob("*.csv"))
            canonical_files = sorted((revision_dir / "canonical").glob("*.parquet"))
            self.assertEqual(len(raw_files), 4)
            self.assertEqual(len(canonical_files), 4)
            self.assertTrue((revision_dir / "manifest.json").is_file())

            manifest = json.loads((revision_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["as_of_date"], "2020-01-15")
            self.assertEqual(manifest["release_revision"], 1)
            self.assertEqual(manifest["revision_fingerprint"], result.revision_fingerprint)
            self.assertEqual(manifest["fingerprint_algorithm"], "ordered-source-sha256-json-v1")

            logical_list_metadata = manifest["metadata"]["logical_lists"]
            self.assertEqual(set(logical_list_metadata.keys()), set(LOGICAL_LIST_ORDER))
            for entry in logical_list_metadata.values():
                self.assertRegex(entry["raw_sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(entry["parquet_sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(entry["raw_byte_count"], 0)
                self.assertEqual(entry["parsed_record_count"], entry["parquet_row_count"])
                self.assertTrue(entry["line_record_reconciled"])
            self.assertEqual(manifest["metadata"]["admission_reasons"], [])

    def test_identical_rerun_returns_no_new_release_pointer_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            store_root = Path(store_dir)
            admit(BASELINE_CANDIDATE_ROOT, store_root)
            pointer_before = (store_root / "promoted-releases.json").read_bytes()
            revision_dirs_before = sorted((store_root / "releases" / "2020-01-15").iterdir())

            result = admit(BASELINE_CANDIDATE_ROOT, store_root)

            self.assertEqual(result.status, "no_new_release")
            self.assertEqual(result.release_revision, 1)
            pointer_after = (store_root / "promoted-releases.json").read_bytes()
            self.assertEqual(pointer_before, pointer_after)
            self.assertEqual(
                sorted((store_root / "releases" / "2020-01-15").iterdir()), revision_dirs_before
            )
            self.assertEqual(_staging_leftovers(store_root), [])

    def test_slash_separated_source_as_of_date_normalizes_to_iso(self) -> None:
        # The real AG registry publishes "As-of Date" slash-separated
        # (e.g. "2026/07/15"), discovered during Plan 02-07's real-release
        # admission -- the committed baseline uses ISO dashes as a
        # simplification. `_best_effort_as_of_date` must normalize this to
        # the strict ISO form `calico_landing.store` requires as the
        # release identity, not pass the raw source separator through.
        with fb.slash_separated_as_of_date() as candidate:
            with tempfile.TemporaryDirectory() as store_dir:
                store_root = Path(store_dir)

                result = admit(candidate.root, store_root)

                self.assertEqual(result.status, "accepted")
                self.assertEqual(result.release_revision, 1)
                self.assertEqual(result.as_of_date, "2020-01-15")

                promoted = read_promoted_releases(store_root)
                self.assertEqual(set(promoted.keys()), {"2020-01-15"})

    def test_unescaped_quote_in_name_round_trips_as_accepted(self) -> None:
        # The committed baseline's charities-may-operate.csv already carries
        # an unescaped quote in one Name field (GATE-A-EVIDENCE.md Section 4
        # regression); the baseline acceptance test above is itself this
        # proof. Assert it explicitly here too as the named regression case.
        with tempfile.TemporaryDirectory() as store_dir:
            result = admit(BASELINE_CANDIDATE_ROOT, Path(store_dir))
            self.assertEqual(result.status, "accepted")

    def test_valid_cp1252_high_byte_round_trips_as_accepted(self) -> None:
        with fb.cp1252_high_byte_field() as candidate:
            _recompute_content_length(candidate.root)
            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))
                self.assertEqual(result.status, "accepted")
                self.assertEqual(result.reasons, ())

    def test_valid_same_date_revision_admits_revision_two_without_zero_day_transition(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            store_root = Path(store_dir)
            first = admit(BASELINE_CANDIDATE_ROOT, store_root)
            self.assertEqual(first.release_revision, 1)

            with fb.valid_same_date_revision() as candidate:
                _recompute_content_length(candidate.root)
                second = admit(candidate.root, store_root)

            self.assertEqual(second.status, "accepted")
            self.assertEqual(second.release_revision, 2)
            self.assertNotEqual(second.revision_fingerprint, first.revision_fingerprint)

            promoted = read_promoted_releases(store_root)
            # One promoted revision per date -- no second analytical date
            # observation was created by the same-day republication (D-09).
            self.assertEqual(set(promoted.keys()), {"2020-01-15"})
            self.assertEqual(promoted["2020-01-15"].release_revision, 2)

            revision_dirs = list((store_root / "releases" / "2020-01-15").iterdir())
            self.assertEqual(len(revision_dirs), 2)
            revision_one_dir = next(path for path in revision_dirs if "rev-0001-" in path.name)
            self.assertEqual(len(list((revision_one_dir / "raw").glob("*.csv"))), 4)
            self.assertEqual(len(list((revision_one_dir / "canonical").glob("*.parquet"))), 4)


class RejectionMatrixTests(unittest.TestCase):
    """One mutated defect at a time; every rejection preserves the prior
    promoted release and exposes no canonical output.
    """

    def test_truncated_payload_rejected_with_deterministic_transfer_code(self) -> None:
        # Deliberately skip `_recompute_content_length`: the stale declared
        # length is exactly what proves transfer-completion verification
        # (D-05 `transfer.length_mismatch`), one of the codes
        # `02-VALIDATION.md`'s fixture matrix accepts for truncation.
        with fb.truncated_payload() as candidate:
            with tempfile.TemporaryDirectory() as store_dir:
                store_root = Path(store_dir)
                result = admit(candidate.root, store_root)

                self.assertEqual(result.status, "rejected")
                self.assertIn("transfer.length_mismatch", _reason_codes(result))
                self.assertEqual(_staging_leftovers(store_root), [])
                self.assertFalse((store_root / "releases" / "2020-01-15").exists())

    def test_missing_logical_file_rejected_as_invalid_mapping_pointer_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            store_root = Path(store_dir)
            admit(BASELINE_CANDIDATE_ROOT, store_root)
            pointer_before = (store_root / "promoted-releases.json").read_bytes()

            with fb.missing_mapping() as candidate:
                result = admit(candidate.root, store_root)

            self.assertEqual(result.status, "rejected")
            self.assertEqual(_reason_codes(result), ["candidate.invalid_mapping"])
            self.assertEqual((store_root / "promoted-releases.json").read_bytes(), pointer_before)
            self.assertEqual(_staging_leftovers(store_root), [])

    def test_wrong_header_rejected_with_safe_logical_location_only(self) -> None:
        with fb.wrong_header() as candidate:
            _recompute_content_length(candidate.root)
            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(len(result.reasons), 1)
                self.assertEqual(result.reasons[0].code, "parse.header_mismatch")
                self.assertEqual(result.reasons[0].logical_list, "charities-undetermined-status")
                self.assertEqual(result.reasons[0].safe_line_number, 1)

    def test_wrong_arity_rejected_with_no_raw_row_in_output(self) -> None:
        with fb.wrong_arity() as candidate:
            _recompute_content_length(candidate.root)
            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(len(result.reasons), 1)
                self.assertEqual(result.reasons[0].code, "parse.arity_mismatch")
                self.assertEqual(result.reasons[0].logical_list, "charities-may-not-operate")

    def test_blank_date_rejected_with_ordered_date_reason(self) -> None:
        with fb.blank_date() as candidate:
            _recompute_content_length(candidate.root)
            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["date.blank"])

    def test_mismatched_date_rejected_with_ordered_date_reason(self) -> None:
        with fb.mismatched_date() as candidate:
            _recompute_content_length(candidate.root)
            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["date.mismatch"])

    def test_duplicate_key_within_list_rejected_with_duplicate_category(self) -> None:
        with fb.duplicate_key_within_list() as candidate:
            _recompute_content_length(candidate.root)
            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["registration.duplicate", "registration.duplicate"])
                self.assertTrue(all(r.logical_list == "charities-may-operate" for r in result.reasons))

    def test_duplicate_key_across_lists_rejected_with_duplicate_category(self) -> None:
        with fb.duplicate_key_across_lists() as candidate:
            _recompute_content_length(candidate.root)
            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["registration.duplicate", "registration.duplicate"])
                affected_lists = {reason.logical_list for reason in result.reasons}
                self.assertEqual(affected_lists, {"charities-may-operate", "charities-not-operating"})

    def test_unknown_registration_family_rejected_blank_keys_stay_accepted(self) -> None:
        with fb.unknown_registration_family() as candidate:
            _recompute_content_length(candidate.root)
            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["registration.unknown_format"])

        # The unmutated baseline carries one intentionally blank
        # registration key (Unregistered Outreach Group); its own
        # acceptance (proven in AcceptedRevisionTests) is the "blank keys
        # remain accepted" half of this requirement.

    def test_invalid_same_date_revision_rejected_preserves_prior_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            store_root = Path(store_dir)
            first = admit(BASELINE_CANDIDATE_ROOT, store_root)
            self.assertEqual(first.status, "accepted")
            pointer_before = (store_root / "promoted-releases.json").read_bytes()
            revision_dirs_before = sorted((store_root / "releases" / "2020-01-15").iterdir())

            with fb.invalid_same_date_revision() as candidate:
                _recompute_content_length(candidate.root)
                result = admit(candidate.root, store_root)

            self.assertEqual(result.status, "rejected")
            self.assertEqual(_reason_codes(result), ["registration.duplicate", "registration.duplicate"])
            self.assertEqual((store_root / "promoted-releases.json").read_bytes(), pointer_before)
            self.assertEqual(
                sorted((store_root / "releases" / "2020-01-15").iterdir()), revision_dirs_before
            )
            self.assertEqual(_staging_leftovers(store_root), [])


class CandidateBoundaryTests(unittest.TestCase):
    """Path/link containment, XLSX dispatch, resource ceilings, and the
    Git-worktree boundaries T-02-04/T-02-07 require.
    """

    def test_xlsx_candidate_rejected_before_csv_parsing(self) -> None:
        with fb.mutated_candidate() as candidate:
            manifest_path = candidate.manifest_path()
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            xlsx_path = candidate.root / "charities-may-operate.xlsx"
            xlsx_path.write_bytes(candidate.csv_path("charities-may-operate").read_bytes())
            document["objects"]["charities-may-operate"]["relative_path"] = (
                "charities-may-operate.xlsx"
            )
            document["objects"]["charities-may-operate"]["content_length"] = xlsx_path.stat().st_size
            manifest_path.write_text(json.dumps(document), encoding="utf-8")

            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["contract.unsupported_xlsx"])

    def test_path_traversal_relative_path_rejected(self) -> None:
        with fb.mutated_candidate() as candidate:
            manifest_path = candidate.manifest_path()
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            document["objects"]["charities-may-operate"]["relative_path"] = "../evil.csv"
            manifest_path.write_text(json.dumps(document), encoding="utf-8")

            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["candidate.invalid_mapping"])

    def test_duplicate_resolved_target_rejected(self) -> None:
        with fb.mutated_candidate() as candidate:
            manifest_path = candidate.manifest_path()
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            document["objects"]["charities-not-operating"]["relative_path"] = (
                "charities-may-operate.csv"
            )
            document["objects"]["charities-not-operating"]["content_length"] = document["objects"][
                "charities-may-operate"
            ]["content_length"]
            manifest_path.write_text(json.dumps(document), encoding="utf-8")

            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["candidate.invalid_mapping"])

    def test_symlink_alias_rejected(self) -> None:
        with fb.mutated_candidate() as candidate:
            alias_path = candidate.root / "alias.csv"
            try:
                os.symlink(candidate.csv_path("charities-may-operate"), alias_path)
            except OSError:
                self.skipTest("symlink creation requires elevated privilege on this host")
                return

            manifest_path = candidate.manifest_path()
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            document["objects"]["charities-may-operate"]["relative_path"] = "alias.csv"
            manifest_path.write_text(json.dumps(document), encoding="utf-8")

            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["candidate.invalid_mapping"])

    def test_resource_ceiling_breach_rejected_while_staging(self) -> None:
        tiny_contract = _tiny_ceiling_contract(
            max_compressed_payload_bytes=10, max_decompressed_payload_bytes=10
        )
        with tempfile.TemporaryDirectory() as staging_dir:
            with self.assertRaises(CandidateError) as ctx:
                resolve_and_stage_candidate(BASELINE_CANDIDATE_ROOT, Path(staging_dir), tiny_contract)
            self.assertEqual(ctx.exception.code, "container.open_failed")

    def test_physical_line_ceiling_breach_rejected_while_staging(self) -> None:
        tiny_contract = _tiny_ceiling_contract(max_physical_line_bytes=5)
        with tempfile.TemporaryDirectory() as staging_dir:
            with self.assertRaises(CandidateError) as ctx:
                resolve_and_stage_candidate(BASELINE_CANDIDATE_ROOT, Path(staging_dir), tiny_contract)
            self.assertEqual(ctx.exception.code, "container.open_failed")

    def test_store_inside_git_worktree_rejected_as_operational_error(self) -> None:
        with tempfile.TemporaryDirectory() as fake_repo:
            (Path(fake_repo) / ".git").mkdir()
            fake_store = Path(fake_repo) / "store"
            fake_store.mkdir()

            result = admit(BASELINE_CANDIDATE_ROOT, fake_store)

            self.assertEqual(result.status, "operational_error")
            # No store layout, staging, or attempt trail is ever created
            # inside the rejected worktree location.
            self.assertEqual(list(fake_store.iterdir()), [])

    def test_in_repository_candidate_outside_fixture_prefix_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as fake_repo:
            (Path(fake_repo) / ".git").mkdir()
            disallowed_candidate_dir = Path(fake_repo) / "somewhere-else"
            disallowed_candidate_dir.mkdir()
            for filename in (*fb.LOGICAL_LIST_FILES.values(), fb.MANIFEST_FILENAME):
                (disallowed_candidate_dir / filename).write_bytes(
                    (BASELINE_CANDIDATE_ROOT / filename).read_bytes()
                )

            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(disallowed_candidate_dir, Path(store_dir))

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["candidate.invalid_mapping"])


class FailureInjectionTests(unittest.TestCase):
    """No visible partial data survives an injected parser, Parquet, or
    store failure (D-07 whole-set rejection).
    """

    def test_injected_parser_failure_exposes_no_partial_canonical_output(self) -> None:
        real_parse_payload = admission_module.parse_payload

        def _fail_second_list(payload: bytes, logical_list: str, contract):
            if logical_list == "charities-not-operating":
                raise StructuralReject("parse.decode_failed", logical_list=logical_list)
            return real_parse_payload(payload, logical_list, contract)

        with mock.patch.object(admission_module, "parse_payload", side_effect=_fail_second_list):
            with tempfile.TemporaryDirectory() as store_dir:
                store_root = Path(store_dir)
                result = admit(BASELINE_CANDIDATE_ROOT, store_root)

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["parse.decode_failed"])
                self.assertEqual(list((store_root / "releases").iterdir()), [])
                self.assertEqual(_staging_leftovers(store_root), [])

    def test_injected_parquet_failure_exposes_no_partial_canonical_output(self) -> None:
        real_write_parquet = admission_module.write_parquet
        call_count = {"n": 0}

        def _fail_second_write(parsed, staging_dir, destination_path, contract):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise CanonicalSerializationError(
                    "canonical.serialization_failed", logical_list=parsed.logical_list
                )
            return real_write_parquet(parsed, staging_dir, destination_path, contract)

        with mock.patch.object(admission_module, "write_parquet", side_effect=_fail_second_write):
            with tempfile.TemporaryDirectory() as store_dir:
                store_root = Path(store_dir)
                result = admit(BASELINE_CANDIDATE_ROOT, store_root)

                self.assertEqual(result.status, "rejected")
                self.assertEqual(_reason_codes(result), ["canonical.serialization_failed"])
                self.assertEqual(list((store_root / "releases").iterdir()), [])
                self.assertEqual(_staging_leftovers(store_root), [])

    def test_injected_store_failure_returns_operational_error_and_publishes_nothing(self) -> None:
        with mock.patch.object(
            admission_module, "commit_revision", side_effect=StoreError("store.busy")
        ):
            with tempfile.TemporaryDirectory() as store_dir:
                store_root = Path(store_dir)
                result = admit(BASELINE_CANDIDATE_ROOT, store_root)

                self.assertEqual(result.status, "operational_error")
                self.assertEqual(result.reasons, ())
                self.assertFalse((store_root / "promoted-releases.json").exists())
                self.assertEqual(list((store_root / "releases").iterdir()), [])


class StatusVocabularyEnforcementTests(unittest.TestCase):
    """`admit()`'s optional `status_contract` parameter (04-01-PLAN.md
    D-02/D-22): omitted by default so every existing caller (this
    baseline fixture included) keeps its exact prior behavior; when a
    caller opts in, an unknown nonblank `Registry Status` rejects the
    whole candidate set through the same non-echoing reason path as every
    other structural rule, while blank status remains admitted.

    The committed baseline fixture (`tests/fixtures/landing/valid/*.csv`)
    predates the closed 33-value vocabulary and legitimately carries
    non-compliant placeholder values ("Active", "Reporting Incomplete") --
    exactly the "unreviewed nonblank status" case this contract exists to
    catch. `test_admit_without_status_contract_keeps_prior_behavior` and
    `test_admit_with_status_contract_rejects_the_unreviewed_placeholder_values`
    both rely on this rather than mutating the fixture.
    """

    def test_admit_without_status_contract_keeps_prior_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            result = admit(BASELINE_CANDIDATE_ROOT, Path(store_dir))
            self.assertEqual(result.status, "accepted")
            self.assertEqual(result.reasons, ())

    def test_admit_with_status_contract_rejects_the_unreviewed_placeholder_values(self) -> None:
        contract = load_default_status_contract()
        with tempfile.TemporaryDirectory() as store_dir:
            result = admit(BASELINE_CANDIDATE_ROOT, Path(store_dir), status_contract=contract)

            self.assertEqual(result.status, "rejected")
            self.assertEqual(_reason_codes(result), ["set.unknown_registry_status"] * len(result.reasons))
            self.assertGreater(len(result.reasons), 0)
            for reason in result.reasons:
                self.assertIn(reason.logical_list, LOGICAL_LIST_ORDER)
                self.assertIsNotNone(reason.safe_count)
                self.assertGreater(reason.safe_count, 0)
                self.assertIsNone(reason.safe_line_number)
            self.assertFalse((Path(store_dir) / "releases").exists() and any((Path(store_dir) / "releases").iterdir()))

    def test_admit_with_status_contract_accepts_a_fully_compliant_candidate(self) -> None:
        with fb.mutated_candidate() as candidate:
            candidate.replace_field("charities-may-operate", 0, 0, "Current")
            candidate.replace_field("charities-may-operate", 1, 0, "Current")
            candidate.replace_field("charities-may-operate", 2, 0, "Current")
            candidate.replace_field("charities-undetermined-status", 0, 0, "Not Registered")
            candidate.replace_field("charities-undetermined-status", 1, 0, "Not Registered")
            _recompute_content_length(candidate.root)

            contract = load_default_status_contract()
            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir), status_contract=contract)

                self.assertEqual(result.status, "accepted")
                self.assertEqual(result.reasons, ())

    def test_admit_with_status_contract_still_admits_blank_status_row(self) -> None:
        # The committed baseline's "Unregistered Outreach Group" row has a
        # blank registration key but a nonblank "Active" status; swap it to
        # blank status instead so this test isolates the blank-status
        # exemption specifically, independent of the unrelated placeholder
        # rejections proven above.
        with fb.mutated_candidate() as candidate:
            candidate.replace_field("charities-may-operate", 0, 0, "Current")
            candidate.replace_field("charities-may-operate", 1, 0, "Current")
            candidate.replace_field("charities-may-operate", 2, 0, "")
            candidate.replace_field("charities-undetermined-status", 0, 0, "Not Registered")
            candidate.replace_field("charities-undetermined-status", 1, 0, "Not Registered")
            _recompute_content_length(candidate.root)

            contract = load_default_status_contract()
            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir), status_contract=contract)

                self.assertEqual(result.status, "accepted")
                self.assertEqual(result.reasons, ())

    def test_status_contract_rejection_never_echoes_the_unknown_value(self) -> None:
        contract = load_default_status_contract()
        with tempfile.TemporaryDirectory() as store_dir:
            result = admit(BASELINE_CANDIDATE_ROOT, Path(store_dir), status_contract=contract)

            combined = result.to_json() + result.render_status()
            for reason in result.reasons:
                combined += json.dumps(reason.to_dict())

            self.assertNotIn("Active", combined)
            self.assertNotIn("Reporting Incomplete", combined)


class NonEchoTests(unittest.TestCase):
    """No synthetic sentinel value ever survives into rendered admission
    output, across the JSON result, the human status line, or the durable
    attempt trail (D-05/D-10).
    """

    def test_duplicate_key_sentinel_never_echoed(self) -> None:
        with fb.duplicate_key_within_list() as candidate:
            _recompute_content_length(candidate.root)
            with tempfile.TemporaryDirectory() as store_dir:
                store_root = Path(store_dir)
                result = admit(candidate.root, store_root)

                combined = result.to_json() + result.render_status()
                for reason in result.reasons:
                    combined += json.dumps(reason.to_dict())

                for marker in SYNTHETIC_PRIVATE_MARKERS:
                    self.assertNotIn(marker, combined)

                attempts_dir = store_root / "attempts"
                for attempt_path in attempts_dir.glob("*.json"):
                    attempt_text = attempt_path.read_text(encoding="utf-8")
                    for marker in SYNTHETIC_PRIVATE_MARKERS:
                        self.assertNotIn(marker, attempt_text)

    def test_unknown_registration_family_sentinel_never_echoed(self) -> None:
        with fb.unknown_registration_family() as candidate:
            _recompute_content_length(candidate.root)
            with tempfile.TemporaryDirectory() as store_dir:
                result = admit(candidate.root, Path(store_dir))

                combined = result.to_json() + result.render_status()
                for marker in SYNTHETIC_PRIVATE_MARKERS:
                    self.assertNotIn(marker, combined)


if __name__ == "__main__":
    unittest.main()
