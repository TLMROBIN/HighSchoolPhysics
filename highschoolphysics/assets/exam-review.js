/* Server owns all facts. Local edits survive polling and background AI completion. */
(() => {
  'use strict';
  const base = () => location.pathname.replace(/\/(?:exams|exam-review)\/?$/, '');
  const api = async (action, payload) => {
    const response = await fetch(base() + '/api/learning/' + action, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    });
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('服务返回异常，请刷新登录状态后重试'); }
    if (!response.ok || !data.ok) {
      const error = new Error(data.message || data.error || '操作未完成，请重试');
      error.status = response.status; throw error;
    }
    return data.result;
  };
  document.querySelectorAll('[data-review-create]').forEach(form => {
    let requestKey = null, requestText = null;
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const status = form.querySelector('[role="status"]'), button = form.querySelector('button[type="submit"]');
      const payload = {assessment_id: form.dataset.assessmentId,
        class_ids: [...form.querySelectorAll('[name="class_ids"]:checked')].map(input => input.value),
        minutes: Number(form.elements.minutes.value)};
      ['show_answer', 'show_statistics', 'show_knowledge', 'show_solution', 'show_errors'].forEach(key => { payload[key] = form.elements[key].checked; });
      if (!payload.class_ids.length) { status.textContent = '请至少选择一个讲评班级'; return; }
      const text = JSON.stringify(payload);
      if (text !== requestText) { requestText = text; requestKey = crypto.randomUUID(); }
      payload.request_key = requestKey;
      button.disabled = true; button.setAttribute('aria-busy', 'true'); status.textContent = '正在整理原题和作答统计…';
      try {
        const result = await api('review-create', payload);
        location.href = base() + '/exam-review?id=' + encodeURIComponent(result.id);
      } catch (error) { status.textContent = error.message; button.disabled = false; button.removeAttribute('aria-busy'); }
    });
  });
  const root = document.querySelector('[data-exam-review]');
  if (!root) return;
  const packageId = root.dataset.examReview;
  const states = {queued: '排队中', running: '正在生成', ready: 'AI 草稿已生成', failed: '生成失败，可重试', reference_only: '原资料草稿，待补充'};
  const labels = {teacher: '教师编辑', ai: 'AI 生成，需核对', reference: '原解析或考试标签'};
  let polling = false, timer;
  const message = root.querySelector('[data-review-message]');
  const cards = [...root.querySelectorAll('[data-review-item]')];
  const drafts = new Map();
  cards.forEach(card => {
    const form = card.querySelector('[data-review-edit]');
    const dirtyFields = new Set();
    const key = 'hsp-review:' + packageId + ':' + card.dataset.reviewItem;
    const snapshot = () => ({revision: Number(card.dataset.revision), content: Object.fromEntries(
      [...form.querySelectorAll('textarea')].map(input => [input.name, input.value])),
      included: form.elements.included.checked, reviewed: form.elements.reviewed.checked, emphasis: form.elements.emphasis.value});
    const cache = () => { try { sessionStorage.setItem(key, JSON.stringify({...snapshot(), dirtyFields: [...dirtyFields]})); } catch (_) {} };
    const clear = () => { try { sessionStorage.removeItem(key); } catch (_) {} };
    try {
      const stored = JSON.parse(sessionStorage.getItem(key));
      if (stored) {
        Object.entries(stored.content || {}).forEach(([name, value]) => { if (form.elements[name] && typeof value === 'string') form.elements[name].value = value; });
        form.elements.included.checked = stored.included;
        form.elements.reviewed.checked = stored.reviewed;
        form.elements.emphasis.value = stored.emphasis;
        (stored.dirtyFields || Object.keys(stored.content || {})).forEach(name => dirtyFields.add(name));
        form.dataset.dirty = 'true';
        form.querySelector('[role="status"]').textContent = Number(card.dataset.revision) === stored.revision ? '已恢复本页未保存的编辑，请保存' : '已恢复旧版未保存内容，请核对后保存；服务器已有更新';
        // Keep the old revision to force a conflict rather than overwrite newer edits.
        card.dataset.revision = stored.revision;
      }
    } catch (_) {}
    form.addEventListener('input', event => {
      if (event.target.tagName === 'TEXTAREA') dirtyFields.add(event.target.name);
      form.dataset.dirty = 'true'; cache();
    });
    form.addEventListener('change', () => { form.dataset.dirty = 'true'; cache(); });
    drafts.set(card.dataset.reviewItem, {form, clear});
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]'), status = form.querySelector('[role="status"]');
      const sent = snapshot(); button.disabled = true; status.textContent = '正在保存…';
      try {
        const item = await api('review-save', {package_id: packageId, item_id: card.dataset.reviewItem, ...sent,
          content: Object.fromEntries(Object.entries(sent.content).filter(([key]) => dirtyFields.has(key)))});
        card.dataset.revision = item.revision;
        if (JSON.stringify({...snapshot(), revision: sent.revision}) === JSON.stringify(sent)) {
          delete form.dataset.dirty; dirtyFields.clear(); clear(); status.textContent = '已保存本题讲评';
          Object.entries(item.content).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value; });
        } else { cache(); status.textContent = '已保存提交时的内容；后续编辑尚未保存'; }
        form.querySelectorAll('[data-field-source]').forEach(label => { label.textContent = labels[item.field_sources[label.dataset.fieldSource]]; });
      } catch (error) {
        status.textContent = error.message;
        if (error.status === 409) {
          try {
            const pack = await api('review-status', {package_id: packageId});
            const latest = pack.items.find(item => item.id === card.dataset.reviewItem);
            if (latest) {
              const panel = form.querySelector('[data-review-conflict]');
              panel.hidden = false; panel.open = true;
              panel.querySelector('[data-review-latest]').textContent = Object.entries(latest.content).map(([key, value]) => {
                const label = form.elements[key]?.closest('label')?.childNodes[0]?.textContent?.trim() || key;
                return label + '\n' + value;
              }).join('\n\n');
              panel.querySelector('[data-review-keep]').onclick = () => {
                Object.entries(latest.content).forEach(([key, value]) => { if (!dirtyFields.has(key) && form.elements[key]) form.elements[key].value = value; });
                card.dataset.revision = latest.revision; form.elements.reviewed.checked = false;
                form.dataset.dirty = 'true'; cache(); panel.hidden = true;
                status.textContent = '已保留改过的字段并载入新版内容，请核对后保存';
              };
              panel.querySelector('[data-review-replace]').onclick = () => {
                Object.entries(latest.content).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value; });
                form.elements.included.checked = latest.included; form.elements.reviewed.checked = latest.reviewed;
                form.elements.emphasis.value = latest.emphasis; card.dataset.revision = latest.revision;
                dirtyFields.clear(); delete form.dataset.dirty; clear(); panel.hidden = true;
                status.textContent = '已载入服务器上的新版内容';
              };
            }
          } catch (_) { status.textContent += '；暂时无法读取新版，请保留输入后稍后重试'; }
        }
      }
      finally { button.disabled = false; }
    });
    form.querySelector('[data-review-retry]').addEventListener('click', async event => {
      event.target.disabled = true;
      try {
        await api('review-retry', {package_id: packageId, item_id: card.dataset.reviewItem});
        form.querySelector('[role="status"]').textContent = '本题已排队；教师编辑会保留'; refresh();
      } catch (error) { form.querySelector('[role="status"]').textContent = error.message; event.target.disabled = false; }
    });
  });
  async function refresh() {
    if (polling) return;
    polling = true; clearTimeout(timer);
    try {
      const pack = await api('review-status', {package_id: packageId});
      root.querySelector('progress').value = pack.completed;
      root.querySelector('[data-review-progress]').textContent = `已处理 ${pack.completed} / ${pack.total} · 排队 ${pack.counts.queued} · 生成中 ${pack.counts.running} · 失败 ${pack.counts.failed} · 原资料草稿 ${pack.counts.reference_only}`;
      root.querySelector('[data-review-stale]').hidden = !pack.stale;
      pack.items.forEach(item => {
        const card = cards.find(node => node.dataset.reviewItem === item.id), draft = drafts.get(item.id);
        if (!card || !draft) return;
        const form = draft.form;
        card.querySelector('[data-item-state]').textContent = states[item.status];
        form.querySelector('[data-review-error]').textContent = item.error;
        form.querySelector('[data-review-retry]').disabled = !['failed', 'reference_only'].includes(item.status) || item.source.answer_state !== 'verified';
        if (!form.dataset.dirty && Number(card.dataset.revision) !== item.revision) {
          Object.entries(item.content).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value; });
          card.dataset.revision = item.revision;
          form.elements.included.checked = item.included;
          form.elements.reviewed.checked = item.reviewed;
          form.elements.emphasis.value = item.emphasis;
          form.querySelectorAll('[data-field-source]').forEach(label => { label.textContent = labels[item.field_sources[label.dataset.fieldSource]]; });
        }
      });
      message.textContent = '';
      if (pack.counts.queued + pack.counts.running > 0) timer = setTimeout(refresh, 8000);
    } catch (error) { message.textContent = error.message; }
    finally { polling = false; }
  }
  root.querySelector('[data-review-refresh]').addEventListener('click', refresh);
  root.querySelector('[data-review-download]').addEventListener('click', event => {
    if ([...drafts.values()].some(draft => draft.form.dataset.dirty)) {
      event.preventDefault(); message.textContent = '有尚未保存的编辑，请先保存对应题目后下载';
      return;
    }
    message.textContent = '正在打包原题图片与讲评资料，请稍候…';
    setTimeout(() => { message.textContent = ''; }, 12000);
  });
  window.addEventListener('beforeunload', event => {
    if ([...drafts.values()].some(draft => draft.form.dataset.dirty)) { event.preventDefault(); event.returnValue = ''; }
  });
  refresh();
})();
