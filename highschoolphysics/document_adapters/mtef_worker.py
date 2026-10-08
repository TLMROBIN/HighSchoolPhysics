"""Isolated decoder for editable MathType equations embedded in DOCX OLE streams."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import sys

from ._vendor.mtef_py.mtef import MTEF


MAX_REQUEST_BYTES = 48 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_OBJECTS = 512
MAX_OLE_OBJECT_BYTES = 5 * 1024 * 1024
MAX_FORMULA_CHARS = 100_000


def _limit_process_resources():
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        if hasattr(resource, "RLIMIT_AS"):
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except (ImportError, OSError, ValueError):
        pass


def _normalise_formula(value):
    if not isinstance(value, str) or len(value) > MAX_FORMULA_CHARS:
        return None, "formula_output_invalid"
    latex = value.strip()
    if "\ufffd" in latex:
        return None, "formula_contains_replacement_character"
    if latex.startswith("$$") and latex.endswith("$$") and len(latex) >= 4:
        body = latex[2:-2]
        delimiter = "$$"
    elif latex.startswith("$") and latex.endswith("$") and len(latex) >= 2:
        body = latex[1:-1]
        delimiter = "$"
    else:
        return None, "formula_delimiter_invalid"

    def preserve_percent(text):
        result = []
        backslashes = 0
        for character in text:
            if character == "%" and backslashes % 2 == 0:
                result.append(r"\%")
            else:
                result.append(character)
            backslashes = backslashes + 1 if character == "\\" else 0
        return "".join(result)

    body = preserve_percent(body)
    if any(ord(character) < 0x20 and character not in "\t\n\r" for character in body):
        return None, "formula_contains_control_character"
    return delimiter + body + delimiter, ""


def _restore_unlisted_cjk(latex):
    unresolved = []

    def restore(match):
        codepoint = int(match.group(1), 16)
        if 0x4E00 <= codepoint <= 0x9FFF:
            return chr(codepoint)
        unresolved.append(codepoint)
        return match.group(0)

    result = re.sub(r"\[U\+([0-9A-Fa-f]{4,6})\]", restore, latex)
    return result, bool(unresolved or re.search(r"\[U\+[0-9A-Fa-f]{4,6}\]", result))


def _decode_one(payload):
    if not isinstance(payload, str):
        return {"status": "failed", "code": "ole_payload_invalid"}
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, base64.binascii.Error):
        return {"status": "failed", "code": "ole_payload_invalid"}
    if not raw or len(raw) > MAX_OLE_OBJECT_BYTES:
        return {"status": "failed", "code": "ole_payload_size_invalid"}

    try:
        with open(os.devnull, "w", encoding="utf-8") as sink, contextlib.redirect_stdout(sink):
            equation, error = MTEF.OpenBytes(raw)
            if error or equation is None or not getattr(equation, "Valid", False):
                return {"status": "failed", "code": "mtef_parse_failed"}
            latex = equation.Translate()
    except Exception:
        return {"status": "failed", "code": "mtef_parse_failed"}

    latex, error = _normalise_formula(latex)
    if error:
        return {"status": "failed", "code": error}

    # Unlisted CJK ideographs are source codepoints, not OCR guesses.
    latex, unresolved = _restore_unlisted_cjk(latex)
    if unresolved:
        return {"status": "failed", "code": "formula_has_unmapped_symbol"}
    return {"status": "converted", "latex": latex}


def main():
    _limit_process_resources()
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        return 2
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 2
    if not isinstance(request, dict) or len(request) > MAX_OBJECTS or not all(isinstance(key, str) for key in request):
        return 2
    result = {key: _decode_one(payload) for key, payload in request.items()}
    response = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(response) > MAX_RESPONSE_BYTES:
        return 3
    sys.stdout.buffer.write(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
