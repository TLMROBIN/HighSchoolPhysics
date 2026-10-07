"""Shared navigation for the four teacher workspaces."""
from html import escape

MODULES = (
    ('intake', '/teacher?module=intake', '题目入库', '导入题目与答案文件，对照原件复核并匹配入库。'),
    ('bank', '/question-bank', '题库管理', '编辑题目、设置知识点等标签，选题并保存试卷。'),
    ('exams', '/teacher?module=exams', '考试管理', '用试卷创建考试，录入学生作答，核对并查看统计。'),
    ('graph', '/teacher?module=graph', '诊断与知识图谱', '审核诊断卡、具体学习目标和知识关联，保留教学依据。'),
    ('progress', '/teacher?module=progress', '学生复习进度', '查看学生复习情况、重做待确认与知识点掌握依据。'),
)


def navigation(active=''):
    links = ['<a href="/teacher"%s>工作台首页</a>' % (' aria-current="page"' if not active else '')]
    links.extend('<a href="%s"%s>%s</a>' % (url, ' aria-current="page"' if key == active else '', label)
                 for key, url, label, _ in MODULES)
    return '<nav class="teacher-workbench-nav" aria-label="教师工作台模块">%s</nav>' % ''.join(links)


def overview():
    return '<div class="teacher-module-grid">%s</div>' % ''.join(
        '<a class="teacher-module-card" href="%s"><h2>%s</h2><p>%s</p><span>进入模块 →</span></a>'
        % (url, escape(label), escape(description)) for _, url, label, description in MODULES
    )
