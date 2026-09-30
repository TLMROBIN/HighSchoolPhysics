#!/usr/bin/env python3
"""Preview, apply, verify or revoke historical display-only corrections."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from highschoolphysics.content_corrections import (  # noqa: E402
    CorrectionError,
    apply_preview,
    build_preview,
    rollback_migration,
    verify_baseline,
)
from highschoolphysics.db import connect  # noqa: E402


def _write_private(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    os.chmod(target, 0o600)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    preview = sub.add_parser("preview")
    preview.add_argument("--db", required=True)
    preview.add_argument("--mapping", required=True)
    preview.add_argument("--output", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--db", required=True)
    apply.add_argument("--mapping", required=True)
    apply.add_argument("--expected-preview-sha256", required=True)
    apply.add_argument("--actor-id", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--db", required=True)
    verify.add_argument("--mapping", required=True)
    verify.add_argument("--baseline", required=True)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--db", required=True)
    rollback.add_argument("--migration-key", required=True)
    rollback.add_argument("--actor-id", required=True)
    rollback.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        conn = connect(args.db)
        try:
            if args.command == "preview":
                mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
                report = build_preview(conn, mapping)
                _write_private(args.output, report)
                print(json.dumps({"output": str(Path(args.output).resolve()), "preview_sha256": report["preview_sha256"], "errors": report["errors"]}, ensure_ascii=False))
                return 2 if report["errors"] else 0
            if args.command == "apply":
                mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
                result = apply_preview(conn, mapping, args.expected_preview_sha256, args.actor_id)
            elif args.command == "verify":
                mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
                baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
                result = verify_baseline(conn, mapping, baseline)
            else:
                result = rollback_migration(conn, args.migration_key, args.actor_id, args.reason)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result.get("ok", True) else 2
        finally:
            conn.close()
    except (OSError, json.JSONDecodeError, CorrectionError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
