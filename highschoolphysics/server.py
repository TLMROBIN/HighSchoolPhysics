import argparse
import getpass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import html

from .auth import AuthService, validate_password
from .db import (
    DEFAULT_DB_PATH,
    bootstrap_admin,
    connect,
    initialize_database,
    seed_demo_data,
)
from .errors import (
    DomainError,
    InvalidRequest,
    PasswordChangeRequired,
    PermissionDenied,
)
from .exporting import build_wrong_book_html
from .repository import PhysicsRepository, dumps
from .security import hash_password
from .sso import OidcExchangeError, exchange_oidc_code_for_claims


ASSET_DIR = Path(__file__).with_name("assets")
ASSET_VERSION = "20260717-student-polish-final"


def ensure_database(path, demo_mode=False):
    conn = connect(path)
    try:
        initialize_database(conn)
        if demo_mode:
            seed_demo_data(conn)
    finally:
        conn.close()


def escape(value):
    return html.escape("" if value is None else str(value))


def truthy(value):
    return str(value).lower() in ("1", "true", "yes", "on", "启用", "双向")


def render_layout(title, user, body, active=""):
    user_text = ""
    if user:
        role_label = {
            "student": "学生",
            "teacher": "教师",
            "admin": "管理员",
        }.get(user["role"], user["role"])
        user_text = (
            "<div class='session-chip'>"
            "<span>%s</span><strong>%s</strong><a href='/logout'>退出</a>"
            "</div>"
            % (escape(role_label), escape(user["display_name"]))
        )
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="/assets/app.css?v={asset_version}">
</head>
<body data-active="{active}">
  <header class="topbar">
    <a class="brand" href="/">高中物理闭环系统</a>
    {user_text}
  </header>
  <main>{body}</main>
  <script src="/assets/app.js?v={asset_version}"></script>
</body>
</html>""".format(
        title=escape(title),
        active=escape(active),
        asset_version=escape(ASSET_VERSION),
        user_text=user_text,
        body=body,
    )


def render_login_page(error="", demo_mode=False):
    error_html = "<p class='form-error'>%s</p>" % escape(error) if error else ""
    demo_accounts = ""
    if demo_mode:
        demo_accounts = """
    <div class="demo-accounts">
      <span>teacher_li / teacher123</span>
      <span>stu_1001 / student123</span>
      <span>admin / admin123</span>
    </div>"""
    body = """
<section class="login-shell">
  <form class="login-panel" method="post" action="/login">
    <h1>高中物理闭环系统</h1>
    {error_html}
    <label>账号<input name="username" autocomplete="username" required></label>
    <label>密码<input name="password" type="password" autocomplete="current-password" required></label>
    <button type="submit">进入</button>
    <a class="sso-login-link" href="/sso/login">使用统一平台登录</a>
    {demo_accounts}
  </form>
</section>""".format(
        error_html=error_html,
        demo_accounts=demo_accounts,
    )
    return render_layout("登录 - 高中物理闭环系统", None, body, "login")


def render_change_password_page(user, error=""):
    error_html = "<p class='form-error'>%s</p>" % escape(error) if error else ""
    body = """
<section class="login-shell">
  <form class="login-panel" method="post" action="/change-password">
    <h1>首次登录修改密码</h1>
    <p class="explain">新密码至少 10 位，并同时包含字母和数字。</p>
    {error_html}
    <label>当前临时密码<input name="current_password" type="password" autocomplete="current-password" required></label>
    <label>新密码<input name="new_password" type="password" minlength="10" autocomplete="new-password" required></label>
    <label>确认新密码<input name="confirm_password" type="password" minlength="10" autocomplete="new-password" required></label>
    <button type="submit">保存并继续</button>
  </form>
</section>""".format(error_html=error_html)
    return render_layout("修改密码 - 高中物理闭环系统", user, body, "change-password")


def _pill_list(items, class_name="pill"):
    if not items:
        return "<span class='%s muted'>未标注</span>" % class_name
    return "".join("<span class='%s'>%s</span>" % (class_name, escape(item["name"])) for item in items)


def _knowledge_link_pills(items):
    if not items:
        return "<span class='pill muted'>未标注知识点</span>"
    pills = []
    for item in items:
        pills.append(
            "<a class='pill' href='#knowledge-%s' data-knowledge-filter='%s'>%s</a>"
            % (
                escape(item["tag_id"]),
                escape(item["tag_id"]),
                escape(item.get("path_text") or item["name"]),
            )
        )
    return "".join(pills)


def _ability_link_pills(items):
    if not items:
        return "<span class='pill ability muted'>未标注能力</span>"
    return "".join(
        "<a class='pill ability' href='#ability-%s'>%s</a>"
        % (escape(item["tag_id"]), escape(item["name"]))
        for item in items
    )


def _related_question_link(question, target_question_ids):
    if question["id"] not in target_question_ids:
        return "<span>%s（未进入错题本）</span>" % escape(question["stem"])
    target_id = "wrong-question-%s" % question["id"]
    return (
        '<a href="#{target_id}" data-action="open-question" '
        'data-target-tab="wrong" data-target-id="{target_id}">{stem}</a>'
    ).format(
        target_id=escape(target_id),
        stem=escape(question["stem"]),
    )


def _manual_mastery_html(item):
    if not item.get("manual_mastery_level"):
        return ""
    note = ""
    if item.get("manual_mastery_note"):
        note = "｜%s" % escape(item["manual_mastery_note"])
    return (
        '<span class="manual-mastery">我的标记：%s%s</span>'
        % (escape(item["manual_mastery_level"]), note)
    )


def _render_module_tree(nodes, target_question_ids, focus_node_id=None):
    children = {}
    by_id = {}
    for node in nodes:
        by_id[node["id"]] = node
        children.setdefault(node["parent_id"], []).append(node)

    focus_path = set()
    current = by_id.get(focus_node_id)
    while current:
        focus_path.add(current["id"])
        current = by_id.get(current.get("parent_id"))

    def render_node(node):
        child_html = "".join(render_node(child) for child in children.get(node["id"], []))
        related = "".join(
            "<li>%s</li>"
            % _related_question_link(question, target_question_ids)
            for question in node["related_questions"]
        ) or "<li>暂时没有与测评关联的题目</li>"
        open_attr = " open" if node["id"] in focus_path else ""
        return """
        <details class="module-node {mastery_class}"{open_attr} id="knowledge-{id}" data-knowledge-id="{id}">
          <summary>
            <span>{name}</span>
            <small>{path}</small>
            <strong>{mastery}</strong>
          </summary>
          <div class="module-node-body">
            <p class="mastery-evidence"><span>系统依据：{evidence}</span>{manual}</p>
            <button type="button" class="secondary" data-action="focus-knowledge"
                    data-knowledge-id="{id}">在关系图中查看</button>
            <p>相关题目 {count} 题</p>
            <ul>{related}</ul>
            {children}
          </div>
        </details>
        """.format(
            open_attr=open_attr,
            id=escape(node["id"]),
            mastery_class=escape(node["mastery_css_class"]),
            name=escape(node["name"]),
            path=escape(node["path_text"]),
            mastery=escape(node["display_mastery_state"]),
            evidence=escape(node["mastery_evidence_text"]),
            manual=_manual_mastery_html(node),
            count=node["related_question_count"],
            related=related,
            children=child_html,
        )

    roots = children.get(None, [])
    if not roots:
        roots = [node for node in nodes if node["parent_id"] not in by_id]
    return "".join(render_node(root) for root in roots)


def _student_graph_edges(nodes, edges):
    node_ids = {node["id"] for node in nodes}
    values = []
    seen = set()

    def add(source, target, kind, relation):
        if source not in node_ids or target not in node_ids or source == target:
            return
        key = (source, target, kind)
        reverse = (target, source, kind)
        if key in seen or reverse in seen:
            return
        seen.add(key)
        values.append(
            {
                "source": source,
                "target": target,
                "kind": kind,
                "relation": relation,
            }
        )

    for node in nodes:
        if node.get("parent_id"):
            add(node["parent_id"], node["id"], "hierarchy", "教材层级")
    for edge in edges:
        add(
            edge["source_node_id"],
            edge["target_node_id"],
            "relation",
            edge.get("relation_type") or "知识关联",
        )
    return values


def _student_focus_node_ids(nodes, graph_edges, focus_node_id, limit=7):
    by_id = {node["id"]: node for node in nodes}
    if focus_node_id not in by_id:
        focus_node_id = nodes[0]["id"] if nodes else ""
    if not focus_node_id:
        return []

    neighbours = []
    for edge in graph_edges:
        other = ""
        if edge["source"] == focus_node_id:
            other = edge["target"]
        elif edge["target"] == focus_node_id:
            other = edge["source"]
        if other:
            neighbours.append(
                (
                    0 if edge["kind"] == "relation" else 1,
                    int(by_id[other].get("level") or 0),
                    by_id[other].get("stable_code") or "",
                    other,
                )
            )

    focus_parent_id = by_id[focus_node_id].get("parent_id")
    if focus_parent_id:
        for node in nodes:
            if node.get("parent_id") == focus_parent_id and node["id"] != focus_node_id:
                neighbours.append(
                    (
                        2,
                        int(node.get("level") or 0),
                        node.get("stable_code") or "",
                        node["id"],
                    )
                )

    ordered = [focus_node_id]
    for _priority, _level, _code, node_id in sorted(neighbours):
        if node_id not in ordered:
            ordered.append(node_id)
        if len(ordered) >= limit:
            break
    return ordered


GRAPH_SHORT_LABELS = {
    "匀变速直线运动的研究": "匀变速运动",
    "实验：探究加速度与力、质量的关系": "探究 a 与 F、m",
}


def _short_graph_label(value):
    text = str(value or "")
    if text in GRAPH_SHORT_LABELS:
        return GRAPH_SHORT_LABELS[text]
    return text if len(text) <= 9 else text[:8] + "…"


def _render_student_relation_graph(nodes, edges, focus_node_id):
    graph_edges = _student_graph_edges(nodes, edges)
    visible_ids = _student_focus_node_ids(nodes, graph_edges, focus_node_id)
    by_id = {node["id"]: node for node in nodes}
    positions = [
        (360, 180),
        (132, 82),
        (360, 68),
        (588, 82),
        (132, 278),
        (360, 292),
        (588, 278),
    ]
    position_by_id = {
        node_id: positions[index]
        for index, node_id in enumerate(visible_ids)
    }
    lines = []
    for edge in graph_edges:
        if edge["source"] not in position_by_id or edge["target"] not in position_by_id:
            continue
        start = position_by_id[edge["source"]]
        end = position_by_id[edge["target"]]
        lines.append(
            "<line x1=\"%s\" y1=\"%s\" x2=\"%s\" y2=\"%s\" "
            "data-edge-kind=\"%s\"><title>%s</title></line>"
            % (
                start[0],
                start[1],
                end[0],
                end[1],
                escape(edge["kind"]),
                escape(edge["relation"]),
            )
        )
    node_markup = []
    for index, node_id in enumerate(visible_ids):
        node = by_id[node_id]
        x, y = position_by_id[node_id]
        selected = index == 0
        aria_label = (
            "查看知识点 %s，当前%s，相关题目%s题"
            % (
                node["name"],
                node["display_mastery_state"],
                node["related_question_count"],
            )
        )
        node_markup.append(
            """
            <g class="graph-node {mastery_class}"
               data-knowledge-id="{id}" data-action="select-knowledge"
               data-detail-level="{detail_level}"
               role="button" tabindex="0" aria-label="{aria_label}"
               aria-current="{aria_current}"
               transform="translate({x},{y})">
              <circle r="{radius}"></circle>
              <text y="5">{short_name}</text>
              <title>{path}｜当前状态：{mastery}｜{evidence}｜相关题目 {count} 题</title>
            </g>
            """.format(
                id=escape(node["id"]),
                mastery_class=(
                    escape(node["mastery_css_class"])
                    + (" is-selected" if selected else "")
                ),
                detail_level="focus" if selected else "neighbor",
                aria_label=escape(aria_label),
                aria_current="true" if selected else "false",
                x=x,
                y=y,
                radius=32 if selected else 27,
                short_name=escape(_short_graph_label(node["name"])),
                path=escape(node["path_text"]),
                evidence=escape(node["mastery_evidence_text"]),
                mastery=escape(node["display_mastery_state"]),
                count=node["related_question_count"],
            )
        )
    search_options = "".join(
        '<option value="{path}" data-knowledge-id="{id}">{name}</option>'.format(
            path=escape(node["path_text"]),
            id=escape(node["id"]),
            name=escape(node["name"]),
        )
        for node in nodes
    )
    graph_payload = json.dumps(
        {
            "nodes": [
                {
                    "id": node["id"],
                    "name": node["name"],
                    "shortName": _short_graph_label(node["name"]),
                    "parentId": node.get("parent_id") or "",
                    "path": node["path_text"],
                    "masteryClass": node["mastery_css_class"],
                    "displayState": node["display_mastery_state"],
                    "evidence": node["mastery_evidence_text"],
                    "questionCount": node["related_question_count"],
                    "level": int(node.get("level") or 0),
                    "stableCode": node.get("stable_code") or "",
                }
                for node in nodes
            ],
            "edges": graph_edges,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    focus_path = by_id.get(focus_node_id, {}).get("path_text", "")
    return """
    <div class="graph-controls">
      <label class="graph-search-field">搜索知识点
        <input type="search" list="student-graph-options" value="{focus_path}"
               data-graph-search autocomplete="off"
               placeholder="输入章节或知识点名称">
      </label>
      <datalist id="student-graph-options">{search_options}</datalist>
      <div class="relation-toolbar" aria-label="知识图谱视图控制">
        <button type="button" class="secondary" data-action="graph-focus-current"
                data-graph-default-focus="{focus_id}">回到当前任务</button>
        <details class="graph-view-controls">
          <summary>调整视图</summary>
          <div>
            <button type="button" class="secondary" data-action="graph-zoom-in"
                    data-graph-detail-control aria-label="放大知识图谱">＋</button>
            <button type="button" class="secondary" data-action="graph-zoom-out"
                    data-graph-detail-control aria-label="缩小知识图谱">－</button>
            <button type="button" class="secondary" data-action="graph-reset"
                    data-graph-detail-control aria-label="复位知识图谱视图">复位</button>
          </div>
        </details>
      </div>
    </div>
    <svg class="student-relation-graph" viewBox="0 0 720 360"
         role="group" aria-label="可交互的知识关系图，默认显示当前知识点和直接相关知识点"
         data-layout="focus-radial-v1" data-graph-scale-state="medium"
         data-graph-default-focus="{focus_id}">
      <g class="graph-stage">{edges}{nodes}</g>
    </svg>
    <div class="graph-legend" aria-label="知识图谱图例">
      <span><i data-edge-kind="hierarchy" aria-hidden="true"></i>教材层级</span>
      <span><i data-edge-kind="relation" aria-hidden="true"></i>知识关联</span>
      <span>当前缩放 <output data-graph-zoom-status aria-live="polite">100%</output></span>
      <span>当前视图 <output data-graph-node-status aria-live="polite">7/7 个节点</output></span>
    </div>
    <details class="graph-related-list">
      <summary data-graph-related-summary>查看全部关联知识点</summary>
      <ul data-graph-related-list></ul>
    </details>
    <p class="graph-help">当前视图优先展示最相关节点；完整关联可在上方列表查看，搜索也可直接跳到任一知识点。</p>
    <script type="application/json" id="student-graph-data">{graph_payload}</script>
    """.format(
        focus_path=escape(focus_path),
        focus_id=escape(focus_node_id),
        search_options=search_options,
        edges="".join(lines),
        nodes="".join(node_markup),
        graph_payload=graph_payload,
    )


def _render_tag_mastery_summary(title, items):
    cards = []
    for item in items:
        related_count = len(item.get("related_questions") or [])
        if (
            item.get("calculated_mastery_state") == "未练习"
            and not item.get("manual_mastery_level")
            and related_count == 0
        ):
            continue
        cards.append(
            """
            <article class="tag-mastery-card {mastery_class}" id="{tag_type}-{tag_id}">
              <div><strong>{name}</strong><span>{code}</span></div>
              <p class="tag-student-explanation"><strong>和当前学习的关系：</strong>{description}</p>
              <p>计算：{calculated}</p>
              <p>{evidence}</p>
              <small>关联题目 {count} 题</small>
            </article>
            """.format(
                mastery_class=escape(item["mastery_css_class"]),
                tag_type=escape(item["tag_type"]),
                tag_id=escape(item["tag_id"]),
                name=escape(item["tag_name"]),
                code=escape(item.get("stable_code") or item["tag_id"]),
                description=escape(
                    item.get("description")
                    or "这个标签说明本题需要使用的物理方法或思维。"
                ),
                calculated=escape(item["calculated_mastery_state"]),
                evidence=escape(item["mastery_evidence_text"]),
                count=related_count,
            )
        )
    if not cards:
        cards.append("<article class='empty-state'>暂无标签</article>")
    return """
    <section class="tag-mastery-summary">
      <div class="panel-head"><h3>{title}</h3><span>只显示已有测评或题目依据的项目</span></div>
      <div class="tag-mastery-grid">{cards}</div>
    </section>
    """.format(title=escape(title), cards="".join(cards))


def _tag_navigation_card_id(module, question_id):
    return "nav-%s-%s-question-%s" % (
        module["tag_type"],
        module["tag_id"],
        question_id,
    )


def _render_tag_navigation_questions(module):
    cards = []
    wrong_by_question = {
        wrong["question_id"]: wrong for wrong in module["wrong_questions"]
    }
    redo_by_question = {
        wrong["question_id"]: wrong for wrong in module["redo_tasks"]
    }
    for question in module["related_questions"]:
        card_id = _tag_navigation_card_id(module, question["id"])
        wrong = wrong_by_question.get(question["id"])
        redo = redo_by_question.get(question["id"])
        wrong_link = ""
        if wrong:
            target_id = "wrong-question-%s" % wrong["question_id"]
            wrong_link = (
                '<a class="button-link secondary" href="#{target}" '
                'data-action="open-question" data-target-tab="wrong" '
                'data-target-id="{target}">查看错题</a>'
            ).format(target=escape(target_id))
        redo_link = ""
        if redo:
            target_id = "redo-question-%s" % redo["question_id"]
            redo_link = (
                '<a class="button-link secondary" href="#{target}" '
                'data-action="open-question" data-target-tab="redo" '
                'data-target-id="{target}">进入重做</a>'
            ).format(target=escape(target_id))
        cards.append(
            """
            <article class="tag-question-card" id="{card_id}">
              <h5>
                <a href="#{card_id}" data-action="open-question"
                   data-target-tab="graph" data-target-panel="{panel}"
                   data-target-id="{card_id}">{stem}</a>
              </h5>
              <p>{grade}｜{chapter}｜{difficulty}</p>
              <div class="tag-question-actions">{wrong_link}{redo_link}</div>
            </article>
            """.format(
                card_id=escape(card_id),
                panel=escape(module["tag_type"]),
                stem=escape(question["stem"]),
                grade=escape(question.get("grade", "")),
                chapter=escape(question.get("chapter", "")),
                difficulty=escape(question.get("difficulty", "")),
                wrong_link=wrong_link,
                redo_link=redo_link,
            )
        )
    if not cards:
        cards.append("<article class='empty-state'>暂无已发布关联题目</article>")
    return "".join(cards)


def _render_tag_navigation_panel(panel, title, modules, active=False):
    cards = []
    for module in modules:
        student_explanation = ""
        if panel in ("ability", "literacy"):
            student_explanation = (
                '<p class="tag-student-explanation"><strong>这表示：</strong>%s</p>'
                % escape(
                    module.get("description")
                    or "这个标签说明本题需要使用的物理方法或思维。"
                )
            )
        cards.append(
            """
            <article class="tag-navigation-card {mastery_class}" id="nav-{panel}-{tag_id}">
              <div class="tag-navigation-head">
                <div>
                  <h4>{name}</h4>
                  <small>{path}</small>
                </div>
                <strong>{state}</strong>
              </div>
              {student_explanation}
              <p class="mastery-evidence"><span>当前掌握证据</span><span>{evidence}</span></p>
              <div class="tag-navigation-counts">
                <span>相关题 {related_count}</span>
                <span>错题 {wrong_count}</span>
                <span>待重做 {redo_count}</span>
              </div>
              <div class="tag-question-list">{questions}</div>
            </article>
            """.format(
                mastery_class=escape(module["mastery_css_class"]),
                panel=escape(panel),
                tag_id=escape(module["tag_id"]),
                name=escape(module["tag_name"]),
                path=escape(module.get("path_text") or module.get("stable_code") or module["tag_id"]),
                state=escape(module["display_mastery_state"]),
                student_explanation=student_explanation,
                evidence=escape(module["mastery_evidence_text"]),
                related_count=len(module["related_questions"]),
                wrong_count=len(module["wrong_questions"]),
                redo_count=len(module["redo_tasks"]),
                questions=_render_tag_navigation_questions(module),
            )
        )
    if not cards:
        cards.append("<article class='empty-state'>暂无可导航标签</article>")
    active_class = " is-active" if active else ""
    hidden_attr = "" if active else " hidden"
    return """
    <section class="tag-family-panel{active_class}" id="tag-family-{panel}"
             data-tag-family-panel="{panel}" role="tabpanel"{hidden_attr}>
      <div class="panel-head"><h3>{title}</h3><span>查看相关题、错题、待重做和掌握依据</span></div>
      <div class="tag-navigation-grid">{cards}</div>
    </section>
    """.format(
        active_class=active_class,
        hidden_attr=hidden_attr,
        panel=escape(panel),
        title=escape(title),
        cards="".join(cards),
    )


def _render_student_navigation(dashboard):
    def has_evidence(module):
        return bool(
            module.get("related_questions")
            or module.get("wrong_questions")
            or module.get("redo_tasks")
            or module.get("manual_mastery_level")
            or module.get("calculated_mastery_state") != "未练习"
        )

    families = [
        (
            "knowledge",
            "知识导航",
            [item for item in dashboard.get("knowledge_navigation", []) if has_evidence(item)],
        ),
        (
            "ability",
            "能力导航",
            [item for item in dashboard.get("ability_navigation", []) if has_evidence(item)],
        ),
        (
            "literacy",
            "核心素养导航",
            [item for item in dashboard.get("literacy_navigation", []) if has_evidence(item)],
        ),
    ]
    buttons = "".join(
        '<button type="button" class="{active}" data-tag-family-tab="{panel}" '
        'role="tab" aria-controls="tag-family-{panel}" aria-selected="{selected}">{title}</button>'.format(
            active="is-active" if index == 0 else "",
            selected="true" if index == 0 else "false",
            panel=escape(panel),
            title=escape(title),
        )
        for index, (panel, title, _items) in enumerate(families)
    )
    panels = "".join(
        _render_tag_navigation_panel(panel, title, items, active=index == 0)
        for index, (panel, title, items) in enumerate(families)
    )
    return """
    <details class="student-navigation-modules">
      <summary>按学习依据浏览更多</summary>
      <div class="secondary-evidence-body">
        <div class="panel-head"><h2>知识、能力与核心素养</h2><span>只展示已有学习依据的项目</span></div>
        <div class="tag-family-tabs" role="tablist" aria-label="学习依据类型">{buttons}</div>
        {panels}
      </div>
    </details>
    """.format(buttons=buttons, panels=panels)


def _redo_status_label(value):
    return {
        "pending": "等待重做",
        "submitted": "已提交，等待教师复核",
        "done": "已完成",
    }.get(value, value or "等待重做")


def _render_wrong_cards(wrongs, id_prefix, student_id=""):
    cards = []
    for wrong in wrongs:
        options = ""
        if wrong["options"]:
            options = "<p class='options'>%s</p>" % "　".join(
                "%s. %s" % (escape(key), escape(value))
                for key, value in sorted(wrong["options"].items())
            )
        mastery = wrong.get("mastery_level") or "未标记"
        mastery_note = wrong.get("mastery_note") or ""
        card_id = "%s-question-%s" % (id_prefix, wrong["question_id"])
        redo_status = wrong.get("latest_redo_status") or wrong.get("redo_status") or "pending"
        redo_attempts = wrong.get("redo_attempts") or []
        redo_history = ""
        if redo_attempts:
            rows = []
            for attempt in redo_attempts:
                score_text = "未评分"
                if attempt.get("score") is not None:
                    score_text = "%s/%s" % (
                        attempt.get("score"),
                        attempt.get("max_score") or wrong["max_score"],
                    )
                rows.append(
                    "<li>%s：%s　本次答案：%s　%s</li>"
                    % (
                        escape(_redo_status_label(attempt.get("status") or "")),
                        escape(score_text),
                        escape(attempt.get("answer") or "空白"),
                        escape(attempt.get("feedback") or "等待教师复核"),
                    )
                )
            redo_history = (
                '<div class="redo-history"><strong>重做记录</strong><ul>%s</ul></div>'
                % "".join(rows)
            )
        mastery_buttons = "".join(
            '<button type="button" data-mastery="{level}" aria-pressed="{pressed}">{level}</button>'.format(
                level=escape(level),
                pressed="true" if mastery == level else "false",
            )
            for level in ("未掌握", "基本掌握", "已掌握", "需教师讲解")
        )
        feedback_id = "%s-feedback" % card_id
        is_redo = id_prefix == "redo"
        header_score = "<strong>%s/%s</strong>" % (
            wrong["score"],
            wrong["max_score"],
        )
        redo_score_context = ""
        if is_redo:
            header_score = ""
            redo_score_context = (
                '<p class="redo-score-context">原测评分数 '
                '<span>%s/%s</span></p>'
            ) % (wrong["score"], wrong["max_score"])
            submit_attributes = ""
            answer_review = (
                '<p class="redo-prior-answer">上次作答：%s</p>'
                '<p class="redo-guidance">请先独立完成这次作答。提交后可到错题本查看正确答案和解析。</p>'
            ) % escape(wrong.get("wrong_answer") or "空白")
            knowledge_names = "、".join(
                escape(tag.get("path_text") or tag["name"])
                for tag in wrong["knowledge_tags"]
            ) or "暂未标注知识点"
            tag_content = '<p class="redo-context">关联知识：%s</p>' % knowledge_names
            mastery_content = ""
            history_content = ""
            if wrong["options"]:
                submit_attributes = (
                    ' disabled aria-disabled="true" data-requires-answer'
                )
                options = ""
                answer_control = """
                  <fieldset class="redo-choice-fieldset" aria-describedby="{feedback_id}">
                    <legend>选择本次答案</legend>
                    <div class="redo-choice-grid">{choices}</div>
                  </fieldset>
                """.format(
                    feedback_id=escape(feedback_id),
                    choices="".join(
                        """
                        <label class="redo-choice-option">
                          <input type="radio" name="answer" value="{key}" required>
                          <span><strong>{key}.</strong> {value}</span>
                        </label>
                        """.format(key=escape(key), value=escape(value))
                        for key, value in sorted(wrong["options"].items())
                    ),
                )
            else:
                answer_control = """
                  <label>填写本次答案
                    <input name="answer" required autocomplete="off" inputmode="text"
                           aria-describedby="{feedback_id} redo-answer-hint-{wrong_id}">
                  </label>
                  <p class="redo-answer-hint" id="redo-answer-hint-{wrong_id}">请写出完整答案；数值题请同时填写单位。</p>
                """.format(
                    feedback_id=escape(feedback_id),
                    wrong_id=escape(wrong["id"]),
                )
            task_content = """
              <form data-student-form="redo-attempt" class="redo-submit-form"
                    aria-describedby="{feedback_id}"
                    data-redo-draft-key="hsp-redo-draft:{student_id}:{wrong_id}">
                <input type="hidden" name="wrong_question_id" value="{wrong_id}">
                {answer_control}
                <button type="submit"{submit_attributes}>提交重做</button>
              </form>
            """.format(
                feedback_id=escape(feedback_id),
                student_id=escape(student_id),
                wrong_id=escape(wrong["id"]),
                answer_control=answer_control,
                submit_attributes=submit_attributes,
            )
        else:
            answer_review = (
                "<p>我的答案：%s　正确答案：%s</p>"
                '<div class="answer-block">解析：%s</div>'
            ) % (
                escape(wrong.get("wrong_answer") or "空白"),
                escape(wrong["correct_answer"]),
                escape(wrong.get("analysis") or "暂无解析"),
            )
            tag_content = '<div class="tag-row">%s%s</div>' % (
                _knowledge_link_pills(wrong["knowledge_tags"]),
                _ability_link_pills(wrong["ability_tags"]),
            )
            mastery_content = """
              <div class="mastery-actions" data-wrong-id="{wrong_id}"
                   data-current-level="{mastery_value}" data-current-note="{mastery_note}">
                <span class="action-label">我现在：</span>{mastery_buttons}
              </div>
            """.format(
                wrong_id=escape(wrong["id"]),
                mastery_value="" if mastery == "未标记" else escape(mastery),
                mastery_note=escape(mastery_note),
                mastery_buttons=mastery_buttons,
            )
            history_content = redo_history
            if redo_status == "pending":
                task_content = (
                    '<button type="button" data-action="open-question" '
                    'data-target-tab="redo" data-target-id="redo-question-%s">去独立重做</button>'
                    % escape(wrong["question_id"])
                )
            elif redo_status == "submitted":
                task_content = '<p class="redo-followup">答案已提交，等待教师复核。</p>'
            else:
                task_content = '<p class="redo-followup">本题重做已完成，可以结合解析继续巩固。</p>'
        cards.append(
            """
        <article class="wrong-card" id="{card_id}" data-knowledge-ids="{knowledge_ids}">
          <div class="card-head"><span>{assessment}</span>{header_score}</div>
          <h2>{stem}</h2>
          {options}
          {answer_review}
          {tag_content}
          {redo_score_context}
          {mastery_content}
          <p class="card-action-feedback" id="{feedback_id}" role="status" aria-live="polite"></p>
          <p class="redo-status">重做状态：{redo_status}</p>
          {history_content}
          {task_content}
        </article>
            """.format(
                card_id=escape(card_id),
                knowledge_ids=" ".join(escape(tag["tag_id"]) for tag in wrong["knowledge_tags"]),
                assessment=escape(wrong["assessment_title"]),
                header_score=header_score,
                stem=escape(wrong["stem"]),
                options=options,
                answer_review=answer_review,
                tag_content=tag_content,
                redo_score_context=redo_score_context,
                mastery_content=mastery_content,
                feedback_id=escape(feedback_id),
                redo_status=escape(_redo_status_label(redo_status)),
                history_content=history_content,
                task_content=task_content,
            )
        )
    if not cards:
        if id_prefix == "redo":
            cards.append(
                """
                <article class="empty-state">
                  <h3>当前没有待重做题目</h3>
                  <p>可以回到知识图谱复习当前薄弱点，新的重做任务会自动出现在这里。</p>
                  <button type="button" class="secondary" data-tab="graph">查看知识图谱</button>
                </article>
                """
            )
        else:
            cards.append(
                """
                <article class="empty-state">
                  <h3>还没有错题记录</h3>
                  <p>完成并发布一次测评后，错题和解析会出现在这里。</p>
                  <button type="button" class="secondary" data-tab="graph">先浏览知识图谱</button>
                </article>
                """
            )
    return "".join(cards)


def _student_focus_node_id(dashboard):
    nodes = dashboard.get("knowledge_tree", [])
    by_id = {node["id"]: node for node in nodes}
    for wrong in dashboard.get("redo_queue", []) + dashboard.get("wrong_questions", []):
        candidates = [
            by_id[tag["tag_id"]]
            for tag in wrong.get("knowledge_tags", [])
            if tag.get("tag_id") in by_id
        ]
        if candidates:
            return max(
                candidates,
                key=lambda node: (
                    int(node.get("level") or 0),
                    len(node.get("path_text") or ""),
                ),
            )["id"]
    for state in ("需教师讲解", "未掌握", "有困难", "不熟练", "基本掌握"):
        for node in nodes:
            if node.get("display_mastery_state") == state:
                return node["id"]
    for node in nodes:
        if node.get("related_question_count"):
            return node["id"]
    return nodes[0]["id"] if nodes else ""


def _student_next_step(dashboard, focus_node):
    node_name = focus_node["name"] if focus_node else "知识图谱"
    redo_queue = dashboard.get("redo_queue", [])
    wrongs = dashboard.get("wrong_questions", [])
    if redo_queue:
        wrong = redo_queue[0]
        target_id = "redo-question-%s" % wrong["question_id"]
        return {
            "title": "先重做一道题",
            "description": "%s 中的错题与“%s”有关。先看清关系和依据，再完成重做。"
            % (wrong["assessment_title"], node_name),
            "action": (
                '<button type="button" data-action="open-question" data-target-tab="redo" '
                'data-target-id="%s">开始重做</button>' % escape(target_id)
            ),
        }
    if wrongs:
        wrong = wrongs[0]
        target_id = "wrong-question-%s" % wrong["question_id"]
        return {
            "title": "先复盘最近错题",
            "description": "从“%s”进入最近的错误依据，再决定是否需要重做或请教师讲解。"
            % node_name,
            "action": (
                '<button type="button" data-action="open-question" data-target-tab="wrong" '
                'data-target-id="%s">查看错题</button>' % escape(target_id)
            ),
        }
    return {
        "title": "先认识一个知识关系",
        "description": "目前还没有已发布的错题。可以先从“%s”出发，熟悉它与前后知识的关系。"
        % node_name,
        "action": (
            '<button type="button" data-action="select-knowledge" data-knowledge-id="%s">查看当前知识点</button>'
            % escape(focus_node["id"] if focus_node else "")
        ),
    }


def render_student_app(user, dashboard):
    wrong_cards = _render_wrong_cards(
        dashboard["wrong_questions"],
        "wrong",
        student_id=user["id"],
    )
    redo_cards = _render_wrong_cards(
        dashboard["redo_queue"],
        "redo",
        student_id=user["id"],
    )
    graph_nodes = dashboard["knowledge_tree"]
    focus_node_id = _student_focus_node_id(dashboard)
    graph_by_id = {node["id"]: node for node in graph_nodes}
    focus_node = graph_by_id.get(focus_node_id)
    next_step = _student_next_step(dashboard, focus_node)
    target_question_ids = {
        wrong["question_id"] for wrong in dashboard["wrong_questions"]
    }
    module_tree = _render_module_tree(
        graph_nodes,
        target_question_ids,
        focus_node_id=focus_node_id,
    )
    relation_graph = _render_student_relation_graph(
        graph_nodes,
        dashboard["knowledge_edges"],
        focus_node_id,
    )

    related_panels = []
    for node in graph_nodes:
        questions = "".join(
            "<li>%s</li>"
            % _related_question_link(question, target_question_ids)
            for question in node["related_questions"]
        ) or "<li>暂时没有与已发布测评关联的题目</li>"
        manual_level = node.get("manual_mastery_level") or ""
        mastery_buttons = "".join(
            '<button type="button" data-action="mark-knowledge" data-level="{level}" '
            'aria-pressed="{pressed}">{level}</button>'.format(
                level=escape(level),
                pressed="true" if manual_level == level else "false",
            )
            for level in ("未掌握", "基本掌握", "已掌握", "需教师讲解")
        )
        active = node["id"] == focus_node_id
        related_panels.append(
            """
            <section class="related-question-panel{active_class}" data-related-for="{id}"{hidden_attr}>
              <div class="selected-knowledge-head">
                <div><p class="selected-path">{path}</p><h3>{name}</h3></div>
                <strong class="mastery-status">当前状态：{mastery}</strong>
              </div>
              <p class="mastery-evidence"><span>为什么是这个状态：{evidence}</span>{manual}</p>
              {evidence_detail}
              <div class="mastery-actions graph-mark-actions" data-knowledge-id="{id}"
                   data-current-level="{manual_level}" data-current-note="{manual_note}">
                <span class="action-label">我现在：</span>{mastery_buttons}
              </div>
              <p class="card-action-feedback" role="status" aria-live="polite"></p>
              <div class="related-questions"><h4>相关题目</h4><ul>{questions}</ul></div>
            </section>
            """.format(
                active_class=" is-active" if active else "",
                hidden_attr="" if active else " hidden",
                id=escape(node["id"]),
                name=escape(node["name"]),
                path=escape(node["path_text"]),
                mastery=escape(node["display_mastery_state"]),
                evidence=escape(node["mastery_evidence_text"]),
                evidence_detail=(
                    '<details class="mastery-evidence-detail"><summary>查看计算依据</summary>'
                    '<p>%s</p></details>' % escape(node["mastery_evidence_detail_text"])
                    if node.get("mastery_evidence_detail_text")
                    and node["mastery_evidence_detail_text"] != node["mastery_evidence_text"]
                    else ""
                ),
                manual=_manual_mastery_html(node),
                manual_level=escape(manual_level),
                manual_note=escape(node.get("manual_mastery_note") or ""),
                mastery_buttons=mastery_buttons,
                questions=questions,
            )
        )

    wrong_filter_options = "".join(
        '<option value="{path}" data-knowledge-id="{id}">{name}</option>'.format(
            path=escape(node["path_text"]),
            id=escape(node["id"]),
            name=escape(node["name"]),
        )
        for node in graph_nodes
    )
    ability_mastery = _render_tag_mastery_summary(
        "能力掌握",
        dashboard.get("ability_mastery", []),
    )
    literacy_mastery = _render_tag_mastery_summary(
        "核心素养掌握",
        dashboard.get("literacy_mastery", []),
    )
    student_navigation = _render_student_navigation(dashboard)

    assessment_cards = []
    assessment_status = {
        "draft": "准备中",
        "scheduled": "待开始",
        "in_progress": "进行中",
        "submitted": "已提交",
        "published": "已发布",
    }
    for item in dashboard["assessments"]:
        score = "—"
        if item["score"] is not None:
            score = "%s/%s" % (item["score"], item["max_score"])
        assessment_wrongs = [
            wrong for wrong in dashboard["wrong_questions"]
            if wrong.get("assessment_id") == item["id"]
        ]
        assessment_redos = [
            wrong for wrong in dashboard["redo_queue"]
            if wrong.get("assessment_id") == item["id"]
        ]
        weak_point_names = []
        for wrong in assessment_wrongs:
            for tag in wrong.get("knowledge_tags", []):
                name = tag.get("name") or tag.get("path_text")
                if name and name not in weak_point_names:
                    weak_point_names.append(name)
        evidence_line = "薄弱点：%s · 丢分题 %s 道" % (
            "、".join(weak_point_names[:2]) or "本次未发现薄弱点",
            len(assessment_wrongs),
        )
        if assessment_redos:
            first_wrong = assessment_redos[0]
            action = (
                '<button type="button" data-action="open-question" data-target-tab="redo" '
                'data-target-id="redo-question-%s">继续重做</button>'
                % escape(first_wrong["question_id"])
            )
        elif assessment_wrongs:
            first_wrong = assessment_wrongs[0]
            action = (
                '<button type="button" class="secondary" data-action="open-question" '
                'data-target-tab="wrong" data-target-id="wrong-question-%s">查看错题</button>'
                % escape(first_wrong["question_id"])
            )
        else:
            action = (
                '<button type="button" class="secondary" data-tab="graph">查看知识图谱</button>'
            )
        published_date = (item.get("published_at") or "")[:10] or "日期待更新"
        assessment_cards.append(
            """
            <article class="assessment-card">
              <div class="assessment-card-head">
                <div><p>{date}</p><h3>{title}</h3></div>
                <strong class="assessment-score">{score}</strong>
              </div>
              <p>{status}｜待处理错题 {wrong_count} 道</p>
              <p class="assessment-evidence">{evidence_line}</p>
              <div class="assessment-actions">
                <button type="button" class="secondary" data-tab="graph">查看当前知识图谱</button>
                {action}
              </div>
            </article>
            """.format(
                date=escape(published_date),
                title=escape(item["title"]),
                score=escape(score),
                status=escape(assessment_status.get(item["status"], item["status"])),
                wrong_count=len(assessment_redos),
                evidence_line=escape(evidence_line),
                action=action,
            )
        )
    if assessment_cards:
        assessment_content = '<div class="assessment-list">%s</div>' % "".join(assessment_cards)
    else:
        assessment_content = """
        <article class="empty-state">
          <h3>还没有已发布的测评</h3>
          <p>教师发布测评结果后，这里会显示得分和对应的学习依据。</p>
          <button type="button" class="secondary" data-tab="graph">先浏览知识图谱</button>
        </article>
        """

    recent_title = (
        dashboard["assessments"][0]["title"]
        if dashboard.get("assessments")
        else "尚无已发布测评"
    )
    body = """
<section class="student-app">
  <div id="action-status" class="action-status student-action-status" role="status"
       aria-live="polite" aria-atomic="true" hidden>
    <span data-status-message></span>
    <button type="button" class="secondary" data-action="undo-student-action" hidden>撤销刚才修改</button>
  </div>

  <section class="student-tab is-active" id="student-panel-graph" data-tab-panel="graph"
           role="tabpanel" aria-labelledby="student-tab-graph">
    <header class="student-start">
      <div class="student-start-copy">
        <p class="student-greeting">{name}，今天先解决什么</p>
        <h1>{task_title}</h1>
        <p>{task_description}</p>
        <div class="student-task-meta" aria-label="当前学习概况">
          <span>待重做 {redo_count}</span>
          <span>错题 {wrong_count}</span>
          <span>最近测评：{recent_title}</span>
        </div>
      </div>
      <div class="student-primary-action">{task_action}</div>
    </header>

    <section class="student-graph-workspace" id="student-graph-workspace" tabindex="-1">
      <div class="panel-head">
        <div><h2>从薄弱点出发</h2><p>关系图优先展示当前知识点和最相关知识，完整关联可在图下展开。</p></div>
        <a class="button-link secondary" href="#module-browser">按教材浏览</a>
      </div>
      {relation_graph}
      <div class="related-question-list">{related_panels}</div>
    </section>

    <details class="module-browser" id="module-browser">
      <summary>按教材浏览完整体系</summary>
      <div class="module-browser-body">
        <div class="panel-head">
          <div><h2>教材目录</h2><p>默认只展开当前知识点所在路径，其余内容按需打开。</p></div>
          <button type="button" class="secondary" data-action="collapse-modules">全部收起</button>
        </div>
        <div class="module-tree">{module_tree}</div>
      </div>
    </details>

    <details class="secondary-evidence">
      <summary>查看能力与核心素养依据</summary>
      <div class="secondary-evidence-body">
        <p class="student-evidence-guide">能力说明“这道题要怎么想、怎么做”；核心素养说明“长期要形成什么物理思维”。</p>
        <div class="tag-mastery-sections">{ability_mastery}{literacy_mastery}</div>
      </div>
    </details>
    {student_navigation}
  </section>

  <section class="student-tab" id="student-panel-wrong" data-tab-panel="wrong"
           role="tabpanel" aria-labelledby="student-tab-wrong" hidden>
    <div class="panel-head"><div><h2>错题本</h2><p>先看需要处理的错题；找特定知识点时再打开筛选。</p></div></div>
    <div class="wrong-grid">{wrong_cards}</div>
    <details class="wrong-filter-tools">
      <summary>按知识点筛选</summary>
      <div class="wrong-filter-body">
        <label>搜索教材、章节或知识点
          <input type="search" list="wrong-knowledge-options" data-wrong-filter-search
                 autocomplete="off" placeholder="例如：必修第一册 > 运动和力的关系">
        </label>
        <datalist id="wrong-knowledge-options">{wrong_filter_options}</datalist>
        <button type="button" class="secondary" data-action="clear-wrong-filter">清除筛选</button>
        <p data-wrong-filter-status role="status" aria-live="polite">当前显示全部错题</p>
        <p class="empty-state" data-wrong-filter-empty hidden>这个知识点下暂时没有错题。可以清除筛选查看其他错题。</p>
      </div>
    </details>
  </section>

  <section class="student-tab" id="student-panel-redo" data-tab-panel="redo"
           role="tabpanel" aria-labelledby="student-tab-redo" hidden>
    <div class="panel-head"><div><h2>待重做</h2><p>提交后会保留答案，并显示教师复核状态。</p></div></div>
    <div class="wrong-grid">{redo_cards}</div>
  </section>

  <section class="student-tab" id="student-panel-recent" data-tab-panel="recent"
           role="tabpanel" aria-labelledby="student-tab-recent" hidden>
    <div class="panel-head"><div><h2>最近测评</h2><p>测评结果会继续连接到图谱和错题重做。</p></div></div>
    {assessment_content}
  </section>

  <nav class="bottom-nav" role="tablist" aria-label="学生学习导航">
    <button type="button" class="is-active" id="student-tab-graph" data-tab="graph"
            role="tab" aria-controls="student-panel-graph" aria-selected="true">知识图谱</button>
    <button type="button" id="student-tab-wrong" data-tab="wrong"
            role="tab" aria-controls="student-panel-wrong" aria-selected="false">错题本</button>
    <button type="button" id="student-tab-redo" data-tab="redo"
            role="tab" aria-controls="student-panel-redo" aria-selected="false">待重做</button>
    <button type="button" id="student-tab-recent" data-tab="recent"
            role="tab" aria-controls="student-panel-recent" aria-selected="false">最近测评</button>
  </nav>
</section>""".format(
        name=escape(user["display_name"]),
        task_title=escape(next_step["title"]),
        task_description=escape(next_step["description"]),
        task_action=next_step["action"],
        redo_count=len(dashboard.get("redo_queue", [])),
        wrong_count=len(dashboard.get("wrong_questions", [])),
        recent_title=escape(recent_title),
        module_tree=module_tree,
        relation_graph=relation_graph,
        related_panels="".join(related_panels),
        ability_mastery=ability_mastery,
        literacy_mastery=literacy_mastery,
        student_navigation=student_navigation,
        wrong_filter_options=wrong_filter_options,
        wrong_cards=wrong_cards,
        redo_cards=redo_cards,
        assessment_content=assessment_content,
    )
    return render_layout("学生端 - 高中物理闭环系统", user, body, "student")


def _render_graph(nodes, edges):
    positions = {
        "kn-mechanics": (82, 46),
        "kn-newton": (230, 58),
        "kn-newton-2": (380, 66),
        "kn-work": (380, 154),
        "kn-kinematics": (226, 160),
    }
    edge_lines = []
    for edge in edges:
        start = positions.get(edge["source_node_id"], (60, 60))
        end = positions.get(edge["target_node_id"], (280, 120))
        edge_lines.append(
            "<line x1='%s' y1='%s' x2='%s' y2='%s'></line>"
            "<text x='%s' y='%s'>%s</text>"
            % (
                start[0],
                start[1],
                end[0],
                end[1],
                (start[0] + end[0]) / 2,
                (start[1] + end[1]) / 2 - 4,
                escape(edge["relation_type"]),
            )
        )
    node_shapes = []
    for node in nodes:
        x, y = positions.get(node["id"], (60 + node["level"] * 90, 60 + len(node_shapes) * 22))
        node_shapes.append(
            "<g><circle cx='%s' cy='%s' r='25'></circle><text x='%s' y='%s'>%s</text></g>"
            % (x, y, x, y + 4, escape(node["name"]))
        )
    return '<svg class="knowledge-graph" viewBox="0 0 480 220" role="img">%s%s</svg>' % (
        "".join(edge_lines),
        "".join(node_shapes),
    )


def _percent(value):
    return "%.1f%%" % (float(value or 0) * 100)


def _render_mastery_state_bar(state_counts):
    labels = ("未练习", "未掌握", "有困难", "不熟练", "已掌握")
    class_by_label = {
        "未练习": "mastery-state-unpracticed",
        "未掌握": "mastery-state-not-mastered",
        "有困难": "mastery-state-difficult",
        "不熟练": "mastery-state-rough",
        "已掌握": "mastery-state-mastered",
    }
    total = max(1, sum(int(state_counts.get(label, 0)) for label in labels))
    segments = []
    for label in labels:
        count = int(state_counts.get(label, 0))
        width = max(4, round(count / total * 100)) if count else 0
        segments.append(
            """
            <span class="analytics-state-segment {css}" style="width:{width}%">
              {label} {count}
            </span>
            """.format(
                css=escape(class_by_label[label]),
                width=width,
                label=escape(label),
                count=count,
            )
        )
    return '<div class="analytics-state-bar">%s</div>' % "".join(segments)


def _render_mastery_student_details(students):
    if not students:
        return ""
    rows = []
    for student in students:
        rows.append(
            """
            <li class="{css}">
              <strong>{student}</strong>
              <span>{state}</span>
              <span>尝试 {eligible}</span>
              <span>正确率 {correct_rate}</span>
              <span>错误 {wrong}</span>
              <span>空白 {blank}</span>
            </li>
            """.format(
                css=escape(student["mastery_css_class"]),
                student=escape(
                    "%s %s" % (
                        student.get("student_no", ""),
                        student.get("student_name", ""),
                    )
                ),
                state=escape(student["mastery_state"]),
                eligible=student["eligible_attempts"],
                correct_rate=_percent(student["correct_rate"]),
                wrong=student["wrong_count"],
                blank=student["blank_count"],
            )
        )
    return """
    <details class="analytics-student-details">
      <summary>学生明细</summary>
      <ul>{rows}</ul>
    </details>
    """.format(rows="".join(rows))


def _render_mastery_analytics_table(items, include_students=False):
    if not items:
        return (
            "<table class='mastery-analytics-table'><tbody>"
            "<tr><td>暂无已发布掌握度数据</td></tr>"
            "</tbody></table>"
        )
    rows = []
    for item in items:
        rows.append(
            """
            <tr class="{css}">
              <td>
                <strong>{name}</strong>
                <span>{code}</span>
                <small>{path}</small>
                {students}
              </td>
              <td>{bar}</td>
              <td>{eligible}</td>
              <td>{correct}</td>
              <td>{wrong}</td>
              <td>{blank}</td>
              <td>{correct_rate}</td>
              <td>{error_rate}</td>
              <td>{blank_rate}</td>
            </tr>
            """.format(
                css=escape(item["mastery_css_class"]),
                name=escape(item["tag_name"]),
                code=escape(item.get("stable_code") or item["tag_id"]),
                path=escape(item.get("path_text") or ""),
                students=(
                    _render_mastery_student_details(item.get("students", []))
                    if include_students
                    else ""
                ),
                bar=_render_mastery_state_bar(item["state_counts"]),
                eligible=item["eligible_attempts"],
                correct=item["correct_count"],
                wrong=item["wrong_count"],
                blank=item["blank_count"],
                correct_rate=_percent(item["correct_rate"]),
                error_rate=_percent(item["error_rate"]),
                blank_rate=_percent(item["blank_rate"]),
            )
        )
    return """
    <table class="mastery-analytics-table">
      <thead>
        <tr>
          <th>标签</th><th>状态分布</th><th>尝试</th><th>正确</th>
          <th>错误</th><th>空白</th><th>正确率</th><th>错误率</th><th>空白率</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """.format(rows="".join(rows))


def _render_teacher_mastery_analytics(analytics):
    family_labels = (
        ("knowledge", "知识掌握"),
        ("ability", "能力掌握"),
        ("literacy", "核心素养掌握"),
    )
    class_name = (analytics.get("class") or {}).get("name", "当前班级")
    assessment = analytics.get("assessment") or {}
    grade_comparison = analytics.get("grade_comparison", {})
    sections = []
    comparison_sections = []
    for family, label in family_labels:
        sections.append(
            """
            <section class="analytics-family" data-analytics-family="{family}">
              <h3>{label}</h3>
              {table}
            </section>
            """.format(
                family=escape(family),
                label=escape(label),
                table=_render_mastery_analytics_table(
                    analytics.get(family, []),
                    include_students=True,
                ),
            )
        )
        comparison_sections.append(
            """
            <section class="analytics-family aggregate-only">
              <h3>{label}</h3>
              {table}
            </section>
            """.format(
                label=escape(label),
                table=_render_mastery_analytics_table(
                    grade_comparison.get(family, []),
                    include_students=False,
                ),
            )
        )
    return """
    <section class="panel span-2 phase2g-analytics">
      <div class="panel-head">
        <div>
          <h2>Phase 2G 掌握度分析</h2>
          <p class="explain">班级掌握图谱基于 Phase 2E 确定性指标；错误率按 eligible tagged attempts 计算，空白率单独展示。</p>
        </div>
        <span>{class_name} / {assessment}</span>
      </div>
      <div class="analytics-block">
        <h3>班级掌握图谱</h3>
        {sections}
      </div>
      <div class="analytics-block aggregate-only">
        <h3>年级均值对比</h3>
        <p class="explain">只展示 {grade} 年级聚合数据，不暴露其他班学生明细。</p>
        {comparison_sections}
      </div>
    </section>
    """.format(
        class_name=escape(class_name),
        assessment=escape(assessment.get("title") or ""),
        sections="".join(sections),
        grade=escape(grade_comparison.get("grade") or ""),
        comparison_sections="".join(comparison_sections),
    )


def _render_admin_mastery_analytics(analytics):
    family_labels = (
        ("knowledge", "知识掌握"),
        ("ability", "能力掌握"),
        ("literacy", "核心素养掌握"),
    )
    grade_sections = []
    grade_data = analytics.get("grades", [])
    for grade in grade_data:
        family_sections = []
        for family, label in family_labels:
            family_sections.append(
                """
                <section class="analytics-family aggregate-only">
                  <h4>{label}</h4>
                  {table}
                </section>
                """.format(
                    label=escape(label),
                    table=_render_mastery_analytics_table(
                        grade.get(family, []),
                        include_students=False,
                    ),
                )
            )
        grade_sections.append(
            """
            <section class="analytics-grade-card aggregate-only" data-admin-analytics-grade="{grade}">
              <div class="analytics-grade-head">
                <h3>{grade}</h3>
                <span>{class_count} 个班 / {student_count} 名学生</span>
              </div>
              <h4>聚合标签掌握</h4>
              {families}
            </section>
            """.format(
                grade=escape(grade.get("grade") or ""),
                class_count=grade.get("class_count", 0),
                student_count=grade.get("student_count", 0),
                families="".join(family_sections),
            )
        )

    trend_rows = []
    for item in analytics.get("trends", []):
        trend_rows.append(
            """
            <tr>
              <td>{grade}</td><td>{title}</td><td>{scheduled_at}</td>
              <td>{class_count}</td><td>{student_count}</td><td>{rate}</td>
            </tr>
            """.format(
                grade=escape(item.get("grade") or ""),
                title=escape(item.get("title") or ""),
                scheduled_at=escape(item.get("scheduled_at") or ""),
                class_count=item.get("class_count", 0),
                student_count=item.get("student_count", 0),
                rate=_percent(item.get("average_score_rate")),
            )
        )
    if not trend_rows:
        trend_rows.append("<tr><td colspan='6'>暂无已发布年级趋势数据</td></tr>")

    # 过滤未分班年级：仅保留 class_count > 0 且 student_count > 0 的年级
    visible_grade_html = []
    for grade, html in zip(grade_data, grade_sections):
        if (grade.get("class_count") or 0) > 0 and (grade.get("student_count") or 0) > 0:
            visible_grade_html.append(html)
    if not visible_grade_html:
        visible_grades_html = "<p class='explain'>暂无已分班的年级掌握数据</p>"
    else:
        visible_grades_html = "".join(visible_grade_html)

    grade_columns_block = (
        f'<div class="mastery-grade-columns">{visible_grades_html}</div>'
    )

    trend_block = """
    <div class="analytics-block">
      <h3>年级掌握趋势</h3>
      <table class="mastery-analytics-table">
        <thead>
          <tr><th>年级</th><th>测评</th><th>时间</th><th>班级数</th><th>学生数</th><th>平均得分率</th></tr>
        </thead>
        <tbody>{trend_rows}</tbody>
      </table>
    </div>
    """.format(trend_rows="".join(trend_rows))

    return trend_block, grade_columns_block


def render_teacher_app(user, dashboard):
    assessments = dashboard.get("assessments", []) or []
    if not assessments:
        # 零 assessment 教师:admin 刚导入 / 刚分配班级的老师,直接给出空状态页,
        # 避免对 assessment[id/title/...] 取值导致 IndexError 或 KeyError。
        # 保留头部 / 退出按钮(由 render_layout 渲染),引导先去组卷。
        # 不渲染 mastery_analytics / diagnostics / class_mastery_analytics 等依赖
        # assessment_id 的区域；保留最小组卷和题库录入入口，避免教师无下一步。
        class_hint = (
            "可用班级：%s" % "、".join(
                escape(item.get("name") or item.get("id") or "")
                for item in dashboard.get("classes", [])
            )
            if dashboard.get("classes")
            else "当前尚未分配班级，可先录入题目或联系管理员分配班级。"
        )
        empty_object_json = escape(dumps({}))
        default_answer_json = escape(dumps({"answer": "A"}))
        paper_items_json = escape(
            dumps(
                [
                    {
                        "question_id": "请先录入题目后填写题目ID",
                        "score": 4,
                    }
                ],
            )
        )
        body = """
<section class="teacher-app">
  <div id="action-status" class="action-status" aria-live="polite">等待操作</div>
  <div class="workspace-grid">
    <section class="panel span-2">
      <div class="panel-head">
        <h1>测评批次</h1>
        <span>批改并发布会先检查低置信答题卡,未复核时不会发布。</span>
      </div>
      <article class="empty-state" data-empty-state="no-assessment">
        <p><strong>暂无测评</strong></p>
        <p>系统还没有为本教师或所任课班级创建任何测评批次。</p>
        <p>请先在下方「组卷与答题卡」中创建试卷,或联系管理员导入测评数据。</p>
      </article>
    </section>
    <section class="panel">
      <h2>LLM 候选审核</h2>
      <p class="explain">LLM 候选审核用于把题目先交给模型生成知识点/能力标签建议和核心素养标签建议,教师确认后才写入正式题库。</p>
      <table><thead><tr><th>题目</th><th>知识标签</th><th>能力标签</th><th>核心素养标签</th><th>操作</th></tr></thead>
        <tbody><tr><td colspan='5'><button data-action='generate-candidate' data-question-id='q-newton-1'>生成 q-newton-1 候选</button></td></tr></tbody>
      </table>
    </section>
    <section class="panel">
      <h2>答题卡复核</h2>
      <table><thead><tr><th>学生</th><th>题目</th><th>识别</th><th>置信度</th><th>操作</th></tr></thead>
        <tbody><tr><td colspan='5'>无待复核答题卡</td></tr></tbody>
      </table>
    </section>
    <section class="panel span-2">
      <div class="panel-head">
        <h2>组卷与答题卡</h2>
        <p class="explain">尚未创建测评,可在此处先组卷。创建测评后,系统会自动启用错题本、PDF 批改、年级掌握趋势等模块。</p>
      </div>
      <p class="explain">{class_hint}</p>
      <div class="phase2d-form-grid">
        <form data-teacher-form="paper-assembly">
          <h3>最小组卷</h3>
          <label>试卷标题<input name="title" value="新建物理小测" required></label>
          <label>来源<input name="source" value="校本组卷" required></label>
          <label>题目 JSON<textarea name="question_items" data-json="true" required>{paper_items_json}</textarea></label>
          <button type="submit">生成试卷</button>
        </form>
        <form data-teacher-form="question" class="question-edit-form">
          <h3>题库录入</h3>
          <label>题干<textarea name="stem" required></textarea></label>
          <label>选项 JSON<textarea name="options" data-json="true" data-default-json="{{}}">{empty_object}</textarea></label>
          <label>答案 JSON<textarea name="answer" data-json="true" data-default-json="{{}}">{default_answer}</textarea></label>
          <label>解析<textarea name="analysis"></textarea></label>
          <label>题型<input name="question_type" value="short_answer" required></label>
          <label>来源<input name="source" value="教师录入" required></label>
          <label>年级<input name="grade" value="高二" required></label>
          <label>章节<input name="chapter" required></label>
          <label>难度<input name="difficulty" value="medium" required></label>
          <button type="submit">保存题目</button>
        </form>
      </div>
    </section>
  </div>
</section>""".format(
            class_hint=class_hint,
            paper_items_json=paper_items_json,
            empty_object=empty_object_json,
            default_answer=default_answer_json,
        )
        return render_layout("教师端 - 高中物理闭环系统", user, body, "teacher")
    assessment = assessments[0]
    candidate_rows = []
    for candidate in dashboard["pending_candidates"]:
        candidate_rows.append(
            """
            <tr>
              <td>{stem}</td>
              <td>{knowledge}</td>
              <td>{ability}</td>
              <td>{literacy}</td>
              <td><button data-action="approve-candidate" data-candidate-id="{candidate_id}">确认</button></td>
            </tr>
            """.format(
                stem=escape(candidate.get("stem", ""))[:80],
                knowledge=_pill_list(candidate["knowledge_tags"]),
                ability=_pill_list(candidate["ability_tags"], "pill ability"),
                literacy=_pill_list(candidate.get("literacy_tags", []), "pill literacy"),
                candidate_id=escape(candidate["id"]),
            )
        )
    if not candidate_rows:
        candidate_rows.append(
            "<tr><td colspan='5'><button data-action='generate-candidate' data-question-id='q-newton-1'>生成 q-newton-1 候选</button></td></tr>"
        )

    review_rows = []
    for item in dashboard["review_items"]:
        review_rows.append(
            """
            <tr>
              <td>{student}</td><td>{stem}</td><td>{answer}</td><td>{confidence}</td>
              <td><button data-action="resolve-review" data-response-id="{response_id}" data-answer="C">确认 C</button></td>
            </tr>
            """.format(
                student=escape(item["student_name"]),
                stem=escape(item["stem"])[:80],
                answer=escape(item["final_answer"]),
                confidence=escape(item["original_confidence"]),
                response_id=escape(item["id"]),
            )
        )
    if not review_rows:
        review_rows.append("<tr><td colspan='5'>无待复核答题卡</td></tr>")

    diag = dashboard["diagnostics"]
    knowledge_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%.1f%%</td></tr>"
        % (escape(item["name"]), item["wrong_count"], item["error_rate"] * 100)
        for item in diag["knowledge_error_rates"]
    )
    ability_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%.1f%%</td></tr>"
        % (escape(item["name"]), item["wrong_count"], item["error_rate"] * 100)
        for item in diag["ability_error_rates"]
    )
    node_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (
            escape(node["stable_code"]),
            escape(node["name"]),
            escape(node["level"]),
            escape(node["source"]),
        )
        for node in dashboard["knowledge_nodes"]
    )
    class_options = "".join(
        "<option value='%s'>%s</option>" % (escape(item["id"]), escape(item["name"]))
        for item in dashboard["classes"]
    )
    student_options = "".join(
        "<option value='%s'>%s %s</option>"
        % (escape(item["id"]), escape(item["student_no"] or ""), escape(item["display_name"]))
        for item in dashboard["students"]
    )
    question_rows = []
    question_options = []
    for question in dashboard.get("question_bank", []):
        knowledge_ids = " ".join(question.get("knowledge_tag_ids", []))
        ability_ids = " ".join(question.get("ability_tag_ids", []))
        literacy_ids = " ".join(question.get("literacy_tag_ids", []))
        question_options.append(
            "<option value='%s'>%s</option>"
            % (escape(question["id"]), escape(question["stem"][:48]))
        )
        question_rows.append(
            """
            <tr data-question-row data-grade="{grade}" data-chapter="{chapter}"
                data-quality-status="{quality_status}" data-review-status="{review_status}"
                data-source-confidence="{source_confidence}" data-knowledge-ids="{knowledge_ids}"
                data-ability-ids="{ability_ids}" data-literacy-ids="{literacy_ids}">
              <td>{stem}</td><td>{paper}</td><td>{number}</td>
              <td>{grade}</td><td>{chapter}</td><td>{difficulty}</td>
              <td><span class="provenance-badge">{status}</span></td>
              <td><button data-action="generate-candidate" data-question-id="{question_id}">生成候选</button></td>
            </tr>
            """.format(
                question_id=escape(question["id"]),
                stem=escape(question["stem"][:80]),
                paper=escape(question.get("original_paper_title") or question.get("source") or "教师录入"),
                number=escape(question.get("original_question_number") or "—"),
                grade=escape(question.get("grade", "")),
                chapter=escape(question.get("chapter", "")),
                difficulty=escape(question.get("difficulty", "")),
                status=escape(question.get("review_status") or question.get("quality_status", "")),
                quality_status=escape(question.get("quality_status", "")),
                review_status=escape(question.get("review_status", "")),
                source_confidence=escape(str(question.get("source_confidence", ""))),
                knowledge_ids=escape(knowledge_ids),
                ability_ids=escape(ability_ids),
                literacy_ids=escape(literacy_ids),
            )
        )
    if not question_rows:
        question_rows.append("<tr><td colspan='8'>暂无题库记录</td></tr>")
    if not question_options:
        question_options.append("<option value='q-newton-1'>q-newton-1</option>")
    parse_rows = []
    for task in dashboard.get("parse_tasks", []):
        parse_rows.append(
            """
            <tr>
              <td>{file}</td><td>{paper}</td><td>{parser}</td><td>{status}</td>
              <td>{count}/{saved}</td>
              <td><button data-action="run-parse-task" data-task-id="{task_id}">执行解析</button></td>
            </tr>
            """.format(
                file=escape(task.get("file_name", "")),
                paper=escape(task.get("original_paper_title") or "—"),
                parser=escape(task.get("parser_mode") or task.get("parser") or ""),
                status=escape(task.get("status", "")),
                count=escape(task.get("item_count", 0)),
                saved=escape(task.get("saved_count", 0)),
                task_id=escape(task["id"]),
            )
        )
    if not parse_rows:
        parse_rows.append("<tr><td colspan='6'>暂无解析任务</td></tr>")
    parsed_rows = []
    for item in dashboard.get("parsed_items", []):
        parsed_rows.append(
            """
            <tr>
              <td>{number}</td><td>{stem}</td><td>{confidence}</td><td>{warnings}</td>
              <td>
                <form data-teacher-form="parsed-question-save" class="inline-save-form">
                  <input type="hidden" name="parsed_item_id" value="{item_id}">
                  <input name="chapter" placeholder="章节" required>
                  <input name="difficulty" placeholder="难度" value="medium" required>
                  <button type="submit">保存为题目</button>
                </form>
              </td>
            </tr>
            """.format(
                number=escape(item.get("question_number", "")),
                stem=escape(item.get("stem", "")[:80]),
                confidence=escape(item.get("confidence", "")),
                warnings=escape(",".join(item.get("warnings", []))),
                item_id=escape(item["id"]),
            )
        )
    if not parsed_rows:
        parsed_rows.append("<tr><td colspan='5'>暂无待复核拆题</td></tr>")

    def tag_options(items):
        return "".join(
            "<option value='%s'>%s %s</option>"
            % (
                escape(item["id"]),
                escape(item.get("stable_code", "")),
                escape(item["name"]),
            )
            for item in items
        )

    def filter_tag_options(items):
        return "".join(
            '<option value="%s">%s %s</option>'
            % (
                escape(item["id"]),
                escape(item.get("stable_code", "")),
                escape(item["name"]),
            )
            for item in items
        )

    def three_selects(name, options):
        return "".join(
            "<select name='%s'><option value=''>不选</option>%s</select>"
            % (name, options)
            for _ in range(3)
        )

    knowledge_tag_options = tag_options(dashboard["knowledge_nodes"])
    ability_tag_options = tag_options(dashboard["ability_tags"])
    literacy_tag_options = tag_options(dashboard.get("literacy_tags", []))
    question_filter_tag_options = (
        '<option value="">全部标签</option>'
        + filter_tag_options(dashboard["knowledge_nodes"])
        + filter_tag_options(dashboard["ability_tags"])
        + filter_tag_options(dashboard.get("literacy_tags", []))
    )
    paper_items_json = escape(
        dumps(
            [
                {"question_id": "q-newton-1", "points": 4},
                {"question_id": "q-newton-2", "points": 6},
            ]
        )
    )
    ocr_items_json = escape(
        dumps(
            [
                {
                    "student_id": "stu-1001",
                    "question_id": "q-newton-1",
                    "answer": "A",
                    "confidence": 0.95,
                },
                {
                    "student_id": "stu-1001",
                    "question_id": "q-newton-2",
                    "answer": "D",
                    "confidence": 0.41,
                },
            ]
        )
    )
    revision_items_json = escape(
        dumps(
            [
                {
                    "response_id": "resp-1001-q1",
                    "revised_answer": "B",
                    "revised_score": 0,
                    "max_score": 4,
                    "reason": "学生实际选择 B",
                }
            ]
        )
    )
    empty_array_json = escape(dumps([]))
    grade_avg = diag["grade_average"]
    body = """
<section class="teacher-app">
  <div id="action-status" class="action-status" aria-live="polite">等待操作</div>
  <div class="workspace-grid">
    <section class="panel span-2">
      <div class="panel-head"><h1>测评批次</h1><span>批改并发布会先检查低置信答题卡，未复核时不会发布。</span></div>
      <table>
        <thead><tr><th>标题</th><th>班级</th><th>状态</th><th>待复核</th><th>错题</th><th>操作</th></tr></thead>
        <tbody><tr><td>{title}</td><td>{class_name}</td><td>{status}</td><td>{review_required}</td><td>{wrong_count}</td><td><button data-action="grade-assessment" data-assessment-id="{assessment_id}">批改并发布</button></td></tr></tbody>
      </table>
      <form class="export-filter" action="/export/wrong-book/{assessment_id}" method="get" target="_blank">
        <strong>A4 错题本</strong>
        <label>按班级筛选<select name="class_id"><option value="">全部班级</option>{class_options}</select></label>
        <label>按学生筛选<select name="student_id"><option value="">全部学生</option>{student_options}</select></label>
        <button type="submit">导出打印版</button>
      </form>
      <form class="export-filter" data-teacher-form="wrong-book-pdf">
        <strong>PDF 生成服务</strong>
        <input type="hidden" name="assessment_id" value="{assessment_id}">
        <button type="submit">生成错题本 PDF</button>
      </form>
      <form class="password-reset-form" data-password-reset-form>
        <strong>重置本班学生密码</strong>
        <label>学生<select name="target_user_id" required>{student_options}</select></label>
        <label>临时密码<input name="temporary_password" type="password" minlength="10" placeholder="至少 10 位，含字母和数字" required></label>
        <button type="submit">重置密码</button>
      </form>
    </section>
    <section class="panel">
      <h2>LLM 候选审核</h2>
      <p class="explain">LLM 候选审核用于把题目先交给模型生成知识点/能力标签建议和核心素养标签建议，教师确认后才写入正式题库。生成 q-newton-1 候选是演示：为样例题 q-newton-1 生成待审核标签。</p>
      <table><thead><tr><th>题目</th><th>知识标签</th><th>能力标签</th><th>核心素养标签</th><th>操作</th></tr></thead><tbody>{candidate_rows}</tbody></table>
    </section>
    <section class="panel">
      <h2>答题卡复核</h2>
      <table><thead><tr><th>学生</th><th>题目</th><th>识别</th><th>置信度</th><th>操作</th></tr></thead><tbody>{review_rows}</tbody></table>
    </section>
    <section class="panel span-2 phase2d-workspace">
      <div class="panel-head">
        <div>
          <h2>组卷与答题卡</h2>
          <p class="explain">Phase 2D 工作台：组卷、答题卡、OCR、批改修订、错因与重做复核。</p>
        </div>
      </div>
      <div class="phase2d-form-grid">
        <form data-teacher-form="paper-assembly">
          <h3>组卷与答题卡</h3>
          <label>试卷标题<input name="title" value="Phase 2D 力学小测" required></label>
          <label>来源<input name="source" value="校本组卷" required></label>
          <label>题目 JSON<textarea name="question_items" data-json="true" required>{paper_items_json}</textarea></label>
          <button type="submit">生成试卷</button>
        </form>
        <form data-teacher-form="ocr-import">
          <h3>OCR 导入复核</h3>
          <label>测评ID<input name="assessment_id" value="{assessment_id}" required></label>
          <label>来源名<input name="source_name" value="PaddleOCR 导入样例" required></label>
          <label>识别器<input name="recognizer" value="PaddleOCR" required></label>
          <label>版本<input name="recognizer_version" value="reserved-local-v2" required></label>
          <label>识别项 JSON<textarea name="items" data-json="true" required>{ocr_items_json}</textarea></label>
          <button type="submit">导入 OCR 结果</button>
        </form>
        <form data-teacher-form="grading-revision">
          <h3>批改修订</h3>
          <label>测评ID<input name="assessment_id" value="{assessment_id}" required></label>
          <label>修订原因<input name="reason" value="发布后复查" required></label>
          <label>修订项 JSON<textarea name="items" data-json="true" required>{revision_items_json}</textarea></label>
          <button type="submit">应用修订</button>
        </form>
        <form data-teacher-form="error-tagging">
          <h3>错因标签</h3>
          <label>错题ID<input name="wrong_question_id" required></label>
          <label>标签ID JSON<textarea name="tag_ids" data-json="true" required>{empty_array_json}</textarea></label>
          <label>备注<input name="note" value="教师归因"></label>
          <button type="submit">保存错因</button>
        </form>
        <form data-teacher-form="redo-review">
          <h3>重做复核</h3>
          <label>重做ID<input name="attempt_id" required></label>
          <label>得分<input name="score" type="number" value="4" required></label>
          <label>反馈<input name="feedback" value="重做正确"></label>
          <button type="submit">复核重做</button>
        </form>
      </div>
    </section>
    <section class="panel span-2 question-bank-workspace">
      <div class="panel-head">
        <div>
          <h2>真实题库</h2>
          <p class="explain">录入、解析、复核并确认知识/能力/核心素养标签。</p>
        </div>
      </div>
      <div class="question-bank-grid">
        <form data-teacher-form="question" class="question-edit-form">
          <h3>新增题目</h3>
          <label>题干<textarea name="stem" required></textarea></label>
          <label>选项 JSON<textarea name="options" data-json="true" data-default-json="{{}}">{empty_object}</textarea></label>
          <label>答案 JSON<textarea name="answer" data-json="true" data-default-json="{{}}">{default_answer}</textarea></label>
          <label>解析<textarea name="analysis"></textarea></label>
          <label>题型<input name="question_type" value="short_answer" required></label>
          <label>来源<input name="source" value="教师录入" required></label>
          <label>年级<input name="grade" value="{grade}" required></label>
          <label>章节<input name="chapter" required></label>
          <label>难度<input name="difficulty" value="medium" required></label>
          <button type="submit">保存题目</button>
        </form>
        <form data-teacher-form="parse-task" class="parse-task-grid">
          <h3>原卷解析</h3>
          <label>原卷标题<input name="paper_title" value="新导入原卷" required></label>
          <label>文件名<input name="document_name" value="sample.txt" required></label>
          <label>解析模式<select name="parser_mode"><option value="deterministic_text">内置文本解析</option><option value="markitdown">MarkItDown</option><option value="mineru_local">MinerU 本地</option><option value="mineru_api">MinerU API</option></select></label>
          <label>来源学校<input name="source_school" value="校内命题"></label>
          <label>发布方<input name="source_publisher" value="高二物理备课组"></label>
          <label>考试类型<input name="exam_type" value="weekly_quiz"></label>
          <label>年级<input name="grade" value="{grade}"></label>
          <label>学期<input name="term" value="2025-2026下"></label>
          <label class="span-2">原文<textarea name="source_text" required>1. 测试题干\nA. 1\nB. 2\n答案：B</textarea></label>
          <button type="submit">创建解析任务</button>
        </form>
      </div>
      <div class="question-filter-bar" data-question-bank-filter>
        <strong>题库筛选</strong>
        <label>年级<input name="filter_grade" placeholder="高二"></label>
        <label>章节<input name="filter_chapter" placeholder="运动和力的关系"></label>
        <label>质量状态<input name="filter_quality_status" placeholder="reviewed"></label>
        <label>标签类型<select name="tag_type"><option value="">全部类型</option><option value="knowledge">知识标签</option><option value="ability">能力标签</option><option value="literacy">核心素养标签</option></select></label>
        <label>正式标签<select name="tag_id">{question_filter_tag_options}</select></label>
        <label>置信度≤<input name="filter_source_confidence_max" placeholder="0.8"></label>
      </div>
      <div class="taxonomy-table-scroll">
        <table><thead><tr><th>题干</th><th>原卷/来源</th><th>题号</th><th>年级</th><th>章节</th><th>难度</th><th>状态</th><th>操作</th></tr></thead><tbody>{question_rows}</tbody></table>
      </div>
      <h3>解析队列</h3>
      <div class="taxonomy-table-scroll">
        <table><thead><tr><th>文件</th><th>原卷</th><th>解析器</th><th>状态</th><th>拆题/保存</th><th>操作</th></tr></thead><tbody>{parse_rows}</tbody></table>
      </div>
      <h3>拆题复核</h3>
      <div class="taxonomy-table-scroll">
        <table class="parsed-item-table"><thead><tr><th>题号</th><th>题干</th><th>置信度</th><th>警告</th><th>保存</th></tr></thead><tbody>{parsed_rows}</tbody></table>
      </div>
      <form data-teacher-form="question-tags-confirm" class="tag-family-grid">
        <h3>标签确认</h3>
        <label>题目<select name="question_id" required>{question_options}</select></label>
        <label>候选ID<input name="candidate_id" placeholder="可选"></label>
        <fieldset><legend>知识标签</legend>{knowledge_selects}</fieldset>
        <fieldset><legend>能力标签</legend>{ability_selects}</fieldset>
        <fieldset><legend>核心素养标签</legend>{literacy_selects}</fieldset>
        <button type="submit">确认标签</button>
      </form>
    </section>
    <section class="panel span-2">
      <div class="panel-head"><h2>知识图谱</h2><span>{node_count} 节点 / {edge_count} 关系</span></div>
      <div class="graph-layout">{graph}<table><thead><tr><th>ID</th><th>节点</th><th>层级</th><th>来源</th></tr></thead><tbody>{node_rows}</tbody></table></div>
    </section>
    {mastery_analytics}
    <section class="panel">
      <h2>班级诊断</h2>
      <p class="explain">教师只能查看自己班级明细，同时可看到同年级平均：{grade} 平均得分率 {grade_rate}%（当前演示数据只有一个班）。</p>
      <table><thead><tr><th>知识点</th><th>错题</th><th>错误率</th></tr></thead><tbody>{knowledge_rows}</tbody></table>
    </section>
    <section class="panel">
      <h2>能力诊断</h2>
      <table><thead><tr><th>能力</th><th>错题</th><th>错误率</th></tr></thead><tbody>{ability_rows}</tbody></table>
    </section>
  </div>
</section>""".format(
        assessment_id=escape(assessment["id"]),
        title=escape(assessment["title"]),
        class_name=escape(assessment["class_name"]),
        status=escape(assessment["status"]),
        review_required=assessment["review_required"],
        wrong_count=assessment["wrong_count"],
        class_options=class_options,
        student_options=student_options,
        candidate_rows="".join(candidate_rows),
        review_rows="".join(review_rows),
        paper_items_json=paper_items_json,
        ocr_items_json=ocr_items_json,
        revision_items_json=revision_items_json,
        empty_array_json=empty_array_json,
        empty_object=escape(dumps({})),
        default_answer=escape(dumps({"type": "short_answer", "answer": ""})),
        question_filter_tag_options=question_filter_tag_options,
        question_rows="".join(question_rows),
        parse_rows="".join(parse_rows),
        parsed_rows="".join(parsed_rows),
        question_options="".join(question_options),
        knowledge_selects=three_selects("knowledge_node_ids", knowledge_tag_options),
        ability_selects=three_selects("ability_tag_ids", ability_tag_options),
        literacy_selects=three_selects("literacy_tag_ids", literacy_tag_options),
        node_count=len(dashboard["knowledge_nodes"]),
        edge_count=len(dashboard["knowledge_edges"]),
        graph=_render_graph(dashboard["knowledge_nodes"], dashboard["knowledge_edges"]),
        node_rows=node_rows,
        mastery_analytics=_render_teacher_mastery_analytics(
            dashboard.get("mastery_analytics", {})
        ),
        grade=escape(grade_avg["grade"]),
        grade_rate=round(grade_avg["average_score_rate"] * 100, 1),
        knowledge_rows=knowledge_rows or "<tr><td colspan='3'>暂无已发布统计</td></tr>",
        ability_rows=ability_rows or "<tr><td colspan='3'>暂无已发布统计</td></tr>",
    )
    return render_layout("教师端 - 高中物理闭环系统", user, body, "teacher")


def render_admin_app(user, dashboard):
    taxonomy_summary = dashboard["taxonomy_summary"]
    taxonomy_sources = dashboard["taxonomy_sources"]
    _mastery_trend, _mastery_grade_columns = _render_admin_mastery_analytics(
        dashboard.get("mastery_analytics", {"grades": [], "trends": []})
    )
    taxonomy_nodes = dashboard["knowledge_nodes"]
    taxonomy_node_by_id = {item["id"]: item for item in taxonomy_nodes}
    source_title_by_key = {
        item["source_key"]: item["title"] for item in taxonomy_sources
    }

    def module_id_for(node):
        current = node
        visited = set()
        while current and current.get("parent_id"):
            if current["id"] in visited:
                return ""
            visited.add(current["id"])
            current = taxonomy_node_by_id.get(current["parent_id"])
        return current["id"] if current and current.get("level") == 1 else ""

    class_group_items = []
    for class_group in dashboard.get("class_groups", []):
        if class_group.get("status") and class_group["status"] != "active":
            continue
        class_group_items.append(
            (
                "<li>"
                "<label class=\"assign-classes-option\">"
                "<input type=\"checkbox\" name=\"class_ids\" value=\"%s\" "
                "data-class-id=\"%s\">"
                "<span class=\"assign-classes-option-label\">"
                "<strong>%s</strong>"
                "<span class=\"assign-classes-option-meta\">%s · %s</span>"
                "</span>"
                "</label>"
                "</li>"
            )
            % (
                escape(class_group["id"]),
                escape(class_group["id"]),
                escape(class_group.get("name") or class_group["id"]),
                escape(class_group.get("grade") or ""),
                escape(class_group.get("school_year") or ""),
            )
        )
    class_group_checkbox_list = "".join(class_group_items) or (
        "<li class=\"assign-classes-empty\">暂无班级可分配。</li>"
    )

    def _render_user_row_actions(item):
        if item.get("role") != "teacher":
            return (
                "<td class=\"admin-user-actions\">"
                "<span class=\"admin-user-actions-empty\">—</span>"
                "</td>"
            )
        teacher_id = escape(item["id"])
        class_ids = [
            cid for cid in (item.get("class_ids") or "").split(",") if cid
        ]
        return (
            "<td class=\"admin-user-actions\">"
            "<button type=\"button\" class=\"secondary\" "
            "data-action=\"open-assign-classes\" data-teacher-id=\"%s\" "
            "aria-expanded=\"false\" aria-controls=\"assign-classes-panel-%s\">"
            "分配班级"
            "</button>"
            "<div class=\"assign-classes-panel\" "
            "data-assign-classes-panel data-teacher-id=\"%s\" hidden "
            "id=\"assign-classes-panel-%s\">"
            "<form class=\"assign-classes-form\" "
            "data-admin-form=\"assign-classes\" "
            "data-endpoint=\"/api/admin/teacher/%s/assign-classes\">"
            "<div class=\"assign-classes-header\">"
            "<strong>%s 当前任课</strong>"
            "<span>%d 个班级 · 勾选以更新</span>"
            "</div>"
            "<ul class=\"assign-classes-options\" "
            "data-assign-classes-options data-initial-ids=\"%s\">"
            "%s"
            "</ul>"
            "<div class=\"assign-classes-actions\">"
            "<button type=\"submit\">保存分配</button>"
            "<button type=\"button\" class=\"secondary\" "
            "data-action=\"close-assign-classes\" data-teacher-id=\"%s\">"
            "取消"
            "</button>"
            "</div>"
            "</form>"
            "</div>"
            "</td>"
        ) % (
            teacher_id,
            teacher_id,
            teacher_id,
            teacher_id,
            teacher_id,
            escape(item.get("display_name") or item.get("username") or ""),
            len(class_ids),
            escape(" ".join(class_ids)),
            class_group_checkbox_list,
            teacher_id,
        )

    user_rows = "".join(
        (
            "<tr data-admin-user-row data-role='%s' data-status='%s' "
            "data-search-text='%s' data-class-ids='%s'>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>%s"
            "</tr>"
        )
        % (
            escape(item["role"]),
            escape(item.get("status", "")),
            escape(
                " ".join(
                    [
                        item.get("username") or "",
                        item.get("display_name") or "",
                        item.get("role") or "",
                        item.get("grade") or "",
                        item.get("class_name") or "",
                        item.get("status") or "",
                    ]
                )
            ),
            escape(item.get("class_ids") or ""),
            escape(item["username"]),
            escape(item["display_name"]),
            escape(item["role"]),
            escape(item.get("grade", "")),
            escape(item.get("class_name", "")),
            escape(item.get("status", "")),
            _render_user_row_actions(item),
        )
        for item in dashboard["user_management"]
    )
    active_ontology = dashboard["active_ontology_version"] or {
        "id": "",
        "version_label": "未发布",
        "status": "missing",
    }
    ontology_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (escape(item["version_label"]), escape(item["status"]), escape(item["source_summary"]))
        for item in dashboard["ontology_versions"]
    )
    publish_options = "".join(
        "<option value='%s'>%s / %s</option>" % (escape(item["id"]), escape(item["version_label"]), escape(item["status"]))
        for item in dashboard["ontology_versions"]
        if item["status"] in ("draft", "review")
    )
    if not publish_options:
        publish_options = "<option value=''>暂无草稿或待审核版本</option>"
    node_options = "".join(
        "<option value='%s'>%s %s</option>" % (escape(item["id"]), escape(item["stable_code"]), escape(item["name"]))
        for item in taxonomy_nodes
    )
    parent_options = "<option value=''>顶级节点</option>" + node_options
    node_rows = "".join(
        (
            "<tr data-taxonomy-node data-module-id='%s' data-search-text='%s'>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td></tr>"
        )
        % (
            escape(module_id_for(item)),
            escape("%s %s %s" % (
                item["stable_code"],
                item["name"],
                item.get("parent_name") or "",
            )),
            escape(item["name"]),
            escape(item.get("parent_name") or "顶级"),
            escape(item["level"]),
            (
                '<span class="taxonomy-badge default">默认</span>'
                if item.get("is_default")
                else '<span class="taxonomy-badge custom">校本</span>'
            ),
            "启用" if item["enabled"] else "停用",
        )
        for item in taxonomy_nodes
    )
    module_options = "<option value=''>全部教材模块</option>" + "".join(
        "<option value='%s'>%s</option>" % (
            escape(item["id"]),
            escape(item["name"]),
        )
        for item in taxonomy_nodes
        if item["level"] == 1 and item["enabled"]
    )
    edge_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (
            escape(item["source_name"]),
            escape(item["relation_type"]),
            escape(item["target_name"]),
            "启用" if item["enabled"] else "停用",
        )
        for item in dashboard["knowledge_edges"]
    )
    ability_rows = "".join(
        "<tr><td>%s</td><td>%s</td></tr>"
        % (
            escape(item["name"]),
            "启用" if item["enabled"] else "停用",
        )
        for item in dashboard["ability_tags"]
    )
    ability_options = "".join(
        "<option value='%s'>%s %s</option>" % (escape(item["id"]), escape(item["stable_code"]), escape(item["name"]))
        for item in dashboard["ability_tags"]
    )
    literacy_options = "".join(
        "<option value='%s'>%s %s</option>" % (
            escape(item["id"]),
            escape(item["stable_code"]),
            escape(item["name"]),
        )
        for item in dashboard["literacy_tags"]
    )
    literacy_parent_options = "<option value=''>顶级素养</option>" + "".join(
        "<option value='%s'>%s %s</option>" % (
            escape(item["id"]),
            escape(item["stable_code"]),
            escape(item["name"]),
        )
        for item in dashboard["literacy_tags"]
        if item["level"] == 1
    )
    literacy_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (
            escape(item["name"]),
            escape(item.get("parent_name") or "顶级"),
            escape(item["level"]),
            (
                '<span class="taxonomy-badge default">默认</span>'
                if item.get("is_default")
                else '<span class="taxonomy-badge custom">校本</span>'
            ),
            "启用" if item["enabled"] else "停用",
        )
        for item in dashboard["literacy_tags"]
    )
    source_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (
            escape(item["title"]),
            escape(item["edition"]),
            escape(item["file_name"] or "书目来源"),
            escape(item["page_count"] or "—"),
            escape(item["verified_at"] or "未核验"),
        )
        for item in taxonomy_sources
    )
    default_nodes = [
        item for item in taxonomy_nodes if item.get("is_default")
    ]
    level_counts = {
        level: sum(1 for item in default_nodes if item["level"] == level)
        for level in (1, 2, 3)
    }
    llm_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (
            escape(item["provider_name"]),
            escape(item["model_name"]),
            escape(item["key_masked"] or "后端保存"),
            escape(item["last_test_status"] or ""),
        )
        for item in dashboard["llm_provider_configs"]
    )
    audit_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (
            escape(item["created_at"]),
            escape(item["actor_id"]),
            escape(item["action"]),
            escape(item["resource_type"]),
        )
        for item in dashboard["audit_events"][-12:]
    )
    parse_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (escape(item["file_name"]), escape(item["parser"]), escape(item["status"]), escape(item["failure_reason"]))
        for item in dashboard["document_parse_tasks"]
    )
    privacy_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (escape(item["basis"]), escape(item["retention_policy"]), escape(item["status"]))
        for item in dashboard["privacy_consent_records"]
    )
    identity_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (escape(item["provider_name"]), escape(item["issuer"]), escape(item["enabled"]))
        for item in dashboard["auth_provider_configs"]
    )
    if not identity_rows:
        identity_rows = "<tr><td>local</td><td>内置账号</td><td>1</td></tr>"
    pdf_export_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (
            escape(item["export_type"]),
            escape(item["status"]),
            escape(item.get("file_name") or "—"),
            escape(item.get("byte_size") or 0),
            escape(item.get("engine_version") or "—"),
        )
        for item in dashboard.get("export_tasks", [])[-8:]
    )
    if not pdf_export_rows:
        pdf_export_rows = "<tr><td colspan='5'>暂无 PDF 导出任务</td></tr>"
    reset_user_options = "".join(
        "<option value='%s'>%s / %s / %s</option>"
        % (
            escape(item["id"]),
            escape(item["username"]),
            escape(item["display_name"]),
            escape(item["role"]),
        )
        for item in dashboard["user_management"]
        if item["status"] == "active" and item["id"] != user["id"]
    )
    export_options_json = escape(
        dumps(
            {
                "include_answers": False,
                "include_analysis": False,
                "include_error_reasons": True,
                "include_redo_history": True,
                "page_break": "student",
            }
        )
    )
    runtime_health_rows = "".join(
        """
        <article class="runtime-health-card" data-status="{status}">
          <strong>{label}</strong>
          <span>{status}</span>
          <small>{version}</small>
          <p>{detail}</p>
          <small>{checked_at}</small>
        </article>
        """.format(
            status=escape(item["status"]),
            label=escape(item.get("label") or item["capability_id"]),
            version=escape(item.get("version") or "版本待检测"),
            detail=escape(item.get("detail") or ""),
            checked_at=escape(item.get("checked_at") or "尚未记录管理员检查"),
        )
        for item in dashboard["production_readiness"]["runtime_checks"]
    )
    provider_rows = "".join(
        """
        <tr>
          <td>{kind}</td><td>{name}</td><td>{model}</td>
          <td>{enabled}</td><td>{secret}</td>
          <td>{daily} 次/日，{monthly} 分/月，{per_call} 分/次</td>
          <td>{test}</td>
        </tr>
        """.format(
            kind=escape(item["provider_kind"]),
            name=escape(item["provider_name"]),
            model=escape(item.get("model_name") or "—"),
            enabled="启用" if item.get("enabled") else "停用",
            secret=escape(item.get("secret_masked") or "未保存"),
            daily=escape(item.get("daily_call_limit", 0)),
            monthly=escape(item.get("monthly_budget_cents", 0)),
            per_call=escape(item.get("per_call_max_cents", 0)),
            test=escape(item.get("last_test_status") or "未测试"),
        )
        for item in dashboard.get("provider_configs", [])
    )
    if not provider_rows:
        provider_rows = "<tr><td colspan='7'>暂无 Provider 配置</td></tr>"
    provider_options = "".join(
        "<option value='%s'>%s / %s / %s</option>"
        % (
            escape(item["id"]),
            escape(item["provider_kind"]),
            escape(item["provider_name"]),
            escape(item.get("model_name") or "默认模型"),
        )
        for item in dashboard.get("provider_configs", [])
    )
    if not provider_options:
        provider_options = "<option value=''>先保存 Provider 配置</option>"
    provider_usage_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (
            escape(item["created_at"]),
            escape(item["request_type"]),
            escape(item["provider_name"]),
            escape(item["estimated_cost_cents"]),
            escape(item["outcome"]),
        )
        for item in dashboard.get("provider_usage_events", [])[-8:]
    )
    if not provider_usage_rows:
        provider_usage_rows = "<tr><td colspan='5'>暂无 Provider 调用记录</td></tr>"

    body = """
<section class="admin-app admin-app-compact">
  <div id="action-status" class="action-status" aria-live="polite">等待操作</div>
  <nav class="admin-tab-nav" role="tablist" aria-label="管理员分区">
    <button type="button" role="tab" class="is-active" data-admin-tab="overview" aria-selected="true">概览</button>
    <button type="button" role="tab" data-admin-tab="accounts" aria-selected="false">账号</button>
    <button type="button" role="tab" data-admin-tab="ontology" aria-selected="false">本体</button>
    <button type="button" role="tab" data-admin-tab="operations" aria-selected="false">运营</button>
    <button type="button" role="tab" data-admin-tab="system" aria-selected="false">系统</button>
  </nav>

  <section class="admin-tab-panel is-active" data-admin-tab-panel="overview" role="tabpanel">
    <div class="workspace-grid">
      <section class="panel span-2 taxonomy-overview">
        <div class="panel-head">
          <div>
            <h1>默认物理体系</h1>
            <p class="explain">依据人教版 2019 六册教材目录与普通高中物理课程标准建立，可在此基础上增补校本节点。</p>
          </div>
          <button data-action="install-default-taxonomy">安装或补齐默认体系</button>
        </div>
        <div class="metric-strip">
          <div><strong>{knowledge_total} 个知识节点</strong><span>{volume_total} 册 / {chapter_total} 章 / {section_total} 节</span></div>
          <div><strong>{ability_total} 个能力标签</strong><span>{ability_active} 个当前启用</span></div>
          <div><strong>{literacy_total} 个核心素养标签</strong><span>{literacy_active} 个当前启用</span></div>
          <div><strong>{source_total} 项来源证据</strong><span>版本 {taxonomy_version}</span></div>
        </div>
        <details>
          <summary>来源与版本</summary>
          <div class="taxonomy-table-scroll">
            <table class="taxonomy-source-table">
              <thead><tr><th>来源</th><th>版本</th><th>文件或书目</th><th>页数</th><th>核验日期</th></tr></thead>
              <tbody>{source_rows}</tbody>
            </table>
          </div>
        </details>
      </section>
      <section class="panel span-2 runtime-health-panel">
        <div class="panel-head">
          <div>
            <h2>生产化就绪度</h2>
            <p class="explain">检查 OCR、文档解析、PDF、SSO 和密钥加密能力是否真实可用。</p>
          </div>
          <form data-admin-form="runtime-check">
            <button type="submit">重新检查</button>
          </form>
        </div>
        <div class="runtime-health-grid">{runtime_health_rows}</div>
      </section>
      {mastery_trend}
      <section class="panel mastery-overview-panel"><h2>掌握分析</h2><p class="explain">管理员视图仅展示按年级聚合的标签掌握度；未分班年级自动隐藏。卡片横向并排，可随年级数量自动扩展。</p>{mastery_grade_columns}</section>
      <section class="panel privacy-panel"><h2>隐私与留存</h2><table><thead><tr><th>依据</th><th>策略</th><th>状态</th></tr></thead><tbody>{privacy_rows}</tbody></table></section>
    </div>
  </section>

  <section class="admin-tab-panel" data-admin-tab-panel="accounts" role="tabpanel" hidden>
    <div class="workspace-grid">
      <section class="panel admin-user-panel">
        <div class="panel-head">
          <div>
            <h1>用户与班级</h1>
            <p class="explain">筛选和翻页只影响当前表格显示，不改变账号数据。</p>
          </div>
          <button data-action="import-demo-student">导入学生</button>
        </div>
        <form class="teacher-import-form" data-admin-form="import-teacher">
          <strong>新建教师</strong>
          <p class="explain">创建教师账号并设置临时密码；首次登录将强制改密。</p>
          <label>账号<input name="username" required maxlength="64" autocomplete="off" placeholder="如 teacher.zhang"></label>
          <label>姓名<input name="display_name" required maxlength="64" placeholder="如 张老师"></label>
          <label>临时密码<input name="temp_password" type="password" required minlength="10" autocomplete="new-password" placeholder="至少 10 位含字母数字"></label>
          <button type="submit">创建教师</button>
        </form>
        <div class="admin-user-toolbar">
          <label>搜索<input data-admin-user-search type="search" placeholder="账号、姓名、年级或班级"></label>
          <label>角色<select data-admin-user-role-filter><option value="">全部角色</option><option value="admin">管理员</option><option value="teacher">教师</option><option value="student">学生</option></select></label>
          <label>状态<select data-admin-user-status-filter><option value="">全部状态</option><option value="active">active</option><option value="disabled">disabled</option></select></label>
          <span class="admin-user-page-size" data-admin-user-page-size="12">每页 12 条</span>
        </div>
        <div class="compact-table-scroll">
          <table class="admin-user-table"><thead><tr><th>账号</th><th>姓名</th><th>角色</th><th>年级</th><th>班级</th><th>状态</th><th>操作</th></tr></thead><tbody>{user_rows}</tbody></table>
        </div>
        <div class="admin-user-pagination" data-admin-user-pagination>
          <button class="secondary" type="button" data-admin-user-prev>上一页</button>
          <span data-admin-user-page-info>第 1 页</span>
          <button class="secondary" type="button" data-admin-user-next>下一页</button>
        </div>
        <form class="password-reset-form" data-password-reset-form>
          <strong>重置同校账号密码</strong>
          <label>账号<select name="target_user_id" required>{reset_user_options}</select></label>
          <label>临时密码<input name="temporary_password" type="password" minlength="10" placeholder="至少 10 位，含字母和数字" required></label>
          <button type="submit">重置密码</button>
        </form>
      </section>
      <section class="panel sso-settings-panel admin-panel-fit">
        <h2>OIDC SSO</h2>
        <p class="explain">统一身份预留已升级为 OIDC SSO：配置学校统一身份入口；client_secret 加密保存，回调按一次性 state 和本地账号绑定策略处理。</p>
        <form data-admin-form="oidc-provider" class="phase2d-config-form">
          <label>Provider 名称<input name="provider_name" value="School OIDC" required></label>
          <label>Issuer<input name="issuer" placeholder="https://idp.example.edu" required></label>
          <label>Client ID<input name="client_id" required></label>
          <label>Client Secret<input name="client_secret" type="password" autocomplete="new-password"></label>
          <label>授权端点<input name="authorization_endpoint" required></label>
          <label>Token 端点<input name="token_endpoint"></label>
          <label>UserInfo 端点<input name="userinfo_endpoint"></label>
          <label>绑定策略<select name="binding_policy"><option value="existing_user_only">仅绑定已有本地账号</option></select></label>
          <label>启用<select name="enabled"><option value="1">启用</option><option value="0">停用</option></select></label>
          <button type="submit">保存 OIDC 配置</button>
        </form>
        <table><thead><tr><th>Provider</th><th>Issuer</th><th>启用</th></tr></thead><tbody>{identity_rows}</tbody></table>
      </section>
    </div>
  </section>

  <section class="admin-tab-panel" data-admin-tab-panel="ontology" role="tabpanel" hidden>
    <div class="workspace-grid">
      <section class="panel admin-panel-fit ontology-release-panel">
        <div class="panel-head"><h2>本体版本发布</h2><span>当前：{active_label} / {active_status}</span></div>
        <div class="ontology-release-grid">
          <form data-admin-form="ontology-draft">
            <label>版本名称<input name="version_label" value="2026校本物理知识图谱v2" required></label>
            <label>来源说明<input name="source_summary" value="课标/教材目录 + 备课组修订" required></label>
            <button type="submit">创建草稿</button>
          </form>
          <form data-admin-form="ontology-publish">
            <label>待发布版本<select name="ontology_version_id">{publish_options}</select></label>
            <button type="submit" name="transition" value="review">送审</button>
            <button type="submit" name="transition" value="publish">发布</button>
          </form>
        </div>
        <table><thead><tr><th>版本</th><th>状态</th><th>来源</th></tr></thead><tbody>{ontology_rows}</tbody></table>
      </section>
      <section class="panel">
        <h2>错因标签</h2>
        <p class="explain">管理员维护全校统一错因标签；教师在错题复核时引用。</p>
        <form data-admin-form="error-reason-tag" class="phase2d-config-form">
          <label>编码<input name="code" value="concept-force" required></label>
          <label>名称<input name="name" value="概念混淆" required></label>
          <label>说明<input name="description" value="力与运动关系理解错误"></label>
          <button type="submit">新增错因</button>
        </form>
      </section>
      <section class="panel">
        <h2>导出配置</h2>
        <p class="explain">配置错题本打印范围，默认不泄露答案和解析。</p>
        <form data-admin-form="export-profile" class="phase2d-config-form">
          <label>名称<input name="name" value="默认错题本" required></label>
          <label>配置 JSON<textarea name="options" data-json="true" required>{export_options_json}</textarea></label>
          <button type="submit">保存导出配置</button>
        </form>
      </section>
      <section class="panel span-2 admin-panel-fit taxonomy-admin-panel">
        <h2>知识图谱与能力标签</h2>
        <p class="explain">管理员设定全校知识图谱、学科能力与版本；教师端只负责审核题目和查看诊断。</p>
        <div class="admin-crud-grid">
          <form data-admin-form="knowledge-node">
            <h3>新增知识节点</h3>
            <label>ID<input name="stable_code" placeholder="M.N.3" required></label>
            <label>名称<input name="name" required></label>
            <label>父节点<select name="parent_id">{parent_options}</select></label>
            <label>别名<input name="aliases"></label>
            <label>来源<input name="source" value="教师校本"></label>
            <label>版本说明<input name="change_note"></label>
            <button type="submit">新增节点</button>
          </form>
          <form data-admin-form="knowledge-node-update">
            <h3>调整知识节点</h3>
            <label>节点<select name="node_id">{node_options}</select></label>
            <label>名称<input name="name" required></label>
            <label>别名<input name="aliases"></label>
            <label>来源<input name="source"></label>
            <label>启用状态<select name="enabled"><option value="1">启用</option><option value="0">停用</option></select></label>
            <label>版本说明<input name="change_note"></label>
            <button type="submit">保存节点</button>
          </form>
          <form data-admin-form="knowledge-edge">
            <h3>新增语义关系</h3>
            <label>起点<select name="source_node_id">{node_options}</select></label>
            <label>关系<select name="relation_type"><option>前置</option><option>关联</option><option>易混</option><option>共现</option><option>迁移</option><option>包含</option><option>同类模型</option></select></label>
            <label>终点<select name="target_node_id">{node_options}</select></label>
            <label>方向<select name="bidirectional"><option value="1">双向</option><option value="0">单向</option></select></label>
            <label>依据<input name="rationale"></label>
            <button type="submit">新增关系</button>
          </form>
          <form data-admin-form="ability-tag">
            <h3>新增能力标签</h3>
            <label>ID<input name="stable_code" placeholder="A.GRAPH" required></label>
            <label>名称<input name="name" required></label>
            <label>说明<input name="description"></label>
            <label>来源<input name="source" value="课标/高考评价体系"></label>
            <button type="submit">新增能力</button>
          </form>
          <form data-admin-form="ability-tag-update">
            <h3>调整能力标签</h3>
            <label>能力<select name="ability_tag_id">{ability_options}</select></label>
            <label>名称<input name="name" required></label>
            <label>说明<input name="description"></label>
            <label>来源<input name="source"></label>
            <label>启用状态<select name="enabled"><option value="1">启用</option><option value="0">停用</option></select></label>
            <label>版本说明<input name="change_note"></label>
            <button type="submit">保存能力</button>
          </form>
        </div>
        <div class="taxonomy-filter-bar">
          <label>教材模块<select data-taxonomy-module>{module_options}</select></label>
          <label>搜索知识点<input data-taxonomy-search type="search" placeholder="输入名称、编码或父级"></label>
        </div>
        <div class="taxonomy-tables-3col">
          <div class="taxonomy-table-scroll">
            <table>
              <colgroup><col style="width:50%"><col><col><col><col></colgroup>
              <thead><tr><th>知识点</th><th>父级</th><th>层级</th><th>类型</th><th>启用状态</th></tr></thead>
              <tbody>{node_rows}</tbody>
            </table>
          </div>
          <div class="taxonomy-table-scroll">
            <table style="width:50%">
              <colgroup><col style="width:50%"><col style="width:50%"></colgroup>
              <thead><tr><th>能力</th><th>启用状态</th></tr></thead>
              <tbody>{ability_rows}</tbody>
            </table>
          </div>
          <div class="taxonomy-table-scroll">
            <table>
              <colgroup><col style="width:50%"><col><col><col><col></colgroup>
              <thead><tr><th>核心素养</th><th>父级</th><th>层级</th><th>类型</th><th>启用状态</th></tr></thead>
              <tbody>{literacy_rows}</tbody>
            </table>
          </div>
        </div>
        <table><thead><tr><th>起点</th><th>关系</th><th>终点</th><th>启用状态</th></tr></thead><tbody>{edge_rows}</tbody></table>
      </section>
      <section class="panel span-2 admin-panel-fit literacy-admin-panel">
        <h2>核心素养管理</h2>
        <p class="explain">默认标签来自课程标准；校本调整会保留版本说明与审计记录。核心素养概览表已并入上方"知识图谱与能力标签"模块的 3 列布局。</p>
        <div class="admin-crud-grid literacy-grid">
          <form data-admin-form="literacy-tag">
            <h3>新增核心素养标签</h3>
            <label>ID<input name="stable_code" placeholder="L.CUSTOM" required></label>
            <label>名称<input name="name" required></label>
            <label>父级<select name="parent_id">{literacy_parent_options}</select></label>
            <label>说明<input name="description"></label>
            <label>来源<input name="source" value="教师校本"></label>
            <label>版本说明<input name="change_note"></label>
            <button type="submit">新增标签</button>
          </form>
          <form data-admin-form="literacy-tag-update">
            <h3>调整核心素养标签</h3>
            <label>标签<select name="literacy_id">{literacy_options}</select></label>
            <label>名称<input name="name" required></label>
            <label>说明<input name="description"></label>
            <label>来源<input name="source"></label>
            <label>启用状态<select name="enabled"><option value="1">启用</option><option value="0">停用</option></select></label>
            <label>版本说明<input name="change_note"></label>
            <button type="submit">保存标签</button>
          </form>
        </div>
      </section>
    </div>
  </section>

  <section class="admin-tab-panel" data-admin-tab-panel="operations" role="tabpanel" hidden>
    <div class="workspace-grid">
      <section class="panel span-2 provider-ops-panel admin-panel-fit">
        <div class="panel-head">
          <div>
            <h2>Provider 运营</h2>
            <p class="explain">LLM 与 MinerU API 的密钥、预算/用量、测试状态和停用策略统一在这里管理。</p>
          </div>
        </div>
        <div class="provider-ops-grid">
          <form data-admin-form="provider-config" class="provider-config-form">
            <h3>LLM 与 MinerU API</h3>
            <label>类型<select name="provider_kind"><option value="llm">LLM</option><option value="mineru_api">MinerU API</option></select></label>
            <label>Provider 名称<input name="provider_name" value="OpenAI Compatible" required></label>
            <label>模型/管线<input name="model_name" value="gpt-4.1-mini"></label>
            <label>API Endpoint<input name="api_endpoint" placeholder="https://api.example.com/v1"></label>
            <label>Secret<input name="secret" type="password" autocomplete="new-password" placeholder="保存后只显示掩码"></label>
            <label>启用<select name="enabled"><option value="1">启用</option><option value="0">停用</option></select></label>
            <label>每日调用上限<input name="daily_call_limit" type="number" value="1000"></label>
            <label>月预算（分）<input name="monthly_budget_cents" type="number" value="0"></label>
            <label>单次上限（分）<input name="per_call_max_cents" type="number" value="0"></label>
            <label>输入单价/千单位（分）<input name="input_cost_per_1k_cents" type="number" value="0" step="0.001"></label>
            <label>输出单价/千单位（分）<input name="output_cost_per_1k_cents" type="number" value="0" step="0.001"></label>
            <button type="submit">保存 Provider</button>
          </form>
          <form data-admin-form="provider-test" class="provider-test-form">
            <h3>连接与预算测试</h3>
            <label>Provider<select name="provider_config_id">{provider_options}</select></label>
            <button type="submit">测试配置</button>
            <p class="explain">测试不会把 secret 写入日志；真实远程烟测由显式运行时检查触发。</p>
          </form>
        </div>
        <div class="taxonomy-table-scroll">
          <table><thead><tr><th>类型</th><th>Provider</th><th>模型</th><th>启用</th><th>Secret</th><th>预算/用量</th><th>测试</th></tr></thead><tbody>{provider_rows}</tbody></table>
        </div>
        <h3>Provider 调用台账</h3>
        <div class="taxonomy-table-scroll">
          <table><thead><tr><th>时间</th><th>请求</th><th>Provider</th><th>估算成本</th><th>结果</th></tr></thead><tbody>{provider_usage_rows}</tbody></table>
        </div>
      </section>
      <section class="panel pdf-export-panel admin-panel-fit">
        <h2>PDF 导出任务</h2>
        <p class="explain">错题本和报告 PDF 由 Playwright 服务生成，任务记录文件大小、引擎版本和失败原因。</p>
        <table><thead><tr><th>类型</th><th>状态</th><th>文件</th><th>大小</th><th>引擎</th></tr></thead><tbody>{pdf_export_rows}</tbody></table>
      </section>
      <section class="panel"><h2>LLM Key</h2><table><thead><tr><th>Provider</th><th>模型</th><th>Key</th><th>测试</th></tr></thead><tbody>{llm_rows}</tbody></table></section>
      <section class="panel"><h2>解析任务</h2><table><thead><tr><th>文件</th><th>工具</th><th>状态</th><th>原因</th></tr></thead><tbody>{parse_rows}</tbody></table></section>
    </div>
  </section>

  <section class="admin-tab-panel" data-admin-tab-panel="system" role="tabpanel" hidden>
    <div class="workspace-grid">
      <section class="panel span-2 audit-log-panel admin-panel-fit"><div class="panel-head"><h2>审计日志</h2><a class="button-link" href="/backup/download">备份导出</a></div><table><thead><tr><th>时间</th><th>操作者</th><th>操作</th><th>资源</th></tr></thead><tbody>{audit_rows}</tbody></table></section>
    </div>
  </section>
</section>""".format(
        user_rows=user_rows,
        knowledge_total=taxonomy_summary["knowledge"]["total"],
        volume_total=level_counts[1],
        chapter_total=level_counts[2],
        section_total=level_counts[3],
        ability_total=taxonomy_summary["abilities"]["total"],
        ability_active=taxonomy_summary["abilities"]["active"],
        literacy_total=taxonomy_summary["literacy"]["total"],
        literacy_active=taxonomy_summary["literacy"]["active"],
        source_total=len(taxonomy_sources),
        taxonomy_version=escape(taxonomy_summary["version"]),
        source_rows=source_rows,
        reset_user_options=reset_user_options,
        runtime_health_rows=runtime_health_rows,
        provider_options=provider_options,
        provider_rows=provider_rows,
        provider_usage_rows=provider_usage_rows,
        mastery_trend=_mastery_trend,
        mastery_grade_columns=_mastery_grade_columns,
        export_options_json=export_options_json,
        active_label=escape(active_ontology["version_label"]),
        active_status=escape(active_ontology["status"]),
        publish_options=publish_options,
        ontology_rows=ontology_rows,
        parent_options=parent_options,
        node_options=node_options,
        module_options=module_options,
        node_rows=node_rows,
        edge_rows=edge_rows,
        ability_rows=ability_rows,
        ability_options=ability_options,
        literacy_parent_options=literacy_parent_options,
        literacy_options=literacy_options,
        literacy_rows=literacy_rows,
        identity_rows=identity_rows,
        pdf_export_rows=pdf_export_rows,
        llm_rows=llm_rows,
        parse_rows=parse_rows,
        privacy_rows=privacy_rows,
        audit_rows=audit_rows,
    )
    return render_layout("管理员端 - 高中物理闭环系统", user, body, "admin")


class PhysicsHandler(BaseHTTPRequestHandler):
    db_path = DEFAULT_DB_PATH
    demo_mode = False

    def log_message(self, format, *args):
        return

    def do_GET(self):
        try:
            self._do_GET()
        except DomainError as error:
            self._send_domain_error(error)
        except Exception:
            self.log_error("Unhandled exception while processing GET")
            self._send_json(
                {
                    "error": "internal_error",
                    "message": "Internal server error",
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/assets/"):
            self._serve_asset(path)
            return
        if path == "/login":
            self._send_html(render_login_page("", self.demo_mode))
            return
        if path == "/logout":
            self._handle_logout()
            return
        if path == "/sso/login":
            self._handle_sso_login()
            return
        if path == "/sso/callback":
            self._handle_sso_callback(parsed)
            return

        conn = connect(self.db_path)
        try:
            user = self._current_user(conn)
            if path == "/change-password":
                if not user:
                    self._redirect("/login")
                else:
                    self._send_html(render_change_password_page(user))
            elif user and user["must_change_password"]:
                self._redirect("/change-password")
            elif path == "/":
                self._redirect(self._home_for(user))
            elif path == "/app":
                if not user:
                    self._redirect("/login")
                elif user["role"] == "student":
                    repo = PhysicsRepository(conn)
                    self._send_html(render_student_app(user, repo.student_dashboard(user["id"])))
                else:
                    self._redirect(self._home_for(user))
            elif path == "/teacher":
                if not user:
                    self._redirect("/login")
                elif user["role"] in ("teacher", "admin"):
                    repo = PhysicsRepository(conn)
                    self._send_html(render_teacher_app(user, repo.teacher_dashboard(user["id"])))
                else:
                    self._send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            elif path == "/admin":
                if not user:
                    self._redirect("/login")
                elif user["role"] == "admin":
                    repo = PhysicsRepository(conn)
                    self._send_html(render_admin_app(user, repo.admin_dashboard(user["id"])))
                else:
                    self._send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            elif path.startswith("/export/wrong-book/"):
                if not user:
                    self._redirect("/login")
                else:
                    assessment_id = path.rsplit("/", 1)[-1]
                    if not AuthService(conn).can_assessment(
                        user,
                        "export",
                        assessment_id,
                    ):
                        raise PermissionDenied(
                            "You do not have access to this assessment"
                        )
                    query = parse_qs(parsed.query)
                    class_id = (query.get("class_id") or [""])[0] or None
                    student_id = (query.get("student_id") or [""])[0] or None
                    if user["role"] == "student":
                        class_id = None
                        student_id = user["id"]
                    repo = PhysicsRepository(conn)
                    self._send_html(
                        build_wrong_book_html(
                            repo,
                            user["id"],
                            assessment_id,
                            class_id=class_id,
                            student_id=student_id,
                        )
                    )
            elif path == "/backup/download":
                if not user or user["role"] != "admin":
                    self._send_error(HTTPStatus.FORBIDDEN, "Forbidden")
                else:
                    repo = PhysicsRepository(conn)
                    self._send_json(repo.export_backup(user["id"]), filename="highschoolphysics-backup.json")
            else:
                self._send_error(HTTPStatus.NOT_FOUND, "Not Found")
        finally:
            conn.close()

    def do_POST(self):
        try:
            self._do_POST()
        except json.JSONDecodeError:
            self._send_domain_error(
                InvalidRequest("Malformed JSON request body"),
                code="invalid_json",
            )
        except KeyError as error:
            self._send_domain_error(
                InvalidRequest("Missing required field: %s" % error.args[0])
            )
        except DomainError as error:
            self._send_domain_error(error)
        except ValueError as error:
            self._send_domain_error(InvalidRequest(str(error)))
        except Exception:
            self.log_error("Unhandled exception while processing POST")
            self._send_json(
                {
                    "error": "internal_error",
                    "message": "Internal server error",
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/login":
            self._handle_login()
            return

        conn = connect(self.db_path)
        try:
            user = self._current_user(conn)
            if not user:
                self._send_json({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            if user["must_change_password"] and path not in (
                "/change-password",
                "/api/password/change",
            ):
                raise PasswordChangeRequired(
                    "You must change your temporary password before continuing"
                )
            payload = self._read_payload()
            auth = AuthService(conn)
            if path in ("/change-password", "/api/password/change"):
                if payload.get("new_password") != payload.get(
                    "confirm_password",
                    payload.get("new_password"),
                ):
                    raise InvalidRequest("New password confirmation does not match")
                auth.change_password(
                    actor_id=user["id"],
                    user_id=user["id"],
                    current_password=payload.get("current_password", ""),
                    new_password=payload.get("new_password", ""),
                )
                if path == "/change-password":
                    self._redirect(self._home_for(auth.user_by_id(user["id"])))
                else:
                    self._send_json({"ok": True})
                return
            if path == "/api/password/reset":
                auth.reset_password(
                    user,
                    payload["target_user_id"],
                    payload["temporary_password"],
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "临时密码已重置，旧会话已失效",
                    }
                )
                return
            repo = PhysicsRepository(conn)
            if path == "/api/teacher/generate-candidate" and user["role"] in ("teacher", "admin"):
                result = repo.generate_llm_candidates(
                    user["id"],
                    payload.get("question_id", "q-newton-1"),
                )
                self._send_json({"ok": True, "candidate": result})
            elif path == "/api/teacher/approve-candidate" and user["role"] in ("teacher", "admin"):
                candidate_id = payload["candidate_id"]
                candidate = repo.get_candidate(candidate_id)
                repo.approve_candidate_tags(
                    user["id"],
                    candidate_id,
                    [item["id"] for item in candidate["knowledge_tags"][:1]],
                    [item["id"] for item in candidate["ability_tags"][:2]],
                )
                self._send_json({"ok": True})
            elif path == "/api/teacher/question" and user["role"] in ("teacher", "admin"):
                result = repo.create_question(
                    actor_id=user["id"],
                    stem=payload["stem"],
                    options=payload.get("options", {}),
                    answer=payload.get("answer", {}),
                    analysis=payload.get("analysis", ""),
                    question_type=payload["question_type"],
                    source=payload.get("source", "教师录入"),
                    grade=payload["grade"],
                    chapter=payload["chapter"],
                    difficulty=payload["difficulty"],
                    media=payload.get("media", []),
                    scenario=payload.get("scenario", ""),
                    quality_status=payload.get("quality_status", "draft"),
                    notes=payload.get("notes", ""),
                    source_school=payload.get("source_school", ""),
                    source_publisher=payload.get("source_publisher", ""),
                    exam_type=payload.get("exam_type", ""),
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "题目已保存",
                        "question": result,
                    }
                )
            elif path == "/api/teacher/question/update" and user["role"] in ("teacher", "admin"):
                result = repo.update_question(
                    actor_id=user["id"],
                    question_id=payload["question_id"],
                    stem=payload["stem"],
                    options=payload.get("options", {}),
                    answer=payload.get("answer", {}),
                    analysis=payload.get("analysis", ""),
                    question_type=payload["question_type"],
                    source=payload.get("source", "教师录入"),
                    grade=payload["grade"],
                    chapter=payload["chapter"],
                    difficulty=payload["difficulty"],
                    media=payload.get("media", []),
                    scenario=payload.get("scenario", ""),
                    quality_status=payload.get("quality_status", "draft"),
                    notes=payload.get("notes", ""),
                    review_status=payload.get("review_status"),
                    source_confidence=payload.get("source_confidence"),
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "题目已更新",
                        "question": result,
                    }
                )
            elif path == "/api/teacher/parse-task" and user["role"] in ("teacher", "admin"):
                task = repo.create_parse_task(
                    actor_id=user["id"],
                    paper_title=payload["paper_title"],
                    document_name=payload["document_name"],
                    source_text=payload["source_text"],
                    parser_mode=payload.get("parser_mode", "deterministic_text"),
                    fallback_policy=payload.get("fallback_policy", "fail_closed"),
                    source_school=payload.get("source_school", ""),
                    source_publisher=payload.get("source_publisher", ""),
                    exam_type=payload.get("exam_type", ""),
                    grade=payload.get("grade", ""),
                    term=payload.get("term", ""),
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "解析任务已创建",
                        "task": task,
                    }
                )
            elif path == "/api/teacher/parse-task/run" and user["role"] in ("teacher", "admin"):
                result = repo.run_parse_task(
                    actor_id=user["id"],
                    task_id=payload["task_id"],
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "解析任务已完成",
                        "result": result,
                    }
                )
            elif path == "/api/teacher/parsed-question/save" and user["role"] in ("teacher", "admin"):
                overrides = dict(payload.get("overrides", {}))
                for key in (
                    "stem",
                    "options",
                    "answer",
                    "analysis",
                    "question_type",
                    "source",
                    "grade",
                    "chapter",
                    "difficulty",
                    "media",
                    "scenario",
                    "quality_status",
                    "notes",
                    "review_status",
                ):
                    if key in payload:
                        overrides[key] = payload[key]
                question = repo.save_parsed_question(
                    actor_id=user["id"],
                    parsed_item_id=payload["parsed_item_id"],
                    overrides=overrides,
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "解析题目已保存",
                        "question": question,
                    }
                )
            elif path == "/api/teacher/question-tags/confirm" and user["role"] in ("teacher", "admin"):
                tags = repo.confirm_question_tags(
                    actor_id=user["id"],
                    question_id=payload["question_id"],
                    candidate_id=payload.get("candidate_id") or None,
                    knowledge_node_ids=payload.get("knowledge_node_ids", []),
                    ability_tag_ids=payload.get("ability_tag_ids", []),
                    literacy_tag_ids=payload.get("literacy_tag_ids", []),
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "题目标签已确认",
                        "tags": tags,
                    }
                )
            elif path == "/api/teacher/paper-assembly" and user["role"] in ("teacher", "admin"):
                result = repo.assemble_paper(
                    actor_id=user["id"],
                    title=payload["title"],
                    source=payload.get("source", "校本组卷"),
                    question_items=payload.get("question_items", []),
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "试卷与答题卡模板已生成",
                        "result": result,
                    }
                )
            elif path == "/api/teacher/assessment-from-paper" and user["role"] in ("teacher", "admin"):
                assessment = repo.create_assessment_from_paper(
                    actor_id=user["id"],
                    paper_id=payload["paper_id"],
                    class_id=payload["class_id"],
                    title=payload["title"],
                    term=payload.get("term", ""),
                    grade=payload["grade"],
                    scheduled_at=payload["scheduled_at"],
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "测评已创建",
                        "assessment": assessment,
                    }
                )
            elif path == "/api/teacher/ocr-import" and user["role"] in ("teacher", "admin"):
                scan = repo.import_ocr_responses(
                    actor_id=user["id"],
                    assessment_id=payload["assessment_id"],
                    source_name=payload.get("source_name", "OCR 导入"),
                    recognizer=payload.get("recognizer", "PaddleOCR"),
                    recognizer_version=payload.get(
                        "recognizer_version",
                        "reserved-local-v2",
                    ),
                    items=payload.get("items", []),
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "OCR 结果已导入",
                        "scan": scan,
                    }
                )
            elif path == "/api/teacher/resolve-review" and user["role"] in ("teacher", "admin"):
                if not AuthService(conn).can_response(
                    user,
                    "review",
                    payload["response_id"],
                ):
                    raise PermissionDenied(
                        "You do not have access to this response"
                    )
                repo.resolve_review_item(
                    user["id"],
                    payload["response_id"],
                    payload.get("corrected_answer", "C"),
                    payload.get("reason", "教师复核确认"),
                )
                self._send_json({"ok": True})
            elif path == "/api/teacher/grade" and user["role"] in ("teacher", "admin"):
                assessment_id = payload.get("assessment_id", "assess-week-1")
                if not AuthService(conn).can_assessment(
                    user,
                    "grade",
                    assessment_id,
                ):
                    raise PermissionDenied(
                        "You do not have access to this assessment"
                    )
                result = repo.grade_assessment(
                    user["id"],
                    assessment_id,
                    publish=bool(payload.get("publish", True)),
                )
                self._send_json({"ok": True, "result": result})
            elif path == "/api/teacher/grading-revision" and user["role"] in ("teacher", "admin"):
                revision = repo.apply_grading_revision(
                    actor_id=user["id"],
                    assessment_id=payload["assessment_id"],
                    reason=payload["reason"],
                    items=payload.get("items", []),
                )
                self._send_json({"ok": True, "revision": revision})
            elif path == "/api/teacher/error-reason-tag" and user["role"] in ("teacher", "admin"):
                tag = repo.create_error_reason_tag(
                    actor_id=user["id"],
                    code=payload["code"],
                    name=payload["name"],
                    description=payload.get("description", ""),
                )
                self._send_json({"ok": True, "tag": tag})
            elif path == "/api/teacher/wrong-question/error-tags" and user["role"] in ("teacher", "admin"):
                wrong_question = repo.tag_wrong_question_error(
                    actor_id=user["id"],
                    wrong_question_id=payload["wrong_question_id"],
                    tag_ids=payload.get("tag_ids", []),
                    note=payload.get("note", ""),
                )
                self._send_json({"ok": True, "wrong_question": wrong_question})
            elif path == "/api/student/redo-attempt" and user["role"] == "student":
                attempt = repo.submit_redo_attempt(
                    actor_id=user["id"],
                    wrong_question_id=payload["wrong_question_id"],
                    answer=payload.get("answer", ""),
                )
                self._send_json({"ok": True, "attempt": attempt})
            elif path == "/api/teacher/redo-attempt/review" and user["role"] in ("teacher", "admin"):
                attempt = repo.review_redo_attempt(
                    actor_id=user["id"],
                    attempt_id=payload["attempt_id"],
                    score=payload["score"],
                    feedback=payload.get("feedback", ""),
                )
                self._send_json({"ok": True, "attempt": attempt})
            elif path == "/api/student/mastery" and user["role"] == "student":
                if truthy(payload.get("clear")):
                    result = repo.clear_mastery_mark(
                        user["id"],
                        payload["wrong_question_id"],
                    )
                else:
                    result = repo.set_mastery_mark(
                        user["id"],
                        payload["wrong_question_id"],
                        payload["level"],
                        payload.get("note", ""),
                    )
                self._send_json({"ok": True, "result": result})
            elif path == "/api/student/knowledge-mastery" and user["role"] == "student":
                if truthy(payload.get("clear")):
                    result = repo.clear_knowledge_mastery_mark(
                        actor_id=user["id"],
                        student_id=user["id"],
                        knowledge_node_id=payload["knowledge_node_id"],
                    )
                else:
                    result = repo.set_knowledge_mastery_mark(
                        actor_id=user["id"],
                        student_id=user["id"],
                        knowledge_node_id=payload["knowledge_node_id"],
                        level=payload["level"],
                        note=payload.get("note", ""),
                    )
                self._send_json({"ok": True, "message": "知识点掌握标记已更新", "result": result})
            elif path == "/api/teacher/wrong-book-pdf":
                if user["role"] not in ("teacher", "admin"):
                    raise PermissionDenied("Teacher or admin access required")
                task = repo.generate_wrong_book_pdf(
                    actor_id=user["id"],
                    assessment_id=payload["assessment_id"],
                    class_id=payload.get("class_id") or None,
                    student_id=payload.get("student_id") or None,
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "错题本 PDF 已生成",
                        "task": task,
                    }
                )
            elif path == "/api/admin/taxonomy/install":
                if user["role"] != "admin":
                    raise PermissionDenied("Admin role required")
                result = repo.install_default_taxonomy(
                    actor_id=user["id"],
                    publish=truthy(payload.get("publish", "0")),
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "默认物理体系已安装",
                        "result": result,
                    }
                )
            elif path == "/api/admin/export-profile":
                if user["role"] != "admin":
                    raise PermissionDenied("Admin role required")
                profile = repo.save_export_profile(
                    actor_id=user["id"],
                    name=payload["name"],
                    options=payload.get("options", {}),
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "导出配置已保存",
                        "profile": profile,
                    }
                )
            elif path == "/api/admin/runtime-check":
                if user["role"] != "admin":
                    raise PermissionDenied("Admin role required")
                checks = repo.record_runtime_capability_checks(user["id"])
                self._send_json(
                    {
                        "ok": True,
                        "message": "生产化就绪度检查已记录",
                        "checks": checks,
                    }
                )
            elif path == "/api/admin/provider-config":
                if user["role"] != "admin":
                    raise PermissionDenied("Admin role required")
                config = repo.save_provider_config(
                    actor_id=user["id"],
                    provider_kind=payload["provider_kind"],
                    provider_name=payload["provider_name"],
                    model_name=payload.get("model_name", ""),
                    secret=payload.get("secret", ""),
                    api_endpoint=payload.get("api_endpoint", ""),
                    enabled=truthy(payload.get("enabled", "0")),
                    daily_call_limit=int(payload.get("daily_call_limit", 0) or 0),
                    monthly_budget_cents=float(
                        payload.get("monthly_budget_cents", 0) or 0
                    ),
                    per_call_max_cents=float(
                        payload.get("per_call_max_cents", 0) or 0
                    ),
                    input_cost_per_1k_cents=float(
                        payload.get("input_cost_per_1k_cents", 0) or 0
                    ),
                    output_cost_per_1k_cents=float(
                        payload.get("output_cost_per_1k_cents", 0) or 0
                    ),
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "Provider 配置已保存",
                        "config": config,
                    }
                )
            elif path == "/api/admin/provider-test":
                if user["role"] != "admin":
                    raise PermissionDenied("Admin role required")
                config = repo.test_provider_connection(
                    actor_id=user["id"],
                    provider_config_id=payload["provider_config_id"],
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "Provider 配置测试已完成",
                        "config": config,
                    }
                )
            elif path == "/api/admin/oidc-provider":
                if user["role"] != "admin":
                    raise PermissionDenied("Admin role required")
                config = repo.save_oidc_provider_config(
                    actor_id=user["id"],
                    provider_name=payload["provider_name"],
                    issuer=payload["issuer"],
                    client_id=payload["client_id"],
                    client_secret=payload.get("client_secret", ""),
                    authorization_endpoint=payload["authorization_endpoint"],
                    token_endpoint=payload.get("token_endpoint", ""),
                    userinfo_endpoint=payload.get("userinfo_endpoint", ""),
                    enabled=truthy(payload.get("enabled", "0")),
                    binding_policy=payload.get(
                        "binding_policy",
                        "existing_user_only",
                    ),
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "OIDC SSO 配置已保存",
                        "config": config,
                    }
                )
            elif path == "/api/admin/literacy-tag":
                if user["role"] != "admin":
                    raise PermissionDenied("Admin role required")
                result = repo.create_literacy_tag(
                    actor_id=user["id"],
                    stable_code=payload["stable_code"],
                    name=payload["name"],
                    parent_id=payload.get("parent_id") or None,
                    description=payload.get("description", ""),
                    source=payload.get("source", "教师校本"),
                    change_note=payload.get("change_note", ""),
                    enabled=truthy(payload.get("enabled", "1")),
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "核心素养标签已新增",
                        "result": result,
                    }
                )
            elif path == "/api/admin/literacy-tag/update":
                if user["role"] != "admin":
                    raise PermissionDenied("Admin role required")
                enabled = truthy(payload.get("enabled", "1"))
                change_note = payload.get("change_note", "")
                if not enabled and not change_note.strip():
                    raise InvalidRequest(
                        "Disabling a literacy tag requires change_note"
                    )
                repo.update_literacy_tag(
                    actor_id=user["id"],
                    literacy_id=payload["literacy_id"],
                    name=payload["name"],
                    description=payload.get("description", ""),
                    source=payload.get("source", ""),
                    change_note=change_note,
                )
                result = repo.set_literacy_tag_enabled(
                    actor_id=user["id"],
                    literacy_id=payload["literacy_id"],
                    enabled=enabled,
                    change_note=change_note,
                )
                self._send_json(
                    {
                        "ok": True,
                        "message": "核心素养标签已保存",
                        "result": result,
                    }
                )
            elif path == "/api/admin/knowledge-node" and user["role"] == "admin":
                result = repo.create_knowledge_node(
                    actor_id=user["id"],
                    stable_code=payload["stable_code"],
                    name=payload["name"],
                    parent_id=payload.get("parent_id") or None,
                    aliases=payload.get("aliases", ""),
                    source=payload.get("source", "教师校本"),
                    description=payload.get("description", ""),
                    textbook_scope=payload.get("textbook_scope", ""),
                    change_note=payload.get("change_note", ""),
                    enabled=truthy(payload.get("enabled", "1")),
                )
                self._send_json({"ok": True, "message": "知识节点已新增", "result": result})
            elif path == "/api/admin/knowledge-node/update" and user["role"] == "admin":
                result = repo.update_knowledge_node(
                    actor_id=user["id"],
                    node_id=payload["node_id"],
                    name=payload["name"],
                    aliases=payload.get("aliases", ""),
                    source=payload.get("source", ""),
                    description=payload.get("description", ""),
                    textbook_scope=payload.get("textbook_scope", ""),
                    change_note=payload.get("change_note", ""),
                )
                result = repo.set_knowledge_node_enabled(
                    actor_id=user["id"],
                    node_id=payload["node_id"],
                    enabled=truthy(payload.get("enabled", "1")),
                    change_note=payload.get("change_note", ""),
                )
                self._send_json({"ok": True, "message": "知识节点已保存", "result": result})
            elif path == "/api/admin/knowledge-edge" and user["role"] == "admin":
                result = repo.create_knowledge_edge(
                    actor_id=user["id"],
                    source_node_id=payload["source_node_id"],
                    target_node_id=payload["target_node_id"],
                    relation_type=payload["relation_type"],
                    bidirectional=truthy(payload.get("bidirectional", "1")),
                    rationale=payload.get("rationale", ""),
                )
                self._send_json({"ok": True, "message": "语义关系已新增", "result": result})
            elif path == "/api/admin/ability-tag" and user["role"] == "admin":
                result = repo.create_ability_tag(
                    actor_id=user["id"],
                    stable_code=payload["stable_code"],
                    name=payload["name"],
                    description=payload.get("description", ""),
                    source=payload.get("source", ""),
                    enabled=truthy(payload.get("enabled", "1")),
                )
                self._send_json({"ok": True, "message": "能力标签已新增", "result": result})
            elif path == "/api/admin/ability-tag/update" and user["role"] == "admin":
                result = repo.update_ability_tag(
                    actor_id=user["id"],
                    ability_tag_id=payload["ability_tag_id"],
                    name=payload["name"],
                    description=payload.get("description", ""),
                    source=payload.get("source", ""),
                    change_note=payload.get("change_note", ""),
                )
                result = repo.set_ability_tag_enabled(
                    actor_id=user["id"],
                    ability_tag_id=payload["ability_tag_id"],
                    enabled=truthy(payload.get("enabled", "1")),
                    change_note=payload.get("change_note", ""),
                )
                self._send_json({"ok": True, "message": "能力标签已保存", "result": result})
            elif path == "/api/admin/ontology-draft" and user["role"] == "admin":
                result = repo.create_ontology_draft(
                    actor_id=user["id"],
                    version_label=payload["version_label"],
                    source_summary=payload["source_summary"],
                )
                self._send_json({"ok": True, "message": "本体草稿已创建", "result": result})
            elif path == "/api/admin/ontology-publish" and user["role"] == "admin":
                ontology_version_id = payload["ontology_version_id"]
                if payload.get("transition") == "review":
                    result = repo.submit_ontology_for_review(user["id"], ontology_version_id)
                    message = "本体版本已送审"
                else:
                    result = repo.publish_ontology_version(user["id"], ontology_version_id)
                    message = "本体版本已发布"
                self._send_json({"ok": True, "message": message, "result": result})
            elif path == "/api/admin/import-student" and user["role"] == "admin":
                user_id = repo.import_student(
                    user["id"],
                    payload.get("username", "stu_demo"),
                    payload.get("display_name", "新学生"),
                    payload.get("student_no", "1999"),
                    payload.get("class_id", "class-physics-1"),
                    hash_password(payload.get("temp_password", "Temp123456")),
                )
                self._send_json({"ok": True, "user_id": user_id})
            elif path == "/api/admin/import-teacher":
                if user["role"] != "admin":
                    raise PermissionDenied("Admin role required")
                username = payload["username"]
                display_name = payload["display_name"]
                temp_password = payload["temp_password"]
                validate_password(temp_password)
                user_id = repo.create_teacher(
                    user["id"],
                    username,
                    display_name,
                    hash_password(temp_password),
                )
                self._send_json({"ok": True, "user_id": user_id})
            elif path.startswith("/api/admin/teacher/") and path.endswith(
                "/assign-classes"
            ):
                if user["role"] != "admin":
                    raise PermissionDenied("Admin role required")
                teacher_id = path[len("/api/admin/teacher/"):-len("/assign-classes")]
                if not teacher_id:
                    raise InvalidRequest("Missing teacher_id in path")
                class_ids = payload.get("class_ids", [])
                if isinstance(class_ids, str):
                    class_ids = [
                        item.strip()
                        for item in class_ids.replace(",", " ").split()
                        if item.strip()
                    ]
                if not isinstance(class_ids, list):
                    raise InvalidRequest("class_ids must be an array")
                result = repo.set_teacher_classes(
                    user["id"],
                    teacher_id,
                    class_ids,
                )
                self._send_json({"ok": True, **result})
            else:
                self._send_json({"error": "not_found_or_forbidden"}, status=HTTPStatus.NOT_FOUND)
        finally:
            conn.close()

    def _handle_login(self):
        payload = self._read_payload()
        conn = connect(self.db_path)
        try:
            auth = AuthService(conn)
            try:
                result = auth.login(
                    payload.get("username", ""),
                    payload.get("password", ""),
                    self.headers.get("User-Agent", ""),
                )
            except ValueError:
                self._send_html(
                    render_login_page(
                        "账号或密码错误",
                        self.demo_mode,
                    ),
                    status=HTTPStatus.UNAUTHORIZED,
                )
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            target = (
                "/change-password"
                if result.user["must_change_password"]
                else self._home_for(result.user)
            )
            self.send_header("Location", target)
            self.send_header(
                "Set-Cookie",
                "hsp_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000" % result.token,
            )
            self.end_headers()
        finally:
            conn.close()

    def _handle_logout(self):
        token = self._session_token()
        conn = connect(self.db_path)
        try:
            if token:
                user = self._current_user(conn)
                AuthService(conn).logout(token, user["id"] if user else None)
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "hsp_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.end_headers()
        finally:
            conn.close()

    def _handle_sso_login(self):
        conn = connect(self.db_path)
        try:
            repo = PhysicsRepository(conn)
            provider = repo.enabled_oidc_provider()
            if provider is None:
                self._send_error(HTTPStatus.NOT_FOUND, "SSO provider is not configured")
                return
            forwarded_prefix = (self.headers.get("X-Forwarded-Prefix") or "").rstrip("/")
            redirect_uri = "%s://%s/sso/callback" % (
                "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http",
                "%s%s" % (self.headers.get("X-Forwarded-Host") or self.headers.get("Host"), forwarded_prefix),
            )
            login = repo.start_sso_login(provider["id"], redirect_uri)
            self._redirect(login["authorization_url"])
        finally:
            conn.close()

    def _handle_sso_callback(self, parsed):
        query = parse_qs(parsed.query)
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        if not code or not state:
            self._send_error(HTTPStatus.BAD_REQUEST, "Missing SSO callback parameters")
            return
        conn = connect(self.db_path)
        try:
            state_row = conn.execute(
                "select * from sso_login_states where state = ?",
                (state,),
            ).fetchone()
            if state_row is None:
                self._send_error(HTTPStatus.BAD_REQUEST, "Invalid SSO state")
                return
            provider = conn.execute(
                "select * from auth_provider_configs where id = ?",
                (state_row["provider_config_id"],),
            ).fetchone()
            if provider is None:
                self._send_error(HTTPStatus.BAD_REQUEST, "Invalid SSO provider")
                return
            repo = PhysicsRepository(conn)
            client_config = json.loads(provider["client_config_json"])
            client_secret = repo._provider_secret_store().decrypt(provider["secret_ciphertext"])
            claims = exchange_oidc_code_for_claims(
                client_config,
                client_secret,
                code,
                state_row["code_verifier"],
                state_row["redirect_uri"],
            )
            result = repo.complete_sso_callback(state, claims)
            session = AuthService(conn).session_for_user(
                result["user"]["id"],
                self.headers.get("User-Agent", ""),
            )
            self.send_response(HTTPStatus.SEE_OTHER)
            target = (
                "/change-password"
                if session.user["must_change_password"]
                else self._home_for(session.user)
            )
            self.send_header("Location", target)
            self.send_header(
                "Set-Cookie",
                "hsp_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000" % session.token,
            )
            self.end_headers()
        except (PermissionDenied, OidcExchangeError) as exc:
            self._send_error(HTTPStatus.UNAUTHORIZED, str(exc))
        finally:
            conn.close()

    def _current_user(self, conn):
        return AuthService(conn).user_from_token(self._session_token())

    def _session_token(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if "=" not in part:
                continue
            key, value = part.strip().split("=", 1)
            if key == "hsp_session":
                return value
        return None

    def _read_payload(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return json.loads(raw or "{}")
        parsed = parse_qs(raw)
        return {key: values[-1] for key, values in parsed.items()}

    def _home_for(self, user):
        if not user:
            return "/login"
        if user["role"] == "student":
            return "/app"
        if user["role"] == "teacher":
            return "/teacher"
        return "/admin"

    def _send_html(self, content, status=HTTPStatus.OK):
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, payload, status=HTTPStatus.OK, filename=None):
        encoded = dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if filename:
            self.send_header("Content-Disposition", "attachment; filename=%s" % filename)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_domain_error(self, error, code=None):
        self._send_json(
            {
                "error": code or error.code,
                "message": error.message,
            },
            status=error.status,
        )

    def _send_error(self, status, message):
        self._send_html(render_layout(str(status), None, "<section class='empty-state'>%s</section>" % escape(message)), status)

    def _redirect(self, target):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", target)
        self.end_headers()

    def _serve_asset(self, path):
        name = Path(path).name
        asset_path = ASSET_DIR / name
        if not asset_path.exists():
            self._send_error(HTTPStatus.NOT_FOUND, "Asset not found")
            return
        content_type = "text/css; charset=utf-8" if name.endswith(".css") else "application/javascript; charset=utf-8"
        payload = asset_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run(
    host="127.0.0.1",
    port=8765,
    db_path=DEFAULT_DB_PATH,
    demo_mode=False,
):
    ensure_database(db_path, demo_mode=demo_mode)
    PhysicsHandler.db_path = db_path
    PhysicsHandler.demo_mode = demo_mode
    server = ThreadingHTTPServer((host, port), PhysicsHandler)
    print("HighSchoolPhysics running at http://%s:%s" % (host, port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true")
    mode.add_argument("--init-admin", metavar="USERNAME")
    parser.add_argument(
        "--admin-display-name",
        default="系统管理员",
    )
    parser.add_argument("--school-name", default="本地学校")
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    if args.init_admin:
        ensure_database(db_path, demo_mode=False)
        password = getpass.getpass("管理员密码: ")
        confirmation = getpass.getpass("再次输入管理员密码: ")
        if password != confirmation:
            parser.error("administrator passwords do not match")
        try:
            validate_password(password)
            conn = connect(db_path)
            try:
                user_id = bootstrap_admin(
                    conn,
                    username=args.init_admin,
                    display_name=args.admin_display_name,
                    password_hash=hash_password(password),
                    school_name=args.school_name,
                )
            finally:
                conn.close()
        except DomainError as error:
            parser.error(error.message)
        except ValueError as error:
            parser.error(str(error))
        print(
            "Created administrator %s (%s)"
            % (args.init_admin, user_id)
        )
        return
    run(
        host=args.host,
        port=args.port,
        db_path=db_path,
        demo_mode=args.demo,
    )


if __name__ == "__main__":
    main()
