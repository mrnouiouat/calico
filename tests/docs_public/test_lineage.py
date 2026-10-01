"""Safe fixture lineage contracts, using disposable source definitions only."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.docs_public import lineage
from tools.privacy_scan.scanner import scan_text


def rehash(document):
    content = {key: value for key, value in document.items() if key != "projection_sha256"}
    document["projection_sha256"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    return document


class ManifestProjectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="calico-lineage-test-")
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve()
        (self.project / "models").mkdir()
        names = ("alpha", "beta", "gamma")
        for name in names:
            (self.project / "models" / f"{name}.sql").write_text("select 1\n", encoding="utf-8")
        nodes = {f"model.calico_registry.{name}": {
            "name": name, "unique_id": f"model.calico_registry.{name}", "resource_type": "model",
            "package_name": "calico_registry", "original_file_path": f"models/{name}.sql",
        } for name in reversed(names)}
        source = "source.calico_registry.runtime_input.revision_catalog"
        self.manifest = {
            "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json",
                         "dbt_version": "1.10.23"},
            "nodes": nodes,
            "sources": {source: {"unique_id": source, "name": "revision_catalog",
                                 "package_name": "calico_registry", "resource_type": "source"}},
            "parent_map": {"model.calico_registry.alpha": [source],
                           "model.calico_registry.beta": ["model.calico_registry.alpha"],
                           "model.calico_registry.gamma": ["model.calico_registry.alpha"]},
        }

    def project_graph(self, manifest=None, *, require_paths=False):
        raw = json.dumps(self.manifest if manifest is None else manifest).encode()
        return lineage.project_manifest(raw, project_dir=self.project, require_paths=require_paths)

    def assert_rejected(self, fn, category=None):
        with self.assertRaises(lineage.LineageProjectionError) as caught:
            fn()
        self.assertRegex(str(caught.exception), r"^lineage\.[a-z_]+$")
        if category:
            self.assertEqual(caught.exception.category, category)

    def test_projects_only_closed_fields_and_immediate_edges(self):
        graph = self.project_graph()
        self.assertEqual(set(graph), {"schema_version", "manifest_schema_version", "mode", "nodes",
                                      "sources", "edges", "model_hashes", "projection_sha256"})
        self.assertEqual(len(graph["nodes"]), 3)
        self.assertEqual(len(graph["sources"]), 1)
        self.assertEqual(len(graph["edges"]), 3)
        self.assertNotIn({"from": graph["sources"][0]["id"], "to": "model.calico_registry.gamma"}, graph["edges"])

    def test_equal_parent_edges_remain_distinct_and_sort_stably(self):
        graph = self.project_graph()
        matching = [edge for edge in graph["edges"] if edge["from"] == "model.calico_registry.alpha"]
        self.assertEqual([edge["to"] for edge in matching], ["model.calico_registry.beta", "model.calico_registry.gamma"])
        shuffled = copy.deepcopy(self.manifest)
        shuffled["nodes"] = dict(reversed(list(shuffled["nodes"].items())))
        shuffled["parent_map"] = dict(reversed(list(shuffled["parent_map"].items())))
        self.assertEqual(lineage.encode_projection(graph), lineage.encode_projection(self.project_graph(shuffled)))

    def test_runtime_metadata_and_sql_never_enter_projection(self):
        manifest = copy.deepcopy(self.manifest)
        marker = "opaque_runtime_metadata"
        manifest["metadata"].update({"invocation_id": marker, "profiles_dir": marker})
        for node in manifest["nodes"].values():
            node.update({"compiled_code": marker, "raw_code": marker, "root_path": "/" + "Users" + "/" + marker})
        graph = self.project_graph(manifest)
        encoded = lineage.encode_projection(graph).decode()
        self.assertNotIn(marker, encoded)
        self.assertNotIn(marker, lineage.render_mermaid(graph))
        self.assertEqual(scan_text("docs/evidence/dbt-lineage-v1.json", encoded), [])
        self.assertEqual(graph, self.project_graph())

    def test_known_non_models_and_external_package_models_are_filtered(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["nodes"]["test.calico_registry.fixture"] = {"resource_type": "test"}
        manifest["nodes"]["model.external.fixture"] = {"resource_type": "model", "package_name": "external"}
        self.assertEqual(self.project_graph(manifest), self.project_graph())

    def test_duplicate_json_keys_fail_before_projection(self):
        raw = b'{"nodes":{},"nodes":{}}'
        self.assert_rejected(lambda: lineage.project_manifest(raw, project_dir=self.project), "lineage.duplicate_key")

    def test_malformed_and_wrong_version_manifests_fail(self):
        for raw in (b"[", b"null", b"[]", b"{}", b'{"nodes":NaN}'):
            with self.subTest(case=raw[:1]):
                self.assert_rejected(lambda: lineage.project_manifest(raw, project_dir=self.project))
        for version in ("1.11.0", "1.10", None):
            manifest = copy.deepcopy(self.manifest)
            manifest["metadata"]["dbt_version"] = version
            self.assert_rejected(lambda: self.project_graph(manifest), "lineage.invalid_manifest_version")

    def test_empty_and_missing_source_models_fail(self):
        for nodes in ({}, {key: value for key, value in self.manifest["nodes"].items() if key.endswith("alpha")}):
            manifest = copy.deepcopy(self.manifest)
            manifest["nodes"] = nodes
            self.assert_rejected(lambda: self.project_graph(manifest))

    def test_invalid_name_id_and_source_file_fail_value_free(self):
        for field, value in (("name", "bad<label>"), ("unique_id", "model.calico_registry.other"),
                             ("original_file_path", "../outside.sql")):
            manifest = copy.deepcopy(self.manifest)
            manifest["nodes"]["model.calico_registry.alpha"][field] = value
            self.assert_rejected(lambda: self.project_graph(manifest))

    def test_unknown_resource_and_source_fail(self):
        for field in ("nodes", "sources"):
            for resource in ("unknown", [], None):
                manifest = copy.deepcopy(self.manifest)
                next(iter(manifest[field].values()))["resource_type"] = resource
                self.assert_rejected(lambda: self.project_graph(manifest), "lineage.unknown_resource")

    def test_unknown_endpoint_and_duplicate_parent_fail(self):
        for parents, category in ((["model.calico_registry.unknown"], "lineage.unknown_endpoint"),
                                  (["model.calico_registry.alpha"] * 2, "lineage.duplicate_edge")):
            manifest = copy.deepcopy(self.manifest)
            manifest["parent_map"]["model.calico_registry.beta"] = parents
            self.assert_rejected(lambda: self.project_graph(manifest), category)

    def test_missing_parent_and_cycle_fail(self):
        manifest = copy.deepcopy(self.manifest)
        del manifest["parent_map"]["model.calico_registry.alpha"]
        self.assert_rejected(lambda: self.project_graph(manifest))
        manifest = copy.deepcopy(self.manifest)
        manifest["parent_map"]["model.calico_registry.alpha"] = ["model.calico_registry.beta"]
        self.assert_rejected(lambda: self.project_graph(manifest), "lineage.cyclic_graph")

    def test_required_mart_paths_fail_closed(self):
        self.assert_rejected(lambda: self.project_graph(require_paths=True), "lineage.required_path_missing")

    def test_source_hash_drift_is_rejected(self):
        graph = self.project_graph()
        (self.project / "models" / "alpha.sql").write_text("select 2\n", encoding="utf-8")
        self.assert_rejected(lambda: lineage.validate_projection(graph, project_dir=self.project), "lineage.source_drift")

    def test_symlink_source_is_rejected(self):
        path = self.project / "models" / "alias.sql"
        path.symlink_to(self.project / "models" / "alpha.sql")
        self.assert_rejected(self.project_graph, "lineage.invalid_source")

    def test_projection_unknown_fields_and_invalid_hashes_fail(self):
        for mutation in (lambda g: g.update({"raw_code": "opaque"}),
                         lambda g: g["nodes"][0].update({"root_path": "opaque"}),
                         lambda g: g["model_hashes"][0].update({"sha256": "invalid"}),
                         lambda g: g["model_hashes"].append(g["model_hashes"][0])):
            graph = self.project_graph()
            mutation(graph)
            self.assert_rejected(lambda: lineage.render_mermaid(graph))

    def test_duplicate_nodes_and_edges_fail_before_rendering(self):
        for field, category in (("nodes", "lineage.duplicate_node"), ("edges", "lineage.duplicate_edge")):
            graph = self.project_graph()
            graph[field].append(copy.deepcopy(graph[field][0]))
            self.assert_rejected(lambda: lineage.render_mermaid(graph), category)

    def test_unknown_render_endpoint_and_content_drift_fail(self):
        graph = self.project_graph()
        graph["edges"][0]["to"] = "model.calico_registry.unknown"
        self.assert_rejected(lambda: lineage.render_mermaid(graph), "lineage.unknown_endpoint")
        graph = self.project_graph()
        graph["model_hashes"][0]["sha256"] = "0" * 64
        self.assert_rejected(lambda: lineage.render_mermaid(graph), "lineage.content_drift")

    def test_renderer_is_deterministic_and_bound_to_source_hashes(self):
        graph = self.project_graph()
        rendered = lineage.render_mermaid(graph)
        self.assertTrue(rendered.startswith("flowchart LR\n"))
        self.assertEqual(rendered.count(" --> "), 3)
        self.assertEqual(rendered, lineage.render_mermaid(graph))
        (self.project / "models" / "alpha.sql").write_text("select 2\n", encoding="utf-8")
        self.assertNotEqual(rendered, lineage.render_mermaid(self.project_graph()))

    def test_renderer_rejects_empty_graph_and_accepts_valid_single_node(self):
        graph = self.project_graph()
        graph["nodes"] = [graph["nodes"][0]]
        graph["sources"] = []
        graph["edges"] = []
        graph["model_hashes"] = [graph["model_hashes"][0]]
        rehash(graph)
        self.assertEqual(lineage.render_mermaid(graph).count('["alpha"]'), 1)
        graph["nodes"] = []
        self.assert_rejected(lambda: lineage.render_mermaid(graph))


class CommittedFixtureEvidenceTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def evidence(self):
        path = self.root / "docs" / "evidence" / "dbt-lineage-v1.json"
        graph = json.loads(path.read_bytes()) if path.is_file() else None
        self.assertIsInstance(graph, dict, "Safe fixture lineage evidence must be generated")
        return graph

    def test_committed_projection_matches_actual_fixture_docs(self):
        from calico_dbt import runner
        graph = self.evidence()
        outcome = runner.docs()
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertEqual(graph, outcome.lineage)
        self.assertEqual(len(graph["nodes"]), 32)

    def test_committed_projection_has_current_source_hashes_and_required_paths(self):
        graph = self.evidence()
        lineage.validate_projection(graph, project_dir=self.root / "dbt", require_paths=True)
        self.assertEqual(lineage.encode_projection(graph),
                         (self.root / "docs" / "evidence" / "dbt-lineage-v1.json").read_bytes())

    def test_required_path_validation_rejects_a_severed_real_graph(self):
        graph = self.evidence()
        graph["edges"] = []
        rehash(graph)
        with self.assertRaises(lineage.LineageProjectionError) as caught:
            lineage.validate_projection(graph, require_paths=True)
        self.assertEqual(caught.exception.category, "lineage.required_path_missing")


if __name__ == "__main__":
    unittest.main()
