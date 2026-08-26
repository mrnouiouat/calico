"""Regression suite for `calico_landing.parquet` (D-01/D-02).

Creates temporary identity-free parsed rows, proves every source string --
including an unescaped quote and non-ASCII punctuation -- round-trips
through the generated-NDJSON-to-DuckDB-to-Parquet path unchanged, proves
the temporary NDJSON is removed on both success and failure, and proves
production code contains no raw-CSV reader or extension-install command
(D-02; mirrors the `rg` scan in this plan's own verification command).

No real organization identity or excluded value is used -- only reserved
synthetic sentinels, per D-10.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from calico_landing.contracts import LOGICAL_LIST_ORDER, CsvContract
from calico_landing.parquet import (
    CanonicalSerializationError,
    ParquetArtifact,
    write_parquet,
)
from calico_landing.parser import ParsedList, ParsedRecord

REPO_ROOT = Path(__file__).resolve().parents[2]

_HEADERS = ("Status", "Reg#", "Name")
_LOGICAL_LIST = "charities-may-operate"

#: Reserved synthetic sentinel, never a real registration/FEIN value. Split
#: via runtime concatenation per D-10 (mirrors Phase 1 Plan 01-03 and
#: Phase 2 Plan 01/02-01's precedent).
_SENTINEL_REG_LIKE = "94-" + "1234567"


def _contract(headers: tuple[str, ...] = _HEADERS) -> CsvContract:
    return CsvContract(
        contract_version=1,
        logical_lists=LOGICAL_LIST_ORDER,
        headers=headers,
        encoding="cp1252",
        quoting="QUOTE_NONE",
        canonical_exchange_format="parquet",
        max_compressed_payload_bytes=1_000_000,
        max_decompressed_payload_bytes=1_000_000,
        max_physical_line_bytes=1_000_000,
    )


def _sample_parsed_list(logical_list: str = _LOGICAL_LIST) -> ParsedList:
    return ParsedList(
        logical_list=logical_list,
        headers=_HEADERS,
        records=(
            ParsedRecord(source_line_no=2, fields=("Active", "001", 'O"Brien Fund')),
            ParsedRecord(source_line_no=3, fields=("Active", "002", "Café — Trust’s")),
        ),
    )


class RoundTripTests(unittest.TestCase):
    def test_all_source_strings_round_trip_unchanged(self) -> None:
        parsed = _sample_parsed_list()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            staging_dir = tmp_path / "staging"
            destination_path = tmp_path / "out" / f"{parsed.logical_list}.parquet"

            artifact = write_parquet(parsed, staging_dir, destination_path, _contract())

            self.assertIsInstance(artifact, ParquetArtifact)
            self.assertEqual(artifact.row_count, 2)
            self.assertEqual(artifact.logical_list, _LOGICAL_LIST)

            import duckdb

            with duckdb.connect(":memory:") as connection:
                rows = connection.read_parquet(str(destination_path)).fetchall()

        self.assertEqual(
            rows,
            [
                ("Active", "001", 'O"Brien Fund', _LOGICAL_LIST, 2),
                ("Active", "002", "Café — Trust’s", _LOGICAL_LIST, 3),
            ],
        )

    def test_schema_and_writer_metadata(self) -> None:
        parsed = _sample_parsed_list()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            destination_path = tmp_path / "out" / "list.parquet"

            artifact = write_parquet(
                parsed, tmp_path / "staging", destination_path, _contract()
            )

        self.assertEqual(
            artifact.schema_columns,
            ("Status", "Reg#", "Name", "source_list", "source_line_no"),
        )
        self.assertEqual(
            artifact.schema_types,
            ("VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR", "BIGINT"),
        )
        self.assertEqual(artifact.compression, "zstd")
        self.assertEqual(artifact.row_group_size, 122_880)
        self.assertTrue(artifact.writer_version.startswith("duckdb-"))
        self.assertRegex(artifact.sha256, r"^[0-9a-f]{64}$")

    def test_sha256_matches_written_file_bytes(self) -> None:
        import hashlib

        parsed = _sample_parsed_list()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            destination_path = tmp_path / "out" / "list.parquet"

            artifact = write_parquet(
                parsed, tmp_path / "staging", destination_path, _contract()
            )

            digest = hashlib.sha256(destination_path.read_bytes()).hexdigest()

        self.assertEqual(artifact.sha256, digest)

    def test_row_ordering_matches_source_line_number(self) -> None:
        # Records supplied out of source-line order must still be written
        # ordered by `source_line_no` (Pattern 2: `.order("source_line_no")`).
        parsed = ParsedList(
            logical_list=_LOGICAL_LIST,
            headers=_HEADERS,
            records=(
                ParsedRecord(source_line_no=5, fields=("Active", "002", "Second")),
                ParsedRecord(source_line_no=2, fields=("Active", "001", "First")),
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            destination_path = tmp_path / "out" / "list.parquet"
            write_parquet(parsed, tmp_path / "staging", destination_path, _contract())

            import duckdb

            with duckdb.connect(":memory:") as connection:
                line_numbers = [
                    row[0]
                    for row in connection.read_parquet(str(destination_path))
                    .project("source_line_no")
                    .fetchall()
                ]

        self.assertEqual(line_numbers, [2, 5])


class NdjsonCleanupTests(unittest.TestCase):
    def test_ndjson_removed_after_success(self) -> None:
        parsed = _sample_parsed_list()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            staging_dir = tmp_path / "staging"
            destination_path = tmp_path / "out" / "list.parquet"

            write_parquet(parsed, staging_dir, destination_path, _contract())

            ndjson_path = staging_dir / f"{parsed.logical_list}.ndjson"
            self.assertFalse(ndjson_path.exists())
            self.assertTrue(destination_path.exists())

    def test_ndjson_removed_after_failure(self) -> None:
        parsed = _sample_parsed_list()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            staging_dir = tmp_path / "staging"

            # `blocker` is a regular file, so creating a destination beneath
            # it as a directory must fail after the NDJSON has already been
            # written -- cleanup must still occur.
            blocker = tmp_path / "blocker"
            blocker.write_bytes(b"not a directory")
            destination_path = blocker / "list.parquet"

            with self.assertRaises(OSError):
                write_parquet(parsed, staging_dir, destination_path, _contract())

            ndjson_path = staging_dir / f"{parsed.logical_list}.ndjson"
            self.assertFalse(ndjson_path.exists())


class RejectionTests(unittest.TestCase):
    def test_header_mismatch_between_parsed_and_contract_rejected(self) -> None:
        parsed = _sample_parsed_list()
        mismatched_contract = _contract(headers=("Different", "Headers", "Here"))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with self.assertRaises(CanonicalSerializationError) as ctx:
                write_parquet(
                    parsed, tmp_path / "staging", tmp_path / "out.parquet", mismatched_contract
                )

        self.assertEqual(ctx.exception.code, "canonical.serialization_failed")
        self.assertEqual(ctx.exception.logical_list, _LOGICAL_LIST)

    def test_existing_destination_never_overwritten(self) -> None:
        parsed = _sample_parsed_list()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            destination_path = tmp_path / "out" / "list.parquet"
            destination_path.parent.mkdir(parents=True)
            destination_path.write_bytes(b"already here")

            with self.assertRaises(CanonicalSerializationError) as ctx:
                write_parquet(parsed, tmp_path / "staging", destination_path, _contract())

            self.assertEqual(ctx.exception.code, "canonical.serialization_failed")
            self.assertEqual(destination_path.read_bytes(), b"already here")
            self.assertFalse((tmp_path / "staging" / f"{_LOGICAL_LIST}.ndjson").exists())


class NonEchoTests(unittest.TestCase):
    def test_rejection_never_carries_the_sentinel_value(self) -> None:
        parsed = ParsedList(
            logical_list=_LOGICAL_LIST,
            headers=_HEADERS,
            records=(ParsedRecord(source_line_no=2, fields=("Active", _SENTINEL_REG_LIKE, "X")),),
        )
        mismatched_contract = _contract(headers=("A", "B", "C"))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with self.assertRaises(CanonicalSerializationError) as ctx:
                write_parquet(
                    parsed, tmp_path / "staging", tmp_path / "out.parquet", mismatched_contract
                )

        exc = ctx.exception
        self.assertNotIn(_SENTINEL_REG_LIKE, str(exc))
        self.assertNotIn(_SENTINEL_REG_LIKE, repr(exc))
        for value in vars(exc).values():
            if isinstance(value, str):
                self.assertNotIn(_SENTINEL_REG_LIKE, value)

    def test_reject_exposes_only_safe_fields(self) -> None:
        parsed = _sample_parsed_list()
        mismatched_contract = _contract(headers=("A", "B", "C"))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with self.assertRaises(CanonicalSerializationError) as ctx:
                write_parquet(
                    parsed, tmp_path / "staging", tmp_path / "out.parquet", mismatched_contract
                )

        self.assertEqual(set(vars(ctx.exception)), {"code", "logical_list", "safe_count"})


class ForbiddenPatternTests(unittest.TestCase):
    """Static proof that production code never reopens raw CSV via DuckDB."""

    def test_no_raw_csv_reader_or_extension_commands(self) -> None:
        source = (REPO_ROOT / "calico_landing" / "parquet.py").read_text(encoding="utf-8")
        forbidden = (
            "read_csv",
            "from_csv_auto",
            "INSTALL ",
            "LOAD ",
            'errors="replace"',
            "errors='replace'",
        )
        for pattern in forbidden:
            self.assertNotIn(pattern, source)


if __name__ == "__main__":
    unittest.main()
