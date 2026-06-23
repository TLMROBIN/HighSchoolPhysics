MASTERY_STATES = ("未练习", "未掌握", "有困难", "不熟练", "已掌握")
MASTERY_TAG_TYPES = ("knowledge", "ability", "literacy")
MASTERY_CSS_CLASSES = {
    "未练习": "mastery-state-unpracticed",
    "未掌握": "mastery-state-not-mastered",
    "有困难": "mastery-state-difficult",
    "不熟练": "mastery-state-rough",
    "已掌握": "mastery-state-mastered",
}


def classify_mastery(eligible_attempts, correct_rate):
    if int(eligible_attempts or 0) <= 0:
        return "未练习"
    rate = float(correct_rate or 0)
    if rate < 0.30:
        return "未掌握"
    if rate < 0.60:
        return "有困难"
    if rate < 0.80:
        return "不熟练"
    return "已掌握"


def normalize_snapshot_tags(tags):
    normalized = []
    seen = set()
    for tag in tags or []:
        if not isinstance(tag, dict):
            continue
        tag_type = tag.get("tag_type")
        tag_id = tag.get("tag_id") or tag.get("id")
        if tag_type not in MASTERY_TAG_TYPES or not tag_id:
            continue
        key = (tag_type, tag_id)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "tag_type": tag_type,
                "tag_id": tag_id,
                "name": tag.get("name") or tag_id,
            }
        )
    return normalized


def blank_answer(answer):
    return not str(answer or "").strip()


def mastery_css_class(state):
    return MASTERY_CSS_CLASSES.get(state, MASTERY_CSS_CLASSES["未练习"])
