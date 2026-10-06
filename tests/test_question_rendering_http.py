import unittest

from highschoolphysics.public_assets import read_public_asset
from highschoolphysics.question_rendering import question_math_asset_tags, render_question


class QuestionRenderingHTTPTests(unittest.TestCase):
    def test_compact_full_question_moves_child_figures_to_right_column(self):
        document={'number':'12','stem_md':'完整公共题干','options':[],
                  'children':[{'key':'p1','label':'(1)','stem_md':'第一问\n\n![电路](asset:img1)','options':[]},
                              {'key':'p2','label':'(2)','stem_md':'第二问','options':[]}]}
        rendered=render_question(document,asset_url=lambda aid:'/assets/'+aid,compact_layout=True)
        self.assertIn('完整公共题干',rendered)
        self.assertIn('第二问',rendered)
        self.assertEqual(rendered.count('<img '),1)
        self.assertLess(rendered.index('第二问'),rendered.index('<aside class="choice-figures">'))
        self.assertIn('<strong>(1)</strong><div><p>第一问',rendered)
    def test_option_labels_are_rendered_once(self):
        rendered = render_question(
            {
                "number": "1",
                "stem_md": "题干",
                "options": [{"key": "A", "markdown": "选项甲"}],
                "children": [],
            }
        )
        self.assertEqual(rendered.count('class="option-key">A.</span>'), 1)
        self.assertNotIn('class="question-options" type=', rendered)

    def test_bundled_math_assets_are_packaged_with_nested_paths(self):
        for path, expected_type in (
            ("vendor/katex/katex.min.js", "javascript"),
            ("vendor/katex/katex.min.css", "css"),
            ("vendor/katex/contrib/auto-render.min.js", "javascript"),
            ("vendor/katex/fonts/KaTeX_Main-Regular.woff2", "font/woff2"),
            ("question-rendering.js", "javascript"),
        ):
            asset = read_public_asset(path)
            self.assertIsNotNone(asset, path)
            content_type, payload = asset
            self.assertIn(expected_type, content_type)
            self.assertGreater(len(payload), 100)

        self.assertIsNone(read_public_asset("../server.py"))
        self.assertIsNone(read_public_asset("vendor/../../server.py"))

    def test_question_layout_loads_only_local_math_renderer_assets(self):
        head, scripts = question_math_asset_tags("test-v1")
        self.assertIn("/assets/vendor/katex/katex.min.css", head)
        self.assertIn("/assets/vendor/katex/katex.min.js", scripts)
        self.assertIn("/assets/vendor/katex/contrib/auto-render.min.js", scripts)
        self.assertIn("/assets/question-rendering.js", scripts)
        self.assertNotIn("cdn.", head + scripts)


if __name__ == "__main__":
    unittest.main()
