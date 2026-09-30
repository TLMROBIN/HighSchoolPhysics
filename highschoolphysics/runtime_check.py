"""CLI for HighSchoolPhysics runtime readiness checks."""

import argparse
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

from .runtime import CAPABILITY_DEFINITIONS, check_runtime_capabilities


def _database_mineru_api_check(check, db_path):
    """Overlay the machine-level check with this school's persisted API state."""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        return {**check, "status": "failed", "detail": "Configured database is unavailable for provider inspection"}
    uri = "file:%s?mode=ro" % quote(str(path), safe="/")
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            "select 1 from sqlite_master where type='table' and name='provider_configs'"
        ).fetchone()
        if table is None:
            return check
        provider = conn.execute(
            """select provider_name,model_name,api_endpoint,secret_ciphertext,last_test_status
               from provider_configs where provider_kind='mineru_api' and enabled=1
               order by updated_at desc,created_at desc limit 1"""
        ).fetchone()
    except sqlite3.Error:
        return {**check, "status": "failed", "detail": "Saved MinerU API provider state could not be read"}
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass
    if provider is None:
        return check
    if not provider["api_endpoint"] or not provider["secret_ciphertext"]:
        return {
            **check,
            "status": "missing_credential",
            "detail": "MinerU API provider is enabled but endpoint or encrypted credential is missing",
            "version": provider["model_name"] or "",
        }
    test_status = (provider["last_test_status"] or "").lower()
    if test_status == "ready":
        status = "ready"
        detail = "Enabled MinerU API provider %s has a successful saved connection test" % provider["provider_name"]
    elif test_status in ("failed", "error"):
        status = "failed"
        detail = "Enabled MinerU API provider %s has a failed saved connection test" % provider["provider_name"]
    else:
        status = "configured"
        detail = "Enabled MinerU API provider %s has not passed a saved connection test" % provider["provider_name"]
    return {
        **check,
        "status": status,
        "detail": detail,
        "version": provider["model_name"] or "",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--db",
        help="Optional application database whose saved provider configuration should be included",
    )
    parser.add_argument(
        "--capability",
        choices=[item["id"] for item in CAPABILITY_DEFINITIONS],
    )
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    definitions = CAPABILITY_DEFINITIONS
    if args.capability:
        definitions = [
            item for item in definitions if item["id"] == args.capability
        ]
    capabilities = check_runtime_capabilities(definitions)
    if args.db:
        capabilities = [
            _database_mineru_api_check(item, args.db)
            if item["capability_id"] == "mineru-api" else item
            for item in capabilities
        ]
    payload = {
        "capabilities": capabilities,
        "smoke_requested": bool(args.smoke),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in capabilities:
            print(
                "%s\t%s\t%s"
                % (item["capability_id"], item["status"], item["detail"])
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
