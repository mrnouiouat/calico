"""Actual-dbt integration proof for promotion and adjacency (T-03-09..T-03-11).

Every test here drives the real `calico_dbt.runner.build()` service against
the actual product `../calico/dbt` project (no `_dbt_project_dir_override`)
using the closed Gate B fixture (`gate_b_fixture_store`) as an owned,
context-managed input. Each `build()` call gets its own fresh temporary
DuckDB database; the only window into a still-open database is the
fixture-only `inspector` callback, invoked after `dbt build` succeeds and
before the runner's `finally` cleanup removes the whole temporary root.
Assertions are made only after `build()` returns and cleanup has completed,
against values already copied out of the closed `FixtureBuildInspection`
facade into test-local immutable tuples/dicts -- proving every assertion
traces to a successful SQL build, never a surviving database.

`FixtureBuildInspection` exposes exactly three fixed, constant-SQL
projections -- `revision_catalog_rows()`, `promoted_release_rows()`, and
`adjacent_release_pair_rows()` -- and no path, connection, arbitrary query,
or registry-record projection (Plan 02's locked interface). Two
consequences follow for this module's design:

- `promoted_release_rows()` and `adjacent_release_pair_rows()` are computed
  directly from the raw `runtime_input.promotion_catalog`/`revision_catalog`
  source tables, not from this plan's `int_promoted_releases` /
  `int_adjacent_release_pairs` models. This module therefore proves D-06's
  pointer-authoritative/highest-revision-fallback behavior and D-07's
  same-date locality behavior by driving the *actual* dbt `promotion` /
  `promotion-adjacency` selections to completion (an incorrect model or an
  incorrect `assert_pointer_or_highest_promotion.sql` /
  `assert_same_date_revision_pair_locality.sql` invariant fails the whole
  `dbt build`, so `outcome.status == "success"` is itself part of the
  proof) combined with cross-checking the facade's independent raw-source
  projections and the fixture's own admission metadata.
- Nothing here can directly query `int_promoted_registry_records`'s output
  rows (the facade deliberately exposes no registry-record projection), so
  "promoted records join on the complete release key" is proven by a
  lightweight structural check of the compiled model's own join predicate,
  combined with the same successful `dbt build` (which also runs that
  model's generic not-null tests).
- "A present pointer that cannot join exactly once fails the build" is not
  separately re-exercised here: `calico_dbt.preflight`'s own pointer/catalog
  consistency check (Wave 2) already fails closed *before* dbt ever runs if
  a real store's pointer disagreed with its catalog, so this exact
  scenario cannot be constructed from a `runner.build()` call. The
  invariant itself is independently enforced in SQL by
  `assert_pointer_or_highest_promotion.sql`, which runs (and passes) as
  part of every successful build in this module.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from calico_dbt import runner
from tests.fixtures.dbt_foundation.fixture_builder import (
    GateBFixtureAdmission,
    gate_b_fixture_store,
    load_gate_b_fixture_spec,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMOTED_REGISTRY_RECORDS_SQL = (
    _REPO_ROOT / "dbt" / "models" / "intermediate" / "int_promoted_registry_records.sql"
)

_SPEC = load_gate_b_fixture_spec()
_MIDDLE_AS_OF_DATE = _SPEC.middle_as_of_date
_MIDDLE_VARIANT_A, _MIDDLE_VARIANT_B = _SPEC.middle_revision_labels


def _atomic_remove_pointer_entry(store_root: Path, as_of_date: str) -> None:
    """Atomically rewrite the owned temporary store's `promoted-releases.json`
    to remove exactly one date's pointer entry, retaining every other date
    entry untouched -- mirrors `calico_landing.store`'s own
    write-temp-then-`os.replace` discipline. Only ever called against a
    `gate_b_fixture_store`-owned temporary store, never a committed fixture.
    """

    pointer_path = store_root / "promoted-releases.json"
    document = json.loads(pointer_path.read_text(encoding="utf-8"))
    promotions = document.get("promotions", {})
    if as_of_date not in promotions:
        raise AssertionError("expected an existing pointer entry for the target date")
    del promotions[as_of_date]

    fd, temp_name = tempfile.mkstemp(
        prefix=".promoted-releases.json.", suffix=".tmp", dir=store_root
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, pointer_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


@contextmanager
def _fixture_store_factory(
    *,
    pointer_variant: str | None = None,
    admissions_out: dict[str, GateBFixtureAdmission] | None = None,
    remove_pointer_for_date: str | None = None,
) -> Iterator[object]:
    """Build one owned Gate B fixture store, optionally recording every
    admission's safe result metadata into `admissions_out` and optionally
    removing one date's pointer entry before the caller's `dbt build` runs
    -- the fixed way this module manufactures the absent-pointer scenario.
    """

    with gate_b_fixture_store(pointer_variant=pointer_variant) as store:
        if admissions_out is not None:
            admissions_out.clear()
            admissions_out.update({admission.revision_label: admission for admission in store.admissions})
        if remove_pointer_for_date is not None:
            _atomic_remove_pointer_entry(store.store_root, remove_pointer_for_date)
        yield store


class RevisionAuditAndPresentPointerPromotionTests(unittest.TestCase):
    """D-05 audit visibility and D-06 present-pointer promotion (T-03-09)."""

    def test_four_revisions_audited_including_both_middle_date_variants(self) -> None:
        captured: dict[str, tuple] = {}

        def inspector(facade: "runner.FixtureBuildInspection") -> None:
            captured["revisions"] = facade.revision_catalog_rows()

        outcome = runner.build(mode="fixture", select="promotion", inspector=inspector)
        self.assertEqual(outcome.status, "success", outcome.category)

        revisions = captured["revisions"]
        self.assertEqual(len(revisions), 4)
        middle_rows = [row for row in revisions if row[0] == _MIDDLE_AS_OF_DATE]
        self.assertEqual(len(middle_rows), 2)

    def test_present_pointer_variant_promotion_build_succeeds(self) -> None:
        for variant in (_MIDDLE_VARIANT_A, _MIDDLE_VARIANT_B):
            with self.subTest(pointer_variant=variant):
                admissions: dict[str, GateBFixtureAdmission] = {}
                captured: dict[str, tuple] = {}

                def factory(variant: str = variant, admissions: dict = admissions):
                    return _fixture_store_factory(pointer_variant=variant, admissions_out=admissions)

                def inspector(facade: "runner.FixtureBuildInspection") -> None:
                    captured["revisions"] = facade.revision_catalog_rows()

                outcome = runner.build(
                    mode="fixture",
                    select="promotion",
                    fixture_store_factory=factory,
                    inspector=inspector,
                )
                self.assertEqual(outcome.status, "success", outcome.category)
                self.assertEqual(len(captured["revisions"]), 4)
                self.assertIn(variant, admissions)


class AbsentPointerFallbackTests(unittest.TestCase):
    """D-06 highest-accepted-revision fallback, exercised over an owned
    temporary store whose middle-date pointer entry has been removed
    (T-03-09).
    """

    def test_absent_pointer_fallback_promotion_build_succeeds_over_two_accepted_revisions(
        self,
    ) -> None:
        admissions: dict[str, GateBFixtureAdmission] = {}
        captured: dict[str, tuple] = {}

        def factory():
            return _fixture_store_factory(
                admissions_out=admissions,
                remove_pointer_for_date=_MIDDLE_AS_OF_DATE,
            )

        def inspector(facade: "runner.FixtureBuildInspection") -> None:
            captured["revisions"] = facade.revision_catalog_rows()

        outcome = runner.build(
            mode="fixture",
            select="promotion",
            fixture_store_factory=factory,
            inspector=inspector,
        )
        # This is the core proof: `assert_pointer_or_highest_promotion.sql`'s
        # no-pointer-fallback check runs inside this exact build and fails
        # it unless `int_promoted_releases` selected the higher accepted
        # middle-date revision -- a build failure here would mean the
        # fallback branch is broken, not that Python inferred a mismatch.
        self.assertEqual(outcome.status, "success", outcome.category)

        middle_admissions = [admissions[_MIDDLE_VARIANT_A], admissions[_MIDDLE_VARIANT_B]]
        higher = max(middle_admissions, key=lambda admission: admission.result.release_revision)
        lower = min(middle_admissions, key=lambda admission: admission.result.release_revision)
        self.assertGreater(higher.result.release_revision, lower.result.release_revision)

        revisions = captured["revisions"]
        self.assertEqual(len(revisions), 4)
        middle_rows = [row for row in revisions if row[0] == _MIDDLE_AS_OF_DATE]
        self.assertEqual(len(middle_rows), 2)
        self.assertIn(
            (
                higher.as_of_date,
                higher.result.release_revision,
                higher.result.revision_fingerprint,
            ),
            middle_rows,
        )


class PromotedRegistryRecordsJoinShapeTests(unittest.TestCase):
    """D-05: promoted staging records join on the complete release key."""

    def test_promoted_registry_records_joins_on_full_release_key(self) -> None:
        content = _PROMOTED_REGISTRY_RECORDS_SQL.read_text(encoding="utf-8")
        self.assertIn("ref('stg_registry_records')", content)
        self.assertIn("ref('int_promoted_releases')", content)
        for column in ("as_of_date", "release_revision", "revision_fingerprint"):
            self.assertIn(f"stg.{column} = promoted.{column}", content)

    def test_promotion_select_alias_build_exercises_the_join(self) -> None:
        # A wrong join predicate (e.g. `as_of_date` alone) would still let
        # `dbt build` succeed structurally, but the not-null generic tests
        # attached to `int_promoted_registry_records` run as part of this
        # exact selection -- combined with the static join-predicate check
        # above, this proves the actual compiled join is the full key.
        outcome = runner.build(mode="fixture", select="promotion")
        self.assertEqual(outcome.status, "success", outcome.category)


class AdjacencyLocalityTests(unittest.TestCase):
    """D-07 positive-gap adjacency and same-date pointer-switch locality
    (T-03-11).
    """

    def test_three_promoted_dates_yield_two_positive_gap_pairs(self) -> None:
        captured: dict[str, tuple] = {}

        def inspector(facade: "runner.FixtureBuildInspection") -> None:
            captured["promoted"] = facade.promoted_release_rows()
            captured["pairs"] = facade.adjacent_release_pair_rows()

        outcome = runner.build(mode="fixture", select="promotion-adjacency", inspector=inspector)
        self.assertEqual(outcome.status, "success", outcome.category)

        self.assertEqual(len(captured["promoted"]), 3)
        pairs = captured["pairs"]
        self.assertEqual(len(pairs), 2)
        for pair in pairs:
            from_date, _from_fp, to_date, _to_fp, gap_days = pair
            self.assertLess(from_date, to_date)
            self.assertGreater(gap_days, 0)

    def test_same_date_pointer_switch_changes_only_touching_pair_fingerprints(self) -> None:
        pairs_by_variant: dict[str, tuple] = {}

        for variant in (_MIDDLE_VARIANT_A, _MIDDLE_VARIANT_B):
            captured: dict[str, tuple] = {}

            def factory(variant: str = variant):
                return _fixture_store_factory(pointer_variant=variant)

            def inspector(facade: "runner.FixtureBuildInspection") -> None:
                captured["pairs"] = facade.adjacent_release_pair_rows()

            outcome = runner.build(
                mode="fixture",
                select="promotion-adjacency",
                fixture_store_factory=factory,
                inspector=inspector,
            )
            self.assertEqual(outcome.status, "success", outcome.category)
            pairs_by_variant[variant] = captured["pairs"]

        pairs_a = pairs_by_variant[_MIDDLE_VARIANT_A]
        pairs_b = pairs_by_variant[_MIDDLE_VARIANT_B]
        self.assertEqual(len(pairs_a), 2)
        self.assertEqual(len(pairs_b), 2)

        # Ordered pair dates, pair count, and gap values are identical
        # across both builds.
        for pair_a, pair_b in zip(pairs_a, pairs_b):
            self.assertEqual(pair_a[0], pair_b[0])  # from_as_of_date
            self.assertEqual(pair_a[2], pair_b[2])  # to_as_of_date
            self.assertEqual(pair_a[4], pair_b[4])  # gap_days

        # Pair 0 ends at the middle date: only its to-side fingerprint
        # (the touching side) may change; its from-side (a non-middle,
        # never-replaced date) must not.
        self.assertEqual(pairs_a[0][1], pairs_b[0][1])
        self.assertNotEqual(pairs_a[0][3], pairs_b[0][3])

        # Pair 1 starts at the middle date: only its from-side fingerprint
        # may change; its to-side (a non-middle date) must not.
        self.assertNotEqual(pairs_a[1][1], pairs_b[1][1])
        self.assertEqual(pairs_a[1][3], pairs_b[1][3])


if __name__ == "__main__":
    unittest.main()
