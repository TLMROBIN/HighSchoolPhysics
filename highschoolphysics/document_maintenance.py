"""Conservative, reference-aware cleanup for document storage orphans."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import shutil
import time


DEFAULT_RETENTION_SECONDS = 24 * 60 * 60


def _directories(path):
    try:
        entries = list(path.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    return sorted((entry for entry in entries if not entry.is_symlink() and entry.is_dir()), key=lambda item: item.name)


def _files(path):
    try:
        entries = list(path.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    return sorted((entry for entry in entries if not entry.is_symlink() and entry.is_file()), key=lambda item: item.name)


def _tree_is_old(path, cutoff):
    """Return true only when every regular entry is older than the retention cutoff."""
    try:
        root_info = path.lstat()
    except FileNotFoundError:
        return False
    if path.is_symlink() or root_info.st_mtime > cutoff:
        return False
    if not path.is_dir():
        return path.is_file()
    for current, dirs, files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        try:
            if current_path.lstat().st_mtime > cutoff:
                return False
        except FileNotFoundError:
            return False
        safe_dirs = []
        for name in dirs:
            child = current_path / name
            try:
                info = child.lstat()
            except FileNotFoundError:
                return False
            if child.is_symlink() or info.st_mtime > cutoff:
                return False
            safe_dirs.append(name)
        dirs[:] = safe_dirs
        for name in files:
            child = current_path / name
            try:
                info = child.lstat()
            except FileNotFoundError:
                return False
            if child.is_symlink() or not child.is_file() or info.st_mtime > cutoff:
                return False
    return True


def collect_orphaned_objects(conn, store, retention_seconds=DEFAULT_RETENTION_SECONDS, dry_run=True):
    """Plan or remove old unreferenced files under a migrated document root.

    The database write lock prevents a publication transaction from adding a
    reference between the reference snapshot and removal. Active uploads,
    referenced objects, and directories for running worker tasks are protected. The
    default is a read-only plan; callers must explicitly pass ``dry_run=False``
    to remove files.
    """
    if conn.in_transaction:
        raise ValueError("Orphan collection requires a connection with no open transaction")
    if (
        isinstance(retention_seconds, bool)
        or not isinstance(retention_seconds, (int, float))
        or not math.isfinite(retention_seconds)
        or retention_seconds < 0
    ):
        raise ValueError("retention_seconds must be a non-negative number")

    now = time.time()
    cutoff = now - retention_seconds
    now_text = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    if dry_run:
        conn.execute("begin")
    else:
        # Fence expired uploads durably before deleting their staging trees. If
        # unlink later fails, the upload stays terminal and cleanup can retry.
        conn.execute("begin immediate")
        try:
            conn.execute(
                "update document_uploads set state='expired' where state='uploading' and expires_at<=?",
                (now_text,),
            )
            conn.execute(
                "update document_uploads set state='expired',assembly_token=NULL,assembly_lease_until=NULL where state='assembling' and expires_at<=? and coalesce(assembly_lease_until,'')<=?",
                (now_text, now_text),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        conn.execute("begin immediate")
    try:
        referenced_keys = {
            row[0] for row in conn.execute("select storage_key from document_files")
        }
        referenced_keys.update(row[0] for row in conn.execute("select storage_key from document_upload_parts"))
        conversion_ids = set()
        for row in conn.execute(
            "select id,markdown_key,layout_key,manifest_key from document_conversions"
        ):
            conversion_ids.add(row["id"])
            referenced_keys.update((row["markdown_key"], row["layout_key"], row["manifest_key"]))
        referenced_keys.update(row[0] for row in conn.execute("select storage_key from document_assets"))

        upload_rows = conn.execute(
            "select id,school_id,state,expires_at,assembly_lease_until from document_uploads where state in ('uploading','assembling')"
        ).fetchall()
        active_uploads = set()
        active_upload_rows = []
        for row in upload_rows:
            if row["state"] == "uploading":
                active = row["expires_at"] > now_text
            else:
                active = row["expires_at"] > now_text or (row["assembly_lease_until"] or "") > now_text
            if active:
                active_uploads.add(row["id"])
                active_upload_rows.append(row)
        protected_original_ids = {
            (row["school_id"], "doc-" + hashlib.sha256(row["id"].encode("utf-8")).hexdigest()[:32])
            for row in active_upload_rows
        }
        live_leases = {
            (row["id"], row["lease_token"])
            for row in conn.execute(
                "select id,lease_token from document_parse_tasks where status='running' and lease_token is not null"
            )
        }
        has_live_worker = bool(live_leases)

        root = store.root
        candidates = set()

        # Temp files are never database objects. A recent mtime plus the DB
        # lock makes interrupted writes safe to leave for a later collection.
        for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
            for name in files:
                path = current_path / name
                if name.startswith(".write-") and not has_live_worker and not active_uploads and _tree_is_old(path, cutoff):
                    candidates.add(path)

        staging = root / "staging"
        for upload_dir in _directories(staging):
            if upload_dir.name not in active_uploads and _tree_is_old(upload_dir, cutoff):
                candidates.add(upload_dir)

        schools = root / "schools"
        for school_dir in _directories(schools):
            originals = school_dir / "originals"
            for document_dir in _directories(originals):
                if (school_dir.name, document_dir.name) in protected_original_ids:
                    continue
                for path in _files(document_dir):
                    key = path.relative_to(root).as_posix()
                    if key not in referenced_keys and _tree_is_old(path, cutoff):
                        candidates.add(path)

            conversions = school_dir / "conversions"
            for conversion_dir in _directories(conversions):
                if conversion_dir.name.startswith(".conversion-"):
                    if not has_live_worker and _tree_is_old(conversion_dir, cutoff):
                        candidates.add(conversion_dir)
                elif conversion_dir.name not in conversion_ids and not has_live_worker and _tree_is_old(conversion_dir, cutoff):
                    candidates.add(conversion_dir)

            assets = school_dir / "assets"
            for prefix_dir in _directories(assets):
                for path in _files(prefix_dir):
                    key = path.relative_to(root).as_posix()
                    if key not in referenced_keys and not has_live_worker and _tree_is_old(path, cutoff):
                        candidates.add(path)

        work = root / "work"
        for task_dir in _directories(work):
            for lease_dir in _directories(task_dir):
                if (task_dir.name, lease_dir.name) not in live_leases and _tree_is_old(lease_dir, cutoff):
                    candidates.add(lease_dir)

        # Do not attempt a child unlink after its containing tree is selected.
        ordered = sorted(candidates, key=lambda item: (len(item.parts), str(item)))
        selected = []
        for path in ordered:
            if any(path == parent or parent in path.parents for parent in selected):
                continue
            selected.append(path)

        removed = []
        errors = []
        if dry_run:
            conn.rollback()
        else:
            for path in selected:
                # Recheck immediately before removal; symlink trees are never
                # followed and are intentionally left for manual inspection.
                if not path.is_symlink() and _tree_is_old(path, cutoff):
                    try:
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink()
                        removed.append(path.relative_to(root).as_posix())
                    except OSError as exc:
                        errors.append({
                            "path": path.relative_to(root).as_posix(),
                            "error": type(exc).__name__,
                        })
            conn.commit()
        return {
            "dry_run": bool(dry_run),
            "retention_seconds": retention_seconds,
            "candidates": [path.relative_to(root).as_posix() for path in selected],
            "removed": removed,
            "errors": errors,
        }
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
