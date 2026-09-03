"""Fixed-endpoint bounded four-object download matrix (06-03-PLAN.md Task 1).

Exercises `calico_capture.source.fetch_candidate` entirely against an
injected `Opener` test double -- never a live socket -- covering the
happy path (exactly four objects, stable order, closed manifest shape) and
every closed failure category: non-HTTPS/off-allowlist endpoints and
redirects, too many redirects, a redirect with no `Location`, a non-200
response, an oversized payload, an empty response, and a declared/actual
`Content-Length` mismatch. Also proves the resulting candidate root is
immediately consumable by the real
`calico_landing.candidate.resolve_and_stage_candidate` boundary, and that
`calico_capture.orchestrator.capture()` now reaches this fetcher by
default.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from calico_capture import source as source_module
from calico_capture.source import RawResponse, SourceError, fetch_candidate
from calico_landing.candidate import resolve_and_stage_candidate
from calico_landing.contracts import LOGICAL_LIST_ORDER, load_csv_contract

_CSV_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "ag-registry-csv-v1.json"
)


class _FakeBody:
    """A `.read(n)`/`.close()` double matching `http.client.HTTPResponse`'s
    shape exactly -- streams fixed bytes in caller-controlled chunks.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0
        self.closed = False

    def read(self, size: int) -> bytes:
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


def _ok_response(data: bytes, *, content_length: "int | None" = None) -> RawResponse:
    return RawResponse(
        status=200,
        location=None,
        content_length=len(data) if content_length is None else content_length,
        body=_FakeBody(data),
    )


def _redirect_response(location: "str | None") -> RawResponse:
    return RawResponse(status=302, location=location, content_length=None, body=_FakeBody(b""))


def _status_response(status: int) -> RawResponse:
    return RawResponse(status=status, location=None, content_length=None, body=_FakeBody(b""))


class _QueueOpener:
    """A per-URL queue of canned responses -- each call to the opener for a
    given URL pops the next queued response. A URL with no explicit queue
    falls back to `default_factory(url)` if provided.
    """

    def __init__(self, by_url: "dict[str, list[RawResponse]]") -> None:
        self._by_url = {url: list(responses) for url, responses in by_url.items()}
        self.calls: list[str] = []

    def __call__(self, url: str, timeout_seconds: float) -> RawResponse:
        self.calls.append(url)
        queue = self._by_url.get(url)
        if not queue:
            raise AssertionError(f"unexpected opener call for {url!r}")
        return queue.pop(0)


def _uniform_opener(response_factory) -> "callable":
    """An opener that returns `response_factory(url)` for every call,
    regardless of which of the four fixed endpoints is requested.
    """

    def _opener(url: str, timeout_seconds: float) -> RawResponse:
        return response_factory(url)

    return _opener


class HappyPathTests(unittest.TestCase):
    def test_fetches_exactly_four_objects_in_stable_order_with_closed_manifest_shape(
        self,
    ) -> None:
        bodies = {
            logical_list: f"{logical_list}-body".encode("ascii")
            for logical_list in LOGICAL_LIST_ORDER
        }

        def _factory(url: str) -> RawResponse:
            for logical_list, endpoint in source_module.SOURCE_ENDPOINTS.items():
                if url == endpoint:
                    return _ok_response(bodies[logical_list])
            raise AssertionError(f"unexpected url {url!r}")

        candidate_root = fetch_candidate(opener=_uniform_opener(_factory))
        try:
            manifest_path = candidate_root / "candidate-set.json"
            self.assertTrue(manifest_path.is_file())
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(document["manifest_version"], 1)
            self.assertEqual(set(document["objects"].keys()), set(LOGICAL_LIST_ORDER))

            for logical_list in LOGICAL_LIST_ORDER:
                entry = document["objects"][logical_list]
                self.assertEqual(entry["relative_path"], f"{logical_list}.csv")
                staged_path = candidate_root / entry["relative_path"]
                self.assertEqual(staged_path.read_bytes(), bodies[logical_list])
                self.assertEqual(entry["content_length"], len(bodies[logical_list]))
        finally:
            import shutil

            shutil.rmtree(candidate_root, ignore_errors=True)

    def test_fetched_candidate_is_consumable_by_the_real_admission_boundary(self) -> None:
        bodies = {
            logical_list: (
                "Status,RegNum,FEIN,SosFtb,Name,City,State,Issue,LastRenewal,"
                "DateStatusSet,AsOfDate\r\n"
                "Current,ABC123,,,Test Org,Sacramento,CA,2020-01-01,2020-01-01,"
                "2020-01-15,2020/01/15\r\n"
            ).encode("cp1252")
            for logical_list in LOGICAL_LIST_ORDER
        }

        def _factory(url: str) -> RawResponse:
            for logical_list, endpoint in source_module.SOURCE_ENDPOINTS.items():
                if url == endpoint:
                    return _ok_response(bodies[logical_list])
            raise AssertionError(f"unexpected url {url!r}")

        candidate_root = fetch_candidate(opener=_uniform_opener(_factory))
        try:
            contract = load_csv_contract(_CSV_CONTRACT_PATH)
            with tempfile.TemporaryDirectory(prefix="calico-source-staging-") as staging_name:
                staging_dir = Path(staging_name)
                staged = resolve_and_stage_candidate(candidate_root, staging_dir, contract)
                self.assertEqual(set(staged.keys()), set(LOGICAL_LIST_ORDER))
        finally:
            import shutil

            shutil.rmtree(candidate_root, ignore_errors=True)


class RedirectAndAllowlistTests(unittest.TestCase):
    def test_same_host_redirect_is_followed(self) -> None:
        body = b"redirected-body"
        first_url = source_module.SOURCE_ENDPOINTS["charities-may-operate"]
        final_url = "https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/final.csv"

        by_url = {first_url: [_redirect_response(final_url)], final_url: [_ok_response(body)]}
        for logical_list, endpoint in source_module.SOURCE_ENDPOINTS.items():
            if logical_list == "charities-may-operate":
                continue
            by_url[endpoint] = [_ok_response(b"x")]

        opener = _QueueOpener(by_url)
        candidate_root = fetch_candidate(opener=opener)
        try:
            staged = candidate_root / "charities-may-operate.csv"
            self.assertEqual(staged.read_bytes(), body)
        finally:
            import shutil

            shutil.rmtree(candidate_root, ignore_errors=True)

    def test_off_allowlist_redirect_rejects_before_admission(self) -> None:
        first_url = source_module.SOURCE_ENDPOINTS["charities-may-operate"]
        malicious_url = "https://evil.example.com/charities-may-operate.csv"
        by_url = {first_url: [_redirect_response(malicious_url)]}

        opener = _QueueOpener(by_url)
        with self.assertRaises(SourceError) as ctx:
            fetch_candidate(opener=opener)
        self.assertEqual(ctx.exception.category, "source.unapproved_endpoint")

    def test_non_https_fixed_endpoint_is_rejected(self) -> None:
        with mock.patch.dict(
            source_module.SOURCE_ENDPOINTS,
            {"charities-may-operate": "http://oag.ca.gov/insecure.csv"},
        ):
            with self.assertRaises(SourceError) as ctx:
                fetch_candidate(opener=_uniform_opener(lambda url: _ok_response(b"x")))
        self.assertEqual(ctx.exception.category, "source.unapproved_endpoint")

    def test_redirect_with_no_location_header_is_rejected(self) -> None:
        first_url = source_module.SOURCE_ENDPOINTS["charities-may-operate"]
        opener = _QueueOpener({first_url: [_redirect_response(None)]})
        with self.assertRaises(SourceError) as ctx:
            fetch_candidate(opener=opener)
        self.assertEqual(ctx.exception.category, "source.redirect_rejected")

    def test_too_many_redirects_is_rejected(self) -> None:
        first_url = source_module.SOURCE_ENDPOINTS["charities-may-operate"]
        hop_url = "https://oag.ca.gov/sites/all/files/agweb/pdfs/charities/reports/hop.csv"
        responses = [_redirect_response(hop_url) for _ in range(source_module._MAX_REDIRECTS + 2)]
        opener = _QueueOpener({first_url: responses[:1], hop_url: responses[1:]})
        with self.assertRaises(SourceError) as ctx:
            fetch_candidate(opener=opener)
        self.assertEqual(ctx.exception.category, "source.too_many_redirects")


class TransferFailureTests(unittest.TestCase):
    def test_non_200_status_fails_closed(self) -> None:
        opener = _uniform_opener(lambda url: _status_response(404))
        with self.assertRaises(SourceError) as ctx:
            fetch_candidate(opener=opener)
        self.assertEqual(ctx.exception.category, "source.transfer_failed")

    def test_oversized_payload_fails_closed_and_leaves_no_partial_file(self) -> None:
        class _HugeBody:
            def __init__(self) -> None:
                self.closed = False

            def read(self, size: int) -> bytes:
                return b"a" * size

            def close(self) -> None:
                self.closed = True

        def _factory(url: str) -> RawResponse:
            return RawResponse(status=200, location=None, content_length=None, body=_HugeBody())

        with self.assertRaises(SourceError) as ctx:
            fetch_candidate(opener=_uniform_opener(_factory))
        self.assertEqual(ctx.exception.category, "source.payload_too_large")

    def test_empty_response_fails_closed(self) -> None:
        opener = _uniform_opener(lambda url: _ok_response(b""))
        with self.assertRaises(SourceError) as ctx:
            fetch_candidate(opener=opener)
        self.assertEqual(ctx.exception.category, "source.empty_response")

    def test_content_length_mismatch_fails_closed(self) -> None:
        opener = _uniform_opener(
            lambda url: _ok_response(b"short", content_length=999)
        )
        with self.assertRaises(SourceError) as ctx:
            fetch_candidate(opener=opener)
        self.assertEqual(ctx.exception.category, "source.transfer_length_mismatch")

    def test_no_object_leaks_offending_url_or_body_text(self) -> None:
        secret_body = b"contains-a-sensitive-value-that-must-never-leak"

        def _factory(url: str) -> RawResponse:
            return _ok_response(secret_body, content_length=1)

        with self.assertRaises(SourceError) as ctx:
            fetch_candidate(opener=_uniform_opener(_factory))
        rendered = str(ctx.exception)
        self.assertNotIn(str(secret_body), rendered)
        for endpoint in source_module.SOURCE_ENDPOINTS.values():
            self.assertNotIn(endpoint, rendered)

    def test_failure_removes_the_owned_temporary_candidate_root(self) -> None:
        created_roots: list[Path] = []
        real_mkdtemp = tempfile.mkdtemp

        def _tracking_mkdtemp(*args: object, **kwargs: object) -> str:
            path = real_mkdtemp(*args, **kwargs)
            created_roots.append(Path(path))
            return path

        with mock.patch("tempfile.mkdtemp", side_effect=_tracking_mkdtemp):
            with self.assertRaises(SourceError):
                fetch_candidate(opener=_uniform_opener(lambda url: _status_response(500)))

        self.assertEqual(len(created_roots), 1)
        self.assertFalse(created_roots[0].exists())


class OrchestratorDefaultWiringTests(unittest.TestCase):
    def test_capture_default_fetch_candidate_resolves_to_the_real_source_fetcher(self) -> None:
        from calico_capture.orchestrator import _default_fetch_candidate

        with mock.patch(
            "calico_capture.source.fetch_candidate", return_value=Path("sentinel")
        ) as fake_fetch:
            result = _default_fetch_candidate()

        fake_fetch.assert_called_once_with()
        self.assertEqual(result, Path("sentinel"))


if __name__ == "__main__":
    unittest.main()
