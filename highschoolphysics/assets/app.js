async function postJSON(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {})
  });
  const data = await response.json();
  if (!response.ok || data.error) {
    throw new Error(data.message || data.error || "request_failed");
  }
  return data;
}

function setStatus(text, kind) {
  const status = document.querySelector("#action-status");
  if (!status) {
    return;
  }
  status.textContent = text;
  status.dataset.kind = kind || "info";
}

function reloadSoon() {
  window.setTimeout(() => window.location.reload(), 650);
}

const ADMIN_FORM_ENDPOINTS = {
  "knowledge-node": "/api/admin/knowledge-node",
  "knowledge-node-update": "/api/admin/knowledge-node/update",
  "knowledge-edge": "/api/admin/knowledge-edge",
  "ability-tag": "/api/admin/ability-tag",
  "ability-tag-update": "/api/admin/ability-tag/update",
  "literacy-tag": "/api/admin/literacy-tag",
  "literacy-tag-update": "/api/admin/literacy-tag/update",
  "ontology-draft": "/api/admin/ontology-draft",
  "ontology-publish": "/api/admin/ontology-publish",
  "error-reason-tag": "/api/teacher/error-reason-tag",
  "export-profile": "/api/admin/export-profile",
  "runtime-check": "/api/admin/runtime-check",
  "provider-config": "/api/admin/provider-config",
  "provider-test": "/api/admin/provider-test",
  "oidc-provider": "/api/admin/oidc-provider",
  "import-teacher": "/api/admin/import-teacher"
};

const TEACHER_FORM_ENDPOINTS = {
  "question": "/api/teacher/question",
  "question-update": "/api/teacher/question/update",
  "parse-task": "/api/teacher/parse-task",
  "parsed-question-save": "/api/teacher/parsed-question/save",
  "question-tags-confirm": "/api/teacher/question-tags/confirm",
  "paper-assembly": "/api/teacher/paper-assembly",
  "assessment-from-paper": "/api/teacher/assessment-from-paper",
  "wrong-book-pdf": "/api/teacher/wrong-book-pdf",
  "ocr-import": "/api/teacher/ocr-import",
  "grading-revision": "/api/teacher/grading-revision",
  "error-tagging": "/api/teacher/wrong-question/error-tags",
  "redo-review": "/api/teacher/redo-attempt/review"
};

const STUDENT_FORM_ENDPOINTS = {
  "redo-attempt": "/api/student/redo-attempt"
};

function formElementFor(form, key) {
  const field = form.elements[key];
  if (!field) {
    return null;
  }
  if (typeof RadioNodeList !== "undefined" && field instanceof RadioNodeList) {
    return field[0] || null;
  }
  return field;
}

function normalizeFormValue(form, key, value) {
  const field = formElementFor(form, key);
  if (field && field.dataset && field.dataset.json === "true") {
    const text = String(value || "").trim();
    if (!text) {
      return JSON.parse(field.dataset.defaultJson || "null");
    }
    return JSON.parse(text);
  }
  return value;
}

function formPayload(form, submitter) {
  const payload = {};
  new FormData(form).forEach((value, key) => {
    const normalized = normalizeFormValue(form, key, value);
    if (payload[key] === undefined) {
      payload[key] = normalized;
    } else if (Array.isArray(payload[key])) {
      payload[key].push(normalized);
    } else {
      payload[key] = [payload[key], normalized];
    }
  });
  if (submitter && submitter.name) {
    payload[submitter.name] = submitter.value;
  }
  Object.keys(payload).forEach((key) => {
    if (Array.isArray(payload[key])) {
      payload[key] = payload[key].filter((value) => value !== "");
    } else if (key.endsWith("_ids")) {
      payload[key] = payload[key] ? [payload[key]] : [];
    }
  });
  return payload;
}

function activateStudentTab(name) {
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tab === name);
  });
  document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.tabPanel === name);
  });
}

function activateAdminTab(name) {
  if (!name) {
    return;
  }
  document.querySelectorAll("[data-admin-tab]").forEach((btn) => {
    const isActive = btn.dataset.adminTab === name;
    btn.classList.toggle("is-active", isActive);
    btn.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  document.querySelectorAll("[data-admin-tab-panel]").forEach((panel) => {
    const isActive = panel.dataset.adminTabPanel === name;
    panel.classList.toggle("is-active", isActive);
    if (isActive) {
      panel.removeAttribute("hidden");
    } else {
      panel.setAttribute("hidden", "");
    }
  });
  if (history.replaceState) {
    history.replaceState(null, "", `#admin=${encodeURIComponent(name)}`);
  }
}

function initAdminTabFromHash() {
  const match = (location.hash || "").match(/^#admin=([a-z]+)$/);
  const allowed = ["overview", "accounts", "ontology", "operations", "system"];
  return match && allowed.includes(match[1]) ? match[1] : "overview";
}

function activateTagFamilyPanel(name) {
  if (!name) {
    return;
  }
  document.querySelectorAll("[data-tag-family-tab]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tagFamilyTab === name);
  });
  document.querySelectorAll("[data-tag-family-panel]").forEach((panel) => {
    panel.classList.toggle(
      "is-active",
      panel.dataset.tagFamilyPanel === name
    );
  });
}

function selectKnowledgeNode(nodeId) {
  document.querySelectorAll("[data-related-for]").forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.relatedFor === nodeId);
  });
  document.querySelectorAll(".graph-node").forEach((node) => {
    node.classList.toggle("is-selected", node.dataset.knowledgeId === nodeId);
  });
  document.querySelectorAll(".graph-mark-actions").forEach((wrapper) => {
    wrapper.dataset.knowledgeId = nodeId;
  });
}

function filterWrongCards(nodeId) {
  document.querySelectorAll("[data-knowledge-filter]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.knowledgeFilter === nodeId);
  });
  document.querySelectorAll(".wrong-card").forEach((card) => {
    if (nodeId === "all") {
      card.hidden = false;
      return;
    }
    const ids = (card.dataset.knowledgeIds || "").split(/\s+/);
    card.hidden = !ids.includes(nodeId);
  });
  activateStudentTab("wrong");
}

function filterAdminTaxonomy() {
  const search = document.querySelector("[data-taxonomy-search]");
  const module = document.querySelector("[data-taxonomy-module]");
  if (!search || !module) {
    return;
  }
  const query = search.value.trim().toLocaleLowerCase();
  document.querySelectorAll("[data-taxonomy-node]").forEach((row) => {
    const matchesModule = !module.value || row.dataset.moduleId === module.value;
    const matchesSearch = !query || (row.dataset.searchText || "").toLocaleLowerCase().includes(query);
    row.hidden = !(matchesModule && matchesSearch);
  });
}

function filterQuestionBank() {
  const filter = document.querySelector("[data-question-bank-filter]");
  if (!filter) {
    return;
  }
  const valueFor = (name) => {
    const field = filter.querySelector(`[name="${name}"]`);
    return field ? field.value.trim() : "";
  };
  const grade = valueFor("filter_grade");
  const chapter = valueFor("filter_chapter");
  const qualityStatus = valueFor("filter_quality_status");
  const confidenceMax = valueFor("filter_source_confidence_max");
  const tagType = valueFor("tag_type");
  const tagId = valueFor("tag_id");
  const maxConfidence = confidenceMax ? Number.parseFloat(confidenceMax) : null;

  document.querySelectorAll("[data-question-row]").forEach((row) => {
    const matchesGrade = !grade || row.dataset.grade === grade;
    const matchesChapter = !chapter || row.dataset.chapter === chapter;
    const matchesQuality = !qualityStatus || row.dataset.qualityStatus === qualityStatus;
    const confidence = Number.parseFloat(row.dataset.sourceConfidence || "1");
    const matchesConfidence = maxConfidence === null || confidence <= maxConfidence;
    let matchesTag = true;
    if (tagType && tagId) {
      const tagIds = (row.dataset[`${tagType}Ids`] || "").split(/\s+/);
      matchesTag = tagIds.includes(tagId);
    }
    row.hidden = !(matchesGrade && matchesChapter && matchesQuality && matchesConfidence && matchesTag);
  });
}

let adminUserPage = 1;

function updateAdminUserTable(resetPage) {
  const rows = Array.from(document.querySelectorAll("[data-admin-user-row]"));
  const pageSizeSource = document.querySelector("[data-admin-user-page-size]");
  if (!rows.length || !pageSizeSource) {
    return;
  }
  const search = document.querySelector("[data-admin-user-search]");
  const roleFilter = document.querySelector("[data-admin-user-role-filter]");
  const statusFilter = document.querySelector("[data-admin-user-status-filter]");
  const query = search ? search.value.trim().toLocaleLowerCase() : "";
  const role = roleFilter ? roleFilter.value : "";
  const status = statusFilter ? statusFilter.value : "";
  const pageSize = Number.parseInt(pageSizeSource.dataset.adminUserPageSize, 10) || 12;
  const matches = rows.filter((row) => {
    const matchesQuery = !query || (row.dataset.searchText || "").toLocaleLowerCase().includes(query);
    const matchesRole = !role || row.dataset.role === role;
    const matchesStatus = !status || row.dataset.status === status;
    return matchesQuery && matchesRole && matchesStatus;
  });
  const totalPages = Math.max(1, Math.ceil(matches.length / pageSize));
  if (resetPage) {
    adminUserPage = 1;
  }
  adminUserPage = Math.min(Math.max(adminUserPage, 1), totalPages);
  const firstIndex = (adminUserPage - 1) * pageSize;
  const visibleRows = new Set(matches.slice(firstIndex, firstIndex + pageSize));
  rows.forEach((row) => {
    row.hidden = !visibleRows.has(row);
  });
  const pageInfo = document.querySelector("[data-admin-user-page-info]");
  if (pageInfo) {
    const visibleStart = matches.length ? firstIndex + 1 : 0;
    const visibleEnd = Math.min(firstIndex + pageSize, matches.length);
    pageInfo.textContent = `第 ${adminUserPage}/${totalPages} 页 · ${visibleStart}-${visibleEnd} / ${matches.length}`;
  }
  const prev = document.querySelector("[data-admin-user-prev]");
  const next = document.querySelector("[data-admin-user-next]");
  if (prev) {
    prev.disabled = adminUserPage <= 1;
  }
  if (next) {
    next.disabled = adminUserPage >= totalPages;
  }
}

let graphScale = 1;
let graphPan = { x: 0, y: 0 };
let dragStart = null;

function graphScaleState() {
  if (graphScale < 0.9) {
    return "low";
  }
  if (graphScale >= 1.15) {
    return "high";
  }
  return "medium";
}

function updateGraphTransform() {
  const graph = document.querySelector(".student-relation-graph");
  const stage = document.querySelector(".student-relation-graph .graph-stage");
  if (graph) {
    graph.setAttribute("data-graph-scale-state", graphScaleState());
  }
  if (stage) {
    stage.setAttribute("transform", `translate(${graphPan.x},${graphPan.y}) scale(${graphScale})`);
  }
}

document.addEventListener("pointerdown", (event) => {
  const graph = event.target.closest(".student-relation-graph");
  if (!graph) {
    return;
  }
  if (event.target.setPointerCapture && event.pointerId !== undefined) {
    event.target.setPointerCapture(event.pointerId);
  }
  dragStart = { x: event.clientX, y: event.clientY, panX: graphPan.x, panY: graphPan.y };
});

document.addEventListener("pointermove", (event) => {
  if (!dragStart) {
    return;
  }
  graphPan = {
    x: dragStart.panX + event.clientX - dragStart.x,
    y: dragStart.panY + event.clientY - dragStart.y
  };
  updateGraphTransform();
});

document.addEventListener("pointerup", () => {
  dragStart = null;
});

document.addEventListener("pointercancel", () => {
  dragStart = null;
});

document.addEventListener("lostpointercapture", () => {
  dragStart = null;
});

document.addEventListener("keydown", (event) => {
  const graphNode = event.target.closest('[data-action="select-knowledge"]');
  if (!graphNode || (event.key !== "Enter" && event.key !== " ")) {
    return;
  }
  event.preventDefault();
  selectKnowledgeNode(graphNode.dataset.knowledgeId);
});

document.addEventListener("input", (event) => {
  if (event.target.matches("[data-taxonomy-search]")) {
    filterAdminTaxonomy();
  }
  if (event.target.matches("[data-admin-user-search]")) {
    updateAdminUserTable(true);
  }
  if (event.target.closest("[data-question-bank-filter]")) {
    filterQuestionBank();
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-taxonomy-module]")) {
    filterAdminTaxonomy();
  }
  if (
    event.target.matches("[data-admin-user-role-filter]") ||
    event.target.matches("[data-admin-user-status-filter]")
  ) {
    updateAdminUserTable(true);
  }
  if (event.target.closest("[data-question-bank-filter]")) {
    filterQuestionBank();
  }
});

document.addEventListener("submit", async (event) => {
  const passwordResetForm = event.target.closest("[data-password-reset-form]");
  if (passwordResetForm) {
    event.preventDefault();
    try {
      setStatus("正在重置临时密码...", "busy");
      const response = await postJSON(
        "/api/password/reset",
        formPayload(passwordResetForm, event.submitter)
      );
      passwordResetForm.reset();
      setStatus(response.message || "临时密码已重置。", "success");
    } catch (error) {
      setStatus(`操作失败：${error.message}`, "error");
    }
    return;
  }

  const studentForm = event.target.closest("[data-student-form]");
  if (studentForm) {
    event.preventDefault();
    const endpoint = STUDENT_FORM_ENDPOINTS[studentForm.dataset.studentForm];
    if (!endpoint) {
      return;
    }
    try {
      setStatus("正在提交重做...", "busy");
      const response = await postJSON(
        endpoint,
        formPayload(studentForm, event.submitter)
      );
      setStatus(response.message || "重做已提交，页面即将刷新。", "success");
      reloadSoon();
    } catch (error) {
      setStatus(`操作失败：${error.message}`, "error");
    }
    return;
  }

  const teacherForm = event.target.closest("[data-teacher-form]");
  if (teacherForm) {
    event.preventDefault();
    const endpoint = TEACHER_FORM_ENDPOINTS[teacherForm.dataset.teacherForm];
    if (!endpoint) {
      return;
    }
    try {
      setStatus("正在保存题库数据...", "busy");
      const response = await postJSON(
        endpoint,
        formPayload(teacherForm, event.submitter)
      );
      setStatus(response.message || "题库数据已保存，页面即将刷新。", "success");
      reloadSoon();
    } catch (error) {
      setStatus(`操作失败：${error.message}`, "error");
    }
    return;
  }

  const form = event.target.closest("[data-admin-form]");
  if (!form) {
    return;
  }
  event.preventDefault();
  const formName = form.dataset.adminForm;
  if (formName === "import-teacher") {
    try {
      const payload = formPayload(form, event.submitter);
      const username = String(payload.username || "").trim();
      const displayName = String(payload.display_name || "").trim();
      const tempPassword = String(payload.temp_password || "");
      if (!username || !displayName || !tempPassword) {
        setStatus("账号、姓名和临时密码都不能为空。", "error");
        return;
      }
      if (tempPassword.length < 10) {
        setStatus("临时密码至少需要 10 位。", "error");
        return;
      }
      setStatus("正在新建教师账号...", "busy");
      const response = await postJSON(ADMIN_FORM_ENDPOINTS["import-teacher"], {
        username,
        display_name: displayName,
        temp_password: tempPassword
      });
      form.reset();
      setStatus(
        `已创建教师 ${displayName}（${response.user_id || ""}），页面即将刷新。`,
        "success"
      );
      reloadSoon();
    } catch (error) {
      setStatus(`操作失败：${error.message}`, "error");
    }
    return;
  }
  if (formName === "assign-classes") {
    try {
      const panel = form.closest("[data-assign-classes-panel]");
      const teacherId = form.dataset.endpoint
        ? ""
        : (panel ? panel.dataset.teacherId : "");
      const endpoint = form.dataset.endpoint
        ? form.dataset.endpoint
        : (teacherId
            ? `/api/admin/teacher/${teacherId}/assign-classes`
            : "");
      if (!endpoint) {
        setStatus("缺少教师 ID，无法提交。", "error");
        return;
      }
      const payload = formPayload(form, event.submitter);
      setStatus("正在保存班级分配...", "busy");
      await postJSON(endpoint, payload);
      setStatus("班级分配已更新，页面即将刷新。", "success");
      reloadSoon();
    } catch (error) {
      setStatus(`操作失败：${error.message}`, "error");
    }
    return;
  }
  const endpoint = ADMIN_FORM_ENDPOINTS[formName];
  if (!endpoint) {
    return;
  }
  try {
    setStatus("正在保存管理员配置...", "busy");
    const response = await postJSON(endpoint, formPayload(form, event.submitter));
    setStatus(response.message || "已保存，页面即将刷新。", "success");
    reloadSoon();
  } catch (error) {
    setStatus(`操作失败：${error.message}`, "error");
  }
});

document.addEventListener("click", async (event) => {
  const adminUserPrev = event.target.closest("[data-admin-user-prev]");
  if (adminUserPrev) {
    adminUserPage -= 1;
    updateAdminUserTable(false);
    return;
  }

  const adminUserNext = event.target.closest("[data-admin-user-next]");
  if (adminUserNext) {
    adminUserPage += 1;
    updateAdminUserTable(false);
    return;
  }

  const adminTab = event.target.closest("[data-admin-tab]");
  if (adminTab) {
    activateAdminTab(adminTab.dataset.adminTab);
    return;
  }

  const tab = event.target.closest("[data-tab]");
  if (tab) {
    activateStudentTab(tab.dataset.tab);
    return;
  }

  const familyTab = event.target.closest("[data-tag-family-tab]");
  if (familyTab) {
    activateTagFamilyPanel(familyTab.dataset.tagFamilyTab);
    return;
  }

  const filter = event.target.closest("[data-knowledge-filter]");
  if (filter) {
    event.preventDefault();
    filterWrongCards(filter.dataset.knowledgeFilter);
    return;
  }

  const graphNode = event.target.closest('[data-action="select-knowledge"]');
  if (graphNode) {
    selectKnowledgeNode(graphNode.dataset.knowledgeId);
    return;
  }

  const mastery = event.target.closest("[data-mastery]");
  if (mastery) {
    const wrapper = mastery.closest("[data-wrong-id]");
    await postJSON("/api/student/mastery", {
      wrong_question_id: wrapper.dataset.wrongId,
      level: mastery.dataset.mastery,
      note: "平板端标记"
    });
    window.location.reload();
    return;
  }

  const action = event.target.closest("[data-action]");
  if (!action) {
    return;
  }

  try {
    if (action.dataset.action === "open-question") {
      event.preventDefault();
      activateStudentTab(action.dataset.targetTab);
      activateTagFamilyPanel(action.dataset.targetPanel);
      window.requestAnimationFrame(() => {
        const target = document.getElementById(action.dataset.targetId);
        if (!target) {
          return;
        }
        target.scrollIntoView({ block: "center", behavior: "smooth" });
        target.classList.add("is-navigation-target");
        window.setTimeout(
          () => target.classList.remove("is-navigation-target"),
          1600
        );
      });
      return;
    }

    if (action.dataset.action === "mark-knowledge") {
      const wrapper = action.closest("[data-knowledge-id]");
      if (!wrapper || !wrapper.dataset.knowledgeId) {
        return;
      }
      await postJSON("/api/student/knowledge-mastery", {
        knowledge_node_id: wrapper.dataset.knowledgeId,
        level: action.dataset.level,
        note: "知识图谱中标记"
      });
      window.location.reload();
      return;
    }

    if (action.dataset.action === "graph-zoom-in") {
      graphScale = Math.min(2.5, graphScale + 0.15);
      updateGraphTransform();
      return;
    }

    if (action.dataset.action === "graph-zoom-out") {
      graphScale = Math.max(0.55, graphScale - 0.15);
      updateGraphTransform();
      return;
    }

    if (action.dataset.action === "graph-reset") {
      graphScale = 1;
      graphPan = { x: 0, y: 0 };
      updateGraphTransform();
      return;
    }

    if (action.dataset.action === "generate-candidate") {
      setStatus("正在生成 q-newton-1 的 LLM 候选标签...", "busy");
      await postJSON("/api/teacher/generate-candidate", {
        question_id: action.dataset.questionId
      });
      setStatus("已生成候选标签，页面即将刷新。", "success");
      reloadSoon();
      return;
    }

    if (action.dataset.action === "approve-candidate") {
      setStatus("正在确认候选标签...", "busy");
      await postJSON("/api/teacher/approve-candidate", {
        candidate_id: action.dataset.candidateId
      });
      setStatus("候选标签已写入正式题库，页面即将刷新。", "success");
      reloadSoon();
      return;
    }

    if (action.dataset.action === "run-parse-task") {
      setStatus("正在执行解析任务...", "busy");
      const response = await postJSON("/api/teacher/parse-task/run", {
        task_id: action.dataset.taskId
      });
      setStatus(response.message || "解析任务完成，页面即将刷新。", "success");
      reloadSoon();
      return;
    }

    if (action.dataset.action === "resolve-review") {
      setStatus("正在保存答题卡复核结果...", "busy");
      await postJSON("/api/teacher/resolve-review", {
        response_id: action.dataset.responseId,
        corrected_answer: action.dataset.answer,
        reason: "教师复核低置信涂卡"
      });
      setStatus("复核结果已保存，页面即将刷新。", "success");
      reloadSoon();
      return;
    }

    if (action.dataset.action === "grade-assessment") {
      setStatus("正在检查复核状态并批改发布...", "busy");
      const response = await postJSON("/api/teacher/grade", {
        assessment_id: action.dataset.assessmentId,
        publish: true
      });
      if (response.result && response.result.status === "blocked_for_review") {
        setStatus(`还有 ${response.result.review_required} 项低置信答题卡需要先复核。`, "warning");
      } else {
        setStatus("批改完成并已发布，页面即将刷新。", "success");
        reloadSoon();
      }
      return;
    }

    if (action.dataset.action === "import-demo-student") {
      const suffix = Math.floor(Math.random() * 9000 + 1000);
      await postJSON("/api/admin/import-student", {
        username: `stu_demo_${suffix}`,
        display_name: `导入学生${suffix}`,
        student_no: `${suffix}`,
        class_id: "class-physics-1",
        temp_password: "Temp123456"
      });
      window.location.reload();
      return;
    }

    if (action.dataset.action === "open-assign-classes") {
      const teacherId = action.dataset.teacherId;
      if (!teacherId) {
        return;
      }
      const row = action.closest("[data-admin-user-row]");
      const panel = document.querySelector(
        `[data-assign-classes-panel][data-teacher-id="${teacherId}"]`
      );
      if (!panel) {
        return;
      }
      const expanded = action.getAttribute("aria-expanded") === "true";
      if (expanded) {
        panel.hidden = true;
        action.setAttribute("aria-expanded", "false");
        return;
      }
      const rowInitial = row && row.dataset.classIds
        ? row.dataset.classIds
        : "";
      const options = panel.querySelector("[data-assign-classes-options]");
      const initial = (options && options.dataset.initialIds
        ? options.dataset.initialIds
        : rowInitial
      ).split(/\s+/).filter(Boolean);
      panel.querySelectorAll('input[type="checkbox"][data-class-id]').forEach((box) => {
        box.checked = initial.indexOf(box.dataset.classId) !== -1;
      });
      panel.hidden = false;
      action.setAttribute("aria-expanded", "true");
      if (typeof panel.scrollIntoView === "function") {
        panel.scrollIntoView({ block: "center", behavior: "smooth" });
      }
      return;
    }

    if (action.dataset.action === "close-assign-classes") {
      const teacherId = action.dataset.teacherId;
      const panel = document.querySelector(
        `[data-assign-classes-panel][data-teacher-id="${teacherId}"]`
      );
      if (panel) {
        panel.hidden = true;
      }
      if (teacherId) {
        const trigger = document.querySelector(
          `[data-action="open-assign-classes"][data-teacher-id="${teacherId}"]`
        );
        if (trigger) {
          trigger.setAttribute("aria-expanded", "false");
        }
      }
      return;
    }

    if (action.dataset.action === "install-default-taxonomy") {
      setStatus("正在校验并补齐默认物理体系...", "busy");
      const response = await postJSON("/api/admin/taxonomy/install", {
        publish: false
      });
      setStatus(response.message || "默认物理体系已补齐，页面即将刷新。", "success");
      reloadSoon();
      return;
    }
  } catch (error) {
    setStatus(`操作失败：${error.message}`, "error");
  }
});

document.addEventListener("DOMContentLoaded", () => {
  updateAdminUserTable(false);
  if (document.querySelector("[data-admin-tab]")) {
    activateAdminTab(initAdminTabFromHash());
    window.addEventListener("hashchange", () => {
      activateAdminTab(initAdminTabFromHash());
    });
  }
});
