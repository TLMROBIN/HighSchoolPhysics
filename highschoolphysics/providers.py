"""Provider operations helpers for production LLM and MinerU integrations."""

import os
from pathlib import Path

from cryptography.fernet import Fernet


_EPHEMERAL_KEY = None


class ProviderSecretStore:
    """Encrypt and decrypt provider secrets with Fernet."""

    def __init__(self, key):
        if isinstance(key, str):
            key = key.encode("utf-8")
        self._fernet = Fernet(key)
        self.key = key.decode("utf-8")

    @staticmethod
    def generate_key():
        return Fernet.generate_key().decode("utf-8")

    @classmethod
    def for_connection(cls, conn):
        env_key = os.environ.get("HIGHSCHOOLPHYSICS_PROVIDER_KEY")
        if env_key:
            return cls(env_key)
        key_file = os.environ.get("HIGHSCHOOLPHYSICS_PROVIDER_KEY_FILE")
        if not key_file:
            key_file = _default_key_file_for_connection(conn)
        if key_file:
            path = Path(key_file)
            if path.exists():
                return cls(path.read_text(encoding="utf-8").strip())
            path.parent.mkdir(parents=True, exist_ok=True)
            key = cls.generate_key()
            path.write_text(key + "\n", encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return cls(key)
        return cls(_ephemeral_key())

    def encrypt(self, secret):
        if not secret:
            return ""
        return self._fernet.encrypt(secret.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext):
        if not ciphertext:
            return ""
        return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def _default_key_file_for_connection(conn):
    try:
        rows = conn.execute("pragma database_list").fetchall()
    except Exception:
        return ""
    for row in rows:
        name = row["name"] if hasattr(row, "keys") else row[1]
        file_name = row["file"] if hasattr(row, "keys") else row[2]
        if name == "main" and file_name and file_name != ":memory:":
            return str(Path(file_name).with_suffix(".provider.key"))
    return ""


def _ephemeral_key():
    global _EPHEMERAL_KEY
    if _EPHEMERAL_KEY is None:
        _EPHEMERAL_KEY = ProviderSecretStore.generate_key()
    return _EPHEMERAL_KEY


def mask_secret(secret):
    if not secret:
        return ""
    suffix = secret[-4:] if len(secret) > 4 else ""
    return "\u2022\u2022\u2022\u2022%s" % suffix


def estimate_cost_cents(
    input_units,
    output_units,
    input_cost_per_1k_cents,
    output_cost_per_1k_cents,
):
    return (
        (float(input_units or 0) / 1000.0) * float(input_cost_per_1k_cents or 0)
        + (float(output_units or 0) / 1000.0) * float(output_cost_per_1k_cents or 0)
    )


def budget_status(
    daily_call_limit,
    monthly_budget_cents,
    current_daily_calls,
    current_monthly_cost_cents,
    estimated_cost_cents,
    per_call_max_cents=0,
):
    if int(daily_call_limit or 0) and int(current_daily_calls or 0) >= int(daily_call_limit):
        return {
            "allowed": False,
            "reason": "daily_call_limit_exceeded",
        }
    if (
        float(per_call_max_cents or 0)
        and float(estimated_cost_cents or 0) > float(per_call_max_cents)
    ):
        return {
            "allowed": False,
            "reason": "per_call_budget_exceeded",
        }
    if (
        float(monthly_budget_cents or 0)
        and float(current_monthly_cost_cents or 0) + float(estimated_cost_cents or 0)
        > float(monthly_budget_cents)
    ):
        return {
            "allowed": False,
            "reason": "monthly_budget_exceeded",
        }
    return {"allowed": True, "reason": "ok"}
