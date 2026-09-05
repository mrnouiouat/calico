"""Validate complete visible/hidden semantic-model exposure against exports.

Phase 8 supplies metadata or a retained manual inspection record. Complete
transitive base lineage is an attestation, never inferred by parsing expressions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from calico_publish.allowlist import Allowlist

FIELD_VISIBILITIES = frozenset({"visible", "hidden"})
FIELD_ORIGINS = frozenset({"source_column", "calculated_column", "measure"})
RELATIONSHIP_CARDINALITIES = frozenset({"one_to_one", "one_to_many", "many_to_one"})
INVENTORY_SOURCES = frozenset({"machine_readable_metadata", "manual_inspection_record"})
INVENTORY_ERROR_CATEGORIES = frozenset({
    "inventory.not_found", "inventory.invalid_encoding", "inventory.invalid_json",
    "inventory.invalid_document_schema", "inventory.duplicate_table", "inventory.duplicate_field",
})
_FINDING_CATEGORIES = frozenset({
    "inventory.unapproved_table", "inventory.unapproved_column",
    "inventory.unapproved_hidden_column", "inventory.unapproved_relationship",
    "inventory.unapproved_calculated_source",
})
_TOP_KEYS = frozenset({"schema_version", "model_name", "inventory_source", "tables", "relationships"})
_FIELD_KEYS = frozenset({"field_name", "visibility", "origin", "lineage_complete", "source_columns"})
_RELATIONSHIP_KEYS = frozenset({"from_table", "from_column", "to_table", "to_column", "cardinality", "cross_filter_direction"})
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?: [A-Za-z][A-Za-z0-9_]*)*$")


class InventoryError(Exception):
    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class InventoryFinding:
    table_name: str
    field_name: str
    category: str

    def __post_init__(self) -> None:
        if (not _matches(self.table_name, _IDENTIFIER)
                or not (self.field_name == "-" or _matches(self.field_name, _LABEL))
                or not _member(self.category, _FINDING_CATEGORIES)):
            raise InventoryError("inventory.invalid_document_schema")

    def render(self) -> str:
        return f"{self.table_name}:{self.field_name}: {self.category}"


def _matches(value: object, pattern: re.Pattern) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _member(value: object, vocabulary: frozenset) -> bool:
    return isinstance(value, str) and value in vocabulary


def _closed(value: object, keys: set | frozenset) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise InventoryError("inventory.invalid_document_schema")


def _validate(document: object) -> None:
    _closed(document, _TOP_KEYS)
    if (type(document["schema_version"]) is not int or document["schema_version"] != 1
            or not _matches(document["model_name"], _LABEL)
            or not _member(document["inventory_source"], INVENTORY_SOURCES)
            or not isinstance(document["tables"], list) or not document["tables"]
            or not isinstance(document["relationships"], list)):
        raise InventoryError("inventory.invalid_document_schema")
    tables = set()
    for table in document["tables"]:
        _closed(table, {"table_name", "fields"})
        if (not _matches(table["table_name"], _IDENTIFIER)
                or not isinstance(table["fields"], list) or not table["fields"]):
            raise InventoryError("inventory.invalid_document_schema")
        if table["table_name"] in tables:
            raise InventoryError("inventory.duplicate_table")
        tables.add(table["table_name"])
        names = set()
        for field in table["fields"]:
            _closed(field, _FIELD_KEYS)
            if (not _matches(field["field_name"], _LABEL)
                    or not _member(field["visibility"], FIELD_VISIBILITIES)
                    or not _member(field["origin"], FIELD_ORIGINS)
                    or field["lineage_complete"] is not True
                    or not isinstance(field["source_columns"], list) or not field["source_columns"]):
                raise InventoryError("inventory.invalid_document_schema")
            if field["field_name"] in names:
                raise InventoryError("inventory.duplicate_field")
            names.add(field["field_name"])
            pairs = set()
            for source in field["source_columns"]:
                _closed(source, {"table_name", "column_name"})
                if not all(_matches(source[key], _IDENTIFIER) for key in source):
                    raise InventoryError("inventory.invalid_document_schema")
                pair = (source["table_name"], source["column_name"])
                if pair in pairs:
                    raise InventoryError("inventory.invalid_document_schema")
                pairs.add(pair)
            if (field["origin"] == "source_column"
                    and pairs != {(table["table_name"], field["field_name"])}):
                raise InventoryError("inventory.invalid_document_schema")
    for relation in document["relationships"]:
        _closed(relation, _RELATIONSHIP_KEYS)
        if (not all(_matches(relation[key], _IDENTIFIER)
                    for key in ("from_table", "from_column", "to_table", "to_column"))
                or not _member(relation["cardinality"], RELATIONSHIP_CARDINALITIES)
                or not _member(relation["cross_filter_direction"], frozenset({"single", "both"}))):
            raise InventoryError("inventory.invalid_document_schema")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    document = {}
    for key, value in pairs:
        if key in document:
            raise InventoryError("inventory.invalid_document_schema")
        document[key] = value
    return document


def load_inventory_document(path: str | Path) -> dict:
    """Read the retained record through a value-free fail-closed ladder."""
    try:
        payload = Path(path).read_bytes()
    except (OSError, TypeError, ValueError):
        raise InventoryError("inventory.not_found") from None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise InventoryError("inventory.invalid_encoding") from None
    try:
        document = json.loads(text, object_pairs_hook=_unique_json_object)
    except (ValueError, RecursionError):
        raise InventoryError("inventory.invalid_json") from None
    _validate(document)
    return document


def check_inventory(document: object, allowlist: Allowlist) -> tuple[InventoryFinding, ...]:
    """Check every field and relationship, irrespective of field visibility."""
    _validate(document)
    approved = {entry.export_name: set(entry.columns) for entry in allowlist.exports}

    def approved_pair(table: str, column: str) -> bool:
        return table in approved and column in approved[table]

    findings = []
    for table in document["tables"]:
        table_name = table["table_name"]
        if table_name not in approved:
            findings.append(InventoryFinding(table_name, "-", "inventory.unapproved_table"))
        for field in table["fields"]:
            if field["origin"] == "source_column":
                if not approved_pair(table_name, field["field_name"]):
                    category = ("inventory.unapproved_hidden_column" if field["visibility"] == "hidden"
                                else "inventory.unapproved_column")
                    findings.append(InventoryFinding(table_name, field["field_name"], category))
            else:
                for source in field["source_columns"]:
                    if not approved_pair(source["table_name"], source["column_name"]):
                        findings.append(InventoryFinding(table_name, field["field_name"],
                                                        "inventory.unapproved_calculated_source"))
    for relation in document["relationships"]:
        if (not approved_pair(relation["from_table"], relation["from_column"])
                or not approved_pair(relation["to_table"], relation["to_column"])):
            findings.append(InventoryFinding(relation["from_table"], relation["from_column"],
                                            "inventory.unapproved_relationship"))
    return tuple(sorted(findings, key=lambda f: (f.table_name, f.field_name, f.category)))


__all__ = ["InventoryError", "InventoryFinding", "load_inventory_document", "check_inventory",
           "INVENTORY_ERROR_CATEGORIES", "FIELD_VISIBILITIES", "FIELD_ORIGINS",
           "RELATIONSHIP_CARDINALITIES", "INVENTORY_SOURCES"]
