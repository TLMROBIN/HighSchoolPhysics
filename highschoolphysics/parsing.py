import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib import request


PARSER_VERSION = "phase2c-v1"
QUESTION_PATTERN = re.compile(r"(?m)^\s*(\d+)[\.、]\s+")
OPTION_PATTERN = re.compile(r"(?m)^\s*([A-D])[\.\、]\s*(.+)$")
ANSWER_PATTERN = re.compile(r"答案[:：]\s*([^\n]+)")


class ParseAdapterError(RuntimeError):
    pass


def _collapse_whitespace(value):
    return re.sub(r"\s+", " ", (value or "").strip())


def _blank_item(index):
    return {
        "item_index": index,
        "page_number": 1,
        "question_number": str(index),
        "stem": "",
        "question_type": "short_answer",
        "options": {},
        "answer": {},
        "analysis": "",
        "answer_area": {},
        "media": [],
        "coordinates": {"page": 1, "bbox": []},
        "confidence": 0.0,
        "parser_name": "deterministic_text",
        "parser_version": PARSER_VERSION,
        "warnings": [],
    }


def _numbered_blocks(source_text):
    matches = list(QUESTION_PATTERN.finditer(source_text or ""))
    for position, match in enumerate(matches):
        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(source_text)
        yield position + 1, match.group(1), source_text[start:end].strip()


def _extract_stem(block):
    lines = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if OPTION_PATTERN.match(stripped) or ANSWER_PATTERN.search(stripped):
            continue
        lines.append(stripped)
    return _collapse_whitespace(" ".join(lines))


def parse_deterministic_text(source_text, parser_version=PARSER_VERSION):
    items = []
    for item_index, question_number, block in _numbered_blocks(source_text):
        item = _blank_item(item_index)
        item["question_number"] = question_number
        item["stem"] = _extract_stem(block)
        item["parser_version"] = parser_version
        item["options"] = {
            option: _collapse_whitespace(text)
            for option, text in OPTION_PATTERN.findall(block)
        }
        answer_match = ANSWER_PATTERN.search(block)
        if answer_match:
            answer = _collapse_whitespace(answer_match.group(1))
            item["answer"] = {"type": "short_answer", "answer": answer}
        else:
            item["warnings"].append("missing_answer")
        if item["options"]:
            item["question_type"] = "single_choice"
            if answer_match:
                item["answer"] = {
                    "type": "single_choice",
                    "answer": _collapse_whitespace(answer_match.group(1)),
                }
            item["answer_area"] = {
                "kind": "choice",
                "locator": "第%s页 第%s题" % (item["page_number"], question_number),
            }
        else:
            item["question_type"] = "short_answer"
            item["answer_area"] = {
                "kind": "text",
                "locator": "第%s页 第%s题" % (item["page_number"], question_number),
            }
        confidence = 0.9
        if "missing_answer" in item["warnings"]:
            confidence -= 0.2
        if len(item["stem"]) < 8:
            confidence -= 0.2
            item["warnings"].append("short_stem")
        item["confidence"] = round(max(0.0, confidence), 2)
        items.append(item)
    return normalize_parser_output(
        {
            "items": items,
            "parser_name": "deterministic_text",
            "parser_version": parser_version,
        }
    )


def normalize_parser_output(raw):
    parser_name = raw.get("parser_name") or "deterministic_text"
    parser_version = raw.get("parser_version") or PARSER_VERSION
    normalized = {
        "parser_name": parser_name,
        "parser_version": parser_version,
        "items": [],
    }
    for index, raw_item in enumerate(raw.get("items", []), start=1):
        item = _blank_item(index)
        item.update(raw_item)
        item["item_index"] = int(item.get("item_index") or index)
        item["page_number"] = item.get("page_number") or 1
        item["question_number"] = str(item.get("question_number") or item["item_index"])
        item["stem"] = _collapse_whitespace(item.get("stem", ""))
        item["question_type"] = item.get("question_type") or "short_answer"
        item["options"] = item.get("options") or {}
        item["answer"] = item.get("answer") or {}
        item["analysis"] = item.get("analysis") or ""
        item["answer_area"] = item.get("answer_area") or {}
        item["media"] = item.get("media") or []
        item["coordinates"] = item.get("coordinates") or {
            "page": item["page_number"],
            "bbox": [],
        }
        item["confidence"] = float(item.get("confidence") or 0.0)
        item["parser_name"] = item.get("parser_name") or parser_name
        item["parser_version"] = item.get("parser_version") or parser_version
        warnings = list(item.get("warnings") or [])
        if item["confidence"] < 0.8 and "low_confidence" not in warnings:
            warnings.append("low_confidence")
        if not item["stem"] and "missing_stem" not in warnings:
            warnings.append("missing_stem")
        item["warnings"] = warnings
        item["review_status"] = (
            "ready" if item["confidence"] >= 0.8 and not warnings else "needs_review"
        )
        normalized["items"].append(item)
    return normalized


def _parsed_text_for_adapter(parser_mode, text, parser_version):
    parsed = parse_deterministic_text(text, parser_version)
    parsed["parser_name"] = parser_mode
    for item in parsed["items"]:
        item["parser_name"] = parser_mode
    return parsed


def _normalize_adapter_result(parser_mode, result, parser_version):
    if isinstance(result, dict):
        raw = dict(result)
        raw["parser_name"] = raw.get("parser_name") or parser_mode
        raw["parser_version"] = raw.get("parser_version") or parser_version
        return normalize_parser_output(raw)
    return _parsed_text_for_adapter(parser_mode, str(result or ""), parser_version)


def _write_temp_source(source_text, config):
    file_name = config.get("file_name") or "source.txt"
    suffix = Path(file_name).suffix or ".txt"
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=suffix,
        delete=False,
    )
    try:
        handle.write(source_text or "")
        return handle.name
    finally:
        handle.close()


def _run_markitdown(source_text, parser_version, config):
    command = config.get("command_path")
    if command:
        if not shutil.which(command):
            raise ParseAdapterError("markitdown adapter command is not available")
        source_path = _write_temp_source(source_text, config)
        try:
            completed = subprocess.run(
                [command, source_path],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        finally:
            Path(source_path).unlink(missing_ok=True)
        if completed.returncode != 0:
            raise ParseAdapterError(
                completed.stderr.strip() or "markitdown adapter failed"
            )
        return completed.stdout
    try:
        from markitdown import MarkItDown
    except Exception:
        command = shutil.which("markitdown")
        if not command:
            raise ParseAdapterError("markitdown adapter command is not available")
        return _run_markitdown(
            source_text,
            parser_version,
            {**config, "command_path": command},
        )
    source_path = _write_temp_source(source_text, config)
    try:
        result = MarkItDown().convert(source_path)
    finally:
        Path(source_path).unlink(missing_ok=True)
    return getattr(result, "text_content", str(result))


def _run_mineru_local(source_text, config):
    command = config.get("command_path") or "mineru"
    if not shutil.which(command):
        raise ParseAdapterError("mineru_local adapter command is not available")
    source_path = _write_temp_source(source_text, config)
    output_dir = tempfile.mkdtemp(prefix="hsp-mineru-")
    try:
        completed = subprocess.run(
            [command, "-p", source_path, "-o", output_dir, "-b", "pipeline"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise ParseAdapterError(
                completed.stderr.strip() or "mineru_local adapter failed"
            )
        output_root = Path(output_dir)
        json_files = sorted(output_root.rglob("*.json"))
        for json_file in json_files:
            try:
                return json.loads(json_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
        markdown_files = sorted(output_root.rglob("*.md"))
        if markdown_files:
            return "\n\n".join(
                path.read_text(encoding="utf-8") for path in markdown_files
            )
        return completed.stdout
    finally:
        Path(source_path).unlink(missing_ok=True)
        shutil.rmtree(output_dir, ignore_errors=True)


def _run_mineru_api(source_text, config):
    endpoint = config.get("api_endpoint")
    if not endpoint:
        raise ParseAdapterError("MinerU API endpoint is required")
    payload = json.dumps(
        {
            "file_name": config.get("file_name") or "source.txt",
            "text": source_text or "",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = config.get("api_token") or config.get("secret")
    if token:
        headers["Authorization"] = "Bearer %s" % token
    api_request = request.Request(endpoint, data=payload, headers=headers)
    with request.urlopen(api_request, timeout=float(config.get("timeout", 30))) as response:
        body = response.read().decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def run_parser(
    parser_mode,
    source_text,
    parser_version=PARSER_VERSION,
    config=None,
    fallback_policy="fail_closed",
    adapter_runner=None,
):
    config = config or {}
    if parser_mode == "deterministic_text":
        return parse_deterministic_text(source_text, parser_version)
    if parser_mode in ("markitdown", "mineru_local", "mineru_api"):
        try:
            if adapter_runner:
                result = adapter_runner(parser_mode, source_text, config)
            elif parser_mode == "markitdown":
                result = _run_markitdown(source_text, parser_version, config)
            elif parser_mode == "mineru_local":
                result = _run_mineru_local(source_text, config)
            else:
                result = _run_mineru_api(source_text, config)
        except ParseAdapterError:
            if fallback_policy == "deterministic_text":
                return parse_deterministic_text(source_text, parser_version)
            raise
        return _normalize_adapter_result(parser_mode, result, parser_version)
    raise ParseAdapterError("Unknown parser mode: %s" % parser_mode)
