import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from highschoolphysics.runtime import (
    CAPABILITY_DEFINITIONS,
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

    def test_mineru_is_not_ready_until_its_supported_pipeline_models_are_present(self):
        definition = next(
            item for item in CAPABILITY_DEFINITIONS if item["id"] == "mineru-local"
        )
        with tempfile.TemporaryDirectory(prefix="hsp-mineru-readiness-") as directory:
            home = Path(directory)
            config_path = home / "mineru.json"
            model_root = home / "pipeline-models"
            with patch.dict("os.environ", {}, clear=True), \
                    patch("highschoolphysics.runtime.Path.home", return_value=home), \
                    patch("highschoolphysics.runtime._package_version", return_value="3.4.0"), \
                    patch("highschoolphysics.runtime.import_module"), \
                    patch("highschoolphysics.runtime.shutil.which", return_value="/usr/bin/mineru"):
                missing = check_single_capability(definition)
                self.assertEqual(missing["status"], "missing_configuration")

                config_path.write_text("{not-json", encoding="utf-8")
                invalid = check_single_capability(definition)
                self.assertEqual(invalid["status"], "invalid_configuration")

                config_path.write_text(
                    json.dumps({"models-dir": {"pipeline": str(model_root)}}),
                    encoding="utf-8",
                )
                model_root.mkdir()
                absent = check_single_capability(definition)
                self.assertEqual(absent["status"], "missing_models")

                for relative_path, expected_type in definition["required_pipeline_models"]:
                    target = model_root / relative_path
                    if expected_type == "file":
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(b"fixture model marker")
                    else:
                        target.mkdir(parents=True, exist_ok=True)

                configured = check_single_capability(definition)
                self.assertEqual(configured["status"], "configured")
                self.assertIn("does not prove OCR quality", configured["detail"])

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

    def test_runtime_check_cli_reads_enabled_mineru_api_provider_from_database(self):
        with tempfile.TemporaryDirectory(prefix="hsp-runtime-provider-") as directory:
            db_path = Path(directory) / "school.sqlite3"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """create table provider_configs(
                         provider_kind text,provider_name text,model_name text,enabled integer,
                         api_endpoint text,secret_ciphertext text,last_test_status text,
                         updated_at text,created_at text
                       )"""
                )
                conn.execute(
                    """insert into provider_configs values(
                         'mineru_api','Production MinerU','vlm',1,
                         'https://mineru.example.test','encrypted-test-value','ready','2026-09-30','2026-09-29'
                       )"""
                )
            completed = subprocess.run(
                [sys.executable, "-m", "highschoolphysics.runtime_check", "--json", "--db", str(db_path)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            mineru_api = next(
                item for item in json.loads(completed.stdout)["capabilities"]
                if item["capability_id"] == "mineru-api"
            )
            self.assertEqual(mineru_api["status"], "ready")
            self.assertIn("successful saved connection test", mineru_api["detail"])
            self.assertEqual(mineru_api["version"], "vlm")


if __name__ == "__main__":
    unittest.main()
