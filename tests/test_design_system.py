import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DesignSystemTests(unittest.TestCase):
    def test_design_system_document_covers_tokens_components_and_pdf(self):
        doc = ROOT / "docs/product-design/highschoolphysics-design-system.md"
        self.assertTrue(doc.exists())
        text = doc.read_text(encoding="utf-8")

        self.assertIn("# HighSchoolPhysics Visual Design System", text)
        self.assertIn("## Design Tokens", text)
        self.assertIn("## Component Inventory", text)
        self.assertIn("## Production Operations Screens", text)
        self.assertIn("## Print And PDF Rules", text)

    def test_css_exposes_reusable_tokens_and_production_components(self):
        css = (ROOT / "highschoolphysics/assets/app.css").read_text(
            encoding="utf-8"
        )

        for token in (
            "--color-primary",
            "--space-4",
            "--radius-card",
            "--shadow-card",
            "--focus-ring",
        ):
            self.assertIn(token, css)
        for selector in (
            ".status-chip",
            ".provider-ops-panel",
            ".sso-settings-panel",
            ".pdf-export-panel",
            ".job-timeline",
            ".pdf-preview-panel",
            "@media print",
        ):
            self.assertIn(selector, css)


if __name__ == "__main__":
    unittest.main()
