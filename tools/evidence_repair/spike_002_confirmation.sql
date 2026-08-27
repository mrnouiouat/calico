-- Bundled, fixed, evidence-only DuckDB SQL (02-RESEARCH.md Pattern 5;
-- D-11/D-12/D-13). This file is never edited at runtime, receives no
-- caller-supplied SQL text, and never embeds a file path -- every view
-- name below is a literal fixed identifier that
-- `tools.evidence_repair.__main__` registers directly from admitted
-- Parquet through the DuckDB relational API (`connection.read_parquet(...)
-- .create_view(...)`), exactly as `calico_landing.parquet` already does
-- for canonical serialization. Python loads this file exactly once, splits
-- it into named blocks on the `-- @query: <name>` marker below, and
-- executes each block verbatim with zero string interpolation.
--
-- "release0", "release1", and "release2" are the three admitted releases
-- the caller selected, always exactly three, always in ascending
-- chronological `as_of_date` order (enforced by
-- `tools.evidence_repair.__main__` before any query here runs). This
-- module never joins, coalesces, or drops a keyless row (D-005); every
-- population view below is a plain four-list union, never a keyed join.
--
-- No block here ever returns a raw field value other than a nonblank
-- `State Charity Reg#` value from the three `membership_release*` blocks --
-- those ordered key sequences are streamed directly into a running
-- SHA-256 digest by the caller and are never otherwise persisted, printed,
-- or included in any output document (D-11).

-- @query: create_population_release0
CREATE VIEW release0_population AS
SELECT TRIM("State Charity Reg#") AS reg_key, TRIM("Registry Status") AS status
FROM r0_charities_may_operate
UNION ALL
SELECT TRIM("State Charity Reg#"), TRIM("Registry Status") FROM r0_charities_not_operating
UNION ALL
SELECT TRIM("State Charity Reg#"), TRIM("Registry Status") FROM r0_charities_undetermined_status
UNION ALL
SELECT TRIM("State Charity Reg#"), TRIM("Registry Status") FROM r0_charities_may_not_operate;

-- @query: create_population_release1
CREATE VIEW release1_population AS
SELECT TRIM("State Charity Reg#") AS reg_key, TRIM("Registry Status") AS status
FROM r1_charities_may_operate
UNION ALL
SELECT TRIM("State Charity Reg#"), TRIM("Registry Status") FROM r1_charities_not_operating
UNION ALL
SELECT TRIM("State Charity Reg#"), TRIM("Registry Status") FROM r1_charities_undetermined_status
UNION ALL
SELECT TRIM("State Charity Reg#"), TRIM("Registry Status") FROM r1_charities_may_not_operate;

-- @query: create_population_release2
CREATE VIEW release2_population AS
SELECT TRIM("State Charity Reg#") AS reg_key, TRIM("Registry Status") AS status
FROM r2_charities_may_operate
UNION ALL
SELECT TRIM("State Charity Reg#"), TRIM("Registry Status") FROM r2_charities_not_operating
UNION ALL
SELECT TRIM("State Charity Reg#"), TRIM("Registry Status") FROM r2_charities_undetermined_status
UNION ALL
SELECT TRIM("State Charity Reg#"), TRIM("Registry Status") FROM r2_charities_may_not_operate;

-- @query: totals_release0
-- Keyed/keyless coverage plus the exact D-006 two-value delinquent
-- population count, all owned here rather than in `calico_landing`.
SELECT
    COUNT(*) AS total_count,
    COUNT(*) FILTER (WHERE reg_key <> '') AS keyed_count,
    COUNT(*) FILTER (WHERE status IN ('Delinquent', 'Delinquent - Late Fees Due')) AS delinquent_count
FROM release0_population;

-- @query: totals_release1
SELECT
    COUNT(*) AS total_count,
    COUNT(*) FILTER (WHERE reg_key <> '') AS keyed_count,
    COUNT(*) FILTER (WHERE status IN ('Delinquent', 'Delinquent - Late Fees Due')) AS delinquent_count
FROM release1_population;

-- @query: totals_release2
SELECT
    COUNT(*) AS total_count,
    COUNT(*) FILTER (WHERE reg_key <> '') AS keyed_count,
    COUNT(*) FILTER (WHERE status IN ('Delinquent', 'Delinquent - Late Fees Due')) AS delinquent_count
FROM release2_population;

-- @query: membership_release0
-- The fixed ordinal ordering (plain ascending byte/codepoint order over
-- the classified key alphabet, which can never contain LF) that the
-- predecessor spike 002 membership-hash algorithm also used -- comparable
-- across runs by construction (02-RESEARCH.md Pattern 5).
SELECT DISTINCT reg_key FROM release0_population WHERE reg_key <> '' ORDER BY reg_key;

-- @query: membership_release1
SELECT DISTINCT reg_key FROM release1_population WHERE reg_key <> '' ORDER BY reg_key;

-- @query: membership_release2
SELECT DISTINCT reg_key FROM release2_population WHERE reg_key <> '' ORDER BY reg_key;

-- @query: exit_count_release0_to_release1
-- One narrowly scoped transition confirmation: how many release0 keys are
-- absent from release1. This is a structural coverage confirmation only --
-- never a status/cohort/spell computation, which stay out of this tool
-- and belong to Phase 3 dbt.
SELECT COUNT(*) AS exit_count
FROM (SELECT DISTINCT reg_key FROM release0_population WHERE reg_key <> '') AS predecessor_keys
WHERE NOT EXISTS (
    SELECT 1
    FROM release1_population AS successor
    WHERE successor.reg_key = predecessor_keys.reg_key AND successor.reg_key <> ''
);

-- @query: exit_count_release1_to_release2
-- Same narrowly scoped transition confirmation, one release later: how
-- many release1 keys are absent from release2.
SELECT COUNT(*) AS exit_count
FROM (SELECT DISTINCT reg_key FROM release1_population WHERE reg_key <> '') AS predecessor_keys
WHERE NOT EXISTS (
    SELECT 1
    FROM release2_population AS successor
    WHERE successor.reg_key = predecessor_keys.reg_key AND successor.reg_key <> ''
);
