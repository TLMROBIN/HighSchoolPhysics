import io
import hashlib
import json
import os
from pathlib import Path
import subprocess
import stat
import tempfile
import threading
import unittest
import zipfile
import xml.etree.ElementTree as ET
from unittest import mock

from PIL import Image

from highschoolphysics.document_adapters import AdapterError, convert_document
from highschoolphysics.document_adapters.docx_native import _math_value, _math_text_run, convert_docx, _reconcile_markitdown_blocks
from highschoolphysics.document_adapters.mtef_worker import _normalise_formula, _restore_unlisted_cjk
from highschoolphysics.document_adapters.mineru_pdf import _run, convert_pdf
from highschoolphysics.document_store import DocumentStore


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def tiny_png():
    image = Image.new("RGB", (24, 18))
    for y in range(18):
        for x in range(24):
            image.putpixel((x, y), ((x * 29) % 255, (y * 37) % 255, ((x + y) * 17) % 255))
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


def make_docx(path, rels_xml=None, include_ole=False, ole_payload=None):
    ole_paragraph = (
        '<w:p><w:r><w:t>embedded formula</w:t>'
        + (
            '<w:object><v:shape><v:imagedata r:id="rId2"/></v:shape>'
            '<o:OLEObject Type="Embed" ProgID="Equation.DSMT4" r:id="rId3"/></w:object>'
            if ole_payload is not None else
            '<w:object><o:OLEObject Type="Embed" ProgID="Equation.DSMT4"/></w:object>'
        )
        + '</w:r></w:p>'
        if include_ole else ""
    )
    document = '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="%s" xmlns:r="%s" xmlns:a="%s" xmlns:m="%s" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:v="urn:schemas-microsoft-com:vml">
 <w:body>
  <w:p><w:r><w:t>第1题：先看图</w:t></w:r><w:r><w:drawing><a:graphic><a:graphicData><a:blip r:embed="rId1"/></a:graphicData></a:graphic></w:drawing></w:r><w:r><w:t>，再计算</w:t></w:r><m:oMath><m:sSup><m:e><m:r><m:t>v</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup><m:r><m:t>=</m:t></m:r><m:f><m:num><m:r><m:t>F</m:t></m:r></m:num><m:den><m:r><m:t>m</m:t></m:r></m:den></m:f></m:oMath></w:p>
  %s
  <w:tbl><w:tr><w:tc><w:p><w:r><w:t>时间</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>速度</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>1 s</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>2 m/s</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
  <w:sectPr/>
 </w:body>
</w:document>''' % (W, R, A, M, ole_paragraph)
    rels = rels_xml or ('''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/picture.png"/>
    </Relationships>''' if ole_payload is None else '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/picture.png"/>
      <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/preview.png"/>
      <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="embeddings/oleObject1.bin"/>
    </Relationships>''')
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", document.encode("utf-8"))
        archive.writestr("word/_rels/document.xml.rels", rels if isinstance(rels, bytes) else rels.encode("utf-8"))
        archive.writestr("word/media/picture.png", tiny_png())
        if ole_payload is not None:
            archive.writestr("word/media/preview.png", tiny_png())
            archive.writestr("word/embeddings/oleObject1.bin", ole_payload)


def make_wps_shape_docx(path):
    document = f'''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}"
 xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
 xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
 <w:body><w:p><mc:AlternateContent>
  <mc:Choice Requires="wps"><w:drawing><wp:anchor><wp:docPr id="6" name="矩形 6"/>
   <wp:positionH relativeFrom="column"><wp:posOffset>-12345</wp:posOffset></wp:positionH>
   <wp:positionV relativeFrom="paragraph"><wp:posOffset>67890</wp:posOffset></wp:positionV>
   <wp:extent cx="6330950" cy="3599180"/><a:graphic><a:graphicData>
    <wps:wsp><wps:cNvPr id="6" name="矩形 6"/><wps:spPr>
     <a:xfrm><a:off x="0" y="0"/><a:ext cx="6330950" cy="3599180"/></a:xfrm>
     <a:prstGeom prst="rect"/><a:noFill/>
    </wps:spPr><wps:txbx><w:txbxContent><w:p><w:r><w:t>1115.</w:t></w:r></w:p></w:txbxContent></wps:txbx>
    </wps:wsp>
   </a:graphicData></a:graphic>
  </wp:anchor></w:drawing></mc:Choice>
  <mc:Fallback><w:pict><v:rect><v:imagedata o:title=""/></v:rect></w:pict></mc:Fallback>
 </mc:AlternateContent></w:p><w:sectPr/></w:body>
</w:document>'''
    rels = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'''
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", document.encode("utf-8"))
        archive.writestr("word/_rels/document.xml.rels", rels.encode("utf-8"))


class DocumentAdapterTests(unittest.TestCase):
    def test_legacy_answer_scripts_restore_source_formatting_without_touching_math_or_ambiguous_text(self):
        from highschoolphysics.document_adapters.docx_native import restore_cached_docx_scripts
        source = io.BytesIO()
        script = '<w:r><w:rPr><w:vertAlign w:val="%s"/></w:rPr><w:t>%s</w:t></w:r>'
        text = lambda value: '<w:r><w:t>%s</w:t></w:r>' % value
        paragraphs = [text('V') + script % ('subscript', '0') + text('=360m') + script % ('superscript', '3'),
                      text('p') + script % ('subscript', '1') + text('=1.92×10') + script % ('superscript', '5') + text('Pa'),
                      text('m') + script % ('superscript', '3') + text(' 和 m3')]
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W, ''.join('<w:p>%s</w:p>' % part for part in paragraphs)))
        layout = {'blocks': [{'id': str(i), 'markdown': md, 'asset_ids': ['figure'], 'source_locator': {'kind': 'word', 'paragraph_index': i}}
                             for i, md in enumerate(['*V*0=360m3', '*p*1=1.92×105Pa，$p_1=1.92\\times10^5$', 'm3 和 m3'], 1)]}
        before = json.dumps(layout)
        fixed = restore_cached_docx_scripts(layout, source.getvalue())
        self.assertEqual(fixed['blocks'][0]['markdown'], 'V$ {}_{0}$=360m$ {}^{3}$')
        self.assertEqual(fixed['blocks'][1]['markdown'], 'p$ {}_{1}$=1.92×10$ {}^{5}$Pa，$p_1=1.92\\times10^5$')
        self.assertEqual(fixed['blocks'][2]['markdown'], 'm3 和 m3')
        self.assertEqual(fixed['blocks'][0]['asset_ids'], ['figure'])
        self.assertEqual(json.dumps(layout), before)
        self.assertEqual(restore_cached_docx_scripts(fixed, source.getvalue()), fixed)

    def test_native_word_run_scripts_keep_subscripts_and_unit_exponents(self):
        from xml.etree import ElementTree as ET
        from highschoolphysics.document_adapters.docx_native import _paragraph_text
        paragraph = ET.fromstring('<w:p xmlns:w="%s"><w:r><w:t>t</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>1</w:t></w:r><w:r><w:t>，20m</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r></w:p>' % W)
        result = _paragraph_text(paragraph, {}, None, None, "school", "b", [], [0], [0], {})
        self.assertEqual(result, 't$ {}_{1}$，20m$ {}^{2}$')

    def test_markitdown_cannot_replace_simple_inline_formula_or_numeric_difference(self):
        for native_text, converted in (
            ("1．小球从静止开始运动，末速度满足 $v^2=2as$，请选择正确结论。", "1．小球从静止开始运动，末速度满足 ，请选择正确结论。"),
            ("1．小球运动如下，速度 v=10。", "1．小球运动如下，速度 v=100。"),
        ):
            native = [{"id": "b1", "type": "paragraph", "markdown": native_text,
                       "asset_ids": [], "issues": [], "source_locator": {}}]
            blocks, _, issues = _reconcile_markitdown_blocks(native, converted)
            self.assertEqual(blocks[0]["markdown"], native_text)
            self.assertEqual(issues[0]["code"], "markitdown_content_difference")
            self.assertEqual(native[0]["issues"], [])

    def test_markitdown_combined_options_without_mathtype_are_not_extra_body(self):
        texts = ["1．核衰变。", "A．高温加快 $^{210}_{84}Po$ 衰变", "B．$Po$ 衰变吸收能量", "C．方程为 $Po\\to Pb+He$", "D．生成 $Pb$ 的中子数"]
        native = [{"id": "b%d" % i, "type": "paragraph", "markdown": text, "asset_ids": [], "issues": [], "source_locator": {}} for i, text in enumerate(texts)]
        converted = "1．核衰变。\n\nA．高温加快 衰变\nB．衰变吸收能量\nC．方程为\nD．生成 的中子数"
        blocks, counts, _ = _reconcile_markitdown_blocks(native, converted)
        self.assertEqual([block["markdown"] for block in blocks], texts)
        self.assertEqual(counts["unmapped_text_block_count"], 0)

    def test_word_numbered_list_does_not_split_an_option_into_question_one(self):
        native = [{"id": "b1", "type": "paragraph", "markdown": "5．波形如图。", "asset_ids": [], "issues": [], "source_locator": {}}, {"id": "b2", "type": "paragraph", "markdown": "A．该绳波传播速度为 $16m/s$", "asset_ids": [], "issues": [], "source_locator": {}}]
        blocks, counts, _ = _reconcile_markitdown_blocks(native, "5．波形如图。\n\n1. 该绳波传播速度为")
        self.assertEqual([block["id"] for block in blocks], ["b1", "b2"])
        self.assertEqual(counts["unmapped_text_block_count"], 0)

    def _run_long_lived_mineru_fixture(self, *, timeout_seconds, cancel_event=None):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        binary = root / "fake-mineru"
        binary.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
        binary.chmod(0o700)
        captured = {}
        real_popen = subprocess.Popen

        def record_process(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            captured["process"] = process
            return process

        with mock.patch("highschoolphysics.document_adapters.mineru_pdf.subprocess.Popen", side_effect=record_process):
            with self.assertRaises(AdapterError) as raised:
                _run([str(binary)], timeout_seconds, os.environ.copy(), cancel_event=cancel_event)
        self.assertIsNotNone(captured["process"].returncode, "the converter subprocess must be reaped")
        return raised.exception

    @unittest.skipUnless(os.name == "posix", "process-group termination requires POSIX")
    def test_mineru_timeout_terminates_and_reaps_the_converter_process(self):
        error = self._run_long_lived_mineru_fixture(timeout_seconds=0.1)
        self.assertEqual(error.code, "conversion_timeout")

    @unittest.skipUnless(os.name == "posix", "process-group termination requires POSIX")
    def test_mineru_cancellation_terminates_and_reaps_the_converter_process(self):
        cancelled = threading.Event()
        cancelled.set()
        error = self._run_long_lived_mineru_fixture(timeout_seconds=20, cancel_event=cancelled)
        self.assertEqual(error.code, "cancelled")

    def test_omml_default_delimiters_are_parentheses_but_explicit_empty_is_invisible(self):
        for properties, expected in (
            ('', r'\left( M+m \right)'),
            ('<m:dPr><m:sepChr m:val=","/></m:dPr>', r'\left( M+m \right)'),
            ('<m:dPr><m:begChr m:val=""/><m:endChr m:val=""/></m:dPr>', r'\left. M+m \right.'),
            ('<m:dPr><m:begChr m:val="["/></m:dPr>', r'\left[ M+m \right)'),
        ):
            formula = ET.fromstring('<m:oMath xmlns:m="%s"><m:d>%s<m:e><m:r><m:t>M+m</m:t></m:r></m:e></m:d></m:oMath>' % (M, properties))
            self.assertEqual(_math_value(formula), (expected, True))

    def test_omml_chinese_punctuation_and_degree_symbols_remain_renderable(self):
        self.assertEqual(_math_text_run("F、F，60∘"), r"F\text{、}F\text{，}60^{\circ}")

    def test_mtef_output_escapes_percent_and_restores_source_cjk_codepoints(self):
        self.assertEqual(_normalise_formula(r"$20{\rm{ % } }$"), (r"$20{\rm{ \% } }$", ""))
        self.assertEqual(_restore_unlisted_cjk(r"$v_{\text{[U+7532]}}$"), (r"$v_{\text{甲}}$", False))
        self.assertEqual(_restore_unlisted_cjk(r"$x_{\text{[U+1F600]}}$"), (r"$x_{\text{[U+1F600]}}$", True))

    def test_docx_preserves_inline_text_formula_image_anchor_and_table(self):
        with tempfile.TemporaryDirectory() as directory:
            docx_path = Path(directory) / "paper.docx"
            make_docx(docx_path)
            store = DocumentStore(Path(directory) / "documents")
            result = convert_docx(docx_path, store, "school-1", "doc-1", "conv-1", "a" * 64)

            self.assertIn("第1题：先看图![插图](asset:", result["markdown"])
            self.assertIn("，再计算", result["markdown"])
            self.assertIn("{v}^{2}", result["markdown"])
            self.assertIn(r"\frac{F}{m}", result["markdown"])
            self.assertIn("| 时间 | 速度 |", result["markdown"])
            self.assertEqual(result["manifest"]["formula_count"], 1)
            self.assertEqual(result["manifest"]["asset_count"], 1)
            self.assertEqual(result["document"]["blocks"][0]["source_locator"]["paragraph_index"], 1)
            self.assertEqual(len(result["document"]["blocks"][0]["issues"]), 1)
            self.assertEqual(result["document"]["blocks"][0]["issues"][0]["severity"], "review")
            self.assertEqual(result["document"]["blocks"][0]["issues"][0]["code"], "formula_requires_review")

    def test_docx_records_markitdown_as_the_text_conversion_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            docx_path = Path(directory) / "paper.docx"
            make_docx(docx_path)
            store = DocumentStore(Path(directory) / "documents")
            with mock.patch(
                "highschoolphysics.document_adapters.docx_native._run_markitdown",
                return_value=("第1题：先看图，再计算", "0.1.6", "used"),
            ) as convert_text:
                result = convert_docx(docx_path, store, "school-1", "doc-markitdown", "conv-markitdown", "c" * 64)

            convert_text.assert_called_once_with(docx_path)
            self.assertEqual(result["adapter_name"], "markitdown+docx-native")
            self.assertEqual(result["manifest"]["markitdown"]["status"], "used")
            self.assertEqual(result["manifest"]["markitdown"]["version"], "0.1.6")

    def test_docx_rejects_unknown_image_relationship_as_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.docx"
            make_docx(path, b"<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'/>")
            store = DocumentStore(Path(directory) / "documents")
            result = convert_docx(path, store, "school-1", "doc-1", "conv-1", "b" * 64)
            self.assertTrue(any(issue["code"] == "image_relationship_missing" for issue in result["manifest"]["issues"]))
            self.assertEqual(result["manifest"]["asset_count"], 0)

    def test_docx_reads_wps_alternate_content_without_leaking_layout_or_empty_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wps-shape.docx"
            make_wps_shape_docx(path)
            store = DocumentStore(Path(directory) / "documents")
            result = convert_docx(path, store, "school-1", "doc-wps", "conv-wps", "b" * 64)

            self.assertEqual(result["markdown"], "1115.")
            self.assertNotIn("-12345", result["markdown"])
            self.assertNotIn("67890", result["markdown"])
            codes = [issue["code"] for issue in result["manifest"]["issues"]]
            self.assertIn("word_shape_requires_visual_review", codes)
            self.assertIn("markitdown_empty_output", codes)
            issue = result["manifest"]["issues"][0]
            self.assertEqual(issue["details"]["shape_id"], "6")
            self.assertEqual(issue["details"]["shape_name"], "矩形 6")
            self.assertEqual(issue["details"]["geometry"], "rect")
            self.assertEqual(issue["details"]["extent_emu"], {"cx": "6330950", "cy": "3599180"})
            self.assertEqual(result["manifest"]["adapter_version"], "1.6.1")

    def test_docx_counts_each_embedded_ole_object_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "embedded.docx"
            make_docx(path, include_ole=True)
            store = DocumentStore(Path(directory) / "documents")
            result = convert_docx(path, store, "school-1", "doc-ole", "conv-ole", "d" * 64)
            self.assertEqual(result["manifest"]["embedded_object_count"], 1)
            issues = result["manifest"]["issues"]
            self.assertEqual(sum(issue["code"] == "embedded_formula_unconverted" for issue in issues), 1)
            self.assertEqual(issues[-1]["severity"], "blocking")

    def test_docx_converts_mathType_ole_formula_and_keeps_preview_for_review(self):
        fixture = Path(__file__).parent / "fixtures" / "mtef" / "parallel.ole"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "embedded.docx"
            make_docx(path, include_ole=True, ole_payload=fixture.read_bytes())
            store = DocumentStore(Path(directory) / "documents")
            result = convert_docx(path, store, "school-1", "doc-ole-readable", "conv-ole-readable", "e" * 64)

            self.assertIn(r"OC\parallel", result["markdown"])
            self.assertEqual(result["manifest"]["embedded_object_count"], 1)
            self.assertEqual(result["manifest"]["embedded_formula_converted_count"], 1)
            self.assertEqual(result["manifest"]["embedded_formula_unresolved_count"], 0)
            issue = next(issue for issue in result["manifest"]["issues"] if issue["code"] == "embedded_formula_requires_review")
            self.assertEqual(issue["severity"], "review")
            self.assertEqual(len(issue["asset_ids"]), 1)
            self.assertEqual(issue["source_locator"]["embedded_object_relationship_id"], "rId3")
            self.assertEqual(issue["source_locator"]["embedded_object_part"], "word/embeddings/oleObject1.bin")

    def test_docx_keeps_corrupt_mtef_object_blocked_with_visible_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrupt.docx"
            make_docx(path, include_ole=True, ole_payload=b"not a compound OLE document")
            store = DocumentStore(Path(directory) / "documents")
            result = convert_docx(path, store, "school-1", "doc-ole-corrupt", "conv-ole-corrupt", "f" * 64)

            self.assertEqual(result["markdown"].count("![插图](asset:"), 1)
            self.assertEqual(result["markdown"].count("![公式预览（待复核）](asset:"), 1)
            self.assertNotIn("[U+", result["markdown"])
            self.assertEqual(result["manifest"]["embedded_formula_converted_count"], 0)
            self.assertEqual(result["manifest"]["embedded_formula_unresolved_count"], 1)
            issue = next(issue for issue in result["manifest"]["issues"] if issue["code"] == "embedded_formula_unconverted")
            self.assertEqual(issue["severity"], "blocking")
            self.assertEqual(issue["details"]["reason"], "mtef_parse_failed")

    def test_docx_converts_mathType_package_relationship(self):
        fixture = Path(__file__).parent / "fixtures" / "mtef" / "parallel.ole"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.docx"
            make_docx(path, include_ole=True, ole_payload=fixture.read_bytes())
            with zipfile.ZipFile(path) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            key = "word/_rels/document.xml.rels"
            entries[key] = entries[key].replace(b"relationships/oleObject", b"relationships/package")
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in entries.items():
                    archive.writestr(name, data)
            result = convert_docx(path, DocumentStore(Path(directory) / "documents"),
                                  "school-1", "doc-package", "conv-package", "a" * 64)
            self.assertIn(r"OC\parallel", result["markdown"])
            self.assertEqual(result["manifest"]["embedded_formula_converted_count"], 1)
            self.assertEqual(result["manifest"]["embedded_formula_unresolved_count"], 0)

    def test_mtef_replacement_character_requires_visible_source_fallback(self):
        self.assertEqual(_normalise_formula("$F^{\ufffd}+mg=ma$"),
                         (None, "formula_contains_replacement_character"))

    def test_legacy_doc_routes_through_pdf_recognition_and_keeps_source_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy.doc"
            source_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy-word-content"
            source.write_bytes(source_bytes)
            rendered_pdf = root / "rendered.pdf"
            rendered_bytes = b"%PDF-1.7\nrendered legacy document"
            rendered_pdf.write_bytes(rendered_bytes)
            source_sha256 = hashlib.sha256(source_bytes).hexdigest()
            rendered_sha256 = hashlib.sha256(rendered_bytes).hexdigest()
            converter = "LibreOffice test build"
            parsed = {"markdown": "editable body", "adapter_version": "3.4.0"}
            cancel_event = object()

            with mock.patch(
                "highschoolphysics.document_adapters.convert_legacy_doc",
                return_value={"pdf_path": rendered_pdf, "sha256": rendered_sha256, "converter": converter},
            ) as render_doc, mock.patch(
                "highschoolphysics.document_adapters.convert_pdf", return_value=parsed
            ) as recognize_pdf:
                result = convert_document(
                    source,
                    "legacy.doc",
                    store=object(),
                    school_id="school-1",
                    document_id="doc-legacy",
                    conversion_id="conv-legacy",
                    work_dir=root / "work",
                    timeout_seconds=37,
                    cancel_event=cancel_event,
                )

            render_doc.assert_called_once_with(
                source, root / "work", timeout_seconds=37, cancel_event=cancel_event
            )
            args, kwargs = recognize_pdf.call_args
            self.assertEqual(args[0], rendered_pdf)
            self.assertEqual(args[5], source_sha256)
            self.assertEqual(args[6], root / "work" / "mineru")
            self.assertEqual(kwargs["timeout_seconds"], 37)
            self.assertIs(kwargs["cancel_event"], cancel_event)
            self.assertEqual(
                kwargs["source_locator"],
                {
                    "kind": "legacy_doc",
                    "source_sha256": source_sha256,
                    "rendered_pdf_sha256": rendered_sha256,
                    "converter": converter,
                },
            )
            self.assertEqual(result["markdown"], "editable body")
            self.assertEqual(result["adapter_name"], "libreoffice+mineru")
            self.assertEqual(result["adapter_version"], converter + "+3.4.0")
            self.assertEqual(result["preview_pdf"], rendered_bytes)
            self.assertEqual(result["preview_converter"], converter)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_sha256)

    def test_mineru_34_schema_adapter_keeps_page_boxes_images_and_formula_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source.write_bytes(b"%PDF-1.7\nsynthetic")
            config = root / "mineru.json"
            config.write_text("{}", encoding="utf-8")
            binary = root / "mineru-fake"
            binary.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
from PIL import Image
args=sys.argv
out=Path(args[args.index("--output")+1]) / "source" / "ocr"
(out/"images").mkdir(parents=True)
Image.new("RGB",(10,10),(0,120,40)).save(out/"images/figure.png")
(out/"source.md").write_text("editable",encoding="utf-8")
middle={"_version_name":"3.4.0","_backend":"pipeline","pdf_info":[{"page_idx":0,"page_size":[100,100],"para_blocks":[{"bbox":[1,1,30,30],"lines":[{"spans":[{"type":"inline_equation","bbox":[4,4,18,18],"content":"\\\\sqrt{x}"}]}]}]}]}
content=[{"type":"text","text":"求 $\\\\sqrt{x}$","page_idx":0,"bbox":[0,0,32,32]},{"type":"text","text":"衰变方程 $A B + \\\\gamma$","page_idx":0,"bbox":[35,0,48,32]},{"type":"image","img_path":"images/figure.png","page_idx":0,"bbox":[50,50,80,80]}]
(out/"source_middle.json").write_text(json.dumps(middle),encoding="utf-8")
(out/"source_content_list.json").write_text(json.dumps(content),encoding="utf-8")
''', encoding="utf-8")
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
            store = DocumentStore(root / "documents")
            with mock.patch.dict(os.environ, {
                "HSP_MINERU_BIN": str(binary),
                "HSP_MINERU_TOOLS_CONFIG_JSON": str(config),
                "HSP_MINERU_METHOD": "ocr",
                "HSP_MINERU_THREADS": "1",
            }):
                result = convert_pdf(source, store, "school-1", "doc-pdf", "conv-pdf", "c" * 64, root / "work")
            self.assertEqual(result["manifest"]["adapter_version"], "3.4.0")
            self.assertEqual(result["manifest"]["page_count"], 1)
            self.assertEqual(result["manifest"]["formula_count"], 1)
            self.assertTrue(any(issue["code"] == "formula_ocr_requires_review" for issue in result["document"]["issues"]))
            review_fields = {
                issue["field"] for issue in result["document"]["issues"]
                if issue["code"] == "formula_ocr_requires_review"
            }
            self.assertEqual(review_fields, {"p1-b1", "p1-b2"})
            self.assertIn("![原卷插图](asset:", result["markdown"])
            self.assertEqual(result["document"]["blocks"][0]["bbox"], [0.0, 0.0, 0.32, 0.32])


if __name__ == "__main__":
    unittest.main()
