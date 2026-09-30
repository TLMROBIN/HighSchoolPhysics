"""Atomic, path-safe storage for uploaded documents and rendered assets."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile


MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_DOCX_ENTRIES = 5000
MAX_DOCX_COMPRESSION_RATIO = 200
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
DOCUMENT_MAGICS = {
    "pdf": (b"%PDF-", "application/pdf"),
    "doc": (bytes.fromhex("D0CF11E0A1B11AE1"), "application/msword"),
    "docx": (b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
}
IMAGE_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class DocumentStoreError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def validate_storage_id(value, label="id"):
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value) or value in (".", ".."):
        raise DocumentStoreError("invalid_path", "Invalid %s" % label)
    return value


def clean_original_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 240:
        raise DocumentStoreError("invalid_filename", "A valid file name is required")
    name = value.replace("\\", "/").split("/")[-1].strip().replace("\x00", "")
    if not name or name in (".", ".."):
        raise DocumentStoreError("invalid_filename", "A valid file name is required")
    suffix = Path(name).suffix.lower()
    if suffix not in (".docx", ".doc", ".pdf"):
        raise DocumentStoreError("unsupported_format", "Only .docx, .doc, and .pdf files are supported")
    return name


def sniff_document(path, original_name, max_bytes=MAX_DOCUMENT_BYTES):
    name = clean_original_name(original_name)
    path = Path(path)
    size = path.stat().st_size
    if size <= 0 or size > max_bytes:
        raise DocumentStoreError("size_limit", "The document must be between 1 byte and 50 MiB")
    suffix = Path(name).suffix.lower()
    magic, mime = DOCUMENT_MAGICS[suffix[1:]]
    with path.open("rb") as stream:
        if stream.read(len(magic)) != magic:
            raise DocumentStoreError("invalid_container", "The file contents do not match its extension")
    if suffix == ".docx":
        _validate_docx_container(path)
    return {"mime_type": mime, "byte_size": size, "extension": suffix[1:]}


def _validate_docx_container(path):
    try:
        with zipfile.ZipFile(path) as archive:
            _validate_docx_archive(archive)
    except DocumentStoreError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise DocumentStoreError("invalid_container", "The Word package is damaged") from exc


class DocumentStore:
    def __init__(self, root=None):
        selected = root or os.environ.get("HSP_DOCUMENT_ROOT")
        self.root = Path(selected or "data/documents").expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, *parts):
        candidate = self.root.joinpath(*parts).resolve()
        if not candidate.is_relative_to(self.root):
            raise DocumentStoreError("invalid_path", "Storage path escaped the document root")
        return candidate

    def atomic_write(self, relative_parts, chunks):
        target = self._path(*relative_parts)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".write-", dir=str(target.parent))
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as stream:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise TypeError("Storage chunks must be bytes")
                    stream.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # link() publishes the fully fsynced temporary file without
                # replacing a concurrently created object at this content key.
                os.link(temporary, target)
            except FileExistsError:
                current = self._existing_file_sha256(target)
                if current != digest.hexdigest():
                    raise DocumentStoreError("storage_conflict", "An object already exists with different bytes")
                os.unlink(temporary)
                return target, digest.hexdigest(), size
            os.unlink(temporary)
            directory_fd = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return target, digest.hexdigest(), size
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _existing_file_sha256(path):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(str(path), flags)
        except OSError as exc:
            raise DocumentStoreError("storage_conflict", "The object path already exists but is not a readable file") from exc
        digest = hashlib.sha256()
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise DocumentStoreError("storage_conflict", "The object path already exists but is not a regular file")
            with os.fdopen(fd, "rb") as stream:
                fd = -1
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        finally:
            if fd >= 0:
                os.close(fd)

    def store_original(self, school_id, document_id, original_name, data, expected_sha256=None):
        school_id = validate_storage_id(school_id, "school id")
        document_id = validate_storage_id(document_id, "document id")
        clean_original_name(original_name)
        if not isinstance(data, bytes) or not data or len(data) > MAX_DOCUMENT_BYTES:
            raise DocumentStoreError("size_limit", "The uploaded document exceeds the configured size limit")
        digest = sha256_bytes(data)
        if expected_sha256 is not None and digest != expected_sha256:
            raise DocumentStoreError("hash_mismatch", "The uploaded document hash does not match")
        info = sniff_document_bytes(data, original_name)
        suffix = Path(original_name).suffix.lower()
        key = "schools/%s/originals/%s/source%s" % (school_id, document_id, suffix)
        path, written_hash, size = self.atomic_write(key.split("/"), [data])
        return {"storage_key": key, "path": path, "sha256": written_hash, "byte_size": size, **info}

    def store_asset(self, school_id, image_bytes, source_locator=None):
        school_id = validate_storage_id(school_id, "school id")
        if not isinstance(image_bytes, bytes) or not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
            raise DocumentStoreError("invalid_asset", "The image is empty or exceeds 25 MiB")
        try:
            from PIL import Image, ImageOps, UnidentifiedImageError

            with Image.open(io.BytesIO(image_bytes)) as opened:
                width, height = opened.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise DocumentStoreError("invalid_asset", "The image dimensions exceed the safe limit")
                image_format = opened.format
                opened.load()
                image = ImageOps.exif_transpose(opened)
                output = io.BytesIO()
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                if image.mode == "RGBA" or image_format == "PNG":
                    image.save(output, format="PNG", optimize=True)
                    mime = "image/png"
                elif image_format == "WEBP":
                    image.save(output, format="WEBP", quality=95, method=4)
                    mime = "image/webp"
                else:
                    image.save(output, format="JPEG", quality=94, optimize=True)
                    mime = "image/jpeg"
                normalized = output.getvalue()
                width, height = image.size
        except DocumentStoreError:
            raise
        except ImportError as exc:
            raise DocumentStoreError("dependency_missing", "Pillow is required to verify image assets") from exc
        except Exception as exc:
            raise DocumentStoreError("invalid_asset", "The extracted image could not be decoded safely") from exc

        digest = sha256_bytes(normalized)
        extension = IMAGE_EXTENSIONS[mime]
        asset_id = "asset_" + hashlib.sha256((school_id + ":" + digest + ":" + mime).encode()).hexdigest()[:32]
        key = "schools/%s/assets/%s/%s%s" % (school_id, digest[:2], digest, extension)
        path, _digest, size = self.atomic_write(key.split("/"), [normalized])
        return {
            "id": asset_id,
            "school_id": school_id,
            "sha256": digest,
            "mime_type": mime,
            "byte_size": size,
            "width_px": width,
            "height_px": height,
            "storage_key": key,
            "path": path,
            "source_locator": source_locator or {},
            "data": normalized,
        }

    def read(self, storage_key, expected_sha256=None, max_bytes=None):
        if not isinstance(storage_key, str) or "\\" in storage_key or "\x00" in storage_key:
            raise DocumentStoreError("invalid_path", "Invalid storage key")
        parts = storage_key.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise DocumentStoreError("invalid_path", "Invalid storage key")
        path = self._path(*parts)
        if not path.is_file():
            raise DocumentStoreError("missing_asset", "The stored file is missing")
        if max_bytes is not None and path.stat().st_size > max_bytes:
            raise DocumentStoreError("size_limit", "The stored file exceeds the read limit")
        data = path.read_bytes()
        digest = sha256_bytes(data)
        if expected_sha256 is not None and digest != expected_sha256:
            raise DocumentStoreError("hash_mismatch", "The stored file hash does not match its manifest")
        return data

    def write_conversion(self, school_id, conversion_id, files):
        school_id = validate_storage_id(school_id, "school id")
        conversion_id = validate_storage_id(conversion_id, "conversion id")
        required = {"document.md", "layout.json", "manifest.json"}
        if not required.issubset(files) or set(files) - (required | {"preview.pdf"}) or any(not isinstance(value, bytes) for value in files.values()):
            raise DocumentStoreError("invalid_conversion", "A conversion must include Markdown, layout, and manifest files")
        base = self._path("schools", school_id, "conversions")
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = base / conversion_id
        if destination.exists():
            for name, content in files.items():
                path = destination / name
                if not path.is_file() or path.read_bytes() != content:
                    raise DocumentStoreError("storage_conflict", "A conversion id already contains different output")
        else:
            temporary = Path(tempfile.mkdtemp(prefix=".conversion-", dir=str(base)))
            try:
                for name, content in files.items():
                    target = temporary / name
                    with target.open("xb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                os.replace(str(temporary), str(destination))
                directory_fd = os.open(str(base), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
        prefix = ["schools", school_id, "conversions", conversion_id]
        return {name: "/".join(prefix + [name]) for name in sorted(files)}

    def create_work_dir(self, task_id, lease_token):
        task_id = validate_storage_id(task_id, "task id")
        lease_token = validate_storage_id(lease_token, "lease token")
        path = self._path("work", task_id, lease_token)
        path.mkdir(parents=True, exist_ok=False, mode=0o700)
        return path

    def remove_work_dir(self, task_id, lease_token):
        path = self._path("work", validate_storage_id(task_id), validate_storage_id(lease_token))
        if path.exists():
            shutil.rmtree(path)


def sniff_document_bytes(data, original_name):
    if not isinstance(data, bytes) or not data or len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentStoreError("size_limit", "The document exceeds the configured size limit")
    suffix = Path(clean_original_name(original_name)).suffix.lower()
    magic, mime = DOCUMENT_MAGICS[suffix[1:]]
    if not data.startswith(magic):
        raise DocumentStoreError("invalid_container", "The file contents do not match its extension")
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                _validate_docx_archive(archive)
        except DocumentStoreError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
            raise DocumentStoreError("invalid_container", "The Word package is damaged") from exc
    return {"mime_type": mime, "byte_size": len(data), "extension": suffix[1:]}


def _validate_docx_archive(archive):
    entries = archive.infolist()
    if not entries or len(entries) > MAX_DOCX_ENTRIES:
        raise DocumentStoreError("invalid_container", "The Word package has an invalid entry count")
    total = 0
    names = set()
    for info in entries:
        name = info.filename.replace("\\", "/")
        parts = name.split("/")
        has_windows_drive = re.match(r"^[A-Za-z]:", name) is not None
        if name.startswith("/") or has_windows_drive or ".." in parts or "." in parts or "\x00" in name:
            raise DocumentStoreError("invalid_container", "The Word package contains an unsafe path")
        if name in names:
            raise DocumentStoreError("invalid_container", "The Word package contains duplicate paths")
        names.add(name)
        if info.flag_bits & 1:
            raise DocumentStoreError("encrypted_document", "Encrypted Word packages are not supported")
        total += info.file_size
        if total > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise DocumentStoreError("invalid_container", "The uncompressed Word package exceeds 250 MiB")
        if info.file_size and info.compress_size == 0:
            raise DocumentStoreError("invalid_container", "The Word package has a malformed compressed entry")
        if info.compress_size and info.file_size / info.compress_size > MAX_DOCX_COMPRESSION_RATIO:
            raise DocumentStoreError("invalid_container", "The Word package compression ratio is unsafe")
    if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
        raise DocumentStoreError("invalid_container", "The Word package is missing document parts")
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise DocumentStoreError("invalid_container", "Macro-enabled Word files are not supported")
