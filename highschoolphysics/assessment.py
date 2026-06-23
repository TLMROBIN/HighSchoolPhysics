from .grading import grade_answer


EXPORT_PROFILE_DEFAULTS = {
    "include_answers": False,
    "include_analysis": False,
    "include_error_reasons": True,
    "include_redo_history": True,
    "page_break": "student",
}


def _region_kind(question_type):
    if question_type in ("single_choice", "multiple_choice"):
        return "choice"
    return "text"


def generate_answer_card_template(template_id, title, snapshots):
    regions = []
    for snapshot in sorted(snapshots, key=lambda item: item["position"]):
        regions.append(
            {
                "question_id": snapshot["question_id"],
                "position": snapshot["position"],
                "points": snapshot["points"],
                "kind": _region_kind(snapshot.get("question_type", "")),
                "locator": "第%s题" % snapshot["position"],
            }
        )
    return {
        "id": template_id,
        "name": "%s答题卡" % title,
        "regions": regions,
    }


def normalize_ocr_items(items, confidence_threshold=0.75):
    normalized = []
    for index, item in enumerate(items, start=1):
        confidence = float(item.get("confidence", 1.0) or 0)
        review_status = "not_required"
        review_reason = ""
        if item.get("conflict"):
            review_status = "required"
            review_reason = "conflict"
        elif confidence < confidence_threshold:
            review_status = "required"
            review_reason = "low_confidence"
        normalized.append(
            {
                "item_index": index,
                "student_id": item["student_id"],
                "question_id": item["question_id"],
                "answer": str(item.get("answer", "")),
                "confidence": confidence,
                "review_status": review_status,
                "review_reason": review_reason,
                "raw": dict(item),
            }
        )
    return normalized


def score_redo_attempt(rule, answer):
    graded = grade_answer(rule, answer)
    return {
        "score": graded["score"],
        "max_score": graded["max_score"],
        "status": "done" if graded["correct"] else "reviewed",
        "correct": graded["correct"],
    }


def default_export_options(options):
    merged = dict(EXPORT_PROFILE_DEFAULTS)
    merged.update(options or {})
    merged["include_answers"] = bool(merged.get("include_answers"))
    merged["include_analysis"] = bool(merged.get("include_analysis"))
    merged["include_error_reasons"] = bool(
        merged.get("include_error_reasons")
    )
    merged["include_redo_history"] = bool(merged.get("include_redo_history"))
    if merged.get("page_break") not in ("student", "question", "none"):
        merged["page_break"] = "student"
    return merged
