"""Closed, deterministic provenance manifest for one published export set."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Sequence

from calico_landing.contracts import LOGICAL_LIST_ORDER

if TYPE_CHECKING:
    from calico_publish.allowlist import Allowlist
    from calico_publish.export import StagedExport

MANIFEST_DOCUMENT_KEYS = frozenset(
    {
        "schema_version",
        "allowlist_version",
        "parser_contract_version",
        "toolchain",
        "accepted_releases",
        "eligible_key_count",
        "exports",
    }
)
_TOOLCHAIN_KEYS = frozenset({"python", "dbt_core", "dbt_duckdb", "duckdb"})
_ACCEPTED_RELEASE_KEYS = frozenset(
    {"as_of_date", "release_revision", "revision_fingerprint", "source_objects"}
)
_SOURCE_OBJECT_KEYS = frozenset({"source_list", "sha256", "byte_size", "row_count"})
_EXPORT_KEYS = frozenset({"export_name", "file_name", "sha256", "row_count", "grain"})
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_SOURCE_LIST_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_PARSER_VERSION = re.compile(r"^[a-z][a-z0-9_-]*-v[1-9][0-9]*$")
MANIFEST_ERROR_CATEGORIES = frozenset({
    "manifest.invalid_schema", "manifest.unknown_schema_version",
    "manifest.unknown_allowlist_version", "manifest.duplicate_export_name",
    "manifest.duplicate_accepted_release", "manifest.unsorted_exports",
    "manifest.empty_exports", "manifest.empty_accepted_releases",
    "manifest.invalid_hash", "manifest.negative_count", "manifest.missing_input",
})


class ManifestError(Exception):
    """A value-free manifest construction or validation failure."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class SourceObjectRecord:
    source_list: str
    sha256: str
    byte_size: int
    row_count: int
    _source_lists: tuple[str, ...] = field(
        default=LOGICAL_LIST_ORDER, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        source_lists = _validate_source_lists(self._source_lists)
        _validate_source_object(self.to_dict(), source_lists=source_lists)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_list": self.source_list,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "row_count": self.row_count,
        }


@dataclass(frozen=True)
class AcceptedRelease:
    as_of_date: str
    release_revision: int
    revision_fingerprint: str
    source_objects: tuple[SourceObjectRecord, ...]

    def __post_init__(self) -> None:
        _record_tuple(self.source_objects, SourceObjectRecord)
        authorities = {item._source_lists for item in self.source_objects}
        if len(authorities) != 1:
            raise ManifestError("manifest.invalid_schema")
        _validate_accepted_release(
            self.to_dict(), source_lists=_validate_source_lists(next(iter(authorities)))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of_date": self.as_of_date,
            "release_revision": self.release_revision,
            "revision_fingerprint": self.revision_fingerprint,
            "source_objects": [item.to_dict() for item in self.source_objects],
        }


@dataclass(frozen=True)
class ExportRecord:
    export_name: str
    file_name: str
    sha256: str
    row_count: int
    grain: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.grain, tuple):
            raise ManifestError("manifest.invalid_schema")
        _validate_export(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "export_name": self.export_name,
            "file_name": self.file_name,
            "sha256": self.sha256,
            "row_count": self.row_count,
            "grain": list(self.grain),
        }


@dataclass(frozen=True)
class PublishedManifest:
    schema_version: int
    allowlist_version: str
    parser_contract_version: str
    toolchain: tuple[tuple[str, str], ...]
    accepted_releases: tuple[AcceptedRelease, ...]
    eligible_key_count: int
    exports: tuple[ExportRecord, ...]
    _allowlist: "Allowlist | None" = field(default=None, repr=False, compare=False)
    _source_lists: tuple[str, ...] = field(
        default=LOGICAL_LIST_ORDER, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _record_tuple(self.accepted_releases, AcceptedRelease)
        _record_tuple(self.exports, ExportRecord)
        if (not isinstance(self.toolchain, tuple)
                or not all(isinstance(item, tuple) and len(item) == 2
                           and all(isinstance(value, str) for value in item)
                           for item in self.toolchain)
                or len(self.toolchain) != len(_TOOLCHAIN_KEYS)):
            raise ManifestError("manifest.invalid_schema")
        authority = _authority(self._allowlist)
        source_lists = _validate_source_lists(self._source_lists)
        validate_published_manifest_document(
            self.to_dict(), allowlist=authority, source_lists=source_lists
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "allowlist_version": self.allowlist_version,
            "parser_contract_version": self.parser_contract_version,
            "toolchain": dict(self.toolchain),
            "accepted_releases": [item.to_dict() for item in self.accepted_releases],
            "eligible_key_count": self.eligible_key_count,
            "exports": [item.to_dict() for item in self.exports],
        }

    def to_json(self) -> str:
        document = self.to_dict()
        validate_published_manifest_document(
            document,
            allowlist=_authority(self._allowlist),
            source_lists=_validate_source_lists(self._source_lists),
        )
        return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def _is_count(value: object, *, positive: bool = False) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= (1 if positive else 0)
    )


def _record_tuple(value: object, record_type: type) -> None:
    if not isinstance(value, tuple) or not all(isinstance(item, record_type) for item in value):
        raise ManifestError("manifest.invalid_schema")


def _validate_source_lists(source_lists: object) -> tuple[str, ...]:
    if (
        not isinstance(source_lists, tuple)
        or not source_lists
        or not all(
            isinstance(item, str) and _SOURCE_LIST_IDENTIFIER.fullmatch(item)
            for item in source_lists
        )
        or len(source_lists) != len(set(source_lists))
    ):
        raise ManifestError("manifest.invalid_schema")
    return source_lists


def _authority(allowlist: "Allowlist | None") -> "Allowlist":
    from calico_publish.allowlist import Allowlist, AllowlistError, ExportEntry, load_allowlist

    if allowlist is None:
        try:
            return load_allowlist(Path(__file__).resolve().parents[1] / "contracts/publication-exports-v1.json")
        except AllowlistError:
            raise ManifestError("manifest.invalid_schema") from None
    if (not isinstance(allowlist, Allowlist)
            or type(allowlist.schema_version) is not int or allowlist.schema_version != 1
            or allowlist.allowlist_version != "publication-exports-v1"
            or not isinstance(allowlist.exports, tuple) or not allowlist.exports
            or not all(isinstance(entry, ExportEntry) for entry in allowlist.exports)):
        raise ManifestError("manifest.invalid_schema")
    # The authority is already validated by load_allowlist; validate names here
    # before using them as keys even when a caller constructs a fixture authority.
    names = [entry.export_name for entry in allowlist.exports]
    if (not all(isinstance(name, str) and _IDENTIFIER.fullmatch(name) for name in names)
            or len(names) != len(set(names))):
        raise ManifestError("manifest.invalid_schema")
    return allowlist


def _validate_source_object(
    document: object, *, source_lists: tuple[str, ...]
) -> None:
    if not isinstance(document, dict) or set(document) != _SOURCE_OBJECT_KEYS:
        raise ManifestError("manifest.invalid_schema")
    if document.get("source_list") not in source_lists:
        raise ManifestError("manifest.invalid_schema")
    if not isinstance(document.get("sha256"), str) or not _HASH.fullmatch(document["sha256"]):
        raise ManifestError("manifest.invalid_hash")
    if not _is_count(document.get("byte_size")) or not _is_count(document.get("row_count")):
        raise ManifestError("manifest.negative_count")


def _validate_accepted_release(
    document: object, *, source_lists: tuple[str, ...]
) -> None:
    if not isinstance(document, dict) or set(document) != _ACCEPTED_RELEASE_KEYS:
        raise ManifestError("manifest.invalid_schema")
    if not isinstance(document.get("as_of_date"), str) or not _DATE.fullmatch(document["as_of_date"]):
        raise ManifestError("manifest.invalid_schema")
    if not _is_count(document.get("release_revision"), positive=True):
        raise ManifestError("manifest.negative_count")
    if not isinstance(document.get("revision_fingerprint"), str) or not _HASH.fullmatch(
        document["revision_fingerprint"]
    ):
        raise ManifestError("manifest.invalid_hash")
    source_objects = document.get("source_objects")
    if not isinstance(source_objects, list) or not source_objects:
        raise ManifestError("manifest.invalid_schema")
    for source_object in source_objects:
        _validate_source_object(source_object, source_lists=source_lists)
    observed_source_lists = [item["source_list"] for item in source_objects]
    if (
        observed_source_lists != sorted(observed_source_lists)
        or set(observed_source_lists) != set(source_lists)
        or len(observed_source_lists) != len(source_lists)
    ):
        raise ManifestError("manifest.invalid_schema")


def _validate_export(document: object) -> None:
    if not isinstance(document, dict) or set(document) != _EXPORT_KEYS:
        raise ManifestError("manifest.invalid_schema")
    if not isinstance(document.get("export_name"), str) or not _IDENTIFIER.fullmatch(document["export_name"]):
        raise ManifestError("manifest.invalid_schema")
    file_name = document.get("file_name")
    if not isinstance(file_name, str) or file_name != f'{document["export_name"]}.csv':
        raise ManifestError("manifest.invalid_schema")
    if not isinstance(document.get("sha256"), str) or not _HASH.fullmatch(document["sha256"]):
        raise ManifestError("manifest.invalid_hash")
    if not _is_count(document.get("row_count")):
        raise ManifestError("manifest.negative_count")
    grain = document.get("grain")
    if (
        not isinstance(grain, list)
        or not grain
        or not all(isinstance(item, str) and _IDENTIFIER.fullmatch(item) for item in grain)
        or len(grain) != len(set(grain))
    ):
        raise ManifestError("manifest.invalid_schema")


def validate_published_manifest_document(
    document: object,
    *,
    allowlist: "Allowlist | None" = None,
    source_lists: tuple[str, ...] = LOGICAL_LIST_ORDER,
) -> None:
    """Validate public provenance against the committed authority by default.

    Default readers validate the complete production source-list authority.
    Fixture readers must supply their own explicit closed source-list tuple.
    JSON Schema describes structure; this validator also proves ordering,
    identity uniqueness, and agreement with the external publication authority.
    """

    source_lists = _validate_source_lists(source_lists)
    if not isinstance(document, dict) or set(document) != MANIFEST_DOCUMENT_KEYS:
        raise ManifestError("manifest.invalid_schema")
    if type(document.get("schema_version")) is not int or document.get("schema_version") != 1:
        raise ManifestError("manifest.unknown_schema_version")
    if document.get("allowlist_version") != "publication-exports-v1":
        raise ManifestError("manifest.unknown_allowlist_version")
    if not isinstance(document.get("parser_contract_version"), str) or not _PARSER_VERSION.fullmatch(document["parser_contract_version"]):
        raise ManifestError("manifest.invalid_schema")
    toolchain = document.get("toolchain")
    if (
        not isinstance(toolchain, dict)
        or set(toolchain) != _TOOLCHAIN_KEYS
        or not all(isinstance(value, str) and _VERSION.fullmatch(value) for value in toolchain.values())
    ):
        raise ManifestError("manifest.invalid_schema")
    if not _is_count(document.get("eligible_key_count")):
        raise ManifestError("manifest.negative_count")

    releases = document.get("accepted_releases")
    if not isinstance(releases, list) or not releases:
        raise ManifestError("manifest.empty_accepted_releases")
    for release in releases:
        _validate_accepted_release(release, source_lists=source_lists)
    release_keys = [(item["as_of_date"], item["release_revision"]) for item in releases]
    if len(release_keys) != len(set(release_keys)):
        raise ManifestError("manifest.duplicate_accepted_release")
    if release_keys != sorted(release_keys):
        raise ManifestError("manifest.invalid_schema")

    exports = document.get("exports")
    if not isinstance(exports, list) or not exports:
        raise ManifestError("manifest.empty_exports")
    for export in exports:
        _validate_export(export)
    export_names = [item["export_name"] for item in exports]
    if len(export_names) != len(set(export_names)):
        raise ManifestError("manifest.duplicate_export_name")
    if export_names != sorted(export_names):
        raise ManifestError("manifest.unsorted_exports")
    entries = {entry.export_name: entry for entry in _authority(allowlist).exports}
    if allowlist is not None and set(export_names) != set(entries):
        raise ManifestError("manifest.invalid_schema")
    for export in exports:
        entry = entries.get(export["export_name"])
        if (entry is None or export["file_name"] != entry.file_name
                or tuple(export["grain"]) != entry.grain):
            raise ManifestError("manifest.invalid_schema")


def project_published_manifest(
    *,
    allowlist: "Allowlist",
    staged_exports: Sequence["StagedExport"],
    accepted_releases: Sequence[AcceptedRelease],
    eligible_key_count: int,
    parser_contract_version: str,
    toolchain: Mapping[str, str],
    source_lists: tuple[str, ...] = LOGICAL_LIST_ORDER,
) -> PublishedManifest:
    """Construct the manifest only from explicitly named, already-safe fields."""

    from calico_publish.export import StagedExport

    if (allowlist is None or not isinstance(staged_exports, (tuple, list)) or not staged_exports
            or not isinstance(accepted_releases, (tuple, list)) or not accepted_releases
            or not isinstance(toolchain, Mapping)
            or not all(isinstance(item, StagedExport) for item in staged_exports)
            or not all(isinstance(item, AcceptedRelease) for item in accepted_releases)):
        raise ManifestError("manifest.missing_input")
    allowlist = _authority(allowlist)
    source_lists = _validate_source_lists(source_lists)
    if not all(isinstance(item.export_name, str) for item in staged_exports):
        raise ManifestError("manifest.invalid_schema")
    if (set(toolchain) != _TOOLCHAIN_KEYS
            or not all(isinstance(value, str) for value in toolchain.values())):
        raise ManifestError("manifest.invalid_schema")
    entries = {entry.export_name: entry for entry in allowlist.exports}
    staged_by_name = {item.export_name: item for item in staged_exports}
    if len(staged_by_name) != len(staged_exports) or set(staged_by_name) != set(entries):
        raise ManifestError("manifest.missing_input")
    exports = tuple(
        ExportRecord(
            export_name=name,
            file_name=staged_by_name[name].file_name,
            sha256=staged_by_name[name].sha256,
            row_count=staged_by_name[name].row_count,
            grain=entries[name].grain,
        )
        for name in sorted(entries)
    )
    releases = tuple(
        AcceptedRelease(
            as_of_date=release.as_of_date,
            release_revision=release.release_revision,
            revision_fingerprint=release.revision_fingerprint,
            source_objects=tuple(sorted(release.source_objects, key=lambda item: item.source_list)),
        )
        for release in sorted(accepted_releases, key=lambda item: (item.as_of_date, item.release_revision))
    )
    return PublishedManifest(
        schema_version=1,
        allowlist_version=allowlist.allowlist_version,
        parser_contract_version=parser_contract_version,
        toolchain=tuple(sorted(toolchain.items())),
        accepted_releases=releases,
        eligible_key_count=eligible_key_count,
        exports=exports,
        _allowlist=allowlist,
        _source_lists=source_lists,
    )


__all__ = [
    "MANIFEST_DOCUMENT_KEYS",
    "MANIFEST_ERROR_CATEGORIES",
    "AcceptedRelease",
    "ExportRecord",
    "ManifestError",
    "PublishedManifest",
    "SourceObjectRecord",
    "project_published_manifest",
    "validate_published_manifest_document",
]
