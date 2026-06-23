import json
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from highschoolphysics.runtime import (
    CAPABILITY_IDS,
    check_runtime_capabilities,
    check_single_capability,
)


class RuntimeCapabilityTests(unittest.TestCase):
    def test_runtime_capabilities_include_production_targets(self):
        self.assertEqual(
            CAPABILITY_IDS,
            (
                "paddleocr",
                "markitdown",
                "mineru-local",
                "mineru-api",
                "playwright-pdf",
                "oidc-sso",
                "secret-encryption",
            ),
        )

    def test_missing_import_is_reported_without_raising(self):
        result = check_single_capability(
            {
                "id": "missing-test",
                "label": "Missing Test",
                "module": "definitely_missing_hsp_module",
            }
        )
        self.assertEqual(result["status"], "missing_dependency")
        self.assertEqual(result["version"], "")
        self.assertIn("definitely_missing_hsp_module", result["detail"])

    def test_disabled_credential_capability_is_explicit(self):
        result = check_single_capability(
            {
                "id": "api-test",
                "label": "API Test",
                "requires_credential": True,
                "enabled": False,
            }
        )
        self.assertEqual(result["status"], "disabled")

    def test_runtime_summary_is_stable_and_contains_all_capabilities(self):
        result = check_runtime_capabilities()
        self.assertEqual(
            [item["capability_id"] for item in result],
            list(CAPABILITY_IDS),
        )
        for item in result:
            self.assertIn("status", item)
            self.assertIn("label", item)
            self.assertIn("detail", item)
            self.assertIn("version", item)

    def test_python_minimum_is_reported_as_degraded(self):
        result = check_single_capability(
            {
                "id": "future-python",
                "label": "Future Python",
                "module": "json",
                "python_min": (99, 0),
            }
        )

        self.assertEqual(result["status"], "degraded")
        self.assertIn("requires Python", result["detail"])

    def test_version_below_minimum_is_reported_as_degraded(self):
        with patch(
            "highschoolphysics.runtime._package_version",
            return_value="0.0.1a1",
        ):
            result = check_single_capability(
                {
                    "id": "old-package",
                    "label": "Old Package",
                    "module": "json",
                    "package": "json",
                    "minimum_version": "0.1.0",
                }
            )

        self.assertEqual(result["status"], "degraded")
        self.assertIn("requires >= 0.1.0", result["detail"])


class RuntimeCliTests(unittest.TestCase):
    def test_pyproject_declares_production_extras(self):
        text = Path("pyproject.toml").read_text()
        for header in (
            "[project.optional-dependencies]",
            "ocr = [",
            "parsing = [",
            "pdf = [",
            "sso = [",
            "providers = [",
            "production = [",
        ):
            self.assertIn(header, text)
        for dependency in (
            "paddleocr",
            "markitdown",
            "mineru",
            "playwright",
            "Authlib",
            "cryptography",
            "openai",
        ):
            self.assertIn(dependency, text)

    def test_runtime_check_cli_outputs_json(self):
        completed = subprocess.run(
            [sys.executable, "-m", "highschoolphysics.runtime_check", "--json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIn("capabilities", payload)
        self.assertEqual(
            [item["capability_id"] for item in payload["capabilities"]],
            list(CAPABILITY_IDS),
        )


if __name__ == "__main__":
    unittest.main()
