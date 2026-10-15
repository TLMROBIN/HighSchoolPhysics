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
  function syncItemSummary(item) {
    const label=item.included?(item.emphasis==='detail'?'详讲':'略讲'):'不纳入';
    const row=[...orderList.children].find(x=>x.dataset.orderItem===item.id);
    if(row)row.querySelector('[data-order-state]').textContent=label;
    const card=cards.find(x=>x.dataset.reviewItem===item.id);
    const rate=item.source.statistics.correct_rate;
    if(card)card.querySelector('summary strong').textContent=label+' · '+(rate===null?'暂无有效数据':(rate*100).toFixed(1)+'%');
  }
  const drafts = new Map();
  const arrangement = root.querySelector('[data-review-arrange]');
  const orderList = arrangement.querySelector('.review-order');
  const arrangementKey = 'hsp-review-plan:' + packageId;
  const settingKeys = ['show_answer','show_statistics','show_knowledge','show_solution','show_errors'];
  const order = () => [...orderList.children].map(row => row.dataset.orderItem);
  const planSnapshot = () => ({revision:Number(arrangement.dataset.revision), item_ids:order(),
    minutes:Number(arrangement.elements.minutes.value), reveal_mode:arrangement.elements.reveal_mode.value,
    ...Object.fromEntries(settingKeys.map(key => [key,arrangement.elements[key].checked]))});
  function orderButtons() {
    [...orderList.children].forEach((row,i) => {
      row.querySelector('[data-order-up]').disabled=i===0;
      row.querySelector('[data-order-down]').disabled=i===orderList.children.length-1;
    });
  }
  function loadPlan(plan) {
    arrangement.dataset.revision=plan.revision;
    arrangement.elements.minutes.value=plan.minutes;
    arrangement.elements.reveal_mode.value=plan.reveal_mode || 'after';
    settingKeys.forEach(key => { arrangement.elements[key].checked=plan[key]; });
    plan.item_ids.forEach(id => { const row=[...orderList.children].find(x=>x.dataset.orderItem===id); if(row) orderList.append(row); });
    orderButtons();
  }
  function planDirty() {
    arrangement.dataset.dirty='true';
    try { sessionStorage.setItem(arrangementKey,JSON.stringify(planSnapshot())); } catch (_) {}
  }
  try {
    const cached=JSON.parse(sessionStorage.getItem(arrangementKey));
    if(cached && JSON.stringify([...cached.item_ids].sort())===JSON.stringify(order().sort())) {
      loadPlan(cached); arrangement.dataset.dirty='true';
      arrangement.querySelector('[role=status]').textContent='已恢复未保存的教学编排，请核对后保存';
    }
  } catch (_) {}
  orderButtons();
  arrangement.addEventListener('input',planDirty);
  arrangement.addEventListener('change',planDirty);
  orderList.addEventListener('click',event => {
    const button=event.target.closest('[data-order-up],[data-order-down]');
    if(!button) return;
    const row=button.closest('[data-order-item]');
    if(button.hasAttribute('data-order-up') && row.previousElementSibling) orderList.insertBefore(row,row.previousElementSibling);
    else if(button.hasAttribute('data-order-down') && row.nextElementSibling) orderList.insertBefore(row.nextElementSibling,row);
    orderButtons(); planDirty();
  });
  arrangement.addEventListener('submit',async event => {
    event.preventDefault();
    const sent=planSnapshot(),button=arrangement.querySelector('[type=submit]'),status=arrangement.querySelector('[role=status]');
    button.disabled=true;status.textContent='正在保存教学编排…';
    try {
      const pack=await api('review-arrange',{package_id:packageId,...sent});
      const unchanged=JSON.stringify(sent)===JSON.stringify(planSnapshot());
      arrangement.dataset.revision=pack.revision;
      root.querySelector('[data-review-duration]').textContent=pack.settings.minutes;
      if(unchanged){delete arrangement.dataset.dirty;try{sessionStorage.removeItem(arrangementKey);}catch(_){};status.textContent='已保存顺序与展示设置';}
      else{planDirty();status.textContent='提交时的编排已保存，后续调整尚未保存';}
      order().forEach(id=>{const card=cards.find(x=>x.dataset.reviewItem===id);if(card)root.querySelector('.review-items').append(card);});
    } catch(error){status.textContent=error.message;}
    finally{button.disabled=false;}
  });
  arrangement.querySelector('[data-arrange-reload]').addEventListener('click',async () => {
    try{
      const pack=await api('review-status',{package_id:packageId});
      loadPlan({...pack.settings,revision:pack.revision,item_ids:pack.items.map(x=>x.id)});
      root.querySelector('[data-review-duration]').textContent=pack.settings.minutes;
      delete arrangement.dataset.dirty;try{sessionStorage.removeItem(arrangementKey);}catch(_){}
      arrangement.querySelector('[role=status]').textContent='已载入服务器保存的教学编排';
    }catch(error){arrangement.querySelector('[role=status]').textContent=error.message;}
  });
  const isDirty=()=>arrangement.dataset.dirty || [...drafts.values()].some(d=>d.form.dataset.dirty);
  const forkForm=root.querySelector('[data-review-fork]');
  let forkKey=null,forkClass=null;
  forkForm.addEventListener('submit',async event=>{
    event.preventDefault();const status=forkForm.querySelector('[role=status]'),button=forkForm.querySelector('[type=submit]');
    if(isDirty()){status.textContent='请先保存题目编辑和教学编排，再生成分班版本';return;}
    const classId=forkForm.elements.class_id.value;
    if(classId!==forkClass){forkClass=classId;forkKey=crypto.randomUUID();}
    button.disabled=true;status.textContent='正在整理本班作答与讲评…';
    try{
      const pack=await api('review-fork',{package_id:packageId,class_ids:[classId],request_key:forkKey});
      location.href=base()+'/exam-review?id='+encodeURIComponent(pack.id);
    }catch(error){status.textContent=error.message;button.disabled=false;}
  });
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
        syncItemSummary(item);
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
        syncItemSummary(item);
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
  root.querySelectorAll('[data-review-download],[data-review-open]').forEach(link => link.addEventListener('click', event => {
    if (isDirty()) {
      event.preventDefault(); message.textContent = '有尚未保存的编辑或编排，请先保存再预览、演示或下载';
      return;
    }
    if(link.hasAttribute('data-review-open'))return;
    message.textContent = '正在打包原题图片与讲评资料，请稍候…';
    setTimeout(() => { message.textContent = ''; }, 12000);
  }));
  window.addEventListener('beforeunload', event => {
    if (isDirty()) { event.preventDefault(); event.returnValue = ''; }
  });
  refresh();
})();
