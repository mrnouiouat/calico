"""Executable numeric and language boundaries for the approved descriptive claim."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from calico_dbt import runner
from tests.fixtures.dbt_foundation.fixture_builder import gate_b_fixture_store_v2


ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "dbt/models/marts/mart_claim_support.sql"
METADATA = ROOT / "dbt/models/marts/metrics.yml"
ASSERTION = ROOT / "dbt/tests/assert_claim_support.sql"
CONTRACT = ROOT / "contracts/claim-support-v1.json"


class ClaimSupportDbtTests(unittest.TestCase):
    def test_fixture_build_executes_claim_support_and_independent_assertion(self) -> None:
        outcome = runner.build(
            mode="fixture", fixture_store_factory=gate_b_fixture_store_v2
        )
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, 31)

    def test_support_relation_has_exact_identity_free_schema(self) -> None:
        expected = (
            "claim_support_version",
            "from_as_of_date", "from_release_revision", "from_revision_fingerprint",
            "to_as_of_date", "to_release_revision", "to_revision_fingerprint", "gap_days",
            "matched_start_status", "matched_end_population",
            "support_count", "support_denominator_count", "support_denominator_id",
            "support_share_of_matched_entries",
            "total_matched_entry_count", "matched_entry_denominator_id",
            "total_entrant_count", "total_entrant_denominator_id",
            "dominant_source_reported_status_date", "dominant_status_date_count",
            "dominant_status_date_denominator_count", "dominant_status_date_denominator_id",
            "dominant_status_date_share", "next_from_as_of_date", "next_to_as_of_date",
            "next_gap_days", "next_total_matched_entry_count",
        )
        captured: dict[str, tuple[str, ...]] = {}

        def inspector(facade: "runner.FixtureBuildInspection") -> None:
            rows = facade._query(  # noqa: SLF001 -- closed constant fixture-only projection
                "select column_name from information_schema.columns "
                "where table_schema = 'main' and table_name = 'mart_claim_support' "
                "order by ordinal_position"
            )
            captured["columns"] = tuple(row[0] for row in rows)

        outcome = runner.build(
            mode="fixture",
            fixture_store_factory=gate_b_fixture_store_v2,
            inspector=inspector,
        )
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertEqual(captured["columns"], expected)


class ClaimSupportSqlContractTests(unittest.TestCase):
    def test_sql_supports_exact_pair_denominators_date_share_and_next_pair(self) -> None:
        text = MODEL.read_text(encoding="utf-8")
        for token in (
            "ref('int_entity_transitions')", "ref('mart_adjacent_pair_metrics')",
            "'Current - Reporting Incomplete'", "published_delinquent_population_v1",
            "matched_observed_entry_population_v1", "all_entrant_population_v1",
            "claim_support_status_movement_v1", "lead(", "row_number() over",
            "end_source_reported_current_status_date",
        ):
            self.assertIn(token, text)
        self.assertNotIn("state_charity_registration_number", text)
        self.assertIsNone(re.search(r"\bfein\b|\bein\b", text, re.IGNORECASE))

    def test_assertion_independently_recomputes_and_has_safe_failure_rows(self) -> None:
        text = ASSERTION.read_text(encoding="utf-8")
        self.assertIn("ref('int_entity_transitions')", text)
        self.assertIn("full outer join", text.lower())
        self.assertIn("failure_reason", text)
        self.assertNotIn("state_charity_registration_number", text)
        self.assertIsNone(re.search(r"\bfein\b|\bein\b", text, re.IGNORECASE))


class ClaimLanguageContractTests(unittest.TestCase):
    def test_contract_is_canonical_closed_and_linked_to_the_relation(self) -> None:
        raw = CONTRACT.read_bytes()
        document = json.loads(raw)
        canonical = (
            json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n"
        ).encode()
        self.assertEqual(raw, canonical)
        self.assertEqual(set(document), {
            "approved_wording", "claim_contract_version", "governed_surfaces",
            "numeric_support", "prohibited_claims", "required_vocabulary",
        })
        self.assertEqual(document["claim_contract_version"], 1)
        support = document["numeric_support"]
        self.assertEqual(support["relation"], "mart_claim_support")
        self.assertEqual(support["relation_version"], "claim_support_status_movement_v1")
        self.assertEqual(set(support["denominator_ids"]), {
            "matched_observed_entry_population_v1",
            "all_entrant_population_v1",
            "claim_support_status_movement_v1",
        })
        model = MODEL.read_text(encoding="utf-8")
        for denominator_id in support["denominator_ids"]:
            self.assertIn(denominator_id, model)

    def test_contract_carries_exact_approved_wording_and_closed_vocabulary(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(document["required_vocabulary"], [
            "published delinquent population", "observed exit", "not observed",
            "source-reported", "release-quality diagnostic",
        ])
        wording = document["approved_wording"]
        self.assertIn("7,737 matched organizations were observed moving", wording)
        self.assertIn("source-reported July 17 status date", wording)
        self.assertIn("total delinquency entries fell from 7,750 to 2", wording)
        self.assertIn("descriptive claim", wording)
        self.assertIn(
            "do not establish internal cause, exact processing time, or workflow",
            wording,
        )
        prohibited = document["prohibited_claims"]
        self.assertEqual(len(prohibited), 10)
        self.assertEqual(len({item["id"] for item in prohibited}), len(prohibited))
        self.assertTrue(all(set(item) == {"id", "fragments"} for item in prohibited))

    def test_governed_metadata_and_docs_use_required_terms_without_prohibited_phrases(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        governed = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8")
            for relative in document["governed_surfaces"]
        ).lower()
        for term in document["required_vocabulary"]:
            self.assertIn(term.lower(), governed)
        for category in document["prohibited_claims"]:
            for fragment in category["fragments"]:
                self.assertNotIn(fragment.lower(), governed)

    def test_contract_does_not_create_publication_machinery(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        serialized = json.dumps(document).lower()
        for forbidden_key in (
            "export_allowlist", "publication_allowlist", "public_fields",
            "api_schema", "orm_model", "power_bi_export",
        ):
            self.assertNotIn(forbidden_key, serialized)


if __name__ == "__main__":
    unittest.main()
