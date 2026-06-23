from decimal import Decimal, InvalidOperation


def _normalize_options(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        pieces = value
    else:
        pieces = str(value).replace("，", ",").replace(";", ",").split(",")
    return sorted([str(piece).strip().upper() for piece in pieces if str(piece).strip()])


def _normalize_answer(value):
    if value is None:
        return ""
    return str(value).strip()


def _as_decimal(value):
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        return None


def grade_answer(rule, response):
    question_type = rule.get("type", "single_choice")
    points = int(rule.get("points", 0))
    answer = rule.get("answer")
    normalized_response = _normalize_answer(response)

    if question_type in ("single_choice", "multiple_choice"):
        expected = _normalize_options(answer)
        actual = _normalize_options(normalized_response)
        correct = bool(expected) and expected == actual
    elif question_type == "fill":
        match = rule.get("match", "exact")
        answers = answer if isinstance(answer, (list, tuple)) else [answer]
        if match == "numeric_tolerance":
            actual_number = _as_decimal(normalized_response)
            tolerance = _as_decimal(rule.get("tolerance", "0"))
            correct = False
            if actual_number is not None and tolerance is not None:
                for expected_answer in answers:
                    expected_number = _as_decimal(expected_answer)
                    if expected_number is not None and abs(actual_number - expected_number) <= tolerance:
                        correct = True
                        break
        else:
            correct = normalized_response in {_normalize_answer(item) for item in answers}
    else:
        correct = False

    return {
        "score": points if correct else 0,
        "max_score": points,
        "correct": correct,
        "status": "correct" if correct else "wrong",
        "review_reasons": [],
    }


def grade_response_set(rules, responses, confidence_threshold=0.75):
    items = {}
    total = 0
    max_total = 0
    review_required = 0

    for question_id, rule in rules.items():
        response_payload = responses.get(question_id, {})
        answer = response_payload.get("answer")
        confidence = float(response_payload.get("confidence", 1.0) or 0)
        graded = grade_answer(rule, answer)
        if confidence < confidence_threshold:
            graded["status"] = "needs_review"
            graded["score"] = 0
            graded["review_reasons"] = ["low_confidence"]
            review_required += 1
        total += graded["score"]
        max_total += graded["max_score"]
        graded["answer"] = answer
        graded["confidence"] = confidence
        items[question_id] = graded

    return {
        "total_score": total,
        "max_score": max_total,
        "review_required": review_required,
        "items": items,
    }
