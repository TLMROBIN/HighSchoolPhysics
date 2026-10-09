(function () {
  "use strict";

  const root = document.body.dataset.basePath || "";
  const url = (path) => root + path;
  const randomKey = () => {
    const cryptoApi = window.crypto;
    if (cryptoApi && cryptoApi.randomUUID) return cryptoApi.randomUUID().replace(/-/g, "");
    if (cryptoApi && cryptoApi.getRandomValues) {
      return Array.from(cryptoApi.getRandomValues(new Uint8Array(24)), (value) => value.toString(16).padStart(2, "0")).join("");
    }
    return Array.from({ length: 48 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
  };
  const request = async (path, payload) => {
    const response = await fetch(url(path), {
      method: payload === undefined ? "GET" : "POST",
      credentials: "same-origin",
      headers: payload === undefined ? {} : { "Content-Type": "application/json" },
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
    let body;
    try { body = await response.json(); } catch (_error) { body = {}; }
    if (!response.ok) throw new Error(body.message || body.error || `请求失败 (${response.status})`);
    return body.result || body;
  };
  const setText = (node, value) => { if (node) node.textContent = value; };
  const SHA256_K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);
  const rotateRight = (value, count) => (value >>> count) | (value << (32 - count));
  const sha256Fallback = async (input) => {
    const bitLength = input.length * 8;
    const paddedLength = Math.ceil((input.length + 9) / 64) * 64;
    const padded = new Uint8Array(paddedLength);
    padded.set(input);
    padded[input.length] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000));
    view.setUint32(paddedLength - 4, bitLength >>> 0);

    const hash = new Uint32Array([
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]);
    const words = new Uint32Array(64);
    const blockCount = paddedLength / 64;
    for (let block = 0; block < blockCount; block += 1) {
      const offset = block * 64;
      for (let index = 0; index < 16; index += 1) words[index] = view.getUint32(offset + index * 4);
      for (let index = 16; index < 64; index += 1) {
        const left = words[index - 15];
        const right = words[index - 2];
        const sigma0 = rotateRight(left, 7) ^ rotateRight(left, 18) ^ (left >>> 3);
        const sigma1 = rotateRight(right, 17) ^ rotateRight(right, 19) ^ (right >>> 10);
        words[index] = (words[index - 16] + sigma0 + words[index - 7] + sigma1) >>> 0;
      }
      let [a, b, c, d, e, f, g, h] = hash;
      for (let index = 0; index < 64; index += 1) {
        const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
        const choose = (e & f) ^ (~e & g);
        const temp1 = (h + sum1 + choose + SHA256_K[index] + words[index]) >>> 0;
        const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
        const majority = (a & b) ^ (a & c) ^ (b & c);
        const temp2 = (sum0 + majority) >>> 0;
        h = g;
        g = f;
        f = e;
        e = (d + temp1) >>> 0;
        d = c;
        c = b;
        b = a;
        a = (temp1 + temp2) >>> 0;
      }
      hash[0] = (hash[0] + a) >>> 0;
      hash[1] = (hash[1] + b) >>> 0;
      hash[2] = (hash[2] + c) >>> 0;
      hash[3] = (hash[3] + d) >>> 0;
      hash[4] = (hash[4] + e) >>> 0;
      hash[5] = (hash[5] + f) >>> 0;
      hash[6] = (hash[6] + g) >>> 0;
      hash[7] = (hash[7] + h) >>> 0;
      if (block > 0 && block % 2048 === 0) await new Promise((resolve) => window.setTimeout(resolve, 0));
    }
    return Array.from(hash, (value) => value.toString(16).padStart(8, "0")).join("");
  };
  const fileHash = async (file) => {
    const cryptoApi = window.crypto;
    const bytes = await file.arrayBuffer();
    if (cryptoApi && cryptoApi.subtle && cryptoApi.subtle.digest) {
      const digest = await cryptoApi.subtle.digest("SHA-256", bytes);
      return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
    }
    return sha256Fallback(new Uint8Array(bytes));
  };
  const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
  const UPLOAD_RESUME_TTL_MS = 24 * 60 * 60 * 1000;
  const resumeScope = document.querySelector("[data-document-home]")?.dataset.actorId || "teacher";
  const resumeStorageKey = `hsp-document-upload-resume-v1:${encodeURIComponent(resumeScope)}`;
  const readResumeRecords = () => {
    try {
      const records = JSON.parse(window.sessionStorage.getItem(resumeStorageKey) || "[]");
      if (!Array.isArray(records)) return [];
      return records.filter((record) => record && typeof record === "object"
        && Date.now() - Number(record.saved_at || 0) < UPLOAD_RESUME_TTL_MS);
    } catch (_error) {
      return [];
    }
  };
  const writeResumeRecords = (records) => {
    try { window.sessionStorage.setItem(resumeStorageKey, JSON.stringify(records.slice(0, 2))); }
    catch (_error) { /* Upload still works when the browser blocks session storage. */ }
  };
  const findResumeRecord = (file, digest, role, paperId, parserMode, importMode) => readResumeRecords().find((record) =>
    record.file_name === file.name
    && record.file_size === file.size
    && record.sha256 === digest
    && record.role === role
    && (record.paper_id || null) === (paperId || null)
    && (record.parser_mode || "mineru_local") === parserMode
    && (record.import_mode || "questions") === importMode
  );
  const rememberUpload = (record) => {
    const records = readResumeRecords().filter((item) => item.request_key !== record.request_key);
    writeResumeRecords([record, ...records]);
  };
  const forgetUpload = (requestKey) => writeResumeRecords(
    readResumeRecords().filter((record) => record.request_key !== requestKey)
  );
  const blobBase64 = (blob) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(reader.error || new Error("无法读取文件分片"));
    reader.readAsDataURL(blob);
  });

  const uploadForm = document.getElementById("document-upload-form");
  if (uploadForm) {
    const fileInput = document.getElementById("document-file");
    const titleInput = document.getElementById("document-title");
    const roleInput = document.getElementById("document-role");
    const paperInput = document.getElementById("document-paper");
    const paperLabel = document.getElementById("document-paper-label");
    roleInput.addEventListener("change", () => {
      paperLabel.hidden = roleInput.value !== "answers";
      document.getElementById("document-import-mode-label").hidden = roleInput.value === "answers";
      paperInput.required = roleInput.value === "answers";
      parserModeInput.disabled = roleInput.value === "answers";
    });
    const parserModeInput = document.getElementById("document-parser-mode");
    const status = document.getElementById("document-upload-progress");
    uploadForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      const button = uploadForm.querySelector("button[type=submit]");
      button.disabled = true;
      try {
        if (!Number.isSafeInteger(file.size) || file.size < 1 || file.size > MAX_UPLOAD_BYTES) {
          throw new Error("文件必须大于 0 字节且不超过 50 MiB。");
        }
        setText(status, "正在计算文件校验值…");
        const digest = await fileHash(file);
        const role = roleInput.value;
        const parserMode = role === "answers" || /\.docx$/i.test(file.name) ? "mineru_local" : (parserModeInput ? parserModeInput.value : "mineru_local");
        const paperId = role === "answers" ? (paperInput.value || null) : null;
        const importMode = role === "answers" ? "questions" : document.getElementById("document-import-mode").value;
        const resumed = findResumeRecord(file, digest, role, paperId, parserMode, importMode);
        const title = (titleInput.value.trim() || (resumed && resumed.title) || file.name.replace(/\.[^.]+$/, "")).trim().slice(0, 240);
        if (!titleInput.value.trim()) titleInput.value = title;
        const requestKey = resumed && resumed.title === title ? resumed.request_key : randomKey();
        const resumeRecord = {
          file_name: file.name,
          file_size: file.size,
          sha256: digest,
          title,
          role,
          parser_mode: parserMode,
          import_mode: importMode,
          paper_id: paperId,
          request_key: requestKey,
          saved_at: Date.now(),
        };
        rememberUpload(resumeRecord);
        const upload = await request("/api/documents/uploads", {
          name: file.name,
          size: file.size,
          sha256: digest,
          title,
          role,
          original_paper_id: paperId,
          parser_mode: parserMode,
          import_mode: importMode,
          tagging_policy: "question_bank",
          request_key: requestKey,
        });
        const received = new Set(upload.received_parts || []);
        if (received.size) setText(status, `已找到此前接收的 ${received.size} / ${upload.total_parts} 个分片，正在续传…`);
        let transferred = received.size;
        for (let index = 0; index < upload.total_parts; index += 1) {
          if (received.has(index)) continue;
          const piece = file.slice(index * upload.chunk_size, Math.min(file.size, (index + 1) * upload.chunk_size));
          const data = await blobBase64(piece);
          const partHash = await fileHash(piece);
          await request(`/api/documents/uploads/${encodeURIComponent(upload.upload_id)}/parts`, {
            index,
            sha256: partHash,
            data_base64: data,
          });
          transferred += 1;
          setText(status, `上传中：${transferred} / ${upload.total_parts} 个分片`);
        }
        setText(status, parserMode === "mineru_api" ? "文件已上传，正在排入 MinerU 云端解析队列…" : "文件已上传，正在排入本地 MinerU 转换队列…");
        const completed = await request(`/api/documents/uploads/${encodeURIComponent(upload.upload_id)}/complete`, { request_key: `complete-${upload.upload_id}` });
        forgetUpload(requestKey);
        window.location.href = url(`/documents/review?task_id=${encodeURIComponent(completed.task_id)}`);
      } catch (error) {
        setText(status, error.message || "上传失败，请重试。已接收的分片会保留。");
        button.disabled = false;
      }
    });
    fileInput.addEventListener("change", () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      const previous = readResumeRecords().find((record) =>
        record.file_name === file.name && record.file_size === file.size && record.role === roleInput.value
      );
      if (previous) {
        if (!titleInput.value.trim()) titleInput.value = previous.title;
        setText(status, "检测到此文件可能有未完成的上传；校验内容一致后会续传已接收的分片。");
      } else {
        setText(status, "");
      }
    });

    const taskList = document.getElementById("document-task-list");
    let polling = false;
    const refreshTasks = async () => {
      if (polling || document.hidden) return;
      polling = true;
      try {
        const result = await request("/api/documents/tasks?limit=25");
        const tasks = result.tasks || result;
        taskList.replaceChildren();
        if (!tasks.length) {
          const empty = document.createElement("p");
          empty.className = "document-empty";
          empty.textContent = "还没有导入任务。";
          taskList.append(empty);
        } else {
          const table = document.createElement("table");
          table.className = "document-task-table";
          const head = document.createElement("thead");
          head.innerHTML = "<tr><th>文件</th><th>类别</th><th>状态</th><th>阶段</th><th>创建时间</th><th></th></tr>";
          const body = document.createElement("tbody");
          tasks.forEach((task) => {
            const row = document.createElement("tr");
            [task.file_name, task.document_role === "paper" ? "试卷" : "答案与解析", task.status, task.phase, task.created_at].forEach((value) => {
              const cell = document.createElement("td");
              cell.textContent = value || "";
              row.append(cell);
            });
            const action = document.createElement("td");
            const link = document.createElement("a");
            link.href = url(`/documents/review?task_id=${encodeURIComponent(task.id)}`);
            link.textContent = "打开复核";
            action.append(link);
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "document-task-delete";
            remove.dataset.taskId = task.id;
            remove.dataset.fileName = task.file_name || "";
            remove.textContent = "删除任务";
            action.append(document.createTextNode(" "), remove);
            row.append(action);
            body.append(row);
          });
          table.append(head, body);
          taskList.append(table);
        }
      } catch (_error) {
        // Keep the last server-rendered task list visible during transient network loss.
      } finally {
        polling = false;
      }
    };
    taskList.addEventListener("click", async (event) => {
      const button = event.target.closest(".document-task-delete");
      if (!button || button.disabled) return;
      const fileName = button.dataset.fileName || "此文件";
      if (!window.confirm(`确定删除“${fileName}”的导入任务及未入库的解析结果和专属文件吗？此操作不可撤销。已入库题目会受到保护；有关联已入库题目的任务无法删除。`)) return;
      button.disabled = true;
      try {
        const result = await request(`/api/documents/tasks/${encodeURIComponent(button.dataset.taskId)}/delete`, {});
        if (result.cleanup_errors && result.cleanup_errors.length) {
          window.alert(`任务已删除，但有 ${result.cleanup_errors.length} 个文件未能清理，请稍后检查存储目录。`);
        }
        await refreshTasks();
      } catch (error) {
        window.alert(error.message || "删除任务失败。");
        button.disabled = false;
      }
    });
    window.setInterval(refreshTasks, 5000);
    document.addEventListener("visibilitychange", refreshTasks);
  }

  const answerDocument = document.querySelector("[data-answer-document-task]");
  if (answerDocument && ["queued", "running"].includes(answerDocument.dataset.taskStatus)) {
    const pollAnswer = async () => {
      try {
        const result = await request(`/api/documents/tasks/${encodeURIComponent(answerDocument.dataset.answerDocumentTask)}`);
        const task = result.task || result;
        if (!["queued", "running"].includes(task.status)) { window.location.reload(); return; }
      } catch (_) { /* Retry transient polling failures. */ }
      window.setTimeout(pollAnswer, 5000);
    };
    window.setTimeout(pollAnswer, 5000);
  }
  const review = document.querySelector("[data-document-review]");
  if (!review) return;
  const taskId = review.dataset.taskId;
  const updateSourceLocator = (node, rawPages) => {
    if (!node) return;
    const pages = Array.from(new Set(rawPages.map(Number).filter((page) => Number.isInteger(page) && page > 0)));
    node.replaceChildren(document.createTextNode(`原卷定位：${pages.length ? `${pages.join("、")} 页` : "未分配来源块"}`));
    const frame = review.querySelector('iframe[name="document-source-preview"]');
    if (!pages.length || !frame || !frame.src) return;
    const link = document.createElement("a");
    const sourceUrl = new URL(frame.src);
    sourceUrl.searchParams.set("jump_page", pages[0]);
    sourceUrl.hash = `page=${pages[0]}`;
    link.href = sourceUrl.toString();
    link.target = frame.name;
    link.textContent = `跳转到第 ${pages[0]} 页`;
    node.append(document.createTextNode(" · "), link);
  };
  const answerForm = review.querySelector("[data-answer-upload-form]");
  if (answerForm) {
    const answerStatus = review.querySelector("[data-answer-status]");
    const previewButton = review.querySelector("[data-answer-preview-task]");
    const previewPanel = review.querySelector("[data-answer-match-preview]");
    const matchList = review.querySelector("[data-answer-match-list]");
    const issueList = review.querySelector("[data-answer-match-issues]");
    const summary = review.querySelector("[data-answer-match-summary]");
    const applyButton = review.querySelector("[data-answer-apply]");
    const answerTaskSelect = review.querySelector("[data-answer-task-select]");
    const existingTaskButton = review.querySelector("[data-answer-preview-existing]");
    let answerTaskId = "";
    let answerPoll = 0;
    const setApplyEnabled = () => {
      applyButton.disabled = !matchList.querySelector("[data-answer-match]:checked");
    };
    const refreshAnswerTask = async () => {
      if (!answerTaskId) return;
      try {
        const task = await request(`/api/documents/tasks/${encodeURIComponent(answerTaskId)}`);
        setText(answerStatus, `答案文件状态：${task.status} · ${task.phase || ""}`);
        if (["parsed", "partially_parsed"].includes(task.status)) {
          previewButton.hidden = false;
          previewButton.disabled = false;
          if (answerPoll) window.clearInterval(answerPoll);
          answerPoll = 0;
        } else if (["failed", "cancelled"].includes(task.status)) {
          if (answerPoll) window.clearInterval(answerPoll);
          answerPoll = 0;
          setText(answerStatus, `答案文件转换${task.status === "failed" ? "失败" : "已取消"}：${task.failure_reason || task.error_code || "请返回任务列表查看详情"}`);
        } else if (!answerPoll) {
          answerPoll = window.setInterval(refreshAnswerTask, 4000);
        }
      } catch (error) {
        setText(answerStatus, error.message || "无法读取答案文件任务状态。");
      }
    };
    const loadAnswerTasks = async () => {
      try {
        const result = await request("/api/documents/tasks?limit=50");
        const tasks = (result.tasks || result).filter((task) =>
          task.original_paper_id === review.querySelector("[data-answer-attachment]").dataset.paperId
          && ["answers", "rubric"].includes(task.document_role)
        );
        answerTaskSelect.replaceChildren();
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = tasks.length ? "选择一个答案文件任务" : "还没有本卷答案文件任务";
        answerTaskSelect.append(placeholder);
        tasks.forEach((task) => {
          const option = document.createElement("option");
          option.value = task.id;
          option.textContent = `${task.file_name} · ${task.status}`;
          answerTaskSelect.append(option);
        });
      } catch (_error) {
        answerTaskSelect.replaceChildren();
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "暂时无法读取本卷答案任务";
        answerTaskSelect.append(option);
      }
    };
    loadAnswerTasks();
    answerTaskSelect.addEventListener("change", () => {
      existingTaskButton.disabled = !answerTaskSelect.value;
    });
    existingTaskButton.disabled = true;
    existingTaskButton.addEventListener("click", async () => {
      answerTaskId = answerTaskSelect.value;
      if (!answerTaskId) return;
      previewButton.hidden = true;
      previewPanel.hidden = true;
      setText(answerStatus, "正在读取所选答案文件任务…");
      await refreshAnswerTask();
    });
    answerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const file = answerForm.querySelector('input[type="file"]').files[0];
      if (!file) return;
      const button = answerForm.querySelector("button[type=submit]");
      button.disabled = true;
      try {
        const digest = await fileHash(file);
        const upload = await request("/api/documents/uploads", {
          name: file.name,
          size: file.size,
          sha256: digest,
          title: file.name.replace(/\.[^.]+$/, ""),
          role: "answers",
          original_paper_id: review.querySelector("[data-answer-attachment]").dataset.paperId,
          request_key: randomKey(),
        });
        const received = new Set(upload.received_parts || []);
        for (let index = 0; index < upload.total_parts; index += 1) {
          if (received.has(index)) continue;
          const piece = file.slice(index * upload.chunk_size, Math.min(file.size, (index + 1) * upload.chunk_size));
          const data = await blobBase64(piece);
          await request(`/api/documents/uploads/${encodeURIComponent(upload.upload_id)}/parts`, {
            index,
            sha256: await fileHash(piece),
            data_base64: data,
          });
          setText(answerStatus, `答案文件上传中：${index + 1} / ${upload.total_parts}`);
        }
        const completed = await request(`/api/documents/uploads/${encodeURIComponent(upload.upload_id)}/complete`, { request_key: randomKey() });
        answerTaskId = completed.task_id;
        const option = document.createElement("option");
        option.value = completed.task_id;
        option.textContent = `${file.name} · 排队中`;
        answerTaskSelect.append(option);
        answerTaskSelect.value = completed.task_id;
        existingTaskButton.disabled = false;
        previewButton.hidden = true;
        previewPanel.hidden = true;
        setText(answerStatus, "答案文件已接收，等待后台转换；转换完成后可生成题号匹配预览。");
        await refreshAnswerTask();
        if (!previewButton.hidden) return;
        if (!answerPoll) answerPoll = window.setInterval(refreshAnswerTask, 4000);
      } catch (error) {
        setText(answerStatus, error.message || "答案文件上传失败；已接收的分片可重试。");
      } finally {
        button.disabled = false;
      }
    });
    previewButton.addEventListener("click", async () => {
      previewButton.disabled = true;
      setText(answerStatus, "正在读取答案块并生成题号匹配建议…");
      try {
        const result = await request(`/api/documents/tasks/${encodeURIComponent(taskId)}/attach-answers`, { answer_task_id: answerTaskId });
        matchList.replaceChildren();
        issueList.replaceChildren();
        (result.matches || []).forEach((match) => {
          const row = document.createElement("li");
          row.className = "answer-match-entry";
          const label = document.createElement("label");
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.dataset.answerMatch = "true";
          checkbox.checked = true;
          checkbox.dataset.itemId = match.item_id;
          checkbox.dataset.answerNumber = match.answer_number;
          checkbox.dataset.expectedRevision = String(match.expected_revision);
          const text = document.createElement("span");
          text.textContent = `第 ${match.question_number} 题 ← 答案区第 ${match.answer_number} 题；复核版本 ${match.expected_revision}。请先对照原解析核验。`;
          label.append(checkbox, text);
          const content = document.createElement("div");
          content.className = "answer-question-comparison";
          const question = document.createElement("section");
          const questionTitle = document.createElement("h3");
          questionTitle.textContent = `第 ${match.question_number} 题`;
          question.innerHTML = match.question_html;
          question.prepend(questionTitle);
          const answer = document.createElement("section");
          answer.innerHTML = `<h3>答案与解析</h3>${match.answer_html}`;
          const editorButton = document.createElement("button");
          editorButton.type = "button";
          editorButton.textContent = "编辑答案与解析";
          const editor = document.createElement("textarea");
          editor.className = "answer-markdown-editor";
          editor.setAttribute("aria-label", "答案与解析 Markdown");
          editor.value = match.answer_markdown;
          editor.hidden = true;
          editor.dataset.answerMarkdown = "true";
          editorButton.addEventListener("click", () => {
            editor.hidden = !editor.hidden;
            editorButton.textContent = editor.hidden ? "编辑答案与解析" : "收起编辑";
          });
          answer.append(editorButton, editor);
          content.append(question, answer);
          row.append(label, content);
          renderMath(content);
          matchList.append(row);
        });
        (result.issues || []).forEach((issue) => {
          const entry = document.createElement("li");
          const names = {
            duplicate_answer_number: "答案区题号重复，未提供自动匹配。",
            question_match_not_unique: "题卷中该题号不是唯一候选，未提供自动匹配。",
            published_question_immutable: "对应题目已经入库，附件不会覆盖已发布内容。",
            existing_answer_preserved: "对应候选已有答案内容，附件不会覆盖。",
          };
          entry.textContent = `答案区第 ${issue.answer_number || "?"} 题：${names[issue.code] || "无法自动关联，需人工处理。"}`;
          issueList.append(entry);
        });
        setText(summary, `找到 ${result.matches.length} 条唯一匹配建议；${result.issues.length} 项无法自动关联。未勾选的建议不会写入。`);
        previewPanel.hidden = false;
        previewPanel.dataset.answerTaskId = answerTaskId;
        matchList.querySelectorAll("[data-answer-match]").forEach((checkbox) => checkbox.addEventListener("change", setApplyEnabled));
        setApplyEnabled();
        setText(answerStatus, "匹配预览完成。答案正文仍需人工核对，确认写入后也保持待复核状态。");
      } catch (error) {
        setText(answerStatus, error.message || "无法生成答案匹配预览。");
      } finally {
        previewButton.disabled = false;
      }
    });
    applyButton.addEventListener("click", async () => {
      const mappings = Array.from(matchList.querySelectorAll("[data-answer-match]:checked"), (checkbox) => ({
        item_id: checkbox.dataset.itemId,
        answer_number: checkbox.dataset.answerNumber,
        expected_revision: Number(checkbox.dataset.expectedRevision),
        answer_markdown: checkbox.closest(".answer-match-entry").querySelector("[data-answer-markdown]").value,
      }));
      if (!mappings.length) return;
      applyButton.disabled = true;
      try {
        await saveAllDraftsBeforeReload();
        mappings.forEach((mapping) => {
          const card = candidateCards().find((item) => item.dataset.documentItem === mapping.item_id);
          if (card) mapping.expected_revision = Number(card.dataset.revision);
        });
        const result = await request(`/api/documents/tasks/${encodeURIComponent(taskId)}/attach-answers`, {
          answer_task_id: answerTaskId,
          mappings,
          request_key: randomKey(),
        });
        setText(answerStatus, `已把 ${result.attached.length} 条答案保存为待复核内容；没有发布题目或改变判分规则。页面即将刷新。`);
        sessionStorage.setItem("hsp-answer-scroll", taskId);
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        setText(answerStatus, error.message || "答案未写入；请重新生成匹配预览。输入仍保留在预览中。");
        setApplyEnabled();
      }
    });
  }
  const renderMath = (container) => {
    container.querySelectorAll(".question-content-image").forEach((image) => {
      const size = () => { const ratio = image.naturalWidth / image.naturalHeight; image.classList.toggle("figure-wide", ratio >= 1.7); image.classList.toggle("figure-tall", ratio < .75); };
      if (image.complete) size(); else image.addEventListener("load", size, {once: true});
    });
    if (window.renderMathInElement) {
      window.renderMathInElement(container, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$", right: "$", display: false },
          { left: "\\[", right: "\\]", display: true },
          { left: "\\(", right: "\\)", display: false },
        ],
        throwOnError: false,
        trust: false,
        ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"],
      });
    }
  };
  const refreshStructureControls = (card, document) => {
    card.questionStructure = document;
    const targetSelect = card.querySelector("[data-structure-target]");
    const targetKey = targetSelect.value;
    targetSelect.replaceChildren(new Option("整题", ""), ...document.children.map((child) => new Option(`小问 ${child.label}`, child.key)));
    targetSelect.value = document.children.some((child) => child.key === targetKey) ? targetKey : "";
    const target = document.children.find((child) => child.key === targetSelect.value) || document;
    card.querySelector("[data-structure-kind]").value = target.kind;
    const optionSelect = card.querySelector("[data-structure-option]");
    const optionKey = optionSelect.value;
    optionSelect.replaceChildren(...target.options.map((option) => new Option(option.key, option.key)));
    if (target.options.some((option) => option.key === optionKey)) optionSelect.value = optionKey;
    card.querySelectorAll("[data-structure-action]").forEach((button) => {
      button.disabled = card.dataset.published === "true" ||
        (["remove_child", "move_child"].includes(button.dataset.structureAction) && !targetSelect.value) ||
        (["remove_option", "move_option"].includes(button.dataset.structureAction) && !optionSelect.value);
    });
  };
  const previewFailure = (card, error) => {
    if (error.previewObsolete) return;
    const body = card.querySelector("[data-preview-body]");
    body.textContent = "预览更新失败，当前修改尚未显示。请保留 Markdown 的章节和起止标记，仅修改正文后重试。";
    setText(card.querySelector(".save-status"), `预览更新失败：${error.message}`);
  };
  const previewCard = async (card, structureAction) => {
    window.clearTimeout(card.previewTimer);
    const sequence = card.previewSequence = (card.previewSequence || 0) + 1;
    const id = card.dataset.documentItem;
    const markdown = card.querySelector(".question-markdown").value;
    const questionNumber = card.querySelector("[data-question-number-edit]").value.trim();
    const operations = JSON.parse(card.dataset.structureOperations || "[]");
    let result;
    try {
      result = await request(`/api/documents/items/${encodeURIComponent(id)}/preview`, {
        task_id: taskId, markdown, structure_operations: operations, structure_action: structureAction,
      });
    } catch (error) {
      error.previewObsolete = sequence !== card.previewSequence;
      previewFailure(card, error);
      throw error;
    }
    if (sequence !== card.previewSequence || markdown !== card.querySelector(".question-markdown").value) {
      const error = new Error("内容已继续修改，正在等待最新预览。");
      error.previewObsolete = true;
      throw error;
    }
    result.document.number = questionNumber;
    result.document.bank_type = card.querySelector('[data-bank-kind]').value;
    card.querySelector("[data-preview-body]").innerHTML = result.html;
    const number = card.querySelector("[data-question-number]");
    if (number) number.textContent = result.document.number;
    renderMath(card.querySelector("[data-preview-body]"));
    refreshStructureControls(card, result.document);
    result.editorMarkdown = markdown;
    return result;
  };
  const saveCard = async (card, reloadAfterSave = true) => {
    if (card.dataset.writeBusy === "true") throw new Error("当前题目正在处理，请稍后再保存。");
    card.dataset.writeBusy = "true";
    const status = card.querySelector(".save-status");
    const saveButton = card.querySelector("[data-save-item]");
    const editor = card.querySelector(".question-markdown");
    const fieldset = card.querySelector(".question-structure-tools fieldset");
    editor.readOnly = true;
    fieldset.disabled = true;
    saveButton.disabled = true;
    setText(status, "正在校验并保存…");
    try {
      const preview = await previewCard(card);
      const document = preview.document;
      const resolved = Array.from(card.querySelectorAll("[data-issue-id]:checked"), (input) => input.dataset.issueId);
      const note = "";
      const result = await request(`/api/documents/items/${encodeURIComponent(card.dataset.documentItem)}/save`, {
        task_id: taskId,
        expected_revision: Number(card.dataset.revision),
        request_key: randomKey(),
        document,
        resolved_issue_ids: resolved,
        resolution_note: note,
      });
      card.dataset.revision = String(result.review_revision);
      card.dataset.savedMarkdown = preview.editorMarkdown;
      card.dataset.savedQuestionNumber = document.number;
      card.dataset.savedBankKind = document.bank_type;
      card.dataset.savedReviewNote = "";
      setText(card.querySelector("[data-revision-label]"), result.review_revision);
      // The saved document is now the server base for subsequent structure edits.
      card.dataset.structureOperations = "[]";
      let issueList = card.querySelector(".document-issues");
      if (!issueList && (result.issues || []).length) {
        issueList = document.createElement("p"); issueList.className = "document-issues";
        card.querySelector(".question-source-link").after(issueList);
      }
      if (issueList) issueList.textContent = (result.issues || []).length ? "题目解析可能有问题，需对照原卷核对。" : "";
      card.dataset.savedIssueResolution = selectedReviewIssueIds(card).join(",");
      cacheDraft(card);
      setText(status, "草稿已保存；其他题目的未保存修改仍保留在页面中。");
      return result;
    } finally {
      card.dataset.writeBusy = "false";
      editor.readOnly = card.dataset.published === "true";
      fieldset.disabled = card.dataset.published === "true";
      saveButton.disabled = false;
    }
  };

  review.querySelectorAll("[data-preview-item]").forEach((button) => {
    button.addEventListener("click", async () => {
      const card = button.closest("[data-document-item]");
      const status = card.querySelector(".save-status");
      setText(status, "正在更新预览…");
      try {
        await previewCard(card);
        setText(status, "预览已更新；保存前修改仍只在此页面。 ");
      } catch (error) {
        previewFailure(card, error);
      }
    });
  });
  review.querySelectorAll("[data-save-item]").forEach((button) => {
    button.addEventListener("click", async () => {
      try { await saveCard(button.closest("[data-document-item]")); }
      catch (error) { setText(button.closest("[data-document-item]").querySelector(".save-status"), error.message); }
    });
  });
  review.querySelectorAll("[data-candidate-move]").forEach((button) => {
    button.addEventListener("click", async () => {
      const list = review.querySelector(".question-card-list");
      const cards = Array.from(list.querySelectorAll(":scope > [data-document-item]"));
      const card = button.closest("[data-document-item]");
      const index = cards.indexOf(card);
      const neighborIndex = index + (button.dataset.candidateMove === "up" ? -1 : 1);
      if (neighborIndex < 0 || neighborIndex >= cards.length) return;
      const oldOrder = cards.map((item) => item.dataset.documentItem);
      const newOrder = oldOrder.slice();
      [newOrder[index], newOrder[neighborIndex]] = [newOrder[neighborIndex], newOrder[index]];
      const status = card.querySelector(".save-status");
      const buttons = Array.from(review.querySelectorAll("[data-candidate-move]"));
      buttons.forEach((item) => { item.disabled = true; });
      setText(status, "正在更新题目顺序…");
      try {
        await request(`/api/documents/tasks/${encodeURIComponent(taskId)}/reorder`, {
          request_key: randomKey(),
          expected_order: oldOrder,
          items: newOrder.map((id) => {
            const current = cards.find((item) => item.dataset.documentItem === id);
            return { id, expected_revision: Number(current.dataset.revision) };
          }),
        });
        if (neighborIndex < index) list.insertBefore(card, cards[neighborIndex]);
        else list.insertBefore(cards[neighborIndex], card);
        const updatedCards = Array.from(list.querySelectorAll(":scope > [data-document-item]"));
        const orderIsComplete = updatedCards.length === Number(review.dataset.candidateCount);
        const hasPublished = updatedCards.some((item) => item.dataset.published === "true");
        updatedCards.forEach((currentCard, currentIndex) => {
          currentCard.querySelectorAll("[data-candidate-move]").forEach((item) => {
            const atEdge = item.dataset.candidateMove === "up" ? currentIndex === 0 : currentIndex === updatedCards.length - 1;
            item.disabled = !orderIsComplete || hasPublished || atEdge;
          });
        });
        refreshRestructureButtons();
        setText(status, "题目顺序已保存，未保存的编辑内容仍保留在页面中。");
      } catch (error) {
        setText(status, error.message || "顺序未更新；请刷新页面并重试。");
        const orderIsComplete = cards.length === Number(review.dataset.candidateCount);
        const hasPublished = cards.some((item) => item.dataset.published === "true");
        buttons.forEach((item) => {
          const currentCard = item.closest("[data-document-item]");
          const currentIndex = cards.indexOf(currentCard);
          const atEdge = item.dataset.candidateMove === "up" ? currentIndex === 0 : currentIndex === cards.length - 1;
          item.disabled = !orderIsComplete || hasPublished || atEdge;
        });
      }
    });
  });

  const candidateCards = () => Array.from(review.querySelectorAll(":scope .question-card-list > [data-document-item]"));
  const refreshRestructureButtons = () => {
    const cards = candidateCards();
    const incomplete = cards.length !== Number(review.dataset.candidateCount);
    const hasPublished = cards.some((item) => item.dataset.published === "true");
    cards.forEach((card, index) => {
      const published = card.dataset.published === "true";
      const splitButton = card.querySelector("[data-restructure-split]");
      const mergeButton = card.querySelector("[data-restructure-merge]");
      if (splitButton) splitButton.disabled = incomplete || hasPublished || published || !splitButton.dataset.sourceBlockId;
      if (mergeButton) {
        const next = cards[index + 1];
        mergeButton.disabled = incomplete || hasPublished || published || !next || next.dataset.published === "true";
      }
    });
  };
  candidateCards().forEach((card) => {
    card.dataset.savedMarkdown = card.querySelector(".question-markdown").value;
    card.dataset.savedQuestionNumber = card.querySelector("[data-question-number-edit]").value.trim();
    card.dataset.savedBankKind = card.querySelector('[data-bank-kind]').value;
    card.dataset.savedIssueResolution = "";
    card.dataset.savedReviewNote = "";
    refreshStructureControls(card, JSON.parse(card.querySelector("[data-question-structure]").dataset.questionStructure));
  });
  refreshRestructureButtons();
  const selectedReviewIssueIds = (card) => Array.from(
    card.querySelectorAll("[data-issue-id]:checked"), (input) => input.dataset.issueId
  ).sort();
  const hasUnsavedCandidateEdits = (card) => (
    JSON.parse(card.dataset.structureOperations || "[]").length > 0 ||
    card.querySelector(".question-markdown").value !== card.dataset.savedMarkdown ||
    card.querySelector("[data-question-number-edit]").value.trim() !== card.dataset.savedQuestionNumber ||
    card.querySelector('[data-bank-kind]').value !== card.dataset.savedBankKind ||
    selectedReviewIssueIds(card).join(",") !== card.dataset.savedIssueResolution
  );
  const draftStorageKey = `hsp-document-review-drafts-v1:${taskId}`;
  const readDrafts = () => {
    try { return JSON.parse(window.sessionStorage.getItem(draftStorageKey) || "{}"); }
    catch (_error) { return {}; }
  };
  const cacheDraft = (card) => {
    if (card.dataset.published === "true") return;
    try {
      const drafts = readDrafts();
      if (!hasUnsavedCandidateEdits(card)) delete drafts[card.dataset.documentItem];
      else drafts[card.dataset.documentItem] = {
        revision: Number(card.dataset.revision), savedAt: Date.now(),
        markdown: card.querySelector(".question-markdown").value,
        number: card.querySelector("[data-question-number-edit]").value,
        bankType: card.querySelector('[data-bank-kind]').value,
        note: "",
        issues: selectedReviewIssueIds(card), operations: JSON.parse(card.dataset.structureOperations || "[]"),
      };
      window.sessionStorage.setItem(draftStorageKey, JSON.stringify(drafts));
    } catch (_error) { /* beforeunload protection still works without storage. */ }
  };
  candidateCards().forEach((card) => {
    const draft = readDrafts()[card.dataset.documentItem];
    if (draft && Date.now() - draft.savedAt < UPLOAD_RESUME_TTL_MS && card.dataset.published !== "true") {
      if (draft.revision === Number(card.dataset.revision)) {
        card.querySelector(".question-markdown").value = draft.markdown;
        card.querySelector("[data-question-number-edit]").value = draft.number;
        if (draft.bankType) card.querySelector('[data-bank-kind]').value = draft.bankType;

        card.dataset.structureOperations = JSON.stringify(draft.operations || []);
        card.querySelectorAll("[data-issue-id]").forEach((input) => { input.checked = draft.issues.includes(input.dataset.issueId); });
        setText(card.querySelector(".save-status"), "已恢复本页未保存草稿；请更新预览并核对后保存。");
        previewCard(card).catch((error) => previewFailure(card, error));
      } else {
        setText(card.querySelector(".save-status"), "有旧版本本地草稿，未覆盖当前版本。可下载后对照恢复。");
        const download = document.createElement("button");
        download.type = "button"; download.textContent = "下载旧版本草稿";
        download.addEventListener("click", () => {
          const link = document.createElement("a");
          link.href = URL.createObjectURL(new Blob([JSON.stringify(draft, null, 2)], { type: "application/json" }));
          link.download = `review-draft-${card.dataset.documentItem}.json`; link.click();
          window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        });
        card.querySelector(".question-card-actions").append(download);
      }
    }
    card.addEventListener("input", (event) => {
      cacheDraft(card);
      if (!event.target.matches(".question-markdown")) return;
      card.previewSequence = (card.previewSequence || 0) + 1;
      window.clearTimeout(card.previewTimer);
      // Hide stale content immediately, including if the next request fails.
      card.querySelector("[data-preview-body]").textContent = "正文已修改，正在更新预览…";
      setText(card.querySelector(".save-status"), "正文已修改，等待预览更新；尚未保存。");
      card.previewTimer = window.setTimeout(async () => {
        try {
          await previewCard(card);
          setText(card.querySelector(".save-status"), "预览已更新；修改尚未保存。");
        } catch (error) {
          previewFailure(card, error);
        }
      }, 450);
    });
    card.addEventListener("change", () => cacheDraft(card));
    card.querySelector("[data-structure-target]").addEventListener("change", () => refreshStructureControls(card, card.questionStructure));
    const changeStructure = async (operation) => {
      if (card.dataset.writeBusy === "true") return;
      card.dataset.writeBusy = "true";
      const status = card.querySelector(".save-status");
      const fieldset = card.querySelector(".question-structure-tools fieldset");
      const editor = card.querySelector(".question-markdown");
      const saveButton = card.querySelector("[data-save-item]");
      editor.readOnly = true;
      saveButton.disabled = true;
      fieldset.disabled = true;
      try {
        const result = await previewCard(card, operation);
        card.dataset.structureOperations = JSON.stringify([...JSON.parse(card.dataset.structureOperations || "[]"), operation]);
        card.querySelector(".question-markdown").value = result.markdown;
        if (operation.action === "add_child") {
          card.querySelector("[data-structure-target]").value = operation.key;
          refreshStructureControls(card, result.document);
        }
        cacheDraft(card);
        setText(status, "结构已在本页调整；请修改新增正文、核对题型与答案，再保存草稿。");
      } catch (error) { setText(status, error.message); }
      finally {
        card.dataset.writeBusy = "false";
        editor.readOnly = card.dataset.published === "true";
        saveButton.disabled = false;
        fieldset.disabled = card.dataset.published === "true";
      }
    };
    card.querySelector("[data-structure-kind]").addEventListener("change", (event) => changeStructure({
      action: "set_kind", child_key: card.querySelector("[data-structure-target]").value, kind: event.target.value,
    }));
    card.querySelectorAll("[data-structure-action]").forEach((button) => button.addEventListener("click", () => changeStructure({
      action: button.dataset.structureAction,
      child_key: card.querySelector("[data-structure-target]").value,
      option_key: card.querySelector("[data-structure-option]").value,
      direction: button.dataset.direction,
      key: "part_" + randomKey().slice(0, 32),
    })));
  });
  const hasUnsavedSourceMapping = () => Array.from(review.querySelectorAll("[data-source-owner]")).some((select) =>
    Array.from(select.selectedOptions, (option) => option.value).sort().join(",") !==
    (select.dataset.initialOwners || "").split(",").filter(Boolean).sort().join(",")
  );
  window.addEventListener("beforeunload", (event) => {
    if (!candidateCards().some(hasUnsavedCandidateEdits) && !hasUnsavedSourceMapping()) return;
    candidateCards().forEach(cacheDraft);
    event.preventDefault(); event.returnValue = "";
  });
  const saveAllDraftsBeforeReload = async () => {
    if (hasUnsavedSourceMapping()) throw new Error("请先保存来源块归属调整，再执行需要刷新页面的操作。");
    for (const card of candidateCards().filter(hasUnsavedCandidateEdits)) await saveCard(card, false);
  };
  const reviewDraftExport = review.querySelector("[data-review-draft-export]");
  let exportingReviewDraft = false;
  reviewDraftExport?.addEventListener("click", async (event) => {
    const changed = candidateCards().filter(hasUnsavedCandidateEdits);
    if (!changed.length) return;
    event.preventDefault();
    if (exportingReviewDraft) return;
    exportingReviewDraft = true;
    reviewDraftExport.setAttribute("aria-disabled", "true");
    const status = review.querySelector("[data-batch-status]");
    setText(status, `正在保存 ${changed.length} 道有修改的题目，再生成校对稿…`);
    try {
      for (const card of changed) await saveCard(card, false);
      setText(status, "草稿已保存，正在下载校对稿…");
      window.location.assign(reviewDraftExport.href);
    } catch (error) {
      setText(status, error.message || "有题目未保存成功；校对稿没有下载，请修复后重试。");
    } finally {
      exportingReviewDraft = false;
      reviewDraftExport.removeAttribute("aria-disabled");
    }
  });
  const restructure = async (button, action) => {
    const cards = candidateCards();
    const card = button.closest("[data-document-item]");
    const status = card.querySelector(".save-status");
    try { await saveAllDraftsBeforeReload(); }
    catch (error) { setText(status, error.message); return; }
    let ids;
    const payload = {
      action,
      request_key: randomKey(),
      expected_revisions: {},
    };
    if (action === "split") {
      const editor = card.querySelector(".question-markdown");
      const startToken = "<!-- hsp:stem:start -->\n";
      const endToken = "\n<!-- hsp:stem:end -->";
      const startMarker = editor.value.indexOf(startToken);
      const bodyStart = startMarker < 0 ? -1 : startMarker + startToken.length;
      const bodyEnd = bodyStart < 0 ? -1 : editor.value.indexOf(endToken, bodyStart);
      if (
        startMarker < 0 || bodyEnd < 0 || editor.selectionStart !== editor.selectionEnd ||
        editor.selectionStart <= bodyStart || editor.selectionStart >= bodyEnd
      ) {
        setText(status, "请把光标放在题干正文中、想要分开的两个部分之间，再点“在题干光标处分题”。");
        return;
      }
      const blockId = button.dataset.sourceBlockId;
      if (!blockId) { setText(status, "当前候选没有来源块，无法建立可追溯的拆分记录。"); return; }
      ids = [card.dataset.documentItem];
      payload.split_anchor = { block_id: blockId, offset: editor.selectionStart - bodyStart };
    } else {
      const index = cards.indexOf(card);
      const next = cards[index + 1];
      if (!next) return;
      if (hasUnsavedCandidateEdits(next)) {
        setText(status, "请先保存下一题的草稿；合并只会读取服务端已保存版本。");
        return;
      }
      ids = [card.dataset.documentItem, next.dataset.documentItem];
    }
    payload.ids = ids;
    ids.forEach((id) => {
      const current = cards.find((item) => item.dataset.documentItem === id);
      payload.expected_revisions[id] = Number(current.dataset.revision);
    });
    const buttons = Array.from(review.querySelectorAll("[data-restructure-split], [data-restructure-merge]"));
    buttons.forEach((item) => { item.disabled = true; });
    setText(status, action === "split" ? "正在按保存版本拆分候选…" : "正在按阅读顺序合并候选…");
    try {
      await request(`/api/documents/tasks/${encodeURIComponent(taskId)}/restructure`, payload);
      setText(status, "重构已保存；正在刷新候选及来源映射…");
      window.location.reload();
    } catch (error) {
      setText(status, error.message || "候选重构未保存；原候选保持不变，可刷新后重试。");
      refreshRestructureButtons();
    }
  };
  review.querySelectorAll("[data-restructure-split]").forEach((button) => {
    button.addEventListener("click", () => restructure(button, "split"));
  });
  review.querySelectorAll("[data-restructure-merge]").forEach((button) => {
    button.addEventListener("click", () => restructure(button, "merge"));
  });

  const sourceOwnerSelects = Array.from(review.querySelectorAll("[data-source-owner]"));
  const sourceMappingButton = review.querySelector("[data-save-source-mapping]");
  const sourceMappingStatus = review.querySelector("[data-source-mapping-status]");
  const selectedOwners = (select) => Array.from(select.selectedOptions, (option) => option.value);
  const sameValues = (left, right) => left.slice().sort().join("\u0000") === right.slice().sort().join("\u0000");
  const spansForCandidate = (candidateId) => sourceOwnerSelects.flatMap((select) => {
    if (!selectedOwners(select).includes(candidateId)) return [];
    const selected = Array.from(select.selectedOptions).find((option) => option.value === candidateId);
    try { return [JSON.parse(select.dataset.sourceSpan)]; }
    catch (_error) { return selected ? [] : []; }
  });
  sourceOwnerSelects.forEach((select) => {
    select.addEventListener("change", () => {
      if (!selectedOwners(select).length) {
        const initial = (select.dataset.initialOwners || "").split(",").filter(Boolean);
        Array.from(select.options).forEach((option) => { option.selected = initial.includes(option.value); });
        setText(sourceMappingStatus, "每个原卷块至少保留一个题目归属。");
      } else {
        setText(sourceMappingStatus, "归属有未保存更改；请保存来源块调整。题目正文尚未改动。");
      }
    });
  });
  sourceMappingButton?.addEventListener("click", async () => {
    const changed = sourceOwnerSelects.filter((select) => {
      const initial = (select.dataset.initialOwners || "").split(",").filter(Boolean);
      return !sameValues(selectedOwners(select), initial);
    });
    if (!changed.length) { setText(sourceMappingStatus, "没有未保存的来源块调整。"); return; }
    const affectedIds = new Set();
    changed.forEach((select) => {
      (select.dataset.initialOwners || "").split(",").filter(Boolean).forEach((id) => affectedIds.add(id));
      selectedOwners(select).forEach((id) => affectedIds.add(id));
    });
    const affectedCards = Array.from(affectedIds, (id) => review.querySelector(`[data-document-item="${CSS.escape(id)}"]`)).filter(Boolean);
    const items = affectedCards.map((card) => ({
      id: card.dataset.documentItem,
      expected_revision: Number(card.dataset.revision),
      source_spans: spansForCandidate(card.dataset.documentItem),
    }));
    sourceMappingButton.disabled = true;
    sourceOwnerSelects.forEach((select) => { select.disabled = true; });
    setText(sourceMappingStatus, "正在校验并保存来源块归属…");
    try {
      const result = await request(`/api/documents/tasks/${encodeURIComponent(taskId)}/sources`, {
        request_key: randomKey(),
        items,
      });
      (result.updated || []).forEach((updated) => {
        const card = review.querySelector(`[data-document-item="${CSS.escape(updated.id)}"]`);
        if (!card) return;
        card.dataset.revision = String(updated.review_revision);
        setText(card.querySelector("[data-revision-label]"), updated.review_revision);
        const pages = Array.from(new Set(spansForCandidate(updated.id)
          .map((span) => span.source_locator?.page)
          .filter(Boolean)));
        updateSourceLocator(card.querySelector(".question-source-link"), pages);
      });
      sourceOwnerSelects.forEach((select) => { select.dataset.initialOwners = selectedOwners(select).join(","); });
      candidateCards().forEach((card) => {
        const spans = spansForCandidate(card.dataset.documentItem);
        const splitButton = card.querySelector("[data-restructure-split]");
        if (splitButton) splitButton.dataset.sourceBlockId = spans[0]?.block_id || "";
      });
      refreshRestructureButtons();
      setText(sourceMappingStatus, "来源块归属已原子保存；题目正文和图片引用未被改写。");
    } catch (error) {
      setText(sourceMappingStatus, error.message || "来源块归属未保存；页面选择仍保留，可刷新后重试。");
    } finally {
      sourceMappingButton.disabled = false;
      sourceOwnerSelects.forEach((select) => { select.disabled = select.dataset.sourceLocked === "true"; });
    }
  });

  let activeMarkdownEditor = null;
  review.addEventListener("focusin", (event) => {
    if (event.target.matches(".question-markdown")) activeMarkdownEditor = event.target;
  });
  review.querySelectorAll("[data-insert-source-asset]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!activeMarkdownEditor || !review.contains(activeMarkdownEditor)) {
        activeMarkdownEditor = review.querySelector("[data-document-item]:not([data-published='true']) .question-markdown");
      }
      if (!activeMarkdownEditor) return;
      const token = `![原卷插图](asset:${button.dataset.insertSourceAsset})`;
      let start = activeMarkdownEditor.selectionStart;
      let end = activeMarkdownEditor.selectionEnd;
      if (start !== end) start = end;
      const lineStart = activeMarkdownEditor.value.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
      const nextNewline = activeMarkdownEditor.value.indexOf("\n", start);
      const lineEnd = nextNewline < 0 ? activeMarkdownEditor.value.length : nextNewline;
      const currentLine = activeMarkdownEditor.value.slice(lineStart, lineEnd).trim();
      if (currentLine.startsWith("<!-- hsp:") && currentLine.includes(":start -->")) {
        start = end = nextNewline < 0 ? lineEnd : nextNewline + 1;
      } else if (currentLine.startsWith("<!-- hsp:") && currentLine.includes(":end -->")) {
        start = end = lineStart;
      } else if (/^## (题干|选项|小问|答案|解析)$/.test(currentLine)) {
        start = end = nextNewline < 0 ? lineEnd : nextNewline + 1;
      }
      const padding = start > 0 && activeMarkdownEditor.value[start - 1] !== "\n" ? "\n\n" : "";
      activeMarkdownEditor.setRangeText(`${padding}${token}\n`, start, end, "end");
      activeMarkdownEditor.dispatchEvent(new Event("input", { bubbles: true }));
      activeMarkdownEditor.focus();
      setText(activeMarkdownEditor.closest("[data-document-item]").querySelector(".save-status"), "原卷图片引用已插入当前光标；更新预览核对位置后保存。");
    });
  });
  review.querySelectorAll("[data-preview-body]").forEach(renderMath);

  review.querySelectorAll(".answer-question-comparison").forEach(renderMath);
  review.querySelectorAll("[data-edit-saved-answer]").forEach((button) => {
    button.addEventListener("click", () => {
      const card = candidateCards().find((entry) => entry.dataset.documentItem === button.dataset.editSavedAnswer);
      if (!card) return;
      card.querySelector(".question-editor").hidden = false;
      const edit = card.querySelector("[data-edit-question]");
      edit.setAttribute("aria-expanded", "true"); edit.textContent = "收起编辑";
      card.querySelector(".question-markdown").focus();
      card.scrollIntoView({block:"start"});
    });
  });
  if (sessionStorage.getItem("hsp-answer-scroll") === taskId) {
    sessionStorage.removeItem("hsp-answer-scroll");
    review.querySelector("[data-answer-attachment]")?.scrollIntoView({block:"start"});
  }
  review.querySelectorAll("[data-edit-question]").forEach((button) => {
    button.addEventListener("click", () => {
      const editor = button.closest("[data-document-item]").querySelector(".question-editor");
      editor.hidden = !editor.hidden;
      button.setAttribute("aria-expanded", String(!editor.hidden));
      button.textContent = editor.hidden ? "编辑题目" : "收起编辑";
    });
  });
  review.querySelector("[data-confirm-paper]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    const status = review.querySelector("[data-batch-status]");
    try {
      await saveAllDraftsBeforeReload();
      await request(`/api/documents/tasks/${encodeURIComponent(taskId)}/confirm-review`, {
        request_key: randomKey(),
        items: candidateCards().map((card) => ({ id: card.dataset.documentItem, expected_revision: Number(card.dataset.revision) })),
      });
      sessionStorage.setItem("hsp-answer-scroll", taskId);
      window.location.reload();
    } catch (error) { setText(status, error.message); }
    finally { button.disabled = false; }
  });
  const selectAll = review.querySelector("[data-publish-select-all]");
  const selectAllButton = review.querySelector("[data-publish-select-all-button]");
  const selectionCount = review.querySelector("[data-publish-selection-count]");
  const publishCheckboxes = () => Array.from(review.querySelectorAll('[data-document-item][data-published="false"] [data-publish-select]:not(:disabled)'));
  const syncPublicationSelection = () => {
    const boxes = publishCheckboxes();
    const count = boxes.filter((box) => box.checked).length;
    selectAll.checked = boxes.length > 0 && count === boxes.length;
    selectAll.indeterminate = count > 0 && count < boxes.length;
    selectAll.disabled = selectAllButton.disabled = boxes.length === 0;
    setText(selectionCount, `已选 ${count} / ${boxes.length} 道题`);
  };
  const selectPublicationItems = (checked) => {
    publishCheckboxes().forEach((box) => { box.checked = checked; });
    syncPublicationSelection();
  };
  selectAll.addEventListener("change", () => selectPublicationItems(selectAll.checked));
  selectAllButton.addEventListener("click", () => selectPublicationItems(true));
  review.addEventListener("change", (event) => {
    if (event.target.matches("[data-publish-select]")) syncPublicationSelection();
  });
  syncPublicationSelection();
  const confirmButton = review.querySelector("[data-confirm-items]");
  confirmButton.addEventListener("click", async () => {
    const cards = Array.from(review.querySelectorAll("[data-document-item]"));
    const selected = cards.filter((card) => card.querySelector("[data-publish-select]").checked && card.dataset.published !== "true");
    const status = review.querySelector("[data-batch-status]");
    if (!selected.length) { setText(status, "请先勾选至少一道题。"); return; }
    confirmButton.disabled = true;
    try {
      await saveAllDraftsBeforeReload();
      for (const card of selected) {
        const saved = await saveCard(card, false);
        card.dataset.revision = String(saved.review_revision);
      }
      const answerMappings = Array.from(review.querySelectorAll("[data-answer-match]:checked"))
        .filter(box => selected.some(card => card.dataset.documentItem === box.dataset.itemId))
        .map(box => ({item_id:box.dataset.itemId, answer_number:box.dataset.answerNumber,
          expected_revision:Number(selected.find(card => card.dataset.documentItem===box.dataset.itemId).dataset.revision),
          answer_markdown:box.closest(".answer-match-entry").querySelector("[data-answer-markdown]").value}));
      if (answerMappings.length) {
        const attached = await request(`/api/documents/tasks/${encodeURIComponent(taskId)}/attach-answers`, {
          answer_task_id:review.querySelector("[data-answer-match-preview]").dataset.answerTaskId, mappings:answerMappings, request_key:randomKey()
        });
        for (const item of attached.attached) selected.find(card => card.dataset.documentItem===item.item_id).dataset.revision=String(item.review_revision);
      }
      const result = await request(`/api/documents/tasks/${encodeURIComponent(taskId)}/confirm`, {
        request_key: randomKey(),
        reviewed: true,
        items: selected.map((card) => ({ id: card.dataset.documentItem, expected_revision: Number(card.dataset.revision) })),
      });
      const tagging = result.automatic_tagging || [];
      const tagged = tagging.filter((item) => item.status === "tagged").length;
      const queued = tagging.filter((item) => ["queued", "running"].includes(item.status)).length;
      const skipped = tagging.some((item) => item.reason === "llm_provider_not_configured");
      const failed = tagging.some((item) => item.status === "failed");
      const tagStatus = skipped ? "；未配置可用大模型，题目已入库但尚未自动标注" : failed ? "；部分自动标签调用失败，请在题库中重试" : queued ? `；${queued} 道已进入后台自动标注队列` : `；自动标签完成 ${tagged} 道`;
      setText(status, `批量入库完成：${(result.published || []).length} 道完整大题${tagStatus}。`);
      window.location.href = url(`/question-bank?batch_id=${encodeURIComponent(result.batch_id || "")}`);
    } catch (error) {
      setText(status, error.message || "草稿修改已保存；本次正式入库未部分写入。请检查复核事项后重试。");
    } finally {
      confirmButton.disabled = false;
    }
  });

  let taskTimer;
  const initialTaskStatus = review.dataset.taskStatus;
  const tagStatusNode = review.querySelector("[data-auto-tagging-status]");
  const refreshTaggingStatus = (task) => {
    const jobs = task.automatic_tagging || [];
    if (!tagStatusNode || !jobs.length) return false;
    const pending = jobs.filter((job) => ["queued", "running"].includes(job.status)).length;
    const failed = jobs.filter((job) => job.status === "failed").length;
    const notConfigured = jobs.filter((job) => job.reason === "llm_provider_not_configured").length;
    const complete = jobs.length - pending - failed - notConfigured;
    const prefix = pending ? `${pending} 道处理中` : "后台标注已结束";
    const suffix = notConfigured ? `；${notConfigured} 道未标注（未配置大模型）` : failed ? `；${failed} 道失败` : `；${complete} 道完成`;
    setText(tagStatusNode, `自动标签：${prefix}${suffix}`);
    return pending > 0;
  };
  const pollTask = async () => {
    if (document.hidden) {
      clearTimeout(taskTimer);
      taskTimer = window.setTimeout(pollTask, 15000);
      return;
    }
    try {
      const task = await request(`/api/documents/tasks/${encodeURIComponent(taskId)}`);
      const taggingPending = refreshTaggingStatus(task);
      if (["queued", "running"].includes(task.status) || taggingPending) {
        clearTimeout(taskTimer);
        taskTimer = window.setTimeout(pollTask, 5000);
      } else if (["queued", "running"].includes(initialTaskStatus)) {
        window.location.reload();
      }
    } catch (_error) {
      clearTimeout(taskTimer);
      taskTimer = window.setTimeout(pollTask, 10000);
    }
  };
  pollTask();
  document.addEventListener("visibilitychange", () => {
    clearTimeout(taskTimer);
    if (!document.hidden) pollTask();
  });
})();
