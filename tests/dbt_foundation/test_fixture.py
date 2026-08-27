"""Contract tests for the Gate B multi-date/multi-revision fixture (D-01,
D-14, D-16).

Exercises the closed `gate-b-fixture-v1.json` scenario shape, the four
locked defect-shape counts, the identity-free/excluded-field guarantees,
real admission through `calico_landing.admission.admit()` for every
revision, pointer-variant selection, and the fixture builder's own closed
validation -- unknown top-level keys, oversized declared rows, an excluded
field carrying a nonblank value, an unapproved registration family, an
unknown pointer variant, and a write attempted outside an owned temporary
root all fail before any candidate is ever materialized (T-03-05).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from calico_landing.contracts import LOGICAL_LIST_ORDER
from calico_landing.store import read_promoted_releases

from tests.fixtures.dbt_foundation import fixture_builder as gb

_REAL_ADMITTED_DATES = frozenset({"2026-07-15", "2026-08-05", "2026-08-19"})


class FixtureSpecShapeTests(unittest.TestCase):
    """The committed scenario document carries exactly three dates, four
    revisions, and one middle date with two distinctly labeled pointer
    variants (D-01).
    """

    def test_spec_loads_with_three_dates_and_four_revisions(self) -> None:
        spec = gb.load_gate_b_fixture_spec()
        self.assertEqual(len(spec.as_of_dates), 3)
        self.assertEqual(len(spec.revisions), 4)

    def test_middle_date_has_exactly_two_revisions_with_distinct_pointer_variants(self) -> None:
        spec = gb.load_gate_b_fixture_spec()
        middle_revisions = [r for r in spec.revisions if r.as_of_date == spec.middle_as_of_date]
        self.assertEqual(len(middle_revisions), 2)
        variants = {r.pointer_variant for r in middle_revisions}
        self.assertEqual(len(variants), 2)
        self.assertNotIn(None, variants)
        self.assertEqual(set(spec.middle_revision_labels), variants)

    def test_non_middle_dates_have_exactly_one_revision_and_no_pointer_variant(self) -> None:
        spec = gb.load_gate_b_fixture_spec()
        for as_of_date in spec.as_of_dates:
            if as_of_date == spec.middle_as_of_date:
                continue
            matching = [r for r in spec.revisions if r.as_of_date == as_of_date]
            self.assertEqual(len(matching), 1)
            self.assertIsNone(matching[0].pointer_variant)

    def test_synthetic_dates_never_collide_with_real_admitted_dates(self) -> None:
        spec = gb.load_gate_b_fixture_spec()
        self.assertEqual(set(spec.as_of_dates) & _REAL_ADMITTED_DATES, set())


class DefectShapeAndPrivacyTests(unittest.TestCase):
    """All four locked D-01 defect shapes are present, excluded fields stay
    blank, every nonblank registration key is an approved family, and every
    identity is invented.
    """

    def test_all_four_required_defect_shapes_present(self) -> None:
        spec = gb.load_gate_b_fixture_spec()
        counts = gb.defect_shape_counts(spec)
        for shape in ("universal_padding", "unmatched_quote", "blank_registration", "blank_status"):
            self.assertGreater(counts[shape], 0, f"missing required defect shape: {shape}")

    def test_excluded_fields_stay_blank_and_registration_keys_are_approved_or_blank(self) -> None:
        spec = gb.load_gate_b_fixture_spec()
        for revision in spec.revisions:
            for logical_list in LOGICAL_LIST_ORDER:
                for record in revision.records[logical_list]:
                    self.assertEqual(record["FEIN"].strip(), "")
                    self.assertEqual(record["SOS/FTB#"].strip(), "")
                    reg_value = record["State Charity Reg#"].strip()
                    if reg_value:
                        self.assertTrue(
                            gb.is_approved_registration_family(reg_value),
                            f"unapproved registration family in {logical_list}: {reg_value!r}",
                        )

    def test_no_real_organization_name_or_fein_shaped_value_present(self) -> None:
        spec = gb.load_gate_b_fixture_spec()
        for revision in spec.revisions:
            for logical_list in LOGICAL_LIST_ORDER:
                for record in revision.records[logical_list]:
                    self.assertIn("Fixture", record["Name"])


class AdmissionThroughPhaseTwoTests(unittest.TestCase):
    """Every generated candidate is admitted through the real
    `calico_landing.admission.admit()` boundary, producing the expected
    3-date/4-revision store with four canonical Parquet objects per
    revision, and no admitted content survives context-manager cleanup.
    """

    def test_default_pointer_variant_admits_all_four_revisions(self) -> None:
        spec = gb.load_gate_b_fixture_spec()
        with gb.gate_b_fixture_store() as store:
            self.assertEqual(len(store.admissions), 4)
            for admission in store.admissions:
                self.assertEqual(admission.result.status, "accepted")
                self.assertEqual(admission.result.reasons, ())

            promoted = read_promoted_releases(store.store_root)
            self.assertEqual(set(promoted.keys()), set(spec.as_of_dates))

            for as_of_date in spec.as_of_dates:
                revision_dirs = list((store.store_root / "releases" / as_of_date).iterdir())
                expected_count = 2 if as_of_date == spec.middle_as_of_date else 1
                self.assertEqual(len(revision_dirs), expected_count)
                for revision_dir in revision_dirs:
                    canonical_files = list((revision_dir / "canonical").glob("*.parquet"))
                    raw_files = list((revision_dir / "raw").glob("*.csv"))
                    self.assertEqual(len(canonical_files), 4)
                    self.assertEqual(len(raw_files), 4)

            expected_pointer_label = spec.middle_revision_labels[-1]
            expected_admission = next(
                a for a in store.admissions if a.revision_label == expected_pointer_label
            )
            middle_promotion = promoted[spec.middle_as_of_date]
            self.assertEqual(
                middle_promotion.release_revision, expected_admission.result.release_revision
            )
            self.assertEqual(
                middle_promotion.revision_fingerprint, expected_admission.result.revision_fingerprint
            )

    def test_explicit_pointer_variant_selects_the_named_middle_revision(self) -> None:
        spec = gb.load_gate_b_fixture_spec()
        other_variant = spec.middle_revision_labels[0]
        with gb.gate_b_fixture_store(pointer_variant=other_variant) as store:
            promoted = read_promoted_releases(store.store_root)
            middle_promotion = promoted[spec.middle_as_of_date]
            expected_admission = next(
                a for a in store.admissions if a.revision_label == other_variant
            )
            self.assertEqual(
                middle_promotion.release_revision, expected_admission.result.release_revision
            )
            self.assertEqual(store.pointer_variant, other_variant)

    def test_unmatched_quote_survives_admission_without_row_fusion(self) -> None:
        spec = gb.load_gate_b_fixture_spec()
        with gb.gate_b_fixture_store() as store:
            for admission in store.admissions:
                revision = next(
                    r for r in spec.revisions if r.revision_label == admission.revision_label
                )
                declared_row_count = sum(len(rows) for rows in revision.records.values())

                revision_dir = next(
                    d
                    for d in (store.store_root / "releases" / revision.as_of_date).iterdir()
                    if d.name.startswith(f"rev-{admission.result.release_revision:04d}-")
                )
                manifest = json.loads((revision_dir / "manifest.json").read_text(encoding="utf-8"))
                total_parsed = sum(
                    entry["parsed_record_count"]
                    for entry in manifest["metadata"]["logical_lists"].values()
                )
                self.assertEqual(total_parsed, declared_row_count)

    def test_no_generated_row_bearing_path_survives_context_manager_cleanup(self) -> None:
        with gb.gate_b_fixture_store() as store:
            store_root = store.store_root
            self.assertTrue(store_root.exists())
        self.assertFalse(store_root.exists())

    def test_admitted_store_never_resolves_inside_a_git_worktree(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        with gb.gate_b_fixture_store() as store:
            with self.assertRaises(ValueError):
                store.store_root.relative_to(repo_root)


class ClosedValidationTests(unittest.TestCase):
    """Malformed, oversized, or excluded-value-carrying scenario documents
    fail before any candidate is ever materialized (T-03-05).
    """

    def _valid_document(self) -> dict:
        return json.loads(gb.FIXTURE_SPEC_PATH.read_text(encoding="utf-8"))

    def _write_and_load(self, tmp_path: Path, document: dict) -> None:
        spec_path = tmp_path / "malformed.json"
        spec_path.write_text(json.dumps(document), encoding="utf-8")
        gb.load_gate_b_fixture_spec(spec_path)

    def test_unknown_top_level_key_rejected(self) -> None:
        document = self._valid_document()
        document["unexpected_key"] = True
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(gb.GateBFixtureError) as ctx:
                self._write_and_load(Path(tmp_dir), document)
            self.assertEqual(ctx.exception.category, "fixture.invalid_spec_schema")

    def test_excessive_revision_count_rejected(self) -> None:
        document = self._valid_document()
        extra_revision = json.loads(json.dumps(document["revisions"][0]))
        extra_revision["revision_label"] = "extra-revision"
        extra_revision["pointer_variant"] = None
        document["revisions"].append(extra_revision)
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(gb.GateBFixtureError) as ctx:
                self._write_and_load(Path(tmp_dir), document)
            self.assertEqual(ctx.exception.category, "fixture.invalid_revision_count")

    def test_row_count_over_declared_cap_rejected(self) -> None:
        document = self._valid_document()
        logical_list = LOGICAL_LIST_ORDER[0]
        base_row = document["revisions"][0]["records"][logical_list][0]
        over_cap = document["max_rows_per_logical_list"] + 1
        document["revisions"][0]["records"][logical_list] = [dict(base_row) for _ in range(over_cap)]
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(gb.GateBFixtureError) as ctx:
                self._write_and_load(Path(tmp_dir), document)
            self.assertEqual(ctx.exception.category, "fixture.row_count_exceeded")

    def test_nonblank_excluded_field_rejected(self) -> None:
        document = self._valid_document()
        logical_list = LOGICAL_LIST_ORDER[0]
        document["revisions"][0]["records"][logical_list][0]["FEIN"] = "12" + "3456789"
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(gb.GateBFixtureError) as ctx:
                self._write_and_load(Path(tmp_dir), document)
            self.assertEqual(ctx.exception.category, "fixture.excluded_field_nonblank")

    def test_unapproved_registration_family_rejected(self) -> None:
        document = self._valid_document()
        logical_list = LOGICAL_LIST_ORDER[0]
        document["revisions"][0]["records"][logical_list][0]["State Charity Reg#"] = "ZZ12345"
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(gb.GateBFixtureError) as ctx:
                self._write_and_load(Path(tmp_dir), document)
            self.assertEqual(ctx.exception.category, "fixture.invalid_registration_family")

    def test_date_mismatch_within_revision_rejected(self) -> None:
        document = self._valid_document()
        logical_list = LOGICAL_LIST_ORDER[0]
        document["revisions"][0]["records"][logical_list][0]["As-of Date"] = "1999-01-01"
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(gb.GateBFixtureError) as ctx:
                self._write_and_load(Path(tmp_dir), document)
            self.assertEqual(ctx.exception.category, "fixture.date_mismatch")

    def test_missing_defect_shape_rejected(self) -> None:
        document = self._valid_document()
        # Blank out the one row carrying the unmatched-quote defect so the
        # collective count drops to zero.
        document["revisions"][1]["records"]["charities-may-operate"][0]["Name"] = (
            "Gate B Fixture Org NoQuote"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(gb.GateBFixtureError) as ctx:
                self._write_and_load(Path(tmp_dir), document)
            self.assertEqual(ctx.exception.category, "fixture.missing_required_defect_shape")

    def test_path_outside_owned_temp_root_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            owned_root = Path(tmp_dir)
            with self.assertRaises(gb.GateBFixtureError) as ctx:
                gb._contained(owned_root, "../escape.csv")
            self.assertEqual(ctx.exception.category, "fixture.path_outside_owned_root")

    def test_unknown_pointer_variant_rejected(self) -> None:
        with self.assertRaises(gb.GateBFixtureError) as ctx:
            with gb.gate_b_fixture_store(pointer_variant="not-a-real-label"):
                pass  # pragma: no cover -- must never be entered
        self.assertEqual(ctx.exception.category, "fixture.unknown_pointer_variant")


if __name__ == "__main__":
    unittest.main()
