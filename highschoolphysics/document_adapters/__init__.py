"""Versioned conversion adapters for supported school document formats."""

from __future__ import annotations

import hashlib
from pathlib import Path

class AdapterError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


from .docx_native import convert_docx
from .mineru_pdf import convert_pdf
from .office_legacy import convert_legacy_doc


def convert_document(
    source_path,
    original_name,
    store,
    school_id,
    document_id,
    conversion_id,
    work_dir,
    timeout_seconds=1200,
    cancel_event=None,
):
    suffix = Path(original_name).suffix.lower()
    source_sha256 = _hash_file(source_path)
    if suffix == ".docx":
        if cancel_event is not None and cancel_event.is_set():
            raise AdapterError("cancelled", "Document conversion was cancelled")
        output = convert_docx(
            source_path,
            store,
            school_id,
            document_id,
            conversion_id,
            source_sha256,
            work_dir=Path(work_dir),
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
        )
        try:
            rendered = convert_legacy_doc(source_path, Path(work_dir) / "word-preview", timeout_seconds=timeout_seconds, cancel_event=cancel_event)
            if rendered["pdf_path"].stat().st_size > 100 * 1024 * 1024:
                raise AdapterError("size_limit", "The Word preview PDF exceeds 100 MiB")
            output["preview_pdf"] = rendered["pdf_path"].read_bytes()
            output["preview_converter"] = rendered["converter"]
        except AdapterError as exc:
            if exc.code == "cancelled":
                raise
            output["manifest"].setdefault("issues", []).append({"code": "word_preview_unavailable", "severity": "review", "field": "source", "message": "Word 原卷网页预览不可用，可下载原件核对"})
            output["preview_pdf"] = None
            output["preview_converter"] = "unavailable"
        return output
    if suffix == ".pdf":
        return convert_pdf(
            source_path,
            store,
            school_id,
            document_id,
            conversion_id,
            source_sha256,
            work_dir,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
        )
    if suffix == ".doc":
        rendered = convert_legacy_doc(source_path, work_dir, timeout_seconds=timeout_seconds, cancel_event=cancel_event)
        output = convert_pdf(
            rendered["pdf_path"],
            store,
            school_id,
            document_id,
            conversion_id,
            source_sha256,
            work_dir / "mineru",
            timeout_seconds=timeout_seconds,
            source_locator={
                "kind": "legacy_doc",
                "source_sha256": source_sha256,
                "rendered_pdf_sha256": rendered["sha256"],
                "converter": rendered["converter"],
            },
            cancel_event=cancel_event,
        )
        output["adapter_name"] = "libreoffice+mineru"
        output["adapter_version"] = rendered["converter"] + "+" + output["adapter_version"]
        output["preview_pdf"] = rendered["pdf_path"].read_bytes()
        output["preview_converter"] = rendered["converter"]
        return output
    raise AdapterError("unsupported_format", "Only .docx, .doc, and .pdf inputs are supported")


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
