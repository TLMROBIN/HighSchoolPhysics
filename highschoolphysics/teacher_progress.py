"""Compact teacher review workspace; all values come from scoped published evidence."""
import json
from html import escape


def workspace(students, knowledge):
    records = []
    topics_by_student = {}
    for (class_name, student_id, tag_id), evidence in knowledge.items():
        topics_by_student.setdefault((class_name, student_id), []).append((tag_id, evidence))
    for (class_name, student_id), item in students.items():
        topics = []
        for tag_id, evidence in topics_by_student.get((class_name, student_id), []):
            topics.append(dict(id=tag_id, tag=evidence['tag'], questions=len(evidence['questions']),
                initial=evidence['initial'], correct=evidence['correct'], verify=evidence['verify'],
                verified=evidence['verified'], pending=evidence['pending'], consolidated=len(evidence['consolidated'])))
        records.append(dict(id=student_id, class_name=class_name, **item, topics=topics))
    payload = json.dumps(records, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    class_options = ''.join('<option>%s</option>' % escape(name) for name in sorted({key[0] for key in students}))
    return '''<section class="review-workspace" data-review-workspace>
      <div class="review-heading"><div><h2 id="progress">班级复习概览</h2><p>先看复习分布，再定位学生或知识点。仅统计已发布考试中有作答记录的学生。</p></div>
      <label>班级<select data-review-class><option value="">全部班级</option>''' + class_options + '''</select></label></div>
      <div data-review-overview>''' + ('<p>暂无已发布考试的学生复习记录。</p>' if not records else '') + '''</div>
      <div class="review-legend"><span class="stage-0">未完成有效验证</span><span class="stage-1">验证 1 次</span><span class="stage-2">验证 2 次</span><span class="stage-3">三次已巩固</span></div>
      <p class="review-note">色条表示错题记录的间隔验证次数；到期和待确认单独计数。相同题目在不同考试中的错题记录分别计算。</p>
      <div class="review-switch" role="group" aria-label="查看方式"><button type="button" data-review-view="students" aria-pressed="true">学生复习情况</button><button type="button" data-review-view="topics" aria-pressed="false">知识点掌握依据</button></div>
      <div class="review-controls"><label class="review-search">查找<input type="search" data-review-search placeholder="输入学生姓名或学号" autocomplete="off"></label>
      <label>筛选<select data-review-state><option value="">全部学生</option><option value="due">有到期题</option><option value="pending">重做待确认</option><option value="started">已开始有效验证</option><option value="unstarted">尚无有效验证</option><option value="consolidated">有已巩固题</option></select></label>
      <label>排序<select data-review-sort><option value="due">到期题优先</option><option value="pending">待确认优先</option><option value="name">班级 / 姓名</option><option value="consolidated">已巩固题优先</option></select></label><button type="button" data-review-reset>重置筛选</button></div>
      <p data-review-count role="status" aria-live="polite"></p>
      <div data-review-list></div>
      <nav class="review-pagination" aria-label="结果分页"><button type="button" data-review-page="prev">上一页</button><span data-review-page-label></span><button type="button" data-review-page="next">下一页</button><label>每页<select data-review-size><option>12</option><option>24</option><option>48</option></select></label></nav>
      <details class="review-explanation"><summary>如何理解复习和知识点数据</summary><p>首次作答正确率与独立验证正确率分别计算。看过解析后的学习练习不计入验证；独立验证答对也须满足间隔要求才增加进度。一次答对不代表整个知识点已掌握，三次已巩固只对应具体题目。未标注知识点的题目不参与知识点统计；待确认记录不参与正确率分母。</p></details>
      <noscript><p>请启用 JavaScript 以查看筛选、图表和个人详情。</p></noscript>
      <script type="application/json" data-review-data>''' + payload + '''</script>
    </section><link rel="stylesheet" href="assets/teacher-progress.css?v=20261007-review-v1"><script src="assets/teacher-progress.js?v=20261007-review-v1" defer></script>'''
