"""Scan candidate documentation without exposing untrusted paths in diagnostics.

Candidate membership is Git's tracked plus nonignored file set, read from the
working tree. Inventory outputs never scan themselves. Markdown destinations
resolve file-relatively; prose backticks prefer root paths, then file-relative
paths, then a unique public basename. External URLs and SQL identifiers are not
local citations. Commands, globs, extension tokens and documented API routes
have explicit syntax categories. Unresolved retained references are annotated,
with a public successor or the planning-boundary explanation as their pointer.

Historical transitions independently authenticate a reachable commit, source
blob, content SHA-256 and exact prior occurrence. They never enter the current
unresolved inventory. All failures carry fixed categories only.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import posixpath
import re
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from tools.privacy_scan.git_objects import BatchBlobReader, classify_mode, list_tree, run_git
from tools.privacy_scan.scanner import scan_text


MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_OCCURRENCES = 50000
INVENTORY_PATH = "docs/provenance/citation-inventory-v1.json"
TRANSITIONS_PATH = "docs/provenance/citation-transitions-v1.json"
REGISTER_PATH = "docs/decisions/register.md"
BOUNDARY_PATH = "docs/decisions/planning-directory-not-published.md"
GENERATED = frozenset({INVENTORY_PATH, TRANSITIONS_PATH})
KINDS = frozenset({"markdown_link", "markdown_image", "reference_definition", "reference_link",
                   "backtick_path", "private_path", "command_syntax", "glob_syntax", "api_syntax",
                   "extension_syntax"})
RESOLUTIONS = frozenset({"resolved", "missing", "missing_fragment", "outside_root", "private", "syntax"})
_PATH = re.compile(r"[A-Za-z0-9_. /%#@+~{}*?\[\]<>|:\\-]+\Z")
_LOCATOR = re.compile(r"L[1-9][0-9]*:C[1-9][0-9]*(?::N[1-9][0-9]*)?\Z")
_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_IDS = re.compile(r"(?<![A-Za-z0-9-])[DCIW]-[0-9]{3}(?![A-Za-z0-9-])")
_EXT = re.compile(r"(?:^|/)[^. /][^\n]*\.(?:md|json|py|sql|ya?ml|csv|toml|txt|pbip|pbir|tmdl|parquet|duckdb|db|png|svg|pdf|html)(?:#[^ ]*)?\Z", re.I)
_ABSOLUTE = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:Users|home|tmp|private|var)/)")
_INLINE = re.compile(r"(!?)\[([^\]\n]*)\]\(\s*(<[^>\n]+>|[^\s\n]+?)(?:\s+[\"'][^\n]*?[\"'])?\s*\)")
_DEFINITION = re.compile(r"^ {0,3}\[([^\]\n]+)\]:[ \t]*(<[^>\n]+>|[^ \t\n]+)(?:[ \t]+[^\n]*)?$", re.M)
_REFERENCE = re.compile(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]")
_BACKTICK = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")


class CitationError(Exception):
    def __init__(self, category: str = "citation.invalid"):
        super().__init__(category)
        self.category = category


class CitationDisposition(str, Enum):
    REPOINTED = "repointed"
    ANNOTATED_LEFT_BEHIND = "annotated_left_behind"
    REMOVED = "removed"


def _public_path(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value) > 4096 or not _PATH.fullmatch(value)
            or value.startswith(("/", "\\")) or "\\" in value
            or any(p in {"", ".", ".."} for p in value.rstrip("/").split("/"))
            or re.match(r"[A-Za-z]:", value) or scan_text("citation", value)):
        raise CitationError("citation.invalid_path")
    return value


@dataclass(frozen=True, order=True)
class CitationOccurrence:
    source: str
    locator: str
    target: str
    kind: str
    resolution: str
    disposition: str | None = None
    successor: str | None = None

    def __post_init__(self):
        _public_path(self.source)
        if (not isinstance(self.locator, str) or not _LOCATOR.fullmatch(self.locator)
                or self.kind not in KINDS or self.resolution not in RESOLUTIONS
                or not isinstance(self.target, str) or not self.target or len(self.target) > 4096
                or scan_text("citation", self.target)):
            raise CitationError("citation.invalid_occurrence")
        unresolved = self.resolution not in {"resolved", "syntax"}
        if unresolved:
            if self.disposition != CitationDisposition.ANNOTATED_LEFT_BEHIND.value:
                raise CitationError("citation.invalid_disposition")
            if self.successor is None:
                raise CitationError("citation.missing_annotation")
            _public_path(self.successor)
        elif self.disposition is not None or self.successor is not None:
            raise CitationError("citation.invalid_disposition")


def _root(root: object) -> Path:
    try:
        p = Path(root)
        if p.is_symlink() or not p.is_dir():
            raise CitationError("citation.invalid_root")
        return p.resolve(strict=True)
    except (TypeError, ValueError, OSError):
        raise CitationError("citation.invalid_root") from None


def _read(root: Path, name: str) -> str:
    _public_path(name)
    p = root / name
    try:
        # Reject symlinks in every path component, including directory links.
        cursor = root
        for part in PurePosixPath(name).parts:
            cursor /= part
            if cursor.is_symlink():
                raise CitationError("citation.unsafe_file")
        if not p.resolve(strict=True).is_relative_to(root) or not p.is_file():
            raise CitationError("citation.unsafe_file")
        with p.open("rb") as handle:
            data = handle.read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise CitationError("citation.oversize")
        return data.decode("utf-8")
    except (OSError, UnicodeError):
        raise CitationError("citation.unreadable") from None


def candidate_paths(root: object) -> tuple[str, ...]:
    base = _root(root)
    try:
        # Git supplies the ignore boundary, including tracked ignored files.
        raw = run_git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], base)
        paths = sorted(set(raw.decode("utf-8").split("\0")) - {""})
        for name in paths:
            _public_path(name)
            cursor = base
            for part in PurePosixPath(name).parts:
                cursor /= part
                if cursor.is_symlink():
                    raise CitationError("citation.unsafe_file")
            if not cursor.is_file() or not cursor.resolve(strict=True).is_relative_to(base):
                raise CitationError("citation.unsafe_file")
        return tuple(paths)
    except CitationError:
        raise
    except Exception:
        raise CitationError("citation.candidate_error") from None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CitationError("citation.duplicate_key")
        result[key] = value
    return result


def _json(text: str):
    try:
        return json.loads(text, object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(CitationError("citation.invalid_json")))
    except (TypeError, ValueError, RecursionError):
        raise CitationError("citation.invalid_json") from None


def load_document(path: Path):
    try:
        if path.is_symlink():
            raise CitationError("citation.unsafe_file")
        with path.open("rb") as handle:
            data = handle.read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise CitationError("citation.oversize")
        return _json(data.decode("utf-8"))
    except (OSError, UnicodeError, TypeError):
        raise CitationError("citation.unreadable") from None


def _documents(root: Path, paths: tuple[str, ...]) -> dict[str, str]:
    return {p: _read(root, p) for p in paths if p not in GENERATED and p.endswith((".md", ".json"))}


def _locator(text: str, offset: int) -> str:
    return f"L{text.count(chr(10), 0, offset) + 1}:C{offset - text.rfind(chr(10), 0, offset)}"


def _anchors(text: str) -> set[str]:
    anchors = set()
    used: dict[str, int] = {}
    for line in text.splitlines():
        m = re.match(r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?$", line)
        if m:
            raw = re.sub(r"[^\w\- ]", "", m[1].lower()).replace(" ", "-")
            index = used.get(raw, 0)
            used[raw] = index + 1
            anchors.add(raw if index == 0 else f"{raw}-{index}")
    anchors.update(re.findall(r"<(?:a|span)\s+[^>]*(?:id|name)=[\"']([^\"']+)[\"']", text, re.I))
    return anchors


def _successor(target: str, paths: set[str]) -> str:
    # Resolve preserved private labels only to actual public successors.
    basename = target.split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    possibilities = sorted(p for p in paths if p.startswith("docs/provenance/")
                           and p not in GENERATED and p.rsplit("/", 1)[-1] in {basename, basename + ".md"})
    return possibilities[0] if len(possibilities) == 1 else BOUNDARY_PATH


def _resolve(source: str, raw: str, kind: str, paths: set[str], documents: dict[str, str]):
    raw = html.unescape(raw.strip().strip("<>"))
    if not raw or len(raw) > 4096:
        raise CitationError("citation.invalid_target")
    if _ABSOLUTE.match(raw) or scan_text("citation", raw):
        return "[private_reference]", "private", BOUNDARY_PATH
    value = unquote(raw, errors="strict").replace("\\", "/")
    if _ABSOLUTE.match(value) or scan_text("citation", value):
        return "[private_reference]", "private", BOUNDARY_PATH
    if kind == "private_path":
        safe = value if _PATH.fullmatch(value) and not value.startswith("/") else "[private_reference]"
        return safe, "private", _successor(safe, paths)
    if urlsplit(value).scheme or value.startswith("//"):
        return None
    if kind.endswith("_syntax"):
        return "[documented_" + kind.removesuffix("_syntax") + "]", "syntax", None
    if not _PATH.fullmatch(value):
        raise CitationError("citation.invalid_target")
    path, _, fragment = value.partition("#")
    if not path:
        possibilities = [source]
    elif path.startswith("/"):
        possibilities = [posixpath.normpath(path.lstrip("/"))]
    elif kind == "backtick_path":
        possibilities = [posixpath.normpath(path), posixpath.normpath(posixpath.join(posixpath.dirname(source), path))]
        # Historical prose often names a unique file without its directory.
        if "/" not in path:
            matches = sorted(p for p in paths if p.rsplit("/", 1)[-1] == path)
            if len(matches) == 1:
                possibilities.extend(matches)
    else:
        possibilities = [posixpath.normpath(posixpath.join(posixpath.dirname(source), path))]
    normalized = possibilities[0]
    if all(p == ".." or p.startswith("../") for p in possibilities):
        return "[outside_repository]", "outside_root", BOUNDARY_PATH
    for p in possibilities:
        if p == ".." or p.startswith("../"):
            continue
        if p in paths or any(name.startswith(p.rstrip("/") + "/") for name in paths):
            normalized = p
            if fragment and (p not in documents or fragment not in _anchors(documents[p])):
                return p + "#" + fragment, "missing_fragment", _successor(p, paths)
            return p + ("#" + fragment if fragment else ""), "resolved", None
    return normalized + ("#" + fragment if fragment else ""), "missing", _successor(normalized, paths)


def _backtick_kind(raw: str) -> str | None:
    if urlsplit(raw.replace("\\", "/")).scheme and not _ABSOLUTE.match(raw):
        return None
    if raw.startswith(("/api/", "/app/")) or "|/" in raw:
        return "api_syntax"
    if re.fullmatch(r"\.[a-z]{1,10}", raw):
        return "extension_syntax"
    if any(c in raw for c in "*{}<>") or "..." in raw:
        return "command_syntax" if " " in raw else "glob_syntax"
    if re.match(r"(?:python|py|git|dbt|GET|POST|PATH=|--)[ =]", raw):
        return "command_syntax" if "/" in raw or "\\" in raw or _EXT.search(raw) else None
    value = raw.replace("\\", "/")
    if _EXT.search(value) or "/" in value or _ABSOLUTE.match(raw):
        # Slash-separated calendar dates and prose quotients are not paths.
        if re.fullmatch(r"[0-9]{4}/[0-9]{2}/[0-9]{2}", value) or " / " in value or " = " in value:
            return None
        return "backtick_path"
    return None


def _raw_occurrences(source: str, text: str):
    if source.endswith(".json"):
        payload = _json(text)
        found = []
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "private_path":
                        if not isinstance(item, str) or not item:
                            raise CitationError("citation.invalid_private_path")
                        found.append(item)
                    else:
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(payload)
        matches = list(re.finditer(r'"private_path"\s*:\s*("(?:[^"\\]|\\.)*")', text))
        if len(matches) != len(found):
            raise CitationError("citation.invalid_private_path")
        return [(m.start(), "private_path", _json(m[1])) for m in matches]
    result = []
    claimed = []
    definitions = {}
    for m in _DEFINITION.finditer(text):
        label = " ".join(m[1].lower().split())
        if label in definitions:
            raise CitationError("citation.duplicate_reference")
        definitions[label] = m[2]
        result.append((m.start(), "reference_definition", m[2]))
        claimed.append(m.span())
    for m in _INLINE.finditer(text):
        result.append((m.start(), "markdown_image" if m[1] else "markdown_link", m[3]))
        claimed.append(m.span())
    for m in _REFERENCE.finditer(text):
        if any(a <= m.start() < b for a, b in claimed):
            continue
        label = " ".join((m[2] or m[1]).lower().split())
        if label not in definitions:
            raise CitationError("citation.missing_reference")
        result.append((m.start(), "reference_link", definitions[label]))
        claimed.append(m.span())
    for m in _BACKTICK.finditer(text):
        if any(a <= m.start() < b for a, b in claimed):
            continue
        kind = _backtick_kind(m[1])
        if kind:
            result.append((m.start(), kind, m[1]))
    return result


def _scan(paths: tuple[str, ...], documents: dict[str, str]) -> list[CitationOccurrence]:
    rows = []
    for source, text in sorted(documents.items()):
        for offset, kind, raw in _raw_occurrences(source, text):
            try:
                resolved = _resolve(source, raw, kind, set(paths), documents)
            except (UnicodeError, ValueError):
                raise CitationError("citation.invalid_target") from None
            if resolved is None:
                continue
            target, resolution, successor = resolved
            rows.append(CitationOccurrence(source, _locator(text, offset), target, kind, resolution,
                                           "annotated_left_behind" if successor else None, successor))
            if len(rows) > MAX_OCCURRENCES:
                raise CitationError("citation.too_many_occurrences")
    return sorted(rows, key=lambda o: (o.source, o.locator, o.kind, o.target))


def scan_citations(root: object) -> list[CitationOccurrence]:
    base = _root(root)
    paths = candidate_paths(base)
    return _scan(paths, _documents(base, paths))


def inventory_document(rows: list[CitationOccurrence]) -> dict:
    if not isinstance(rows, list) or any(not isinstance(o, CitationOccurrence) for o in rows):
        raise CitationError("citation.invalid_inventory")
    return {"schema_version": "citation-inventory-v1", "entries": [asdict(o) for o in rows
            if o.resolution not in {"resolved", "syntax"}]}


def _validate_occurrence(value: object) -> CitationOccurrence:
    fields = {"source", "locator", "target", "kind", "resolution", "disposition", "successor"}
    if not isinstance(value, dict) or set(value) != fields:
        raise CitationError("citation.invalid_occurrence")
    try:
        return CitationOccurrence(**value)
    except (TypeError, ValueError):
        raise CitationError("citation.invalid_occurrence") from None


def check_inventory(rows: list[CitationOccurrence], document: object) -> None:
    if (not isinstance(document, dict) or set(document) != {"schema_version", "entries"}
            or document["schema_version"] != "citation-inventory-v1" or not isinstance(document["entries"], list)):
        raise CitationError("citation.invalid_inventory")
    seen = set()
    for value in document["entries"]:
        row = _validate_occurrence(value)
        key = (row.source, row.locator, row.kind)
        if key in seen:
            raise CitationError("citation.duplicate_occurrence")
        seen.add(key)
    if document != inventory_document(rows):
        raise CitationError("citation.inventory_mismatch")


def _historical(root: Path, commit: str):
    if not isinstance(commit, str) or not _OID.fullmatch(commit):
        raise CitationError("citation.invalid_anchor")
    try:
        if commit not in run_git(["rev-list", "--all"], root).decode("ascii").splitlines():
            raise CitationError("citation.unreachable_anchor")
        entries = list_tree(commit, root)
        documents, blobs = {}, {}
        paths = tuple(sorted(e.path for e in entries))
        with BatchBlobReader(root) as reader:
            for entry in entries:
                _public_path(entry.path)
                if entry.path in GENERATED or not entry.path.endswith((".md", ".json")):
                    continue
                if classify_mode(entry.mode, entry.obj_type) is not None:
                    raise CitationError("citation.invalid_anchor")
                _, body, oversized = reader.read(entry.oid, MAX_DOCUMENT_BYTES)
                if oversized:
                    raise CitationError("citation.oversize")
                documents[entry.path] = body.decode("utf-8")
                blobs[entry.path] = (entry.oid, hashlib.sha256(body).hexdigest())
        return _scan(paths, documents), blobs
    except CitationError:
        raise
    except Exception:
        raise CitationError("citation.anchor_error") from None


def _transition_entries(root: Path, commit: str, prior: list[CitationOccurrence], blobs: dict) -> list[dict]:
    current = scan_citations(root)
    keyed = {(o.source, o.locator, o.kind): o for o in current}
    result = []
    for old in prior:
        if old.resolution == "syntax":
            continue
        new = keyed.get((old.source, old.locator, old.kind))
        if new is not None and new.target == old.target:
            continue
        blob, digest = blobs[old.source]
        result.append({"prior_commit": commit, "prior_blob": blob, "prior_sha256": digest,
                       "occurrence": asdict(old), "disposition": "repointed" if new else "removed",
                       "current": asdict(new) if new else None})
    return result


def transition_document(root: object, commit: str, prior: list[CitationOccurrence], prior_bytes: bytes) -> dict:
    base = _root(root)
    authenticated, blobs = _historical(base, commit)
    if (not isinstance(prior, list) or any(o not in authenticated for o in prior)
            or len({o.source for o in prior}) != 1
            or not isinstance(prior_bytes, bytes)
            or hashlib.sha256(prior_bytes).hexdigest() != blobs[prior[0].source][1]):
        raise CitationError("citation.invalid_anchor")
    return {"schema_version": "citation-transitions-v1", "entries": _transition_entries(base, commit, prior, blobs)}


def check_transitions(root: object, document: object) -> None:
    base = _root(root)
    if (not isinstance(document, dict) or set(document) != {"schema_version", "entries"}
            or document["schema_version"] != "citation-transitions-v1" or not isinstance(document["entries"], list)):
        raise CitationError("citation.invalid_transitions")
    seen, cache = set(), {}
    expected_order = []
    for entry in document["entries"]:
        if (not isinstance(entry, dict) or set(entry) != {"prior_commit", "prior_blob", "prior_sha256", "occurrence", "disposition", "current"}
                or not isinstance(entry["prior_blob"], str) or not _OID.fullmatch(entry["prior_blob"])
                or not isinstance(entry["prior_sha256"], str) or not _SHA.fullmatch(entry["prior_sha256"])
                or entry["disposition"] not in {"removed", "repointed"}):
            raise CitationError("citation.invalid_transition")
        old = _validate_occurrence(entry["occurrence"])
        key = (entry["prior_commit"], old.source, old.locator, old.kind)
        if key in seen:
            raise CitationError("citation.duplicate_transition")
        seen.add(key)
        expected_order.append(key)
        commit = entry["prior_commit"]
        if commit not in cache:
            cache[commit] = _historical(base, commit)
        rows, blobs = cache[commit]
        if old not in rows or blobs[old.source] != (entry["prior_blob"], entry["prior_sha256"]):
            raise CitationError("citation.anchor_mismatch")
        if _transition_entries(base, commit, [old], blobs) != [entry]:
            raise CitationError("citation.transition_mismatch")
    if expected_order != sorted(expected_order):
        raise CitationError("citation.transition_order")


def scan_decision_ids(root: object) -> list[str]:
    base = _root(root)
    paths = candidate_paths(base)
    documents = _documents(base, paths)
    return sorted({m[0] for path, text in documents.items() if path != REGISTER_PATH for m in _IDS.finditer(text)})


def check_decision_register(root: object, text: str | None = None) -> None:
    base = _root(root)
    text = _read(base, REGISTER_PATH) if text is None else text
    if not isinstance(text, str):
        raise CitationError("citation.invalid_register")
    definitions = re.findall(r"^## ([DCIW]-[0-9]{3})\n\n([^\n]+)\n", text, re.M)
    required = scan_decision_ids(base)
    if (len(definitions) != len(required) or [d[0] for d in definitions] != required
            or any(len(s) > 700 or scan_text("citation", s) or _IDS.search(s) for _, s in definitions)
            or set(_IDS.findall(text)) != set(required)):
        raise CitationError("citation.register_mismatch")


def atomic_write(path: Path, data: bytes) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise CitationError("citation.unsafe_file")
        fd, temporary = tempfile.mkstemp(prefix=".citation-", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        raise CitationError("citation.write_error") from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def encode_document(document: dict) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


# Curated authority for the IDs actually cited when this contract was introduced.
# New citations require a reviewed one-line summary; no authority is invented.
DECISION_SUMMARIES = {
    "D-001": "The California Charity Registry Monitor describes changes in the published registry population; calico abbreviates california charity observatory.",
    "D-003": "Python owns one canonical landing interpretation: decode current registry CSV as CP1252 and parse with QUOTE_NONE; dbt consumes admitted normalized data.",
    "D-006": "The published delinquent population consists exactly of Delinquent and Delinquent - Late Fees Due; status vocabulary changes require review.",
    "D-007": "V1 intentionally publishes bounded organization name, full State Charity Reg#, city/state and observed history; excluded identifiers, addresses, people, contacts, raw sources and unapproved joins remain private.",
    "D-008": "The settled stack uses Python for landing and admission, DuckDB/dbt SQL for analytics, Power BI for presentation, private immutable storage and scheduled GitHub Actions capture.",
    "D-010": "Formal survival analysis, restricted mean duration, constant-hazard equivalents and standardized 30-day risk are deferred; a release count alone is not a readiness gate.",
    "D-012": "A broader archive census is excluded from v1 and remains optional work after the public presentation gate.",
    "D-020": "The retained Gate A evidence is the benchmark for Gate B reproduction; documented parser repairs and definition corrections preserve predecessor claims through supersedes pairs.",
    "W-002": "The unredacted migration investigation remains private; its redacted successor is the sole publishable investigation artifact.",
}


def _register(ids: list[str], summaries: dict[str, str]) -> bytes:
    if not isinstance(summaries, dict):
        raise CitationError("citation.invalid_summary")
    parts = ["# Public decision register\n\n",
             "This register contains only zero-padded global authority IDs cited by current public documentation. "
             "Historical unpadded tokens retain their original meaning. Summaries describe the resolved build authority; "
             "the private planning record is excluded.\n"]
    for identifier in ids:
        summary = summaries.get(identifier)
        if (not isinstance(summary, str) or not summary.strip() or summary != summary.strip()
                or "\n" in summary or "\r" in summary or len(summary) > 700
                or scan_text("citation", summary) or _IDS.search(summary)):
            raise CitationError("citation.missing_safe_summary")
        parts.append(f"\n## {identifier}\n\n{summary}\n")
    return "".join(parts).encode("utf-8")


@contextmanager
def _lock(root: Path):
    """Serialize publication; lock contention fails without exposing a path."""
    try:
        raw = run_git(["rev-parse", "--git-path", "citation-scan.lock"], root).decode().strip()
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        if path.is_symlink():
            raise CitationError("citation.unsafe_file")
        handle = path.open("a+b")
    except Exception:
        raise CitationError("citation.lock_error") from None
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except OSError:
        raise CitationError("citation.lock_busy") from None
    finally:
        handle.close()


def _counts(rows: list[CitationOccurrence], transitions: dict, ids: list[str]) -> dict[str, int]:
    return {"occurrences": len(rows), "unresolved": len(inventory_document(rows)["entries"]),
            "transitions": len(transitions["entries"]), "decisions": len(ids)}


def _check_repository(root: Path) -> dict[str, int]:
    rows = scan_citations(root)
    inventory = load_document(root / INVENTORY_PATH)
    check_inventory(rows, inventory)
    transitions = load_document(root / TRANSITIONS_PATH)
    check_transitions(root, transitions)
    check_decision_register(root)
    paths = set(candidate_paths(root))
    for row in rows:
        if row.successor and row.successor not in paths:
            raise CitationError("citation.missing_successor")
    if (root / INVENTORY_PATH).read_bytes() != encode_document(inventory):
        raise CitationError("citation.noncanonical_inventory")
    if (root / TRANSITIONS_PATH).read_bytes() != encode_document(transitions):
        raise CitationError("citation.noncanonical_transitions")
    return _counts(rows, transitions, scan_decision_ids(root))


def check_repository(root: object) -> dict[str, int]:
    """Read-only contract check; never regenerates drifting documents."""
    base = _root(root)
    with _lock(base):
        return _check_repository(base)


def write_repository(root: object, *, summaries: dict[str, str] | None = None) -> dict[str, int]:
    """Generate exact documents, preserving independently verified transitions."""
    base = _root(root)
    with _lock(base):
        ids = scan_decision_ids(base)
        authority = dict(DECISION_SUMMARIES)
        # Preserve already curated one-line summaries for later phases.
        if (base / REGISTER_PATH).exists():
            retained = _read(base, REGISTER_PATH)
            for identifier, summary in re.findall(r"^## ([DCIW]-[0-9]{3})\n\n([^\n]+)\n", retained, re.M):
                authority.setdefault(identifier, summary)
        if summaries is not None:
            if not isinstance(summaries, dict):
                raise CitationError("citation.invalid_summary")
            authority.update(summaries)
        register = _register(ids, authority)
        check_decision_register(base, register.decode())
        if (base / TRANSITIONS_PATH).exists():
            transitions = load_document(base / TRANSITIONS_PATH)
            check_transitions(base, transitions)
        else:
            transitions = {"schema_version": "citation-transitions-v1", "entries": []}
        # Empty repositories have no historical transitions. A HEAD anchor is
        # used only if one exists; every actual historical record is checked.
        try:
            commit = run_git(["rev-parse", "--verify", "HEAD"], base).decode("ascii").strip()
        except Exception:
            commit = None
        if commit:
            old, blobs = _historical(base, commit)
            new_entries = _transition_entries(base, commit, old, blobs)
            known = {(e["prior_commit"], e["occurrence"]["source"], e["occurrence"]["locator"], e["occurrence"]["kind"])
                     for e in transitions["entries"]}
            for entry in new_entries:
                key = (entry["prior_commit"], entry["occurrence"]["source"], entry["occurrence"]["locator"], entry["occurrence"]["kind"])
                if key not in known:
                    transitions["entries"].append(entry)
            transitions["entries"].sort(key=lambda e: (e["prior_commit"], e["occurrence"]["source"], e["occurrence"]["locator"], e["occurrence"]["kind"]))
            check_transitions(base, transitions)
        # Validate final projected candidate content before replacing any file.
        paths = tuple(sorted(set(candidate_paths(base)) | GENERATED | {REGISTER_PATH}))
        documents = _documents(base, candidate_paths(base))
        documents[REGISTER_PATH] = register.decode("utf-8")
        rows = _scan(paths, documents)
        for row in rows:
            if row.successor and row.successor not in paths:
                raise CitationError("citation.missing_successor")
        inventory = inventory_document(rows)
        check_inventory(rows, inventory)
        atomic_write(base / REGISTER_PATH, register)
        atomic_write(base / INVENTORY_PATH, encode_document(inventory))
        atomic_write(base / TRANSITIONS_PATH, encode_document(transitions))
        return _check_repository(base)
