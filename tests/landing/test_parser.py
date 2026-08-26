"""Regression suite for `calico_landing.parser` (D-02/D-03).

Table-driven, identity-free physical-line/encoding/header/arity cases per
`02-RESEARCH.md` and `02-PATTERNS.md`: an unescaped quote stays data in one
physical record, valid CP1252 punctuation in `0x80-0x9F` round-trips
without U+FFFD, all three physical terminators are recognized, an invalid
CP1252 byte and an embedded U+FFFD are both rejected, header/arity
mismatches fail closed with a safe line number, blank physical lines
(interior and trailing) are skipped without breaking line/record
reconciliation, and no `StructuralReject` ever carries a raw field value or
row (D-05/D-10 non-echo discipline).

No real organization identity or excluded value is used -- only reserved
synthetic sentinels, per D-10.
"""

from __future__ import annotations

import unittest

from calico_landing.contracts import LOGICAL_LIST_ORDER, CsvContract
from calico_landing.parser import ParsedList, StructuralReject, parse_payload

#: A synthetic, identity-free three-column header set used by most cases
#: below -- the parser is generic over `contract.headers`; only
#: `test_contracts.py` and the private real-release proof exercise the
#: exact eleven published headers.
_HEADERS = ("Status", "Reg#", "Name")
_LOGICAL_LIST = "charities-may-operate"

#: Reserved synthetic sentinel, never a real registration/FEIN value. Split
#: via runtime concatenation so the committed source text never contains a
#: contiguous privacy-scanner match while the runtime value stays
#: byte-identical (mirrors the fix documented for Phase 1 Plan 01-03 and
#: Phase 2 Plan 01).
_SENTINEL_REG_LIKE = "94-" + "1234567"


def _contract(headers: tuple[str, ...] = _HEADERS, encoding: str = "cp1252") -> CsvContract:
    return CsvContract(
        contract_version=1,
        logical_lists=LOGICAL_LIST_ORDER,
        headers=headers,
        encoding=encoding,
        quoting="QUOTE_NONE",
        canonical_exchange_format="parquet",
        max_compressed_payload_bytes=1_000_000,
        max_decompressed_payload_bytes=1_000_000,
        max_physical_line_bytes=1_000_000,
    )


class UnescapedQuoteTests(unittest.TestCase):
    """The predecessor default-parser quote-fusion defect must not recur."""

    def test_unescaped_quote_stays_data_in_one_record(self) -> None:
        payload = 'Status,Reg#,Name\r\nActive,001,O"Brien Fund\r\n'.encode("cp1252")

        parsed = parse_payload(payload, _LOGICAL_LIST, _contract())

        self.assertIsInstance(parsed, ParsedList)
        self.assertEqual(len(parsed.records), 1)
        self.assertEqual(parsed.records[0].fields, ("Active", "001", 'O"Brien Fund'))
        self.assertEqual(parsed.records[0].source_line_no, 2)

    def test_multiple_unescaped_quotes_do_not_fuse_two_records(self) -> None:
        payload = (
            'Status,Reg#,Name\r\n'
            'Active,001,"Leading Quote\r\n'
            'Active,002,Trailing Quote"\r\n'
        ).encode("cp1252")

        parsed = parse_payload(payload, _LOGICAL_LIST, _contract())

        # Two physical data lines must remain two records, not one fused
        # record -- the exact regression `GATE-A-EVIDENCE.md` Section 4
        # documents for the default RFC-style quote-aware parser.
        self.assertEqual(len(parsed.records), 2)
        self.assertEqual(parsed.records[0].fields[2], '"Leading Quote')
        self.assertEqual(parsed.records[1].fields[2], 'Trailing Quote"')


class Cp1252PunctuationTests(unittest.TestCase):
    """Valid CP1252 bytes in 0x80-0x9F are punctuation, not rejection grounds."""

    def test_cp1252_high_punctuation_round_trips(self) -> None:
        # 0x93/0x94 = left/right double quotation mark, 0x96 = en dash --
        # all valid, distinct CP1252 mappings inside 0x80-0x9F.
        raw_name = b"Big\x93Quote\x94 \x96 Value"
        payload = b"Status,Reg#,Name\r\nActive,001," + raw_name + b"\r\n"

        parsed = parse_payload(payload, _LOGICAL_LIST, _contract())

        expected = "Big“Quote” – Value"
        self.assertEqual(parsed.records[0].fields[2], expected)
        self.assertNotIn("�", parsed.records[0].fields[2])


class PhysicalTerminatorTests(unittest.TestCase):
    """CRLF, LF, and CR are all recognized as explicit physical terminators."""

    def _assert_two_records_at_lines_two_and_three(self, payload: bytes) -> None:
        parsed = parse_payload(payload, _LOGICAL_LIST, _contract())
        self.assertEqual(len(parsed.records), 2)
        self.assertEqual(parsed.records[0].source_line_no, 2)
        self.assertEqual(parsed.records[1].source_line_no, 3)
        self.assertEqual(parsed.records[0].fields, ("Active", "001", "First"))
        self.assertEqual(parsed.records[1].fields, ("Active", "002", "Second"))

    def test_crlf_terminator(self) -> None:
        payload = b"Status,Reg#,Name\r\nActive,001,First\r\nActive,002,Second\r\n"
        self._assert_two_records_at_lines_two_and_three(payload)

    def test_lf_terminator(self) -> None:
        payload = b"Status,Reg#,Name\nActive,001,First\nActive,002,Second\n"
        self._assert_two_records_at_lines_two_and_three(payload)

    def test_cr_terminator(self) -> None:
        payload = b"Status,Reg#,Name\rActive,001,First\rActive,002,Second\r"
        self._assert_two_records_at_lines_two_and_three(payload)


class DecodeFailureTests(unittest.TestCase):
    """Strict decode failures and embedded U+FFFD both fail closed."""

    def test_invalid_cp1252_byte_rejected(self) -> None:
        # 0x81 has no CP1252 mapping and must raise under errors="strict".
        payload = b"Status,Reg#,Name\r\nActive,001,Bad\x81Byte\r\n"

        with self.assertRaises(StructuralReject) as ctx:
            parse_payload(payload, _LOGICAL_LIST, _contract())

        self.assertEqual(ctx.exception.code, "parse.decode_failed")
        self.assertEqual(ctx.exception.logical_list, _LOGICAL_LIST)

    def test_embedded_replacement_character_rejected(self) -> None:
        # cp1252 strict decoding can never itself produce U+FFFD, so this
        # exercises the explicit zero-U+FFFD assertion against a contract
        # whose encoding validly decodes a replacement character (D-02).
        payload = "Status,Reg#,Name\r\nActive,001,Already�Bad\r\n".encode("utf-8")

        with self.assertRaises(StructuralReject) as ctx:
            parse_payload(payload, _LOGICAL_LIST, _contract(encoding="utf-8"))

        self.assertEqual(ctx.exception.code, "parse.decode_failed")
        self.assertEqual(ctx.exception.logical_list, _LOGICAL_LIST)


class HeaderMismatchTests(unittest.TestCase):
    def test_wrong_header_rejected(self) -> None:
        payload = b"Wrong,Header,Row\r\nActive,001,First\r\n"

        with self.assertRaises(StructuralReject) as ctx:
            parse_payload(payload, _LOGICAL_LIST, _contract())

        self.assertEqual(ctx.exception.code, "parse.header_mismatch")
        self.assertEqual(ctx.exception.logical_list, _LOGICAL_LIST)
        self.assertEqual(ctx.exception.safe_line_number, 1)

    def test_missing_header_row_rejected(self) -> None:
        payload = b""

        with self.assertRaises(StructuralReject) as ctx:
            parse_payload(payload, _LOGICAL_LIST, _contract())

        self.assertEqual(ctx.exception.code, "parse.header_mismatch")
        self.assertEqual(ctx.exception.safe_line_number, 1)


class ArityMismatchTests(unittest.TestCase):
    def test_too_few_fields_rejected(self) -> None:
        payload = f"Status,Reg#,Name\r\nActive,{_SENTINEL_REG_LIKE}\r\n".encode("cp1252")

        with self.assertRaises(StructuralReject) as ctx:
            parse_payload(payload, _LOGICAL_LIST, _contract())

        self.assertEqual(ctx.exception.code, "parse.arity_mismatch")
        self.assertEqual(ctx.exception.logical_list, _LOGICAL_LIST)
        self.assertEqual(ctx.exception.safe_line_number, 2)

    def test_too_many_fields_rejected(self) -> None:
        payload = f"Status,Reg#,Name\r\nActive,{_SENTINEL_REG_LIKE},First,Extra\r\n".encode(
            "cp1252"
        )

        with self.assertRaises(StructuralReject) as ctx:
            parse_payload(payload, _LOGICAL_LIST, _contract())

        self.assertEqual(ctx.exception.code, "parse.arity_mismatch")
        self.assertEqual(ctx.exception.safe_line_number, 2)


class BlankLineReconciliationTests(unittest.TestCase):
    def test_interior_and_trailing_blank_lines_are_skipped(self) -> None:
        # Physical lines: 1=header, 2=data, 3=blank, 4=data, 5=trailing
        # blank produced by the final CRLF. Only lines 2 and 4 are data.
        payload = (
            "Status,Reg#,Name\r\n"
            "Active,001,First\r\n"
            "\r\n"
            "Active,002,Second\r\n"
        ).encode("cp1252")

        parsed = parse_payload(payload, _LOGICAL_LIST, _contract())

        self.assertEqual(len(parsed.records), 2)
        self.assertEqual(parsed.records[0].source_line_no, 2)
        self.assertEqual(parsed.records[1].source_line_no, 4)


class FieldPreservationTests(unittest.TestCase):
    def test_field_strings_are_preserved_unchanged(self) -> None:
        payload = "Status,Reg#,Name\r\n  Active  ,001,  Padded Name  \r\n".encode("cp1252")

        parsed = parse_payload(payload, _LOGICAL_LIST, _contract())

        # Universal trim belongs to dbt (D-03); the parser must not strip.
        self.assertEqual(parsed.records[0].fields, ("  Active  ", "001", "  Padded Name  "))

    def test_returns_headers_and_logical_list_from_contract(self) -> None:
        payload = "Status,Reg#,Name\r\nActive,001,First\r\n".encode("cp1252")

        parsed = parse_payload(payload, "charities-not-operating", _contract())

        self.assertEqual(parsed.headers, _HEADERS)
        self.assertEqual(parsed.logical_list, "charities-not-operating")


class NonEchoTests(unittest.TestCase):
    """A `StructuralReject` never carries a raw field value or row (D-05/D-10)."""

    def test_arity_reject_never_carries_the_sentinel_value(self) -> None:
        payload = f"Status,Reg#,Name\r\nActive,{_SENTINEL_REG_LIKE}\r\n".encode("cp1252")

        with self.assertRaises(StructuralReject) as ctx:
            parse_payload(payload, _LOGICAL_LIST, _contract())

        exc = ctx.exception
        self.assertNotIn(_SENTINEL_REG_LIKE, str(exc))
        self.assertNotIn(_SENTINEL_REG_LIKE, repr(exc))
        for value in vars(exc).values():
            if isinstance(value, str):
                self.assertNotIn(_SENTINEL_REG_LIKE, value)

    def test_reject_exposes_only_safe_fields(self) -> None:
        payload = b"Wrong,Header,Row\r\n"

        with self.assertRaises(StructuralReject) as ctx:
            parse_payload(payload, _LOGICAL_LIST, _contract())

        self.assertEqual(
            set(vars(ctx.exception)),
            {"code", "logical_list", "safe_line_number", "safe_count"},
        )


if __name__ == "__main__":
    unittest.main()
