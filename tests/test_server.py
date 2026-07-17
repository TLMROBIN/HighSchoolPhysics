import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from highschoolphysics.auth import AuthService
from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics.server import (
    render_admin_app,
    render_change_password_page,
    render_login_page,
    render_student_app,
    render_teacher_app,
    main,
)


class ServerRenderingTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmpdir.name) / "server.sqlite3")
        initialize_database(self.conn)
        seed_demo_data(self.conn)
        self.auth = AuthService(self.conn)
        self.repo = PhysicsRepository(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _publish_demo_assessment(self):
        teacher = self.auth.login(
            "teacher_li",
            "teacher123",
            "unit-test",
        ).user
        candidate = self.repo.generate_llm_candidates(
            actor_id=teacher["id"],
            question_id="q-newton-1",
        )
        self.repo.approve_candidate_tags(
            actor_id=teacher["id"],
            candidate_id=candidate["id"],
            knowledge_node_ids=["kn-pep2019-r1-c04-s03"],
            ability_tag_ids=[
                "ab-context-modeling",
                "ab-equation-building",
            ],
        )
        self.repo.resolve_review_item(
            actor_id=teacher["id"],
            response_id="resp-1001-q2",
            corrected_answer="C",
            reason="测试复核",
        )
        self.repo.grade_assessment(
            actor_id=teacher["id"],
            assessment_id="assess-week-1",
            publish=True,
        )

    def test_login_page_is_direct_app_entry(self):
        html = render_login_page("")

        self.assertIn("<form", html)
        self.assertIn("高中物理闭环系统", html)
        self.assertNotIn("产品介绍", html)

    def test_login_hides_demo_credentials_by_default(self):
        html = render_login_page("", demo_mode=False)

        self.assertNotIn("teacher123", html)
        self.assertNotIn("student123", html)
        self.assertNotIn("admin123", html)

    def test_login_shows_demo_credentials_when_enabled(self):
        html = render_login_page("", demo_mode=True)

        self.assertIn("teacher_li / teacher123", html)
        self.assertIn("stu_1001 / student123", html)
        self.assertIn("admin / admin123", html)

    def test_init_admin_cli_creates_login_and_exits(self):
        db_path = Path(self.tmpdir.name) / "bootstrap.sqlite3"

        with patch(
            "highschoolphysics.server.getpass.getpass",
            side_effect=["AdminPhysics123", "AdminPhysics123"],
        ), patch("builtins.print"):
            main(
                [
                    "--db",
                    str(db_path),
                    "--init-admin",
                    "school_admin",
                    "--admin-display-name",
                    "学校管理员",
                    "--school-name",
                    "本地学校",
                ]
            )

        conn = connect(db_path)
        try:
            result = AuthService(conn).login(
                "school_admin",
                "AdminPhysics123",
                "unit-test",
            )
        finally:
            conn.close()
        self.assertEqual(result.user["role"], "admin")

    def test_demo_cli_explicitly_enables_demo_mode(self):
        db_path = Path(self.tmpdir.name) / "demo.sqlite3"

        with patch("highschoolphysics.server.run") as run_server:
            main(
                [
                    "--demo",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "9876",
                    "--db",
                    str(db_path),
                ]
            )

        run_server.assert_called_once_with(
            host="127.0.0.1",
            port=9876,
            db_path=db_path,
            demo_mode=True,
        )

    def test_student_app_is_graph_first_with_wrong_book_as_secondary(self):
        student = self.auth.login("stu_1001", "student123", "unit-test").user
        html = render_student_app(student, self.repo.student_dashboard(student["id"]))

        self.assertIn('data-tab-panel="graph"', html)
        self.assertIn('class="student-tab is-active"', html)
        self.assertIn("今天先解决什么", html)
        self.assertIn("从薄弱点出发", html)
        self.assertIn("按教材浏览", html)
        self.assertIn("相关题目", html)
        self.assertIn('data-action="mark-knowledge"', html)
        self.assertIn('class="bottom-nav"', html)
        self.assertIn("知识图谱", html)
        self.assertIn("错题本", html)
        self.assertIn("待重做", html)
        self.assertIn("最近测评", html)

    def test_student_app_renders_calculated_mastery_colors_and_evidence(self):
        self._publish_demo_assessment()
        student = self.auth.login("stu_1001", "student123", "unit-test").user
        self.repo.set_knowledge_mastery_mark(
            actor_id=student["id"],
            student_id=student["id"],
            knowledge_node_id="kn-pep2019-r1-c04-s03",
            level="需教师讲解",
            note="需要复盘",
        )

        html = render_student_app(
            student,
            self.repo.student_dashboard(student["id"]),
        )

        self.assertIn("mastery-state-mastered", html)
        self.assertIn("正确率 100%", html)
        self.assertIn("为什么是这个状态：历史表现：1 次评测，正确率 100%", html)
        self.assertIn("我的标记：需教师讲解", html)
        self.assertIn("需要复盘", html)
        self.assertIn("能力掌握", html)
        self.assertIn("核心素养掌握", html)
        self.assertIn("mastery-state-unpracticed", html)

    def test_student_relation_graph_uses_layered_layout_and_accessible_nodes(self):
        teacher = self.auth.login("teacher_li", "teacher123", "unit-test").user
        self.repo.confirm_question_tags(
            actor_id=teacher["id"],
            question_id="q-newton-1",
            knowledge_node_ids=["kn-pep2019-r1-c04-s03"],
            ability_tag_ids=["ab-force-analysis"],
            literacy_tag_ids=["lit-thinking-model"],
        )
        student = self.auth.login("stu_1001", "student123", "unit-test").user

        html = render_student_app(student, self.repo.student_dashboard(student["id"]))

        self.assertIn('data-layout="focus-radial-v1"', html)
        self.assertIn('viewBox="0 0 720 360"', html)
        self.assertIn('role="group"', html)
        self.assertIn('role="button"', html)
        self.assertIn('tabindex="0"', html)
        self.assertIn('aria-label="查看知识点', html)
        self.assertIn('data-graph-detail-control', html)
        self.assertIn('data-graph-search', html)
        self.assertIn('class="graph-legend"', html)
        self.assertIn('data-graph-zoom-status', html)
        self.assertIn('data-graph-node-status', html)
        self.assertIn('data-graph-related-summary', html)
        self.assertIn('data-graph-related-list', html)
        self.assertIn("教材层级", html)
        self.assertIn("知识关联", html)
        self.assertIn('id="student-graph-data"', html)
        self.assertNotIn("kn-mechanics", html)

    def test_student_graph_assets_support_keyboard_zoom_detail_and_pointer_cancel(self):
        script = Path("highschoolphysics/assets/app.js").read_text()
        styles = Path("highschoolphysics/assets/app.css").read_text()

        self.assertIn("function graphScaleState", script)
        self.assertIn("function renderStudentGraph", script)
        self.assertIn("function studentGraphLayout", script)
        self.assertIn("function graphLabelLines", script)
        self.assertIn("node.shortName || node.name", script)
        self.assertIn("探究 a 与 F、m", Path("highschoolphysics/server.py").read_text())
        self.assertIn('viewBox: "0 0 360 420"', script)
        self.assertIn("allRelatedIds.slice(0, narrow ? 4 : 7)", script)
        self.assertIn("另有 ${hiddenCount} 个未显示", script)
        self.assertIn("data-graph-scale-state", script)
        self.assertIn("data-graph-zoom-status", script)
        self.assertIn('addEventListener("keydown"', script)
        self.assertIn("setPointerCapture", script)
        self.assertIn("pointercancel", script)
        self.assertIn("lostpointercapture", script)
        self.assertIn('[data-graph-scale-state="low"]', styles)
        self.assertIn('[data-graph-scale-state="high"]', styles)

    def test_student_app_uses_progressive_disclosure_and_searchable_wrong_filter(self):
        self._publish_demo_assessment()
        student = self.auth.login("stu_1001", "student123", "unit-test").user

        html = render_student_app(
            student,
            self.repo.student_dashboard(student["id"]),
        )

        self.assertIn('class="module-browser"', html)
        self.assertIn('data-action="collapse-modules"', html)
        self.assertIn('data-wrong-filter-search', html)
        self.assertIn('id="wrong-knowledge-options"', html)
        self.assertIn('data-graph-default-focus="kn-pep2019-r1-c04-s03"', html)
        self.assertIn("先看需要处理的错题", html)
        self.assertNotIn('class="filter-row"', html)
        self.assertLessEqual(html.count('data-knowledge-filter='), 8)

    def test_student_app_exposes_live_status_accessible_tabs_and_plain_language(self):
        student = self.auth.login("stu_1001", "student123", "unit-test").user

        html = render_student_app(student, self.repo.student_dashboard(student["id"]))

        self.assertIn('id="action-status"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('role="tablist"', html)
        self.assertIn('role="tab"', html)
        self.assertIn('aria-selected="true"', html)
        self.assertIn('role="tabpanel"', html)
        self.assertIn('aria-label="放大知识图谱"', html)
        self.assertIn('aria-label="缩小知识图谱"', html)
        self.assertIn('data-action="undo-student-action"', html)
        self.assertNotIn("Phase 2E", html)
        self.assertNotIn("附属功能", html)
        self.assertNotIn("｜显示：", html)
        self.assertNotIn("｜计算：", html)

    def test_student_assets_include_busy_undo_touch_and_reduced_motion_support(self):
        script = Path("highschoolphysics/assets/app.js").read_text()
        styles = Path("highschoolphysics/assets/app.css").read_text()

        self.assertIn("function setStudentBusy", script)
        self.assertIn("function setStudentUndo", script)
        self.assertIn("function friendlyStudentError", script)
        self.assertIn("dataWrongFilterSearch", script)
        self.assertLess(
            script.index("const studentPayload = formPayload"),
            script.index("setStudentBusy(studentForm, true)"),
        )
        self.assertIn('setAttribute("aria-selected"', script)
        self.assertIn('window.scrollTo({ top: 0, behavior: "auto" })', script)
        self.assertIn("restoreFocus = false", script)
        self.assertIn("selectedNode.focus()", script)
        self.assertIn("相关证据已更新", script)
        self.assertIn(".student-app button", styles)
        self.assertIn("min-height: 44px", styles)
        self.assertIn(".related-questions a", styles)
        self.assertIn(".tag-question-card h5 a", styles)
        self.assertIn("@media (min-width: 821px)", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)
        self.assertNotIn("opacity: 0.2", styles)

    def test_student_app_renders_three_family_navigation_without_duplicate_ids(self):
        self._publish_demo_assessment()
        teacher = self.auth.login("teacher_li", "teacher123", "unit-test").user
        candidate = self.repo.generate_llm_candidates(
            actor_id=teacher["id"],
            question_id="q-newton-1",
        )
        self.repo.confirm_question_tags(
            actor_id=teacher["id"],
            question_id="q-newton-1",
            candidate_id=candidate["id"],
            knowledge_node_ids=["kn-pep2019-r1-c04-s03"],
            ability_tag_ids=["ab-force-analysis"],
            literacy_tag_ids=["lit-thinking-model"],
        )
        student = self.auth.login("stu_1001", "student123", "unit-test").user

        html = render_student_app(
            student,
            self.repo.student_dashboard(student["id"]),
        )

        self.assertIn('data-tag-family-panel="knowledge"', html)
        self.assertIn('data-tag-family-panel="ability"', html)
        self.assertIn('data-tag-family-panel="literacy"', html)
        self.assertIn("知识导航", html)
        self.assertIn("能力导航", html)
        self.assertIn("核心素养导航", html)
        self.assertIn("当前掌握证据", html)
        self.assertIn('data-target-tab="graph"', html)
        self.assertIn('data-target-panel="ability"', html)

        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))

        script = Path("highschoolphysics/assets/app.js").read_text()
        self.assertIn("activateTagFamilyPanel", script)
        self.assertIn("targetPanel", script)

    def test_student_question_cards_have_unique_tab_specific_targets(self):
        self._publish_demo_assessment()
        student = self.auth.login("stu_1001", "student123", "unit-test").user

        html = render_student_app(
            student,
            self.repo.student_dashboard(student["id"]),
        )

        self.assertEqual(
            len(re.findall(r'\sid="wrong-question-q-newton-1"', html)),
            1,
        )
        self.assertEqual(
            len(re.findall(r'\sid="redo-question-q-newton-1"', html)),
            1,
        )
        self.assertIn('data-action="open-question"', html)
        self.assertIn('data-target-tab="wrong"', html)
        self.assertIn(
            'data-target-id="wrong-question-q-newton-1"',
            html,
        )

    def test_completed_redo_stays_in_wrong_book_but_leaves_redo_panel(self):
        self._publish_demo_assessment()
        self.conn.execute(
            """
            update wrong_questions
            set redo_status = 'done'
            where student_id = ? and question_id = ?
            """,
            ("stu-1001", "q-newton-1"),
        )
        self.conn.commit()
        student = self.auth.login("stu_1001", "student123", "unit-test").user

        html = render_student_app(
            student,
            self.repo.student_dashboard(student["id"]),
        )

        self.assertEqual(
            len(re.findall(r'\sid="wrong-question-q-newton-1"', html)),
            1,
        )
        self.assertEqual(
            len(re.findall(r'\sid="redo-question-q-newton-1"', html)),
            0,
        )

    def test_student_app_exposes_redo_submission_form(self):
        self._publish_demo_assessment()
        student = self.auth.login("stu_1001", "student123", "unit-test").user
        html = render_student_app(
            student,
            self.repo.student_dashboard(student["id"]),
        )

        self.assertIn("提交重做", html)
        self.assertIn('data-student-form="redo-attempt"', html)
        redo_panel = html.split('id="student-panel-redo"', 1)[1].split(
            'id="student-panel-recent"', 1
        )[0]
        wrong_panel = html.split('id="student-panel-wrong"', 1)[1].split(
            'id="student-panel-redo"', 1
        )[0]
        self.assertIn("请先独立完成这次作答", redo_panel)
        self.assertIn("选择本次答案", redo_panel)
        self.assertEqual(redo_panel.count('type="radio" name="answer"'), 4)
        self.assertIn(
            '<button type="submit" disabled aria-disabled="true" data-requires-answer>',
            redo_panel,
        )
        self.assertNotIn('name="answer" required autocomplete="off"', redo_panel)
        self.assertNotIn("正确答案：", redo_panel)
        self.assertNotIn("解析：", redo_panel)
        self.assertNotIn('data-mastery=', redo_panel)
        self.assertIn("正确答案：", wrong_panel)
        self.assertIn("解析：", wrong_panel)
        self.assertIn("去独立重做", wrong_panel)
        self.assertNotIn('data-student-form="redo-attempt"', wrong_panel)

    def test_student_assets_clear_graph_status_and_enable_answered_redo(self):
        script = Path("highschoolphysics/assets/app.js").read_text()

        self.assertIn('clearTransientStudentStatus("graph-selection")', script)
        self.assertIn('"graph-selection"', script)
        self.assertIn("function syncRedoSubmitState", script)
        self.assertIn('input[name="answer"]:checked', script)
        self.assertIn("submit.disabled = !ready", script)

    def test_pending_live_tag_wrong_overrides_stale_mastery_display(self):
        self._publish_demo_assessment()
        student = self.auth.login("stu_1001", "student123", "unit-test").user

        html = render_student_app(
            student,
            self.repo.student_dashboard(student["id"]),
        )

        self.assertIn("当前状态：待巩固", html)
        self.assertIn("本次测评新增 1 道待纠错题，先完成重做", html)
        self.assertIn("历史表现：此前 1 次评测，正确率 100%", html)
        self.assertIn("查看计算依据", html)

    def test_recent_assessment_cards_link_back_to_learning_actions(self):
        self._publish_demo_assessment()
        student = self.auth.login("stu_1001", "student123", "unit-test").user

        html = render_student_app(
            student,
            self.repo.student_dashboard(student["id"]),
        )
        recent_panel = html.split('id="student-panel-recent"', 1)[1].split(
            'class="bottom-nav"', 1
        )[0]

        self.assertIn('class="assessment-list"', recent_panel)
        self.assertIn('class="assessment-card"', recent_panel)
        self.assertIn("继续重做", recent_panel)
        self.assertIn("薄弱点：牛顿第二定律 · 丢分题 1 道", recent_panel)
        self.assertIn("查看当前知识图谱", recent_panel)
        self.assertIn('data-target-tab="redo"', recent_panel)
        self.assertNotIn("<table", recent_panel)

    def test_student_app_exposes_redo_history_and_latest_status(self):
        self._publish_demo_assessment()
        wrong = self.repo.list_wrong_questions_for_student("stu-1001")[0]
        attempt = self.repo.submit_redo_attempt(
            actor_id="stu-1001",
            wrong_question_id=wrong["id"],
            answer="C",
        )
        self.repo.review_redo_attempt(
            actor_id="user-teacher-li",
            attempt_id=attempt["id"],
            score=wrong["max_score"],
            feedback="重做正确",
        )
        student = self.auth.login("stu_1001", "student123", "unit-test").user

        html = render_student_app(
            student,
            self.repo.student_dashboard(student["id"]),
        )

        self.assertIn("重做状态：已完成", html)
        self.assertIn("重做记录", html)
        self.assertIn("本次答案：C", html)
        self.assertIn("重做正确", html)

    def test_change_password_page_requires_current_and_confirmed_passwords(self):
        student = self.auth.login("stu_1001", "student123", "unit-test").user

        html = render_change_password_page(student)

        self.assertIn("首次登录修改密码", html)
        self.assertIn('name="current_password"', html)
        self.assertIn('name="new_password"', html)
        self.assertIn('name="confirm_password"', html)

    def test_teacher_app_has_graph_list_review_and_export_actions(self):
        teacher = self.auth.login("teacher_li", "teacher123", "unit-test").user
        html = render_teacher_app(teacher, self.repo.teacher_dashboard(teacher["id"]))

        self.assertIn('class="knowledge-graph"', html)
        self.assertIn("LLM 候选审核", html)
        self.assertIn("答题卡复核", html)
        self.assertIn('data-action="grade-assessment"', html)
        self.assertIn("班级诊断", html)
        self.assertIn("A4 错题本", html)
        self.assertIn("LLM 候选审核用于把题目先交给模型生成知识点/能力标签建议", html)
        self.assertIn("生成 q-newton-1 候选", html)
        self.assertIn("批改并发布会先检查低置信答题卡", html)
        self.assertIn('id="action-status"', html)
        self.assertIn('name="student_id"', html)
        self.assertIn("按学生筛选", html)
        self.assertIn("重置本班学生密码", html)
        self.assertIn("stu-1001", html)
        self.assertIn('data-password-reset-form', html)

    def test_teacher_app_exposes_phase_2d_assessment_revision_and_redo_tools(self):
        teacher = self.auth.login("teacher_li", "teacher123", "unit-test").user
        html = render_teacher_app(teacher, self.repo.teacher_dashboard(teacher["id"]))

        self.assertIn("组卷与答题卡", html)
        self.assertIn("OCR 导入复核", html)
        self.assertIn("批改修订", html)
        self.assertIn("错因标签", html)
        self.assertIn('data-teacher-form="paper-assembly"', html)
        self.assertIn('data-teacher-form="ocr-import"', html)
        self.assertIn('data-teacher-form="grading-revision"', html)
        self.assertIn('data-teacher-form="redo-review"', html)

    def test_teacher_app_exposes_phase_2g_mastery_analytics(self):
        self._publish_demo_assessment()
        teacher = self.auth.login("teacher_li", "teacher123", "unit-test").user

        html = render_teacher_app(teacher, self.repo.teacher_dashboard(teacher["id"]))

        self.assertIn("Phase 2G 掌握度分析", html)
        self.assertIn("班级掌握图谱", html)
        self.assertIn("年级均值对比", html)
        self.assertIn("知识掌握", html)
        self.assertIn("能力掌握", html)
        self.assertIn("核心素养掌握", html)
        self.assertIn("学生明细", html)
        self.assertIn("错误率", html)
        self.assertIn("空白率", html)
        self.assertIn('data-analytics-family="knowledge"', html)

    def test_teacher_app_exposes_question_bank_parse_queue_and_tri_family_tags(self):
        teacher = self.auth.login("teacher_li", "teacher123", "unit-test").user
        candidate = self.repo.generate_llm_candidates(
            actor_id=teacher["id"],
            question_id="q-newton-1",
        )
        self.repo.confirm_question_tags(
            actor_id=teacher["id"],
            question_id="q-newton-1",
            candidate_id=candidate["id"],
            knowledge_node_ids=["kn-pep2019-r1-c04-s03"],
            ability_tag_ids=["ab-force-analysis"],
            literacy_tag_ids=["lit-thinking-model"],
        )
        html = render_teacher_app(
            teacher,
            self.repo.teacher_dashboard(teacher["id"]),
        )

        self.assertIn("真实题库", html)
        self.assertIn("新增题目", html)
        self.assertIn("原卷解析", html)
        self.assertIn("拆题复核", html)
        self.assertIn("知识标签", html)
        self.assertIn("能力标签", html)
        self.assertIn("核心素养标签", html)
        self.assertIn('data-teacher-form="question"', html)
        self.assertIn('data-teacher-form="parse-task"', html)
        self.assertIn('data-action="run-parse-task"', html)
        self.assertIn('data-action="generate-candidate"', html)
        self.assertIn('data-question-bank-filter', html)
        self.assertIn('<option value="literacy">核心素养标签</option>', html)
        self.assertIn('value="lit-thinking-model"', html)
        self.assertIn('data-literacy-ids="lit-thinking-model"', html)

    def test_admin_app_exposes_identity_llm_audit_backup_scaffolds(self):
        admin = self.auth.login("admin", "admin123", "unit-test").user
        html = render_admin_app(admin, self.repo.admin_dashboard())

        self.assertIn("用户与班级", html)
        self.assertIn("年级", html)
        self.assertIn("班级", html)
        self.assertIn("知识图谱与能力标签", html)
        self.assertIn("管理员设定", html)
        self.assertIn("本体版本发布", html)
        self.assertIn('data-admin-form="knowledge-node"', html)
        self.assertIn('data-admin-form="knowledge-edge"', html)
        self.assertIn('data-admin-form="ability-tag"', html)
        self.assertIn('data-admin-form="ontology-draft"', html)
        self.assertIn('data-admin-form="ontology-publish"', html)
        self.assertIn("启用状态", html)
        self.assertIn("统一身份预留", html)
        self.assertIn("LLM Key", html)
        self.assertIn("隐私与留存", html)
        self.assertIn("审计日志", html)
        self.assertIn("备份导出", html)
        self.assertIn("重置同校账号密码", html)
        self.assertIn('data-password-reset-form', html)

    def test_admin_user_management_is_compact_filterable_and_paginated(self):
        admin = self.auth.login("admin", "admin123", "unit-test").user
        html = render_admin_app(admin, self.repo.admin_dashboard())

        self.assertIn('class="admin-app admin-app-compact"', html)
        self.assertIn('data-admin-user-search', html)
        self.assertIn('data-admin-user-role-filter', html)
        self.assertIn('data-admin-user-status-filter', html)
        self.assertIn('data-admin-user-page-size="12"', html)
        self.assertIn('data-admin-user-row', html)
        self.assertIn('data-admin-user-pagination', html)
        self.assertIn('data-admin-user-prev', html)
        self.assertIn('data-admin-user-next', html)
        self.assertIn("每页 12 条", html)
        script = Path("highschoolphysics/assets/app.js").read_text()
        self.assertIn("function updateAdminUserTable", script)
        self.assertIn("adminUserPage", script)

    def test_admin_density_styles_constrain_wide_operational_panels(self):
        styles = Path("highschoolphysics/assets/app.css").read_text()

        self.assertIn(".admin-app-compact .compact-table-scroll", styles)
        self.assertIn("width: max-content", styles)
        self.assertIn("--admin-user-account-col", styles)
        self.assertIn("--admin-field-max", styles)
        self.assertIn("--admin-provider-field-max", styles)
        self.assertIn(".admin-app-compact .panel.admin-panel-fit", styles)
        self.assertIn("--admin-panel-fit-max", styles)
        self.assertIn("width: fit-content", styles)
        self.assertIn(".admin-app-compact .panel:not(.admin-panel-fit)", styles)
        self.assertIn(".literacy-admin-panel", styles)
        self.assertIn(".taxonomy-admin-panel", styles)
        # admin-redesign (commit 391b026) 移除了 panel 死限宽 720/980 + 旧 grid
        # 下列 3 行断言旧 grid 风格存在,与本次改造目标直接冲突 → 删
        # self.assertIn("repeat(2, fit-content(720px))", styles)
        # self.assertIn("fit-content(620px) fit-content(320px)", styles)
        # self.assertIn("width: min(320px, 100%)", styles)
        # admin-tab v2 (commit 后续) 把 workspace-grid minmax 420 → 340,
        # 让 1440 屏能并排 3 个小 panel;此断言被替换为新值
        # self.assertIn("repeat(auto-fit, minmax(420px, 1fr))", styles)
        self.assertIn("repeat(auto-fit, minmax(340px, 1fr))", styles)
        self.assertIn(".admin-tab-nav", styles)
        self.assertIn(".audit-log-panel", styles)
        self.assertIn(".pdf-export-panel", styles)
        self.assertIn("width: fit-content", styles)
        # runtime-health-grid 横排卡片：v3 改为 minmax(150px, 1fr) 让 6 卡片
        # 在 1440 屏一行排满
        self.assertIn("repeat(auto-fit, minmax(150px, 1fr))", styles)
        self.assertIn(".mastery-grade-columns", styles)
        self.assertIn(".mastery-overview-panel", styles)
        self.assertIn(".phase2g-analytics .analytics-block", styles)
        self.assertIn("justify-self: start", styles)
        html = render_login_page()
        self.assertIn('/assets/app.css?v=20260717-student-polish-final', html)
        self.assertIn('/assets/app.js?v=20260717-student-polish-final', html)

    def test_admin_app_exposes_export_profiles_and_error_reason_tags(self):
        admin = self.auth.login("admin", "admin123", "unit-test").user
        html = render_admin_app(admin, self.repo.admin_dashboard())

        self.assertIn("错因标签", html)
        self.assertIn("导出配置", html)
        self.assertIn('data-admin-form="error-reason-tag"', html)
        self.assertIn('data-admin-form="export-profile"', html)

    def test_admin_app_exposes_production_readiness_panel(self):
        admin = self.auth.login("admin", "admin123", "unit-test").user
        html = render_admin_app(admin, self.repo.admin_dashboard(admin["id"]))

        self.assertIn("生产化就绪度", html)
        self.assertIn("PaddleOCR 本地识别", html)
        self.assertIn("MarkItDown 文档解析", html)
        self.assertIn("MinerU 本地解析", html)
        self.assertIn("Playwright PDF", html)
        self.assertIn('data-admin-form="runtime-check"', html)
        self.assertIn("runtime-health-grid", html)

    def test_admin_app_exposes_provider_operations_panel(self):
        admin = self.auth.login("admin", "admin123", "unit-test").user
        self.repo.save_provider_config(
            actor_id=admin["id"],
            provider_kind="llm",
            provider_name="OpenAI Compatible",
            model_name="gpt-4.1-mini",
            secret="sk-render-secret",
            enabled=True,
            daily_call_limit=20,
            monthly_budget_cents=500,
        )
        html = render_admin_app(admin, self.repo.admin_dashboard(admin["id"]))

        self.assertIn("Provider 运营", html)
        self.assertIn("LLM 与 MinerU API", html)
        self.assertIn('data-admin-form="provider-config"', html)
        self.assertIn('data-admin-form="provider-test"', html)
        self.assertIn("预算/用量", html)
        self.assertIn("••••", html)
        self.assertNotIn("sk-render-secret", html)

    def test_admin_app_exposes_oidc_settings_without_secret(self):
        admin = self.auth.login("admin", "admin123", "unit-test").user
        self.repo.save_oidc_provider_config(
            actor_id=admin["id"],
            provider_name="School OIDC",
            issuer="https://idp.example.test",
            client_id="physics-client",
            client_secret="oidc-render-secret",
            authorization_endpoint="https://idp.example.test/authorize",
            enabled=True,
        )
        html = render_admin_app(admin, self.repo.admin_dashboard(admin["id"]))

        self.assertIn("OIDC SSO", html)
        self.assertIn('data-admin-form="oidc-provider"', html)
        self.assertIn("School OIDC", html)
        self.assertIn("https://idp.example.test", html)
        self.assertNotIn("oidc-render-secret", html)

    def test_admin_app_exposes_pdf_export_jobs(self):
        self._publish_demo_assessment()
        admin = self.auth.login("admin", "admin123", "unit-test").user
        self.repo.generate_wrong_book_pdf(
            actor_id=admin["id"],
            assessment_id="assess-week-1",
            output_dir=Path(self.tmpdir.name) / "exports",
            engine=lambda html, options: {
                "pdf_bytes": b"%PDF-1.4\nadmin\n",
                "engine_version": "fake-pdf-v1",
            },
        )
        html = render_admin_app(admin, self.repo.admin_dashboard(admin["id"]))

        self.assertIn("PDF 导出任务", html)
        self.assertIn("wrong_book_pdf", html)
        self.assertIn("fake-pdf-v1", html)

    def test_admin_app_exposes_phase_2g_grade_analytics_without_student_rows(self):
        self._publish_demo_assessment()
        admin = self.auth.login("admin", "admin123", "unit-test").user

        html = render_admin_app(admin, self.repo.admin_dashboard(admin["id"]))

        self.assertIn("掌握分析", html)
        self.assertIn("年级掌握趋势", html)
        self.assertIn("聚合标签掌握", html)
        self.assertIn("aggregate-only", html)
        self.assertIn("高二", html)
        self.assertNotIn('data-admin-analytics-student-id="stu-1001"', html)
        # 未分班年级不显示：高一在 demo 数据中 class_count=0,应被过滤
        self.assertNotIn(">高一<", html)

    def test_admin_app_exposes_default_taxonomy_sources_filters_and_literacy(self):
        admin = self.auth.login(
            "admin",
            "admin123",
            "unit-test",
        ).user
        self.conn.execute(
            """
            update taxonomy_sources
            set title = '<课程标准>', local_path = '/private/source.pdf'
            where source_key = 'curriculum-standard-2017-2020'
            """
        )
        self.conn.commit()

        html = render_admin_app(
            admin,
            self.repo.admin_dashboard(),
        )
        teacher = self.auth.login(
            "teacher_li",
            "teacher123",
            "unit-test",
        ).user
        teacher_html = render_teacher_app(
            teacher,
            self.repo.teacher_dashboard(teacher["id"]),
        )

        self.assertIn("默认物理体系", html)
        self.assertIn("158 个知识节点", html)
        self.assertIn("6 册 / 27 章 / 125 节", html)
        self.assertIn("15 个能力标签", html)
        self.assertIn("18 个核心素养标签", html)
        self.assertIn("来源与版本", html)
        self.assertIn("安装或补齐默认体系", html)
        self.assertIn('data-action="install-default-taxonomy"', html)
        self.assertIn('data-taxonomy-search', html)
        self.assertIn('data-taxonomy-module', html)
        self.assertIn('class="taxonomy-badge default"', html)
        self.assertIn("核心素养管理", html)
        self.assertIn("admin-panel-fit taxonomy-admin-panel", html)
        self.assertIn("admin-panel-fit literacy-admin-panel", html)
        self.assertIn("pdf-export-panel admin-panel-fit", html)
        self.assertIn("audit-log-panel admin-panel-fit", html)
        self.assertIn('data-admin-form="literacy-tag"', html)
        self.assertIn(
            'data-admin-form="literacy-tag-update"',
            html,
        )
        self.assertIn("物理观念", html)
        self.assertIn("科学思维", html)
        self.assertIn("&lt;课程标准&gt;", html)
        self.assertNotIn("<课程标准>", html)
        self.assertNotIn("/private/source.pdf", teacher_html)


if __name__ == "__main__":
    unittest.main()
