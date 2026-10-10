"""Immutable inspection and precisely bounded publication changes."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def api(test):
    test.assertIsNotNone(importlib.util.find_spec("tools.docs_public.inspection"),
                         "Immutable public inspection must exist before freeze")
    from tools.docs_public import inspection
    return inspection


class PublicInspectionContractTests(unittest.TestCase):
    def test_immutable_inspection_contract_exists_before_freeze(self):
        m = api(self)
        for name in ("build_substantive_input_manifest", "validate_public_inspection",
                     "render_public_inspection", "check_final_url_diff"):
            self.assertTrue(callable(getattr(m, name, None)))


class MigrationRedactionGateTests(unittest.TestCase):
    pass


class ExactFinalDiffTests(unittest.TestCase):
    pass


class StableReportCitationTests(unittest.TestCase):
    pass
