import tempfile
import unittest
from pathlib import Path

from highschoolphysics.pdf_export import write_pdf_artifact


class PdfExportTests(unittest.TestCase):
    def test_write_pdf_artifact_uses_engine_and_records_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "wrong-book.pdf"

            artifact = write_pdf_artifact(
                "<html><body>错题本</body></html>",
                output_path,
                engine=lambda html, options: {
                    "pdf_bytes": b"%PDF-1.4\nfake\n",
                    "engine_version": "fake-pdf-v1",
                },
                options={"format": "A4"},
            )

            self.assertTrue(output_path.exists())
            self.assertEqual(output_path.read_bytes(), b"%PDF-1.4\nfake\n")
            self.assertEqual(artifact["content_type"], "application/pdf")
            self.assertEqual(artifact["byte_size"], len(b"%PDF-1.4\nfake\n"))
            self.assertEqual(artifact["engine_version"], "fake-pdf-v1")


if __name__ == "__main__":
    unittest.main()
