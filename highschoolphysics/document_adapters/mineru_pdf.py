"""Adapter for the verified MinerU 3.4 pipeline output schema."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

from ..document_models import validate_document_ir
from ..document_store import DocumentStoreError
from . import AdapterError


SUPPORTED_MINERU_VERSION = "3.4.0"
MAX_PDF_PAGES = 100


def _run(command, timeout_seconds, env, cancel_event=None):
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout_seconds
        while True:
            if cancel_event is not None and cancel_event.is_set():
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                raise AdapterError("cancelled", "PDF recognition was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                raise AdapterError("conversion_timeout", "PDF recognition exceeded the 20 minute time limit")
            try:
                output, _ = process.communicate(timeout=min(1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except OSError as exc:
        raise AdapterError("dependency_missing", "MinerU could not be started") from exc
    return process.returncode, output


def _select_method(source_path):
    override = os.environ.get("HSP_MINERU_METHOD", "").strip().lower()
    if override in ("auto", "ocr"):
        return override
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return "ocr"
    try:
        result = subprocess.run(
            [pdftotext, "-f", "1", "-l", "3", "-layout", str(source_path), "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "ocr"
    return "auto" if result.returncode == 0 and len(result.stdout.decode("utf-8", "ignore").strip()) >= 40 else "ocr"


def _safe_bbox(raw, width, height):
    if not isinstance(raw, (list, tuple)) or len(raw) != 4 or width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = raw
    try:
        box = [float(x0) / width, float(y0) / height, float(x1) / width, float(y1) / height]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    box = [max(0.0, min(1.0, value)) for value in box]
    if box[0] > box[2] or box[1] > box[3]:
        return None
    return box


def _page_formula_index(middle):
    result = {}
    for page in middle.get("pdf_info", []):
        page_index = page.get("page_idx")
        formulas = []
        for block in page.get("para_blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("type") == "inline_equation":
                        bbox = span.get("bbox") or block.get("bbox")
                        if isinstance(bbox, list) and len(bbox) == 4:
                            formulas.append([float(v) for v in bbox])
        result[page_index] = formulas
    return result


def _has_formula_for_block(item, formulas):
    bbox = item.get("bbox")
    if not isinstance(bbox, list):
        return False
    try:
        target = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return False
    return any(
        min(target[2], source[2]) >= max(target[0], source[0])
        and min(target[3], source[3]) >= max(target[1], source[1])
        for source in formulas.get(item.get("page_idx"), [])
    )


def _contains_math_markup(markdown):
    """Catch editable OCR equations even when MinerU's two bbox streams miss."""
    if not isinstance(markdown, str):
        return False
    return bool(re.search(r"(?<!\\)(?:\$|\\\(|\\\[|\\begin\{(?:equation|align|gather)\})", markdown))


def _mineru_config(env):
    config = env.get("HSP_MINERU_TOOLS_CONFIG_JSON") or env.get("MINERU_TOOLS_CONFIG_JSON")
    if not config or not Path(config).is_file():
        raise AdapterError("model_unavailable", "MinerU requires a readable local model configuration")
    return str(Path(config).resolve())


def convert_pdf(
    source_path,
    store,
    school_id,
    document_id,
    conversion_id,
    source_sha256,
    work_dir,
    timeout_seconds=1200,
    source_locator=None,
    cancel_event=None,
):
    source_path = Path(source_path).resolve()
    work_dir = Path(work_dir).resolve()
    binary = os.environ.get("HSP_MINERU_BIN") or shutil.which("mineru")
    if not binary:
        raise AdapterError("dependency_missing", "MinerU is required for PDF conversion")
    env = os.environ.copy()
    env["MINERU_MODEL_SOURCE"] = env.get("MINERU_MODEL_SOURCE", "local")
    env["MINERU_TOOLS_CONFIG_JSON"] = _mineru_config(env)
    threads = env.get("HSP_MINERU_THREADS", "1")
    if not threads.isdigit() or not (1 <= int(threads) <= 4):
        raise AdapterError("invalid_configuration", "HSP_MINERU_THREADS must be between 1 and 4")
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "MINERU_API_MAX_CONCURRENT_REQUESTS", "MINERU_PROCESSING_WINDOW_SIZE"):
        env[key] = "2" if key == "MINERU_PROCESSING_WINDOW_SIZE" else threads
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_root = work_dir / "mineru-output"
    output_root.mkdir(mode=0o700, exist_ok=True)
    input_path = work_dir / "source.pdf"
    if input_path != source_path:
        shutil.copyfile(source_path, input_path)
    method = _select_method(input_path)
    code, log = _run(
        [binary, "--path", str(input_path), "--output", str(output_root), "--backend", "pipeline", "--method", method, "--lang", "ch"],
        timeout_seconds,
        env,
        cancel_event=cancel_event,
    )
    base = output_root / input_path.stem / "ocr"
    markdown_path = base / (input_path.stem + ".md")
    middle_path = base / (input_path.stem + "_middle.json")
    content_path = base / (input_path.stem + "_content_list.json")
    if code != 0 or not (markdown_path.is_file() and middle_path.is_file() and content_path.is_file()):
        raise AdapterError("invalid_adapter_output", "MinerU did not produce the verified 3.4 output files")
    try:
        middle = json.loads(middle_path.read_text(encoding="utf-8"))
        content = json.loads(content_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterError("invalid_adapter_output", "MinerU returned malformed output") from exc
    if middle.get("_version_name") != SUPPORTED_MINERU_VERSION or middle.get("_backend") != "pipeline":
        raise AdapterError("invalid_adapter_output", "The installed MinerU version or backend differs from the verified adapter")
    pages_in = middle.get("pdf_info")
    if not isinstance(pages_in, list) or not pages_in or len(pages_in) > MAX_PDF_PAGES:
        raise AdapterError("invalid_adapter_output", "MinerU returned an invalid page list")
    page_sizes = {}
    for page in pages_in:
        width, height = page.get("page_size", (0, 0))
        if not isinstance(width, (int, float)) or not isinstance(height, (int, float)) or width <= 0 or height <= 0:
            raise AdapterError("invalid_adapter_output", "MinerU returned invalid page dimensions")
        page_sizes[page.get("page_idx")] = (float(width), float(height))
    if not isinstance(content, list):
        raise AdapterError("invalid_adapter_output", "MinerU content list is not an array")

    assets = {}
    issues = []
    blocks = []
    formulas = _page_formula_index(middle)
    for index, item in enumerate(content, 1):
        page_idx = item.get("page_idx")
        if page_idx not in page_sizes:
            raise AdapterError("invalid_adapter_output", "MinerU returned a block for an unknown PDF page")
        width, height = page_sizes[page_idx]
        kind = item.get("type")
        block_issues = []
        if kind == "image":
            img_path = item.get("img_path")
            if not isinstance(img_path, str) or not img_path:
                raise AdapterError("invalid_adapter_output", "MinerU image block has no asset path")
            image_path = (base / img_path).resolve()
            if not image_path.is_relative_to(base.resolve()) or not image_path.is_file():
                raise AdapterError("unresolved_assets", "MinerU referenced an image outside its output package")
            try:
                asset = store.store_asset(school_id, image_path.read_bytes(), {"kind": "pdf", "page": page_idx + 1, "bbox": item.get("bbox"), "asset_role": "figure"})
            except DocumentStoreError as exc:
                block_issues.append({"code": "image_asset_unavailable", "severity": "blocking", "field": "p%d-b%d" % (page_idx + 1, index), "message": str(exc)})
                issues.extend(block_issues)
                markdown = ""
                asset_id = None
            else:
                assets[asset["id"]] = asset
                asset_id = asset["id"]
                markdown = "![原卷插图](asset:%s)" % asset_id
            block_type = "figure"
            asset_ids = [asset_id] if asset_id else []
        else:
            markdown = item.get("text", "") if isinstance(item.get("text", ""), str) else ""
            if kind in ("footer", "page_number"):
                block_type = "footer"
            elif item.get("text_level"):
                block_type = "heading"
            else:
                block_type = "paragraph"
            asset_ids = []
            if _has_formula_for_block(item, formulas) or _contains_math_markup(markdown):
                block_issues.append({
                    "code": "formula_ocr_requires_review",
                    "severity": "review",
                    "field": "p%d-b%d" % (page_idx + 1, index),
                    "message": "OCR 公式必须与原卷逐式核对",
                })
                issues.extend(block_issues)
        markdown = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not markdown and block_type != "figure":
            continue
        page_number = page_idx + 1
        locator = {"kind": "pdf", "page": page_number, "pdf_page": page_number, "content_list_index": index}
        if source_locator:
            locator.update(source_locator)
        block_id = "p%d-b%04d" % (page_number, index)
        blocks.append({
            "id": block_id,
            "type": block_type,
            "page": page_number,
            "column": None,
            "order": len(blocks) + 1,
            "bbox": _safe_bbox(item.get("bbox"), width, height),
            "markdown": markdown,
            "asset_ids": asset_ids,
            "source_locator": locator,
            "issues": block_issues,
        })

    output_markdown = "\n\n".join(block["markdown"] for block in blocks if block["markdown"])
    if not output_markdown.strip() or not blocks:
        raise AdapterError("empty_document", "MinerU did not recognize editable text or figure blocks")
    represented_pages = {block["page"] for block in blocks}
    missing_pages = sorted(set(range(1, len(pages_in) + 1)) - represented_pages)
    if missing_pages:
        raise AdapterError("invalid_adapter_output", "MinerU did not return content blocks for every PDF page")
    page_models = [
        {
            "page": page["page_idx"] + 1,
            "width": page_sizes[page["page_idx"]][0],
            "height": page_sizes[page["page_idx"]][1],
            "rotation_applied": 0,
        }
        for page in pages_in
    ]
    document = {
        "schema_version": 1,
        "document_id": document_id,
        "conversion_id": conversion_id,
        "source_sha256": source_sha256,
        "pages": page_models,
        "blocks": blocks,
        "assets": [{key: asset[key] for key in ("id", "sha256", "mime_type", "width_px", "height_px", "storage_key")} for asset in assets.values()],
        "issues": issues,
    }
    validate_document_ir(document, known_asset_ids=set(assets))
    formula_count = sum(len(value) for value in formulas.values())
    manifest = {
        "schema_version": 1,
        "adapter": "mineru-pipeline",
        "adapter_version": middle["_version_name"],
        "backend": middle["_backend"],
        "method": method,
        "source_sha256": source_sha256,
        "rendered_source_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "page_count": len(pages_in),
        "block_count": len(blocks),
        "formula_count": formula_count,
        "asset_count": len(assets),
        "issues": issues,
    }
    return {
        "adapter_name": "mineru-pipeline",
        "adapter_version": middle["_version_name"],
        "document": document,
        "markdown": output_markdown,
        "assets": list(assets.values()),
        "layout": document,
        "manifest": manifest,
    }
