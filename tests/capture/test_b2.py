"""Offline contract tests for the publication-only B2 archive reader."""

from __future__ import annotations

import unittest
from unittest import mock

from calico_capture.archive import ArchiveError
from calico_capture.b2 import (
    B2ReadOnlyArchive,
    EXPECTED_CAPABILITIES,
    EXPECTED_NAME_PREFIX,
    EXPECTED_READ_ONLY_CAPABILITIES,
)
from tests.capture.test_b2_adapter import (
    _new_simulator,
    _provision_scoped_credentials,
    _shared_api_config,
)


class TestB2ReadOnlyArchive(unittest.TestCase):
    def test_exact_list_and_read_scope_is_accepted(self) -> None:
        raw = _new_simulator()
        key_id, key, _ = _provision_scoped_credentials(
            raw, capabilities=EXPECTED_READ_ONLY_CAPABILITIES
        )
        archive = B2ReadOnlyArchive.authorize(key_id, key, api_config=_shared_api_config(raw))
        self.assertEqual(archive.scope.capabilities, frozenset({"listFiles", "readFiles"}))
        self.assertEqual(archive.list_versions(EXPECTED_NAME_PREFIX + "missing.json"), ())

    def test_capture_capable_key_is_rejected(self) -> None:
        raw = _new_simulator()
        key_id, key, _ = _provision_scoped_credentials(raw, capabilities=EXPECTED_CAPABILITIES)
        with self.assertRaises(ArchiveError) as raised:
            B2ReadOnlyArchive.authorize(key_id, key, api_config=_shared_api_config(raw))
        self.assertEqual(raised.exception.category, "archive.scope_rejected")

    def test_write_method_fails_before_provider_io(self) -> None:
        raw = _new_simulator()
        key_id, key, _ = _provision_scoped_credentials(
            raw, capabilities=EXPECTED_READ_ONLY_CAPABILITIES
        )
        archive = B2ReadOnlyArchive.authorize(key_id, key, api_config=_shared_api_config(raw))
        with mock.patch.object(archive._bucket, "upload_bytes") as upload:
            with self.assertRaises(ArchiveError) as raised:
                archive.put_object(EXPECTED_NAME_PREFIX + "object.bin", b"synthetic")
        self.assertEqual(raised.exception.category, "archive.scope_rejected")
        upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
