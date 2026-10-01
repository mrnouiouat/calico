<!-- calico-provenance-v1:start -->
**Historical record — contains superseded figures and guidance.**

Imported 2026-10-01. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.

The original body below is preserved byte for byte. Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.

| Superseded claim | Corrected successor | Evidence or decision |
|---|---|---|
| 2026-07-15 charities-undetermined-status parsed rows (parser repair) (body lines 20): 128,475 | 128,477 | [Authority](../../../evidence/gate-a/spike-001-successor-v1.json) |
| 2026-07-15 total rows (parser repair) (body lines 41): 557,065 | 557,067 | [Authority](../../../evidence/gate-a/spike-001-successor-v1.json) |
| Archive census prerequisite (body lines 4): Archive census prerequisite | Archive census is outside v1 (D-012) | [Authority](../../../decisions/register.md) |

Current authority: [Gate A correction index](../../../evidence/gate-a/correction-index-v1.json), [recomputed spike evidence](../../../evidence/gate-a/spike-002-successor-v1.json), and [controlling decisions](../../../decisions/register.md).

<!-- calico-provenance-v1:end -->
{
  "manifest_version": 1,
  "audit_date": "2026-08-21",
  "scope": "Bounded representative validation only; not a complete archive census or backfill.",
  "expected_headers": ["Registry Status","State Charity Reg#","FEIN","SOS/FTB#","Name","City","State","Issue Date","Last Renewal","Date Status Set","As-of Date"],
  "entries": [
    {
      "logical_release":"2019-02-04","list":"charities-may-operate","format":"xlsx","original_url":"https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities_report_may_list.xlsx","wayback_timestamp":"20190226194219","replay_url":"https://web.archive.org/web/20190226194219id_/https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities_report_may_list.xlsx","http_status":200,"received_bytes":10559445,"sha256":"f9d3944a8d9d34139a75f93905e2bcd1d6591b5b91cbbd3452074f45b53e9e2e","parsed_rows":131932,"as_of_values":["2019/02/04"],"validation":"valid","notes":["Header at worksheet row 5 after three explanatory rows and one blank row.","25 otherwise populated records have blank Registry Status."]
    },
    {
      "logical_release":"2022-02-16","list":"charities-may-operate","format":"csv","original_url":"https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-may-operate.csv","wayback_timestamp":"20220221161457","replay_url":"https://web.archive.org/web/20220221161457id_/https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-may-operate.csv","http_status":200,"received_bytes":37445905,"sha256":"cd3e886de59549e80fd62998652a023c32cf006bcec2881ce77869273aa03ca3","parsed_rows":128239,"as_of_values":["2022/02/16"],"validation":"valid"
    },
    {
      "logical_release":"2026-07-15","list":"charities-may-operate","format":"csv","original_url":"https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-may-operate.csv","wayback_timestamp":"20260731075400","replay_url":"https://web.archive.org/web/20260731075400id_/https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-may-operate.csv","http_status":200,"received_bytes":49187517,"sha256":"d08bad94695e025200f8274edf6c4e2b70969277598d519ce78481f4a7eaf1c7","parsed_rows":168450,"as_of_values":["2026/07/15"],"validation":"valid"
    },
    {
      "logical_release":"2026-07-15","list":"charities-not-operating","format":"csv","original_url":"https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-not-operating.csv","wayback_timestamp":"20260731075611","replay_url":"https://web.archive.org/web/20260731075611id_/https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-not-operating.csv","http_status":200,"received_bytes":44301189,"sha256":"a98220def7b17a5e831513cfcaec915bc301ec999e80843a0b35f9483c570efa","parsed_rows":151716,"as_of_values":["2026/07/15"],"validation":"valid"
    },
    {
      "logical_release":"2026-07-15","list":"charities-undetermined-status","format":"csv","original_url":"https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-undetermined-status.csv","wayback_timestamp":"20260731075500","replay_url":"https://web.archive.org/web/20260731075500id_/https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-undetermined-status.csv","http_status":200,"received_bytes":37515401,"sha256":"b30f9d9d74bbdce7c139f708e8895147ce1e2976b11c17506336795f15bb1363","parsed_rows":128475,"physical_data_lines":128477,"as_of_values":["2026/07/15"],"validation":"valid","notes":["Two extra physical lines occur inside quoted CSV records."]
    },
    {
      "logical_release":"2026-07-15","list":"charities-may-not-operate","format":"csv","original_url":"https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-may-not-operate.csv","wayback_timestamp":"20260731075350","replay_url":"https://web.archive.org/web/20260731075350id_/https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-may-not-operate.csv","http_status":200,"received_bytes":31659925,"sha256":"759e067df30b552c3b90840e08b5551d175d23a25277a66ce25b3dc223767d8b","parsed_rows":108424,"as_of_values":["2026/07/15"],"validation":"valid"
    },
    {
      "logical_release":"2024-11-20","list":"charities-may-operate","format":"csv","original_url":"https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-may-operate.csv","wayback_timestamp":"20241126181756","replay_url":"https://web.archive.org/web/20241126181756id_/https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-may-operate.csv","http_status":200,"received_bytes":42466845,"sha256":"b8e11f97bffbdfcaa7fbef97264566e43144b11041e8e58237c00e4c2c81c886","parsed_rows":145434,"as_of_values":["2024/11/20"],"validation":"file-valid-release-rejected"
    },
    {
      "logical_release":"2024-11-20","list":"charities-not-operating","format":"csv","original_url":"https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-not-operating.csv","wayback_timestamp":"20241126182446","replay_url":"https://web.archive.org/web/20241126182446id_/https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-not-operating.csv","http_status":200,"received_bytes":43367373,"sha256":"4b4237ebf724a8c246a9b84dcfeec02d27df3c5bbf363c99e6d7024bd9ff693a","parsed_rows":148518,"as_of_values":["2024/11/20"],"validation":"file-valid-release-rejected"
    },
    {
      "logical_release":"2024-11-20","list":"charities-may-not-operate","format":"csv","original_url":"https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-may-not-operate.csv","wayback_timestamp":"20241126181954","replay_url":"https://web.archive.org/web/20241126181954id_/https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-may-not-operate.csv","http_status":200,"advertised_bytes":36560561,"received_bytes":5550683,"partial_prefix_sha256":"4f5e73550a3cd1ca7a0e6302bed344691fe8673fa42193d457f894ce196c7fa1","complete_rows_before_truncation":19008,"truncated_final_field_count":6,"as_of_values":["2024/11/20"],"validation":"invalid-truncated","notes":["curl error 18; SHA-256 is for the received prefix only, not the advertised object."]
    },
    {
      "logical_release":"2024-11-20","list":"charities-undetermined-status","format":"csv","original_url":"https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-undetermined-status.csv","wayback_timestamp":"20241126182331","replay_url":"https://web.archive.org/web/20241126182331id_/https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/charities-undetermined-status.csv","http_status":200,"advertised_bytes":37845945,"received_bytes":2340154,"partial_prefix_sha256":"76f5d478c7eb94a9256ff19d23734ff25bb77ac0a08762993e179ca1321d3588","complete_rows_before_truncation":8011,"truncated_final_field_count":8,"as_of_values":["2024/11/20"],"validation":"invalid-truncated","notes":["curl error 18; SHA-256 is for the received prefix only, not the advertised object."]
    }
  ],
  "release_verdicts": {
    "2019-02-04":"single-file representative valid; full four-list atomicity not tested",
    "2022-02-16":"single-file representative valid; full four-list atomicity not tested",
    "2026-07-15":"complete four-file release valid; 557065 total rows",
    "2024-11-20":"rejected; two of four archive replay bodies truncated"
  }
}
