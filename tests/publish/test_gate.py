"""Behavior tests for the fail-closed publication gate (07-04)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import unittest
from pathlib import Path

from calico_publish.allowlist import load_allowlist
from calico_publish.gate import (
    GATE_ERROR_CATEGORIES,
    GATE_VIOLATION_CATEGORIES,
    GateError,
    GateViolation,
    verify,
)
from calico_publish.manifest import validate_published_manifest_document
from tests.fixtures.publish.fixture_builder import (
    BASELINE_DIR,
    FixtureBuilderError,
    extra_unapproved_column,
    grain_uniqueness_break,
    identity_column_in_aggregate,
    manifest_hash_disagreement,
    mutated_publication,
    removed_approved_column,
    reordered_column_list,
)

_MANIFEST = Path("manifest") / "published-manifest-v1.json"
_FIXTURE_SOURCE_LISTS = ("synthetic_source",)


def _verify(root: Path):
    return verify(
        root,
        load_allowlist(root / "publication-exports-v1.json"),
        root / _MANIFEST,
        source_lists=_FIXTURE_SOURCE_LISTS,
    )


def _categories(root: Path) -> list[str]:
    return [item.category for item in _verify(root).violations]


class PublicationGateFixtureTests(unittest.TestCase):
    def test_01_committed_baseline_loads_and_passes(self) -> None:
        allowlist = load_allowlist(BASELINE_DIR / "publication-exports-v1.json")
        document = json.loads((BASELINE_DIR / _MANIFEST).read_text(encoding="utf-8"))
        validate_published_manifest_document(
            document, allowlist=allowlist, source_lists=_FIXTURE_SOURCE_LISTS
        )
        self.assertEqual(
            verify(
                BASELINE_DIR,
                allowlist,
                BASELINE_DIR / _MANIFEST,
                source_lists=_FIXTURE_SOURCE_LISTS,
            ).violations,
            (),
        )

    def test_committed_csv_bytes_are_utf8_lf_without_bom(self) -> None:
        for path in sorted((BASELINE_DIR / "exports").glob("*.csv")):
            payload = path.read_bytes()
            self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r", payload)
            payload.decode("utf-8")

    def test_mutation_root_is_removed_and_baseline_is_unchanged(self) -> None:
        before = {
            path.relative_to(BASELINE_DIR): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in BASELINE_DIR.rglob("*")
            if path.is_file()
        }
        with extra_unapproved_column() as publication:
            temporary_root = publication.root
            self.assertTrue(temporary_root.is_dir())
        self.assertFalse(temporary_root.exists())
        after = {
            path.relative_to(BASELINE_DIR): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in BASELINE_DIR.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_path_escape_is_rejected_with_fixed_category(self) -> None:
        with mutated_publication() as publication:
            with self.assertRaises(FixtureBuilderError) as caught:
                publication.write_bytes(Path("..") / "outside", b"safe")
        self.assertEqual(caught.exception.category, "mutation.path_outside_owned_root")

    def test_extra_unapproved_column_is_rejected_and_named(self) -> None:
        with extra_unapproved_column() as publication:
            violations = _verify(publication.root).violations
        self.assertEqual(
            violations,
            (GateViolation("fixture_named_history", "gate.column_set_mismatch", "unapproved_field"),),
        )

    def test_removed_approved_column_is_rejected_and_named(self) -> None:
        with removed_approved_column() as publication:
            violations = _verify(publication.root).violations
        self.assertIn(
            GateViolation("fixture_named_history", "gate.column_set_mismatch", "observation_state"),
            violations,
        )

    def test_reordered_column_list_is_rejected(self) -> None:
        with reordered_column_list() as publication:
            self.assertEqual(_categories(publication.root), ["gate.column_order_mismatch"])

    def test_grain_uniqueness_break_is_rejected(self) -> None:
        with grain_uniqueness_break() as publication:
            self.assertEqual(_categories(publication.root), ["gate.grain_not_unique"])

    def test_identity_column_in_aggregate_is_rejected(self) -> None:
        with identity_column_in_aggregate() as publication:
            self.assertIn("gate.identity_column_in_aggregate", _categories(publication.root))

    def test_manifest_hash_disagreement_is_rejected(self) -> None:
        with manifest_hash_disagreement() as publication:
            self.assertEqual(
                _categories(publication.root), ["gate.manifest_export_hash_mismatch"]
            )


class PublicationGateContentTests(unittest.TestCase):
    def test_violation_vocabularies_are_exact_and_disjoint(self) -> None:
        self.assertEqual(
            GATE_VIOLATION_CATEGORIES,
            frozenset(
                {
                    "gate.column_set_mismatch",
                    "gate.column_order_mismatch",
                    "gate.row_arity_mismatch",
                    "gate.grain_not_unique",
                    "gate.identity_column_in_aggregate",
                    "gate.manifest_export_hash_mismatch",
                    "gate.manifest_row_count_mismatch",
                    "gate.manifest_export_set_mismatch",
                }
            ),
        )
        self.assertEqual(
            GATE_ERROR_CATEGORIES,
            frozenset(
                {
                    "gate.export_file_missing",
                    "gate.unexpected_export_file",
                    "gate.export_empty_file",
                    "gate.export_invalid_encoding",
                    "gate.export_byte_order_mark",
                    "gate.export_carriage_return",
                    "gate.manifest_invalid_schema",
                    "gate.manifest_not_found",
                }
            ),
        )
        self.assertTrue(GATE_VIOLATION_CATEGORIES.isdisjoint(GATE_ERROR_CATEGORIES))

    def test_content_violations_accumulate_and_are_sorted(self) -> None:
        with mutated_publication() as publication:
            aggregate = Path("exports") / "fixture_aggregate_metrics.csv"
            rows = publication._read_rows(aggregate)
            rows[0].append("unapproved_field")
            for row in rows[1:]:
                row.append("synthetic_extra")
            publication._write_rows(aggregate, rows)
            publication.refresh_export_record("fixture_aggregate_metrics")

            named = Path("exports") / "fixture_named_history.csv"
            rows = publication._read_rows(named)
            rows[2][0:2] = rows[1][0:2]
            publication._write_rows(named, rows)
            publication.refresh_export_record("fixture_named_history")
            result = _verify(publication.root)
        self.assertEqual(len(result.violations), 2)
        self.assertEqual(
            list(result.violations),
            sorted(
                result.violations,
                key=lambda item: (item.export_name, item.category, item.column_name or ""),
            ),
        )

    def test_header_only_export_passes(self) -> None:
        with mutated_publication() as publication:
            relative = Path("exports") / "fixture_named_history.csv"
            rows = publication._read_rows(relative)
            publication._write_rows(relative, [rows[0]])
            publication.refresh_export_record("fixture_named_history")
            self.assertTrue(_verify(publication.root).passed)

    def test_manifest_row_count_disagreement_is_rejected(self) -> None:
        with mutated_publication() as publication:
            publication.replace_manifest_field("fixture_named_history", "row_count", 99)
            self.assertEqual(_categories(publication.root), ["gate.manifest_row_count_mismatch"])

    def test_manifest_export_set_disagreement_is_rejected(self) -> None:
        with mutated_publication() as publication:
            document = publication.read_manifest()
            document["exports"] = document["exports"][:1]
            publication.write_manifest(document)
            self.assertEqual(_categories(publication.root), ["gate.manifest_export_set_mismatch"])

    def test_gate_is_idempotent_and_does_not_mutate_bytes(self) -> None:
        before = {
            path.relative_to(BASELINE_DIR): path.read_bytes()
            for path in BASELINE_DIR.rglob("*")
            if path.is_file()
        }
        allowlist = load_allowlist(BASELINE_DIR / "publication-exports-v1.json")
        first = verify(
            BASELINE_DIR,
            allowlist,
            BASELINE_DIR / _MANIFEST,
            source_lists=_FIXTURE_SOURCE_LISTS,
        )
        second = verify(
            BASELINE_DIR,
            allowlist,
            BASELINE_DIR / _MANIFEST,
            source_lists=_FIXTURE_SOURCE_LISTS,
        )
        after = {
            path.relative_to(BASELINE_DIR): path.read_bytes()
            for path in BASELINE_DIR.rglob("*")
            if path.is_file()
        }
        self.assertEqual(first, second)
        self.assertEqual(before, after)

    def test_rendered_violations_never_contain_fixture_cell_values(self) -> None:
        values: set[str] = set()
        for path in sorted((BASELINE_DIR / "exports").glob("*.csv")):
            rows = csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline=""))
            for row in list(rows)[1:]:
                values.update(value for value in row if value)
        with grain_uniqueness_break() as publication:
            rendered = "\n".join(item.render() for item in _verify(publication.root).violations)
        for value in values:
            self.assertNotIn(value, rendered)

    def test_render_sanitizes_an_untrusted_header_locator(self) -> None:
        token = "unsafe" + ":/locator"
        rendered = GateViolation(
            "fixture_named_history", "gate.column_set_mismatch", token
        ).render()
        self.assertNotIn(token, rendered)
        self.assertEqual(rendered, "fixture_named_history:-: gate.column_set_mismatch")


class PublicationGateBoundaryTests(unittest.TestCase):
    def _assert_error(self, expected: str, mutator) -> None:
        with mutated_publication() as publication:
            mutator(publication)
            with self.assertRaises(GateError) as caught:
                _verify(publication.root)
        self.assertEqual(caught.exception.category, expected)

    def test_missing_export_file_fails_closed(self) -> None:
        self._assert_error(
            "gate.export_file_missing",
            lambda publication: publication._contained(
                Path("exports") / "fixture_named_history.csv"
            ).unlink(),
        )

    def test_unexpected_export_file_fails_closed(self) -> None:
        self._assert_error(
            "gate.unexpected_export_file",
            lambda publication: publication.write_bytes(
                Path("exports") / "unexpected.csv", b"safe_header\n"
            ),
        )

    def test_nonfile_export_entry_fails_closed(self) -> None:
        def mutate(publication) -> None:
            publication._contained(Path("exports") / "nested").mkdir()

        self._assert_error("gate.unexpected_export_file", mutate)

    def test_zero_byte_export_fails_closed(self) -> None:
        self._assert_error(
            "gate.export_empty_file",
            lambda publication: publication.write_bytes(
                Path("exports") / "fixture_named_history.csv", b""
            ),
        )

    def test_invalid_utf8_export_fails_closed(self) -> None:
        self._assert_error(
            "gate.export_invalid_encoding",
            lambda publication: publication.write_bytes(
                Path("exports") / "fixture_named_history.csv", bytes((255, 254))
            ),
        )

    def test_malformed_csv_fails_closed(self) -> None:
        self._assert_error(
            "gate.export_invalid_encoding",
            lambda publication: publication.write_bytes(
                Path("exports") / "fixture_named_history.csv", b'"unterminated\n'
            ),
        )

    def test_byte_order_mark_fails_closed(self) -> None:
        def mutate(publication) -> None:
            relative = Path("exports") / "fixture_named_history.csv"
            publication.write_bytes(relative, b"\xef\xbb\xbf" + publication.read_bytes(relative))

        self._assert_error("gate.export_byte_order_mark", mutate)

    def test_carriage_return_fails_closed(self) -> None:
        def mutate(publication) -> None:
            relative = Path("exports") / "fixture_named_history.csv"
            publication.write_bytes(relative, publication.read_bytes(relative).replace(b"\n", b"\r\n"))

        self._assert_error("gate.export_carriage_return", mutate)

    def test_invalid_manifest_schema_fails_closed(self) -> None:
        def mutate(publication) -> None:
            document = publication.read_manifest()
            document["unexpected"] = True
            publication.write_manifest(document)

        self._assert_error("gate.manifest_invalid_schema", mutate)

    def test_manifest_file_identity_mismatch_fails_closed(self) -> None:
        def mutate(publication) -> None:
            publication.replace_manifest_field(
                "fixture_named_history", "file_name", "fixture_aggregate_metrics.csv"
            )

        self._assert_error("gate.manifest_invalid_schema", mutate)

    def test_manifest_grain_identity_mismatch_fails_closed(self) -> None:
        def mutate(publication) -> None:
            publication.replace_manifest_field(
                "fixture_named_history", "grain", ["registration_key"]
            )

        self._assert_error("gate.manifest_invalid_schema", mutate)

    def test_missing_manifest_fails_closed(self) -> None:
        with mutated_publication() as publication:
            with self.assertRaises(GateError) as caught:
                verify(
                    publication.root,
                    load_allowlist(publication.allowlist_path),
                    publication.root / "manifest" / "missing.json",
                )
        self.assertEqual(caught.exception.category, "gate.manifest_not_found")

    def test_row_arity_mismatch_is_a_content_violation(self) -> None:
        with mutated_publication() as publication:
            relative = Path("exports") / "fixture_named_history.csv"
            publication.append_row(relative, ["synthetic_short"])
            publication.refresh_export_record("fixture_named_history")
            self.assertEqual(_categories(publication.root), ["gate.row_arity_mismatch"])


if __name__ == "__main__":
    unittest.main()
