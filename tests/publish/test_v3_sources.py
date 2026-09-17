"""Successor sources are explicit, governed, and independent of live activation."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from calico_dbt import runner
from calico_publish.allowlist import AllowlistError, load_allowlist
from calico_publish.export import StagedExport
from calico_publish.manifest import project_published_manifest, validate_published_manifest_document
from tests.publish.test_manifest import inputs
from tests.dbt_longitudinal.test_public_models import _public_models_fixture_store

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/publication-exports-v3.json"


class SuccessorSourceTests(unittest.TestCase):
    def test_banner_uses_promoted_same_date_successor_revision(self):
        # Execute the actual dbt SQL against a synthetic revision catalog.
        promotion = (ROOT / "dbt/models/intermediate/int_promoted_releases.sql").read_text(encoding="utf-8")
        banner = (ROOT / "dbt/models/marts/mart_publication_status.sql").read_text(encoding="utf-8")
        import re
        promotion = re.sub(r"\{\{.*?\}\}", lambda match: (
            "runtime_input.revision_catalog" if "revision_catalog" in match[0]
            else "runtime_input.promotion_catalog" if "promotion_catalog" in match[0]
            else ""), promotion, flags=re.S)
        banner = re.sub(r"\{\{.*?\}\}", lambda match: (
            "int_promoted_releases" if "ref(" in match[0] else ""), banner, flags=re.S)
        with duckdb.connect() as connection:
            connection.execute("create schema runtime_input")
            connection.execute("create table runtime_input.revision_catalog "
                               "(as_of_date date, release_revision integer, revision_fingerprint varchar, parser_contract_version varchar)")
            connection.execute("insert into runtime_input.revision_catalog values "
                               "('2032-01-05', 1, 'synthetic-a', 'synthetic-v1'),"
                               "('2032-02-16', 1, 'synthetic-b', 'synthetic-v1'),"
                               "('2032-02-16', 2, 'synthetic-c', 'synthetic-v1')")
            connection.execute("create table runtime_input.promotion_catalog as "
                               "select as_of_date, release_revision, revision_fingerprint "
                               "from runtime_input.revision_catalog where release_revision=2")
            connection.execute("create view int_promoted_releases as " + promotion)
            rows = connection.execute(banner).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(str(rows[0][0]), "2032-02-16")
            self.assertEqual(rows[0][1], 2)

    def test_complete_authority_and_explicit_manifest_round_trip(self):
        authority = load_allowlist(CONTRACT)
        self.assertEqual(authority.supersedes, "publication-exports-v2")
        self.assertEqual(len(authority.exports), 12)
        self.assertEqual(len(authority.control_sources), 1)
        control = authority.control_sources[0]
        self.assertEqual(control.export_name, "capture_status")
        self.assertEqual(control.fixed_path, "capture-status.json")
        self.assertEqual(control.source_contract, "capture-status-v3")
        self.assertEqual(control.columns, ("outcome", "reason_category", "ended_at_utc",
                         "newer_attempt_not_accepted", "source_publication_state",
                         "source_publication_retired_on"))
        old = load_allowlist(ROOT / "contracts/publication-exports-v2.json")
        old_by_name = {e.export_name: e for e in old.exports}
        for entry in authority.exports:
            if entry.export_name not in ("dim_public_organizations", "mart_publication_status"):
                self.assertEqual(entry, old_by_name[entry.export_name])
        dim = next(e for e in authority.exports if e.export_name == "dim_public_organizations")
        self.assertEqual(dim.columns, old_by_name[dim.export_name].columns +
                         ("latest_release_observation_state",))
        arguments = inputs()
        arguments["allowlist"] = authority
        arguments["staged_exports"] = tuple(
            StagedExport(e.export_name, e.file_name, "exports/" + e.file_name, "a" * 64, 0)
            for e in authority.exports
        )
        result = project_published_manifest(**arguments)
        document = json.loads(result.to_json())
        validate_published_manifest_document(document, allowlist=authority)
        self.assertEqual(document["allowlist_version"], "publication-exports-v3")

    def test_invalid_control_declarations_fail_closed(self):
        original = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for field, value in (("fixed_path", "../private.json"), ("format", "csv"),
                             ("source_contract", "capture-status-v2"),
                             ("export_class", "aggregate"), ("extra", True),
                             ("columns", ["unapproved"])):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                changed = copy.deepcopy(original)
                changed["control_sources"][0][field] = value
                path = Path(tmp) / "authority.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(AllowlistError) as caught:
                    load_allowlist(path)
                self.assertEqual(str(caught.exception), "allowlist.invalid_schema")

    def test_fixture_publisher_controls_status_only_updates_and_interruptions(self):
        import hashlib
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from calico_capture.status import project_safe_status, project_publication_status
        from calico_publish.cli import main
        from calico_publish.gate import GateError, verify
        from calico_publish.transaction import TransactionError
        from tests.publish.test_transaction import PublicationFixture, _git
        authority = load_allowlist(CONTRACT)
        with tempfile.TemporaryDirectory() as temp:
            fixture = PublicationFixture(Path(temp))
            (fixture.staging / "exports/public.csv").unlink()
            (fixture.staging / "manifest/published-manifest-v1.json").unlink()
            def fixture_build(**kwargs):
                return runner.build(fixture_store_factory=_public_models_fixture_store, **kwargs)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["export", "--mode", "fixture", "--staging", str(fixture.staging)],
                            build_runner=fixture_build)
            self.assertEqual(code, 0)
            manifest_path = fixture.staging / "manifest/published-manifest-v1.json"
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(document["allowlist_version"], "publication-exports-v3")
            self.assertEqual(len(document["exports"]), 12)
            self.assertNotIn("capture_status", {item["export_name"] for item in document["exports"]})
            def status_for(outcome, reason, published=False, index=0):
                status = project_safe_status(trigger="local", outcome=outcome, reason_category=reason,
                    started_at_utc="2032-01-01T00:00:00Z", ended_at_utc=f"2032-01-01T00:00:{index:02d}Z")
                return project_publication_status(status.to_dict(), publication_succeeded=published).to_json()
            status_path = fixture.staging / "capture-status.json"
            with self.assertRaises(GateError) as caught:
                verify(fixture.staging, authority, manifest_path,
                       source_lists=("synthetic_source",), verify_controls=True)
            self.assertEqual(caught.exception.category, "gate.control_source_missing")
            status_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(GateError) as caught:
                verify(fixture.staging, authority, manifest_path,
                       source_lists=("synthetic_source",), verify_controls=True)
            self.assertEqual(caught.exception.category, "gate.control_source_invalid")
            status_path.write_text(status_for("accepted", "none"), encoding="utf-8")
            self.assertTrue(verify(fixture.staging, authority, manifest_path,
                                  source_lists=("synthetic_source",), verify_controls=True).passed)
            paths = tuple(sorted(["exports/" + item.file_name for item in authority.exports] +
                                 ["manifest/published-manifest-v1.json"]))
            # The fetched parent must carry a complete v3 control; malformed
            # controls fail before any publication object can be written.
            from calico_publish.transaction import publish_tree
            def publish(**kwargs):
                return publish_tree(repo_dir=fixture.repo, staging_dir=fixture.staging,
                    staged_files=paths, remote="origin", target_ref="published-data",
                    commit_subject="Publish synthetic governed successor", author_name="Synthetic Publisher",
                    author_email="synthetic" + "@" + "example.invalid", allowlist=authority, **kwargs)
            with self.assertRaises(TransactionError) as caught:
                publish()
            self.assertEqual(caught.exception.category, "transaction.control_source_invalid")
            (fixture.repo / "capture-status.json").write_bytes(status_path.read_bytes())
            _git(fixture.repo, "add", "capture-status.json")
            _git(fixture.repo, "commit", "-m", "Stage the synthetic pending attempt")
            _git(fixture.repo, "push", "origin", "HEAD:published-data")
            before = _git(fixture.repo, "ls-remote", "origin", "refs/heads/published-data").split()[0]
            for interruption in ("before_write_tree", "before_commit_tree", "after_commit_tree", "before_push"):
                with self.subTest(interruption=interruption):
                    def stop(stage):
                        if stage == interruption:
                            raise RuntimeError("synthetic interruption")
                    with self.assertRaises(RuntimeError):
                        publish(failure_hook=stop)
                    self.assertEqual(_git(fixture.repo, "ls-remote", "origin", "refs/heads/published-data").split()[0], before)
            result = publish()
            self.assertEqual(result.status, "published")
            _git(fixture.repo, "fetch", "origin", "published-data")
            _git(fixture.repo, "checkout", "-B", "published-data", "FETCH_HEAD")
            hashes = {path: hashlib.sha256((fixture.repo / path).read_bytes()).hexdigest() for path in paths}
            control_oid = _git(fixture.repo, "rev-parse", "HEAD:capture-status.json")
            self.assertEqual(control_oid, _git(fixture.repo, "rev-parse", f"{before}:capture-status.json"))
            replays = (("accepted", "none", True), ("no_new_release", "source_not_advanced", False),
                       ("rejected", "structural_rejection", False),
                       ("operational_error", "warehouse_build_error", False), ("accepted", "none", False))
            for index, (outcome, reason, published) in enumerate(replays, 1):
                prior = _git(fixture.repo, "rev-parse", "HEAD")
                payload = status_for(outcome, reason, published, index)
                (fixture.repo / "capture-status.json").write_text(payload, encoding="utf-8")
                status_path.write_text(payload, encoding="utf-8")
                _git(fixture.repo, "add", "capture-status.json")
                _git(fixture.repo, "commit", "-m", "Update the synthetic safe attempt status")
                _git(fixture.repo, "push", "origin", "HEAD:published-data")
                self.assertEqual(_git(fixture.repo, "diff", "--name-only", prior, "HEAD"), "capture-status.json")
                self.assertEqual(hashes, {path: hashlib.sha256((fixture.repo / path).read_bytes()).hexdigest() for path in paths})
                self.assertTrue(verify(fixture.repo, authority,
                    fixture.repo / "manifest/published-manifest-v1.json",
                    source_lists=("synthetic_source",), verify_controls=True).passed)

    def test_fixture_sql_sources_and_promoted_identity(self):
        authority = load_allowlist(CONTRACT)
        def inspect(path):
            with duckdb.connect(str(path), read_only=True) as connection:
                banner = connection.execute(
                    "select published_as_of_date, published_release_revision from mart_publication_status"
                ).fetchall()
                expected = connection.execute(
                    "select as_of_date, release_revision from int_promoted_releases "
                    "order by as_of_date desc limit 1"
                ).fetchall()
                self.assertEqual(banner, expected)
                self.assertEqual(len(banner), 1)
                self.assertEqual(connection.execute(
                    "select count(*) from dim_public_organizations d cross join mart_publication_status p "
                    "where d.latest_release_observation_state <> case when exists "
                    "(select 1 from int_keyed_snapshots s where "
                    "s.state_charity_registration_number=d.state_charity_registration_number "
                    "and s.as_of_date=p.published_as_of_date "
                    "and s.release_revision=p.published_release_revision) "
                    "then 'observed' else 'not_observed' end"
                ).fetchone()[0], 0)
                states = connection.execute(
                    "select distinct latest_release_observation_state from dim_public_organizations"
                ).fetchall()
                self.assertEqual(set(states), {("observed",), ("not_observed",)})
                for entry in authority.exports:
                    columns = {row[0] for row in connection.execute(
                        "select column_name from information_schema.columns where table_name=?",
                        [entry.source_relation],
                    ).fetchall()}
                    self.assertTrue(set(entry.columns) <= columns)
        outcome = runner.build(mode="fixture", fixture_store_factory=_public_models_fixture_store,
                               export=inspect)
        self.assertEqual(outcome.status, "success", outcome.category)


if __name__ == "__main__":
    unittest.main()
