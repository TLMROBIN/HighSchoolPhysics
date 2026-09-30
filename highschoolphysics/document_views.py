"""Server-rendered teacher pages for whole-document import and review."""

from __future__ import annotations

import hashlib
import html

from .document_models import canonical_json
from .question_content import serialize_question_md
from .question_rendering import render_question


def _e(value):
    return html.escape("" if value is None else str(value), quote=True)


def _issue_identity(issue):
    return hashlib.sha256(canonical_json(issue).encode("utf-8")).hexdigest()[:20]


def _status(status):
    return {
        "queued": "排队中",
        "running": "转换中",
        "parsed": "待复核",
        "partially_parsed": "部分完成",
        "failed": "转换失败",
        "cancelled": "已取消",
    }.get(status, status)


def documents_home(user, tasks):
    rows = []
    for task in tasks:
        task_id = task["id"]
        review = '<a href="/documents/review?task_id=%s">打开复核</a>' % _e(task_id)
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                _e(task["file_name"]),
                _e(_status(task["status"])),
                _e(task.get("phase", "")),
                _e(task.get("created_at", "")),
                review,
            )
        )
    task_table = (
        "<table class='document-task-table'><thead><tr><th>文件</th><th>状态</th><th>阶段</th><th>创建时间</th><th></th></tr></thead><tbody>%s</tbody></table>"
        % "".join(rows)
        if rows
        else "<p class='document-empty'>还没有导入任务。</p>"
    )
    return """<section class="document-page" data-document-home data-actor-id="%s">
  <div class="document-heading"><div><p class="eyebrow">题库内容入库</p><h1>导入整份试卷</h1><p>选择 Word 或 PDF 原件。系统保存原卷，自动转换和拆题，再由教师集中复核。</p></div><a class="button-secondary" href="/teacher">返回教师工作台</a></div>
  <form class="document-upload-form" id="document-upload-form">
    <label>试卷文件<input id="document-file" type="file" accept=".docx,.doc,.pdf,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" required></label>
    <label>试卷名称<input id="document-title" type="text" maxlength="240" placeholder="可留空，使用文件名"></label>
    <label>PDF 解析方式<select id="document-parser-mode" name="parser_mode"><option value="mineru_api" selected>MinerU 云端 API（推荐）</option><option value="mineru_local">服务器本地 MinerU</option></select></label>
    <p class="document-parser-help">DOCX 继续使用可编辑正文解析；PDF 云端模式会将原文件发送至 MinerU 官方 API。</p>
    <button type="submit">上传并开始转换</button>
    <div class="document-upload-progress" id="document-upload-progress" aria-live="polite"></div>
  </form>
  <section class="document-task-section"><h2>我的导入任务</h2><div id="document-task-list">%s</div></section>
  <p class="document-limits">支持 DOCX、旧 DOC、PDF；单文件最大 50 MiB。DOCX 在本机服务器转换；PDF 可选官方云端 API 或本机 MinerU，云端解析会把文件发送到 MinerU，且仅在明确选择后使用。解析失败会显示错误，不会改用另一条识别路径。</p>
</section>""" % (_e(user.get("id", "")), task_table)


def _issue_list(item):
    issues = item.get("issues", [])
    if not issues:
        return "<p class='issue-clear'>当前没有已知复核事项。</p>"
    entries = []
    for issue in issues:
        severity = issue.get("severity", "review")
        resolved = issue.get("state") == "resolved" or severity == "info"
        checkbox = "" if resolved else (
            '<label class="issue-resolve"><input type="checkbox" data-issue-id="%s">已对照原卷核对</label>' % _e(_issue_identity(issue))
        )
        entries.append(
            '<li class="issue issue-%s %s"><strong>%s</strong><span>%s</span>%s</li>'
            % (
                _e(severity),
                "issue-resolved" if resolved else "",
                _e("已记录核对" if resolved else "需核对" if severity == "review" else "需修正" if severity == "blocking" else "提示"),
                _e(issue.get("message") or issue.get("code", "")),
                checkbox,
            )
        )
    return "<ul class='document-issues'>%s</ul>" % "".join(entries)


def _source_pages(document):
    pages = []
    for span in document.get("source_spans", []):
        locator = span.get("source_locator") or {}
        raw_page = locator.get("page")
        if isinstance(raw_page, bool):
            continue
        if isinstance(raw_page, int):
            page = raw_page
        elif isinstance(raw_page, str) and raw_page.strip().isdigit():
            page = int(raw_page.strip())
        else:
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    return pages


def _source_mapping_controls(items):
    candidates = [item for item in items if not item.get("published_revision_id")]
    if len(candidates) < 2:
        return ""
    candidate_labels = {
        item["id"]: "%s 题（%s）" % (
            (item.get("document") or {}).get("number", "未编号"),
            item["id"][-8:],
        )
        for item in candidates
    }
    source_groups = {}
    for item in items:
        document = item.get("document") or {}
        for span in document.get("source_spans", []):
            fingerprint = hashlib.sha256(canonical_json(span).encode("utf-8")).hexdigest()
            group = source_groups.setdefault(fingerprint, {"span": span, "owners": [], "locked": False})
            if item["id"] not in group["owners"]:
                group["owners"].append(item["id"])
            group["locked"] = group["locked"] or bool(item.get("published_revision_id"))
    rows = []
    for fingerprint, group in source_groups.items():
        span = group["span"]
        locator = span.get("source_locator") or {}
        page = locator.get("page")
        label = "第 %s 页" % page if page else "原卷位置"
        select_options = []
        for item_id, candidate_label in candidate_labels.items():
            selected = " selected" if item_id in group["owners"] else ""
            select_options.append(
                '<option value="%s"%s>%s</option>'
                % (_e(item_id), selected, _e(candidate_label))
            )
        disabled = " disabled" if group["locked"] else ""
        rows.append(
            '<label class="source-mapping-row"><span>%s · <code>%s</code></span>'
            '<select multiple size="2" data-source-owner data-source-locked="%s" data-source-fingerprint="%s" '
            'data-source-span="%s" data-initial-owners="%s" aria-label="来源块归属"%s>%s</select></label>'
            % (
                _e(label),
                _e(span.get("block_id", "")),
                "true" if group["locked"] else "false",
                _e(fingerprint),
                _e(canonical_json(span)),
                _e(",".join(group["owners"])),
                disabled,
                "".join(select_options),
            )
        )
    if not rows:
        return ""
    return (
        '<details class="source-mapping-tools"><summary>原卷块归属（%d 项）</summary>'
        '<p>可把原卷文字块分配到其他草稿题；每项至少保留一个归属。图片在下方插入题目 Markdown 后可随正文移动。</p>'
        '<div class="source-mapping-list">%s</div>'
        '<button type="button" data-save-source-mapping>保存来源块调整</button>'
        '<span data-source-mapping-status aria-live="polite"></span></details>'
        % (len(rows), "".join(rows))
    )


def _source_asset_gallery(task_id, assets):
    if not assets:
        return ""
    figures = []
    for asset in assets:
        locators = asset.get("source_locators") or [{}]
        pages = sorted({str(item.get("page")) for item in locators if item.get("page")})
        page_label = " · 第 %s 页" % "、".join(pages) if pages else ""
        src = "/api/documents/assets/%s?task_id=%s" % (_e(asset["id"]), _e(task_id))
        figures.append(
            '<figure class="source-asset"><img src="%s" alt="原卷图片资源%s" loading="lazy">'
            '<figcaption><code>%s</code>%s</figcaption>'
            '<button type="button" data-insert-source-asset="%s">插入到当前题目</button></figure>'
            % (src, _e(page_label), _e(asset["id"]), _e(page_label), _e(asset["id"]))
        )
    return (
        '<details class="source-asset-tools"><summary>可编辑的原卷图片资源（%d 张）</summary>'
        '<p>先把光标放到目标题目的 Markdown 编辑框，再插入图片；图片仍是独立资源，不会变成截图正文。</p>'
        '<div class="source-asset-list">%s</div></details>'
        % (len(figures), "".join(figures))
    )


def document_review_page(task, items, source_assets=()):
    cards = []
    source_url = "/api/documents/tasks/%s/preview" % _e(task["id"])
    reordering_locked = any(item.get("published_revision_id") for item in items) or int(task.get("item_count", len(items))) > len(items)
    for index, item in enumerate(items):
        document = item.get("document") or {}
        item_id = item["id"]
        markdown = serialize_question_md(document) if document else ""
        asset_url = lambda asset_id, tid=task["id"]: "/api/documents/assets/%s?task_id=%s" % (_e(asset_id), _e(tid))
        preview = render_question(document, asset_url=asset_url, include_solution=True) if document else "<p>没有可预览的可编辑题目内容。</p>"
        published = bool(item.get("published_revision_id"))
        export_links = (
            '<a href="/api/documents/items/%s/export.zip">下载本题离线包</a> · '
            '<a href="/api/documents/items/%s/export.zip?include_solution=1">含答案包</a>' % (_e(item_id), _e(item_id))
            if published else ""
        )
        move_disabled = "disabled" if reordering_locked or published else ""
        source_spans = document.get("source_spans", [])
        split_block_id = next((span.get("block_id") for span in source_spans if span.get("block_id")), "")
        order_controls = (
            '<span class="candidate-order-actions" aria-label="调整题目顺序">'
            '<button type="button" data-candidate-move="up" aria-label="上移题目" %s>上移</button>'
            '<button type="button" data-candidate-move="down" aria-label="下移题目" %s>下移</button>'
            "</span>"
            % (
                "disabled" if move_disabled or index == 0 else "",
                "disabled" if move_disabled or index == len(items) - 1 else "",
            )
        )
        restructure_controls = (
            '<span class="candidate-restructure-actions" aria-label="拆分或合并草稿题目">'
            '<button type="button" data-restructure-split data-source-block-id="%s" %s>在题干光标处分题</button>'
            '<button type="button" data-restructure-merge %s>与下一题合并</button>'
            '</span>'
            % (
                _e(split_block_id),
                "disabled" if move_disabled or not split_block_id else "",
                "disabled" if move_disabled or published or index == len(items) - 1 else "",
            )
        )
        pages = _source_pages(document)
        source_label = "原卷定位：%s" % ("、".join(str(page) for page in pages) + " 页" if pages else "Word 段落/表格位置")
        source_link = (
            ' · <a href="%s?jump_page=%d#page=%d" target="document-source-preview">跳转到第 %d 页</a>'
            % (_e(source_url), pages[0], pages[0], pages[0])
            if pages else ""
        )
        cards.append(
            """<article class="document-question-card" data-document-item="%s" data-revision="%s" data-published="%s">
  <header class="question-card-heading"><label class="question-select"><input type="checkbox" data-publish-select %s>纳入本次批量入库</label><strong>第 <span data-question-number>%s</span> 题</strong><label class="question-number-field">题号<input type="text" maxlength="32" required data-question-number-edit value="%s" %s></label><span>复核版本 <span data-revision-label>%s</span></span><span class="publish-state">%s</span>%s</header>
  <div class="question-source-link">%s%s</div>
  %s
  <label class="markdown-editor-label">整题 Markdown（含稳定选项和小问 ID）<textarea class="question-markdown" rows="18" spellcheck="false">%s</textarea></label>
  <label class="review-note-label">复核记录<textarea class="review-note" rows="2" placeholder="标记识别事项已核对时，说明对照了原卷的哪一处。"></textarea></label>
  <div class="question-card-actions"><button type="button" data-preview-item>更新预览</button><button type="button" data-save-item>保存草稿</button>%s%s<span class="save-status" aria-live="polite"></span></div>
  <div class="question-preview"><h3>渲染预览</h3><div data-preview-body>%s</div></div>
</article>"""
            % (
                _e(item_id),
                _e(item.get("review_revision", 1)),
                "true" if published else "false",
                "disabled checked" if published else "",
                _e(document.get("number", "")),
                _e(document.get("number", "")),
                "disabled" if published else "",
                _e(item.get("review_revision", 1)),
                "已入库" if published else "草稿",
                order_controls,
                _e(source_label),
                source_link,
                _issue_list(item),
                html.escape(markdown, quote=False),
                restructure_controls,
                export_links,
                preview,
            )
        )
    if task.get("status") not in ("parsed", "partially_parsed"):
        source_panel = "<div class='source-unavailable'>转换完成后显示原卷预览。当前任务：%s</div>" % _e(_status(task.get("status", "")))
    else:
        source_panel = '<iframe title="原卷 PDF 预览" name="document-source-preview" src="%s" loading="lazy"></iframe>' % source_url
    answer_attachment = ""
    if task.get("document_role") == "paper" and task.get("original_paper_id"):
        answer_attachment = """<section class="answer-attachment" data-answer-attachment data-paper-id="%s">
  <h2>关联独立答案或解析文件</h2>
  <p>先上传答案文件。系统只提出题号唯一且答案为空的草稿匹配；逐条查看 Markdown 并勾选确认后，答案会以“待复核”状态写入，不会自动发布或判分。</p>
  <form data-answer-upload-form><label>答案/解析文件<input type="file" accept=".docx,.doc,.pdf,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" required></label><button type="submit">上传答案文件</button></form>
  <label class="answer-task-select-label">已上传的本卷答案任务<select data-answer-task-select><option value="">正在查找可用答案任务…</option></select></label>
  <button type="button" data-answer-preview-existing>读取所选答案任务</button>
  <span data-answer-status aria-live="polite"></span>
  <button type="button" data-answer-preview-task hidden>生成题号匹配预览</button>
  <div data-answer-match-preview hidden><p data-answer-match-summary></p><ul data-answer-match-list></ul><ul data-answer-match-issues></ul><button type="button" data-answer-apply disabled>确认所选匹配并保存为待复核答案</button></div>
</section>""" % _e(task["original_paper_id"])
    review_export_link = (
        '<a class="button-secondary" data-review-draft-export href="/api/documents/tasks/%s/review-export.zip">下载未审核校对稿（含未解决事项）</a>'
        '<small class="review-export-hint">下载前会先保存本页尚未保存的题目修改。</small>'
        % _e(task["id"])
        if any(not item.get("published_revision_id") for item in items)
        else ""
    )
    return """<section class="document-review-page" data-document-review data-task-id="%s" data-task-status="%s" data-candidate-count="%s">
  <header class="review-heading"><div><a href="/documents">← 返回导入任务</a><p class="eyebrow">%s · %s</p><h1>整卷复核</h1><p>候选完整大题 %s 道；请核对题干、公式、选项、小问和图片，再勾选要入库的题。</p></div><a class="button-secondary" href="/api/documents/tasks/%s/source">下载原文件</a></header>
  %s
  <div class="document-review-workspace"><section class="source-panel"><h2>原卷对照</h2>%s</section><section class="candidate-panel"><div class="candidate-toolbar"><h2>题目编辑与预览</h2><button type="button" data-confirm-items>批量入库选中题目</button><span data-batch-status aria-live="polite"></span>%s%s</div>%s%s<div class="question-card-list">%s</div></section></div>
</section>
""" % (
        _e(task["id"]),
        _e(task.get("status", "")),
        _e(task.get("item_count", len(items))),
        _e(task["file_name"]),
        _e(_status(task["status"])),
        _e(len(items)),
        _e(task["id"]),
        answer_attachment,
        source_panel,
        ('<a class="button-secondary" href="/api/documents/tasks/%s/export.zip">下载已入库整卷包</a> · <a class="button-secondary" href="/api/documents/tasks/%s/export.zip?include_solution=1">含答案整卷包</a>' % (_e(task["id"]), _e(task["id"]))) if task.get("published_count") else "",
        review_export_link,
        _source_mapping_controls(items),
        _source_asset_gallery(task["id"], source_assets),
        "".join(cards) if cards else "<p class='document-empty'>尚未生成题目候选。任务可能是答案文件，或转换未发现题号边界。</p>",
    )
