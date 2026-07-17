# Deliverable: 修 render_teacher_app 零 assessment 场景

## 1. Summary
修复了 `highschoolphysics/server.py:1033-1477` 中 `render_teacher_app` 在
`dashboard["assessments"]` 为空时的 `IndexError` 根因:零 assessment 场景(管理员
刚 import-teacher + 分配班级的老师)直接走「暂无测评」空状态早返回分支,避免对
`assessment["id"]` / `assessment["title"]` 等字段取值导致 500。新增
`tests/test_teacher_empty_dashboard.py` 三个回归测试(admin → import-teacher →
assign-classes → 教师改密登录 → GET /teacher 200 + 含空状态文案),远端 190 个
unittest 全过(159.587s)。

## 2. Changed files

### 修改
- `highschoolphysics/server.py` — `render_teacher_app` 函数体起手加零 assessment
  早返回分支,空状态页:包含头部 / 退出按钮(由 `render_layout` 渲染)+ 测评批次
  panel + LLM 候选审核 + 答题卡复核 + 组卷与答题卡入口 + 班级下拉。**不渲染**
  mastery_analytics / diagnostics / class_mastery_analytics / phase2d-form-grid
  (这些都依赖 `assessment_id`)。`data-empty-state="no-assessment"` 标记供测试断言。

### 新增
- `tests/test_teacher_empty_dashboard.py` — 246 行,3 个测试:
  - `test_teacher_with_zero_assessments_does_not_500`:端到端,LivePhysicsServer
    (seed=False) → bootstrap_admin → 手搓 class-physics-empty 班级 → admin
    import-teacher + assign-classes → 教师改密 → GET /teacher 验证 status=200
    + 含「暂无测评」+ `data-empty-state="no-assessment"`。
  - `test_render_teacher_app_empty_dashboard_does_not_raise`:纯函数验证
    `render_teacher_app(user, dashboard_with_empty_assessments)` 不抛 IndexError。
  - `test_teacher_dashboard_zero_assessments_returns_empty_lists`:repository
    端验证 `teacher_dashboard(teacher_id)` 在 0 assessment 时返回空结构
    (assessments=[], students=[], diagnostics.assessment=None)。

## 3. server.py 改动 diff 摘要

```
@@ -1031,7 +1031,56 @@
 def render_teacher_app(user, dashboard):
-    assessment = dashboard["assessments"][0]
+    assessments = dashboard.get("assessments", []) or []
+    if not assessments:
+        # 零 assessment 教师:admin 刚导入 / 刚分配班级的老师,直接给出空状态页,
+        # 避免对 assessment[id/title/...] 取值导致 IndexError 或 KeyError。
+        # 保留头部 / 退出按钮(由 render_layout 渲染),引导先去组卷。
+        # 不渲染 mastery_analytics / diagnostics / class_mastery_analytics / phase2d-form-grid
+        # 这些区域都依赖 assessment_id,等管理员建好测评后再展示。
+        class_options = "".join(
+            "<option value='%s'>%s</option>"
+            % (escape(item["id"]), escape(item["name"]))
+            for item in dashboard.get("classes", [])
+        )
+        body = """
+<section class="teacher-app">
+  <div id="action-status" class="action-status" aria-live="polite">等待操作</div>
+  <div class="workspace-grid">
+    <section class="panel span-2">
+      <div class="panel-head">
+        <h1>测评批次</h1>
+        <span>批改并发布会先检查低置信答题卡,未复核时不会发布。</span>
+      </div>
+      <article class="empty-state" data-empty-state="no-assessment">
+        <p><strong>暂无测评</strong></p>
+        <p>系统还没有为本教师或所任课班级创建任何测评批次。</p>
+        <p>请先在下方「组卷与答题卡」中创建试卷,或联系管理员导入测评数据。</p>
+      </article>
+    </section>
+    <section class="panel">
+      <h2>LLM 候选审核</h2>
+      ...
+    </section>
+    <section class="panel">
+      <h2>答题卡复核</h2>
+      ...
+    </section>
+    <section class="panel span-2">
+      <div class="panel-head">
+        <h2>组卷与答题卡</h2>
+        <p class="explain">尚未创建测评,可在此处先组卷。创建测评后,系统会自动启用错题本、PDF 批改、年级掌握趋势等模块。</p>
+      </div>
+    </section>
+  </div>
+</section>""".format(class_options=class_options)
+        return render_layout("教师端 - 高中物理闭环系统", user, body, "teacher")
+    assessment = assessments[0]
     candidate_rows = []
```

具体行号(以 `git show HEAD:highschoolphysics/server.py` 为准):
- 第 1034 行:`assessments = dashboard.get("assessments", []) or []`
- 第 1035 行:`if not assessments:` 进入空状态分支
- 第 1082 行:`return render_layout(...)` 早返回
- 第 1083 行:`assessment = assessments[0]` 原逻辑(非空时)

## 4. 本地 unittest 输出末 5 行

```
$ python3 -m unittest discover -s tests -v
# 注意:本地仓库是不完整副本(从远端 scp 拉的临时工作区),缺少 auth / db / errors /
# security / exporting / graph_layout / sso 等子模块,本地 `unittest discover` 跑不起来。
# 真正的单测验证在远端完成(见下)。
```

## 5. 远端 unittest 输出末 5 行

```
$ cd /home/yub/Documents/trae_projects/HighSchoolPhysics && .venv/bin/python -m unittest discover -s tests -v
test_unpublished_results_are_hidden_from_student (test_workflow.WorkflowTests) ... ok
test_wrong_book_export_profile_controls_answers_analysis_and_redo_history (test_workflow.WorkflowTests) ... ok

----------------------------------------------------------------------
Ran 190 tests in 159.587s

OK
```

新加的 3 个测试全过:
```
test_render_teacher_app_empty_dashboard_does_not_raise (test_teacher_empty_dashboard.TeacherEmptyDashboardTests) ... ok
test_teacher_dashboard_zero_assessments_returns_empty_lists (test_teacher_empty_dashboard.TeacherEmptyDashboardTests) ... ok
test_teacher_with_zero_assessments_does_not_500 (test_teacher_empty_dashboard.TeacherEmptyDashboardTests) ... ok
```

## 6. 远端新服务 PID

- **新 PID: 3845128**(`nohup .venv/bin/python -m highschoolphysics.server --host 0.0.0.0 --port 8765 --db data/school.sqlite3`)
- 原 PID 3823311 已被 kill
- 端口 8765 监听中(`ss -tlnp` 确认)
- 启动日志:`HighSchoolPhysics running at http://0.0.0.0:8765`

## 7. 远端 git log -1 输出

```
$ cd /home/yub/Documents/trae_projects/HighSchoolPhysics && git log --oneline -3
9fcad189 test: regression for zero-assessment teacher dashboard
70cf11d7 fix: render_teacher_app graceful empty state
ab3775e6 feat: complete production operations integration
```

两次提交:
- `70cf11d7`:server.py 修复(对应本地 commit 24e6a36)
- `9fcad189`:tests/test_teacher_empty_dashboard.py(包含第二次修订,LivePhysicsServer seed=False + 手搓 bootstrap_admin + class-physics-empty 班级)

## 8. 关键注意事项(给 verifier)

- **本地工作区是不完整副本**(`from .auth` / `from .db` 等本地 import 链断),本地
  `python3 -m unittest discover -s tests` 跑不起来。**真实验证在远端**。
- 测试关键决策:第一次提交用默认 `seed=True` + `seed_other_class` + 分配到
  `class-physics-1` 失败,因 demo assessment `assess-week-1` 通过 class scope
  仍出现在新教师 dashboard 里。改用 `seed=False` 启动 + `bootstrap_admin` + 手搓
  `class-physics-empty` 班级后通过。
- t_e2e 用户未动,admin 密码未动,`data/school.sqlite3` 未 reset,所有约束遵守。
- 修复版 server.py 已生效(远端 PID 3845128),t_e2e / teacher_li 等已有教师的
  正常使用不受影响(他们的 assessments 不为空,走原逻辑)。
