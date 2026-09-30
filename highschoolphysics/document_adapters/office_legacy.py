"""Isolated LibreOffice rendering for binary legacy Word documents."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

from . import AdapterError


MAX_VECTOR_IMAGES = 512
MAX_VECTOR_IMAGE_BYTES = 5 * 1024 * 1024
MAX_VECTOR_TOTAL_BYTES = 32 * 1024 * 1024


def rasterize_vector_assets(assets, work_dir, timeout_seconds=300, cancel_event=None):
    """Rasterize embedded WMF/EMF images in one isolated LibreOffice process."""
    if not assets:
        return {}
    binary = os.environ.get("HSP_LIBREOFFICE_BIN") or shutil.which("libreoffice") or shutil.which("soffice")
    if not binary:
        raise AdapterError("dependency_missing", "LibreOffice is required to render embedded vector images")
    if len(assets) > MAX_VECTOR_IMAGES:
        raise AdapterError("invalid_adapter_output", "The document contains too many vector images to render safely")

    normalized = {}
    total_bytes = 0
    for name, payload in assets.items():
        suffix = Path(name).suffix.lower()
        if suffix not in (".wmf", ".emf") or not isinstance(payload, bytes) or not payload:
            raise AdapterError("invalid_adapter_output", "The document contains an invalid vector image")
        if len(payload) > MAX_VECTOR_IMAGE_BYTES:
            raise AdapterError("invalid_adapter_output", "A vector image exceeds the safe size limit")
        total_bytes += len(payload)
        if total_bytes > MAX_VECTOR_TOTAL_BYTES:
            raise AdapterError("invalid_adapter_output", "Vector images exceed the combined size limit")
        digest = hashlib.sha256(payload).hexdigest()
        normalized[name] = (digest, suffix, payload)

    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="vector-", dir=str(work_dir)) as temporary:
        root = Path(temporary)
        input_dir = root / "input"
        output_dir = root / "output"
        profile_dir = root / "profile"
        input_dir.mkdir(mode=0o700)
        output_dir.mkdir(mode=0o700)
        profile_dir.mkdir(mode=0o700)
        input_paths = {}
        for digest, suffix, payload in normalized.values():
            if digest in input_paths:
                continue
            path = input_dir / (digest + suffix)
            path.write_bytes(payload)
            input_paths[digest] = path
        command = [
            binary,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--norestore",
            "-env:UserInstallation=" + profile_dir.as_uri(),
            "--convert-to",
            "png",
            "--outdir",
            str(output_dir),
            *(str(path) for path in input_paths.values()),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise AdapterError("dependency_missing", "LibreOffice could not be started") from exc
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise AdapterError("cancelled", "Vector image conversion was cancelled")
            if time.monotonic() >= deadline:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise AdapterError("conversion_timeout", "Vector image conversion exceeded the time limit")
            time.sleep(0.1)
        successful = {}
        for digest in input_paths:
            output_path = output_dir / (digest + ".png")
            if output_path.is_file() and 0 < output_path.stat().st_size <= 25 * 1024 * 1024:
                successful[digest] = output_path.read_bytes()
        return {
            name: successful.get(digest)
            for name, (digest, _suffix, _payload) in normalized.items()
        }


def convert_legacy_doc(source_path, work_dir, timeout_seconds=1200, cancel_event=None):
    source_path = Path(source_path).resolve()
    work_dir = Path(work_dir).resolve()
    binary = os.environ.get("HSP_LIBREOFFICE_BIN") or shutil.which("libreoffice") or shutil.which("soffice")
    if not binary:
        raise AdapterError("dependency_missing", "LibreOffice is required to convert legacy .doc files")
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir = work_dir / "legacy-render"
    profile_dir = work_dir / "legacy-profile"
    output_dir.mkdir(mode=0o700, exist_ok=True)
    profile_dir.mkdir(mode=0o700, exist_ok=True)
    output_path = output_dir / (source_path.stem + ".pdf")
    command = [
        binary,
        "--headless",
        "--nologo",
        "--nodefault",
        "--nolockcheck",
        "--norestore",
        "-env:UserInstallation=" + profile_dir.as_uri(),
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(source_path),
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
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
                raise AdapterError("cancelled", "Legacy Word conversion was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                raise AdapterError("conversion_timeout", "Legacy Word conversion exceeded the time limit")
            try:
                process.communicate(timeout=min(1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except OSError as exc:
        raise AdapterError("dependency_missing", "LibreOffice could not be started") from exc
    if process.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
        raise AdapterError("conversion_failed", "LibreOffice could not render this legacy Word document")
    version = "unknown"
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, timeout=15, check=False, text=True)
        if result.returncode == 0 and result.stdout.strip():
            version = result.stdout.strip().splitlines()[0][:120]
    except (OSError, subprocess.TimeoutExpired):
        pass
    digest = hashlib.sha256()
    with output_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"pdf_path": output_path, "sha256": digest.hexdigest(), "converter": "LibreOffice/" + version}
