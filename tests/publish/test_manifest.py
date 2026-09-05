"""Closed public provenance, safe construction, and deterministic ordering."""

import copy
import dataclasses
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from calico_publish import manifest as module
from calico_landing.contracts import LOGICAL_LIST_ORDER
from calico_publish.allowlist import load_allowlist
from calico_publish.export import StagedExport
from calico_publish.gate import GateError, verify

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    authority = load_allowlist(ROOT / "contracts/publication-exports-v1.json")
    return dict(
        allowlist=authority,
        staged_exports=tuple(StagedExport(e.export_name, e.file_name,
                             "exports/" + e.file_name, "a" * 64, 0)
                             for e in authority.exports),
        accepted_releases=(module.AcceptedRelease(
            "2026-01-01",
            1,
            "b" * 64,
            tuple(
                module.SourceObjectRecord(name, str(index) * 64, 0, 0)
                for index, name in enumerate(sorted(LOGICAL_LIST_ORDER), start=1)
            ),
        ),),
        eligible_key_count=0, parser_contract_version="registry-csv-contract-v1",
        toolchain=dict(python="3.13.15", dbt_core="1.10.23", dbt_duckdb="1.10.1", duckdb="1.5.5"),
    )


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.arguments = inputs()
        self.manifest = module.project_published_manifest(**self.arguments)
        self.document = self.manifest.to_dict()

    def reject(self, document, category):
        with self.assertRaises(module.ManifestError) as raised:
            module.validate_published_manifest_document(document)
        self.assertEqual(str(raised.exception), category)

    def test_round_trip_determinism_and_zero_named_exports(self):
        serialized = self.manifest.to_json()
        self.assertEqual(serialized, module.project_published_manifest(**self.arguments).to_json())
        self.assertEqual(set(json.loads(serialized)), module.MANIFEST_DOCUMENT_KEYS)
        self.assertEqual(serialized, json.dumps(self.document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
        module.validate_published_manifest_document(json.loads(serialized))
        self.assertEqual(self.document["eligible_key_count"], 0)
        self.assertTrue(all(e["row_count"] == 0 for e in self.document["exports"]))
        self.assertFalse(re.search(r"[A-Za-z]:[\\/]|/[A-Za-z]", serialized))
        def check(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertFalse(re.search(r"time|timestamp|exception|message|error|credential", key))
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(self.document)

    def test_all_document_error_categories(self):
        changes = [
            ("manifest.invalid_schema", lambda d: d.update(extra="unsafe")),
            ("manifest.unknown_schema_version", lambda d: d.update(schema_version=2)),
            ("manifest.unknown_allowlist_version", lambda d: d.update(allowlist_version="other")),
            ("manifest.duplicate_export_name", lambda d: d["exports"].append(copy.deepcopy(d["exports"][0]))),
            ("manifest.duplicate_accepted_release", lambda d: d["accepted_releases"].append(copy.deepcopy(d["accepted_releases"][0]))),
            ("manifest.unsorted_exports", lambda d: d["exports"].reverse()),
            ("manifest.empty_exports", lambda d: d.update(exports=[])),
            ("manifest.empty_accepted_releases", lambda d: d.update(accepted_releases=[])),
            ("manifest.invalid_hash", lambda d: d["exports"][0].update(sha256="wrong")),
            ("manifest.negative_count", lambda d: d.update(eligible_key_count=-1)),
        ]
        for category, mutate in changes:
            with self.subTest(category=category):
                document = copy.deepcopy(self.document)
                mutate(document)
                self.reject(document, category)
        self.assertEqual(module.MANIFEST_ERROR_CATEGORIES, {c for c, _ in changes} | {"manifest.missing_input"})

    def test_unknown_and_missing_keys_at_every_level(self):
        for path in ((), ("toolchain",), ("exports", 0), ("accepted_releases", 0),
                     ("accepted_releases", 0, "source_objects", 0)):
            for extra in (True, False):
                document = copy.deepcopy(self.document)
                target = document
                for key in path:
                    target = target[key]
                if extra:
                    target["private_detail"] = "unsafe"
                else:
                    del target[next(iter(target))]
                self.reject(document, "manifest.invalid_schema")

    def test_wrong_numeric_types_and_negative_counts(self):
        for version in (True, 1.0, "1", None):
            document = copy.deepcopy(self.document)
            document["schema_version"] = version
            self.reject(document, "manifest.unknown_schema_version")
        for value in (-1, True, 1.0, "0", None):
            for path, key in (((), "eligible_key_count"), (("exports", 0), "row_count"),
                              (("accepted_releases", 0), "release_revision"),
                              (("accepted_releases", 0, "source_objects", 0), "byte_size")):
                document = copy.deepcopy(self.document)
                target = document
                for part in path:
                    target = target[part]
                target[key] = value
                self.reject(document, "manifest.negative_count")

    def test_version_fields_reject_paths_and_arbitrary_text(self):
        for value in ("C:" + "\\" + "private", "/" + "home/private", "provider failure", "", None):
            for key in ("parser_contract_version", "python", "dbt_core", "dbt_duckdb", "duckdb"):
                document = copy.deepcopy(self.document)
                (document if key == "parser_contract_version" else document["toolchain"])[key] = value
                self.reject(document, "manifest.invalid_schema")

    def test_allowlist_identity_grain_and_filename_checked_independently(self):
        for changes in (dict(export_name="unapproved", file_name="unapproved.csv"),
                        dict(grain=["unapproved"]), dict(file_name="another.csv")):
            document = copy.deepcopy(self.document)
            document["exports"][0].update(changes)
            document["exports"].sort(key=lambda e: e["export_name"])
            self.reject(document, "manifest.invalid_schema")
        with self.assertRaises(module.ManifestError):
            dataclasses.replace(self.manifest, exports=self.manifest.exports[:-1])

    def test_narrow_fixture_authority_round_trips(self):
        arguments = inputs()
        arguments["allowlist"] = dataclasses.replace(arguments["allowlist"], exports=arguments["allowlist"].exports[:1])
        arguments["staged_exports"] = arguments["staged_exports"][:1]
        fixture_sources = ("synthetic_source",)
        arguments["accepted_releases"] = (
            module.AcceptedRelease(
                "2026-01-01",
                1,
                "b" * 64,
                (
                    module.SourceObjectRecord(
                        "synthetic_source",
                        "c" * 64,
                        0,
                        0,
                        _source_lists=fixture_sources,
                    ),
                ),
            ),
        )
        arguments["source_lists"] = fixture_sources
        result = module.project_published_manifest(**arguments)
        with self.assertRaises(module.ManifestError):
            module.validate_published_manifest_document(json.loads(result.to_json()))
        module.validate_published_manifest_document(
            result.to_dict(),
            allowlist=arguments["allowlist"],
            source_lists=fixture_sources,
        )

    def test_missing_and_malformed_builder_inputs_are_safe(self):
        for key in self.arguments:
            arguments = dict(self.arguments)
            arguments[key] = None
            with self.subTest(field=key), self.assertRaises(module.ManifestError):
                module.project_published_manifest(**arguments)
        for key in ("staged_exports", "accepted_releases"):
            for value in ((), (object(),), "unsafe", 1):
                arguments = dict(self.arguments, **{key: value})
                with self.assertRaises(module.ManifestError):
                    module.project_published_manifest(**arguments)
        arguments = dict(self.arguments, staged_exports=())
        with self.assertRaises(module.ManifestError) as raised:
            module.project_published_manifest(**arguments)
        self.assertEqual(raised.exception.category, "manifest.missing_input")

    def test_records_validate_at_construction(self):
        for call in (lambda: module.SourceObjectRecord("valid", "bad", 0, 0),
                     lambda: module.AcceptedRelease("2026-01-01", 1, "a" * 64, (object(),)),
                     lambda: module.ExportRecord("valid", "valid.csv", "a" * 64, 0, None),
                     lambda: dataclasses.replace(self.manifest, toolchain=None),
                     lambda: dataclasses.replace(self.manifest, accepted_releases=(object(),))):
            with self.assertRaises(module.ManifestError):
                call()

    def test_records_and_release_sort_order_fail_closed(self):
        release = self.document["accepted_releases"][0]
        source = release["source_objects"][0]
        for sources in ([source, source], [dict(source, source_list="z"), dict(source, source_list="a")]):
            document = copy.deepcopy(self.document)
            document["accepted_releases"][0]["source_objects"] = sources
            self.reject(document, "manifest.invalid_schema")
        document = copy.deepcopy(self.document)
        document["accepted_releases"] = [dict(release, release_revision=2), release]
        self.reject(document, "manifest.invalid_schema")

    def test_synthetic_authority_reaches_the_same_gate_validator(self):
        arguments = inputs()
        entry = dataclasses.replace(arguments["allowlist"].exports[0],
            export_name="fixture_named_history", file_name="fixture_named_history.csv")
        authority = dataclasses.replace(arguments["allowlist"], exports=(entry,))
        payload = (",".join(entry.columns) + "\n").encode("utf-8")
        arguments.update(allowlist=authority, staged_exports=(StagedExport(
            entry.export_name, entry.file_name, "exports/" + entry.file_name,
            hashlib.sha256(payload).hexdigest(), 0),))
        manifest = module.project_published_manifest(**arguments)
        self.reject(manifest.to_dict(), "manifest.invalid_schema")
        with tempfile.TemporaryDirectory(prefix="calico-manifest-") as temp:
            root = Path(temp)
            (root / "exports").mkdir()
            (root / "exports" / entry.file_name).write_bytes(payload)
            path = root / "manifest.json"
            path.write_bytes(manifest.to_json().encode("ascii"))
            self.assertTrue(
                verify(
                    root,
                    authority,
                    path,
                ).passed
            )
            changed = manifest.to_dict()
            changed["exports"][0]["grain"] = ["unapproved"]
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(GateError) as raised:
                verify(
                    root,
                    authority,
                    path,
                )
            self.assertEqual(str(raised.exception), "gate.manifest_invalid_schema")

    def test_nested_invalid_hashes_and_counts(self):
        for path, key in ((("accepted_releases", 0), "revision_fingerprint"),
                          (("accepted_releases", 0, "source_objects", 0), "sha256")):
            for value in (None, {}, "A" * 64, "a" * 63, "a" * 64 + "\n"):
                document = copy.deepcopy(self.document)
                target = document
                for part in path:
                    target = target[part]
                target[key] = value
                self.reject(document, "manifest.invalid_hash")
        for value in ([], None, {}, ["same", "same"], [1]):
            document = copy.deepcopy(self.document)
            document["exports"][0]["grain"] = value
            self.reject(document, "manifest.invalid_schema")

    def test_frozen_records_and_toolchain_do_not_retain_mutable_inputs(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.manifest.eligible_key_count = 1
        before = self.manifest.to_json()
        self.arguments["toolchain"]["python"] = "unsafe"
        self.assertEqual(before, self.manifest.to_json())

    def test_builder_sorts_releases_and_exports(self):
        arguments = inputs()
        first = arguments["accepted_releases"][0]
        arguments["accepted_releases"] = (dataclasses.replace(first, release_revision=2), first)
        arguments["staged_exports"] = tuple(reversed(arguments["staged_exports"]))
        document = module.project_published_manifest(**arguments).to_dict()
        self.assertEqual([r["release_revision"] for r in document["accepted_releases"]], [1, 2])
        self.assertEqual([e["export_name"] for e in document["exports"]], sorted(e["export_name"] for e in document["exports"]))

    def test_schema_matches_document_and_closed_records(self):
        schema = json.loads((ROOT / "contracts/published-manifest-v1.schema.json").read_text(encoding="utf-8"))
        for node in (schema, schema["properties"]["toolchain"], *schema["$defs"].values()):
            self.assertIs(node["additionalProperties"], False)
            self.assertEqual(set(node["required"]), set(node["properties"]))
        self.assertEqual(set(schema["required"]), module.MANIFEST_DOCUMENT_KEYS)
        self.assertEqual(schema["properties"]["exports"]["minItems"], 1)
        for key in ("python", "dbt_core", "dbt_duckdb", "duckdb"):
            pattern = schema["properties"]["toolchain"]["properties"][key]["pattern"]
            self.assertTrue(re.fullmatch(pattern, self.document["toolchain"][key]))
            self.assertFalse(re.fullmatch(pattern, "/" + "private/path"))


if __name__ == "__main__":
    unittest.main()
