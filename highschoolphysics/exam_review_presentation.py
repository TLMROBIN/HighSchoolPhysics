"""One saved teaching plan for browser presentation and portable exports."""
from html import escape
from pathlib import Path
import re
from urllib.parse import quote

from .exam_review import CONTENT_FIELDS
from .question_rendering import render_markdown


def build_plan(package):
    from .exam_review_export import _stats_markdown, percent
    settings = package['settings']
    items = [item for item in package['items'] if item['included']]
    weights = sum(3 if x['emphasis'] == 'detail' else 1 for x in items) or 1
    questions, slides = [], []
    for item in items:
        source, content = item['source'], item['content']
        title = '第%s题' % source['number']
        notes = '\n\n'.join(filter(None, [content['notes'], content['classroom_question']]))
        if item['emphasis'] == 'brief':
            notes += '\n\n' + '\n\n'.join('## %s\n\n%s' % (CONTENT_FIELDS[key], content[key])
                for flag, keys in [('show_knowledge', ['knowledge_notes']), ('show_solution', ['solution_steps', 'option_analysis']),
                                   ('show_errors', ['possible_errors', 'teacher_errors'])]
                if settings[flag] for key in keys if content[key])
        unit = []

        def add(kind, heading, markdown):
            unit.append(dict(kind=kind, title=heading, markdown=markdown, item_id=item['id'],
                             question_number=source['number'], speaker_notes=notes,
                             reviewed=item['reviewed'], answer_state=source['answer_state']))

        prompt = source['prompt_markdown']
        first, separator, rest = prompt.partition('\n')
        if first.strip() in ('# '+source['number'], '# 第'+source['number']+'题'):
            prompt = rest.lstrip('\n')
        answer = source['answer'] or '尚未提供参考答案'
        if source['answer_state'] != 'verified':
            answer = '**参考答案待核对**\n\n' + answer
        add('question', title, prompt)
        if settings.get('reveal_mode') == 'together':
            if settings['show_answer']:
                unit[-1]['inline_answer'] = '参考答案：' + answer
            if settings['show_statistics']:
                unit[-1]['inline_statistics'] = '正确率：%s（正确 %s / 已确认 %s）' % (
                    percent(source['statistics']['correct_rate']), source['statistics']['correct_count'], source['statistics']['confirmed_count'])
        result = []
        if settings['show_answer'] and settings.get('reveal_mode', 'after') == 'after':
            result += ['## 参考答案\n\n' + answer]
        if settings['show_statistics']:
            result += [_stats_markdown(source, settings['show_answer'])]
        if result:
            add('answer_statistics', title + ' · 答案与作答情况', '\n\n'.join(result))
            if settings['show_answer']:
                unit[-1].update(answer=source['answer'], answer_state=source['answer_state'], correct_options=source.get('correct_options', []))
            if settings['show_statistics']:
                unit[-1].update(statistics=source['statistics'], classes=source['classes'])
        if item['emphasis'] == 'detail':
            for flag, keys in [('show_knowledge', ['knowledge_notes']), ('show_solution', ['solution_steps', 'option_analysis']),
                               ('show_errors', ['possible_errors', 'teacher_errors'])]:
                if settings[flag]:
                    for key in keys:
                        if content[key].strip():
                            add('explanation', title + ' · ' + CONTENT_FIELDS[key], content[key])
            if settings['show_errors']:
                facts = ['教师标注：%s，%s 人。' % (x['label'], x['count']) for x in source['error_evidence']]
                facts += ['学生自述：%s，%s 人（整题范围，不自动归因到各小问）。' % (x['label'], x['count'])
                          for x in source.get('student_reports', [])]
                if facts:
                    add('evidence', title + ' · 已收集的错因记录', '\n\n'.join(facts))
            if content['classroom_question'].strip():
                add('check', title + ' · 课堂检查', content['classroom_question'])
        minutes = round(settings['minutes']*.9*(3 if item['emphasis']=='detail' else 1)/weights, 1)
        questions.append(dict(position=item['position'], question_number=source['number'], emphasis=item['emphasis'],
            material_path='questions/%03d.md' % item['position'], slides=unit, suggested_minutes=minutes,
            speaker_notes=notes, classroom_question=content['classroom_question'], field_sources=item['field_sources']))
        pages = []
        for slide in unit:
            parts = _chunks(slide['markdown'])
            for i, part in enumerate(parts, 1):
                pages.append(dict(slide, markdown=part, title=slide['title'] +
                    ('（%s/%s）' % (i, len(parts)) if len(parts)>1 else '')))
        questions[-1]['slides'] = pages
        slides.extend(pages)
    return dict(minutes=settings['minutes'], overview_minutes=round(settings['minutes']*.1, 1),
                settings=settings, questions=questions, slides=slides)


def _chunks(markdown):
    """Split at Markdown block boundaries; keep formulas/tables and source text intact."""
    from markdown_it import MarkdownIt
    tokens = MarkdownIt('commonmark').enable('table').parse(markdown)
    lines = markdown.splitlines(keepends=True)
    starts = sorted({0, len(lines)} | {t.map[0] for t in tokens if t.level == 0 and t.map})
    blocks = [''.join(lines[a:b]) for a, b in zip(starts, starts[1:])]
    pages, current, size = [], [], 0
    math_open = False
    for block in blocks:
        cost = len(block) + 450*block.count('![') + (70 if '|' in block else 20)*block.count('\n')
        if current and size + cost > 650 and not math_open:
            pages.append(''.join(current)); current, size = [], 0
        current.append(block); size += cost
        if len(re.findall(r'(?<!\\)\$\$', block)) % 2:
            math_open = not math_open
        if block.count(r'\[') != block.count(r'\]'):
            math_open = not math_open
    if current:
        pages.append(''.join(current))
    return pages or ['']


def render_presentation(package, *, preview=False, asset_paths=None):
    plan = build_plan(package)
    offline = asset_paths is not None
    asset_root = 'vendor/katex/' if offline else '/assets/vendor/katex/'
    blocks, choices = [], []
    sources = {item['id']: item['source'] for item in package['items']}
    for slide in plan['slides']:
        def asset_url(aid):
            asset = sources[slide['item_id']]['assets'].get(aid)
            if not asset or asset.get('missing'):
                return None
            return ('/review-pack/' + asset_paths[aid]) if offline else (
                '/api/exam-review/%s/assets/%s?item_id=%s' % (quote(package['id']), quote(aid), quote(slide['item_id'])))
        chunks = [slide['markdown']]
        for part, md in enumerate(chunks, 1):
            title = slide['title'] + ('（%s/%s）' % (part, len(chunks)) if len(chunks)>1 else '')
            body = render_markdown(md, asset_url)
            inline = render_markdown('\n\n'.join(filter(None,[slide.get('inline_answer'),slide.get('inline_statistics')])),asset_url)
            notes = render_markdown(slide['speaker_notes'], asset_url) if preview else ''
            if offline:
                body = body.replace('src="/review-pack/', 'src="')
                inline = inline.replace('src="/review-pack/', 'src="')
                notes = notes.replace('src="/review-pack/', 'src="')
            index = len(blocks)
            choices.append('<option value="%s">%s · %s</option>' % (index, index+1, escape(title)))
            state = '' if slide['reviewed'] else '<span class="deck-draft">讲评草稿 · 待核对</span>'
            blocks.append('<section class="deck-slide"%s aria-label="%s"><header><h1>%s</h1>%s</header>%s'
                          '<div class="deck-body" tabindex="0">%s</div><p class="deck-overflow" hidden>本页内容较长，可在内容区向下滚动</p>'
                          '%s</section>' % (' hidden' if index else '', escape(title, quote=True), escape(title), state,
                          '<div class="deck-inline-answer">'+inline+'</div>' if inline else '', body,
                          '<aside class="deck-note-content" hidden>%s</aside>' % notes if preview else ''))
    if not blocks:
        blocks = ['<section class="deck-slide"><h1>尚未选择讲评题目</h1><p>返回讲评资料，将需要展示的题目设为“纳入导出”并保存。</p></section>']
    css = Path(__file__).with_name('assets').joinpath('exam-review-deck.css').read_text()
    js = Path(__file__).with_name('assets').joinpath('exam-review-deck.js').read_text()
    back = '<a href="/exam-review?id=%s">返回编辑</a>' % quote(package['id']) if not offline else '<a href="预览.html">资料总览</a>'
    notes_panel = '<aside class="deck-notes"><h2>讲课备注</h2><div data-deck-notes></div></aside>' if preview else ''
    stale = '<p class="deck-warning">本资料已过期，展示的是保存时的原题和统计。</p>' if package['stale'] else ''
    title = package['source']['title']
    scope = '、'.join(x['name'] for x in package['source']['classes'])
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s · 考试讲评</title><link rel="stylesheet" href="%skatex.min.css"><style>%s</style></head>
<body class="%s"><div class="deck-shell"><div class="deck-context">%s<span>%s · %s</span></div>%s
<main class="deck-stage" aria-label="考试讲评演示">%s</main>
<nav class="deck-controls" aria-label="翻页控制"><button data-deck-prev>上一页</button><label>跳转到<select data-deck-jump>%s</select></label>
<output data-deck-counter aria-live="polite"></output><button data-deck-next>下一页</button><button data-deck-fullscreen>全屏</button></nav>
<p class="deck-help" data-deck-message role="status">左右方向键、空格翻页；触屏可左右滑动</p>%s</div>
<script src="%skatex.min.js"></script><script src="%scontrib/auto-render.min.js"></script><script>%s</script></body></html>''' % (
        escape(title), asset_root, css, 'deck-preview' if preview else 'deck-present', back, escape(title), escape(scope), stale,
        ''.join(blocks), ''.join(choices), notes_panel, asset_root, asset_root, js)
