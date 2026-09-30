import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

from highschoolphysics.document_adapters import AdapterError
from highschoolphysics.document_adapters.mineru_api import (
    _extract_result,
    _require_https_host,
    convert_pdf,
)


class MinerUApiTests(unittest.TestCase):
    def _result_zip(self, _url, path, _timeout):
        middle = {
            "_version_name": "3.4.0",
            "_backend": "vlm",
            "pdf_info": [{"page_idx": 0, "page_size": [100, 100], "para_blocks": []}],
        }
        content = [{"type": "text", "page_idx": 0, "bbox": [1, 2, 90, 20], "text": "题干"}]
        with ZipFile(path, "w") as archive:
            archive.writestr("full.md", "题干")
            archive.writestr("layout.json", json.dumps(middle))
            archive.writestr("source_content_list.json", json.dumps(content))

    def test_official_batch_upload_and_poll_flow_returns_structured_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source.write_bytes(b"%PDF-1.4 test")
            work = root / "work"
            work.mkdir()
            calls = []

            def fake_api(url, token, *, method="GET", payload=None, timeout=30):
                calls.append((url, token, method, payload))
                if method == "POST":
                    return {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload.invalid/signed"]}}
                return {
                    "code": 0,
                    "data": {"extract_result": [{"data_id": "doc-stable", "state": "done", "full_zip_url": "https://cdn-mineru.openxlab.org.cn/result.zip"}]},
                }

            with mock.patch("highschoolphysics.document_adapters.mineru_api._api_json", side_effect=fake_api), \
                 mock.patch("highschoolphysics.document_adapters.mineru_api._put_source") as put_source, \
                 mock.patch("highschoolphysics.document_adapters.mineru_api._download_result", side_effect=self._result_zip):
                result = convert_pdf(
                    source,
                    work,
                    {"api_token": "test-token", "model_name": "vlm", "data_id": "doc-stable", "is_ocr": False},
                    timeout_seconds=30,
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][0], "https://mineru.net/api/v4/file-urls/batch")
            self.assertEqual(calls[0][1], "test-token")
            self.assertEqual(calls[0][3]["model_version"], "vlm")
            self.assertFalse(calls[0][3]["files"][0]["is_ocr"])
            self.assertEqual(calls[1][0], "https://mineru.net/api/v4/extract-results/batch/batch-1")
            put_source.assert_called_once()
            self.assertEqual(result["version"], "mineru-api-vlm")
            self.assertEqual(Path(result["middle_path"]).name, "layout.json")
            self.assertEqual(Path(result["content_path"]).name, "source_content_list.json")

    def test_result_archive_rejects_parent_path_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "unsafe.zip"
            with ZipFile(archive_path, "w") as archive:
                archive.writestr("../escaped.txt", "no")
            with self.assertRaisesRegex(AdapterError, "unsafe path"):
                _extract_result(archive_path, root / "out")

    def test_result_and_upload_urls_require_official_https_hosts(self):
        with self.assertRaises(AdapterError):
            _require_https_host("http://cdn-mineru.openxlab.org.cn/file.zip", {"cdn-mineru.openxlab.org.cn"})
        with self.assertRaises(AdapterError):
            _require_https_host("https://attacker.example/file.zip", {"cdn-mineru.openxlab.org.cn"})


if __name__ == "__main__":
    unittest.main()
