"""Value-free scanner and byte-preserving provenance contracts."""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tools.privacy_scan.policy import load_policy
from tools.privacy_scan.scanner import scan_text, scan_paths, _scan_utf8_chunks

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / "policies/publishable-tree.json")


class StreetPrecisionTests(unittest.TestCase):
    def test_counted_file_prose_is_clean_at_every_split(self):
        prose = b"Parse 5 gzip files the same way."
        for text in (prose, prose.upper(), b"Read 12 csv records the same way."):
            self.assertFalse(scan_text("notes.md", text.decode()), "prose classification")
            for split in range(len(text) + 1):
                self.assertFalse(_scan_utf8_chunks("notes.md", [text[:split], text[split:]]), "stream prose classification")
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                (root / "notes.md").write_bytes(text)
                self.assertFalse(scan_paths(root, ["notes.md"], POLICY), "path prose classification")

    def test_address_positives_survive_all_suffixes_and_splits(self):
        for suffix in ("St", "Street", "Ave", "Avenue", "Blvd", "Boulevard", "Rd", "Road", "Dr", "Drive", "Ln", "Lane", "Way", "Ct", "Court"):
            for prefix, ending in (("", ""), ("Address: ", ", Unit 2"), ("'", "'")):
                text = (prefix + "4210" + " Synthetic " + suffix + ending).encode()
                self.assertTrue(any(f.category == "street_address" for f in scan_text("notes.MD", text.decode())), "address classification")
                for split in range(len(text) + 1):
                    self.assertTrue(any(f.category == "street_address" for f in _scan_utf8_chunks("notes.MD", [text[:split], text[split:]])), "stream address classification")
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    (root / "notes.MD").write_bytes(text)
                    self.assertTrue(any(f.category == "street_address" for f in scan_paths(root, ["notes.MD"], POLICY)), "path address classification")
        for text in (b"5" + b" The Same Way", b"Mailing address: " + b"5" + b" gzip files the same way", b"5" + b" gzip files the same way, Synthetic City"):
            self.assertTrue(any(f.category == "street_address" for f in scan_text("notes", text.decode())), "ambiguous address remains detected")

    def test_non_echo_findings(self):
        sentinel = "4210" + " Synthetic Ave"
        rendered = "\n".join(f.render() for f in scan_text("notes.md", sentinel))
        self.assertFalse(sentinel in rendered, "finding reflected a sentinel")


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.api = importlib.import_module("tools.docs_public.provenance")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "original.md"
        self.body = b"# Historical investigation\r\nA preserved record.\r\n"
        self.source.write_bytes(self.body)
        self.anchor = self.api.PredecessorAnchor("synthetic-investigation", hashlib.sha256(self.body).hexdigest(), "docs/provenance/record.md")

    def build(self):
        return self.api.build_successor(self.source, self.anchor, self.root, POLICY, import_date="2026-10-01")

    def test_exact_body_adjacency_and_hash_index(self):
        record = self.build()
        data = (self.root / self.anchor.destination).read_bytes()
        self.assertTrue(data[record.prefix_bytes:] == self.body, "body changed")
        self.assertTrue(self.source.read_bytes() == self.body, "source changed")
        self.api.validate_successor(data, record)
        index = json.loads((self.root / "docs/provenance/index-v1.json").read_bytes())
        self.assertTrue(index["entries"][0] == record.to_dict(), "index mismatch")

    def test_source_hash_mismatch_writes_nothing(self):
        self.source.write_bytes(self.body + b"drift")
        with self.assertRaises(self.api.ProvenanceError) as caught:
            self.build()
        self.assertTrue(str(caught.exception) == "provenance.predecessor_hash", "unsafe error")
        self.assertFalse((self.root / "docs/provenance").exists(), "mismatch published")

    def test_empty_body_is_rejected_before_publication(self):
        self.source.write_bytes(b"")
        anchor = self.api.PredecessorAnchor("empty-source", hashlib.sha256(b"").hexdigest(), "docs/provenance/empty.md")
        rejected = False
        try:
            self.api.build_successor(self.source, anchor, self.root, POLICY, import_date="2026-10-01")
        except self.api.ProvenanceError:
            rejected = True
        self.assertTrue(rejected, "empty historical body was accepted")
        self.assertFalse((self.root / "docs/provenance").exists(), "empty body published")

    def test_marker_and_body_tampering_fail_closed(self):
        record = self.build()
        data = (self.root / self.anchor.destination).read_bytes()
        mutations = (data.replace(self.api.START_MARKER, b"", 1), self.api.START_MARKER + data, data.replace(self.api.END_MARKER, self.api.START_MARKER), data + b"\n", data[:-1], self.api.END_MARKER + data)
        for changed in mutations:
            with self.assertRaises(self.api.ProvenanceError):
                self.api.validate_successor(changed, record)

    def test_unsafe_candidate_is_not_written_or_echoed(self):
        sentinel = b"4210" + b" Synthetic Ave"
        self.source.write_bytes(sentinel)
        self.anchor = self.api.PredecessorAnchor("synthetic-investigation", hashlib.sha256(sentinel).hexdigest(), "docs/provenance/record.md")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(self.api.ProvenanceError) as caught:
                self.build()
        self.assertFalse(sentinel.decode() in output.getvalue() + str(caught.exception), "diagnostic reflected a sentinel")
        self.assertFalse((self.root / "docs/provenance").exists(), "unsafe candidate published")

    def test_interruption_restores_previous_pair(self):
        self.build()
        before = {p.relative_to(self.root): p.read_bytes() for p in (self.root / "docs/provenance").rglob("*") if p.is_file()}
        original_replace = self.api.os.replace
        calls = 0
        def interrupt(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            return original_replace(source, destination)
        with patch.object(self.api.os, "replace", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.api.build_successor(self.source, self.anchor, self.root, POLICY, import_date="2026-10-01", replace_existing=True)
        after = {p.relative_to(self.root): p.read_bytes() for p in (self.root / "docs/provenance").rglob("*") if p.is_file()}
        self.assertTrue(before == after, "pair changed after interruption")

    def test_duplicate_destinations_rejected_equal_bodies_retained(self):
        self.build()
        with self.assertRaises(self.api.ProvenanceError):
            self.build()
        second = self.api.PredecessorAnchor("another-source-slot", self.anchor.predecessor_sha256, "docs/provenance/second.md")
        self.api.build_successor(self.source, second, self.root, POLICY, import_date="2026-10-01")
        index = json.loads((self.root / "docs/provenance/index-v1.json").read_bytes())
        self.assertTrue(len(index["entries"]) == 2, "equal bodies were merged")

    def test_traversal_and_symlink_fail_closed(self):
        for path in ("../record.md", "docs/provenance/../record.md", "docs/provenance2/record.md"):
            with self.assertRaises(self.api.ProvenanceError):
                bad = self.api.PredecessorAnchor("source", self.anchor.predecessor_sha256, path)
                self.api.build_successor(self.source, bad, self.root, POLICY, import_date="2026-10-01")
        (self.root / "docs").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(self.api.ProvenanceError):
            self.build()

    def test_index_tampering_rejects_before_publication(self):
        self.build()
        path = self.root / "docs/provenance/index-v1.json"
        original = path.read_bytes()
        payload = json.loads(original)
        payload["entries"].append(payload["entries"][0])
        path.write_text(json.dumps(payload))
        with self.assertRaises(self.api.ProvenanceError):
            self.build()
        path.write_bytes(original)
        payload = json.loads(original)
        payload["entries"][0]["body_sha256"] = "0" * 64
        path.write_text(json.dumps(payload))
        with self.assertRaises(self.api.ProvenanceError):
            self.build()

    def test_recovery_after_uncatchable_directory_swap_interruption(self):
        self.build()
        previous = (self.root / self.anchor.destination).read_bytes()
        target = self.root / "docs/provenance"
        target.rename(self.root / "docs/.provenance-last-complete")
        self.api.build_successor(self.source, self.anchor, self.root, POLICY, import_date="2026-10-01", replace_existing=True)
        self.assertTrue((self.root / self.anchor.destination).read_bytes() == previous, "recovered pair differs")

    def test_invalid_date_and_source_markers_write_nothing(self):
        with self.assertRaises(self.api.ProvenanceError):
            self.api.build_successor(self.source, self.anchor, self.root, POLICY, import_date="bad")
        self.source.write_bytes(self.api.START_MARKER + self.body)
        anchor = self.api.PredecessorAnchor("source", hashlib.sha256(self.source.read_bytes()).hexdigest(), "docs/provenance/record.md")
        with self.assertRaises(self.api.ProvenanceError):
            self.api.build_successor(self.source, anchor, self.root, POLICY, import_date="2026-10-01")
        self.assertFalse((self.root / "docs/provenance").exists(), "malformed source published")


class JsonEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.api = importlib.import_module("tools.docs_public.provenance")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def make(self, body=b'{"counts": [1, 2]}\r\n'):
        source = self.root / "source.json"
        source.write_bytes(body)
        anchor = self.api.PredecessorAnchor("synthetic-json", hashlib.sha256(body).hexdigest(), "docs/provenance/synthetic.json.md")
        record = self.api.build_successor(source, anchor, self.root, POLICY, import_date="2026-10-01")
        return (self.root / record.destination).read_bytes(), record

    def test_json_envelope_rejects_reanchored_suffix(self):
        data, record = self.make()
        changed = data + b"suffix"
        forged = replace(record, predecessor_sha256=hashlib.sha256(changed[record.prefix_bytes:]).hexdigest(),
            body_sha256=hashlib.sha256(changed[record.prefix_bytes:]).hexdigest(), successor_sha256=hashlib.sha256(changed).hexdigest())
        with self.assertRaises(self.api.ProvenanceError):
            self.api.validate_successor(changed, forged)

    def test_json_envelope_preserves_boundary_and_final_byte(self):
        for body in (b"{}", b'{"counts": [1, 2]}\r\n', b"[1,2]\n"):
            with self.subTest(length=len(body)):
                data, record = self.make(body)
                self.assertTrue(self.api.validate_successor(data, record) == body, "JSON body changed")
                self.assertTrue(data[record.prefix_bytes:] == body and data[-1:] == body[-1:], "JSON boundary changed")
                self.assertTrue(json.loads(data[record.prefix_bytes:]) == json.loads(body), "JSON parse changed")
                for changed in (data[:record.prefix_bytes - 1] + data[record.prefix_bytes:], data[:-1] + b"x"):
                    with self.assertRaises(self.api.ProvenanceError):
                        self.api.validate_successor(changed, record)
                # Each iteration intentionally replaces the same logical source.
                (self.root / self.api.INDEX_PATH).unlink()
                (self.root / record.destination).unlink()
                (self.root / "docs/provenance").rmdir()

    def test_empty_single_and_multiple_correction_lists(self):
        anchor = self.api.PredecessorAnchor("synthetic-json", "a" * 64, "docs/provenance/synthetic.json.md")
        for count in (0, 1, 3):
            rows = [(f"release list claim {i}", f"corrected {i}", self.api.AUTHORITY_LINKS[0]) for i in range(count)]
            prefix = self.api._prefix(anchor, "2026-10-01", rows)
            self.assertTrue(prefix.count(b"| [Authority](") == count, "correction cardinality changed")
            self.assertTrue(prefix.endswith(self.api.END_MARKER), "end marker drift")


class CompleteProvenanceTests(unittest.TestCase):
    def test_generated_replay_evidence_preserves_ten_slots_and_rejects_other_markdown(self):
        api = importlib.import_module("tools.docs_public.provenance")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            shutil.copytree(ROOT / "docs", root / "docs")
            (root / "contracts").mkdir()
            shutil.copyfile(ROOT / "contracts/metric-denominators-v1.json",
                            root / "contracts/metric-denominators-v1.json")
            evidence = root / "docs/provenance/HOSTED-REPLAY-EVIDENCE.md"
            evidence.unlink(missing_ok=True)
            expected = api.validate_complete_index(root)
            self.assertEqual(len(expected), 10)
            evidence.write_text("# Separately validated generated replay evidence\n", encoding="utf-8")
            self.assertEqual(api.validate_complete_index(root), expected)
            for name in ("UNAPPROVED.md", "HOSTED-REPLAY-EVIDENCE-copy.md"):
                extra = evidence.with_name(name)
                extra.write_text("# Unapproved document\n", encoding="utf-8")
                with self.assertRaises(api.ProvenanceError) as caught:
                    api.validate_complete_index(root)
                self.assertEqual(str(caught.exception), "provenance.index_completeness")
                extra.unlink()
            self.assertEqual(api.validate_complete_index(root), expected)

    def test_citation_contracts_are_not_successors_and_unknown_files_fail(self):
        api = importlib.import_module("tools.docs_public.provenance")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            shutil.copytree(ROOT / "docs", root / "docs")
            (root / "contracts").mkdir()
            shutil.copyfile(ROOT / "contracts/metric-denominators-v1.json", root / "contracts/metric-denominators-v1.json")
            records = api.validate_complete_index(root)
            self.assertEqual(len(records), 10)
            for name in ("citation-inventory-v1.json", "citation-transitions-v1.json"):
                (root / "docs/provenance" / name).write_text('{"synthetic": true}\n')
            self.assertEqual(len(api.validate_complete_index(root)), 10)
            extra = root / "docs/provenance/unapproved.json"
            extra.write_text('{}\n')
            with self.assertRaises(api.ProvenanceError):
                api.validate_complete_index(root)
            extra.unlink()
            successor = root / records[0].destination
            successor.write_bytes(successor.read_bytes() + b"unexpected suffix")
            with self.assertRaises(api.ProvenanceError):
                api.validate_complete_index(root)

    def test_exact_ten_sources_are_retained(self):
        api = importlib.import_module("tools.docs_public.provenance")
        payload = json.loads((ROOT / api.INDEX_PATH).read_bytes())
        self.assertTrue(len(payload["entries"]) == 10, "exact ten-source inventory is incomplete")
        api.validate_complete_index(ROOT)

    def test_complete_index_rejects_missing_extra_duplicate_order_and_hash_drift(self):
        api = importlib.import_module("tools.docs_public.provenance")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            shutil.copytree(ROOT / "docs", root / "docs")
            index = root / api.INDEX_PATH
            original = json.loads(index.read_bytes())
            rows = original["entries"]
            for changed in ([], rows[:-1], rows + [rows[0]], list(reversed(rows)), rows[1:] + [dict(rows[0], source_label="fabricated")]):
                index.write_text(json.dumps(dict(original, entries=changed)))
                with self.assertRaises(api.ProvenanceError):
                    api.validate_complete_index(root)
            index.write_text(json.dumps(original))
            path = root / rows[0]["destination"]
            data = path.read_bytes()
            path.write_bytes(data[:-1] + b"x")
            with self.assertRaises(api.ProvenanceError):
                api.validate_complete_index(root)

    def test_remaining_body_anchors_json_and_observed_framing(self):
        api = importlib.import_module("tools.docs_public.provenance")
        records = api.validate_complete_index(ROOT)
        lengths = {"spike-001-readme": 5984, "spike-001-json": 7305, "spike-002-json": 8781,
                   "spike-003-readme": 7277, "spike-005-readme": 14578}
        for record in records:
            if record.source_label not in lengths:
                continue
            data = (ROOT / record.destination).read_bytes()
            body = api.validate_successor(data, record)
            self.assertTrue(len(body) == lengths[record.source_label], "body length changed")
            self.assertTrue(record.body_sha256 == record.predecessor_sha256, "predecessor bytes changed")
            banner = data[:record.prefix_bytes]
            for row in api.derive_guidance(body, extended=True):
                old, new, _ = row.render()
                self.assertTrue(old.encode() in banner and new.encode() in banner, "observed guidance missing")
            if record.destination.endswith(".json.md"):
                self.assertTrue(isinstance(json.loads(body), dict), "historical JSON is not parseable")
        self.assertFalse((ROOT / "docs/provenance/spikes/004-job-posting-audit").exists(), "absent spike fabricated")

    def test_json_numeric_corrections_are_contextual_and_membership_is_confirmed(self):
        api = importlib.import_module("tools.docs_public.provenance")
        records = {row.source_label: row for row in api.validate_complete_index(ROOT)}
        checks = {"spike-001-json": (b"128,475", b"128,477", b"charities-undetermined-status"),
                  "spike-002-json": (b"557,065", b"557,067", b"557,289", b"557,291", b"309,624", b"309,626", b"309,212", b"309,214", b"canonical keyed membership")}
        for label, tokens in checks.items():
            record = records[label]
            banner = (ROOT / record.destination).read_bytes()[:record.prefix_bytes]
            self.assertTrue(all(token in banner for token in tokens), "contextual JSON correction missing")

    def test_every_derived_numeric_occurrence_has_a_banner_row(self):
        api = importlib.import_module("tools.docs_public.provenance")
        records = {row.source_label: row for row in api.validate_complete_index(ROOT)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.md"
            project.write_bytes(b"## Superseded Figures and Claims\n| 557,065 | 557,067 |\n")
            for label, kind in (("spike-001-readme", "001"), ("spike-001-json", "001"), ("spike-002-json", "002")):
                record = records[label]
                data = (ROOT / record.destination).read_bytes()
                body = api.validate_successor(data, record)
                source_record = records[f"spike-{kind}-json"]
                historical = root / f"{kind}.json"
                historical.write_bytes(api.validate_successor((ROOT / source_record.destination).read_bytes(), source_record))
                rows = api.derive_spike_corrections(body, ROOT, historical, kind, project if kind == "001" else None)
                self.assertTrue(bool(rows), "numeric correction set is empty")
                for row in rows:
                    old, new, _ = row.render()
                    self.assertTrue(old.encode() in data[:record.prefix_bytes] and new.encode() in data[:record.prefix_bytes], "numeric occurrence lacks banner row")
                if label == "spike-001-readme":
                    totals = [row for row in rows if "total rows" in row.claim]
                    self.assertTrue(len(totals) == 2 and totals[0].body_lines != totals[1].body_lines, "equal occurrences collapsed")

    def test_missing_committed_evidence_authority_fails(self):
        api = importlib.import_module("tools.docs_public.provenance")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            shutil.copytree(ROOT / "docs", root / "docs")
            (root / api.AUTHORITY_LINKS[0]).unlink()
            with self.assertRaises(api.ProvenanceError):
                api.validate_complete_index(root)

    def test_diagnostic_correction_links_to_the_observed_exit_definition(self):
        import re
        api = importlib.import_module("tools.docs_public.provenance")
        records = api.validate_complete_index(ROOT)
        corrected = set()
        for record in records:
            banner = (ROOT / record.destination).read_bytes()[:record.prefix_bytes].decode()
            rows = [line for line in banner.splitlines()
                    if line.startswith("| Historical diagnostic denominator")]
            for row in rows:
                self.assertIn("all observed exits", row)
                self.assertNotIn("D-006", row)
                match = re.search(r"\[Authority\]\(([^)]+)\)", row)
                self.assertIsNotNone(match)
                authority = ((ROOT / record.destination).parent / match.group(1)).resolve()
                self.assertEqual(authority, ROOT / "contracts/metric-denominators-v1.json")
                contract = json.loads(authority.read_bytes())
                definitions = {entry["id"]: entry["definition"] for entry in contract["denominator_definitions"]}
                self.assertIn("All exact-key records", definitions["all_observed_exits_v1"])
                self.assertIn("delinquency_exit_observed", definitions["all_observed_exits_v1"])
                self.assertEqual(contract["diagnostic_role"], "release_quality_diagnostic")
                self.assertEqual(set(contract["measure_ids"]),
                                 {"conditional_precision", "eligible_exit_sensitivity", "all_exit_sensitivity"})
                corrected.add(record.source_label)
        self.assertEqual(corrected, {"spike-002-json", "spike-003-readme", "spike-005-readme"})


class CommittedTracerTests(unittest.TestCase):
    def test_public_tracer_chain_and_correction_rows(self):
        api = importlib.import_module("tools.docs_public.provenance")
        index = json.loads((ROOT / api.INDEX_PATH).read_bytes())
        rows = [item for item in index["entries"] if item["source_label"] == "spike-002-readme"]
        self.assertTrue(len(rows) == 1, "tracer slot missing or duplicated")
        record = api.SuccessorRecord.from_dict(rows[0])
        self.assertTrue(record.predecessor_sha256 == "349b619aa6f6111f7ec9b3e4dbb38e44d1b95934cabdc2ff86a6f96c60fa2e5e", "predecessor anchor drift")
        data = (ROOT / record.destination).read_bytes()
        body = api.validate_successor(data, record)
        banner = data[:record.prefix_bytes]
        for old, new in ((557065, 557067), (557289, 557291), (309624, 309626), (309212, 309214)):
            if f"{old:,}".encode() in body or str(old).encode() in body:
                self.assertTrue(f"{old:,}".encode() in banner and f"{new:,}".encode() in banner, "missing evidence-derived correction")
        for decision in (b"D-001", b"D-007", b"D-008", b"D-010", b"D-012", b"D-003"):
            self.assertTrue(decision in banner, "missing supersession authority")
        self.assertFalse(scan_paths(ROOT, sorted([record.destination, api.INDEX_PATH]), POLICY), "public tracer is unsafe")


class ObservedFramingTests(unittest.TestCase):
    def setUp(self):
        self.api = importlib.import_module("tools.docs_public.provenance")

    def test_guidance_is_observed_and_definition_aware(self):
        body = b"# Historical\nAggregate-only publication.\nPower BI, otherwise Evidence.\n8 releases before analysis.\nArchive census prerequisite.\nPublic Registry Operations Monitor\n"
        rows = self.api.derive_guidance(body)
        self.api.validate_correction_rows(body, rows)
        self.assertTrue(len(rows) == 5, "observed supersession rule missing")
        for decision in ("D-001", "D-007", "D-008", "D-010", "D-012"):
            self.assertTrue(any(decision in row.corrected for row in rows), "governing decision missing")
        self.assertFalse(self.api.derive_guidance(b"# Ordinary historical note\nThere are 6 headings and 8 tasks.\n"), "unobserved rule invented")

    def test_unmatched_or_forged_correction_rows_fail(self):
        body = b"Aggregate-only publication.\n"
        row = self.api.derive_guidance(body)[0]
        for changed in (replace(row, body_lines=(2,)), replace(row, corrected="unsupported successor"), replace(row, body_lines=()), replace(row, body_lines=(1, 1))):
            with self.assertRaises(self.api.ProvenanceError):
                self.api.validate_correction_rows(body, [changed])
        numeric = self.api.CorrectionRow("2026-08-05 total rows", "557,289", "557,291", self.api.AUTHORITY_LINKS[1], (1,))
        with self.assertRaises(self.api.ProvenanceError):
            self.api.validate_correction_rows(body, [numeric])
        numeric_body = b"2026-08-05 total rows: 557,289\n"
        self.api.validate_correction_rows(numeric_body, [numeric])
        for changed in (replace(numeric, claim="unrelated claim"), replace(numeric, corrected="557,300"), replace(numeric, authority="unapproved.md")):
            with self.assertRaises(self.api.ProvenanceError):
                self.api.validate_correction_rows(numeric_body, [changed])

    def test_observed_only_banner_does_not_invent_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source.md"
            body = b"# Historical note\r\nOrdinary unchanged prose.\r\n"
            source.write_bytes(body)
            anchor = self.api.PredecessorAnchor("synthetic-note", hashlib.sha256(body).hexdigest(), "docs/provenance/note.md")
            record = self.api.build_successor(source, anchor, root, POLICY, import_date="2026-10-01")
            data = (root / anchor.destination).read_bytes()
            self.assertTrue(self.api.validate_successor(data, record) == body, "historical body changed")
            self.assertFalse(b"D-007" in data[:record.prefix_bytes], "unobserved publication rule invented")
            with self.assertRaises(self.api.ProvenanceError):
                self.api.build_successor(source, anchor, root, POLICY, import_date="2026-10-01", observed_only=False)

    def test_missing_source_and_wrong_cardinality_write_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            missing = root / "missing.md"
            for sources in ({}, {"migration-report": missing}, {"migration-report": missing, "gate-a-evidence": missing}, {"migration-report": missing, "gate-a-evidence": missing, "extra": missing}):
                with self.assertRaises(self.api.ProvenanceError):
                    self.api.build_core_successors(sources, root, POLICY, project=missing, import_date="2026-10-01")
                self.assertFalse((root / "docs").exists(), "invalid inputs published")


class CommittedCoreTests(unittest.TestCase):
    def setUp(self):
        self.api = importlib.import_module("tools.docs_public.provenance")
        payload = json.loads((ROOT / self.api.INDEX_PATH).read_bytes())
        self.records = [self.api.SuccessorRecord.from_dict(row) for row in payload["entries"]]

    def test_core_original_redacted_successor_chain_and_five_locations(self):
        core = [row for row in self.records if row.redaction_chain is not None]
        self.assertTrue(len(core) == 2, "core slots missing")
        self.assertTrue(sum(len(row.redaction_chain.regions) for row in core) == 5, "redaction cardinality differs")
        payload = json.loads((ROOT / self.api.REDACTION_PATH).read_bytes())
        self.api.validate_redaction_record(payload, self.records)
        for record in core:
            data = (ROOT / record.destination).read_bytes()
            body = self.api.validate_successor(data, record)
            self.assertTrue(body.count(self.api.LOCAL_PATH_TOKEN) == len(record.redaction_chain.regions), "replacement count differs")
            self.assertFalse(scan_paths(ROOT, [record.destination], POLICY), "core candidate is unsafe")
            self.assertTrue(self.api.REDACTION_PATH.split("/")[-1].encode() in data[:record.prefix_bytes], "redaction authority missing")

    def test_forged_missing_extra_or_unrecorded_redaction_fails(self):
        core = [row for row in self.records if row.redaction_chain is not None]
        self.assertTrue(len(core) == 2, "core slots missing")
        record = core[0]
        data = (ROOT / record.destination).read_bytes()
        chain = record.redaction_chain
        for changed in (replace(chain, regions=chain.regions[:-1]), replace(chain, regions=chain.regions + chain.regions[:1]),
                        replace(chain, original_bytes=0), replace(chain, preserved_sha256=("0" * 64,) * len(chain.preserved_sha256))):
            with self.assertRaises(self.api.ProvenanceError):
                self.api.validate_successor(data, replace(record, redaction_chain=changed))
        with self.assertRaises(self.api.ProvenanceError):
            self.api.validate_successor(data, replace(record, redaction_chain=None))
        changed_body = data[record.prefix_bytes:].replace(self.api.LOCAL_PATH_TOKEN, b"[ALTERED]", 1)
        changed = data[:record.prefix_bytes] + changed_body
        with self.assertRaises(self.api.ProvenanceError):
            self.api.validate_successor(changed, replace(record, body_sha256=hashlib.sha256(changed_body).hexdigest(), successor_sha256=hashlib.sha256(changed).hexdigest()))
        payload = self.api.redaction_record(self.records)
        for count in (0, 4, 6):
            with self.assertRaises(self.api.ProvenanceError):
                self.api.validate_redaction_record({**payload, "count": count}, self.records)

    def test_gate_corrections_have_exact_numeric_occurrences(self):
        record = next((row for row in self.records if row.source_label == "gate-a-evidence"), None)
        self.assertTrue(record is not None, "Gate A slot missing")
        data = (ROOT / record.destination).read_bytes()
        body = self.api.validate_successor(data, record)
        banner = data[:record.prefix_bytes]
        self.assertTrue(b"7,733" in body and b"7,733" in banner and b"7,737" in banner and b"D-006" in banner, "definition correction missing")
        self.assertTrue(b"557,065" in body and b"557,065" in banner and b"557,067" in banner, "coverage correction missing")
        self.assertFalse(b"7,758" in banner, "absent denominator correction invented")
        self.assertFalse(b"2026-08-05 to 2026-08-19 entries" in banner, "heading digit treated as transition count")


class CommittedSharedGuidanceTests(unittest.TestCase):
    def test_shared_source_slots_and_unchanged_shared_bodies(self):
        api = importlib.import_module("tools.docs_public.provenance")
        index = json.loads((ROOT / api.INDEX_PATH).read_bytes())
        expected = (
            api.PredecessorAnchor("migration-report", api.CORE_SPECS["migration-report"][1], "docs/provenance/MIGRATION-REPORT.md"),
            api.PredecessorAnchor("gate-a-evidence", api.CORE_SPECS["gate-a-evidence"][1], "docs/provenance/GATE-A-EVIDENCE.md"),
            api.PredecessorAnchor("spike-002-readme", "349b619aa6f6111f7ec9b3e4dbb38e44d1b95934cabdc2ff86a6f96c60fa2e5e", "docs/provenance/spikes/002-entity-change-validation/README.md"),
            api.PredecessorAnchor("spike-manifest", "8004a7891823c98f7ee17c4332190cd217d558e11097ea846c40615d048226f3", "docs/provenance/spikes/MANIFEST.md"),
            api.PredecessorAnchor("spike-conventions", "5d69a9d50c1eaa9d8fb4335617ea065409df92aa94c375d207bc3f2857edd144", "docs/provenance/spikes/CONVENTIONS.md"),
        )
        actual = {row["source_label"]: row for row in index["entries"]}
        self.assertTrue(all(anchor.source_label in actual and actual[anchor.source_label]["predecessor_sha256"] == anchor.predecessor_sha256
            and actual[anchor.source_label]["destination"] == anchor.destination for anchor in expected), "existing five anchors changed")
        records = api.validate_index(ROOT, tuple(api.PredecessorAnchor(row["source_label"], row["predecessor_sha256"], row["destination"]) for row in index["entries"]))
        for record in records:
            if record.source_label not in ("spike-manifest", "spike-conventions"):
                continue
            data = (ROOT / record.destination).read_bytes()
            body = api.validate_successor(data, record)
            self.assertTrue(len(body) == (3320 if record.source_label == "spike-manifest" else 2715), "shared body size differs")
            self.assertTrue(record.body_sha256 == record.predecessor_sha256, "shared body bytes differ")
            banner = data[:record.prefix_bytes]
            for row in api.derive_guidance(body):
                old, new, authority = row.render()
                self.assertTrue(old.encode() in banner and new.encode() in banner, "observed supersession row missing")
        self.assertFalse((ROOT / "docs/provenance/spikes/004-job-posting-audit").exists(), "missing spike fabricated")

    def test_index_rejects_missing_extra_and_duplicate_records(self):
        api = importlib.import_module("tools.docs_public.provenance")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source.md"
            body = b"# Synthetic historical body\n"
            source.write_bytes(body)
            anchor = api.PredecessorAnchor("synthetic-slot", hashlib.sha256(body).hexdigest(), "docs/provenance/body.md")
            api.build_successor(source, anchor, root, POLICY, import_date="2026-10-01")
            api.validate_index(root, (anchor,))
            extra = api.PredecessorAnchor("missing-slot", anchor.predecessor_sha256, "docs/provenance/missing.md")
            for expected in ((), (anchor, extra), (anchor, anchor)):
                with self.assertRaises(api.ProvenanceError):
                    api.validate_index(root, expected)
            (root / "docs/provenance/unindexed.md").write_bytes(body)
            with self.assertRaises(api.ProvenanceError):
                api.validate_index(root, (anchor,))


class CoreTransactionTests(unittest.TestCase):
    def setUp(self):
        self.api = importlib.import_module("tools.docs_public.provenance")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "docs").mkdir()
        (self.root / "docs/previous.md").write_bytes(b"Existing public documentation.\n")

    def synthetic_inputs(self, total=5):
        sources, specs = {}, {}
        for label, count in (("migration-report", max(0, total-1)), ("gate-a-evidence", min(1, total))):
            sentinel = b"C:" + b"\\SyntheticWorkspace"
            body = b"# Synthetic historical record\n" + (b"`" + sentinel + b"`\n") * count
            source = self.root / (label + ".md")
            source.write_bytes(body)
            regions, cursor = [], 0
            for _ in range(count):
                start = body.index(sentinel, cursor)
                end = start + len(sentinel)
                regions.append((start, end, body[:start].count(b"\n") + 1, hashlib.sha256(sentinel).hexdigest()))
                cursor = end
            safe = body.replace(sentinel, self.api.LOCAL_PATH_TOKEN)
            specs[label] = (len(body), hashlib.sha256(body).hexdigest(), hashlib.sha256(safe).hexdigest(), tuple(regions))
            sources[label] = source
        return sources, specs

    def snapshot(self):
        return {path.relative_to(self.root): path.read_bytes() for path in (self.root / "docs").rglob("*") if path.is_file()}

    def import_inputs(self, sources, specs):
        with patch.dict(self.api.CORE_SPECS, specs, clear=True), patch.object(self.api, "derive_core_corrections", return_value=[]):
            return self.api.build_core_successors(sources, self.root, POLICY, project=self.root / "unused.md", import_date="2026-10-01")

    def test_core_transaction_publishes_complete_five_region_record(self):
        sources, specs = self.synthetic_inputs()
        records = self.import_inputs(sources, specs)
        self.assertTrue(len(records) == 2, "core transaction incomplete")
        self.assertTrue(json.loads((self.root / self.api.REDACTION_PATH).read_bytes())["count"] == 5, "redaction record missing")
        self.assertTrue((self.root / "docs/previous.md").read_bytes() == b"Existing public documentation.\n", "unrelated public documentation changed")
        self.assertFalse((self.root / ".docs-provenance-last-complete").exists(), "backup left after success")

    def test_wrong_cardinality_and_second_source_drift_leave_destinations_unchanged(self):
        before = self.snapshot()
        for total in (0, 4, 6):
            sources, specs = self.synthetic_inputs(total)
            with self.assertRaises(self.api.ProvenanceError):
                self.import_inputs(sources, specs)
            self.assertTrue(self.snapshot() == before, "wrong-cardinality candidate replaced destination")
        sources, specs = self.synthetic_inputs()
        sources["gate-a-evidence"].write_bytes(b"changed original")
        with self.assertRaises(self.api.ProvenanceError):
            self.import_inputs(sources, specs)
        self.assertTrue(self.snapshot() == before, "second source drift replaced destination")

    def test_record_scan_failure_does_not_replace_any_destination(self):
        sources, specs = self.synthetic_inputs()
        before = self.snapshot()
        original_scan = self.api.scan_paths
        def reject_record(root, paths, policy):
            return [object()] if self.api.REDACTION_PATH in paths else original_scan(root, paths, policy)
        with patch.object(self.api, "scan_paths", side_effect=reject_record):
            with self.assertRaises(self.api.ProvenanceError):
                self.import_inputs(sources, specs)
        self.assertTrue(self.snapshot() == before, "unsafe record replaced destination")

    def test_interrupted_common_directory_swap_restores_all_previous_docs(self):
        sources, specs = self.synthetic_inputs()
        before = self.snapshot()
        original_replace = self.api.os.replace
        def interrupt(source, destination):
            if Path(destination) == self.root / "docs":
                if Path(source) == self.root / ".docs-provenance-last-complete":
                    return original_replace(source, destination)
                raise KeyboardInterrupt()
            return original_replace(source, destination)
        with patch.object(self.api.os, "replace", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.import_inputs(sources, specs)
        self.assertTrue(self.snapshot() == before, "interruption failed to restore documentation")


if __name__ == "__main__":
    unittest.main()
