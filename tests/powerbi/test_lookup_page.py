from __future__ import annotations
import json, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
VIS=ROOT/'powerbi/Calico.Report/definition/pages/organization_lookup/visuals'
def load(name): return json.loads((VIS/name/'visual.json').read_text(encoding='utf-8'))
def refs(payload):
    found=set()
    def walk(value):
        if isinstance(value,dict):
            if isinstance(value.get('queryRef'),str): found.add(value['queryRef'])
            for child in value.values(): walk(child)
        elif isinstance(value,list):
            for child in value: walk(child)
    walk(payload); return found

class LookupPageTests(unittest.TestCase):
    def test_every_selected_detail_is_guarded(self):
        for name in ('latest_observed','missing_warning','dated_history'):
            payload=load(name); text=json.dumps(payload)
            self.assertIn('dim_public_organizations.Exact Registration Selection Guard',refs(payload))
            self.assertIn('"condition": "equals", "value": 1',text)

    def test_latest_warning_and_history_use_exported_fields(self):
        latest=refs(load('latest_observed'))
        for field in ('latest_source_reported_status','latest_observed_as_of_date','latest_observed_release_revision'):
            self.assertIn(f'dim_public_organizations.{field}',latest)
        warning=json.dumps(load('missing_warning'))
        self.assertIn('latest_release_observation_state',warning)
        self.assertIn('Not observed in the latest accepted release',warning)
        history=load('dated_history'); history_refs=refs(history)
        for field in ('as_of_date','release_revision','observation_state','source_reported_status'):
            self.assertIn(f'fct_public_status_observations.{field}',history_refs)
        self.assertEqual([item['direction'] for item in history['visual']['query']['queryState']['sortDefinition']['sort']],['Descending','Descending'])

    def test_static_verification_correction_lineage_and_caveats_remain_visible(self):
        payload=load('verification_and_caveats'); text=json.dumps(payload,ensure_ascii=False)
        for required in ('https://oag.ca.gov/charities/reports','https://ca-rcf.evokeplatform.com/app/publicPortal/verification',
                         'source-display-correction.yml','2026-08-19','2026-09-02','approximately 15 days',
                         'real-time','does not independently establish','Source files â†’ accepted release â†’ tested dbt models â†’ published export',
                         'published-manifest-v1.json','one due-diligence input'):
            self.assertIn(required,text)
        self.assertNotIn('state_charity_registration_number=',text)
        self.assertIn('dim_public_organizations.official_verification_instructions',refs(payload))

    def test_correction_template_requests_only_safe_categories(self):
        text=(ROOT/'.github/ISSUE_TEMPLATE/source-display-correction.yml').read_text(encoding='utf-8').lower()
        for required in ('correction category','published release date and revision','description','do not post excluded identifiers','street addresses','personal information'):
            self.assertIn(required,text)
        for forbidden in ('upload','screenshot request','ein:', 'fein:'):
            self.assertNotIn(forbidden,text)

if __name__=='__main__': unittest.main()
