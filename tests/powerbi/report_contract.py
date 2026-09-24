"""Fail-closed structural reader for the committed PBIR report."""

from __future__ import annotations

import json
from pathlib import Path


PAGES = ["published_registry_change", "cohort_persistence", "release_quality", "organization_lookup"]
PAGE_NAMES = ["Published registry change", "Cohort persistence", "Release quality", "Organization lookup"]
RELEVANT_KEYS = {"NativeVisualCalculation", "Calculation", "drillthrough", "tooltip", "bookmark", "interaction"}


class ReportContractError(ValueError):
    pass


def _walk(value, path="root"):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"NativeVisualCalculation", "Calculation"}:
                raise ReportContractError(f"report.prohibited_calculation:{path}")
            if key in {"drillthrough", "tooltip", "bookmark"}:
                raise ReportContractError(f"report.prohibited_navigation:{path}")
            if key == "interaction" and child not in (None, "None"):
                raise ReportContractError(f"report.prohibited_interaction:{path}")
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")
    else:
        yield path, value


def validate_report(report: Path, allowlist: Path, claims: Path) -> None:
    authority = json.loads(allowlist.read_text(encoding="utf-8"))
    allowed = {item["export_name"]: set(item["columns"]) for item in authority["exports"]}
    allowed["capture_status"] = set(authority["control_sources"][0]["columns"])
    claim_contract = json.loads(claims.read_text(encoding="utf-8"))
    pages = json.loads((report / "pages/pages.json").read_text(encoding="utf-8"))
    if pages.get("pageOrder") != PAGES:
        raise ReportContractError("report.page_order")
    for page_id, display_name in zip(PAGES, PAGE_NAMES, strict=True):
        page = json.loads((report / f"pages/{page_id}/page.json").read_text(encoding="utf-8"))
        if page.get("displayName") != display_name or page.get("visibility", "AlwaysVisible") != "AlwaysVisible":
            raise ReportContractError(f"report.page_contract:{page_id}")
        visuals = report / "pages" / page_id / "visuals"
        if not (visuals / "release_status_banner/visual.json").is_file():
            raise ReportContractError(f"report.banner_missing:{page_id}")
        for visual_path in visuals.glob("*/visual.json"):
            payload = json.loads(visual_path.read_text(encoding="utf-8"))
            list(_walk(payload, f"{page_id}.{visual_path.parent.name}"))
            def check_columns(value):
                if isinstance(value, dict):
                    field = value.get("field", {})
                    column = field.get("Column") if isinstance(field, dict) else None
                    if isinstance(column, dict):
                        table = column.get("Expression", {}).get("SourceRef", {}).get("Entity")
                        name = column.get("Property")
                        if table not in allowed or name not in allowed[table]:
                            raise ReportContractError(f"report.unapproved_field:{page_id}:{visual_path.parent.name}")
                    for child in value.values(): check_columns(child)
                elif isinstance(value, list):
                    for child in value: check_columns(child)
            check_columns(payload)
            text = json.dumps(payload).lower()
            for claim in claim_contract["prohibited_claims"]:
                for fragment in claim["fragments"]:
                    if fragment in text and not any(prefix in text for prefix in ("does not establish", "do not establish", "never")):
                        raise ReportContractError(f"report.prohibited_text:{claim['id']}:{page_id}:{visual_path.parent.name}")
