"""Value-free scanner and byte-preserving provenance contracts."""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
