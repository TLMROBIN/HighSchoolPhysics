"""Accessible, outcome-only student and teacher workspaces."""
from collections import defaultdict
from urllib.parse import quote
from .repository import loads
from .exam_views import esc, image_html
from .learning import LABELS, progress, snapshot


def form(action, body):
    return '<form class="learning-form" data-action="%s">%s<div role="status"></div></form>'%(action,body)

def hidden(k,v): return '<input type="hidden" name="%s" value="%s">'%(esc(k),esc(v))
def select(name, rows): return '<select name="%s" required><option value="">请选择</option>%s</select>'%(name,''.join('<option value="%s">%s</option>'%(esc(k),esc(v)) for k,v in rows))
def tags(s): return '<p>'+''.join('<span class="pill">%s：%s</span> '%('知识点' if t['tag_type']=='knowledge' else '能力',esc(t['name'])) for t in loads(s['tag_snapshot_json'],[]) if t['tag_type'] in ('knowledge','ability'))+'</p>'
def images(c,q): return ''.join(image_html(r[0]) for r in c.execute('select id from exam_assets where question_id=?',(q,)))
def footer(): return '<script src="assets/learning.js?v=1" defer></script>'
def base(user): return '<section class="panel learning"><h1>错题与学习记录</h1><nav>'+('<a href="app">学生首页</a>' if user['role']=='student' else '<a href="teacher">教师工作台</a>')+' · <a href="exams">周测与首次作答</a></nav><p>只记录作答与对错，不记录分数。知识点、能力标签用于关联练习，不能凭一道题判断已经掌握。</p>'

def student(repo,user,params):
    c=repo.conn;uid=user['id'];body=[base(user)]
    wrongs=[dict(r) for r in c.execute("select w.* from wrong_questions w join assessment_sessions a on a.id=w.assessment_id where w.student_id=? and a.grading_status='published' order by w.created_at desc,w.id",(uid,))]
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
        body.append('<article><h2>作答练习</h2><p>%s · 验证 %s/3 · 下次日期 %s</p><p>%s</p>%s%s'%(p['status'],p['count'],p['due'],esc(s['stem']),tags(s),images(c,w['question_id'])))
        controls=''
        options=loads(s['options_json'],{})
        if isinstance(options,list): options={chr(65+i):x for i,x in enumerate(options)}
        if s['question_type'] in ('single_choice','multiple_choice'):
            for k,v in options.items(): controls+='<label style="display:block;padding:10px"><input type="%s" name="answer" value="%s"> %s. %s</label>'%('checkbox' if s['question_type']=='multiple_choice' else 'radio',esc(k),esc(k),esc(v))
        else: controls='<label>我的作答<textarea name="answer" rows="3"></textarea></label>'
        body.append(form('submit',hidden('wrong_id',wid)+'<label>练习方式<select name="purpose"><option value="'+('verify' if p['available'] else 'learn')+'">'+('独立验证' if p['available'] else '学习练习')+'</option><option value="'+('learn' if p['available'] else 'verify')+'">'+('学习练习' if p['available'] else '提前独立作答（不提前增加验证进度）')+'</option></select></label>'+controls+'<button>提交作答</button>'))
        body.append('<button type="button" class="learning-solution" data-id="%s">查看答案与解析，切换为学习练习</button><div class="solution-output" role="status"></div></article>'%esc(wid))
    body.append('<h2 id="wrong">我的错题本</h2>')
    for w in unique.values():
        s=snapshot(c,w);p=progress(c,w)
        tag=(params.get('tag') or [''])[0]
        if tag and not any(t['name']==tag for t in loads(s['tag_snapshot_json'],[])):continue
        r=c.execute('select initial_answer from student_responses where id=?',(w['response_id'],)).fetchone()
        body.append('<details><summary>%s · %s · %s/3</summary><p>%s</p>%s%s<aside><strong>首次作答记录</strong><p>%s</p><small>保留本次周测最初提交的选项或填空；后续重做不会覆盖这里。</small></aside><p>上次结果：%s · 下次验证：%s</p><a href="app?practice=%s">%s</a></details>'%(esc(s['stem'][:65]),p['status'],p['count'],esc(s['stem']),tags(s),images(c,w['question_id']),esc(r[0]) or '空白',p['last'],p['due'],quote(w['id']),'开始验证' if p['available'] else '学习练习'))
    body.append('<h2>最近的练习记录</h2><p>已复核仅表示结果已确认；本题是否巩固仍以三次间隔验证为准。</p><table><tr><th>提交时间</th><th>作答</th><th>用途</th><th>结果 / 教师反馈</th></tr>')
    for a in c.execute('select * from redo_attempts where student_id=? order by submitted_at desc limit 20',(uid,)):
        body.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s %s</td></tr>'%(esc(a['submitted_at']),esc(a['answer']) or '空白','独立验证' if a['purpose']=='verify' else '学习练习' if a['purpose']=='learn' else '历史记录',LABELS[a['outcome']],esc(a['feedback'])))
    body.append('</table>')
    body.append('<h2>知识点与能力关联导航</h2><p>点击标签查看关联的错题；标签表示题目关联，不作细分诊断。</p>'+metrics(repo,uid))
    return ''.join(body)+'</section>'+footer()

def metrics(repo,uid):
    c=repo.conn;groups=defaultdict(lambda:dict(q=set(),attempts=0,correct=0,wrong=0,blank=0))
    rows=c.execute("select r.question_id,r.outcome,s.tag_snapshot_json from student_responses r join question_version_snapshots s on s.id=r.snapshot_id join assessment_sessions a on a.id=r.assessment_id where r.student_id=? and a.grading_status='published' union all select w.question_id,a.outcome,s.tag_snapshot_json from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id join student_responses r on r.id=w.response_id join question_version_snapshots s on s.id=r.snapshot_id where a.student_id=? and a.purpose='verify'",(uid,uid)).fetchall()
    for r in rows:
        if r['outcome']=='pending': continue
        seen=set()
        for t in loads(r['tag_snapshot_json'],[]):
            key=(t['tag_type'],t['tag_id'])
            if key in seen or t['tag_type'] not in ('knowledge','ability'): continue
            seen.add(key);g=groups[(t['tag_type'],t['name'])];g['q'].add(r['question_id']);g['attempts']+=1;g[r['outcome']]+=1
    return '<p>原测与每次独立验证均计入尝试；看过解析后的学习练习单独保留，不计入正确率。</p><table><tr><th>关联标签</th><th>不同题数</th><th>作答次数</th><th>正确率</th></tr>'+''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%.1f%%</td></tr>'%('<a href="app?tag='+quote(k[1])+'#wrong">'+esc(k[1])+'</a>',len(g['q']),g['attempts'],100*g['correct']/g['attempts']) for k,g in groups.items())+'</table>'

def teacher(repo,user,params):
    c=repo.conn;body=[base(user),'<h2>教师工作台</h2><p><a href="#create">① 录题与创建周测</a> · <a href="#review">② 待确认作答</a> · <a href="exams">③ 周测统计</a> · <a href="#progress">④ 复习进度</a></p>']
    assessments=repo.assessment_overview(user['id'])
    body.append('<h2>选择班级与周测</h2><ul>'+''.join('<li><a href="exams?id=%s">%s · %s</a></li>'%(quote(a['id']),esc(a['class_name']),esc(a['title'])) for a in assessments)+'</ul>')
    body.append('<h2 id="create">录入选择题、填空题</h2>')
    nodes=c.execute('select id,name from knowledge_nodes order by name').fetchall();abilities=c.execute('select id,name from ability_tags order by name').fetchall()
    body.append(form('question','<label>题目<textarea name="stem" required></textarea></label><label>题型'+select('question_type',[('single_choice','单选'),('multiple_choice','多选'),('fill','填空')])+'</label><label>选项（每行一个，依次 A、B、C、D；填空题留空）<textarea name="options"></textarea></label><label>标准答案<input name="answer" required></label><label>解析<textarea name="analysis"></textarea></label><label>原题图（可选）<input type="file" class="question-image" accept="image/*"></label><label>知识点'+select('knowledge',nodes)+'</label><label>能力'+select('ability',abilities)+'</label><button>保存题目与标签</button>'))
    classes=c.execute('select id,name from class_groups where school_id=? order by name',(user['school_id'],)).fetchall()
    if user['role']=='teacher': classes=[r for r in classes if c.execute('select 1 from teacher_classes where teacher_id=? and class_id=?',(user['id'],r[0])).fetchone()]
    questions=c.execute("select id,stem from questions where school_id=? and question_type in ('single_choice','multiple_choice','fill') order by created_at desc",(user['school_id'],)).fetchall()
    body.append('<h2>每组练习题数</h2>'+form('settings','<label>班级'+select('class_id',classes)+'</label><label>题数<input name="daily_limit" type="number" min="1" max="20" value="5"></label><button>保存</button>'))
    body.append('<details><summary>批量确认题库标签</summary>'+form('tags','<label>知识点'+select('knowledge',nodes)+'</label><label>能力'+select('ability',abilities)+'</label>'+''.join('<label><input type="checkbox" name="questions" value="%s">%s</label>'%(esc(q[0]),esc(q[1][:65])) for q in questions)+'<button>为所选题确认标签</button>')+'</details>')
    body.append('<h2>组卷并创建周测</h2>'+form('assessment','<label>名称<input name="title" required></label><label>班级'+select('class_id',classes)+'</label><label>日期<input type="date" name="date"></label><fieldset><legend>选择题目（按列表顺序编号）</legend>'+''.join('<label style="display:block"><input type="checkbox" name="questions" value="%s">%s</label>'%(esc(q[0]),esc(q[1][:100])) for q in questions)+'</fieldset><button>创建周测</button>'))
    allowed={a['id'] for a in assessments}
    pending=c.execute("select a.*,w.assessment_id,u.display_name,r.initial_answer,s.stem,s.grading_rule_json from redo_attempts a join wrong_questions w on w.id=a.wrong_question_id join users u on u.id=a.student_id join student_responses r on r.id=w.response_id join question_version_snapshots s on s.id=r.snapshot_id where a.outcome='pending' order by a.submitted_at").fetchall()
    body.append('<h2 id="review">待确认的重做</h2>')
    for a in pending:
        if a['assessment_id'] not in allowed: continue
        body.append('<article><h3>%s</h3><p>%s</p><p>实际作答：%s</p><p>标准答案：%s</p>%s</article>'%(esc(a['display_name']),esc(a['stem']),esc(a['answer']),esc(loads(a['grading_rule_json'],{}).get('answer')),form('review',hidden('attempt_id',a['id'])+select('outcome',[('correct','正确'),('wrong','错误'),('blank','空白')])+'<label>反馈（可选）<input name="feedback"></label><button>确认结果</button>')))
    body.append('<h2 id="progress">复习进度</h2><table><tr><th>班级 / 周测</th><th>到期题</th><th>待确认</th><th>三次已巩固</th></tr>')
    for a in assessments:
        ps=[progress(c,dict(w)) for w in c.execute('select * from wrong_questions where assessment_id=?',(a['id'],))]
        body.append('<tr><td>%s / %s</td><td>%s</td><td>%s</td><td>%s</td></tr>'%(esc(a['class_name']),esc(a['title']),sum(p['available'] for p in ps),sum(p['pending'] for p in ps),sum(p['count']>=3 for p in ps)))
    return ''.join(body)+'</table></section>'+footer()

def exams(repo,user,aid=None):
    c=repo.conn;staff=user['role'] in ('teacher','admin');body=[base(user)]
    if not aid:
        rows=repo.assessment_overview(user['id']) if staff else [dict(r) for r in c.execute("select a.*,c.name class_name from assessment_sessions a join class_groups c on c.id=a.class_id join assessment_participants p on p.assessment_id=a.id where p.student_id=? and p.status='present' and a.grading_status='published'",(user['id'],))]
        body.append('<h2>周测记录</h2><ul>'+''.join('<li><a href="exams?id=%s">%s · %s</a></li>'%(quote(a['id']),esc(a['class_name']),esc(a['title'])) for a in rows)+'</ul>')
        if staff: body.append('<p><a href="teacher#create">录题并创建新周测</a></p>')
        return ''.join(body)+'</section>'+footer()
    a=repo.assessment_detail(user['id'],aid)
    if not staff and a['grading_status']!='published':
        from .errors import PermissionDenied
        raise PermissionDenied('尚未发布')
    body.append('<h2>%s</h2>'%esc(a['title']))
    qs=c.execute('select s.*,q.original_question_number from question_version_snapshots s join questions q on q.id=s.question_id where assessment_id=? order by position',(aid,)).fetchall()
    participants=c.execute('select p.*,u.display_name from assessment_participants p join users u on u.id=p.student_id where assessment_id=?',(aid,)).fetchall()
    rs=[dict(r) for r in c.execute('select r.*,u.display_name from student_responses r join users u on u.id=r.student_id where assessment_id=?',(aid,)) if staff or r['student_id']==user['id']]
    if staff and a['grading_status']!='published':
        body.append('<h3>核对学生范围</h3>')
        for p in participants:
            body.append(form('participant',hidden('assessment_id',aid)+hidden('student_id',p['student_id'])+'<span>%s · 当前：%s</span>'%(esc(p['display_name']),esc(p['status']))+select('status',[('present','纳入'),('absent','缺考'),('not_included','不纳入')])+'<button>更新</button>'))
        body.append('<h3>录入 / 导入作答</h3><p>CSV 列名：学生,题号,作答,结果。学生可填姓名、学号或账号；题号用本页顺序号。结果填正确、错误、空白、待确认，留空时选择题自动核对、填空不匹配交教师确认。逗号答案请用英文双引号包围。</p>')
        body.append(form('answers',hidden('assessment_id',aid)+'<input type="file" class="answers-file" accept=".csv,text/csv"><textarea name="csv" rows="8" placeholder="学生,题号,作答,结果&#10;张三,1,A,"></textarea><button>预览表格</button><button type="button" class="confirm-answers" hidden>确认保存</button>'))
        expected=sum(p['status']=='present' for p in participants)*len(qs)
        actual=sum(r['student_id'] in {p['student_id'] for p in participants if p['status']=='present'} for r in rs)
        body.append('<p>应有 %s 条，已录入 %s 条，待确认 %s 条。缺失不能当作空白。</p>'%(expected,actual,sum(r['outcome']=='pending' for r in rs)))
        body.append(form('publish',hidden('assessment_id',aid)+'<button>核对完成，发布到错题本</button>'))
    groups=defaultdict(lambda:dict(total=0,wrong=0,blank=0,students=set(),affected=set()))
    included={p['student_id'] for p in participants if p['status']=='present'}
    for q in qs:
        rows=[r for r in rs if r['question_id']==q['question_id'] and r['student_id'] in included]
        body.append('<article><h3>原题 %s（录入序号 %s）</h3><p>%s</p>%s%s'%(esc(q['original_question_number'] or q['position']),q['position'],esc(q['stem']),tags(q),images(c,q['question_id'])))
        body.append('<details><summary>查看标准答案与解析</summary><p>%s</p></details>'%esc(loads(q['grading_rule_json'],{}).get('answer')) if staff else '')
        body.append('<table><tr><th>学生</th><th>首次作答记录</th><th>结果</th><th>原图</th></tr>')
        for r in rows:
            media=loads(r.get('ocr_payload_json'),{}).get('media_id','')
            body.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'%(esc(r['display_name']),esc(r['initial_answer']) or '空白',LABELS[r['outcome']],'<a target="_blank" href="exam-media?id=%s">答题卡</a>'%quote(media) if media else ''))
            if r['outcome']=='pending': continue
            seen=set()
            for t in loads(q['tag_snapshot_json'],[]):
                key=(t['tag_type'],t['tag_id'])
                if key in seen or t['tag_type'] not in ('knowledge','ability'): continue
                seen.add(key);g=groups[t['name']];g['total']+=1;g['wrong']+=r['outcome']=='wrong';g['blank']+=r['outcome']=='blank';g['students'].add(r['student_id'])
                if r['outcome']!='correct':g['affected'].add(r['student_id'])
        body.append('</table></article>')
    if staff:
        body.append('<h2>标签统计（首次作答）</h2><p>错误率＝非空错误 / 已确认非空作答；空白率＝空白 / 已确认作答；受影响学生＝至少一次错误或空白 / 该标签已有确认作答的学生。缺失和待确认不进入分母。</p><table><tr><th>标签</th><th>错误率</th><th>空白率</th><th>受影响学生</th></tr>')
        for name,g in groups.items():
            body.append('<tr><td>%s</td><td>%s</td><td>%.1f%%</td><td>%s / %s</td></tr>'%(esc(name),'%.1f%%'%(100*g['wrong']/(g['total']-g['blank'])) if g['total']>g['blank'] else '—',100*g['blank']/g['total'],len(g['affected']),len(g['students'])))
        body.append('</table>')
    return ''.join(body)+'</section>'+footer()

def export_wrong_book(repo,user,aid,student_id=None,class_id=None):
    c=repo.conn;repo.assessment_detail(user['id'],aid)
    rows=c.execute('select w.*,u.display_name,u.class_id from wrong_questions w join users u on u.id=w.student_id where w.assessment_id=? order by u.display_name,w.id',(aid,)).fetchall()
    if user['role']=='student': student_id=user['id'];class_id=None
    rows=[r for r in rows if (not student_id or r['student_id']==student_id) and (not class_id or r['class_id']==class_id)]
    out=['<section class="panel learning"><h1>错题学习单</h1><p>首次作答与复习记录分开保存。请先尝试回想，再参考答案。</p>']
    for w in rows:
        s=snapshot(c,w)
        out.append('<article><h2>%s</h2><p>%s</p>%s%s<p>首次作答：%s</p><p>参考答案：%s</p></article>'%(esc(w['display_name']),esc(s['stem']),tags(s),images(c,w['question_id']),esc(w['wrong_answer']) or '空白',esc(loads(s['grading_rule_json'],{}).get('answer'))))
        if user['role']=='student':
            from .learning import now
            c.execute('insert into learning_views values(?,?,?) on conflict(student_id,question_id) do update set viewed_at=excluded.viewed_at',(user['id'],w['question_id'],now()))
    c.commit()
    return ''.join(out)+'</section>'
