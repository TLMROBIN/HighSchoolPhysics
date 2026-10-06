"""Extract explicit choice keys without treating reasoning letters as answers."""
import copy
import re

CHOICE_TYPES = {"single_choice", "multiple_choice"}
LETTERS = r"([A-F](?:[\s,，、;；]*[A-F])*)"


def extract_choice_answer(markdown, options, kind):
    text = re.sub(r"[*`$]", "", markdown or "").strip()
    candidates = []
    first = text.splitlines()[0] if text else ""
    first = re.sub(r"^(?:【答案】|\[答案\]|(?:参考|正确|标准)?答案\s*[:：]?)\s*", "", first).strip()
    if re.fullmatch(LETTERS + r"[。.]?", first, re.I):
        candidates.append(first.rstrip("。."))
    candidates += re.findall(r"(?:故选|因此选|正确答案(?:为|是)|答案为)\s*[:：]?\s*" + LETTERS + r"(?=[。．.!！\s]|$)", text, re.I)
    keys = {"".join(sorted(set(re.findall(r"[A-F]", value.upper())))) for value in candidates}
    allowed = {o["key"] for o in options}
    if len(keys) != 1:
        return None
    answer = keys.pop()
    if not answer or not set(answer) <= allowed or (kind == "single_choice" and len(answer) != 1):
        return None
    return answer


def prepare_answers(document, reviewed=False):
    """Freeze unambiguous reviewed keys; keep missing/conflicting content pending."""
    doc = copy.deepcopy(document)
    for part in [doc, *doc.get("children", [])]:
        if part.get("kind") not in CHOICE_TYPES:
            continue
        answer = extract_choice_answer(part.get("answer_md", ""), part.get("options", []), part["kind"])
        if not answer:
            part["grading_rule"] = None
            part["answer_state"] = "needs_review" if part.get("answer_md") else "missing"
            continue
        part["grading_rule"] = {"type": part["kind"], "answer": answer, "match": "exact"}
        if reviewed or part.get("answer_state") == "verified":
            part["answer_state"] = "verified"
        raw = part.get("answer_md", "")
        if "\n" in raw:
            remainder = raw.split("\n", 1)[1].strip()
            part["answer_md"] = answer
            if remainder and remainder not in part.get("analysis_md", ""):
                part["analysis_md"] = remainder + ("\n\n" + part["analysis_md"] if part.get("analysis_md") else "")
    return doc


def row_answer(row):
    if row["kind"] in CHOICE_TYPES:
        return row.get("grading_rule") or {}
    return row.get("grading_rule") or row.get("answer_md", "") or {}
