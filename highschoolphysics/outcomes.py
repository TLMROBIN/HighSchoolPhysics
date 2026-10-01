"""Deterministic, score-free answer decisions."""
import re
from decimal import Decimal, InvalidOperation


VERSION = "outcome-2"


def decide(rule, answer, options=None, verified=True):
    raw = str(answer or "")
    normalized = raw.strip()
    result = dict(outcome="pending", normalized_answer=normalized,
                  reason_code="answer_rule", rule_version=VERSION)
    if not normalized:
        return dict(result, outcome="blank", reason_code="explicit_blank")
    if not verified or rule.get("answer") in (None, "", []):
        return result
    kind = rule.get("type")
    if kind in ("single_choice", "multiple_choice"):
        if not re.fullmatch(r"[A-Fa-f,，、;；\s]+", normalized):
            return dict(result, reason_code="invalid_options")
        actual = set(re.findall(r"[A-F]", normalized.upper()))
        expected = set(re.findall(r"[A-F]", str(rule["answer"]).upper()))
        allowed = set(options) if options else set("ABCDEF")
        if not expected or not expected <= allowed or (kind == "single_choice" and len(expected) != 1):
            return result
        if not actual <= allowed or (kind == "single_choice" and len(actual) != 1):
            return dict(result, reason_code="invalid_options")
        return dict(result, normalized_answer="".join(sorted(actual)),
                    outcome="correct" if actual == expected else "wrong",
                    reason_code="option_set")
    if kind != "fill":
        return result
    if rule.get("match") == "numeric_quantity":
        from .fill_rules import decide_quantity
        return dict(result, **decide_quantity(rule, normalized))
    answers = rule["answer"] if isinstance(rule["answer"], (list, tuple)) else [rule["answer"]]
    if rule.get("match") == "numeric_tolerance":
        try:
            number = Decimal(normalized)
            tolerance = Decimal(str(rule.get("tolerance", 0)))
            if not number.is_finite() or not tolerance.is_finite() or tolerance < 0:
                return result
            for expected in answers:
                target = Decimal(str(expected))
                if target.is_finite() and abs(number - target) <= tolerance:
                    return dict(result, outcome="correct", reason_code="numeric_tolerance")
        except (InvalidOperation, ValueError):
            pass
    elif normalized in {str(item).strip() for item in answers}:
        return dict(result, outcome="correct", reason_code="answer_alias")
    return result
