"""Runtime capability checks for production dependencies."""

from importlib import import_module, metadata
import json
import os
from pathlib import Path
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
        "supported_version": "3.4.0",
        "required_pipeline_models": (
            ("models/Layout/PP-DocLayoutV2", "directory"),
            ("models/MFR/unimernet_hf_small_2503", "directory"),
            ("models/OCR/paddleocr_torch", "directory"),
            ("models/TabRec/SlanetPlus/slanet-plus.onnx", "file"),
            ("models/TabRec/UnetStructure/unet.onnx", "file"),
            ("models/TabCls/paddle_table_cls/PP-LCNet_x1_0_table_cls.onnx", "file"),
            ("models/MFR/pp_formulanet_plus_m", "directory"),
        ),
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
    "configured",
    "missing_dependency",
    "missing_executable",
    "missing_configuration",
    "invalid_configuration",
    "missing_models",
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


def _mineru_local_configuration(definition):
    config_name = (
        os.environ.get("HSP_MINERU_TOOLS_CONFIG_JSON")
        or os.environ.get("MINERU_TOOLS_CONFIG_JSON")
        or "mineru.json"
    )
    config_path = Path(config_name).expanduser()
    if not config_path.is_absolute():
        config_path = Path.home() / config_path
    if not config_path.is_file():
        return {
            "status": "missing_configuration",
            "detail": "MinerU is installed, but its local pipeline model configuration is missing",
        }
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "status": "invalid_configuration",
            "detail": "MinerU local model configuration cannot be read as valid JSON",
        }
    model_dirs = config.get("models-dir") if isinstance(config, dict) else None
    model_root_value = model_dirs.get("pipeline") if isinstance(model_dirs, dict) else None
    if not isinstance(model_root_value, str) or not model_root_value.strip():
        return {
            "status": "missing_configuration",
            "detail": "MinerU configuration does not define a local pipeline model directory",
        }
    model_root = Path(model_root_value).expanduser()
    if not model_root.is_absolute():
        model_root = (Path.cwd() / model_root).resolve()
    if not model_root.is_dir():
        return {
            "status": "missing_models",
            "detail": "MinerU pipeline model directory is not present",
        }
    missing = []
    for relative_path, expected_type in definition.get("required_pipeline_models", ()):
        candidate = model_root / relative_path
        if expected_type == "file" and not candidate.is_file():
            missing.append(relative_path)
        elif expected_type == "directory" and not candidate.is_dir():
            missing.append(relative_path)
    if missing:
        return {
            "status": "missing_models",
            "detail": "MinerU local pipeline is missing %d required model paths" % len(missing),
        }
    return {
        "status": "configured",
        "detail": "MinerU 3.4.0 local pipeline files are configured; this does not prove OCR quality on a real sample",
    }


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
    supported_version = definition.get("supported_version")
    if supported_version and version != supported_version:
        return {
            "capability_id": capability_id,
            "label": label,
            "status": "degraded",
            "detail": "%s version %s is unsupported; this adapter requires %s"
            % (label, version or "unknown", supported_version),
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
    if definition.get("required_pipeline_models"):
        configured = _mineru_local_configuration(definition)
        return {
            "capability_id": capability_id,
            "label": label,
            "status": configured["status"],
            "detail": configured["detail"],
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
