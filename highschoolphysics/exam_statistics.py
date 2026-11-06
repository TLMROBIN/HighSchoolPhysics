"""Deterministic statistics for one exam unit, shared by the UI and review exports."""
from collections import Counter
import math
import re

from .repository import loads


CONFIRMED = {'correct', 'wrong', 'blank'}
REVIEWED = {'not_required', 'resolved', 'confirmed', 'reviewed'}
METRIC_RULES = {
    'correct_rate': '多选题按全对1、非空漏选且无错选0.5、错选及确认空白0计算平均得分率；选项未知、缺失、待复核不计入。其他题为正确人数 / 已确认结果人数。',
    'option_rate': '选择该选项人数 / 已确认且选项可辨识的作答人数（含确认空白）；仅得分和无效选项不计入。',
    'combination_rate': '选择完整组合的人数 / 选项统计分母；多选各选项选择率之和可超过100%。',
    'score_rate': '多选题使用1/0.5/0归一化得分率；其他题使用有效导入得分之和 / 对应满分之和。归一化分数不代表卷面分。',
    'source': '本次考试经复核和更正后的有效首次作答；重做不参与。',
}


def fraction(count, total):
    return count / total if total else None


def is_confirmed(row):
    return row.get('outcome') in CONFIRMED and row.get('review_status', 'reviewed') in REVIEWED


def choice_keys(question):
    options = question.get('options')
    if options is None:
        options = loads(question.get('options_json'), {})
    if isinstance(options, dict):
        return list(options)
    return [item['key'] if isinstance(item, dict) else chr(65 + i) for i, item in enumerate(options or [])]


def choice_answer(question, row):
    """None means unrecognised, while an empty string is a confirmed blank."""
    if not is_confirmed(row):
        return None
    if row['outcome'] == 'blank':
        return ''
    raw = str(row.get('final_answer') or '').strip().upper()
    if not re.fullmatch(r'[A-H,，、;；\s]+', raw):
        return None
    answer = ''.join(sorted(set(re.findall('[A-H]', raw))))
    rule = question.get('grading_rule') or loads(question.get('grading_rule_json'), {})
    allowed = set(choice_keys(question))
    if not answer or (allowed and not set(answer) <= allowed):
        return None
    if rule.get('type') == 'single_choice' and len(answer) != 1:
        return None
    return answer


def multiple_choice_credit(question, row):
    """Normalized credit from confirmed effective answers; None stays unknown."""
    rule = question.get('grading_rule') or loads(question.get('grading_rule_json'), {})
    if rule.get('type') != 'multiple_choice':
        return None
    expected = choice_answer(question, dict(final_answer=rule.get('answer', ''), outcome='correct'))
    actual = choice_answer(question, row)
    if not expected or actual is None:
        return None
    target, selected = set(expected), set(actual)
    return 1.0 if selected == target else .5 if selected and selected < target else 0.0


def choice_result_label(question, row):
    credit = multiple_choice_credit(question, row)
    if credit is None:
        return None
    return {1: '全对（得分率100%）', .5: '漏选半对（得分率50%）', 0: '空白（得分率0%）' if row['outcome'] == 'blank' else '错选（得分率0%）'}[credit]


def question_statistics(question, rows, present_count, choice_rate_policy=None):
    # Defend against accidental join fan-out. Responses are unique per student/unit.
    rows = list({row.get('student_id', 'row-%s' % i): dict(row) for i, row in enumerate(rows)}.values())
    confirmed = [r for r in rows if is_confirmed(r)]
    counts = Counter(r['outcome'] for r in confirmed)
    rule = question.get('grading_rule') or loads(question.get('grading_rule_json'), {})
    choice = rule.get('type') in ('single_choice', 'multiple_choice')
    answers = [choice_answer(question, r) for r in confirmed] if choice else []
    answers = [a for a in answers if a is not None]
    combinations = Counter(a or '空白' for a in answers)
    strict_rate = fraction(counts['correct'], len(confirmed))
    rate = strict_rate
    partial_count = 0
    weighted = None
    credits = [multiple_choice_credit(question, r) for r in confirmed] if rule.get('type') == 'multiple_choice' else []
    credits = [value for value in credits if value is not None]
    if rule.get('type') == 'multiple_choice':
        partial_count = credits.count(.5)
        weighted = fraction(sum(credits), len(credits))
        rate = weighted
    total_score = total_max = score_count = 0
    for row in confirmed:
        score, maximum = row.get('imported_score'), row.get('imported_max_score')
        if (isinstance(score, (int, float)) and not isinstance(score, bool)
                and isinstance(maximum, (int, float)) and not isinstance(maximum, bool)
                and math.isfinite(score) and math.isfinite(maximum) and 0 <= score <= maximum and maximum > 0):
            total_score += score
            total_max += maximum
            score_count += 1
    if rule.get('type') == 'multiple_choice':
        total_score, total_max, score_count = sum(credits), len(credits), len(credits)
    return {
        'present_count': present_count, 'recorded_count': len(rows),
        'confirmed_count': len(confirmed), 'correct_count': counts['correct'],
        'wrong_count': counts['wrong'], 'blank_count': counts['blank'],
        'pending_count': len(rows) - len(confirmed), 'missing_count': max(0, present_count - len(rows)),
        'correct_rate': rate, 'strict_correct_rate': strict_rate,
        'partial_count': partial_count, 'choice_score_rate': weighted,
        'option_denominator': len(answers),
        'option_unknown_count': len(confirmed) - len(answers) if choice else 0,
        'options': [{'key': key, 'count': sum(key in a for a in answers),
                     'rate': fraction(sum(key in a for a in answers), len(answers))} for key in choice_keys(question)] if choice else [],
        'combinations': [{'answer': key, 'count': count, 'rate': fraction(count, len(answers))}
                         for key, count in sorted(combinations.items(), key=lambda x: (-x[1], x[0]))],
        'score_count': score_count, 'score_sum': total_score, 'max_score_sum': total_max,
        'score_rate': fraction(total_score, total_max),
    }
