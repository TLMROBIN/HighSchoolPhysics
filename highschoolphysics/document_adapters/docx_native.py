"""OOXML-first DOCX conversion preserving body order, image anchors, and OMML."""

from __future__ import annotations

from pathlib import Path
import posixpath
import re
import base64
import difflib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

from ..document_models import validate_document_ir
from ..document_store import DocumentStoreError, _validate_docx_archive
from . import AdapterError


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
V = "urn:schemas-microsoft-com:vml"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
NS = {"w": W, "r": R, "a": A, "v": V, "m": M, "mc": MC, "wp": WP, "wps": WPS}

MAX_OLE_OBJECTS = 512
MAX_OLE_OBJECT_BYTES = 5 * 1024 * 1024
MAX_OLE_TOTAL_BYTES = 32 * 1024 * 1024
MAX_MTEF_OUTPUT_BYTES = 8 * 1024 * 1024
DOCX_ADAPTER_VERSION = "1.5.0"


def _run_markitdown(source_path):
    """Extract the Word document's Markdown text before OOXML enrichment."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        from markitdown import MarkItDown
    except ImportError:
        return None, "", "missing_dependency"
    try:
        package_version = version("markitdown")
    except PackageNotFoundError:
        package_version = "unknown"
    try:
        converted = MarkItDown().convert(str(source_path))
    except Exception:
        return None, package_version, "conversion_failed"
    markdown = getattr(converted, "text_content", "") or ""
    if not markdown.strip():
        return None, package_version, "empty_output"
    return markdown.replace("\r\n", "\n").replace("\r", "\n"), package_version, "used"


def _visible_text(markdown):
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", markdown or "", flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[`*_>#~]", " ", text)
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[^\w\u3400-\u9fff]+", "", text).casefold()


def _has_native_rich_content(block):
    markdown = block.get("markdown", "")
    return (bool(block.get("asset_ids")) or "asset:" in markdown or "$" in markdown
            or block.get("type") == "table"
            or any("formula" in issue.get("code", "") for issue in block.get("issues", []))
            or bool(re.search(r"\\(?:[A-Za-z]+|[()\[\]])", markdown)))


def _reconcile_markitdown_blocks(native_blocks, markitdown_markdown):
    """Use MarkItDown text as the editable text layer and keep OOXML rich anchors."""
    chunks = [part.strip() for part in re.split(r"\n\s*\n+", markitdown_markdown) if part.strip()]
    output = []
    issues = []
    cursor = 0
    matched = 0
    replaced = 0
    unmatched = 0
    # Compare text with math removed as well: MarkItDown cannot read many
    # MathType objects and may combine several OOXML paragraphs into one chunk.
    native_plain = "\n".join(re.sub(r"\$[^$]*\$", "", block.get("markdown", "")) for block in native_blocks)
    native_joined = _visible_text(native_plain)
    native_full = _visible_text("\n".join(block.get("markdown", "") for block in native_blocks))


    for chunk_index, chunk in enumerate(chunks, 1):
        visible = _visible_text(chunk)
        if len(visible) < 4:
            continue
        best_index = None
        best_ratio = 0.0
        search_end = min(len(native_blocks), cursor + 40)
        for native_index in range(cursor, search_end):
            native_text = _visible_text(native_blocks[native_index].get("markdown", ""))
            if not native_text:
                continue
            ratio = difflib.SequenceMatcher(None, visible, native_text, autojunk=False).ratio()
            if visible in native_text or native_text in visible:
                ratio = max(ratio, min(len(visible), len(native_text)) / max(len(visible), len(native_text)))
            if ratio > best_ratio:
                best_index, best_ratio = native_index, ratio

        if best_index is not None and best_ratio >= 0.72:
            output.extend(dict(block) for block in native_blocks[cursor:best_index])
            block = dict(native_blocks[best_index])
            locator = dict(block.get("source_locator") or {})
            locator["markitdown_block_index"] = chunk_index
            block["source_locator"] = locator
            matched += 1
            same_text = visible == _visible_text(block.get("markdown", ""))
            if not same_text:
                issue = {
                    "code": "markitdown_content_difference", "severity": "review", "field": block["id"],
                    "message": "两种 Word 解析结果内容不一致；已保留原生正文，请核对公式、数值和单位。",
                }
                block["issues"] = list(block.get("issues", [])) + [issue]
                issues.append(issue)
            if same_text and not _has_native_rich_content(block):
                image_free = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", chunk, flags=re.DOTALL).strip()
                if image_free:
                    block["markdown"] = image_free
                    replaced += 1
                elif visible:
                    issue = {
                        "code": "markitdown_image_unmapped",
                        "severity": "review",
                        "field": block["id"],
                        "message": "MarkItDown detected an image that has no matching editable Word image anchor",
                    }
                    block.setdefault("issues", []).append(issue)
                    issues.append(issue)
            output.append(block)
            cursor = best_index + 1
            continue

        # Word list numbering may turn an A option into "1." in Markdown.
        # Only suppress it when its text matches an actual native option.
        numbered = re.match(r"^\s*\d+[.)．]\s+(.+)$", chunk, re.DOTALL)
        native_option_match = numbered and any(
            re.match(r"^\s*[A-H][.．、)]", block.get("markdown", ""))
            and _visible_text(numbered.group(1)) == _visible_text(re.sub(r"^\s*[A-H][.．、)]\s*", "", re.sub(r"\$[^$]*\$", "", block.get("markdown", ""))))
            for block in native_blocks[cursor:search_end]
        )
        if not native_option_match and visible not in native_full and visible not in native_joined:
            clean = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", chunk, flags=re.DOTALL).strip()
            if clean:
                block_id = "md%06d" % chunk_index
                issue = {
                    "code": "markitdown_text_unmapped",
                    "severity": "review",
                    "field": block_id,
                    "message": "MarkItDown extracted Word text that could not be aligned to an OOXML paragraph",
                }
                block_type = "table" if clean.startswith("|") and "|" in clean[1:] else "heading" if clean.startswith("#") else "paragraph"
                output.append({
                    "id": block_id,
                    "type": block_type,
                    "page": None,
                    "column": None,
                    "order": len(output) + 1,
                    "bbox": None,
                    "markdown": clean,
                    "asset_ids": [],
                    "source_locator": {"kind": "word_markitdown", "part": "document.md", "markdown_block_index": chunk_index},
                    "issues": [issue],
                })
                issues.append(issue)
                unmatched += 1

    output.extend(dict(block) for block in native_blocks[cursor:])
    return output, {
        "version": "",
        "text_block_count": len(chunks),
        "matched_block_count": matched,
        "replaced_text_block_count": replaced,
        "unmapped_text_block_count": unmatched,
    }, issues


class DocxAdapterError(AdapterError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _q(namespace, name):
    return "{%s}%s" % (namespace, name)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _attribute(node, name):
    return node.get(_q(W, name)) or node.get(name)


def _xml_part(archive, name):
    try:
        data = archive.read(name)
    except KeyError:
        return None
    if b"<!DOCTYPE" in data[:4096].upper() or b"<!ENTITY" in data[:4096].upper():
        raise DocxAdapterError("invalid_container", "The Word package contains a forbidden XML declaration")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise DocxAdapterError("invalid_container", "The Word package contains invalid XML") from exc


def _image_relationships(archive):
    root = _xml_part(archive, "word/_rels/document.xml.rels")
    relationships = {}
    if root is None:
        return relationships
    for rel in root:
        rel_id = rel.get("Id")
        target = rel.get("Target", "")
        target_mode = rel.get("TargetMode", "Internal")
        rel_type = rel.get("Type", "")
        if not rel_id or target_mode.lower() == "external":
            continue
        if not rel_type.endswith("/image"):
            continue
        normalized = posixpath.normpath(posixpath.join("word", target.replace("\\", "/")))
        if normalized.startswith("../") or normalized.startswith("/"):
            continue
        relationships[rel_id] = normalized
    return relationships


def _ole_relationships(archive):
    root = _xml_part(archive, "word/_rels/document.xml.rels")
    relationships = {}
    if root is None:
        return relationships
    for rel in root:
        rel_id = rel.get("Id")
        target = rel.get("Target", "")
        target_mode = rel.get("TargetMode", "Internal")
        rel_type = rel.get("Type", "")
        if not rel_id or target_mode.lower() == "external" or not rel_type.endswith("/oleObject"):
            continue
        normalized = posixpath.normpath(posixpath.join("word", target.replace("\\", "/")))
        if normalized.startswith("../") or normalized.startswith("/"):
            continue
        relationships[rel_id] = normalized
    return relationships


def _ole_formula_relationship_ids(root):
    result = []
    seen = set()
    for node in root.iter():
        if _local(node.tag) != "OLEObject":
            continue
        rel_id = node.get(_q(R, "id"))
        if rel_id and rel_id not in seen:
            result.append(rel_id)
            seen.add(rel_id)
    return result


def _run_mtef_decoder(payload, timeout_seconds, cancel_event=None):
    command = [sys.executable, "-m", "highschoolphysics.document_adapters.mtef_worker"]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(Path(__file__).resolve().parents[2]),
            start_new_session=True,
        )
    except OSError as exc:
        raise DocxAdapterError("dependency_missing", "The isolated MathType decoder could not be started") from exc

    input_pending = payload
    deadline = time.monotonic() + max(1, min(timeout_seconds, 60))

    def stop_process():
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()

    while True:
        if cancel_event is not None and cancel_event.is_set():
            stop_process()
            raise DocxAdapterError("cancelled", "Word formula conversion was cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stop_process()
            raise DocxAdapterError("conversion_timeout", "MathType formula conversion exceeded the time limit")
        try:
            output, _ = process.communicate(input=input_pending, timeout=min(0.2, remaining))
            break
        except subprocess.TimeoutExpired:
            input_pending = None
    if process.returncode != 0 or len(output) > MAX_MTEF_OUTPUT_BYTES:
        raise DocxAdapterError("invalid_adapter_output", "The MathType decoder returned invalid output")
    try:
        decoded = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DocxAdapterError("invalid_adapter_output", "The MathType decoder returned invalid output") from exc
    if not isinstance(decoded, dict):
        raise DocxAdapterError("invalid_adapter_output", "The MathType decoder returned invalid output")
    return decoded


def _decode_ole_formulas(archive, root, timeout_seconds, cancel_event=None):
    rels = _ole_relationships(archive)
    relation_ids = _ole_formula_relationship_ids(root)
    if len(relation_ids) > MAX_OLE_OBJECTS:
        raise DocxAdapterError("invalid_container", "The Word document contains too many embedded equations")

    inputs = {}
    input_targets = {}
    unresolved = {}
    total_bytes = 0
    for rel_id in relation_ids:
        target = rels.get(rel_id)
        if not target:
            unresolved[rel_id] = {"status": "failed", "code": "ole_relationship_missing"}
            continue
        try:
            ole_data = archive.read(target)
        except KeyError:
            unresolved[rel_id] = {"status": "failed", "code": "ole_payload_missing"}
            continue
        if len(ole_data) > MAX_OLE_OBJECT_BYTES:
            unresolved[rel_id] = {"status": "failed", "code": "ole_payload_too_large"}
            continue
        total_bytes += len(ole_data)
        if total_bytes > MAX_OLE_TOTAL_BYTES:
            unresolved[rel_id] = {"status": "failed", "code": "ole_payload_budget_exceeded"}
            continue
        inputs[rel_id] = base64.b64encode(ole_data).decode("ascii")
        input_targets[rel_id] = target

    if not inputs:
        return unresolved
    payload = json.dumps(inputs, separators=(",", ":")).encode("utf-8")
    if cancel_event is not None and cancel_event.is_set():
        raise DocxAdapterError("cancelled", "Word formula conversion was cancelled")
    decoded = _run_mtef_decoder(payload, timeout_seconds, cancel_event)
    if set(decoded) != set(inputs):
        raise DocxAdapterError("invalid_adapter_output", "The MathType decoder omitted or changed an equation reference")
    for rel_id, target in input_targets.items():
        decoded[rel_id]["source_target"] = target
    unresolved.update(decoded)
    return unresolved


def _numbering_info(archive):
    root = _xml_part(archive, "word/numbering.xml")
    if root is None:
        return {}
    abstract = {}
    for item in root.findall("w:abstractNum", NS):
        abstract_id = _attribute(item, "abstractNumId")
        levels = {}
        for level in item.findall("w:lvl", NS):
            ilvl = int(_attribute(level, "ilvl") or 0)
            num_format = level.find("w:numFmt", NS)
            level_text = level.find("w:lvlText", NS)
            levels[ilvl] = (
                num_format.get(_q(W, "val"), "decimal") if num_format is not None else "decimal",
                level_text.get(_q(W, "val"), "%1.") if level_text is not None else "%1.",
            )
        abstract[abstract_id] = levels
    result = {}
    for item in root.findall("w:num", NS):
        num_id = _attribute(item, "numId")
        abstract_id = item.find("w:abstractNumId", NS)
        if num_id and abstract_id is not None:
            result[num_id] = abstract.get(_attribute(abstract_id, "val"), {})
    return result


def _styles(archive):
    root = _xml_part(archive, "word/styles.xml")
    if root is None:
        return {}
    result = {}
    for style in root.findall("w:style", NS):
        style_id = style.get(_q(W, "styleId"))
        name = style.find("w:name", NS)
        if style_id and name is not None:
            result[style_id] = name.get(_q(W, "val"), "")
    return result


def _number_symbol(number, number_format):
    if number_format in ("bullet", "none"):
        return "•"
    if number_format in ("lowerLetter", "upperLetter"):
        if 1 <= number <= 26:
            value = chr(ord("a") + number - 1)
            return value.upper() if number_format == "upperLetter" else value
        return str(number)
    if number_format in ("lowerRoman", "upperRoman") and 0 < number < 4000:
        pairs = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
        remaining = number
        chars = []
        for value, symbol in pairs:
            while remaining >= value:
                chars.append(symbol)
                remaining -= value
        result = "".join(chars)
        return result.lower() if number_format == "lowerRoman" else result
    return str(number)


def _paragraph_numbering(paragraph, numbering, counters):
    num_pr = paragraph.find("w:pPr/w:numPr", NS)
    if num_pr is None:
        return ""
    num_id_node = num_pr.find("w:numId", NS)
    level_node = num_pr.find("w:ilvl", NS)
    if num_id_node is None:
        return ""
    num_id = _attribute(num_id_node, "val")
    level = int(_attribute(level_node, "val") or 0) if level_node is not None else 0
    num_format, template = numbering.get(num_id, {}).get(level, ("decimal", "%1."))
    state = counters.setdefault(num_id, {})
    state[level] = state.get(level, 0) + 1
    for deeper in [current for current in state if current > level]:
        state[deeper] = 0
    output = template
    for marker in re.findall(r"%(\d+)", template):
        target_level = int(marker) - 1
        number = state.get(target_level, 0) or (state.get(level, 1) if target_level == level else 1)
        target_format = numbering.get(num_id, {}).get(target_level, ("decimal", ""))[0]
        output = output.replace("%%%s" % marker, _number_symbol(number, target_format))
    if output == template and "%" in template:
        return ""
    return output + " "


def _math_text(value):
    return {
        "−": "-",
        "×": r"\times ",
        "÷": r"\div ",
        "≤": r"\le ",
        "≥": r"\ge ",
        "≠": r"\ne ",
        "≈": r"\approx ",
        "∠": r"\angle ",
        "、": r"\text{、}",
        "，": r"\text{，}",
        "∘": r"^{\circ}",
        "°": r"^{\circ}",
        "→": r"\to ",
        "⃗": r"\vec{}",
    }.get(value, value)


def _math_text_run(value):
    return "".join(_math_text(character) for character in value)


def _math_value(node):
    """Convert common OMML structures while flagging unknown structures."""
    supported = True

    def children_text(element, skip_properties=True):
        nonlocal supported
        if element is None:
            return ""
        pieces = []
        for child in list(element):
            if skip_properties and (_local(child.tag).endswith("Pr") or _local(child.tag) in ("ctrlPr", "argPr")):
                continue
            value, ok = render(child)
            pieces.append(value)
            supported = supported and ok
        return "".join(pieces)

    def first_named(element, name):
        return next((child for child in list(element) if _local(child.tag) == name), None)

    def render(element):
        nonlocal supported
        name = _local(element.tag)
        if name in ("oMath", "oMathPara", "r", "e", "num", "den", "sub", "sup", "deg", "fName", "lim", "limLow", "limUpp"):
            return children_text(element), True
        if name in ("t",):
            return _math_text_run("".join(element.itertext())), True
        if name == "f":
            return r"\frac{%s}{%s}" % (children_text(first_named(element, "num")), children_text(first_named(element, "den"))), True
        if name == "sSup":
            return "{%s}^{%s}" % (children_text(first_named(element, "e")), children_text(first_named(element, "sup"))), True
        if name == "sSub":
            return "{%s}_{%s}" % (children_text(first_named(element, "e")), children_text(first_named(element, "sub"))), True
        if name == "sSubSup":
            return "{%s}_{%s}^{%s}" % (
                children_text(first_named(element, "e")),
                children_text(first_named(element, "sub")),
                children_text(first_named(element, "sup")),
            ), True
        if name == "rad":
            body = children_text(first_named(element, "e"))
            degree = children_text(first_named(element, "deg"))
            return (r"\sqrt[%s]{%s}" % (degree, body)) if degree else (r"\sqrt{%s}" % body), True
        if name == "d":
            props = first_named(element, "dPr")
            begin = end = ""
            if props is not None:
                beg = first_named(props, "begChr")
                end_node = first_named(props, "endChr")
                begin = beg.get(_q(M, "val"), "") if beg is not None else ""
                end = end_node.get(_q(M, "val"), "") if end_node is not None else ""
            return r"\left%s %s \right%s" % (begin or ".", children_text(first_named(element, "e")), end or "."), True
        if name == "nary":
            props = first_named(element, "naryPr")
            char = "∑"
            if props is not None:
                chr_node = first_named(props, "chr")
                if chr_node is not None:
                    char = chr_node.get(_q(M, "val"), char)
            operator = {"∑": r"\sum", "∏": r"\prod", "∫": r"\int", "∮": r"\oint"}.get(char)
            if operator is None:
                supported = False
                operator = r"\mathop{%s}" % char
            low = children_text(first_named(element, "sub"))
            high = children_text(first_named(element, "sup"))
            body = children_text(first_named(element, "e"))
            return operator + (("_{%s}" % low) if low else "") + (("^{%s}" % high) if high else "") + " " + body, supported
        if name == "func":
            function = children_text(first_named(element, "fName"))
            arg = children_text(first_named(element, "e"))
            return r"\operatorname{%s}\left(%s\right)" % (function, arg), True
        if name in ("bar", "groupChr"):
            body = children_text(first_named(element, "e"))
            return r"\overline{%s}" % body, True
        if name == "acc":
            props = first_named(element, "accPr")
            char = "̂"
            if props is not None:
                chr_node = first_named(props, "chr")
                if chr_node is not None:
                    char = chr_node.get(_q(M, "val"), char)
            macro = {"̂": r"\hat", "̃": r"\tilde", "̅": r"\bar", "⃗": r"\vec"}.get(char)
            if macro is None:
                supported = False
            body = children_text(first_named(element, "e"))
            if macro is None:
                return r"\overset{%s}{%s}" % (char, body), False
            return macro + "{%s}" % body, supported
        if name in ("eqArr", "m"):  # Matrix-like structures remain editable TeX.
            rows = [children_text(child) for child in list(element) if _local(child.tag) == "e"]
            return r"\begin{aligned}%s\end{aligned}" % r"\\".join(rows), True
        if name in ("box", "borderBox", "phant", "sPre"):
            value = children_text(element)
            return value, True
        if name.endswith("Pr") or name in ("ctrlPr", "argPr"):
            return "", True
        value = children_text(element)
        supported = False
        return value, False

    value, okay = render(node)
    return re.sub(r"\s+", " ", value).strip(), okay and supported


def _paragraph_text(paragraph, rels, archive, store, school_id, block_id, issues, formula_counter, ole_counter, asset_registry, vector_images=None, ole_formulas=None):
    output = []
    vector_image_map = vector_images or {}
    ole_formula_map = ole_formulas or {}

    def walk(node, inside_ole_object=False, evidence_assets=None, containing_shape=None):
        tag = node.tag
        if tag == _q(MC, "AlternateContent"):
            # Markup Compatibility content is an either/or representation. Prefer
            # the WPS DrawingML branch we can inspect, rather than also walking
            # its legacy VML fallback (which can duplicate text or contain an
            # empty <v:imagedata> with no relationship at all).
            choice = next(
                (
                    child for child in list(node)
                    if child.tag == _q(MC, "Choice")
                    and "wps" in child.get("Requires", "").split()
                ),
                None,
            )
            fallback = next((child for child in list(node) if child.tag == _q(MC, "Fallback")), None)
            selected = choice if choice is not None else fallback
            if selected is not None:
                docpr = next((item for item in selected.iter() if item.tag == _q(WP, "docPr")), None)
                shape_info = {
                    "shape_id": docpr.get("id"),
                    "shape_name": docpr.get("name"),
                } if docpr is not None else containing_shape
                for child in list(selected):
                    walk(child, inside_ole_object, evidence_assets, shape_info)
            return
        if tag == _q(WPS, "wsp"):
            # Floating WPS shapes have editable text, image fills, and geometry
            # mixed in one subtree. Only emit actual image references and text
            # box content; never leak position/size metadata into question text.
            props = next((item for item in node.iter() if item.tag == _q(WPS, "cNvPr")), None)
            shape_id = props.get("id") if props is not None else (containing_shape or {}).get("shape_id")
            shape_name = props.get("name") if props is not None else (containing_shape or {}).get("shape_name")
            shape_geom = next((item for item in node.iter() if item.tag == _q(A, "prstGeom")), None)
            xfrm = next((item for item in node.iter() if item.tag == _q(A, "xfrm")), None)
            extent = None
            if xfrm is not None:
                ext = next((item for item in list(xfrm) if item.tag == _q(A, "ext")), None)
                if ext is not None:
                    extent = {key: ext.get(key) for key in ("cx", "cy") if ext.get(key) is not None}
            for image_node in (item for item in node.iter() if item.tag == _q(A, "blip")):
                walk(image_node, inside_ole_object, evidence_assets, containing_shape)
            textbox = next((item for item in node.iter() if item.tag == _q(W, "txbxContent")), None)
            if textbox is not None:
                for child in list(textbox):
                    walk(child, inside_ole_object, evidence_assets, containing_shape)
            issues.append({
                "code": "word_shape_requires_visual_review",
                "severity": "blocking",
                "field": block_id,
                "message": "Word 浮动形状的文字已提取，但绘图几何没有转成题目正文；请对照原卷复核",
                "details": {
                    "shape_id": shape_id,
                    "shape_name": shape_name,
                    "geometry": shape_geom.get("prst") if shape_geom is not None else None,
                    "extent_emu": extent,
                },
            })
            return
        if _local(tag) == "posOffset":
            # wp:posOffset stores a layout coordinate as element text. It is
            # metadata, not printable document copy.
            return
        if tag == _q(W, "object"):
            ole_objects = [item for item in node.iter() if _local(item.tag) == "OLEObject"]
            if ole_objects:
                ole_counter[0] += len(ole_objects)
                object_evidence_assets = []
                for child in list(node):
                    walk(child, True, object_evidence_assets, containing_shape)
                preview_ids = sorted(set(object_evidence_assets))
                for item in ole_objects:
                    rel_id = item.get(_q(R, "id"))
                    decoded = ole_formula_map.get(rel_id, {})
                    formula_counter[0] += 1
                    source_locator = {
                        "kind": "word",
                        "part": "word/document.xml",
                        "embedded_object_relationship_id": rel_id,
                    }
                    if decoded.get("source_target"):
                        source_locator["embedded_object_part"] = decoded["source_target"]
                    if decoded.get("status") == "converted" and isinstance(decoded.get("latex"), str):
                        output.append(decoded["latex"])
                        has_preview = bool(preview_ids)
                        issues.append({
                            "code": "embedded_formula_requires_review",
                            "severity": "review" if has_preview else "blocking",
                            "field": block_id,
                            "asset_ids": preview_ids,
                            "source_locator": source_locator,
                            "message": "MathType 原生公式已转成可编辑 LaTeX；需对照对应预览核对公式内容",
                        })
                    else:
                        issues.append({
                            "code": "embedded_formula_unconverted",
                            "severity": "blocking",
                            "field": block_id,
                            "asset_ids": preview_ids,
                            "source_locator": source_locator,
                            "message": "MathType 公式源数据未能转换；请根据原卷预览手动录入可编辑公式后再发布",
                            "details": {"reason": decoded.get("code", "ole_relationship_missing")},
                        })
                return
        if tag == _q(M, "oMath"):
            latex, supported = _math_value(node)
            formula_counter[0] += 1
            if latex:
                output.append("$%s$" % latex)
            issues.append({
                "code": "formula_requires_review" if supported else "formula_structure_unconverted",
                "severity": "review" if supported else "blocking",
                "field": block_id,
                "message": "原生公式已转为 LaTeX，需对照原件核对" if supported else "公式含未支持的数学结构，需对照原件修正",
            })
            return
        if tag in (_q(A, "blip"), _q(V, "imagedata")):
            rel_id = node.get(_q(R, "embed")) or node.get(_q(R, "id"))
            target = rels.get(rel_id)
            if target is None:
                issues.append({"code": "image_relationship_missing", "severity": "blocking", "field": block_id})
                return
            try:
                image_bytes = vector_image_map.get(target)
                if target not in vector_image_map:
                    image_bytes = archive.read(target)
                if image_bytes is None:
                    raise DocumentStoreError("unsupported_vector", "LibreOffice could not rasterize the embedded vector image")
                asset = store.store_asset(
                    school_id,
                    image_bytes,
                    {"kind": "word", "part": "word/document.xml", "relationship_id": rel_id},
                )
            except (KeyError, DocumentStoreError) as exc:
                issues.append({"code": "image_asset_unavailable", "severity": "blocking", "field": block_id, "message": str(exc)})
                return
            asset_registry[asset["id"]] = asset
            if inside_ole_object:
                if evidence_assets is not None:
                    evidence_assets.append(asset["id"])
            else:
                output.append("![插图](asset:%s)" % asset["id"])
            return
        if tag in (_q(W, "tab"),):
            output.append("\t")
            return
        if tag in (_q(W, "br"), _q(W, "cr")):
            output.append("\n")
            return
        if tag in (_q(W, "t"), _q(M, "t")):
            output.append("".join(node.itertext()))
            return
        if tag in (_q(W, "instrText"), _q(W, "delText"), _q(W, "softHyphen")):
            return
        if tag == _q(W, "noBreakHyphen"):
            output.append("-")
            return
        for child in list(node):
            walk(child, inside_ole_object, evidence_assets, containing_shape)

    for child in list(paragraph):
        if child.tag == _q(W, "pPr"):
            continue
        walk(child)
    return "".join(output)


def _table_markdown(table, rels, archive, store, school_id, issues, assets, block_id, formula_counter, ole_counter, vector_images=None, ole_formulas=None):
    rows = []
    for row in table.findall("w:tr", NS):
        cells = []
        for cell in row.findall("w:tc", NS):
            content = []
            for paragraph in cell.findall("w:p", NS):
                content.append(_paragraph_text(paragraph, rels, archive, store, school_id, block_id, issues, formula_counter, ole_counter, assets, vector_images, ole_formulas))
            text = "<br>".join(part.strip() for part in content if part.strip())
            cells.append(text.replace("|", r"\|").replace("\n", "<br>"))
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    divider = "| " + " | ".join("---" for _ in rows[0].strip("| ").split("|")) + " |"
    return "\n".join((rows[0], divider) + tuple(rows[1:]))


def convert_docx(source_path, store, school_id, document_id, conversion_id, source_sha256, work_dir=None, timeout_seconds=1200, cancel_event=None):
    source_path = Path(source_path)
    if cancel_event is not None and cancel_event.is_set():
        raise DocxAdapterError("cancelled", "Word conversion was cancelled")
    markitdown_markdown = None
    markitdown_version = ""
    markitdown_status = "unavailable"
    try:
        with zipfile.ZipFile(source_path) as archive:
            _validate_docx_archive(archive)
            markitdown_markdown, markitdown_version, markitdown_status = _run_markitdown(source_path)
            root = _xml_part(archive, "word/document.xml")
            if root is None:
                raise DocxAdapterError("invalid_container", "The Word document body is missing")
            body = root.find("w:body", NS)
            if body is None:
                raise DocxAdapterError("invalid_container", "The Word document body is invalid")
            ole_formulas = _decode_ole_formulas(archive, root, timeout_seconds, cancel_event)
            rels = _image_relationships(archive)
            vector_targets = sorted(
                target for target in set(rels.values())
                if Path(target).suffix.lower() in (".wmf", ".emf")
            )
            vector_images = {}
            vector_work = None
            temporary_work = None
            if vector_targets:
                if work_dir is None:
                    temporary_work = tempfile.TemporaryDirectory(prefix="hsp-docx-vector-")
                    vector_work = Path(temporary_work.name)
                else:
                    vector_work = Path(work_dir) / "vector-images"
                try:
                    from .office_legacy import rasterize_vector_assets

                    vector_images = rasterize_vector_assets(
                        {target: archive.read(target) for target in vector_targets},
                        vector_work,
                        timeout_seconds=min(timeout_seconds, 300),
                        cancel_event=cancel_event,
                    )
                except AdapterError as exc:
                    if exc.code == "cancelled":
                        raise
                    vector_images = {target: None for target in vector_targets}
                finally:
                    if temporary_work is not None:
                        temporary_work.cleanup()
            numbering = _numbering_info(archive)
            styles = _styles(archive)
            blocks = []
            all_issues = []
            all_assets = {}
            counters = {}
            formula_counter = [0]
            ole_counter = [0]
            body_order = 0
            paragraph_index = 0
            for child in list(body):
                if child.tag == _q(W, "p"):
                    paragraph_index += 1
                    block_id = "b%06d" % (body_order + 1)
                    paragraph_issues = []
                    text = _paragraph_text(
                        child,
                        rels,
                        archive,
                        store,
                        school_id,
                        block_id,
                        paragraph_issues,
                        formula_counter,
                        ole_counter,
                        all_assets,
                        vector_images,
                        ole_formulas,
                    )
                    num_prefix = _paragraph_numbering(child, numbering, counters)
                    if num_prefix:
                        text = num_prefix + text
                    if not text.strip() and not paragraph_issues:
                        continue
                    body_order += 1
                    pstyle = child.find("w:pPr/w:pStyle", NS)
                    style_id = _attribute(pstyle, "val") if pstyle is not None else ""
                    style_name = styles.get(style_id, "")
                    kind = "formula" if not text.strip() else "heading" if style_name.lower().startswith("heading") or style_name.startswith("标题") else "list_item" if num_prefix else "paragraph"
                    markdown = "## %s" % text if kind == "heading" else "- %s" % text if num_prefix and num_prefix.strip() == "•" else text
                    all_issues.extend(paragraph_issues)
                    asset_ids = sorted(set(re.findall(r"\(asset:([A-Za-z0-9_-]+)\)", markdown)))
                    blocks.append({
                        "id": block_id,
                        "type": kind,
                        "page": None,
                        "column": None,
                        "order": body_order,
                        "bbox": None,
                        "markdown": markdown,
                        "asset_ids": asset_ids,
                        "source_locator": {"kind": "word", "part": "word/document.xml", "paragraph_index": paragraph_index},
                        "issues": paragraph_issues,
                    })
                elif child.tag == _q(W, "tbl"):
                    body_order += 1
                    block_id = "b%06d" % body_order
                    issues = []
                    markdown = _table_markdown(child, rels, archive, store, school_id, issues, all_assets, block_id, formula_counter, ole_counter, vector_images, ole_formulas)
                    if not markdown:
                        continue
                    all_issues.extend(issues)
                    asset_ids = sorted(set(re.findall(r"\(asset:([A-Za-z0-9_-]+)\)", markdown)))
                    blocks.append({
                        "id": block_id,
                        "type": "table",
                        "page": None,
                        "column": None,
                        "order": body_order,
                        "bbox": None,
                        "markdown": markdown,
                        "asset_ids": asset_ids,
                        "source_locator": {"kind": "word", "part": "word/document.xml", "body_child_index": body_order},
                        "issues": issues,
                    })
            assets = [all_assets[key] for key in sorted(all_assets)]
    except DocxAdapterError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise DocxAdapterError("invalid_container", "The Word document could not be read safely") from exc

    markitdown_info = {
        "status": markitdown_status,
        "version": markitdown_version,
        "text_chars": len(markitdown_markdown or ""),
        "text_block_count": 0,
        "matched_block_count": 0,
        "replaced_text_block_count": 0,
        "unmapped_text_block_count": 0,
    }
    if markitdown_markdown is not None:
        blocks, reconciled, markitdown_issues = _reconcile_markitdown_blocks(blocks, markitdown_markdown)
        markitdown_info.update(reconciled)
        markitdown_info["version"] = markitdown_version
        all_issues.extend(markitdown_issues)
    elif markitdown_status in ("missing_dependency", "conversion_failed", "empty_output"):
        issue = {
            "code": "markitdown_%s" % markitdown_status,
            "severity": "review",
            "field": "source",
            "message": "MarkItDown could not produce editable text; the OOXML parser output requires source review",
        }
        all_issues.append(issue)
        markitdown_info["issues"] = [issue]
    document = {
        "schema_version": 1,
        "document_id": document_id,
        "conversion_id": conversion_id,
        "source_sha256": source_sha256,
        "pages": [],
        "blocks": blocks,
        "assets": [{key: asset[key] for key in ("id", "sha256", "mime_type", "width_px", "height_px", "storage_key")} for asset in assets],
        "issues": all_issues,
    }
    validate_document_ir(document, known_asset_ids={asset["id"] for asset in assets})
    markdown = "\n\n".join(block["markdown"] for block in blocks)
    manifest = {
        "schema_version": 1,
        "adapter": "markitdown+docx-native" if markitdown_markdown is not None else "docx-native",
        "adapter_version": DOCX_ADAPTER_VERSION,
        "markitdown": markitdown_info,
        "source_sha256": source_sha256,
        "block_count": len(blocks),
        "formula_count": formula_counter[0],
        "embedded_object_count": ole_counter[0],
        "embedded_formula_converted_count": sum(issue["code"] == "embedded_formula_requires_review" for issue in all_issues),
        "embedded_formula_unresolved_count": sum(issue["code"] == "embedded_formula_unconverted" for issue in all_issues),
        "vector_image_count": len(vector_targets),
        "vector_images_rasterized": sum(value is not None for value in vector_images.values()),
        "vector_images_failed": sum(value is None for value in vector_images.values()),
        "asset_count": len(assets),
        "issues": all_issues,
    }
    return {
        "adapter_name": "markitdown+docx-native" if markitdown_markdown is not None else "docx-native",
        "adapter_version": DOCX_ADAPTER_VERSION,
        "document": document,
        "markdown": markdown,
        "assets": assets,
        "layout": document,
        "manifest": manifest,
    }


def _xml_part(archive, name):
    try:
        data = archive.read(name)
    except KeyError:
        return None
    if b"<!DOCTYPE" in data[:4096].upper() or b"<!ENTITY" in data[:4096].upper():
        raise DocxAdapterError("invalid_container", "The Word package contains a forbidden XML declaration")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise DocxAdapterError("invalid_container", "The Word package contains invalid XML") from exc
