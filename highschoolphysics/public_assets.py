"""Path-safe reader for intentionally public, packaged web assets."""

import mimetypes
from pathlib import Path


ASSET_DIR = Path(__file__).with_name("assets")


def read_public_asset(relative_path):
    if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
        return None
    candidate = (ASSET_DIR / relative_path).resolve()
    root = ASSET_DIR.resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    content_type = {
        ".woff2": "font/woff2",
        ".woff": "font/woff",
        ".ttf": "font/ttf",
    }.get(candidate.suffix.lower()) or mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
    if content_type in ("text/css", "application/javascript", "text/javascript"):
        content_type += "; charset=utf-8"
    return content_type, candidate.read_bytes()
