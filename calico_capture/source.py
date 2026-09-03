"""Safe, bounded four-object download into the existing candidate contract
(06-03-PLAN.md Task 1; D-06; 06-RESEARCH.md "no production network
downloader exists ... copy hash-while-streaming candidate staging").

`fetch_candidate()` is the real production `CandidateFetcher` that
`calico_capture.orchestrator.capture()`'s `fetch_candidate` parameter now
defaults to (Task 1's own orchestrator wiring). It fetches exactly the four
`LOGICAL_LIST_ORDER` objects from fixed HTTPS endpoints on the single
approved source host, follows at most a bounded number of same-host
redirects, streams each response into a uniquely owned OS temporary root
while hashing and counting bytes, and only after every one of the four
objects completes successfully writes the existing closed
`candidate-set.json` manifest shape `calico_landing.candidate.
resolve_and_stage_candidate` already consumes.

Every step is bounded: a fixed per-request timeout, a fixed maximum
redirect count, a closed same-host redirect allowlist, and a fixed maximum
payload size per object. This module never decodes, parses, or inspects
CSV content -- it only moves and hashes opaque bytes -- and never logs,
prints, or includes a URL, header, response body, or exception text in a
raised `SourceError`; only a fixed safe `category` and the affected
`logical_list` identifier ever cross this module's boundary (D-06 non-echo
discipline, mirrored from `calico_landing.candidate.CandidateError`).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from calico_landing.candidate import MANIFEST_FILENAME
from calico_landing.contracts import LOGICAL_LIST_ORDER

#: Fixed HTTPS source endpoints, one per closed logical-list identity
#: (D-06). Verified against the currently observed live object identities
#: recorded in `.planning/spikes/001-archive-sample-validation/
#: archive-sample-manifest.json`'s own `original_url` fields -- the same
#: rolling URLs `06-CONTEXT.md`/`PROJECT.md` describe as serving only the
#: latest published files. No other endpoint is ever contacted.
SOURCE_ENDPOINTS: dict[str, str] = {
    "charities-may-operate": (
        "https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/"
        "charities-may-operate.csv"
    ),
    "charities-not-operating": (
        "https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/"
        "charities-not-operating.csv"
    ),
    "charities-undetermined-status": (
        "https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/"
        "charities-undetermined-status.csv"
    ),
    "charities-may-not-operate": (
        "https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/"
        "charities-may-not-operate.csv"
    ),
}

#: The single approved scheme/host every fixed endpoint and every followed
#: redirect must match -- a closed allowlist, never a caller-supplied value
#: (mirrors `tools/privacy_scan/scanner.py`'s single-host verification
#: allowlist pattern).
_ALLOWED_SCHEME = "https"
_ALLOWED_HOST = "oag.ca.gov"

#: Bounded transfer limits (`the agent's Discretion`, 06-CONTEXT.md).
_MAX_REDIRECTS = 5
_REQUEST_TIMEOUT_SECONDS = 60.0
_COPY_CHUNK_BYTES = 1024 * 1024
#: One safe fixed per-object ceiling -- well above the largest currently
#: observed real object (~49MB per `archive-sample-manifest.json`'s own
#: recorded `received_bytes`) but still bounded, so a malicious or runaway
#: response can never exhaust local disk.
_MAX_OBJECT_BYTES = 500 * 1024 * 1024

_MANIFEST_VERSION = 1


class SourceError(Exception):
    """Raised on any source download failure. Carries only a fixed safe
    `category` and an optional `logical_list` identifier -- never a URL,
    header, response body, or exception text (D-06 non-echo discipline,
    mirrored from `calico_landing.candidate.CandidateError`).
    """

    def __init__(self, category: str, *, logical_list: str | None = None) -> None:
        super().__init__(category)
        self.category = category
        self.logical_list = logical_list


@dataclass(frozen=True)
class RawResponse:
    """One safe, transport-neutral single-hop HTTP response record an
    `Opener` returns. `body` must expose a binary-mode `.read(n) -> bytes`
    method (matching `http.client.HTTPResponse`) and, if not `None`, a
    no-argument `.close()` -- the same shape the default opener and every
    test double share, so this module's own streaming/redirect loop is the
    only place that ever interprets a status code or follows a hop.
    """

    status: int
    location: str | None
    content_length: int | None
    body: object


#: A single-hop, non-redirect-following HTTPS GET: `(url, timeout_seconds)
#: -> RawResponse`. This module's own loop is what interprets a 3xx status
#: and decides whether to follow a redirect -- an `Opener` never follows
#: one itself. Injectable so tests exercise every redirect/allowlist/
#: timeout/size/mismatch scenario without a live socket (D-14).
Opener = Callable[[str, float], RawResponse]


def _validate_endpoint(url: str, *, logical_list: str) -> None:
    """Fail closed unless `url` is exactly `https://` on the single
    approved host -- applied to both the fixed initial endpoint and every
    followed redirect target (T-06-03A).
    """

    parsed = urlsplit(url)
    if parsed.scheme != _ALLOWED_SCHEME or parsed.hostname != _ALLOWED_HOST:
        raise SourceError("source.unapproved_endpoint", logical_list=logical_list)


def _default_opener(url: str, timeout_seconds: float) -> RawResponse:
    import http.client

    parsed = urlsplit(url)
    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port or 443, timeout=timeout_seconds
    )
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    try:
        connection.request("GET", target)
        response = connection.getresponse()
    except (OSError, http.client.HTTPException) as exc:
        connection.close()
        raise SourceError("source.transfer_failed") from exc

    location = response.getheader("Location") if 300 <= response.status < 400 else None
    raw_content_length = response.getheader("Content-Length")
    content_length: int | None = None
    if raw_content_length is not None:
        try:
            content_length = int(raw_content_length)
        except ValueError:
            content_length = None

    return RawResponse(
        status=response.status,
        location=location,
        content_length=content_length,
        body=response,
    )


def _close_response_body(response: RawResponse) -> None:
    close = getattr(response.body, "close", None)
    if close is not None:
        close()


def _stream_response_to_file(
    response: RawResponse, destination_path: Path, *, logical_list: str
) -> int:
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with open(destination_path, "wb") as handle:
            while True:
                chunk = response.body.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                byte_count += len(chunk)
                if byte_count > _MAX_OBJECT_BYTES:
                    raise SourceError(
                        "source.payload_too_large", logical_list=logical_list
                    )
                digest.update(chunk)
                handle.write(chunk)
    except OSError as exc:
        raise SourceError("source.transfer_failed", logical_list=logical_list) from exc
    except SourceError:
        destination_path.unlink(missing_ok=True)
        raise

    if byte_count == 0:
        destination_path.unlink(missing_ok=True)
        raise SourceError("source.empty_response", logical_list=logical_list)

    if response.content_length is not None and response.content_length != byte_count:
        destination_path.unlink(missing_ok=True)
        raise SourceError("source.transfer_length_mismatch", logical_list=logical_list)

    digest.hexdigest()  # bound the transfer identity; not part of the manifest shape
    return byte_count


def _fetch_one_object(
    logical_list: str, url: str, destination_path: Path, opener: Opener
) -> int:
    """Fetch exactly one fixed-endpoint object, following at most
    `_MAX_REDIRECTS` same-host HTTPS hops, and return the exact byte count
    streamed to `destination_path`. Never leaves a partial file behind on
    any failure path.
    """

    _validate_endpoint(url, logical_list=logical_list)
    current_url = url

    for _ in range(_MAX_REDIRECTS + 1):
        response = opener(current_url, _REQUEST_TIMEOUT_SECONDS)
        try:
            if 300 <= response.status < 400:
                location = response.location
                if not location:
                    raise SourceError(
                        "source.redirect_rejected", logical_list=logical_list
                    )
                _validate_endpoint(location, logical_list=logical_list)
                current_url = location
                continue

            if response.status != 200:
                raise SourceError(
                    "source.transfer_failed", logical_list=logical_list
                )

            return _stream_response_to_file(
                response, destination_path, logical_list=logical_list
            )
        finally:
            _close_response_body(response)

    raise SourceError("source.too_many_redirects", logical_list=logical_list)


def fetch_candidate(*, opener: Opener = _default_opener) -> Path:
    """Fetch exactly the four fixed `LOGICAL_LIST_ORDER` objects into a
    fresh, uniquely owned OS temporary candidate root and emit the closed
    `candidate-set.json` manifest `calico_landing.candidate` already
    consumes (D-06; must_haves truth 1).

    `opener` is a test-only seam (`Opener`); production callers never pass
    it and get the real bounded HTTPS transport (`_default_opener`).

    Fails closed with `SourceError` -- and removes the owned temporary
    root before raising -- if any of the four objects cannot be safely,
    completely fetched: this function either returns one complete,
    ordered, four-object candidate directory with its manifest, or raises
    without ever leaving a usable partial candidate on disk (must_haves
    truth 2 / acceptance criteria).
    """

    candidate_root = Path(tempfile.mkdtemp(prefix="calico-source-"))

    try:
        objects: dict[str, dict[str, object]] = {}
        for logical_list in LOGICAL_LIST_ORDER:
            url = SOURCE_ENDPOINTS[logical_list]
            destination_path = candidate_root / f"{logical_list}.csv"
            byte_count = _fetch_one_object(logical_list, url, destination_path, opener)
            objects[logical_list] = {
                "relative_path": f"{logical_list}.csv",
                "content_length": byte_count,
            }

        manifest_document = {"manifest_version": _MANIFEST_VERSION, "objects": objects}
        manifest_path = candidate_root / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(
                manifest_document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ),
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(candidate_root, ignore_errors=True)
        raise

    return candidate_root


__all__ = [
    "Opener",
    "RawResponse",
    "SOURCE_ENDPOINTS",
    "SourceError",
    "fetch_candidate",
]
