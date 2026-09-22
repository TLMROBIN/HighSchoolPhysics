"""Exam inspection UI shared by administrators, teachers, and students."""
import html
import json
from urllib.parse import quote
from .repository import loads
from .errors import PermissionDenied


def esc(v):
    return html.escape(str(v if v is not None else ''))


def image_html(media_id, label='原题图'):
    return '<a href="exam-media?id=%s" target="_blank"><img class="exam-image" loading="lazy" src="exam-media?id=%s" alt="%s"></a>' % (quote(media_id),quote(media_id),esc(label))


def render_exams(repo, user, assessment_id=None):
    actor=user['id']
    staff=user['role'] in ('admin','teacher')
    body=['<section class="panel exam-workspace"><a href="%s">返回首页</a><h1>考试与作答</h1>' % ('admin' if user['role']=='admin' else 'teacher' if staff else 'app')]
    if not assessment_id:
        if staff:
            body.append('''<p>选择题与实验填空分项导入。先检查人数、题号和满分，再发布到学生错题本。</p>
            <details><summary>导入新的考试整理包</summary>
            <p>整理包包含题图、标签、已核对的作答和扫描依据。原始扫描PDF需先完成学生对应与作答核对。</p>
            <label>选择整理包<input type="file" id="exam-bundle-file" accept=".json,application/json"></label>
            <button type="button" id="exam-preview">检查导入内容</button>
            <button type="button" id="exam-import" disabled>导入并发布</button>
            <div id="exam-import-result" role="status" aria-live="polite"></div></details>''')
            rows=repo.assessment_overview(actor)
        else:
            rows=[dict(r) for r in repo.conn.execute("select a.*,c.name class_name from assessment_sessions a join class_groups c on c.id=a.class_id join assessment_participants p on p.assessment_id=a.id where p.student_id=? and p.status='present' and a.grading_status='published' order by a.created_at desc",(actor,))]
        body.append('<ul class="exam-list">')
        for a in rows:
            body.append('<li><a href="exams?id=%s">%s</a> · %s · 满分%s · %s</li>' % (quote(a['id']),esc(a['title']),esc(a['class_name']),a['full_score'],esc(a['status'])))
        if not rows: body.append('<li>暂无考试。导入并发布后可在这里查看。</li>')
        body.append('</ul>')
    else:
        a=repo.assessment_detail(actor,assessment_id)
        if not staff and a['grading_status']!='published': raise PermissionDenied('考试尚未发布')
        body.append('<a href="exams">所有考试</a><h2>%s</h2><p>本系统范围满分 %s 分。按实际纳入的选择题、填空题统计，未纳入的学生不计零分。</p>'%(esc(a['title']),a['full_score']))
        questions=[dict(r) for r in repo.conn.execute('select s.*,q.original_question_number,q.analysis from question_version_snapshots s join questions q on q.id=s.question_id where assessment_id=? order by position',(assessment_id,))]
        params=[assessment_id]
        filt=''
        if not staff: filt=' and r.student_id=?';params.append(actor)
        responses=[dict(r) for r in repo.conn.execute('select r.*,u.display_name from student_responses r join users u on u.id=r.student_id where r.assessment_id=?'+filt+' order by u.display_name',params)]
        if staff:
            participants=[dict(r) for r in repo.conn.execute('select p.*,u.display_name from assessment_participants p join users u on u.id=p.student_id where assessment_id=? order by u.display_name',(assessment_id,))]
            body.append('<details open><summary>学生与总分</summary><table><thead><tr><th>学生</th><th>纳入状态</th><th>本范围得分</th><th>记录数</th></tr></thead><tbody>')
            for p in participants:
                rs=[r for r in responses if r['student_id']==p['student_id']]
                body.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'%(esc(p['display_name']),'已纳入' if p['status']=='present' else '本次未纳入',sum(r['score'] or 0 for r in rs) if rs else '—',len(rs)))
            body.append('</tbody></table></details>')
        else:
            body.append('<p>我的得分：<strong>%s / %s</strong> · <a href="app#wrong">去错题本</a></p>' % (sum(r['score'] or 0 for r in responses),a['full_score']))
        for q in questions:
            rule=loads(q['grading_rule_json'],{})
            tags=loads(q['tag_snapshot_json'],[])
            ans=rule.get('answer','')
            if isinstance(ans,list):ans=' / '.join(str(x) for x in ans)
            rows=[r for r in responses if r['question_id']==q['question_id']]
            wrong=sum((r['score'] or 0)<r['max_score'] for r in rows)
            body.append('<article class="exam-question"><h3>第%s题 · %s分</h3><p>%s</p>' % (esc(q['original_question_number'] or q['position']),q['points'],esc(q['stem'])))
            body.append('<div class="tag-row">'+''.join('<span class="pill">%s：%s</span>' % ('知识点' if t['tag_type']=='knowledge' else '能力',esc(t['name'])) for t in tags if t['tag_type'] in ('knowledge','ability'))+'</div>')
            for im in repo.conn.execute('select id from exam_assets where question_id=? order by rowid',(q['question_id'],)):
                body.append(image_html(im['id']))
            body.append('<p>标准答案：%s</p><p>%s</p>'%(esc(ans),esc(q['analysis'])))
            if staff: body.append('<p>未完全答对：%s / %s 人</p>'%(wrong,len(rows)))
            body.append('<details%s><summary>%s</summary><div class="exam-table-scroll"><table><thead><tr><th>学生</th><th>实际作答</th><th>得分</th><th>依据</th></tr></thead><tbody>'%(' open' if not staff else '', '我的作答' if not staff else '查看全班本题作答'))
            for r in rows:
                media=loads(r['ocr_payload_json'],{}).get('media_id')
                link='<a href="exam-media?id=%s" target="_blank">答题原图</a>'%quote(media) if media else ''
                body.append('<tr><td>%s</td><td>%s</td><td>%s/%s</td><td>%s <small>%s</small></td></tr>'%(esc(r['display_name']),esc(r['final_answer']) or '空白',r['score'],r['max_score'],link,esc(r.get('score_reason',''))))
            body.append('</tbody></table></div></details></article>')
    body.append('</section>')
    return ''.join(body)
