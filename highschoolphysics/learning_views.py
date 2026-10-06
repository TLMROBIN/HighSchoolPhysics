"""Accessible, outcome-only student and teacher workspaces."""
from collections import Counter, defaultdict
import hashlib
import json
from urllib.parse import quote
from .document_models import ASSET_URI_RE
from .repository import loads
from .exam_views import esc, image_html
from .learning import LABELS, progress, snapshot
from .question_content import render_snapshot_content, render_snapshot_options, render_snapshot_solution, snapshot_content
from .question_rendering import render_question
from .fill_rules import UNITS, instructions, reference_answer
from .teacher_workspace import navigation, overview


QUESTION_TYPE_LABELS = {
    "single_choice": "单选",
    "multiple_choice": "多选",
    "fill": "填空",
    "short_answer": "简答",
    "structured": "解答题",
    "experiment": "实验题",
}


def _question_document_asset_url(conn, rows, document, school_id):
    question_ids = [row["id"] for row in rows]
    placeholders = ",".join("?" for _ in question_ids)
    assets = conn.execute(
        "select id,question_id from exam_assets where school_id=? and student_id is null and question_id in (%s)"
        % placeholders,
        [school_id, *question_ids],
    ).fetchall()
    assets_by_question = defaultdict(set)
    for asset in assets:
        assets_by_question[asset["question_id"]].add(asset["id"])

    markdown_fields = [document.get("stem_md", "")]
    markdown_fields.extend(item.get("markdown", "") for item in document.get("options", []))
    for child in document.get("children", []):
        markdown_fields.append(child.get("stem_md", ""))
        markdown_fields.extend(item.get("markdown", "") for item in child.get("options", []))
    source_asset_ids = {
        asset_id
        for value in markdown_fields
        for asset_id in ASSET_URI_RE.findall(value or "")
    }
    legacy_ids = {}
    for row in rows:
        for asset_id in source_asset_ids:
            legacy_id = "docmedia-" + hashlib.sha256(
                (row["id"] + ":" + asset_id).encode("utf-8")
            ).hexdigest()[:32]
            if legacy_id in assets_by_question[row["id"]]:
                legacy_ids.setdefault(asset_id, legacy_id)

    return lambda asset_id: (
        "/exam-media?id=" + quote(legacy_ids[asset_id], safe="")
        if asset_id in legacy_ids
        else None
    )


def _question_tags_for_rows(repo, rows):
    by_question = []
    combined = {"knowledge": set(), "ability": set(), "literacy": set()}
    for row in rows:
        tags = repo.tags_for_question(row["id"])
        by_question.append((row, tags))
        for tag in tags:
            if tag["tag_type"] in combined:
                combined[tag["tag_type"]].add(tag["tag_id"])
    return by_question, combined


def _question_tag_pills(tags):
    labels = {"knowledge": "知识点", "ability": "能力", "literacy": "素养"}
    if not tags:
        return '<span class="question-tag-empty">尚未标注</span>'
    return "".join(
        '<span class="pill %s">%s · %s</span>'
        % (esc(tag["tag_type"]), labels.get(tag["tag_type"], "标签"), esc(tag["name"]))
        for tag in tags
    )


def _legacy_question_preview(conn, row, school_id):
    options = loads(row["options_json"], {})
    if isinstance(options, list):
        options = {chr(65 + index): value for index, value in enumerate(options)}
    option_html = ""
    if isinstance(options, dict) and options:
        option_html = '<ol class="question-options">%s</ol>' % "".join(
            '<li><span class="option-key">%s.</span> %s</li>'
            % (esc(key), esc(value))
            for key, value in options.items()
        )
    image_ids = conn.execute(
        "select id from exam_assets where question_id=? and school_id=? and student_id is null order by rowid",
        (row["id"], school_id),
    ).fetchall()
    return (
        '<article class="question-content"><div class="question-stem">%s</div>%s%s</article>'
        % (esc(row["stem"]), option_html, "".join(image_html(item["id"]) for item in image_ids))
    )


def _teacher_question_groups(repo, user):
    c = repo.conn
    rows = c.execute(
        """select q.*,binding.group_id,binding.child_key,revision.document_json,
                  revision.review_state as content_review_state
           from questions q
           left join question_content_bindings binding on binding.question_id=q.id
           left join question_content_groups content_group
             on content_group.id=binding.group_id and content_group.school_id=q.school_id
           left join question_content_revisions revision
             on revision.id=content_group.current_revision_id and revision.group_id=content_group.id
           where q.school_id=?
           order by q.created_at desc,q.id""",
        (user["school_id"],),
    ).fetchall()
    grouped = {}
    for row in rows:
        item = dict(row)
        item["options"] = loads(item.get("options_json"), {})
        item["media"] = loads(item.get("media_json"), [])
        item["group_key"] = item.get("group_id") or item["id"]
        grouped.setdefault(item["group_key"], []).append(item)

    def source_number(rows):
        raw_document = rows[0].get("document_json")
        try:
            document = loads(raw_document, None) if raw_document else None
        except (TypeError, ValueError):
            document = None
        return str(
            (document or {}).get("number")
            or next(
                (row.get("original_question_number") for row in rows if row.get("original_question_number")),
                "",
            )
            or ""
        ).strip()

    source_number_counts = Counter(source_number(rows) for rows in grouped.values())
    cards = []
    for group_index, (group_key, group_rows) in enumerate(grouped.items(), start=1):
        raw_document = group_rows[0].get("document_json")
        try:
            document = loads(raw_document, None) if raw_document else None
        except (TypeError, ValueError):
            document = None
        if not isinstance(document, dict) or not isinstance(document.get("children", []), list):
            document = None

        if document and document.get("children"):
            child_order = {child.get("key"): index for index, child in enumerate(document["children"])}
            child_labels = {child.get("key"): child.get("label", "") for child in document["children"]}
            group_rows.sort(key=lambda row: child_order.get(row.get("child_key"), len(child_order)))
            for row in group_rows:
                row["child_label"] = child_labels.get(row.get("child_key"), "")
            expected_keys = set(child_order)
            actual_keys = {row.get("child_key") for row in group_rows}
            complete = expected_keys == actual_keys and len(group_rows) == len(expected_keys)
        else:
            complete = len(group_rows) == 1

        by_question, tag_ids = _question_tags_for_rows(repo, group_rows)
        question_ids = [row["id"] for row in group_rows]
        from .question_bank import tags_ready
        tagged = all(tags_ready(repo,row['id'],tags) for row,tags in by_question)
        content_verified = not document or group_rows[0].get("content_review_state") == "verified"
        selectable = complete and content_verified and tagged
        number = source_number(group_rows)
        title = "题库题目 %02d" % group_index
        source_label = "原卷题号：第%s题" % number if number else "原卷题号：待确认"
        number_warning = ""
        if not number:
            number_warning = "题号待确认，入卷前请对照原卷补齐。"
        elif source_number_counts[number] > 1:
            number_warning = "原卷题号重复，答案导入可能无法唯一匹配；请对照原卷核对。"
        stable_id = str(group_key)[-12:]
        child_count = len((document or {}).get("children", [])) or len(group_rows)
        type_ids = sorted({row["question_type"] for row in group_rows})
        type_text = "、".join(QUESTION_TYPE_LABELS.get(kind, kind) for kind in type_ids)
        all_text = " ".join(
            [title, source_label, stable_id]
            + [row["stem"] for row in group_rows]
            + [tag["name"] for _, tags in by_question for tag in tags]
        )
        if document:
            preview = render_question(
                document,
                asset_url=_question_document_asset_url(c, group_rows, document, user["school_id"]),
            )
        else:
            preview = "".join(_legacy_question_preview(c, row, user["school_id"]) for row in group_rows)

        if not complete:
            disabled_reason = "这道大题仍有小问未完整入库，暂不能加入试卷。"
        elif not content_verified:
            disabled_reason = "原题结构尚未完成复核，暂不能加入试卷。"
        elif not tagged:
            disabled_reason = "请先在题库确认每个小问的标签，至少选择一个知识点；能力和素养可按依据留空。"
        else:
            disabled_reason = ""

        warning_html = (
            '<p class="question-pick-warning">%s</p>' % esc(number_warning)
            if number_warning
            else ""
        )

        labels = "".join(
            '<li><strong>%s</strong><div class="question-tag-list">%s</div></li>'
            % (
                esc((row.get("child_label") or row.get("original_question_number") or "本题")),
                _question_tag_pills(tags),
            )
            for row, tags in by_question
        )
        hidden_ids = "".join(
            '<input type="hidden" name="questions" value="%s" disabled>' % esc(question_id)
            for question_id in question_ids
        )
        cards.append(
            '<article class="question-pick-card" data-question-card data-group-key="%s" '
            'data-search="%s" data-knowledge-ids="%s" data-ability-ids="%s" '
            'data-literacy-ids="%s" data-type-ids="%s" data-question-ids="%s" '
            'data-title="%s" data-child-count="%s" data-selectable="%s">'
            '<header class="question-pick-heading"><label class="question-pick-toggle-label">'
            '<input type="checkbox" data-group-toggle aria-label="将%s整题加入试卷" %s %s>'
            '<span>整题加入试卷</span></label><div><h3>%s</h3><p>%s · %s · %s 个小问</p><p class="question-pick-meta">稳定 ID <code>%s</code></p></div></header>'
            '%s<details class="question-pick-preview-details"><summary>展开题干与全部小问</summary><div class="question-pick-preview" data-question-preview-body>%s</div></details>'
            '<details class="question-tag-details"><summary>各小问标签</summary><ol class="question-tag-breakdown">%s</ol></details>'
            '<p class="question-pick-disabled"%s>%s</p>%s</article>'
            % (
                esc(group_key),
                esc(all_text.lower()),
                esc(" ".join(sorted(tag_ids["knowledge"]))),
                esc(" ".join(sorted(tag_ids["ability"]))),
                esc(" ".join(sorted(tag_ids["literacy"]))),
                esc(" ".join(type_ids)),
                esc(json.dumps(question_ids, ensure_ascii=False)),
                esc(title + " · " + source_label),
                child_count,
                "true" if selectable else "false",
                esc(title),
                "" if selectable else "disabled",
                "" if selectable else "aria-describedby=\"pick-note-%s\"" % esc(group_key),
                esc(title),
                esc(source_label),
                esc(type_text or "未分类"),
                child_count,
                stable_id,
                warning_html,
                preview,
                labels,
                "" if selectable else ' id="pick-note-%s"' % esc(group_key),
                esc(disabled_reason),
                '<div class="question-pick-hidden-ids" hidden>%s</div>' % hidden_ids,
            )
        )
    return cards


def form(action, body):
    return '<form class="learning-form" data-action="%s">%s<div role="status"></div></form>'%(action,body)

def hidden(k,v): return '<input type="hidden" name="%s" value="%s">'%(esc(k),esc(v))
def select(name, rows): return '<select name="%s" required><option value="">请选择</option>%s</select>'%(name,''.join('<option value="%s">%s</option>'%(esc(k),esc(v)) for k,v in rows))
def tags(s):
    labels = {'knowledge': '知识点', 'ability': '能力', 'literacy': '素养'}
    return '<p>'+''.join('<span class="pill">%s：%s</span> '%(labels[t['tag_type']],esc(t['name'])) for t in loads(s['tag_snapshot_json'],[]) if t['tag_type'] in labels)+'</p>'
def images(c,q): return ''.join(image_html(r[0]) for r in c.execute('select id from exam_assets where question_id=?',(q,)))
def question_fragment(c, snapshot_row, user, base_path="", include_solution=False, include_options=True, whole_group=False):
    rendered = render_snapshot_content(
        c,
        snapshot_row["id"],
        user["school_id"],
        base_path,
        include_solution,
        include_options,
        whole_group,
    )
    if rendered is not None:
        return rendered
    old = '<div class="legacy-question-content"><p>%s</p>%s</div>' % (esc(snapshot_row["stem"]), images(c, snapshot_row["question_id"]))
    return old


def answer_controls(c, snapshot_row, user, base_path=""):
    option_rows = render_snapshot_options(c, snapshot_row["id"], user["school_id"], base_path)
    multiple = snapshot_row["question_type"] == "multiple_choice"
    control_type = "checkbox" if multiple else "radio"
    if option_rows is not None:
        if option_rows:
            return "".join(
                '<label style="display:block;padding:10px"><input type="%s" name="answer" value="%s"> %s. %s</label>'
                % (control_type, esc(option["key"]), esc(option["key"]), option["html"])
                for option in option_rows
            )
        return '<p>%s</p><label>我的作答<textarea name="answer" rows="3"></textarea></label>' % esc(instructions(loads(snapshot_row['grading_rule_json'],{})))
    options = loads(snapshot_row["options_json"], {})
    if isinstance(options, list):
        options = {chr(65 + index): value for index, value in enumerate(options)}
    if snapshot_row["question_type"] in ("single_choice", "multiple_choice"):
        return "".join(
            '<label style="display:block;padding:10px"><input type="%s" name="answer" value="%s"> %s. %s</label>'
            % (control_type, esc(key), esc(key), esc(value))
            for key, value in options.items()
        )
    return '<p>%s</p><label>我的作答<textarea name="answer" rows="3"></textarea></label>' % esc(instructions(loads(snapshot_row['grading_rule_json'],{})))
def fill_question_context(s):
    try:
        question_type = s.get('question_type') if hasattr(s, 'get') else s['question_type']
    except (KeyError, IndexError):
        return ''
    if question_type != 'fill': return ''
    return '<p class="fill-question-context"><strong>完整填空题：</strong>本题按空拆分统计；下方原题展示完整大题题干、图示和全部空，本条记录对应其中当前错空。</p>'

def question_part_context(c, snapshot_row, school_id):
    content = snapshot_content(c, snapshot_row["id"], school_id)
    if not content or not content.get("child_label"):
        return ''
    label = esc(content["child_label"])
    return '<p class="question-part-context"><strong>本次作答对应：%s小问。</strong>完整题干和其他小问一并展示。</p>' % label
def footer(): return '<link rel="stylesheet" href="assets/learning-responses.css?v=1"><script src="assets/learning.js?v=20261006-question-bank-v1" defer></script>'
def base(user,title="错题与学习记录"): return '<section class="panel learning"><h1>%s</h1><nav>'%esc(title)+('<a href="app">学生首页</a>' if user['role']=='student' else '<a href="teacher">教师工作台</a>')+' · <a href="exams">周测与首次作答</a></nav><p>只记录作答与对错，不记录分数。知识点、能力标签用于关联练习，不能凭一道题判断已经掌握。</p>'

def practice_history(c, user, wrong):
    rows = c.execute("""select a.answer,a.outcome,a.submitted_at from redo_attempts a
        join wrong_questions w on w.id=a.wrong_question_id
        where a.student_id=? and w.question_id=? and a.school_id=?
        order by a.submitted_at,a.id""", (user['id'],wrong['question_id'],user['school_id'])).fetchall()
    if not rows:
        return '<p>尚无后续复习记录。</p>'
    return '<h4>后续复习记录</h4><ol>' + ''.join('<li>%s · 选择：%s · %s</li>' %
        (esc(r['submitted_at']),esc(r['answer']) or '空白',LABELS[r['outcome']]) for r in rows) + '</ol>'

def student(repo,user,params,base_path=""):
    c=repo.conn;uid=user['id'];body=[base(user)]
    wrongs=[dict(r) for r in c.execute("select w.* from wrong_questions w join assessment_sessions a on a.id=w.assessment_id where w.student_id=? and w.is_active=1 and a.grading_status='published' order by w.created_at desc,w.id",(uid,))]
    unique={}
    for w in wrongs: unique.setdefault(w['question_id'],w)
    due=[w for w in unique.values() if progress(c,w)['available']]
    limit_row=c.execute('select daily_limit from learning_settings where class_id=?',(user['class_id'],)).fetchone()
    limit=limit_row[0] if limit_row else 5
    body.append('<h2>今天到期 · %s 题</h2><p>每组最多 %s 题。先独立回想，再查看解析；同一道题按间隔答对三次，才显示本题已巩固。</p>'%(len(due),limit))
    for w in due[:limit]: body.append('<p><a href="app?practice=%s">开始第 %s 次验证</a></p>'%(quote(w['id']),progress(c,w)['count']+1))
    if not due: body.append('<p>今天暂无到期题。可以查看错题，或进行不计验证次数的学习练习。</p>')
    wid=(params.get('practice') or [''])[0]
    if wid:
        w=repo._require_wrong_question_student(uid,wid)
        if w['id'] not in {x['id'] for x in wrongs}:
            from .errors import PermissionDenied
            raise PermissionDenied('尚未发布')
        s=snapshot(c,w);p=progress(c,w)
        body.append('<article><h2>作答练习</h2><p>%s · 验证 %s/3 · 下次日期 %s</p>%s%s%s%s'%(p['status'],p['count'],p['due'],question_part_context(c,s,user['school_id']),question_fragment(c,s,user,base_path,include_options=False,whole_group=True),fill_question_context(s),tags(s)))
        controls=answer_controls(c,s,user,base_path)
        body.append(form('submit',hidden('wrong_id',wid)+'<label>练习方式<select name="purpose"><option value="'+('verify' if p['available'] else 'learn')+'">'+('独立验证' if p['available'] else '学习练习')+'</option><option value="'+('learn' if p['available'] else 'verify')+'">'+('学习练习' if p['available'] else '提前独立作答（不提前增加验证进度）')+'</option></select></label>'+controls+'<button>提交作答</button>'))
        body.append('<button type="button" class="learning-solution" data-id="%s">查看答案与解析，切换为学习练习</button><div class="solution-output" role="status"></div></article>'%esc(wid))
    body.append('<h2 id="wrong">我的错题本</h2>')
    for w in unique.values():
        s=snapshot(c,w);p=progress(c,w)
        tag=(params.get('tag') or [''])[0]
        if tag and not any(t['name']==tag for t in loads(s['tag_snapshot_json'],[])):continue
        r=c.execute('select initial_answer from student_responses where id=?',(w['response_id'],)).fetchone()
        body.append('<details><summary>%s · %s · %s/3</summary>%s%s%s%s<aside><strong>首次作答记录</strong><p>%s</p><small>保留本次周测最初提交的答案；后续重做不会覆盖这里。</small></aside><p>上次结果：%s · 下次验证：%s</p><a href="app?practice=%s">%s</a></details>'%(esc(s['stem'][:65]),p['status'],p['count'],question_part_context(c,s,user['school_id']),question_fragment(c,s,user,base_path,whole_group=True),fill_question_context(s),tags(s),esc(r[0]) or '空白',p['last'],p['due'],quote(w['id']),'开始验证' if p['available'] else '学习练习'))
        body[-1] = body[-1].rsplit('</details>',1)[0] + practice_history(c,user,w) + '</details>'
    body.append('<h2>最近的练习记录</h2><p>已复核仅表示结果已确认；本题是否巩固仍以三次间隔验证为准。</p><table><tr><th>题目</th><th>提交时间</th><th>作答</th><th>用途</th><th>结果 / 教师反馈</th></tr>')
    for a in c.execute('select a.*,s.stem from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id join student_responses r on r.id=w.response_id join question_version_snapshots s on s.id=r.snapshot_id where a.student_id=? order by a.submitted_at desc limit 20',(uid,)):
        body.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s %s</td></tr>'%(esc(a['stem'][:65]),esc(a['submitted_at']),esc(a['answer']) or '空白','独立验证' if a['purpose']=='verify' else '学习练习' if a['purpose']=='learn' else '历史记录',LABELS[a['outcome']],esc(a['feedback'])))
    body.append('</table>')
    body.append('<h2>知识点与能力关联导航</h2><p>点击标签查看关联的错题；标签表示题目关联，不作细分诊断。</p>'+metrics(repo,uid))
    return ''.join(body)+'</section>'+footer()

def metrics(repo,uid):
    c=repo.conn;groups=defaultdict(lambda:dict(q=set(),attempts=0,correct=0,wrong=0,blank=0))
    rows=c.execute("select r.question_id,r.outcome,s.tag_snapshot_json from student_responses r join question_version_snapshots s on s.id=r.snapshot_id join assessment_sessions a on a.id=r.assessment_id join assessment_participants participant on participant.assessment_id=r.assessment_id and participant.student_id=r.student_id where participant.status='present' and r.student_id=? and a.grading_status='published' union all select w.question_id,a.outcome,s.tag_snapshot_json from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id join student_responses r on r.id=w.response_id join question_version_snapshots s on s.id=r.snapshot_id where a.student_id=? and a.purpose='verify'",(uid,uid)).fetchall()
    for r in rows:
        if r['outcome']=='pending': continue
        seen=set()
        for t in loads(r['tag_snapshot_json'],[]):
            key=(t['tag_type'],t['tag_id'])
            if key in seen or t['tag_type'] not in ('knowledge','ability'): continue
            seen.add(key);g=groups[(t['tag_type'],t['name'])];g['q'].add(r['question_id']);g['attempts']+=1;g[r['outcome']]+=1
    graph=['<details><summary>展开知识点与能力关联图</summary><div style="overflow:auto"><svg role="img" aria-label="知识点和能力与练习题关联图" width="900" height="%s" xmlns="http://www.w3.org/2000/svg">'%max(150,45*len(groups)+60)]
    for i,(key,g) in enumerate(groups.items()):
        y=45*i+35
        graph.append('<path d="M100 %s H260" stroke="#65a5a2"/><text x="12" y="%s" fill="#183c48">%s</text><a href="app?tag=%s#wrong"><rect x="260" y="%s" width="600" height="34" rx="8" fill="#e8f5f3"/><text x="274" y="%s" fill="#164b49">%s · %s 道题</text></a>'%(y,y+5,'知识点' if key[0]=='knowledge' else '能力',quote(key[1]),y-20,y+3,esc(key[1]),len(g['q'])))
    graph.append('</svg></div></details>')
    return ''.join(graph)+'<p>原测与每次独立验证均计入尝试；看过解析后的学习练习单独保留，不计入正确率。</p><table><tr><th>关联标签</th><th>不同题数</th><th>作答次数</th><th>正确率</th></tr>'+''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%.1f%%</td></tr>'%('<a href="app?tag='+quote(k[1])+'#wrong">'+esc(k[1])+'</a>',len(g['q']),g['attempts'],100*g['correct']/g['attempts']) for k,g in groups.items())+'</table>'


def _teacher_learning_evidence(repo, assessments):
    """Use published snapshots and independent redo evidence in the teacher's scope."""
    c=repo.conn
    students={}
    knowledge={}
    for assessment in assessments:
        if assessment['grading_status'] != 'published':
            continue
        rows=c.execute("""select r.*,u.display_name,s.tag_snapshot_json from student_responses r
            join users u on u.id=r.student_id
            join question_version_snapshots s on s.id=r.snapshot_id
            join assessment_participants p on p.assessment_id=r.assessment_id and p.student_id=r.student_id
            where r.assessment_id=? and p.status='present'""",(assessment['id'],)).fetchall()
        wrongs={w['response_id']:dict(w) for w in c.execute('select * from wrong_questions where assessment_id=? and is_active=1',(assessment['id'],))}
        attempts_by_wrong=defaultdict(list)
        for attempt in c.execute("select a.wrong_question_id,a.outcome from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id where w.assessment_id=? and w.is_active=1 and a.purpose='verify'",(assessment['id'],)):
            attempts_by_wrong[attempt['wrong_question_id']].append(attempt)
        for row in rows:
            student=students.setdefault((assessment['class_name'],row['student_id']),
                dict(name=row['display_name'],wrong=0,due=0,pending=0,consolidated=0))
            wrong=wrongs.get(row['id'])
            state=progress(c,dict(wrong)) if wrong else None
            if state:
                student['wrong']+=1
                student['due']+=int(state['available'])
                student['pending']+=int(state['pending'])
                student['consolidated']+=int(state['count']>=3)
            attempts=attempts_by_wrong[wrong['id']] if wrong else []
            seen=set()
            for tag in loads(row['tag_snapshot_json'],[]):
                if tag['tag_type'] != 'knowledge' or tag['tag_id'] in seen:
                    continue
                seen.add(tag['tag_id'])
                key=(assessment['class_name'],row['student_id'],tag['tag_id'])
                evidence=knowledge.setdefault(key,dict(name=row['display_name'],tag=tag['name'],questions=set(),initial=0,correct=0,verify=0,verified=0,pending=0,consolidated=set()))
                evidence['questions'].add(row['question_id'])
                if row['outcome'] != 'pending':
                    evidence['initial']+=1
                    evidence['correct']+=int(row['outcome']=='correct')
                else:
                    evidence['pending']+=1
                evidence['verify']+=sum(a['outcome']!='pending' for a in attempts)
                evidence['verified']+=sum(a['outcome']=='correct' for a in attempts)
                evidence['pending']+=sum(a['outcome']=='pending' for a in attempts)
                if state and state['count']>=3:
                    evidence['consolidated'].add(row['question_id'])
    body=['<h2>学生复习情况</h2><div class="teacher-evidence-table"><table><tr><th>班级</th><th>学生</th><th>错题记录</th><th>到期题</th><th>重做待确认</th><th>三次已巩固</th></tr>']
    for (class_name,_), item in sorted(students.items(),key=lambda pair:(pair[0][0],pair[1]['name'],pair[0][1])):
        body.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (esc(class_name),esc(item['name']),item['wrong'],item['due'],item['pending'],item['consolidated']))
    body.append('</table></div>' if students else '</table><p>暂无已发布考试的学生复习记录。</p></div>')
    body.append('<h2>知识点掌握依据</h2><p>按学生与知识点查看首次作答、独立复习验证和已巩固题目。看过解析后的学习练习不计入验证；正确率与三次已巩固题数提供掌握依据，不能凭一道题判断整个知识点已掌握。未标注知识点的题目不参与下表。</p><div class="teacher-evidence-table"><table><tr><th>班级 / 学生</th><th>知识点</th><th>不同题数</th><th>首次作答正确率</th><th>独立验证答对 / 次数</th><th>待确认记录</th><th>三次已巩固题数</th></tr>')
    for (class_name,_,_), item in sorted(knowledge.items(),key=lambda pair:(pair[0][0],pair[1]['name'],pair[1]['tag'],pair[0][1])):
        rate='%.1f%% (%s/%s)' % (100*item['correct']/item['initial'],item['correct'],item['initial']) if item['initial'] else '待确认'
        body.append('<tr><td>%s / %s</td><td>%s</td><td>%s</td><td>%s</td><td>%s / %s</td><td>%s</td><td>%s</td></tr>' % (esc(class_name),esc(item['name']),esc(item['tag']),len(item['questions']),rate,item['verified'],item['verify'],item['pending'],len(item['consolidated'])))
    body.append('</table></div>' if knowledge else '</table><p>暂无带知识点标签的已发布作答记录。</p></div>')
    return ''.join(body)

def teacher(repo,user,params,document_import_enabled=False):
    c=repo.conn
    module=(params.get('module') or [''])[0]
    titles={'intake':'题目入库','exams':'考试管理','progress':'学生复习进度'}
    body=['<section class="panel learning"><h1>%s</h1>' % titles.get(module,'教师工作台'), navigation(module if module in titles else '')]
    if module not in titles:
        return ''.join(body)+overview()+'</section>'+footer()
    school_classes={row[0] for row in c.execute('select id from class_groups where school_id=?',(user['school_id'],))}
    assessments=[a for a in repo.assessment_overview(user['id']) if a['class_id'] in school_classes]
    assessment_items=''.join(
        '<li><a href="exams?id=%s">%s · %s</a></li>' %
        (quote(a['id']),esc(a['class_name']),esc(a['title']))
        for a in assessments
    )
    if module == "exams":
        body.append(
            '<section class="teacher-assessments"><h2>已有周测</h2>%s</section>' %
            ('<ul class="teacher-assessment-list">%s</ul>' % assessment_items if assessment_items else '<p class="teacher-empty-note">目前还没有已创建的周测。</p>')
        )

    nodes=c.execute('select id,name from knowledge_nodes where school_id=? and enabled=1 and deleted_at is null order by level,name',(user['school_id'],)).fetchall()
    abilities=c.execute('select id,name from ability_tags where school_id=? and enabled=1 and deleted_at is null order by name',(user['school_id'],)).fetchall()
    literacy=c.execute('select id,name from literacy_tags where school_id=? and enabled=1 and deleted_at is null order by level,name',(user['school_id'],)).fetchall()
    classes=c.execute('select id,name from class_groups where school_id=? order by name',(user['school_id'],)).fetchall()
    if user['role']=='teacher':
        classes=[r for r in classes if c.execute('select 1 from teacher_classes where teacher_id=? and class_id=?',(user['id'],r[0])).fetchone()]

    if module == "exams":
        from .question_bank import list_papers
        papers=list_papers(repo,user)
        body.append('<section class="panel" id="new-exam"><h2>新建考试</h2><p>选择已保存的整套试卷。<a href="/question-bank">进入题库选题组卷</a></p>'+form('assessment','<label>试卷'+select('paper_id',[(p['id'],p['title']+' · '+str(p['question_count'])+' 道小题') for p in papers])+'</label><label>考试名称<input name="title" required maxlength="160"></label><label>班级'+select('class_id',classes)+'</label><label>日期<input name="date" type="date"></label><button>使用整套试卷创建考试</button>')+'</section>')

    if module == "intake":
        body.append('<section id="create" class="teacher-intake-section"><h2>题目入库</h2><p class="section-intro">按整份试卷导入，或手动录入一道题。导入后的题目会先进入复核，再纳入题库。</p><div class="teacher-intake-grid">')
        if document_import_enabled:
            body.append(
                '<article class="teacher-intake-card teacher-import-card"><p class="eyebrow">整卷导入</p>'
                '<h3>导入题目与答案文件</h3><p>上传题目与答案 DOCX 或 PDF，对照原件复核题目、图片和答案匹配，再批量入库。</p>'
                '<a class="button-link" href="/documents">导入文件与匹配答案</a></article>'
            )
        body.append(
            '<article class="teacher-intake-card"><p class="eyebrow">单题录入</p><h3>手动录入一道题</h3>'
            + form('question',
                '<label>完整题干<textarea name="stem" rows="4" required></textarea></label>'
                '<label>题型'+select('question_type',[('single_choice','单选'),('multiple_choice','多选'),('fill','填空')])+'</label>'
                '<label>选项（每行一项；填空题留空）<textarea name="options" rows="3"></textarea></label>'
                '<label>标准答案<textarea name="answer" rows="2" required></textarea></label>'
                '<fieldset data-fill-rule hidden disabled><legend>填空核对规则</legend>'
                '<label>核对方式'+select('fill_match',[('exact','原文匹配'),('aliases','多个可接受答案（每行一个）'),('numeric_quantity','数值与单位')]).replace('<option value="">请选择</option>','')+'</label>'
                '<p>规则无法确定的答案交教师复核；只影响这道新题，已发布周测不改判。</p>'
                '<div data-quantity-rule hidden><label>标准单位'+select('unit',[(u,u or '纯数值（无单位）') for u in UNITS]).replace(' required','').replace('<option value="">请选择</option>','')+'</label>'
                '<label><input type="checkbox" name="unit_required">学生必须填写单位</label>'
                '<label><input type="checkbox" name="allow_unit_conversion">允许同量纲单位换算</label>'
                '<label>绝对容差（按标准单位）<input name="absolute_tolerance" value="0" inputmode="decimal"></label>'
                '<label>相对容差（0—1，例如 0.01 表示 1%）<input name="relative_tolerance" value="0" inputmode="decimal"></label>'
                '<label>有效数字位数（可选，1—12）<input name="significant_figures" type="number" min="1" max="12"></label>'
                '<p>支持小数、数值分数和 e 科学计数；未知单位与表达式待复核。整数末尾零的精度不明确时也待复核。</p>'
                '</div></fieldset>'
                '<label>解析（可选）<textarea name="analysis" rows="3"></textarea></label>'
                '<label>原题图片（可选）<input type="file" class="question-image" accept="image/*"></label>'
                '<label>知识点'+select('knowledge',nodes)+'</label>'
                '<label>能力'+select('ability',abilities)+'</label>'
                '<label>素养'+select('literacy',literacy)+'</label>'
                '<button class="button-primary">保存单题</button>'
            )
            + '</article></div></section>'
        )

    if module != "progress":
        return ''.join(body)+'</section>'+footer()
    body.append('<details class="teacher-settings-details"><summary>每日练习设置</summary>')
    body.append('<h3>每组练习题数</h3>'+form('settings','<label>班级'+select('class_id',classes)+'</label><label>题数<input name="daily_limit" type="number" min="1" max="20" value="5"></label><button>保存设置</button>')+'</details>')
    allowed={a['id'] for a in assessments}
    pending=c.execute("select a.*,w.assessment_id,u.display_name,r.initial_answer,s.stem,s.grading_rule_json from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id join users u on u.id=a.student_id join student_responses r on r.id=w.response_id join question_version_snapshots s on s.id=r.snapshot_id where w.is_active=1 and a.outcome='pending' order by a.submitted_at").fetchall()
    body.append('<h2 id="review">待确认的重做</h2>')
    for a in pending:
        if a['assessment_id'] not in allowed: continue
        body.append('<article><h3>%s</h3><p>%s</p><p>实际作答：%s</p><p>标准答案：%s</p>%s</article>'%(esc(a['display_name']),esc(a['stem']),esc(a['answer']),esc(reference_answer(loads(a['grading_rule_json'],{}))),form('review',hidden('attempt_id',a['id'])+select('outcome',[('correct','正确'),('wrong','错误'),('blank','空白')])+'<label>反馈（可选）<input name="feedback"></label><button>确认结果</button>')))
    body.append('<h2 id="progress">复习进度</h2><table><tr><th>班级 / 周测</th><th>到期题</th><th>待确认</th><th>三次已巩固</th></tr>')
    for a in assessments:
        ps=[progress(c,dict(w)) for w in c.execute('select * from wrong_questions where is_active=1 and assessment_id=?',(a['id'],))]
        body.append('<tr><td>%s / %s</td><td>%s</td><td>%s</td><td>%s</td></tr>'%(esc(a['class_name']),esc(a['title']),sum(p['available'] for p in ps),sum(p['pending'] for p in ps),sum(p['count']>=3 for p in ps)))
    body.append('</table>')
    body.append(_teacher_learning_evidence(repo, assessments))
    return ''.join(body)+'</section>'+footer()

def exams(repo,user,aid=None,base_path=""):
    c=repo.conn;staff=user['role'] in ('teacher','admin');body=[base(user)]
    if staff: body.append(navigation('exams'))
    if not aid:
        rows=repo.assessment_overview(user['id']) if staff else [dict(r) for r in c.execute("select a.*,c.name class_name from assessment_sessions a join class_groups c on c.id=a.class_id join assessment_participants p on p.assessment_id=a.id where p.student_id=? and p.status='present' and a.grading_status='published'",(user['id'],))]
        body.append('<h2>周测记录</h2><ul>'+''.join('<li><a href="exams?id=%s">%s · %s</a></li>'%(quote(a['id']),esc(a['class_name']),esc(a['title'])) for a in rows)+'</ul>')
        if staff: body.append('<p><a href="/teacher?module=exams#new-exam">新建考试</a></p>')
        return ''.join(body)+'</section>'+footer()
    a=repo.assessment_detail(user['id'],aid)
    if not staff and a['grading_status']!='published':
        from .errors import PermissionDenied
        raise PermissionDenied('尚未发布')
    body.append('<h2>%s</h2>'%esc(a['title']))
    qs=c.execute('''select s.*,q.original_question_number,
                           binding.revision_id as content_revision_id,
                           binding.child_key as content_child_key,
                           revision.group_id as content_group_id
                    from question_version_snapshots s
                    join questions q on q.id=s.question_id
                    left join snapshot_content_bindings binding on binding.snapshot_id=s.id
                    left join question_content_revisions revision on revision.id=binding.revision_id
                    where s.assessment_id=? order by s.position''',(aid,)).fetchall()
    question_groups=[]
    groups_by_key={}
    for question in qs:
        group_key=question['content_group_id'] or ('snapshot:'+question['id'])
        if group_key not in groups_by_key:
            groups_by_key[group_key]=[]
            question_groups.append(groups_by_key[group_key])
        groups_by_key[group_key].append(question)
    participants=c.execute('select p.*,u.display_name from assessment_participants p join users u on u.id=p.student_id where assessment_id=?',(aid,)).fetchall()
    rs=[dict(r) for r in c.execute('select r.*,u.display_name from student_responses r join users u on u.id=r.student_id where assessment_id=?',(aid,)) if staff or r['student_id']==user['id']]
    if staff and a['grading_status']!='published':
        body.append('<h3>核对学生范围</h3>')
        for p in participants:
            body.append(form('participant',hidden('assessment_id',aid)+hidden('student_id',p['student_id'])+'<span>%s · 当前：%s</span>'%(esc(p['display_name']),esc({'present':'纳入','absent':'缺考','not_included':'不纳入'}.get(p['status'],p['status'])))+select('status',[('present','纳入'),('absent','缺考'),('not_included','不纳入')])+'<button>更新</button>'))
        body.append('<h3>录入 / 导入作答</h3><p>CSV 列名：学生,题号,作答,结果。学生可填姓名、学号或账号；题号用下方每个评分小问的顺序号。有外部结果请选择“导入已核对结果”并说明来源；与规则不一致时须复核。留空时选择题自动核对、填空不匹配交教师确认。逗号答案请用英文双引号包围。</p>')
        body.append(form('answers',hidden('assessment_id',aid)+'<label>导入方式<select name="source_type"><option value="answers">导入学生答案</option><option value="external">导入已核对结果</option></select></label><label>来源名称<input name="source_name" value="教师 CSV 录入" maxlength="240"></label><label>外部结果核对依据<textarea name="source_reason" rows="2"></textarea></label><input type="file" class="answers-file" accept=".csv,text/csv"><textarea name="csv" rows="8" placeholder="学生,题号,作答,结果&#10;张三,1,A,"></textarea><button>预览表格</button><div class="response-preview"></div><button type="button" class="confirm-answers" hidden>确认保存</button>'))
        expected=sum(p['status']=='present' for p in participants)*len(qs)
        actual=sum(r['student_id'] in {p['student_id'] for p in participants if p['status']=='present'} for r in rs)
        body.append('<p>应有 %s 条，已录入 %s 条，待确认 %s 条。缺失不能当作空白。</p>'%(expected,actual,sum(r['outcome']=='pending' for r in rs)))
        body.append(form('publish',hidden('assessment_id',aid)+'<button>核对完成，发布到错题本</button>'))
    groups=defaultdict(lambda:dict(total=0,wrong=0,blank=0,students=set(),affected=set()))
    included={p['student_id'] for p in participants if p['status']=='present'}
    for group_index, question_group in enumerate(question_groups):
        first=question_group[0]
        first_content=snapshot_content(c,first['id'],user['school_id'])
        document=first_content['document'] if first_content else {}
        question_number=(document or {}).get('number') or first['original_question_number'] or first['position']
        group_preview=question_fragment(c,first,user,base_path,whole_group=True)
        body.append('<article class="assessment-question-group"><details class="assessment-question-details"%s><summary><h3>测评题目 %02d · 原卷题号 第%s题 · %s个小问</h3><span>展开题干、作答记录与证据</span></summary>%s'%(
            " open" if group_index == 0 else "", group_index + 1, esc(question_number), len(question_group), group_preview
        ))
        for q in question_group:
            content=snapshot_content(c,q['id'],user['school_id'])
            child_label=content.get('child_label','') if content else ''
            part_title=('小问 '+child_label) if child_label else ('整题作答' if len(question_group)==1 else '作答单元 '+str(q['position']))
            rows=[r for r in rs if r['question_id']==q['question_id'] and r['student_id'] in included]
            body.append('<section class="assessment-subquestion"><h4>%s · 作答序号 %s</h4>%s%s%s'%(
                esc(part_title),q['position'],fill_question_context(q),tags(q),
                '<details><summary>本小问标准答案与解析</summary>%s</details>' % (
                    render_snapshot_solution(c,q['id'],user['school_id'],base_path)
                    or '<p>标准答案：%s</p>' % esc(reference_answer(loads(q['grading_rule_json'],{})) or '尚未导入，暂不能自动核对')
                ) if staff else ''
            ))
            body.append('<table class="response-table"><tr><th>学生</th><th>首次作答记录</th><th>当前有效结果</th><th>原图 / 复核</th></tr>')
            for r in rows:
                media=loads(r.get('ocr_payload_json'),{}).get('media_id','')
                correction_note = ('<p>经更正的作答：%s</p>' % (esc(r['final_answer']) or '空白')) if r['initial_answer']!=r['final_answer'] else ''
                operations = ('<a href="exam-media?id=%s">答题卡</a>' % quote(media)) if media else ''
                if staff:
                    operations += response_controls(r, a['grading_status']=='published')
                else:
                    latest=c.execute('select method,reason from response_decisions where id=?',(r['effective_decision_id'],)).fetchone()
                    if latest and latest['method']=='teacher_correction':
                        correction_note += '<p>教师更正说明：%s</p>' % esc(latest['reason'])
                body.append('<tr><td data-label="学生">%s</td><td data-label="首次作答记录">%s%s</td><td data-label="当前有效结果">%s</td><td data-label="核对与证据">%s</td></tr>'%(esc(r['display_name']),esc(r['initial_answer']) or '空白',correction_note,LABELS[r['outcome']],operations))
                if r['outcome']=='pending': continue
                seen=set()
                for t in loads(q['tag_snapshot_json'],[]):
                    key=(t['tag_type'],t['tag_id'])
                    if key in seen or t['tag_type'] not in ('knowledge','ability','literacy'): continue
                    seen.add(key);g=groups[t['name']];g['total']+=1;g['wrong']+=r['outcome']=='wrong';g['blank']+=r['outcome']=='blank';g['students'].add(r['student_id'])
                    if r['outcome']!='correct':g['affected'].add(r['student_id'])
            body.append('</table></section>')
        body.append('</details></article>')
    if staff:
        body.append('<h2>标签统计（首次作答）</h2><p>错误率＝非空错误 / 已确认非空作答；空白率＝空白 / 已确认作答；受影响学生＝至少一次错误或空白 / 该标签已有确认作答的学生。缺失和待确认不进入分母。</p><table><tr><th>标签</th><th>错误率</th><th>空白率</th><th>受影响学生</th></tr>')
        for name,g in groups.items():
            body.append('<tr><td>%s</td><td>%s</td><td>%.1f%%</td><td>%s / %s</td></tr>'%(esc(name),'%.1f%%'%(100*g['wrong']/(g['total']-g['blank'])) if g['total']>g['blank'] else '—',100*g['blank']/g['total'],len(g['affected']),len(g['students'])))
        body.append('</table>')
    return ''.join(body)+'</section>'+footer()

def export_wrong_book(repo,user,aid,student_id=None,class_id=None,base_path=""):
    c=repo.conn;repo.assessment_detail(user['id'],aid)
    rows=c.execute('select w.*,u.display_name,u.class_id from wrong_questions w join users u on u.id=w.student_id where w.is_active=1 and w.assessment_id=? order by u.display_name,w.id',(aid,)).fetchall()
    if user['role']=='student': student_id=user['id'];class_id=None
    rows=[r for r in rows if (not student_id or r['student_id']==student_id) and (not class_id or r['class_id']==class_id)]
    out=['<section class="panel learning"><h1>错题学习单</h1><p>首次作答与复习记录分开保存。请先尝试回想，再参考答案。</p>']
    for w in rows:
        s=snapshot(c,w)
        content = question_fragment(c,s,user,base_path,include_solution=True,whole_group=True)
        out.append('<article><h2>%s</h2>%s%s%s%s<p>首次作答：%s</p><p>参考答案：%s</p></article>'%(esc(w['display_name']),question_part_context(c,s,user['school_id']),content,fill_question_context(s),tags(s),esc(w['wrong_answer']) or '空白',esc(reference_answer(loads(s['grading_rule_json'],{})))))
        if user['role']=='student':
            from .learning import now
            c.execute('insert into learning_views values(?,?,?) on conflict(student_id,question_id) do update set viewed_at=excluded.viewed_at',(user['id'],w['question_id'],now()))
    c.commit()
    return ''.join(out)+'</section>'


def response_controls(r, published):
    history = '<button type="button" class="response-history" data-id="%s">查看证据与判定历史</button><div class="response-history-output"></div>' % esc(r['id'])
    if not published and r['outcome']!='pending':
        return history
    action='response-correct' if published else 'response-review'
    label='更正作答或结果' if published else '确认待复核作答'
    body=hidden('response_id',r['id'])+hidden('expected_decision_id',r['effective_decision_id'])
    body+='<label>核对后的答案<textarea name="answer">%s</textarea></label>' % esc(r['final_answer'])
    body+='<label>核对后的结果'+select('outcome',[('correct','正确'),('wrong','错误'),('blank','空白')])+'</label>'
    if published:
        body+='<label>更正类型'+select('reason_code',[('extraction_error','识别或转录错误'),('external_error','外部结果错误'),('judgment_error','判定错误')])+'</label>'
    body+='<label>核对依据（必填）<textarea name="reason" required maxlength="2000"></textarea></label>'
    body+='<button>%s</button><div class="response-preview"></div>' % ('预览更正影响' if published else '确认复核')
    if published:
        body+='<button type="button" class="confirm-answers" hidden>确认更正并更新统计</button>'
    return history+'<details><summary>%s</summary>%s</details>' % (label,form(action,body))
