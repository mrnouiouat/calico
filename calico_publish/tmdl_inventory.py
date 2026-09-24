"""Generate a fail-closed semantic inventory from the committed TMDL subset.

Expressions are bounded by TMDL indentation or triple-backtick fences. Their
contents are never interpreted as declarations or used to infer DAX lineage.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


class TmdlInventoryError(Exception):
    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*\Z")
_NAME = re.compile(r"(?:[A-Za-z][A-Za-z0-9_]*|'(?:[^']|'')+')\Z")
_DECLARATION = re.compile(r"(column|measure|partition) (.+?)(?: =(?: (.*))?)?\Z")
_ANNOTATION = re.compile(r"annotation CalicoBaseLineage = (.+)\Z")
_UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z")
_COLUMN_PROPERTIES = frozenset({"dataType", "formatString", "summarizeBy", "sourceColumn", "isHidden", "lineageTag"})
_MEASURE_PROPERTIES = frozenset({"formatString", "isHidden", "lineageTag"})
_PARTITION_PROPERTIES = frozenset({"mode"})


def _read(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise TmdlInventoryError("tmdl.unreadable_metadata") from None


def _depth(line: str) -> tuple[int, str]:
    depth = len(line) - len(line.lstrip("\t"))
    body = line[depth:]
    if body.startswith((" ", "\t")):
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    return depth, body


def _name(raw: str) -> str:
    if not _NAME.fullmatch(raw):
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    return raw[1:-1].replace("''", "'") if raw.startswith("'") else raw


def _lineage(value: str) -> list[dict[str, str]]:
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise TmdlInventoryError("tmdl.invalid_lineage")
            result[key] = item
        return result
    try:
        pairs = json.loads(value, object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        raise TmdlInventoryError("tmdl.invalid_lineage") from None
    if not isinstance(pairs, list) or not pairs:
        raise TmdlInventoryError("tmdl.invalid_lineage")
    seen = set()
    for pair in pairs:
        if (not isinstance(pair, dict) or set(pair) != {"table_name", "column_name"}
                or not all(isinstance(v, str) and _IDENTIFIER.fullmatch(v) for v in pair.values())):
            raise TmdlInventoryError("tmdl.invalid_lineage")
        key = (pair["table_name"], pair["column_name"])
        if key in seen:
            raise TmdlInventoryError("tmdl.invalid_lineage")
        seen.add(key)
    return pairs


def _expression(lines: list[str], start: int, parent_depth: int, initial: str,
                *, stop_properties: bool = False) -> int:
    """Consume one expression, stopping only at its documented boundary."""
    if initial == "```":
        for index in range(start + 1, len(lines)):
            if lines[index].strip() == "```":
                return index + 1
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    if initial.strip():
        return start + 1
    index = start + 1
    seen = False
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        depth = len(lines[index]) - len(lines[index].lstrip("\t"))
        if depth <= parent_depth:
            break
        if stop_properties and seen:
            body = lines[index][depth:]
            if body.startswith(("annotation ", "formatString:", "isHidden", "column ",
                                "lineageTag:", "measure ", "partition ")):
                break
        seen = True
        index += 1
    if not seen:
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    return index


def _single_root(path: Path, root: str, properties: frozenset[str]) -> None:
    lines = _read(path)
    if not lines or lines[0] != root:
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    seen = set()
    for line in lines[1:]:
        if not line.strip():
            continue
        depth, body = _depth(line)
        if depth != 1:
            raise TmdlInventoryError("tmdl.unsupported_syntax")
        key = body.split(":", 1)[0].split(" = ", 1)[0]
        if key not in properties or key in seen:
            raise TmdlInventoryError("tmdl.unsupported_syntax")
        if key == "annotation __PBI_TimeIntelligenceEnabled" and body != "annotation __PBI_TimeIntelligenceEnabled = 0":
            raise TmdlInventoryError("tmdl.unsupported_syntax")
        seen.add(key)


def _model(path: Path) -> set[str]:
    """Validate Desktop's native model root and return its explicit table refs."""
    lines = _read(path)
    if not lines or lines[0] != "model Model":
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    refs: set[str] = set()
    simple = {
        "culture: en-US", "defaultPowerBIDataSourceVersion: powerBI_V3",
        "discourageImplicitMeasures", "sourceQueryCulture: en-US",
    }
    seen: set[str] = set()
    index = 1
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        depth, body = _depth(lines[index])
        if depth == 1 and body in simple:
            if body in seen:
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            seen.add(body)
            index += 1
            continue
        if depth == 1 and body == "dataAccessOptions":
            if body in seen or index + 2 >= len(lines):
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            seen.add(body)
            children = []
            index += 1
            while index < len(lines) and lines[index].strip():
                child_depth, child = _depth(lines[index])
                if child_depth != 2:
                    break
                children.append(child)
                index += 1
            if children != ["legacyRedirects", "returnErrorValuesAsNull"]:
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            continue
        if depth == 0 and body == "annotation __PBI_TimeIntelligenceEnabled = 0":
            if body in seen:
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            seen.add(body)
            index += 1
            continue
        if depth == 0 and body.startswith("annotation PBI_QueryOrder = "):
            try:
                order = json.loads(body.split(" = ", 1)[1])
            except ValueError:
                raise TmdlInventoryError("tmdl.unsupported_syntax") from None
            if not isinstance(order, list) or len(order) != len(set(order)) or not all(isinstance(item, str) for item in order):
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            seen.add("PBI_QueryOrder")
            index += 1
            continue
        if depth == 0 and body == 'annotation PBI_ProTooling = ["DevMode"]':
            seen.add("PBI_ProTooling")
            index += 1
            continue
        if depth == 0 and body.startswith("ref table "):
            name = body[10:]
            if not _IDENTIFIER.fullmatch(name) or name in refs:
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            refs.add(name)
            index += 1
            continue
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    if not simple <= seen or "dataAccessOptions" not in seen or "annotation __PBI_TimeIntelligenceEnabled = 0" not in seen:
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    return refs


def _relationships(path: Path) -> list[dict[str, str]]:
    lines = _read(path)
    result = []
    names = set()
    index = 0
    allowed = {"fromColumn", "toColumn", "fromCardinality", "toCardinality",
               "crossFilteringBehavior", "isActive"}
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        depth, body = _depth(lines[index])
        if depth != 0 or not body.startswith("relationship ") or len(body) <= 13:
            raise TmdlInventoryError("tmdl.unsupported_syntax")
        name = body[13:]
        if name in names:
            raise TmdlInventoryError("tmdl.duplicate_object")
        names.add(name)
        properties = {}
        index += 1
        while index < len(lines):
            if not lines[index].strip():
                index += 1
                continue
            depth, body = _depth(lines[index])
            if depth == 0:
                break
            if depth != 1 or ": " not in body:
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            key, value = body.split(": ", 1)
            if key not in allowed or key in properties or not value:
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            properties[key] = value
            index += 1
        if not {"fromColumn", "toColumn"} <= properties.keys() or properties.get("isActive", "true") != "true":
            raise TmdlInventoryError("tmdl.unsupported_syntax")
        endpoints = []
        for key in ("fromColumn", "toColumn"):
            parts = properties[key].split(".")
            if len(parts) != 2 or not all(_IDENTIFIER.fullmatch(part) for part in parts):
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            endpoints.append(parts)
        cardinality = (properties.get("fromCardinality", "many"), properties.get("toCardinality", "one"))
        if cardinality not in {("many", "one"), ("one", "many"), ("one", "one")}:
            raise TmdlInventoryError("tmdl.unsupported_syntax")
        direction = properties.get("crossFilteringBehavior", "oneDirection")
        if direction not in {"oneDirection", "bothDirections"}:
            raise TmdlInventoryError("tmdl.unsupported_syntax")
        result.append({"from_table": endpoints[0][0], "from_column": endpoints[0][1],
                       "to_table": endpoints[1][0], "to_column": endpoints[1][1],
                       "cardinality": "_to_".join(cardinality),
                       "cross_filter_direction": "single" if direction == "oneDirection" else "both"})
    return result


def _expressions(path: Path) -> None:
    lines = _read(path)
    if not lines:
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    seen = set()
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        depth, body = _depth(lines[index])
        match = re.fullmatch(r"expression (\w+) = (.+)", body)
        if depth != 0 or not match or match.group(1) in seen:
            raise TmdlInventoryError("tmdl.unsupported_syntax")
        seen.add(match.group(1))
        index = _expression(lines, index, 0, match.group(2))
        expression_metadata = set()
        while index < len(lines):
            if not lines[index].strip():
                index += 1
                continue
            metadata_depth, metadata = _depth(lines[index])
            if metadata_depth == 0:
                break
            if metadata_depth != 1 or metadata in expression_metadata:
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            if metadata.startswith("lineageTag: "):
                if not _UUID.fullmatch(metadata.split(": ", 1)[1]):
                    raise TmdlInventoryError("tmdl.unsupported_syntax")
            elif metadata != "annotation PBI_ResultType = Text":
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            expression_metadata.add(metadata)
            index += 1
    if seen != {"PublishedBaseUrl"}:
        raise TmdlInventoryError("tmdl.unsupported_syntax")


def _table(path: Path) -> dict:
    lines = _read(path)
    if not lines or not lines[0].startswith("table "):
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    table_name = lines[0][6:]
    if not _IDENTIFIER.fullmatch(table_name) or table_name != path.stem:
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    fields = []
    seen = set()
    partitions = 0
    index = 1
    table_lineage_seen = False
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        depth, body = _depth(lines[index])
        if depth == 1 and body.startswith("lineageTag: "):
            if table_lineage_seen or not _UUID.fullmatch(body.split(": ", 1)[1]):
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            table_lineage_seen = True
            index += 1
            continue
        if depth == 1 and body == "annotation PBI_ResultType = Table":
            index += 1
            continue
        match = _DECLARATION.fullmatch(body)
        if depth != 1 or not match:
            raise TmdlInventoryError("tmdl.unsupported_syntax")
        kind, raw_name, initial = match.groups()
        if initial is None and body.endswith(" ="):
            initial = ""
        name = _name(raw_name)
        if (kind, name) in seen or (kind in {"column", "measure"} and name in {f["field_name"] for f in fields}):
            raise TmdlInventoryError("tmdl.duplicate_object")
        seen.add((kind, name))
        if kind == "partition":
            partitions += 1
            if name != table_name or initial != "m":
                raise TmdlInventoryError("tmdl.unsupported_syntax")
        elif kind == "measure":
            if initial is None:
                raise TmdlInventoryError("tmdl.unsupported_syntax")
        elif initial is not None and not initial.strip():
            raise TmdlInventoryError("tmdl.unsupported_syntax")
        field = {"field_name": name, "visibility": "visible",
                 "origin": "measure" if kind == "measure" else "calculated_column" if initial is not None else "source_column",
                 "lineage_complete": True, "source_columns": []}
        properties = {}
        if kind == "measure":
            index = _expression(lines, index, 1, initial, stop_properties=True)
        else:
            index += 1
        while index < len(lines):
            if not lines[index].strip():
                index += 1
                continue
            depth, body = _depth(lines[index])
            if depth <= 1:
                break
            if depth not in ({2, 3} if kind == "measure" else {2}):
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            annotation = _ANNOTATION.fullmatch(body)
            if annotation:
                if "CalicoBaseLineage" in properties:
                    raise TmdlInventoryError("tmdl.duplicate_object")
                properties["CalicoBaseLineage"] = _lineage(annotation.group(1))
                index += 1
                continue
            if body == "annotation UnderlyingDateTimeDataType = Date":
                if kind != "column" or "UnderlyingDateTimeDataType" in properties:
                    raise TmdlInventoryError("tmdl.unsupported_syntax")
                properties["UnderlyingDateTimeDataType"] = "Date"
                index += 1
                continue
            if kind == "partition" and body == "source =":
                if "source" in properties:
                    raise TmdlInventoryError("tmdl.duplicate_object")
                properties["source"] = True
                index = _expression(lines, index, 2, "")
                continue
            if ": " in body:
                key, value = body.split(": ", 1)
            else:
                key, value = body, None
            allowed = (_PARTITION_PROPERTIES if kind == "partition" else
                       _MEASURE_PROPERTIES if kind == "measure" else _COLUMN_PROPERTIES)
            if key not in allowed or key in properties or (value is None and key != "isHidden"):
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            if key == "lineageTag" and (value is None or not _UUID.fullmatch(value)):
                raise TmdlInventoryError("tmdl.unsupported_syntax")
            if key == "isHidden":
                if value not in (None, "true", "false"):
                    raise TmdlInventoryError("tmdl.unsupported_syntax")
                field["visibility"] = "hidden" if value != "false" else "visible"
            properties[key] = value
            index += 1
        if kind == "partition":
            if properties != {"mode": "import", "source": True}:
                raise TmdlInventoryError("tmdl.unsupported_syntax")
        else:
            if "CalicoBaseLineage" not in properties:
                raise TmdlInventoryError("tmdl.missing_lineage")
            field["source_columns"] = properties["CalicoBaseLineage"]
            if kind == "column" and initial is None:
                if (properties.get("sourceColumn") != name or
                        field["source_columns"] != [{"table_name": table_name, "column_name": name}]):
                    raise TmdlInventoryError("tmdl.invalid_lineage")
            fields.append(field)
    if partitions != 1 or not fields:
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    return {"table_name": table_name, "fields": fields}


def generate_inventory(model_dir: Path) -> dict:
    """Enumerate every supported TMDL object; reject unknown files and grammar."""
    root = Path(model_dir) / "definition"
    expected = {root / "database.tmdl", root / "model.tmdl", root / "expressions.tmdl"}
    try:
        actual = set(root.rglob("*.tmdl"))
        other = {path for path in root.rglob("*") if path.is_file() and path.suffix != ".tmdl"}
    except OSError:
        raise TmdlInventoryError("tmdl.unreadable_metadata") from None
    table_paths = sorted((root / "tables").glob("*.tmdl"))
    relationships_path = root / "relationships.tmdl"
    optional = {relationships_path} if relationships_path in actual else set()
    if not expected <= actual or actual != expected | optional | set(table_paths) or other or not table_paths:
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    _single_root(root / "database.tmdl", "database", frozenset({"compatibilityLevel", "compatibilityMode"}))
    model_refs = _model(root / "model.tmdl")
    _expressions(root / "expressions.tmdl")
    tables = [_table(path) for path in table_paths]
    if not model_refs or not model_refs <= {table["table_name"] for table in tables}:
        raise TmdlInventoryError("tmdl.unsupported_syntax")
    relationships = _relationships(relationships_path) if optional else []
    return {"schema_version": 1, "model_name": "registry_monitor",
            "inventory_source": "machine_readable_metadata", "tables": tables, "relationships": relationships}


__all__ = ["TmdlInventoryError", "generate_inventory"]
