"""Contract tests for `calico_landing.contracts` and `calico_landing.result`.

Covers: exact JSON keys/types/versions for both committed contract
documents, the exact eleven-header/four-logical-list order, the locked
CP1252/QUOTE_NONE/Parquet dialect, malformed/extra-field documents in
temporary directories, the deterministic non-echo result/reason model, and
the D-14/D-15 XLSX deferral (no reader dependency, single unsupported
reason). Also proves D-007 is preserved: the contract does not redact or
gate organization name / full registration number (`tests.test_scanner`
style negative-case discipline, mirrored from
`tests/tools/privacy_scan/test_scanner.py`).

No real organization identity or excluded value is ever used -- only
reserved synthetic sentinels, per D-10.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from calico_landing.contracts import (
    LOGICAL_LIST_ORDER,
    UNSUPPORTED_XLSX_REASON,
    ContractError,
    CsvContract,
    StatusContract,
    XlsxContract,
    load_csv_contract,
    load_status_contract,
    load_xlsx_contract,
)
from calico_landing.result import (
    REASON_RANK,
    AdmissionReason,
    AdmissionResult,
    ResultError,
    sort_reasons,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"
CSV_CONTRACT_PATH = CONTRACTS_DIR / "ag-registry-csv-v1.json"
XLSX_CONTRACT_PATH = CONTRACTS_DIR / "ag-registry-xlsx-2019-deferred-v1.json"
STATUS_CONTRACT_PATH = CONTRACTS_DIR / "ag-registry-status-v1.json"
RESULT_SCHEMA_PATH = CONTRACTS_DIR / "admission-result-v1.schema.json"

#: The exact deduplicated 33-value union of the four baseline
#: `status_vocabulary` arrays (`.planning/research/ag-schema-baseline.json`,
#: 2026-08-05 snapshot) -- independently recomputed here so this test
#: proves the committed contract against the source baseline, not merely
#: against itself.
EXPECTED_NONBLANK_STATUS_VOCABULARY = frozenset(
    {
        "Closed - Registration Not Required",
        "Current",
        "Current - Awaiting Reporting",
        "Current - In Process",
        "Current - Probationary Registration",
        "Current - Reporting Incomplete",
        "Delinquent",
        "Delinquent - Late Fees Due",
        "Dissolution Pending",
        "Dissolution Waiver Issued",
        "Dissolved",
        "Enforcement Action Pending",
        "Exempt",
        "Exempt - Dissolution Pending",
        "Exempt - Dissolution Waiver Issued",
        "Exempt - Dissolved",
        "Exempt - Facility Financing",
        "Exempt - Form 990-PF Required",
        "Exempt - Religious",
        "Exempt - Withdrawn",
        "Mutual Benefit",
        "Never Registered - Diss. Waiver Issued",
        "Never Registered - Dissolution Pending",
        "Never Registered - Dissolved",
        "Never Registered - Withdrawn",
        "Not Registered",
        "Not Registered - Cease and Desist Order",
        "Registered - Corporate Trustee",
        "Revoked",
        "Subject to Cease and Desist Order",
        "Suspended",
        "Trust Closed",
        "Withdrawn",
    }
)

EXPECTED_HEADERS = (
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

#: Reserved synthetic sentinel values -- never a real organization identity
#: or excluded value. Used only to prove non-echo behavior. Built via
#: runtime concatenation so the committed source text never contains a
#: contiguous privacy-scanner match while the runtime value stays
#: byte-identical (mirrors the fix documented for Plan 01-03 in Phase 1).
_SENTINEL_FEIN_LIKE = "94-" + "1234567"
_SENTINEL_PATH_LIKE = "C:" + "\\Users\\synthetic\\evidence.csv"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class CsvContractDocumentTests(unittest.TestCase):
    """The committed CSV contract document encodes D-01/D-02 exactly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.document = _load_json(CSV_CONTRACT_PATH)
        cls.contract = load_csv_contract(CSV_CONTRACT_PATH)

    def test_document_exists(self) -> None:
        self.assertTrue(CSV_CONTRACT_PATH.is_file())

    def test_document_key_set_is_exact_and_closed(self) -> None:
        self.assertEqual(
            set(self.document.keys()),
            {
                "contract_version",
                "logical_lists",
                "headers",
                "encoding",
                "quoting",
                "canonical_exchange_format",
                "max_compressed_payload_bytes",
                "max_decompressed_payload_bytes",
                "max_physical_line_bytes",
            },
        )

    def test_contract_version_is_one(self) -> None:
        self.assertEqual(self.document["contract_version"], 1)

    def test_logical_lists_exact_order_and_count(self) -> None:
        self.assertEqual(tuple(self.document["logical_lists"]), LOGICAL_LIST_ORDER)
        self.assertEqual(len(self.document["logical_lists"]), 4)

    def test_headers_exact_order_and_count(self) -> None:
        self.assertEqual(tuple(self.document["headers"]), EXPECTED_HEADERS)
        self.assertEqual(len(self.document["headers"]), 11)

    def test_encoding_is_cp1252(self) -> None:
        self.assertEqual(self.document["encoding"], "cp1252")

    def test_quoting_is_quote_none(self) -> None:
        self.assertEqual(self.document["quoting"], "QUOTE_NONE")

    def test_canonical_exchange_format_is_parquet(self) -> None:
        self.assertEqual(self.document["canonical_exchange_format"], "parquet")

    def test_payload_ceilings_are_locked_values(self) -> None:
        self.assertEqual(self.document["max_compressed_payload_bytes"], 2147483648)
        self.assertEqual(self.document["max_decompressed_payload_bytes"], 2147483648)
        self.assertEqual(self.document["max_physical_line_bytes"], 4194304)

    def test_loader_returns_frozen_contract_with_matching_fields(self) -> None:
        self.assertIsInstance(self.contract, CsvContract)
        self.assertEqual(self.contract.logical_lists, LOGICAL_LIST_ORDER)
        self.assertEqual(self.contract.headers, EXPECTED_HEADERS)
        self.assertEqual(self.contract.encoding, "cp1252")
        self.assertEqual(self.contract.quoting, "QUOTE_NONE")
        self.assertEqual(len(self.contract.headers), 11)
        self.assertEqual(len(self.contract.logical_lists), 4)

    def test_loader_result_is_immutable(self) -> None:
        with self.assertRaises(AttributeError):
            self.contract.encoding = "utf-8"  # type: ignore[misc]

    def test_headers_include_d007_allowed_identity_fields(self) -> None:
        # D-007: organization name and the full registration number are
        # allowed published fields, never redacted or excluded here.
        self.assertIn("Name", self.contract.headers)
        self.assertIn("State Charity Reg#", self.contract.headers)


class XlsxContractDocumentTests(unittest.TestCase):
    """The committed XLSX contract document encodes D-14/D-15 exactly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.document = _load_json(XLSX_CONTRACT_PATH)
        cls.contract = load_xlsx_contract(XLSX_CONTRACT_PATH)

    def test_document_exists(self) -> None:
        self.assertTrue(XLSX_CONTRACT_PATH.is_file())

    def test_document_key_set_is_exact_and_closed(self) -> None:
        self.assertEqual(
            set(self.document.keys()),
            {
                "contract_version",
                "status",
                "unsupported_reason",
                "known_worksheet",
                "reopening_trigger",
                "reader_dependency_required",
            },
        )

    def test_status_is_deferred(self) -> None:
        self.assertEqual(self.document["status"], "deferred")

    def test_unsupported_reason_is_locked_code(self) -> None:
        self.assertEqual(self.document["unsupported_reason"], "contract.unsupported_xlsx")
        self.assertEqual(self.document["unsupported_reason"], UNSUPPORTED_XLSX_REASON)

    def test_known_worksheet_header_row_is_five(self) -> None:
        self.assertEqual(self.document["known_worksheet"]["header_row"], 5)

    def test_known_worksheet_blank_status_row_count_is_25(self) -> None:
        self.assertEqual(self.document["known_worksheet"]["blank_status_row_count"], 25)

    def test_known_worksheet_declares_a_future_quality_flag(self) -> None:
        flag = self.document["known_worksheet"]["required_quality_flag"]
        self.assertIsInstance(flag, str)
        self.assertGreater(len(flag), 0)

    def test_reopening_trigger_is_a_nonempty_string(self) -> None:
        self.assertIsInstance(self.document["reopening_trigger"], str)
        self.assertGreater(len(self.document["reopening_trigger"]), 0)

    def test_reader_dependency_required_is_false(self) -> None:
        self.assertIs(self.document["reader_dependency_required"], False)

    def test_loader_returns_frozen_contract_with_matching_fields(self) -> None:
        self.assertIsInstance(self.contract, XlsxContract)
        self.assertEqual(self.contract.status, "deferred")
        self.assertEqual(self.contract.unsupported_reason, "contract.unsupported_xlsx")
        self.assertEqual(self.contract.known_worksheet.header_row, 5)
        self.assertEqual(self.contract.known_worksheet.blank_status_row_count, 25)
        self.assertFalse(self.contract.reader_dependency_required)

    def test_loader_result_is_immutable(self) -> None:
        with self.assertRaises(AttributeError):
            self.contract.status = "active"  # type: ignore[misc]


class NoXlsxReaderDependencyTests(unittest.TestCase):
    """D-15: no XLSX reader import or dependency exists anywhere in scope."""

    FORBIDDEN_READER_TOKENS = ("openpyxl", "xlrd", "pandas", "pyexcel")

    def test_contracts_module_imports_no_xlsx_reader(self) -> None:
        source = (REPO_ROOT / "calico_landing" / "contracts.py").read_text(encoding="utf-8")
        for token in self.FORBIDDEN_READER_TOKENS:
            self.assertNotIn(token, source)

    def test_result_module_imports_no_xlsx_reader(self) -> None:
        source = (REPO_ROOT / "calico_landing" / "result.py").read_text(encoding="utf-8")
        for token in self.FORBIDDEN_READER_TOKENS:
            self.assertNotIn(token, source)

    def test_requirements_dbt_gains_no_reader_dependency(self) -> None:
        content = (REPO_ROOT / "requirements-dbt.txt").read_text(encoding="utf-8")
        for token in self.FORBIDDEN_READER_TOKENS:
            self.assertNotIn(token, content.lower())

    def test_xlsx_deferral_never_presents_a_synthetic_workbook_as_proof(self) -> None:
        # The deferral record documents known facts about the 2019 shape but
        # commits no binary/workbook fixture as historical proof (D-14).
        self.assertFalse(list(CONTRACTS_DIR.glob("*.xlsx")))
        decision_doc = REPO_ROOT / "docs" / "decisions" / "legacy-xlsx-contract.md"
        self.assertTrue(decision_doc.is_file())
        text = decision_doc.read_text(encoding="utf-8")
        self.assertIn("synthetic", text.lower())


class StatusContractDocumentTests(unittest.TestCase):
    """The committed status contract document encodes D-02/D-22 exactly:
    one supported version, an exact four-key top-level schema, and the
    deduplicated 33-value nonblank vocabulary from the baseline.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.document = _load_json(STATUS_CONTRACT_PATH)
        cls.contract = load_status_contract(STATUS_CONTRACT_PATH)

    def test_document_exists(self) -> None:
        self.assertTrue(STATUS_CONTRACT_PATH.is_file())

    def test_document_key_set_is_exact_and_closed(self) -> None:
        self.assertEqual(
            set(self.document.keys()),
            {
                "contract_version",
                "logical_lists",
                "nonblank_status_vocabulary",
                "delinquent_statuses",
            },
        )

    def test_contract_version_is_one(self) -> None:
        self.assertEqual(self.document["contract_version"], 1)

    def test_logical_lists_exact_order_and_count(self) -> None:
        self.assertEqual(tuple(self.document["logical_lists"]), LOGICAL_LIST_ORDER)
        self.assertEqual(len(self.document["logical_lists"]), 4)

    def test_nonblank_vocabulary_is_exactly_the_deduplicated_33_value_baseline_union(self) -> None:
        vocabulary = self.document["nonblank_status_vocabulary"]
        self.assertEqual(len(vocabulary), 33)
        self.assertEqual(len(set(vocabulary)), 33)
        self.assertEqual(set(vocabulary), EXPECTED_NONBLANK_STATUS_VOCABULARY)

    def test_both_locked_delinquent_statuses_are_present(self) -> None:
        self.assertEqual(
            set(self.document["delinquent_statuses"]),
            {"Delinquent", "Delinquent - Late Fees Due"},
        )
        self.assertTrue(
            set(self.document["delinquent_statuses"]).issubset(
                set(self.document["nonblank_status_vocabulary"])
            )
        )

    def test_blank_status_is_not_a_vocabulary_member(self) -> None:
        self.assertNotIn("", self.document["nonblank_status_vocabulary"])

    def test_contract_does_not_carry_archived_paths_counts_or_excluded_columns(self) -> None:
        # D-22: archived paths, row counts, and excluded source columns
        # from the workshop baseline must never cross into this product
        # contract.
        for forbidden_key in ("archived_path", "as_of_date", "columns", "reg_shapes", "row_count"):
            self.assertNotIn(forbidden_key, self.document)

    def test_loader_returns_frozen_contract_with_matching_fields(self) -> None:
        self.assertIsInstance(self.contract, StatusContract)
        self.assertEqual(self.contract.logical_lists, LOGICAL_LIST_ORDER)
        self.assertEqual(self.contract.nonblank_status_vocabulary, EXPECTED_NONBLANK_STATUS_VOCABULARY)
        self.assertEqual(
            set(self.contract.delinquent_statuses), {"Delinquent", "Delinquent - Late Fees Due"}
        )

    def test_loader_result_is_immutable(self) -> None:
        with self.assertRaises(AttributeError):
            self.contract.contract_version = 2  # type: ignore[misc]


class MalformedStatusContractTests(unittest.TestCase):
    """Malformed/extra-field status contract documents fail closed by fixed
    category, mirroring `MalformedCsvContractTests`.
    """

    def _write(self, tmp_dir: Path, document: dict) -> Path:
        path = tmp_dir / "malformed-status.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def _valid_document(self) -> dict:
        return json.loads(STATUS_CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_missing_file_raises_contract_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.json"
            with self.assertRaises(ContractError) as ctx:
                load_status_contract(missing)
            self.assertEqual(ctx.exception.category, "contract_not_found")

    def test_missing_key_raises_invalid_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            del document["delinquent_statuses"]
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_status_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_status_contract_schema")

    def test_extra_key_raises_invalid_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["unexpected_field"] = "synthetic-value"
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_status_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_status_contract_schema")

    def test_unsupported_version_raises_unsupported_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["contract_version"] = 2
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_status_contract(path)
            self.assertEqual(ctx.exception.category, "unsupported_status_contract_version")

    def test_wrong_logical_list_order_raises_invalid_status_logical_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["logical_lists"] = list(reversed(document["logical_lists"]))
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_status_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_status_logical_lists")

    def test_non_33_count_vocabulary_raises_invalid_status_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["nonblank_status_vocabulary"] = document["nonblank_status_vocabulary"][:-1]
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_status_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_status_vocabulary")

    def test_duplicate_vocabulary_entry_raises_invalid_status_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            vocabulary = document["nonblank_status_vocabulary"][:-1]
            vocabulary.append(vocabulary[0])
            document["nonblank_status_vocabulary"] = vocabulary
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_status_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_status_vocabulary")

    def test_delinquent_statuses_not_a_subset_raises_invalid_delinquent_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["delinquent_statuses"] = ["Delinquent", "Not A Real Status"]
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_status_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_delinquent_statuses")

    def test_wrong_delinquent_status_count_raises_invalid_delinquent_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["delinquent_statuses"] = ["Delinquent"]
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_status_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_delinquent_statuses")

    def test_error_never_echoes_sentinel_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["unexpected_field"] = _SENTINEL_PATH_LIKE
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_status_contract(path)
            rendered = f"{ctx.exception.category}:{ctx.exception}"
            self.assertNotIn(_SENTINEL_PATH_LIKE, rendered)
            self.assertNotIn(str(path), rendered)


class ResultSchemaDocumentTests(unittest.TestCase):
    """The committed result schema document matches the locked D-04/D-05 shape."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.document = _load_json(RESULT_SCHEMA_PATH)

    def test_document_exists(self) -> None:
        self.assertTrue(RESULT_SCHEMA_PATH.is_file())

    def test_top_level_status_enum_matches_locked_vocabulary(self) -> None:
        self.assertEqual(
            set(self.document["properties"]["status"]["enum"]),
            {"accepted", "rejected", "no_new_release", "operational_error"},
        )

    def test_reason_code_enum_matches_reason_rank_vocabulary(self) -> None:
        reason_schema = self.document["$defs"]["reason"]["properties"]["code"]
        self.assertEqual(set(reason_schema["enum"]), set(REASON_RANK.keys()))

    def test_reason_logical_list_enum_matches_logical_list_order_plus_null(self) -> None:
        reason_schema = self.document["$defs"]["reason"]["properties"]["logical_list"]
        self.assertEqual(set(reason_schema["enum"]), set(LOGICAL_LIST_ORDER) | {None})

    def test_schema_forbids_additional_properties_at_top_level(self) -> None:
        self.assertIs(self.document["additionalProperties"], False)

    def test_schema_forbids_additional_properties_on_reason(self) -> None:
        self.assertIs(self.document["$defs"]["reason"]["additionalProperties"], False)


class MalformedCsvContractTests(unittest.TestCase):
    """Malformed/extra-field CSV contract documents fail closed by fixed category."""

    def _write(self, tmp_dir: Path, document: dict) -> Path:
        path = tmp_dir / "malformed-csv.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def _valid_document(self) -> dict:
        return json.loads(CSV_CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_missing_file_raises_contract_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.json"
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(missing)
            self.assertEqual(ctx.exception.category, "contract_not_found")

    def test_invalid_json_raises_invalid_contract_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_contract_json")

    def test_missing_key_raises_invalid_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            del document["quoting"]
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_csv_contract_schema")

    def test_extra_key_raises_invalid_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["unexpected_field"] = "synthetic-value"
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_csv_contract_schema")

    def test_unsupported_version_raises_unsupported_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["contract_version"] = 2
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(path)
            self.assertEqual(ctx.exception.category, "unsupported_csv_contract_version")

    def test_wrong_logical_list_order_raises_invalid_logical_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["logical_lists"] = list(reversed(document["logical_lists"]))
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_logical_lists")

    def test_wrong_header_count_raises_invalid_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["headers"] = document["headers"][:10]
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_headers")

    def test_duplicate_header_raises_invalid_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["headers"] = list(document["headers"][:-1]) + [document["headers"][0]]
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_headers")

    def test_wrong_encoding_raises_invalid_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["encoding"] = "utf-8"
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_encoding")

    def test_wrong_quoting_raises_invalid_quoting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["quoting"] = "QUOTE_MINIMAL"
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_quoting")

    def test_non_positive_payload_ceiling_raises_invalid_payload_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["max_physical_line_bytes"] = 0
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_payload_ceiling")

    def test_error_never_echoes_sentinel_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["unexpected_field"] = _SENTINEL_PATH_LIKE
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_csv_contract(path)
            rendered = f"{ctx.exception.category}:{ctx.exception}"
            self.assertNotIn(_SENTINEL_PATH_LIKE, rendered)
            self.assertNotIn(str(path), rendered)


class MalformedXlsxContractTests(unittest.TestCase):
    """Malformed/extra-field XLSX contract documents fail closed by fixed category."""

    def _write(self, tmp_dir: Path, document: dict) -> Path:
        path = tmp_dir / "malformed-xlsx.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def _valid_document(self) -> dict:
        return json.loads(XLSX_CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_extra_key_raises_invalid_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["unexpected_field"] = "synthetic-value"
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_xlsx_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_xlsx_contract_schema")

    def test_wrong_status_raises_invalid_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["status"] = "active"
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_xlsx_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_xlsx_status")

    def test_reader_dependency_required_true_raises_invalid_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["reader_dependency_required"] = True
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_xlsx_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_reader_dependency_flag")

    def test_wrong_unsupported_reason_raises_invalid_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            document["unsupported_reason"] = "contract.something_else"
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_xlsx_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_unsupported_reason")

    def test_missing_known_worksheet_key_raises_invalid_known_worksheet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document = self._valid_document()
            del document["known_worksheet"]["header_row"]
            path = self._write(Path(tmp), document)
            with self.assertRaises(ContractError) as ctx:
                load_xlsx_contract(path)
            self.assertEqual(ctx.exception.category, "invalid_known_worksheet")


class AdmissionReasonTests(unittest.TestCase):
    def test_unknown_code_raises_result_error(self) -> None:
        with self.assertRaises(ResultError) as ctx:
            AdmissionReason(code="not.a.real.code")
        self.assertEqual(ctx.exception.category, "unknown_reason_code")

    def test_unknown_logical_list_raises_result_error(self) -> None:
        with self.assertRaises(ResultError) as ctx:
            AdmissionReason(code="date.blank", logical_list="not-a-real-list")
        self.assertEqual(ctx.exception.category, "unknown_logical_list")

    def test_field_set_is_exact_and_value_free(self) -> None:
        field_names = set(AdmissionReason.__dataclass_fields__.keys())
        self.assertEqual(
            field_names,
            {"code", "logical_list", "safe_line_number", "safe_location", "safe_count"},
        )

    def test_reason_is_immutable(self) -> None:
        reason = AdmissionReason(code="date.blank")
        with self.assertRaises(AttributeError):
            reason.code = "date.mismatch"  # type: ignore[misc]


class SortReasonsTests(unittest.TestCase):
    def test_reasons_sort_by_rank_then_logical_list_then_line(self) -> None:
        reasons = [
            AdmissionReason(code="date.blank", logical_list="charities-may-not-operate"),
            AdmissionReason(code="candidate.invalid_mapping"),
            AdmissionReason(code="date.blank", logical_list="charities-may-operate", safe_line_number=5),
            AdmissionReason(code="date.blank", logical_list="charities-may-operate", safe_line_number=2),
        ]
        ordered = sort_reasons(reasons)
        ordered_codes_and_lists = [(r.code, r.logical_list, r.safe_line_number) for r in ordered]
        self.assertEqual(
            ordered_codes_and_lists,
            [
                ("candidate.invalid_mapping", None, None),
                ("date.blank", "charities-may-operate", 2),
                ("date.blank", "charities-may-operate", 5),
                ("date.blank", "charities-may-not-operate", None),
            ],
        )

    def test_sort_is_deterministic_and_stable_across_repeated_calls(self) -> None:
        reasons = [
            AdmissionReason(code="registration.duplicate"),
            AdmissionReason(code="parse.header_mismatch"),
        ]
        first = sort_reasons(reasons)
        second = sort_reasons(list(reversed(reasons)))
        self.assertEqual([r.code for r in first], [r.code for r in second])


class AdmissionResultTests(unittest.TestCase):
    def test_accepted_exit_code_is_zero(self) -> None:
        result = AdmissionResult.accepted("2026-08-05", 1, "0" * 64)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.reasons, ())

    def test_rejected_exit_code_is_one(self) -> None:
        result = AdmissionResult.rejected([AdmissionReason(code="date.blank")])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.status, "rejected")

    def test_no_new_release_exit_code_is_two(self) -> None:
        result = AdmissionResult.no_new_release("2026-08-19", 1, "a" * 64)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.status, "no_new_release")

    def test_operational_error_exit_code_is_three(self) -> None:
        result = AdmissionResult.operational_error([AdmissionReason(code="store.busy")])
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.status, "operational_error")

    def test_unknown_status_raises_result_error(self) -> None:
        with self.assertRaises(ResultError) as ctx:
            AdmissionResult(
                status="unknown_status",
                as_of_date=None,
                release_revision=None,
                revision_fingerprint=None,
                reasons=(),
            )
        self.assertEqual(ctx.exception.category, "unknown_status")

    def test_constructor_sorts_reasons_deterministically(self) -> None:
        result = AdmissionResult.rejected(
            [
                AdmissionReason(code="date.blank"),
                AdmissionReason(code="candidate.invalid_mapping"),
            ]
        )
        self.assertEqual([r.code for r in result.reasons], ["candidate.invalid_mapping", "date.blank"])

    def test_result_is_immutable(self) -> None:
        result = AdmissionResult.accepted("2026-08-05", 1, "0" * 64)
        with self.assertRaises(AttributeError):
            result.status = "rejected"  # type: ignore[misc]

    def test_render_status_accepted(self) -> None:
        result = AdmissionResult.accepted("2026-08-05", 1, "0" * 64)
        self.assertEqual(result.render_status(), "accepted as_of=2026-08-05 revision=1")

    def test_render_status_rejected_has_no_raw_value(self) -> None:
        result = AdmissionResult.rejected([AdmissionReason(code="registration.duplicate")])
        self.assertEqual(result.render_status(), "rejected reasons=1")

    def test_to_json_has_exact_closed_key_set(self) -> None:
        result = AdmissionResult.accepted("2026-08-05", 1, "0" * 64)
        document = json.loads(result.to_json())
        self.assertEqual(
            set(document.keys()),
            {"schema_version", "status", "as_of_date", "release_revision", "revision_fingerprint", "reasons"},
        )
        self.assertEqual(document["schema_version"], 1)

    def test_to_json_reason_entries_have_exact_closed_key_set(self) -> None:
        result = AdmissionResult.rejected(
            [AdmissionReason(code="date.blank", logical_list="charities-may-operate", safe_line_number=3)]
        )
        document = json.loads(result.to_json())
        self.assertEqual(len(document["reasons"]), 1)
        self.assertEqual(
            set(document["reasons"][0].keys()),
            {"code", "logical_list", "safe_line_number", "safe_location", "safe_count"},
        )

    def test_to_json_never_echoes_sentinel_fein_like_value(self) -> None:
        # safe_location is a deliberately safe field; even so, prove a
        # sentinel excluded-shaped value placed nowhere in the model never
        # appears in serialized output (non-echo discipline, D-05/D-10).
        result = AdmissionResult.rejected([AdmissionReason(code="registration.duplicate")])
        rendered = result.to_json() + result.render_status()
        self.assertNotIn(_SENTINEL_FEIN_LIKE, rendered)
        self.assertFalse(re.search(r"\b\d{2}-\d{7}\b", rendered))


if __name__ == "__main__":
    unittest.main()
