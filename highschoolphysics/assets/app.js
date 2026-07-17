async function postJSON(url, payload) {
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    });
  } catch (cause) {
    const error = new Error("network_unavailable");
    error.cause = cause;
    error.status = 0;
    throw error;
  }
  let data = {};
  try {
    data = await response.json();
  } catch (_error) {
    data = {};
  }
  if (!response.ok || data.error) {
    const error = new Error(data.message || data.error || "request_failed");
    error.status = response.status;
    throw error;
  }
  return data;
}

function setStatus(text, kind) {
  const status = document.querySelector("#action-status");
  if (!status) {
    return;
  }
  const message = status.querySelector("[data-status-message]");
  if (message) {
    message.textContent = text;
  } else {
    status.textContent = text;
  }
  status.dataset.kind = kind || "info";
  status.removeAttribute("hidden");
}

function reloadSoon() {
  window.setTimeout(() => window.location.reload(), 1200);
}

let studentUndoAction = null;

function setStudentBusy(container, busy) {
  if (!container) {
    return;
  }
  container.setAttribute("aria-busy", busy ? "true" : "false");
  container.querySelectorAll("button, input, select, textarea").forEach((control) => {
    control.disabled = Boolean(busy);
  });
}

function setInlineStudentStatus(container, text, kind) {
  const card = container ? container.closest(".wrong-card, .related-question-panel") : null;
  const status = card ? card.querySelector(".card-action-feedback") : null;
  if (status) {
    status.textContent = text;
    status.dataset.kind = kind || "info";
  }
}

function friendlyStudentError(error, actionLabel) {
  if (!error || error.status === 0 || error.message === "network_unavailable") {
    return `${actionLabel}没有完成。请检查网络后重试，你填写的内容仍保留在页面中。`;
  }
  if (error.status === 401) {
    return "登录状态已失效。请重新登录后继续，当前填写的内容仍保留在页面中。";
  }
  if (error.status === 403) {
    return `${actionLabel}没有完成，因为当前账号没有权限。请刷新页面或联系教师。`;
  }
  if (error.status === 429) {
    return "操作太频繁，请稍等片刻再试。";
  }
  if (error.status >= 500) {
    return `${actionLabel}没有完成，服务暂时不可用。请稍后重试。`;
  }
  return `${actionLabel}没有完成：${error.message || "请检查内容后重试"}`;
}

function setStudentUndo(action) {
  studentUndoAction = action || null;
  const button = document.querySelector('[data-action="undo-student-action"]');
  if (!button) {
    return;
  }
  button.hidden = !studentUndoAction;
  button.disabled = false;
}

function updateMasterySelection(wrapper, level) {
  if (!wrapper) {
    return;
  }
  wrapper.dataset.currentLevel = level || "";
  wrapper.querySelectorAll("[data-mastery], [data-action=\"mark-knowledge\"]").forEach((button) => {
    const value = button.dataset.mastery || button.dataset.level || "";
    button.setAttribute("aria-pressed", value === level ? "true" : "false");
  });
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
  const previous = document.querySelector("[data-tab][aria-selected=\"true\"]");
  const switched = previous && previous.dataset.tab !== name;
  document.querySelectorAll("[data-tab]").forEach((button) => {
    const isActive = button.dataset.tab === name;
    button.classList.toggle("is-active", isActive);
    if (button.getAttribute("role") === "tab") {
      button.setAttribute("aria-selected", isActive ? "true" : "false");
      button.tabIndex = isActive ? 0 : -1;
    }
  });
  document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
    const isActive = panel.dataset.tabPanel === name;
    panel.classList.toggle("is-active", isActive);
    panel.hidden = !isActive;
  });
  if (switched) {
    window.scrollTo({ top: 0, behavior: "auto" });
  }
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
    const isActive = button.dataset.tagFamilyTab === name;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", isActive ? "true" : "false");
    button.tabIndex = isActive ? 0 : -1;
  });
  document.querySelectorAll("[data-tag-family-panel]").forEach((panel) => {
    const isActive = panel.dataset.tagFamilyPanel === name;
    panel.classList.toggle("is-active", isActive);
    panel.hidden = !isActive;
  });
}

function selectKnowledgeNode(nodeId, renderGraph = true, restoreFocus = false) {
  if (!nodeId) {
    return;
  }
  if (renderGraph) {
    renderStudentGraph(nodeId);
  }
  document.querySelectorAll("[data-related-for]").forEach((panel) => {
    const isActive = panel.dataset.relatedFor === nodeId;
    panel.classList.toggle("is-active", isActive);
    panel.hidden = !isActive;
  });
  document.querySelectorAll(".graph-node").forEach((node) => {
    const isSelected = node.dataset.knowledgeId === nodeId;
    node.classList.toggle("is-selected", isSelected);
    node.setAttribute("aria-current", isSelected ? "true" : "false");
  });
  const data = readStudentGraphData();
  const selected = data.nodes.find((node) => node.id === nodeId);
  const search = document.querySelector("[data-graph-search]");
  if (selected && search) {
    search.value = selected.path;
  }
  if (restoreFocus) {
    window.requestAnimationFrame(() => {
      const selectedNode = document.querySelector(
        `.graph-node[data-knowledge-id="${CSS.escape(nodeId)}"]`
      );
      if (selectedNode) {
        selectedNode.focus();
      }
      if (selected) {
        setStatus(`已选择“${selected.name}”，相关证据已更新。`, "info");
      }
    });
  }
}

function filterWrongCards(nodeId) {
  document.querySelectorAll("[data-knowledge-filter]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.knowledgeFilter === nodeId);
  });
  const cards = Array.from(document.querySelectorAll("#student-panel-wrong .wrong-card"));
  let visibleCount = 0;
  cards.forEach((card) => {
    if (nodeId === "all") {
      card.hidden = false;
      visibleCount += 1;
      return;
    }
    const ids = (card.dataset.knowledgeIds || "").split(/\s+/);
    card.hidden = !ids.includes(nodeId);
    if (!card.hidden) {
      visibleCount += 1;
    }
  });
  const status = document.querySelector("[data-wrong-filter-status]");
  const empty = document.querySelector("[data-wrong-filter-empty]");
  if (status) {
    status.textContent = nodeId === "all"
      ? `当前显示全部 ${visibleCount} 道错题`
      : `筛选后显示 ${visibleCount} 道错题`;
  }
  if (empty) {
    empty.hidden = visibleCount !== 0;
  }
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

const SVG_NS = "http://www.w3.org/2000/svg";
let studentGraphDataCache = null;

function readStudentGraphData() {
  if (studentGraphDataCache) {
    return studentGraphDataCache;
  }
  const source = document.querySelector("#student-graph-data");
  if (!source) {
    studentGraphDataCache = { nodes: [], edges: [] };
    return studentGraphDataCache;
  }
  try {
    studentGraphDataCache = JSON.parse(source.textContent || "{}");
  } catch (_error) {
    studentGraphDataCache = { nodes: [], edges: [] };
  }
  studentGraphDataCache.nodes = studentGraphDataCache.nodes || [];
  studentGraphDataCache.edges = studentGraphDataCache.edges || [];
  return studentGraphDataCache;
}

function focusedStudentGraphIds(focusId, limit = 7) {
  const data = readStudentGraphData();
  const byId = new Map(data.nodes.map((node) => [node.id, node]));
  if (!byId.has(focusId)) {
    focusId = data.nodes.length ? data.nodes[0].id : "";
  }
  if (!focusId) {
    return [];
  }
  const neighbours = [];
  data.edges.forEach((edge) => {
    let other = "";
    if (edge.source === focusId) {
      other = edge.target;
    } else if (edge.target === focusId) {
      other = edge.source;
    }
    if (!other || !byId.has(other)) {
      return;
    }
    const node = byId.get(other);
    neighbours.push({
      id: other,
      priority: edge.kind === "relation" ? 0 : 1,
      level: Number(node.level || 0),
      stableCode: node.stableCode || ""
    });
  });
  const focusNode = byId.get(focusId);
  if (focusNode && focusNode.parentId) {
    data.nodes.forEach((node) => {
      if (node.parentId === focusNode.parentId && node.id !== focusId) {
        neighbours.push({
          id: node.id,
          priority: 2,
          level: Number(node.level || 0),
          stableCode: node.stableCode || ""
        });
      }
    });
  }
  neighbours.sort((left, right) => (
    left.priority - right.priority ||
    left.level - right.level ||
    left.stableCode.localeCompare(right.stableCode, "zh-CN")
  ));
  const ids = [focusId];
  neighbours.forEach((item) => {
    if (ids.length < limit && !ids.includes(item.id)) {
      ids.push(item.id);
    }
  });
  return ids;
}

function graphLabelLines(value) {
  const text = String(value || "");
  if (text.length <= 6) {
    return [text];
  }
  if (text.length <= 12) {
    return [text.slice(0, 6), text.slice(6)];
  }
  return [text.slice(0, 6), `${text.slice(6, 11)}…`];
}

function studentGraphLayout() {
  const narrow = window.matchMedia && window.matchMedia("(max-width: 600px)").matches;
  if (narrow) {
    return {
      viewBox: "0 0 360 420",
      positions: [
        [180, 210],
        [80, 72],
        [280, 72],
        [180, 348]
      ]
    };
  }
  return {
    viewBox: "0 0 720 360",
    positions: [
      [360, 180],
      [132, 82],
      [360, 68],
      [588, 82],
      [132, 278],
      [360, 292],
      [588, 278]
    ]
  };
}

function renderStudentGraph(focusId) {
  const graph = document.querySelector(".student-relation-graph");
  const stage = graph ? graph.querySelector(".graph-stage") : null;
  if (!graph || !stage) {
    return;
  }
  const data = readStudentGraphData();
  const byId = new Map(data.nodes.map((node) => [node.id, node]));
  const narrow = window.matchMedia && window.matchMedia("(max-width: 600px)").matches;
  const allRelatedIds = focusedStudentGraphIds(focusId, Number.MAX_SAFE_INTEGER);
  const visibleIds = allRelatedIds.slice(0, narrow ? 4 : 7);
  if (!visibleIds.length) {
    return;
  }
  focusId = visibleIds[0];
  const layout = studentGraphLayout();
  const positions = layout.positions;
  graph.setAttribute("viewBox", layout.viewBox);
  const positionById = new Map(
    visibleIds.map((id, index) => [id, positions[index]])
  );
  stage.replaceChildren();
  data.edges.forEach((edge) => {
    if (!positionById.has(edge.source) || !positionById.has(edge.target)) {
      return;
    }
    const [x1, y1] = positionById.get(edge.source);
    const [x2, y2] = positionById.get(edge.target);
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", x1);
    line.setAttribute("y1", y1);
    line.setAttribute("x2", x2);
    line.setAttribute("y2", y2);
    line.dataset.edgeKind = edge.kind || "relation";
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = edge.relation || "知识关联";
    line.appendChild(title);
    stage.appendChild(line);
  });
  visibleIds.forEach((nodeId, index) => {
    const node = byId.get(nodeId);
    const [x, y] = positionById.get(nodeId);
    const selected = index === 0;
    const group = document.createElementNS(SVG_NS, "g");
    group.setAttribute("class", `graph-node ${node.masteryClass || ""}${selected ? " is-selected" : ""}`.trim());
    group.setAttribute("transform", `translate(${x},${y})`);
    group.setAttribute("role", "button");
    group.setAttribute("tabindex", "0");
    group.setAttribute("aria-current", selected ? "true" : "false");
    group.setAttribute(
      "aria-label",
      `查看知识点 ${node.name}，当前${node.displayState}，相关题目${node.questionCount}题`
    );
    group.dataset.action = "select-knowledge";
    group.dataset.knowledgeId = node.id;
    group.dataset.detailLevel = selected ? "focus" : "neighbor";
    const circle = document.createElementNS(SVG_NS, "circle");
    circle.setAttribute("r", selected ? "32" : "27");
    const text = document.createElementNS(SVG_NS, "text");
    const labelLines = graphLabelLines(node.name);
    text.setAttribute("y", labelLines.length > 1 ? "-4" : "5");
    labelLines.forEach((line, lineIndex) => {
      const tspan = document.createElementNS(SVG_NS, "tspan");
      tspan.setAttribute("x", "0");
      tspan.setAttribute("dy", lineIndex === 0 ? "0" : "15");
      tspan.textContent = line;
      text.appendChild(tspan);
    });
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = `${node.path}｜当前状态：${node.displayState}｜${node.evidence}｜相关题目 ${node.questionCount} 题`;
    group.append(circle, text, title);
    stage.appendChild(group);
  });
  graph.dataset.graphFocus = focusId;
  const nodeStatus = document.querySelector("[data-graph-node-status]");
  const relatedSummary = document.querySelector("[data-graph-related-summary]");
  const relatedList = document.querySelector("[data-graph-related-list]");
  if (nodeStatus) {
    nodeStatus.textContent = `${visibleIds.length}/${allRelatedIds.length} 个节点`;
  }
  if (relatedSummary) {
    const hiddenCount = Math.max(0, allRelatedIds.length - visibleIds.length);
    relatedSummary.textContent = hiddenCount
      ? `查看全部关联知识点（另有 ${hiddenCount} 个未显示）`
      : "查看全部关联知识点";
  }
  if (relatedList) {
    relatedList.replaceChildren();
    allRelatedIds.slice(1).forEach((nodeId) => {
      const node = byId.get(nodeId);
      if (!node) {
        return;
      }
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.dataset.action = "select-knowledge";
      button.dataset.knowledgeId = node.id;
      button.textContent = node.name;
      item.appendChild(button);
      relatedList.appendChild(item);
    });
  }
  graphScale = 1;
  graphPan = { x: 0, y: 0 };
  updateGraphTransform();
}

function findStudentKnowledge(query) {
  const normalized = String(query || "").trim().toLocaleLowerCase();
  if (!normalized) {
    return null;
  }
  const nodes = readStudentGraphData().nodes;
  return nodes.find((node) => (
    node.path.toLocaleLowerCase() === normalized ||
    node.name.toLocaleLowerCase() === normalized ||
    node.id.toLocaleLowerCase() === normalized
  )) || nodes.find((node) => (
    node.path.toLocaleLowerCase().includes(normalized) ||
    node.name.toLocaleLowerCase().includes(normalized)
  )) || null;
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
  const zoomStatus = document.querySelector("[data-graph-zoom-status]");
  if (graph) {
    graph.setAttribute("data-graph-scale-state", graphScaleState());
  }
  if (stage) {
    stage.setAttribute("transform", `translate(${graphPan.x},${graphPan.y}) scale(${graphScale})`);
  }
  if (zoomStatus) {
    zoomStatus.textContent = `${Math.round(graphScale * 100)}%`;
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
  const tab = event.target.closest('[role="tab"][data-tab]');
  if (tab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    const tabs = Array.from(document.querySelectorAll('.bottom-nav [role="tab"]'));
    const currentIndex = tabs.indexOf(tab);
    let nextIndex = currentIndex;
    if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    } else if (event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % tabs.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = tabs.length - 1;
    }
    if (tabs[nextIndex]) {
      event.preventDefault();
      activateStudentTab(tabs[nextIndex].dataset.tab);
      tabs[nextIndex].focus();
    }
    return;
  }
  const graphNode = event.target.closest('[data-action="select-knowledge"]');
  if (!graphNode || (event.key !== "Enter" && event.key !== " ")) {
    return;
  }
  event.preventDefault();
  selectKnowledgeNode(graphNode.dataset.knowledgeId, true, true);
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
  if (event.target.matches("[data-graph-search]")) {
    const node = findStudentKnowledge(event.target.value);
    if (node) {
      selectKnowledgeNode(node.id);
      setStatus(`已定位到“${node.name}”。`, "info");
    } else if (event.target.value.trim()) {
      setStatus("没有找到这个知识点。请从建议中选择，或换一个关键词。", "warning");
    }
  }
  const dataWrongFilterSearch = event.target.closest("[data-wrong-filter-search]");
  if (dataWrongFilterSearch) {
    if (!dataWrongFilterSearch.value.trim()) {
      filterWrongCards("all");
    } else {
      const node = findStudentKnowledge(dataWrongFilterSearch.value);
      if (node) {
        dataWrongFilterSearch.value = node.path;
        filterWrongCards(node.id);
      } else {
        const status = document.querySelector("[data-wrong-filter-status]");
        if (status) {
          status.textContent = "没有找到这个知识点。请从建议中选择，或换一个关键词。";
        }
      }
    }
  }
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
    if (studentForm.getAttribute("aria-busy") === "true") {
      return;
    }
    const endpoint = STUDENT_FORM_ENDPOINTS[studentForm.dataset.studentForm];
    if (!endpoint) {
      return;
    }
    const studentPayload = formPayload(studentForm, event.submitter);
    setStudentUndo(null);
    setStudentBusy(studentForm, true);
    setInlineStudentStatus(studentForm, "正在提交重做，请稍候…", "busy");
    try {
      setStatus("正在提交重做，请稍候…", "busy");
      const response = await postJSON(
        endpoint,
        studentPayload
      );
      const message = response.message || "重做已提交，正在更新待重做状态。";
      setInlineStudentStatus(studentForm, message, "success");
      setStatus(message, "success");
      reloadSoon();
    } catch (error) {
      const message = friendlyStudentError(error, "重做提交");
      setStudentBusy(studentForm, false);
      setInlineStudentStatus(studentForm, message, "error");
      setStatus(message, "error");
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
    event.preventDefault();
    selectKnowledgeNode(graphNode.dataset.knowledgeId);
    return;
  }

  const mastery = event.target.closest("[data-mastery]");
  if (mastery) {
    const wrapper = mastery.closest("[data-wrong-id]");
    if (!wrapper || wrapper.getAttribute("aria-busy") === "true") {
      return;
    }
    const previousLevel = wrapper.dataset.currentLevel || "";
    const previousNote = wrapper.dataset.currentNote || "";
    const nextLevel = mastery.dataset.mastery;
    setStudentUndo(null);
    setStudentBusy(wrapper, true);
    setInlineStudentStatus(wrapper, `正在保存“${nextLevel}”…`, "busy");
    setStatus(`正在保存“${nextLevel}”…`, "busy");
    try {
      await postJSON("/api/student/mastery", {
        wrong_question_id: wrapper.dataset.wrongId,
        level: nextLevel,
        note: "学生错题自评"
      });
      wrapper.dataset.currentNote = "学生错题自评";
      updateMasterySelection(wrapper, nextLevel);
      setStudentBusy(wrapper, false);
      setInlineStudentStatus(wrapper, `已标记为“${nextLevel}”。`, "success");
      setStatus(`已标记为“${nextLevel}”。`, "success");
      setStudentUndo({
        endpoint: "/api/student/mastery",
        payload: previousLevel
          ? {
              wrong_question_id: wrapper.dataset.wrongId,
              level: previousLevel,
              note: previousNote
            }
          : { wrong_question_id: wrapper.dataset.wrongId, clear: true },
        wrapper,
        level: previousLevel,
        note: previousNote,
        successMessage: "已撤销刚才的错题掌握标记。"
      });
    } catch (error) {
      const message = friendlyStudentError(error, "掌握标记保存");
      setStudentBusy(wrapper, false);
      setInlineStudentStatus(wrapper, message, "error");
      setStatus(message, "error");
    }
    return;
  }

  const action = event.target.closest("[data-action]");
  if (!action) {
    return;
  }

  try {
    if (action.dataset.action === "undo-student-action") {
      if (!studentUndoAction) {
        return;
      }
      const undo = studentUndoAction;
      action.disabled = true;
      setStatus("正在撤销刚才的修改…", "busy");
      await postJSON(undo.endpoint, undo.payload);
      updateMasterySelection(undo.wrapper, undo.level);
      undo.wrapper.dataset.currentNote = undo.note || "";
      setInlineStudentStatus(undo.wrapper, undo.successMessage, "success");
      setStatus(undo.successMessage, "success");
      setStudentUndo(null);
      return;
    }

    if (action.dataset.action === "open-question") {
      event.preventDefault();
      activateStudentTab(action.dataset.targetTab);
      activateTagFamilyPanel(action.dataset.targetPanel);
      window.requestAnimationFrame(() => {
        const target = document.getElementById(action.dataset.targetId);
        if (!target) {
          return;
        }
        const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        target.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
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
      if (!wrapper || !wrapper.dataset.knowledgeId || wrapper.getAttribute("aria-busy") === "true") {
        return;
      }
      const previousLevel = wrapper.dataset.currentLevel || "";
      const previousNote = wrapper.dataset.currentNote || "";
      const nextLevel = action.dataset.level;
      setStudentUndo(null);
      setStudentBusy(wrapper, true);
      setInlineStudentStatus(wrapper, `正在保存“${nextLevel}”…`, "busy");
      setStatus(`正在保存“${nextLevel}”…`, "busy");
      await postJSON("/api/student/knowledge-mastery", {
        knowledge_node_id: wrapper.dataset.knowledgeId,
        level: nextLevel,
        note: "学生图谱自评"
      });
      wrapper.dataset.currentNote = "学生图谱自评";
      updateMasterySelection(wrapper, nextLevel);
      setStudentBusy(wrapper, false);
      setInlineStudentStatus(wrapper, `已标记为“${nextLevel}”。`, "success");
      setStatus(`已标记为“${nextLevel}”。`, "success");
      setStudentUndo({
        endpoint: "/api/student/knowledge-mastery",
        payload: previousLevel
          ? {
              knowledge_node_id: wrapper.dataset.knowledgeId,
              level: previousLevel,
              note: previousNote
            }
          : { knowledge_node_id: wrapper.dataset.knowledgeId, clear: true },
        wrapper,
        level: previousLevel,
        note: previousNote,
        successMessage: "已撤销刚才的知识点掌握标记。"
      });
      return;
    }

    if (action.dataset.action === "focus-knowledge") {
      activateStudentTab("graph");
      selectKnowledgeNode(action.dataset.knowledgeId);
      const workspace = document.querySelector("#student-graph-workspace");
      if (workspace) {
        const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        workspace.scrollIntoView({ block: "start", behavior: reduceMotion ? "auto" : "smooth" });
        workspace.focus({ preventScroll: true });
      }
      return;
    }

    if (action.dataset.action === "collapse-modules") {
      document.querySelectorAll(".module-tree details[open]").forEach((details) => {
        details.open = false;
      });
      setStatus("教材目录已全部收起。", "info");
      return;
    }

    if (action.dataset.action === "clear-wrong-filter") {
      const search = document.querySelector("[data-wrong-filter-search]");
      if (search) {
        search.value = "";
      }
      filterWrongCards("all");
      return;
    }

    if (action.dataset.action === "graph-focus-current") {
      const graph = document.querySelector(".student-relation-graph");
      const focusId = graph ? graph.dataset.graphDefaultFocus : "";
      if (focusId) {
        selectKnowledgeNode(focusId);
        setStatus("已回到当前学习任务。", "info");
      }
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
    if (action.dataset.action === "mark-knowledge") {
      const wrapper = action.closest("[data-knowledge-id]");
      const message = friendlyStudentError(error, "掌握标记保存");
      setStudentBusy(wrapper, false);
      setInlineStudentStatus(wrapper, message, "error");
      setStatus(message, "error");
      return;
    }
    if (action.dataset.action === "undo-student-action") {
      action.disabled = false;
      setStatus(friendlyStudentError(error, "撤销操作"), "error");
      return;
    }
    setStatus(`操作失败：${error.message}`, "error");
  }
});

document.addEventListener("DOMContentLoaded", () => {
  const graph = document.querySelector(".student-relation-graph");
  if (graph && graph.dataset.graphDefaultFocus) {
    selectKnowledgeNode(graph.dataset.graphDefaultFocus);
  }
  updateAdminUserTable(false);
  if (document.querySelector("[data-admin-tab]")) {
    activateAdminTab(initAdminTabFromHash());
    window.addEventListener("hashchange", () => {
      activateAdminTab(initAdminTabFromHash());
    });
  }
});

let studentGraphResizeTimer = null;
window.addEventListener("resize", () => {
  window.clearTimeout(studentGraphResizeTimer);
  studentGraphResizeTimer = window.setTimeout(() => {
    const graph = document.querySelector(".student-relation-graph");
    if (graph && graph.dataset.graphFocus) {
      renderStudentGraph(graph.dataset.graphFocus);
    }
  }, 120);
});
