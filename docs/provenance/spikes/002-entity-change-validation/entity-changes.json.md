<!-- calico-provenance-v1:start -->
**Historical record — contains superseded figures and guidance.**

Imported 2026-10-01. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.

The original body below is preserved byte for byte. Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.

| Superseded claim | Corrected successor | Evidence or decision |
|---|---|---|
| 2026-07-15 rows (parser repair) (body lines 20): 557,065 | 557,067 | [Authority](../../../evidence/gate-a/spike-002-successor-v1.json) |
| 2026-07-15 keyless_rows (parser repair) (body lines 20): 309,624 | 309,626 | [Authority](../../../evidence/gate-a/spike-002-successor-v1.json) |
| 2026-08-05 rows (parser repair) (body lines 21): 557,289 | 557,291 | [Authority](../../../evidence/gate-a/spike-002-successor-v1.json) |
| 2026-08-05 keyless_rows (parser repair) (body lines 21): 309,212 | 309,214 | [Authority](../../../evidence/gate-a/spike-002-successor-v1.json) |
| 2026-07-15 canonical keyed membership (body lines 24): 0f153d45d0102eb3951bf876d00db440f6e5b5adfbe087d9dc2446e3b0c0c0fe | 0f153d45d0102eb3951bf876d00db440f6e5b5adfbe087d9dc2446e3b0c0c0fe; confirmed by committed recomputation, no recalculation here | [Authority](../../../evidence/gate-a/spike-002-successor-v1.json) |
| 2026-08-05 canonical keyed membership (body lines 25): 974bd2be8fc0995bce483263982a6fbe50334503962a36399cf7786d017d6c87 | 974bd2be8fc0995bce483263982a6fbe50334503962a36399cf7786d017d6c87; confirmed by committed recomputation, no recalculation here | [Authority](../../../evidence/gate-a/spike-002-successor-v1.json) |
| Historical diagnostic denominator (body lines 31, 38, 40): Historical diagnostic denominator | Historical strict-cure percentages are not current governed metrics; the current last-renewal diagnostic uses all observed exits independently of parser repair (D-006) | [Authority](../../../decisions/register.md) |

Current authority: [Gate A correction index](../../../evidence/gate-a/correction-index-v1.json), [recomputed spike evidence](../../../evidence/gate-a/spike-002-successor-v1.json), and [controlling decisions](../../../decisions/register.md).

<!-- calico-provenance-v1:end -->
{
  "comparison": {
    "from": "2026-07-15",
    "to": "2026-08-05",
    "identity_key": "trimmed nonblank State Charity Reg#",
    "delinquent_statuses": ["Delinquent", "Delinquent - Late Fees Due"],
    "canonical_membership_hash": "Sort registration numbers ordinally, append LF to every value, UTF-8 encode, then SHA-256 hash."
  },
  "source_files": [
    {"release":"2026-07-15","list":"charities-may-operate","wayback_timestamp":"20260731075400","rows":168450,"raw_bytes":49187517,"raw_sha256":"d08bad94695e025200f8274edf6c4e2b70969277598d519ce78481f4a7eaf1c7"},
    {"release":"2026-07-15","list":"charities-not-operating","wayback_timestamp":"20260731075611","rows":151716,"raw_bytes":44301189,"raw_sha256":"a98220def7b17a5e831513cfcaec915bc301ec999e80843a0b35f9483c570efa"},
    {"release":"2026-07-15","list":"charities-undetermined-status","wayback_timestamp":"20260731075500","rows":128475,"raw_bytes":37515401,"raw_sha256":"b30f9d9d74bbdce7c139f708e8895147ce1e2976b11c17506336795f15bb1363"},
    {"release":"2026-07-15","list":"charities-may-not-operate","wayback_timestamp":"20260731075350","rows":108424,"raw_bytes":31659925,"raw_sha256":"759e067df30b552c3b90840e08b5551d175d23a25277a66ce25b3dc223767d8b"},
    {"release":"2026-08-05","list":"charities-may-operate","rows":161337,"raw_bytes":47110521,"raw_sha256":"d5d5579c1cd1a3cdf0b8961a75f9f6a68b497920c94b7f0364006fd59f3b6168"},
    {"release":"2026-08-05","list":"charities-not-operating","rows":151962,"raw_bytes":44373021,"raw_sha256":"4d8ef4a702f7d24eab602a32b39f5ae79064bf343d31698b653d10d2991413cc"},
    {"release":"2026-08-05","list":"charities-undetermined-status","rows":127945,"raw_bytes":37360641,"raw_sha256":"260bf3266297f364c85395b8f8078ea32de1ae2762d5eca41e4e26b709cfdded"},
    {"release":"2026-08-05","list":"charities-may-not-operate","rows":116045,"raw_bytes":33885257,"raw_sha256":"4972d783377c81c464483c8d7a5cf44719d6f4a1831355005e8714397bfda7de"}
  ],
  "release_coverage": {
    "2026-07-15": {"rows":557065,"keyed_entities":247441,"keyless_rows":309624,"duplicate_or_conflicting_keys":0},
    "2026-08-05": {"rows":557289,"keyed_entities":248077,"keyless_rows":309212,"duplicate_or_conflicting_keys":0}
  },
  "membership_sets": {
    "from_keyed":{"count":247441,"sha256":"0f153d45d0102eb3951bf876d00db440f6e5b5adfbe087d9dc2446e3b0c0c0fe"},
    "to_keyed":{"count":248077,"sha256":"974bd2be8fc0995bce483263982a6fbe50334503962a36399cf7786d017d6c87"},
    "intersection":{"count":247436,"sha256":"e617a3a127cd3fb1ece4edbf09eb351c6fb052db3e65c6bfba3689ed51dbbc89"},
    "added":{"count":641,"sha256":"956c3280a88780dcad26c827c86573c44d51fea8fea14104fa5e1e9e8c4ca6c2"},
    "removed":{"count":5,"sha256":"b20aec970417fd737c84f2d86c6159cadcd1a62e904032b518d4741761492754"},
    "changed_status":{"count":15757,"sha256":"1f52bb20c1d1b106b177b368ae86e2c74d7bc7b702cd4f5e0c85ef1e517041a6"},
    "starting_delinquent":{"count":5476,"sha256":"1bd1e5e5fbf36e7360672df02e1b640d871f727c3863a3fde434a5029108ce08"},
    "strict_to_current":{"count":53,"sha256":"f481495e3cff944d8456889c03b4a23a3c5e02df991ca1b83b7b9f4a33590c9c"},
    "all_delinquent_exits":{"count":65,"sha256":"f7a59f0e23960e9713391fe9721d1cb8fe3865c0ba2f2c3b902a9f14eaeca410"},
    "newly_delinquent":{"count":7758,"sha256":"e0a78e39d2195a2feaa0479409f2ce3dd81d7a20341ffdc2cacf264640830c44"}
  },
  "keyed_entity_changes": {"intersection":247436,"added":641,"removed":5,"changed_status":15757,"unchanged_status":231679},
  "added_statuses": {"Current":575,"Current - In Process":31,"Current - Reporting Incomplete":26,"Delinquent":8,"Registered - Corporate Trustee":1},
  "removed_statuses": {"Current":2,"Current - In Process":1,"Mutual Benefit":1,"Revoked":1},
  "delinquency": {"starting_delinquent":5476,"strict_to_current":53,"all_exits":65,"disappearances":0,"newly_delinquent":7758,"net_change":7693},
  "starting_delinquent_outcomes": {"Current":53,"Current - In Process":5,"Current - Reporting Incomplete":3,"Delinquent":5334,"Delinquent - Late Fees Due":77,"Dissolution Waiver Issued":1,"Suspended":3},
  "last_renewal_diagnostic": {"registry_wide_clears":182,"clears_among_starting_delinquent":46,"strict_cures_with_clear":40,"conditional_precision":0.8695652173913043,"registry_wide_precision":0.21978021978021978,"conditional_sensitivity_among_strict_cures_with_populated_start":1.0,"unconditional_sensitivity":0.7547169811320755},
  "nonzero_status_changes": [
    {"from":"Current - Reporting Incomplete","to":"Delinquent","count":7733},
    {"from":"Current - In Process","to":"Current","count":3985},
    {"from":"Current","to":"Current - In Process","count":2168},
    {"from":"Current - In Process","to":"Current - Reporting Incomplete","count":774},
    {"from":"Current - Reporting Incomplete","to":"Current","count":509},
    {"from":"Current","to":"Current - Reporting Incomplete","count":128},
    {"from":"Current","to":"Dissolution Waiver Issued","count":56},
    {"from":"Delinquent","to":"Current","count":45},
    {"from":"Current - Awaiting Reporting","to":"Current - In Process","count":41},
    {"from":"Revoked","to":"Current - Probationary Registration","count":34},
    {"from":"Delinquent","to":"Delinquent - Late Fees Due","count":30},
    {"from":"Current","to":"Dissolution Pending","count":27},
    {"from":"Dissolution Waiver Issued","to":"Dissolved","count":26},
    {"from":"Suspended","to":"Current","count":22},
    {"from":"Current - Probationary Registration","to":"Current","count":19},
    {"from":"Current - In Process","to":"Dissolution Waiver Issued","count":18},
    {"from":"Current - Reporting Incomplete","to":"Current - In Process","count":16},
    {"from":"Current - In Process","to":"Current - Awaiting Reporting","count":10},
    {"from":"Current - Reporting Incomplete","to":"Dissolution Waiver Issued","count":10},
    {"from":"Current - Awaiting Reporting","to":"Current - Reporting Incomplete","count":9},
    {"from":"Dissolution Pending","to":"Dissolution Waiver Issued","count":9},
    {"from":"Delinquent - Late Fees Due","to":"Current","count":8},
    {"from":"Suspended","to":"Delinquent - Late Fees Due","count":8},
    {"from":"Current","to":"Withdrawn","count":5},
    {"from":"Current - In Process","to":"Dissolution Pending","count":5},
    {"from":"Current - Probationary Registration","to":"Current - In Process","count":5},
    {"from":"Delinquent","to":"Current - In Process","count":5},
    {"from":"Current - Awaiting Reporting","to":"Current","count":4},
    {"from":"Current - Awaiting Reporting","to":"Delinquent","count":4},
    {"from":"Current - Reporting Incomplete","to":"Delinquent - Late Fees Due","count":4},
    {"from":"Dissolution Pending","to":"Current","count":3},
    {"from":"Current","to":"Current - Awaiting Reporting","count":2},
    {"from":"Current - Awaiting Reporting","to":"Withdrawn","count":2},
    {"from":"Current - Reporting Incomplete","to":"Current - Awaiting Reporting","count":2},
    {"from":"Current - Reporting Incomplete","to":"Dissolution Pending","count":2},
    {"from":"Current - Reporting Incomplete","to":"Dissolved","count":2},
    {"from":"Current - Reporting Incomplete","to":"Withdrawn","count":2},
    {"from":"Delinquent","to":"Current - Reporting Incomplete","count":2},
    {"from":"Delinquent","to":"Suspended","count":2},
    {"from":"Exempt - Religious","to":"Exempt - Dissolution Waiver Issued","count":2},
    {"from":"Current","to":"Dissolved","count":1},
    {"from":"Current","to":"Trust Closed","count":1},
    {"from":"Current - Awaiting Reporting","to":"Dissolution Waiver Issued","count":1},
    {"from":"Current - In Process","to":"Trust Closed","count":1},
    {"from":"Delinquent","to":"Dissolution Waiver Issued","count":1},
    {"from":"Delinquent - Late Fees Due","to":"Current - Reporting Incomplete","count":1},
    {"from":"Delinquent - Late Fees Due","to":"Suspended","count":1},
    {"from":"Dissolution Pending","to":"Current - Reporting Incomplete","count":1},
    {"from":"Dissolution Pending","to":"Dissolved","count":1},
    {"from":"Dissolution Pending","to":"Withdrawn","count":1},
    {"from":"Dissolution Waiver Issued","to":"Current","count":1},
    {"from":"Enforcement Action Pending","to":"Current","count":1},
    {"from":"Exempt","to":"Delinquent","count":1},
    {"from":"Exempt - Dissolution Pending","to":"Exempt - Religious","count":1},
    {"from":"Never Registered - Dissolution Pending","to":"Never Registered - Diss. Waiver Issued","count":1},
    {"from":"Subject to Cease and Desist Order","to":"Enforcement Action Pending","count":1},
    {"from":"Suspended","to":"Current - Reporting Incomplete","count":1},
    {"from":"Suspended","to":"Dissolution Waiver Issued","count":1},
    {"from":"Withdrawn","to":"Current - In Process","count":1}
  ]
}
