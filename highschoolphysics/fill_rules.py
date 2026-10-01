"""Opt-in numeric fill rules; exact rational unit factors, no expression evaluation."""
import re
import unicodedata
from decimal import Decimal
from fractions import Fraction


# Deliberately finite catalog. Case is meaningful (mA is not MA).
UNITS = {
    '': ('number', Fraction(1)),
    'm': ('length', Fraction(1)), 'cm': ('length', Fraction(1, 100)),
    'mm': ('length', Fraction(1, 1000)), 'km': ('length', Fraction(1000)),
    's': ('time', Fraction(1)), 'ms': ('time', Fraction(1, 1000)),
    'min': ('time', Fraction(60)), 'h': ('time', Fraction(3600)),
    'kg': ('mass', Fraction(1)), 'g': ('mass', Fraction(1, 1000)),
    'm/s': ('speed', Fraction(1)), 'km/h': ('speed', Fraction(5, 18)),
    'cm/s': ('speed', Fraction(1, 100)), 'm/s^2': ('acceleration', Fraction(1)),
    'N': ('force', Fraction(1)), 'mN': ('force', Fraction(1, 1000)),
    'J': ('energy', Fraction(1)), 'kJ': ('energy', Fraction(1000)),
    'W': ('power', Fraction(1)), 'kW': ('power', Fraction(1000)),
    'A': ('current', Fraction(1)), 'mA': ('current', Fraction(1, 1000)),
    'V': ('voltage', Fraction(1)), 'mV': ('voltage', Fraction(1, 1000)),
    'Ω': ('resistance', Fraction(1)), 'kΩ': ('resistance', Fraction(1000)),
    'Hz': ('frequency', Fraction(1)), 'kHz': ('frequency', Fraction(1000)),
    'Pa': ('pressure', Fraction(1)), 'kPa': ('pressure', Fraction(1000)),
}
DECIMAL = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?'
NUMBER = rf'(?:{DECIMAL})(?:\s*/\s*(?:{DECIMAL}))?'
QUANTITY = re.compile(rf'^({NUMBER})\s*([^\d\s].*)?$')


def normalize(text):
    # Preserve case, signs and all unrecognized symbols.
    return unicodedata.normalize('NFKC', str(text)).replace('−', '-').strip()


def unit_name(text):
    unit = normalize(text).replace(' ', '')
    return {'m/s2': 'm/s^2', 'm/s²': 'm/s^2'}.get(unit, unit)


def number(text):
    text = normalize(text)
    if len(text) > 120 or not re.fullmatch(NUMBER, text):
        raise ValueError('只支持有限数值、小数、e 科学计数或数值分数')
    values = []
    for part in text.split('/'):
        value = Decimal(part.strip())
        if not value.is_finite() or abs(value.adjusted()) > 100:
            raise ValueError('数值超出支持范围')
        values.append(Fraction(value))
    if len(values) == 2 and values[1] == 0:
        raise ValueError('分母不能为零')
    return values[0] if len(values) == 1 else values[0] / values[1]


def quantity(text):
    text = normalize(text)
    if len(text) > 256:
        raise ValueError('作答过长')
    match = QUANTITY.fullmatch(text)
    if not match:
        raise ValueError('数值格式待确认')
    return number(match[1]), unit_name(match[2] or ''), match[1]


def significant_figures(text):
    if '/' in text:
        return None
    mantissa = text.lower().split('e')[0].lstrip('+-')
    digits = mantissa.replace('.', '').lstrip('0')
    if not digits:
        return None
    # Trailing zeros in an integer without a decimal point are ambiguous.
    if '.' not in mantissa and digits.endswith('0'):
        return None
    return len(digits)


def validate(rule):
    canonical = rule.get('unit', '')
    if canonical not in UNITS:
        raise ValueError('请选择支持的标准单位')
    if not isinstance(rule.get('allow_unit_conversion', False), bool) or not isinstance(rule.get('unit_required', False), bool):
        raise ValueError('单位规则需为明确开关')
    if not canonical and rule.get('unit_required'):
        raise ValueError('纯数值题不能要求单位')
    absolute = number(rule.get('absolute_tolerance', '0'))
    relative = number(rule.get('relative_tolerance', '0'))
    if absolute < 0 or not 0 <= relative <= 1:
        raise ValueError('绝对容差不能为负；相对容差需在 0—1 之间')
    figures = rule.get('significant_figures')
    if figures is not None and (type(figures) is not int or not 1 <= figures <= 12):
        raise ValueError('有效数字需为 1—12 的整数')
    answers = rule['answer'] if isinstance(rule['answer'], list) else [rule['answer']]
    if not answers or len(answers) > 20:
        raise ValueError('标准答案需为 1—20 项')
    targets = []
    for answer in answers:
        value, unit, _ = quantity(answer)
        unit = unit or canonical
        if unit not in UNITS or UNITS[unit][0] != UNITS[canonical][0]:
            raise ValueError('标准答案单位与所选量纲不一致')
        if unit != canonical and not rule.get('allow_unit_conversion'):
            raise ValueError('标准答案需使用标准单位，或允许单位换算')
        targets.append(value * UNITS[unit][1] / UNITS[canonical][1])
    return canonical, absolute, relative, figures, targets


def decide_quantity(rule, answer):
    pending = {'outcome': 'pending', 'normalized_answer': normalize(answer)}
    try:
        canonical, absolute, relative, figures, targets = validate(rule)
    except (ValueError, ArithmeticError, KeyError, TypeError):
        return dict(pending, reason_code='answer_rule')
    try:
        value, unit, lexical = quantity(answer)
    except (ValueError, ArithmeticError):
        return dict(pending, reason_code='numeric_format')
    if not unit and canonical and rule.get('unit_required'):
        return dict(pending, reason_code='unit_required')
    unit = unit or canonical
    if unit not in UNITS:
        return dict(pending, reason_code='unit_unknown')
    if UNITS[unit][0] != UNITS[canonical][0]:
        return dict(pending, reason_code='unit_mismatch')
    if unit != canonical and not rule.get('allow_unit_conversion'):
        return dict(pending, reason_code='unit_conversion_disabled')
    if figures is not None and significant_figures(lexical) != figures:
        return dict(pending, reason_code='precision_review')
    value = value * UNITS[unit][1] / UNITS[canonical][1]
    correct = any(abs(value - target) <= max(absolute, abs(target) * relative) for target in targets)
    return dict(pending, outcome='correct' if correct else 'wrong', reason_code='numeric_quantity')


def teacher_rule(payload):
    """Validate before creating a question; omitted fields preserve old behavior."""
    mode = payload.get('fill_match', 'exact')
    if mode not in ('exact', 'aliases', 'numeric_quantity'):
        raise ValueError('请选择填空核对方式')
    if mode == 'exact':
        return {'answer': payload['answer'], 'match': 'exact'}
    if mode == 'aliases':
        answers = [line.strip() for line in str(payload['answer']).splitlines() if line.strip()]
        if not 1 <= len(answers) <= 20:
            raise ValueError('请按每行一个填写 1—20 个可接受答案')
        return {'answer': answers, 'match': 'exact'}
    figures = str(payload.get('significant_figures') or '').strip()
    if figures and not re.fullmatch(r'\d{1,2}', figures):
        raise ValueError('有效数字需为 1—12 的整数')
    rule = dict(answer=payload['answer'], match=mode, unit=payload.get('unit', ''),
                unit_required=payload.get('unit_required') in ('on', True),
                allow_unit_conversion=payload.get('allow_unit_conversion') in ('on', True),
                absolute_tolerance=payload.get('absolute_tolerance') or '0',
                relative_tolerance=payload.get('relative_tolerance') or '0',
                significant_figures=int(figures) if figures else None)
    validate(rule)
    return rule


def instructions(rule):
    if rule.get('match') != 'numeric_quantity':
        return ''
    parts = ['按' + (rule.get('unit') or '纯数值') + '核对']
    if rule.get('unit_required'):
        parts.append('作答需填写单位')
    if rule.get('allow_unit_conversion'):
        parts.append('允许同量纲单位换算')
    if rule.get('significant_figures'):
        parts.append('有效数字 %s 位' % rule['significant_figures'])
    parts.extend(['绝对容差 %s' % rule.get('absolute_tolerance', '0'),
                  '相对容差 %s' % rule.get('relative_tolerance', '0')])
    return '；'.join(parts)


def reference_answer(rule):
    answer = rule.get('answer')
    if rule.get('match') != 'numeric_quantity':
        return answer
    values = answer if isinstance(answer, list) else [answer]
    shown = []
    for value in values:
        try:
            _, unit, _ = quantity(value)
        except (ValueError, ArithmeticError):
            unit = ''
        shown.append(str(value) + (' ' + rule['unit'] if not unit and rule.get('unit') else ''))
    return ' / '.join(shown) + '（' + instructions(rule) + '）'
