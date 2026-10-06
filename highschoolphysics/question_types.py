"""Whole-question categories, separate from each small part's grading format."""

BANK_TYPES = {'single_choice': '单选题', 'multiple_choice': '多选题',
              'experiment': '实验题', 'solution': '解答题', 'fill': '填空题',
              'unknown': '待确认'}


def bank_type(kind, stem='', document=None):
    if document and document.get('bank_type') in BANK_TYPES:
        return document['bank_type']
    kind = (document or {}).get('kind', kind)
    if kind in ('single_choice', 'multiple_choice', 'experiment', 'fill'):
        return kind
    if kind in ('short_answer', 'structured'):
        return 'solution'
    return 'unknown'
