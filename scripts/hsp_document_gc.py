#!/usr/bin/env python3
"""Plan or apply conservative cleanup of unreferenced document objects."""

import argparse
import json
import math
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from highschoolphysics.db import DEFAULT_DB_PATH, connect
from highschoolphysics.document_maintenance import collect_orphaned_objects
from highschoolphysics.document_store import DocumentStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--document-root", type=Path, help="Defaults to <database parent>/documents")
    parser.add_argument("--retention-hours", type=float, default=24)
    parser.add_argument("--apply", action="store_true", help="Remove listed objects and mark expired uploads")
    args = parser.parse_args(argv)

    database = args.database.expanduser().resolve()
    configured_root = os.environ.get("HSP_DOCUMENT_ROOT")
    root = (
        args.document_root.expanduser().resolve()
        if args.document_root
        else Path(configured_root).expanduser().resolve()
        if configured_root
        else database.parent / "documents"
    )
    if not database.is_file():
        parser.error("database does not exist: %s" % database)
    if not root.is_dir():
        parser.error("document root does not exist: %s" % root)
    if not math.isfinite(args.retention_hours) or args.retention_hours < 0:
        parser.error("retention-hours must be a finite non-negative number")

    conn = None
    try:
        if args.apply:
            conn = connect(database)
        else:
            conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
        migration_table = conn.execute(
            "select 1 from sqlite_master where type='table' and name='app_schema_migrations'"
        ).fetchone()
        version = conn.execute(
            "select version from app_schema_migrations where feature='document_ingestion'"
        ).fetchone() if migration_table else None
        if version is None or version[0] < 12:
            parser.error("document-ingestion schema v12 is not installed")
        result = collect_orphaned_objects(
            conn,
            DocumentStore(root),
            retention_seconds=args.retention_hours * 60 * 60,
            dry_run=not args.apply,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except sqlite3.Error as exc:
        print("database error: %s" % exc, file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
