from __future__ import annotations
import json, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
VIS=ROOT/'powerbi/Calico.Report/definition/pages/release_quality/visuals'
def load(n): return json.loads((VIS/n/'visual.json').read_text(encoding='utf-8'))
def refs(p):
    out=set()
    def walk(v):
        if isinstance(v,dict):
            if isinstance(v.get('queryRef'),str): out.add(v['queryRef'])
            for x in v.values(): walk(x)
        elif isinstance(v,list):
            for x in v: walk(x)
    walk(p); return out
class ReleaseQualityPageTests(unittest.TestCase):
    def test_release_health_has_versions_schema_and_capture_counts(self):
        r=refs(load('release_health'))
        for n in 'release_health_state parser_contract_version schema_contract_version schema_added_column_count schema_removed_column_count schema_type_changed_column_count release_flag_rule_versions capture_attempted_count capture_succeeded_count capture_failed_count capture_unavailable_count'.split(): self.assertIn(f'mart_release_quality.{n}',r)
    def test_coverage_has_raw_denominator_bounds_and_category_selector(self):
        r=refs(load('coverage'))
        for n in 'published_delinquent_category keyed_record_count unkeyed_record_count raw_total_record_count keyed_coverage_proportion keyed_coverage_wilson_95_lower keyed_coverage_wilson_95_upper'.split(): self.assertIn(f'mart_release_snapshot_metrics.{n}',r)
        self.assertTrue(all(not item.startswith('mart_registry_population_coverage.') for item in r))
        categories=refs(load('coverage_categories'))
        for n in 'as_of_date release_revision source_list source_reported_registry_status coverage_class record_count'.split(): self.assertIn(f'mart_registry_population_coverage.{n}',categories)
        self.assertTrue(all(not item.startswith('mart_release_snapshot_metrics.') for item in categories))
        self.assertIn('never sum across categories',json.dumps(load('coverage')))
    def test_exact_three_diagnostic_roles_have_full_support(self):
        p=load('last_renewal_diagnostic'); r=refs(p)
        for n in 'measure_name diagnostic_role diagnostic_eligible_count numerator_count denominator_count measure_value wilson_95_lower wilson_95_upper from_as_of_date from_release_revision to_as_of_date to_release_revision gap_days'.split(): self.assertIn(f'mart_last_renewal_diagnostic.{n}',r)
        text=json.dumps(p); self.assertIn('do not define an event',text); self.assertIn('published-manifest-v1.json',text)
    def test_no_raw_hash_source_or_named_table(self):
        text=' '.join(json.dumps(load(n)).lower() for n in ('release_health','coverage','coverage_categories','last_renewal_diagnostic'))
        for bad in ('dim_public_organizations','fct_public_status_observations','sha256','source payload'): self.assertNotIn(bad,text)
if __name__=='__main__': unittest.main()
