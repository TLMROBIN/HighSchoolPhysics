"""Official MinerU cloud API adapter for whole-file PDF recognition."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import time
from urllib import error, request
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile

from . import AdapterError


API_ORIGIN = "https://mineru.net"
UPLOAD_HOSTS = {"mineru.oss-cn-shanghai.aliyuncs.com"}
RESULT_HOSTS = {"cdn-mineru.openxlab.org.cn"}
MAX_RESULT_ZIP_BYTES = 512 * 1024 * 1024
MAX_RESULT_FILES = 5000
MAX_RESULT_UNPACKED_BYTES = 1024 * 1024 * 1024
POLL_INTERVAL_SECONDS = 3


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_API_OPENER = request.build_opener(_NoRedirect)


def _open_api(req, timeout):
    return _API_OPENER.open(req, timeout=timeout)


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise AdapterError("cancelled", "PDF recognition was cancelled")


def _api_json(url, token, *, method="GET", payload=None, timeout=30):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with _open_api(req, timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise AdapterError("mineru_api_rejected", "MinerU API request failed")
            raw = response.read(4 * 1024 * 1024 + 1)
    except error.HTTPError as exc:
        # Do not include the response body: providers can echo request data.
        raise AdapterError("mineru_api_rejected", "MinerU API rejected the request") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise AdapterError("mineru_api_unavailable", "MinerU API could not be reached") from exc
    if len(raw) > 4 * 1024 * 1024:
        raise AdapterError("invalid_adapter_output", "MinerU API response is too large")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterError("invalid_adapter_output", "MinerU API returned invalid JSON") from exc
    if not isinstance(result, dict) or result.get("code") != 0:
        raise AdapterError("mineru_api_rejected", "MinerU API did not accept the task")
    return result


def _require_https_host(url, allowed_hosts, code="invalid_adapter_output"):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in allowed_hosts or parsed.username or parsed.password:
            raise ValueError("unsafe URL")
    except (TypeError, ValueError) as exc:
        raise AdapterError(code, "MinerU returned an unsupported transfer URL") from exc
    return url


def _put_source(upload_url, source_path, timeout):
    _require_https_host(upload_url, UPLOAD_HOSTS)
    try:
        import requests
    except ImportError as exc:
        raise AdapterError("dependency_missing", "requests is required for MinerU API uploads") from exc
    try:
        # MinerU's signed OSS upload URL requires a bare PUT without a
        # Content-Type header. urllib injects application/x-www-form-urlencoded
        # for byte bodies, which invalidates the signature; requests mirrors
        # the official client example and does not add that header for bytes.
        response = requests.put(
            upload_url,
            data=Path(source_path).read_bytes(),
            timeout=timeout,
            allow_redirects=False,
        )
        if response.status_code not in (200, 201):
            raise AdapterError("mineru_api_upload_failed", "MinerU file upload failed")
    except AdapterError:
        raise
    except (requests.RequestException, TimeoutError, OSError) as exc:
        raise AdapterError("mineru_api_unavailable", "MinerU file upload could not be completed") from exc


def _download_result(url, destination, timeout):
    _require_https_host(url, RESULT_HOSTS)
    req = request.Request(url, headers={"Accept": "application/zip"})
    total = 0
    try:
        with request.urlopen(req, timeout=timeout) as response, Path(destination).open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESULT_ZIP_BYTES:
                    raise AdapterError("invalid_adapter_output", "MinerU result archive is too large")
                output.write(chunk)
    except AdapterError:
        raise
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        raise AdapterError("mineru_api_unavailable", "MinerU result could not be downloaded") from exc
    with Path(destination).open("rb") as archive:
        signature = archive.read(2)
    if total < 4 or signature != b"PK":
        raise AdapterError("invalid_adapter_output", "MinerU did not return a ZIP result")


def _extract_result(zip_path, output_dir):
    root = Path(output_dir).resolve()
    total = 0
    try:
        with ZipFile(zip_path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_RESULT_FILES:
                raise AdapterError("invalid_adapter_output", "MinerU result archive has too many files")
            for entry in entries:
                name = entry.filename
                path = PurePosixPath(name)
                mode = entry.external_attr >> 16
                if path.is_absolute() or ".." in path.parts or "\\" in name or (mode & 0o170000) == 0o120000:
                    raise AdapterError("invalid_adapter_output", "MinerU result archive contains an unsafe path")
                total += entry.file_size
                if total > MAX_RESULT_UNPACKED_BYTES:
                    raise AdapterError("invalid_adapter_output", "MinerU result archive expands beyond the allowed size")
                target = (root / Path(*path.parts)).resolve()
                if target != root and root not in target.parents:
                    raise AdapterError("invalid_adapter_output", "MinerU result archive contains an unsafe path")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(entry) as source, target.open("wb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                os.chmod(target, 0o600)
    except AdapterError:
        raise
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise AdapterError("invalid_adapter_output", "MinerU returned an invalid result archive") from exc

    markdown = _find(root, ("full.md",)) or _first_with_suffix(root, ".md")
    middle = _find(root, ("layout.json", "source_middle.json", "middle.json"))
    content = _find(root, ("source_content_list.json", "content_list.json")) or _first_matching(root, "_content_list.json")
    if markdown is None or middle is None or content is None:
        raise AdapterError("invalid_adapter_output", "MinerU archive is missing Markdown or structured PDF output")
    return markdown, middle, content


def _find(root, names):
    for name in names:
        matches = sorted(root.rglob(name))
        if matches:
            return matches[0]
    return None


def _first_with_suffix(root, suffix):
    matches = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == suffix)
    return matches[0] if matches else None


def _first_matching(root, suffix):
    matches = sorted(path for path in root.rglob("*") if path.is_file() and path.name.endswith(suffix))
    return matches[0] if matches else None


def convert_pdf(source_path, work_dir, config, *, timeout_seconds, cancel_event=None):
    token = config.get("api_token") or config.get("secret")
    if not token:
        raise AdapterError("provider_not_ready", "MinerU API token is not configured")
    model_version = (config.get("model_name") or "vlm").strip().lower()
    if model_version not in ("pipeline", "vlm"):
        raise AdapterError("invalid_configuration", "MinerU model must be pipeline or vlm")
    source_path = Path(source_path)
    work_dir = Path(work_dir)
    data_id = "hsp-" + (config.get("data_id") or "document")
    if len(data_id) > 128:
        data_id = data_id[:128]
    api_base = config.get("api_endpoint") or API_ORIGIN
    parsed = urlsplit(api_base)
    if parsed.scheme != "https" or parsed.hostname != "mineru.net" or parsed.path not in ("", "/", "/api/v4"):
        raise AdapterError("invalid_configuration", "MinerU API endpoint must use the official HTTPS API host")
    base = API_ORIGIN
    deadline = time.monotonic() + max(1, timeout_seconds)
    request_timeout = min(60, max(5, timeout_seconds))

    _check_cancel(cancel_event)
    created = _api_json(
        base + "/api/v4/file-urls/batch",
        token,
        method="POST",
        payload={
            "files": [{"name": source_path.name, "data_id": data_id, "is_ocr": bool(config.get("is_ocr", True))}],
            "model_version": model_version,
            "enable_formula": True,
            "enable_table": True,
            "language": "ch",
        },
        timeout=request_timeout,
    )
    data = created.get("data") or {}
    batch_id = data.get("batch_id")
    urls = data.get("file_urls")
    if not isinstance(batch_id, str) or not batch_id or not isinstance(urls, list) or not urls:
        raise AdapterError("invalid_adapter_output", "MinerU did not return an upload task")
    upload_url = urls[0]
    if isinstance(upload_url, dict):
        upload_url = upload_url.get("url")
    if not isinstance(upload_url, str):
        raise AdapterError("invalid_adapter_output", "MinerU did not return a valid upload URL")
    _check_cancel(cancel_event)
    _put_source(upload_url, source_path, request_timeout)

    result = None
    while time.monotonic() < deadline:
        _check_cancel(cancel_event)
        response = _api_json(
            base + "/api/v4/extract-results/batch/" + batch_id,
            token,
            timeout=request_timeout,
        )
        result_data = response.get("data") or {}
        results = result_data.get("extract_result") or []
        if isinstance(results, dict):
            results = [results]
        result = next((item for item in results if item.get("data_id") == data_id), None)
        if result is None and len(results) == 1:
            result = results[0]
        if result and result.get("state") == "done":
            break
        if result and result.get("state") == "failed":
            raise AdapterError("mineru_api_parse_failed", "MinerU could not parse this document")
        remaining = deadline - time.monotonic()
        if remaining > 0:
            if cancel_event is not None:
                cancel_event.wait(min(POLL_INTERVAL_SECONDS, remaining))
            else:
                time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
    else:
        raise AdapterError("conversion_timeout", "MinerU cloud parsing exceeded the time limit")
    zip_url = result.get("full_zip_url") if result else None
    if not isinstance(zip_url, str) or not zip_url:
        raise AdapterError("invalid_adapter_output", "MinerU completed without a result archive")
    archive_path = work_dir / "mineru-api-result.zip"
    _download_result(zip_url, archive_path, request_timeout)
    result_root = work_dir / "mineru-api-result"
    result_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    markdown, middle, content = _extract_result(archive_path, result_root)
    return {
        "base": middle.parent,
        "markdown_path": markdown,
        "middle_path": middle,
        "content_path": content,
        "version": "mineru-api-%s" % model_version,
        "backend": model_version,
        "batch_id": batch_id,
    }
