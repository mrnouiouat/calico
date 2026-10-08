"""Neutral finite CP1252 candidates for isolated production replay."""

from dataclasses import dataclass
from pathlib import Path
import json

from calico_landing.contracts import LOGICAL_LIST_ORDER
from tests.fixtures.dbt_foundation.fixture_builder import load_gate_b_fixture_spec, _FIELD_ORDER


@dataclass(frozen=True)
class HostedReplayFixture:
    releases: tuple
    policy: bytes
    excluded_value: str = ""

    def materialize(self, root: Path, index: int, *, invalid=False, changed=False):
        root.mkdir()
        payloads = self.source_payloads(index, invalid=invalid, changed=changed)
        objects = {}
        for logical_list, raw in payloads.items():
            name = logical_list + ".csv"
            (root / name).write_bytes(raw)
            objects[logical_list] = {"relative_path": name, "content_length": len(raw)}
        (root / "candidate-set.json").write_text(json.dumps({"manifest_version": 1,
            "objects": objects}), encoding="utf-8")
        return root

    def source_payloads(self, index, *, invalid=False, changed=False):
        revision = self.releases[index]
        payloads = {}
        for logical_list in LOGICAL_LIST_ORDER:
            lines = [",".join(_FIELD_ORDER)]
            for original in revision.records[logical_list]:
                row = {key: value.strip() for key, value in original.items()}
                row["Registry Status"] = {"Active": "Current", "Reporting Incomplete":
                    "Current - Reporting Incomplete"}.get(row["Registry Status"], row["Registry Status"])
                row["FEIN"] = self.excluded_value
                row["SOS/FTB#"] = ""
                if changed:
                    row["Name"] += " Revised"
                lines.append(",".join(row[key] for key in _FIELD_ORDER))
            if invalid and logical_list == LOGICAL_LIST_ORDER[0]:
                lines[0] = "Wrong,Header"
            raw = ("\r\n".join(lines) + "\r\n").encode("cp1252")
            payloads[logical_list] = raw
        return payloads


def hosted_replay_fixture():
    spec = load_gate_b_fixture_spec()
    releases = tuple(sorted(spec.revisions, key=lambda r: (r.as_of_date, r.revision_label)))
    classifications = {}
    for revision in releases:
        for rows in revision.records.values():
            for row in rows:
                key = row["State Charity Reg#"].strip()
                if key:
                    classifications[key] = "eligible"
    keys = sorted(classifications)
    classifications[keys[-1]] = "unclassified"
    classifications[keys[-2]] = "ambiguous_natural_person"
    policy = json.dumps({"schema_version": 1, "classification_version": "hosted-fixture-v1",
        "classifications": [{"registration_number": key, "classification": classifications[key]}
                            for key in keys]}, sort_keys=True, separators=(",", ":")).encode()
    return HostedReplayFixture(releases, policy)
