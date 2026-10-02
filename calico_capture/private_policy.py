"""Immutable private eligibility bytes bound to one prior public publication.

Content is written and read back before its binding manifest, under the existing
archive/v1/ authority only. Identical retries verify the exact existing version.
Named classification bytes remain private. Removing a bundle requires an owner
operation that deliberately purges EVERY retained version; this module exposes
no delete, hide, share, retention-write or key-administration capability.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from calico_capture.archive import Archive, ArchiveObjectVersion
from calico_dbt.eligibility import EligibilityError, load_eligibility_classifications
from calico_landing.candidate import CandidateError, reject_store_in_git_worktree
from calico_publish.allowlist import AllowlistError, load_allowlist
from calico_publish.manifest import ManifestError, validate_published_manifest_document
from tools.privacy_scan.scanner import scan_text

_PREFIX = "archive/v1/private-policy/"
_SIDECAR = "public-eligibility-v1.json"
_KEYS = frozenset({"schema_version", "classification_version", "sidecar_sha256", "sidecar_length_bytes",
                   "prior_publication_manifest_sha256", "prior_publication_commit", "objects"})
_HASH = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_AUTHORITY = Path(__file__).resolve().parents[1] / "contracts/publication-exports-v3.json"


class PrivatePolicyError(Exception):
    """Only a fixed category crosses the private-data boundary."""
    def __init__(self, category):
        super().__init__(category)
        self.category = category


def canonical_json(document):
    return (json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _unique(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise PrivatePolicyError("private_policy.invalid_document")
        document[key] = value
    return document


def _parse(raw, category):
    try:
        return json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, TypeError, UnicodeError, PrivatePolicyError):
        raise PrivatePolicyError(category) from None


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def private_policy_object_key(sidecar_sha256):
    if not _hash(sidecar_sha256):
        raise PrivatePolicyError("private_policy.invalid_hash")
    return _PREFIX + "objects/" + sidecar_sha256 + ".json"


def private_policy_manifest_key(prior_publication_manifest_sha256):
    if not _hash(prior_publication_manifest_sha256):
        raise PrivatePolicyError("private_policy.invalid_hash")
    return _PREFIX + "manifests/" + prior_publication_manifest_sha256 + ".json"


def _validate_key(key):
    if not isinstance(key, str) or not re.fullmatch(
            r"archive/v1/private-policy/(?:objects|manifests)/[0-9a-f]{64}\.json", key):
        raise PrivatePolicyError("private_policy.invalid_object_key")


@dataclass(frozen=True)
class PrivatePolicyManifest:
    schema_version: int
    classification_version: str
    sidecar_sha256: str
    sidecar_length_bytes: int
    prior_publication_manifest_sha256: str
    prior_publication_commit: str
    objects: tuple[str, ...]

    def to_dict(self):
        return {"schema_version": self.schema_version,
                "classification_version": self.classification_version,
                "sidecar_sha256": self.sidecar_sha256,
                "sidecar_length_bytes": self.sidecar_length_bytes,
                "prior_publication_manifest_sha256": self.prior_publication_manifest_sha256,
                "prior_publication_commit": self.prior_publication_commit,
                "objects": list(self.objects)}


def validate_private_policy_manifest(document):
    if not isinstance(document, dict) or set(document) != _KEYS:
        raise PrivatePolicyError("private_policy.invalid_manifest")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise PrivatePolicyError("private_policy.invalid_manifest")
    version = document["classification_version"]
    if not isinstance(version, str) or not version or scan_text("private-policy-version", version):
        raise PrivatePolicyError("private_policy.invalid_classification_version")
    if not _hash(document["sidecar_sha256"]) or not _hash(document["prior_publication_manifest_sha256"]):
        raise PrivatePolicyError("private_policy.invalid_hash")
    if (type(document["sidecar_length_bytes"]) is not int or document["sidecar_length_bytes"] <= 0):
        raise PrivatePolicyError("private_policy.invalid_length")
    if not isinstance(document["prior_publication_commit"], str) or not _COMMIT.fullmatch(document["prior_publication_commit"]):
        raise PrivatePolicyError("private_policy.invalid_publication_commit")
    keys = document["objects"]
    if not isinstance(keys, list) or len(keys) != 1:
        raise PrivatePolicyError("private_policy.invalid_object_key")
    _validate_key(keys[0])
    if keys[0] != private_policy_object_key(document["sidecar_sha256"]):
        raise PrivatePolicyError("private_policy.object_identity_mismatch")
    return PrivatePolicyManifest(**{**document, "objects": tuple(keys)})


def _read_local_file(path):
    """Reject links/reparse aliases in every existing component; never echo a path."""
    try:
        absolute = Path(os.path.abspath(path))
        current = Path(absolute.anchor)
        metadata = os.lstat(current)
        for part in absolute.parts[1:]:
            current = current / part
            metadata = os.lstat(current)
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if stat.S_ISLNK(metadata.st_mode) or (reparse and getattr(metadata, "st_file_attributes", 0) & reparse):
                raise PrivatePolicyError("private_policy.link_rejected")
        if not stat.S_ISREG(metadata.st_mode):
            raise PrivatePolicyError("private_policy.invalid_local_file")
        descriptor = os.open(absolute, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            return stream.read()
    except FileNotFoundError:
        raise PrivatePolicyError("private_policy.local_file_missing") from None
    except (OSError, TypeError, ValueError):
        raise PrivatePolicyError("private_policy.local_read_failed") from None


def _sidecar_version(raw):
    document = _parse(raw, "private_policy.invalid_sidecar")
    # Reuse the one eligibility authority on the EXACT bytes read from the source
    # or archive, in an owned canonical temporary root; no classifier is added.
    try:
        with tempfile.TemporaryDirectory(prefix="calico-policy-check-") as temp:
            root = Path(temp).resolve()
            (root / _SIDECAR).write_bytes(raw)
            load_eligibility_classifications(root, require_document=True)
    except (EligibilityError, OSError):
        raise PrivatePolicyError("private_policy.invalid_sidecar") from None
    return document["classification_version"]


def _versions(archive, key):
    _validate_key(key)
    try:
        versions = archive.list_versions(key)
    except Exception:
        raise PrivatePolicyError("private_policy.archive_list_failed") from None
    if not isinstance(versions, tuple) or any(
            not isinstance(version, ArchiveObjectVersion)
            or not isinstance(version.version_id, str) or not version.version_id
            or not _hash(version.sha256) or type(version.content_length) is not int
            or version.content_length < 0 for version in versions):
        raise PrivatePolicyError("private_policy.invalid_archive_metadata")
    if not versions:
        return ()
    if len(versions) != 1:
        raise PrivatePolicyError("private_policy.version_ambiguous")
    if versions[0].action == "hide":
        raise PrivatePolicyError("private_policy.hidden_object")
    if versions[0].action == "start":
        raise PrivatePolicyError("private_policy.unfinished_upload")
    if versions[0].action != "upload":
        raise PrivatePolicyError("private_policy.version_ambiguous")
    return versions


def _read_exact(archive, key, *, missing_category="private_policy.object_missing"):
    versions = _versions(archive, key)
    if not versions:
        raise PrivatePolicyError(missing_category)
    version = versions[0]
    try:
        raw = archive.get_object(key, version_id=version.version_id)
    except Exception:
        raise PrivatePolicyError("private_policy.archive_read_failed") from None
    if (not isinstance(raw, bytes) or version.content_length != len(raw)
            or version.sha256 != hashlib.sha256(raw).hexdigest()):
        raise PrivatePolicyError("private_policy.readback_mismatch")
    return raw


def _ensure_exact(archive, key, raw):
    versions = _versions(archive, key)
    if versions:
        version = versions[0]
        if version.content_length != len(raw) or version.sha256 != hashlib.sha256(raw).hexdigest():
            raise PrivatePolicyError("private_policy.collision")
    else:
        try:
            archive.put_object(key, raw)
        except Exception:
            raise PrivatePolicyError("private_policy.archive_write_failed") from None
    if _read_exact(archive, key) != raw:
        raise PrivatePolicyError("private_policy.readback_mismatch")


def load_private_policy_manifest(archive: Archive, *, prior_publication_manifest_sha256,
                                 prior_publication_commit=None, expected_classification_version=None):
    key = private_policy_manifest_key(prior_publication_manifest_sha256)
    raw = _read_exact(archive, key, missing_category="private_policy.manifest_missing")
    document = _parse(raw, "private_policy.invalid_manifest")
    manifest = validate_private_policy_manifest(document)
    if raw != canonical_json(document):
        raise PrivatePolicyError("private_policy.noncanonical_manifest")
    if (manifest.prior_publication_manifest_sha256 != prior_publication_manifest_sha256
            or (prior_publication_commit is not None and manifest.prior_publication_commit != prior_publication_commit)):
        raise PrivatePolicyError("private_policy.publication_binding_mismatch")
    if (expected_classification_version is not None and manifest.classification_version != expected_classification_version):
        raise PrivatePolicyError("private_policy.classification_version_mismatch")
    sidecar = _read_exact(archive, manifest.objects[0])
    if hashlib.sha256(sidecar).hexdigest() != manifest.sidecar_sha256:
        raise PrivatePolicyError("private_policy.sidecar_hash_mismatch")
    if len(sidecar) != manifest.sidecar_length_bytes:
        raise PrivatePolicyError("private_policy.sidecar_length_mismatch")
    if _sidecar_version(sidecar) != manifest.classification_version:
        raise PrivatePolicyError("private_policy.classification_version_mismatch")
    return manifest


def seed_private_policy_bundle(archive: Archive, store_root, *, published_manifest_path, published_data_commit):
    try:
        root = Path(store_root)
        reject_store_in_git_worktree(root)
        # Read with no-follow before invoking the existing loader, rejecting root
        # aliases as well as a linked direct child.
        try:
            sidecar = _read_local_file(root / _SIDECAR)
        except PrivatePolicyError as exc:
            if exc.category == "private_policy.local_file_missing":
                raise PrivatePolicyError("private_policy.sidecar_missing") from None
            raise
        load_eligibility_classifications(root, require_document=True)
        version = _sidecar_version(sidecar)
    except CandidateError:
        raise PrivatePolicyError("private_policy.invalid_store") from None
    except EligibilityError:
        raise PrivatePolicyError("private_policy.invalid_sidecar") from None
    public_raw = _read_local_file(published_manifest_path)
    public_document = _parse(public_raw, "private_policy.invalid_publication_manifest")
    try:
        validate_published_manifest_document(public_document, allowlist=load_allowlist(_AUTHORITY))
    except (ManifestError, AllowlistError):
        raise PrivatePolicyError("private_policy.invalid_publication_manifest") from None
    if public_raw != canonical_json(public_document):
        raise PrivatePolicyError("private_policy.noncanonical_publication_manifest")
    digest = hashlib.sha256(sidecar).hexdigest()
    publication_digest = hashlib.sha256(public_raw).hexdigest()
    manifest = validate_private_policy_manifest({
        "schema_version": 1, "classification_version": version, "sidecar_sha256": digest,
        "sidecar_length_bytes": len(sidecar), "prior_publication_manifest_sha256": publication_digest,
        "prior_publication_commit": published_data_commit, "objects": [private_policy_object_key(digest)]})
    _ensure_exact(archive, manifest.objects[0], sidecar)
    _ensure_exact(archive, private_policy_manifest_key(publication_digest), canonical_json(manifest.to_dict()))
    return {"category": "seed_policy.completed", "sidecar_sha256": digest,
            "classification_version": version, "prior_publication_manifest_sha256": publication_digest,
            "prior_publication_commit": published_data_commit, "readback_verified": True}


__all__ = ["PrivatePolicyError", "PrivatePolicyManifest", "private_policy_object_key",
           "private_policy_manifest_key", "validate_private_policy_manifest",
           "seed_private_policy_bundle", "load_private_policy_manifest"]
