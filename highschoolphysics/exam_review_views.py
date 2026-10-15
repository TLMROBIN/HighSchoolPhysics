"""Teacher-facing review-material entry and editable question cards."""
from html import escape
from urllib.parse import quote

from .exam_review import CONTENT_FIELDS, _exam_scope, get, owned_package
from .exam_review_export import percent
from .question_rendering import render_markdown


def esc(value):
    return escape(str(value if value is not None else ''), quote=True)


STATUS = {'queued': '排队中', 'running': '正在生成', 'ready': 'AI 草稿已生成',
          'failed': '生成失败，可重试', 'reference_only': '原资料草稿，待补充'}


def entry_panel(repo, user, aid, class_ids=None):
    _, _, classes = _exam_scope(repo, user, aid)
    selected = {x['id'] for x in classes} if class_ids is None else set(class_ids)
    options = ''.join('<label><input type="checkbox" name="class_ids" value="%s"%s>%s</label>' %
                      (esc(cls['id']), ' checked' if cls['id'] in selected else '', esc(cls['name'])) for cls in classes)
    toggles = ''.join('<label><input type="checkbox" name="%s" checked>%s</label>' % (key, label) for key, label in
                      [('show_answer', '正确答案'), ('show_statistics', '正确率与选项分布'), ('show_knowledge', '知识点'),
                       ('show_solution', '解题思路与选项辨析'), ('show_errors', '错因与纠正')])
    packs = repo.conn.execute('''select id,created_at from exam_review_packages where assessment_id=? and school_id=?
        order by created_at desc,id desc limit 5''', (aid, user['school_id'])).fetchall()
    links = ''.join('<li><a href="/exam-review?id=%s">讲评资料 · %s</a></li>' % (quote(x['id']), esc(x['created_at'][:19].replace('T', ' ')+' UTC')) for x in packs)
    return '''<section class="exam-review-entry" data-exam-review-create>
        <h3>考试讲评资料</h3><p>按所选班级整理原题、统计与讲评草稿，可编辑后下载，交给办公助手制作课件。</p>
        <form data-review-create data-assessment-id="%s"><fieldset><legend>讲评班级</legend><div class="review-options">%s</div></fieldset>
        <label>课堂时长（分钟）<input name="minutes" type="number" min="1" max="180" value="45" required></label>
        <details><summary>课堂展示内容</summary><div class="review-options">%s</div></details>
        <button type="submit">生成讲评资料草稿</button><p role="status" aria-live="polite"></p></form>%s</section>''' % (
            esc(aid), options, toggles, '<details><summary>最近生成的资料</summary><ul>'+links+'</ul></details>' if links else '')


def _statistics(source):
    stats = source['statistics']
    options = ''.join('<li><strong>%s</strong><span>%s 人 · %s</span></li>' %
                      (esc(x['key']), x['count'], percent(x['rate'])) for x in stats['options'])
    classes = ''.join('<tr><td>%s</td><td>%s / %s</td><td>%s</td><td>%s / %s</td></tr>' %
                      (esc(x['name']), x['statistics']['correct_count'], x['statistics']['confirmed_count'],
                       percent(x['statistics']['correct_rate']), x['statistics']['pending_count'], x['statistics']['missing_count']) for x in source['classes'])
    return '''<section class="review-facts"><h4>本次考试作答统计</h4>
        <p><strong>正确率 %s</strong> · 正确 %s / 已确认 %s（含空白 %s）</p>
        <p>实到 %s · 待复核 %s · 缺失 %s。选项统计分母 %s，已确认但选项未知 %s。</p>
        <ul class="review-option-stats">%s</ul><p>%s</p>
        <div class="review-table-scroll"><table><tr><th>班级</th><th>正确/已确认</th><th>正确率</th><th>待复核/缺失</th></tr>%s</table></div></section>''' % (
            percent(stats['correct_rate']), stats['correct_count'], stats['confirmed_count'], stats['blank_count'],
            stats['present_count'], stats['pending_count'], stats['missing_count'], stats['option_denominator'],
            stats['option_unknown_count'], options, '多选题各选项选择率之和可以超过100%。' if options else '', classes)


def item_card(package_id, item, first=False):
    source = item['source']
    asset_url = lambda aid: '/api/exam-review/%s/assets/%s?item_id=%s' % (quote(package_id), quote(aid), quote(item['id']))
    prompt = render_markdown(source['prompt_markdown'], asset_url)
    answer = render_markdown(source['answer'], asset_url) if source['answer'] else '<p>参考答案尚未提供</p>'
    field_html = []
    for key, label in CONTENT_FIELDS.items():
        origin = {'teacher': '教师编辑', 'ai': 'AI 生成，需核对', 'reference': '原解析或考试标签'}[item['field_sources'][key]]
        field_html.append('<label>%s <small data-field-source="%s">%s</small><textarea name="%s" rows="%s" maxlength="20000">%s</textarea></label>' %
                          (esc(label), key, esc(origin), key, 6 if key == 'solution_steps' else 3, esc(item['content'].get(key, ''))))
    evidence = ''.join('<li>教师标注：%s · %s 人</li>' % (esc(x['label']), x['count']) for x in source['error_evidence'])
    evidence += ''.join('<li>学生自述：%s · %s 人（整题范围，不自动归因到各小问）</li>' %
                        (esc(x['label']), x['count']) for x in source.get('student_reports', []))
    warning = '<p class="review-warning">参考答案尚未核对，请先校对原题答案；本题不会自动生成解析。</p>' if source['answer_state'] != 'verified' else ''
    return '''<details class="review-item" data-review-item="%s" data-revision="%s"%s>
        <summary><span>第%s题</span><strong>%s · %s</strong><small data-item-state>%s</small></summary>
        <div class="review-item-body"><div class="review-original">%s%s<details><summary>参考答案与原解析</summary>%s%s</details></div>
        %s<details><summary>已有错因证据</summary>%s<p>选项选择率是事实；由错误选项推测的原因仍需验证。没有记录不代表没有错误。</p></details>
        <form data-review-edit><div class="review-options"><label><input name="included" type="checkbox"%s>纳入导出</label>
        <label>讲评安排<select name="emphasis"><option value="detail"%s>详讲</option><option value="brief"%s>略讲</option></select></label>
        <label><input name="reviewed" type="checkbox"%s>我已核对本题讲评内容</label></div>%s
        <div class="review-actions"><button type="submit">保存本题讲评</button><button type="button" class="secondary" data-review-retry%s>重试本题 AI 生成</button></div>
        <p data-review-error>%s</p><p role="status" aria-live="polite"></p>
        <details data-review-conflict hidden><summary>对照服务器上的新版讲评</summary><pre data-review-latest></pre>
        <div class="review-actions"><button type="button" data-review-keep>保留我改过的字段，载入其余新版内容</button>
        <button type="button" class="secondary" data-review-replace>使用服务器上的新版内容</button></div></details>
        </form></div></details>''' % (
            esc(item['id']), item['revision'], ' open' if first else '', esc(source['number']),
            '详讲' if item['emphasis'] == 'detail' else '略讲', percent(source['statistics']['correct_rate']),
            esc(STATUS[item['status']]), prompt, warning, answer, render_markdown(source['analysis'], asset_url),
            _statistics(source), '<ul>'+evidence+'</ul>' if evidence else '<p>暂无已收集的错因记录。</p>',
            ' checked' if item['included'] else '', ' selected' if item['emphasis'] == 'detail' else '',
            ' selected' if item['emphasis'] == 'brief' else '', ' checked' if item['reviewed'] else '', ''.join(field_html),
            '' if item['status'] in ('failed', 'reference_only') and source['answer_state'] == 'verified' else ' disabled', esc(item['error']))


def page(repo, user, pid):
    package = get(repo, user, pid)
    aid = package['source']['assessment_id']
    counts = package['counts']
    settings = package['settings']
    return '''<section class="panel exam-review-workspace" data-exam-review="%s">
        <a href="/exams?id=%s">← 返回考试与作答</a><h1>%s · 讲评资料</h1>
        <p>%s · %s 分钟。原题和统计固定为生成时的版本；可逐题编辑后下载。</p>
        <div class="review-warning" data-review-stale%s>考试资料或作答已更新。本页保留原版本，请返回考试生成新版。</div>
        <div class="review-progress" role="status" aria-live="polite"><progress max="%s" value="%s"></progress>
        <span data-review-progress>已处理 %s / %s · 排队 %s · 失败 %s · 原资料草稿 %s</span></div>
        <p>只有题目、答案和题图发送给模型。参考答案待核对或未配置模型时保留原资料草稿，可手工补充。</p>
        <div class="review-actions"><a class="button-link" data-review-download href="/api/exam-review/%s/export.zip">下载讲评资料包</a>
        <button type="button" class="secondary" data-review-refresh>刷新生成进度</button></div>
        <p data-review-message role="status" aria-live="polite"></p>
        <p class="review-caption">包含逐题 Markdown、完整 JSON、统计 CSV、逐页建议、原题图片、制作说明和离线预览；统计口径与考试页面一致。%s</p>
        <div class="review-items">%s</div></section>''' % (
            esc(pid), quote(aid), esc(package['source']['title']), esc('、'.join(x['name'] for x in package['source']['classes'])), settings['minutes'],
            '' if package['stale'] else ' hidden', package['total'], package['completed'], package['completed'], package['total'],
            counts['queued'], counts['failed'], counts['reference_only'], quote(pid),
            '导出时按所选课堂展示内容组织总稿；完整逐题数据仍含教师参考答案。',
            ''.join(item_card(pid, item, i == 0) for i, item in enumerate(package['items'])))
