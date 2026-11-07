#!/usr/bin/env python3
"""Reconcile completed question-ingestion runs into complete reusable exam papers.

This does not change question content, answers, tags, or ingestion-run state. It
verifies source hashes and completed workflow evidence, then assembles the
ordered question IDs through the same repository domain operation used by the
question-bank UI.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from highschoolphysics import question_bank
from highschoolphysics.db import connect
from highschoolphysics.document_models import canonical_sha256
from highschoolphysics.repository import PhysicsRepository


class ReconcileError(RuntimeError):
    pass


def read_state(root: Path, name: str) -> dict:
    path = root / name / "state.json"
    if not path.is_file():
        raise ReconcileError(f"run_state_missing:{name}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("stage") != "complete":
        raise ReconcileError(f"run_not_complete:{name}:{state.get('stage')}")
    if (path.parent / "STOP.json").exists():
        raise ReconcileError(f"completed_run_has_stop:{name}")
    if not state.get("report", {}).get("main_questions"):
        raise ReconcileError(f"completed_run_report_missing:{name}")
    return state


def published_items(conn, state: dict, run_name: str) -> dict[str, dict]:
    publication = state.get("publication") or {}
    if not publication.get("batch_id"):
        raise ReconcileError(f"publication_missing:{run_name}")
    sample = set(state.get("sample", []))
    if set(state.get("watchdog_content_done", [])) != sample:
        raise ReconcileError(f"content_watchdog_incomplete:{run_name}")
    if set(state.get("watchdog_tags_done", [])) != sample:
        raise ReconcileError(f"tag_watchdog_incomplete:{run_name}")
    mapped = {}
    for item in publication.get("published", []):
        row = conn.execute(
            "select question_number,document_json,disposition from parsed_question_items where id=?",
            (item.get("item_id"),),
        ).fetchone()
        if row is None or row["disposition"] != "active":
            raise ReconcileError(f"published_item_missing:{run_name}:{item.get('item_id')}")
        number = str(row["question_number"])
        if number in mapped:
            raise ReconcileError(f"duplicate_question_number_in_run:{run_name}:{number}")
        document = json.loads(row["document_json"])
        children = document.get("children", [])
        units = children or [document]
        qids = item.get("question_ids", [])
        if len(qids) != len(units) or len(set(qids)) != len(qids):
            raise ReconcileError(f"published_unit_count_mismatch:{run_name}:{number}")
        if any(not unit.get("answer_md", "").strip() or unit.get("answer_state") != "verified" for unit in units):
            raise ReconcileError(f"answer_unverified:{run_name}:{number}")
        if any(issue.get("severity") in ("blocking", "review") and issue.get("state") != "resolved"
               for issue in document.get("issues", [])):
            raise ReconcileError(f"unresolved_content_issue:{run_name}:{number}")
        omissions = [entry for entry in state.get("report", {}).get("omitted_subquestions", [])
                     if str(entry.get("number")) == number]
        omitted_audit = state.get("child_omissions", {}).get(item["item_id"], {})
        for omission in omissions:
            source_child = next((child for child in omitted_audit.get("omitted_children", [])
                                 if child.get("key") == omission.get("key")), {})
            omission["label"] = source_child.get("label", omission.get("key", "小问"))
        for qid in qids:
            current = question_bank.content(conn, qid, state["school_id"])
            if not current or canonical_sha256(current["document"]) != canonical_sha256(document):
                raise ReconcileError(f"published_content_drift:{run_name}:{number}:{qid}")
            if question_bank.tag_revision(conn, qid) < 1:
                raise ReconcileError(f"tags_not_adopted:{run_name}:{number}:{qid}")
        mapped[number] = {
            "item_id": item["item_id"], "question_ids": qids,
            "run": run_name, "document_sha256": canonical_sha256(document),
            "answer_units": len(units), "omitted_subquestions": omissions,
        }
    return mapped


def paper_title(source_title: str, omissions: list[dict]) -> str:
    title = re.sub(r"（部分导入[^）]*）$", "", source_title).strip()
    if omissions:
        missing = "、".join(f"第{o.get('number')}题{o.get('label', o.get('key', '小问'))}待补" for o in omissions)
        return f"{title}（全题号汇总；{missing}）"
    return title + "（全题号汇总）"


def plan_one(root: Path, db_path: Path, conn, repo: PhysicsRepository, entry: dict) -> dict:
    base_name = entry.get("base_run")
    if not isinstance(base_name, str) or not base_name:
        raise ReconcileError("manifest_base_run_required")
    base = read_state(root, base_name)
    if Path(base["db"]).resolve() != db_path.resolve():
        raise ReconcileError(f"database_mismatch:{base_name}")
    actor = repo._require_question_bank_actor(base["actor_id"])
    if actor["school_id"] != base["school_id"]:
        raise ReconcileError(f"actor_school_mismatch:{base_name}")

    expected = [str(value) for value in base["paper"].get("expected_numbers", [])]
    if not expected or len(expected) != len(set(expected)):
        raise ReconcileError(f"invalid_expected_numbers:{base_name}")
    chosen = published_items(conn, base, base_name)
    source_hashes = {role: base["paper"]["files"][role]["sha256"] for role in ("paper", "answers")}
    candidates = []
    for run_name in entry.get("supplement_runs", []):
        state = read_state(root, run_name)
        if Path(state["db"]).resolve() != db_path.resolve():
            raise ReconcileError(f"database_mismatch:{run_name}")
        files = state.get("paper", {}).get("files", {})
        if any(files.get(role, {}).get("sha256") != source_hashes[role] for role in source_hashes):
            raise ReconcileError(f"source_hash_mismatch:{base_name}:{run_name}")
        candidates.append((run_name, state, published_items(conn, state, run_name)))

    for number in expected:
        if number in chosen:
            continue
        matches = [(name, items[number]) for name, _, items in candidates if number in items]
        if len(matches) != 1:
            raise ReconcileError(f"supplement_match_count:{base_name}:{number}:{len(matches)}")
        chosen[number] = matches[0][1]
    extras = sorted(set(chosen) - set(expected))
    missing = [number for number in expected if number not in chosen]
    if missing or extras:
        raise ReconcileError(f"question_number_coverage:{base_name}:missing={missing}:extra={extras}")

    qids = [qid for number in expected for qid in chosen[number]["question_ids"]]
    if not qids or len(qids) != len(set(qids)):
        raise ReconcileError(f"duplicate_or_empty_question_ids:{base_name}")
    omissions = [omission for number in expected for omission in chosen[number]["omitted_subquestions"]]
    title = entry.get("title") or paper_title(base["paper"]["title"], omissions)
    if not isinstance(title, str) or not title.strip() or len(title) > 240:
        raise ReconcileError(f"invalid_completed_paper_title:{base_name}")

    base_title = re.sub(r"（部分导入[^）]*）$", "", base["paper"]["title"]).strip()
    title_prefix = base_title + "（全题号汇总"
    existing = conn.execute("select id,title from papers where school_id=? and (title=? or title like ?) order by id",
                            (base["school_id"], title, title_prefix + "%")).fetchall()
    matches = []
    rename_from = None
    for row in existing:
        actual = [value[0] for value in conn.execute(
            "select question_id from paper_questions where paper_id=? order by position", (row["id"],))]
        if actual == qids:
            matches.append(row["id"])
            if row["title"] != title:
                rename_from = row["title"]
        elif row["title"].startswith(title_prefix):
            raise ReconcileError(f"completed_title_exists_with_different_contents:{row['title']}:{row['id']}")
    if len(matches) > 1:
        raise ReconcileError(f"duplicate_completed_paper_records:{title}")
    return {
        "base_run": base_name, "title": title, "source_title": base["paper"]["title"],
        "source_sha256": source_hashes, "expected_question_numbers": expected,
        "question_map": chosen, "question_ids": qids, "main_question_count": len(expected),
        "answer_unit_count": len(qids), "omitted_subquestions": omissions,
        "existing_paper_id": matches[0] if matches else None,
        "school_id": base["school_id"], "actor_id": base["actor_id"],
        "db": str(db_path), "rename_from": rename_from,
    }


def audit_superseded_runs(root: Path, conn, entries: list[dict]) -> list[dict]:
    """Keep stopped retries visible while proving their question scope was replaced."""
    audited = []
    seen = set()
    for entry in entries:
        name = entry.get("run")
        if not isinstance(name, str) or name in seen:
            raise ReconcileError("invalid_superseded_run_name")
        seen.add(name)
        path = root / name / "state.json"
        if not path.is_file():
            raise ReconcileError(f"superseded_run_state_missing:{name}")
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("stage") == "complete":
            raise ReconcileError(f"cannot_supersede_completed_run:{name}")
        numbers = [str(value) for value in entry.get("question_numbers", [])]
        replacements = entry.get("replacement_runs", [])
        if not numbers or not replacements:
            raise ReconcileError(f"superseded_run_scope_missing:{name}")
        old_files = old.get("paper", {}).get("files", {})
        replacement_items = {}
        for replacement_name in replacements:
            replacement_path = root / replacement_name / "state.json"
            if not replacement_path.is_file():
                raise ReconcileError(f"replacement_run_state_missing:{replacement_name}")
            replacement = read_state(root, replacement_name)
            files = replacement.get("paper", {}).get("files", {})
            for role in ("paper", "answers"):
                if old_files.get(role, {}).get("sha256") != files.get(role, {}).get("sha256"):
                    raise ReconcileError(f"superseded_source_hash_mismatch:{name}:{replacement_name}:{role}")
            replacement_items.update(published_items(conn, replacement, replacement_name))
        missing = [number for number in numbers if number not in replacement_items]
        if missing:
            raise ReconcileError(f"superseded_question_not_replaced:{name}:{missing}")
        stop_path = path.parent / "STOP.json"
        stop_reason = json.loads(stop_path.read_text(encoding="utf-8")).get("reason") if stop_path.exists() else ""
        audited.append({
            "run": name, "stage": old.get("stage"), "stop_reason": stop_reason,
            "question_numbers": numbers, "replacement_runs": replacements,
            "source_sha256": {role: old_files.get(role, {}).get("sha256") for role in ("paper", "answers")},
        })
    return audited


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Explicit base and supplement run map")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--create", action="store_true", help="Assemble verified complete papers")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not isinstance(manifest.get("papers"), list) or not manifest["papers"]:
        raise ReconcileError("invalid_reconciliation_manifest")
    root, db_path = Path(args.state_root).resolve(), Path(args.db).resolve()
    conn = connect(db_path)
    try:
        repo = PhysicsRepository(conn)
        plan = [plan_one(root, db_path, conn, repo, entry) for entry in manifest["papers"]]
        superseded = audit_superseded_runs(root, conn, manifest.get("superseded_runs", []))
        if len({entry["title"] for entry in plan}) != len(plan):
            raise ReconcileError("duplicate_reconciled_titles")
        if args.create:
            for entry in plan:
                if entry["existing_paper_id"]:
                    if entry.get("rename_from"):
                        repo.rename_paper(entry["actor_id"], entry["existing_paper_id"],
                                          entry["title"], entry["rename_from"])
                        entry["renamed_from"] = entry.pop("rename_from")
                    continue
                assembled = repo.assemble_paper(entry["actor_id"], entry["title"],
                    "外部AI Agent按原卷SHA及已验收导入批次汇总",
                    [{"question_id": qid, "points": 0} for qid in entry["question_ids"]])
                entry["created_paper_id"] = assembled["paper"]["id"]
                entry["existing_paper_id"] = entry["created_paper_id"]
        report = {
            "version": 1, "status": "complete" if args.create else "planned",
            "scope": "2026 source exam question-bank papers",
            "papers": [{k: v for k, v in entry.items() if k not in ("actor_id", "db")}
                       for entry in plan],
            "superseded_runs": superseded,
        }
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output.chmod(0o600)
        print(json.dumps({"status": report["status"], "paper_count": len(plan),
                          "main_questions": sum(x["main_question_count"] for x in plan),
                          "answer_units": sum(x["answer_unit_count"] for x in plan),
                          "omitted_subquestions": sum(len(x["omitted_subquestions"]) for x in plan),
                          "superseded_runs": len(superseded),
                          "report": str(output)}, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReconcileError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
