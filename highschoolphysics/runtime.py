"""Runtime capability checks for production dependencies."""

from importlib import import_module, metadata
import shutil
import sys


CAPABILITY_IDS = (
    "paddleocr",
    "markitdown",
    "mineru-local",
    "mineru-api",
    "playwright-pdf",
    "oidc-sso",
    "secret-encryption",
)

CAPABILITY_DEFINITIONS = (
    {
        "id": "paddleocr",
        "label": "PaddleOCR 本地识别",
        "module": "paddleocr",
        "package": "paddleocr",
        "minimum_version": "3.0.0",
    },
    {
        "id": "markitdown",
        "label": "MarkItDown 文档解析",
        "module": "markitdown",
        "package": "markitdown",
        "minimum_version": "0.1.0",
        "python_min": (3, 10),
    },
    {
        "id": "mineru-local",
        "label": "MinerU 本地解析",
        "module": "mineru",
        "package": "mineru",
        "executable": "mineru",
        "minimum_version": "2.0.0",
        "python_min": (3, 10),
    },
    {
        "id": "mineru-api",
        "label": "MinerU API",
        "requires_credential": True,
        "enabled": False,
    },
    {
        "id": "playwright-pdf",
        "label": "Playwright PDF",
        "module": "playwright",
        "package": "playwright",
        "minimum_version": "1.40",
    },
    {
        "id": "oidc-sso",
        "label": "OIDC SSO",
        "module": "authlib",
        "package": "Authlib",
        "minimum_version": "1.3",
    },
    {
        "id": "secret-encryption",
        "label": "密钥加密",
        "module": "cryptography.fernet",
        "package": "cryptography",
        "minimum_version": "42",
    },
)

CAPABILITY_STATUSES = (
    "ready",
    "missing_dependency",
    "missing_executable",
    "missing_credential",
    "disabled",
    "degraded",
    "failed",
)


def _package_version(package_name):
    if not package_name:
        return ""
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return ""


def _version_tuple(value):
    parts = []
    for chunk in str(value or "").replace("-", ".").split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        if digits:
            parts.append(int(digits))
    return tuple(parts)


def _version_is_below(current, minimum):
    current_tuple = _version_tuple(current)
    minimum_tuple = _version_tuple(minimum)
    if not current_tuple or not minimum_tuple:
        return False
    length = max(len(current_tuple), len(minimum_tuple))
    current_tuple += (0,) * (length - len(current_tuple))
    minimum_tuple += (0,) * (length - len(minimum_tuple))
    return current_tuple < minimum_tuple


def check_single_capability(definition):
    capability_id = definition["id"]
    label = definition.get("label", capability_id)
    python_min = definition.get("python_min")
    if python_min and sys.version_info[:2] < tuple(python_min):
        return {
            "capability_id": capability_id,
            "label": label,
            "status": "degraded",
            "detail": "%s requires Python %s.%s or newer"
            % (label, python_min[0], python_min[1]),
            "version": "",
        }
    if definition.get("enabled") is False:
        return {
            "capability_id": capability_id,
            "label": label,
            "status": "disabled",
            "detail": "%s is disabled until configured" % label,
            "version": "",
        }
    module_name = definition.get("module")
    package_name = definition.get("package") or module_name
    version = _package_version(package_name)
    if module_name:
        try:
            import_module(module_name)
        except Exception:
            return {
                "capability_id": capability_id,
                "label": label,
                "status": "missing_dependency",
                "detail": "Python package %s is not importable" % module_name,
                "version": version,
            }
    minimum_version = definition.get("minimum_version")
    if minimum_version and _version_is_below(version, minimum_version):
        return {
            "capability_id": capability_id,
            "label": label,
            "status": "degraded",
            "detail": "%s version %s requires >= %s"
            % (label, version or "unknown", minimum_version),
            "version": version,
        }
    executable = definition.get("executable")
    if executable and not shutil.which(executable):
        return {
            "capability_id": capability_id,
            "label": label,
            "status": "missing_executable",
            "detail": "Executable %s is not on PATH" % executable,
            "version": version,
        }
    if definition.get("requires_credential"):
        return {
            "capability_id": capability_id,
            "label": label,
            "status": "missing_credential",
            "detail": "%s requires admin credentials" % label,
            "version": version,
        }
    return {
        "capability_id": capability_id,
        "label": label,
        "status": "ready",
        "detail": "%s dependency is importable" % label,
        "version": version,
    }


def check_runtime_capabilities(definitions=CAPABILITY_DEFINITIONS):
    return [check_single_capability(definition) for definition in definitions]
