(() => {
  const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  document.querySelectorAll('.atlas-module-switcher').forEach(disclosure => {
    const narrow = matchMedia('(max-width: 850px)');
    const adapt = () => { disclosure.open = !narrow.matches; };
    adapt(); narrow.addEventListener('change', adapt);
  });
  document.querySelectorAll('[data-learning-graph]').forEach((root, instance) => {
    const data = JSON.parse(root.querySelector('[data-graph-data]').textContent);
    const nodes = new Map(data.nodes.map(n => [n.id, n]));
    const ordered = values => [...values].sort((a, b) => String(a.sort || a.id).localeCompare(String(b.sort || b.id), 'zh-CN', {numeric:true}));
    const children = new Map(), adjacent = new Map(), evidence = new Map();
    nodes.forEach(n => { if (!children.has(n.parent)) children.set(n.parent, []); children.get(n.parent).push(n); });
    const edges = data.edges.filter(e => nodes.has(e.source) && nodes.has(e.target));
    edges.forEach(e => [e.source, e.target].forEach(id => { if (!adjacent.has(id)) adjacent.set(id, []); adjacent.get(id).push(e); }));
    data.evidence.forEach(e => { if (!evidence.has(e.node_id)) evidence.set(e.node_id, []); evidence.get(e.node_id).push(e); });
    evidence.forEach(items => items.sort((a, b) => (b.date || '').localeCompare(a.date || '')));
    const books = ordered(data.nodes.filter(n => n.kind === 'knowledge' && !nodes.has(n.parent)))
      .sort((a, b) => Number(!a.name.startsWith('必修')) - Number(!b.name.startsWith('必修')));
    const q = selector => root.querySelector(selector);
    const search = q('[data-graph-search]'), kind = q('[data-graph-kind]');
    const canvas = q('[data-graph-canvas]'), list = q('[data-graph-list]'), detail = q('[data-graph-detail]');
    const symmetric = e => ['related', 'confusable'].includes(e.kind);
    const type = n => n.kind === 'knowledge' && n.level === 1 ? '教材' : n.kind === 'knowledge' && n.level === 2 ? '章节' : data.kinds[n.kind];
    const ancestry = id => {
      const path = [], seen = new Set();
      while (nodes.has(id) && !seen.has(id)) { seen.add(id); path.unshift(nodes.get(id)); id = nodes.get(id).parent; }
      return path;
    };
    const bookOf = id => ancestry(id).find(n => books.some(b => b.id === n.id))?.id;
    let focus, book, listMode = false, zoom = 1, limit = 12, searchLimit = 20, model;
    function readLocation() {
      const params = new URLSearchParams(location.search);
      focus = nodes.has(params.get('focus')) ? params.get('focus') : null;
      book = bookOf(focus) || books.find(n => n.id === params.get('graph_book'))?.id || books[0]?.id || data.nodes[0]?.id;
    }
    function writeLocation() {
      const url = new URL(location.href);
      if (focus) url.searchParams.set('focus', focus); else url.searchParams.delete('focus');
      if (book) url.searchParams.set('graph_book', book);
      if (url.href !== location.href) history.pushState(null, '', url);
    }
    function nodeButton(n, extra = '') {
      return `<button type="button" class="atlas-node-link" data-node="${esc(n.id)}"><span class="atlas-kind-dot ${esc(n.kind)}" aria-hidden="true"></span><span>${esc(n.name)}${extra ? `<small>${esc(extra)}</small>` : ''}</span><span class="atlas-link-arrow" aria-hidden="true">→</span></button>`;
    }
    function updateSearch() {
      const term = search.value.trim().toLocaleLowerCase(), active = Boolean(term || kind.value);
      q('[data-graph-results]').hidden = !active; q('[data-graph-clear]').hidden = !active;
      if (!active) return;
      const matches = ordered(data.nodes.filter(n => (!kind.value || kind.value === n.kind) && (!term || `${n.name} ${n.definition}`.toLocaleLowerCase().includes(term))));
      q('[data-graph-search-status]').textContent = matches.length ? `找到 ${matches.length} 项内容，选择一项定位到图谱。` : '没有找到匹配内容。试试更短的词，或清空筛选。';
      q('[data-graph-matches]').innerHTML = matches.slice(0, searchLimit).map(n => nodeButton(n, ancestry(n.id).map(p => p.name).join(' / '))).join('') + (matches.length > searchLimit ? '<button type="button" class="secondary" data-graph-search-more>继续显示结果</button>' : '');
    }
    function clearSearch() { search.value = ''; kind.value = ''; searchLimit = 20; updateSearch(); }
    function showNode(id) {
      const n = nodes.get(id); if (!n) return;
      const own = evidence.get(id) || [], links = adjacent.get(id) || [], groups = new Map();
      links.forEach(e => { if (!groups.has(e.kind)) groups.set(e.kind, []); groups.get(e.kind).push(e); });
      const chapter = ancestry(id).find(p => p.kind === 'knowledge' && p.level === 2);
      const params = new URLSearchParams(location.search);
      params.set('module', 'graph'); params.set('focus', id);
      if (chapter) params.set('region', chapter.id); else params.delete('region');
      const evidenceUrl = chapter ? `app?${params.toString()}#region-detail` : '#learning-evidence';
      detail.innerHTML = `<div class="atlas-detail-top"><span class="atlas-kind-label ${esc(n.kind)}">${esc(type(n))}</span><button type="button" class="secondary atlas-detail-jump" data-graph-map-jump>回到图谱</button></div><h3>${esc(n.name)}</h3><p>${esc(n.definition === n.name ? '沿着相连的内容继续探索，点击连线可以查看关系依据。' : n.definition)}</p><div class="atlas-node-facts"><span><strong>${(children.get(id) || []).length}</strong> 项下级内容</span><span><strong>${links.length}</strong> 条直接关系</span></div>${data.audience === 'teacher' ? '<a class="atlas-evidence-link" href="teacher?module=regions">查看班级学习地图 <span aria-hidden="true">→</span></a>' : `<a class="atlas-evidence-link" data-graph-evidence href="${esc(evidenceUrl)}">${chapter ? '查看本区域学习证据' : '查看我的学习证据'} <span aria-hidden="true">→</span></a>`}<h4>相连的内容</h4>${groups.size ? [...groups].map(([relation, items]) => `<details class="atlas-relations"${groups.size === 1 && items.length < 7 ? ' open' : ''}><summary>${esc(data.relations[relation])}<span>${items.length}</span></summary>${items.map(e => {
        const other = nodes.get(e.source === id ? e.target : e.source);
        const direction = symmetric(e) ? '双向关系' : e.source === id ? '从当前内容指向' : '指向当前内容';
        return `<div class="atlas-relation-row">${nodeButton(other, direction)}<button type="button" class="atlas-source-link" data-edge="${esc(e.id)}">${e.status === 'curated' ? '待确认 · ' : ''}查看依据</button></div>`;
      }).join('')}</details>`).join('') : '<p class="atlas-empty">还没有整理关联关系，可通过教材目录继续查找。</p>'}${data.audience === 'student' ? `<details class="atlas-observations"><summary>我的诊断观察 <span>${own.length}</span></summary>${own.length ? own.map(e => `<article><strong>${esc(e.status)}</strong><p>${esc(e.label)}</p>${e.objective_name ? `<p>诊断时目标：${esc(e.objective_name)}</p>` : ''}<a href="${esc(e.url)}">查看本题证据</a></article>`).join('') : '<p>尚未观察。相关错题并不意味着这个节点整体薄弱。</p>'}</details>` : ''}`;
    }
    function showEdge(id) {
      const e = edges.find(item => item.id === id); if (!e) return;
      root.querySelectorAll('[data-edge]').forEach(el => el.classList.toggle('is-selected', el.dataset.edge === id));
      detail.innerHTML = `<div class="atlas-detail-top"><span class="atlas-kind-label">关系依据</span><button type="button" class="secondary" data-graph-return-node>返回节点</button></div><h3>${esc(data.relations[e.kind])}</h3><p class="atlas-edge-direction">${esc(nodes.get(e.source).name)} <span>${symmetric(e) ? '↔' : '→'}</span> ${esc(nodes.get(e.target).name)}</p><p class="atlas-relation-status${e.status === 'curated' ? ' is-pending' : ''}">${esc(data.statuses[e.status])}</p><h4>为什么相连</h4><p>${esc(e.reason)}</p><h4>适用条件</h4><p>${esc(e.conditions || '按来源任务理解')}</p><h4>依据来源</h4><p>${esc(e.locator)}</p>${nodeButton(nodes.get(e.source), '查看起点')}${nodeButton(nodes.get(e.target), '查看终点')}`;
    }
    function graphModel() {
      const center = focus || book, all = adjacent.get(center) || [];
      const incoming = ordered([...new Set(all.filter(e => e.target === center && !symmetric(e)).map(e => e.source))].map(id => nodes.get(id)));
      const outgoing = ordered([...new Set(all.filter(e => e.source === center || symmetric(e)).map(e => e.source === center ? e.target : e.source))].filter(id => id !== center).map(id => nodes.get(id)));
      const left = focus ? incoming.filter(n => !outgoing.some(o => o.id === n.id)) : [];
      const right = focus ? outgoing : ordered(children.get(center) || []);
      const neighbors = [...left, ...right], visible = new Set(neighbors.slice(0, focus ? limit : neighbors.length).map(n => n.id));
      return {center, left:left.filter(n => visible.has(n.id)), right:right.filter(n => visible.has(n.id)), neighbors, visible};
    }
    function draw() {
      if (!model || !nodes.has(model.center)) return;
      const measured = Math.round(canvas.clientWidth) || 740, narrow = measured < 700;
      const width = Math.max(260, measured), box = narrow ? Math.min(242, width - 42) : 196;
      const visible = [...model.left, ...model.right], row = 104, height = narrow ? Math.max(330, (visible.length + 1) * 112 + 32) : Math.max(380, Math.max(model.left.length, model.right.length) * row + 48);
      const positions = new Map();
      positions.set(model.center, {x:narrow ? width / 2 : focus ? width / 2 : width * 0.24, y:narrow ? 64 : height / 2});
      if (narrow) visible.forEach((n, i) => positions.set(n.id, {x:width / 2, y:176 + i * 112}));
      else [model.left, model.right].forEach((items, side) => items.forEach((n, i) => positions.set(n.id, {x:side ? (focus ? width - 116 : width * 0.74) : 116, y:(height - items.length * row) / 2 + row / 2 + i * row})));
      const lines = edges.filter(e => (e.source === model.center || e.target === model.center) && e.source !== e.target && positions.has(e.source) && positions.has(e.target) && (focus || e.kind === 'contains'));
      const marker = `atlas-arrow-${instance}`;
      const svgLines = lines.map(e => {
        const a = positions.get(e.source), b = positions.get(e.target);
        let path;
        if (narrow) {
          const side = e.target === model.center ? -1 : 1, x = a.x + side * box / 2, bend = side < 0 ? 8 : width - 8;
          path = `M${x} ${a.y} C${bend} ${a.y},${bend} ${b.y},${x} ${b.y}`;
        } else {
          const side = b.x > a.x ? 1 : -1, x1 = a.x + side * box / 2, x2 = b.x - side * box / 2, middle = (x1 + x2) / 2;
          path = `M${x1} ${a.y} C${middle} ${a.y},${middle} ${b.y},${x2} ${b.y}`;
        }
        return `<g class="atlas-edge ${e.status === 'curated' ? 'candidate' : ''}" role="button" tabindex="0" data-edge="${esc(e.id)}" aria-label="${esc(`${data.relations[e.kind]}：${nodes.get(e.source).name}${symmetric(e) ? '与' : '指向'}${nodes.get(e.target).name}`)}"><title>${esc(data.relations[e.kind])}</title><path class="atlas-edge-hit" d="${path}"/><path class="atlas-edge-line" d="${path}"${e.kind === 'contains' ? '' : ` marker-end="url(#${marker})"${symmetric(e) ? ` marker-start="url(#${marker})"` : ''}`}/></g>`;
      }).join('');
      const svgNodes = [...positions].map(([id, p]) => {
        const n = nodes.get(id), chars = [...n.name], lineLength = Math.floor((box - 28) / 16);
        const first = chars.slice(0, lineLength).join(''), second = chars.slice(lineLength, lineLength * 2).join('') + (chars.length > lineLength * 2 ? '…' : '');
        const own = evidence.get(id) || [], childCount = (children.get(id) || []).length;
        const subtitle = own[0]?.status || (childCount ? `${type(n)} · ${childCount} 项下级内容` : type(n));
        const symbol = n.kind === 'ability' ? '<circle r="4"/>' : n.kind === 'literacy' ? '<path d="M0 -5L5 0L0 5L-5 0Z"/>' : `<rect x="-4" y="-4" width="8" height="8" rx="${n.kind === 'objective' ? 3 : 0}"/>`;
        return `<g class="atlas-node ${id === model.center ? 'selected' : ''} ${own[0]?.status === '本步待检查' ? 'needs-check' : ''}" transform="translate(${p.x} ${p.y})" role="button" tabindex="0" data-node="${esc(id)}" aria-label="${esc(`${type(n)}：${n.name}，${subtitle}`)}" aria-pressed="${id === model.center}"><title>${esc(n.name)}</title><rect class="atlas-node-box" x="${-box / 2}" y="-42" width="${box}" height="84" rx="8"/><g class="atlas-node-symbol ${esc(n.kind)}" transform="translate(${-box / 2 + 17} -27)">${symbol}</g><text text-anchor="middle" y="${second ? -8 : -3}" class="atlas-node-name">${esc(first)}</text>${second ? `<text text-anchor="middle" y="12" class="atlas-node-name">${esc(second)}</text>` : ''}<text text-anchor="middle" y="31" class="atlas-node-meta">${esc(subtitle)}</text></g>`;
      }).join('');
      canvas.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width * zoom}" height="${height * zoom}" style="width:${width * zoom}px;height:${height * zoom}px" aria-label="${esc(nodes.get(model.center).name)}的${focus ? '关系' : '章节'}图"><defs><marker id="${marker}" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto-start-reverse"><path d="M0 0L7 3.5L0 7Z" fill="currentColor"/></marker></defs>${svgLines}${svgNodes}</svg>`;
      q('[data-graph-zoom="reset"]').textContent = Math.round(zoom * 100) + '%';
      q('[data-graph-zoom="out"]').disabled = zoom <= 0.9;
      q('[data-graph-zoom="in"]').disabled = zoom >= 1.8;
    }
    function render() {
      q('[data-graph-books]').innerHTML = books.map(n => `<button type="button" data-book="${esc(n.id)}" aria-pressed="${book === n.id}">${esc(n.name)}</button>`).join('');
      q('[data-graph-book-select]').innerHTML = books.map(n => `<option value="${esc(n.id)}"${book === n.id ? ' selected' : ''}>${esc(n.name)}</option>`).join('');
      root.dataset.overview = String(!focus);
      model = graphModel();
      if (!nodes.has(model.center)) {
        canvas.innerHTML = '<p class="atlas-empty">还没有可用的知识内容。教材体系准备好后，会在这里显示地图。</p>';
        detail.innerHTML = '<h3>等待教材内容</h3><p>可先查看已有题目和学习记录。</p>';
        q('[data-graph-count]').textContent = '暂无可用节点'; return;
      }
      const current = nodes.get(model.center), path = ancestry(model.center);
      q('[data-graph-breadcrumb]').innerHTML = path.map((n, i) => i === path.length - 1 ? `<span aria-current="location">${esc(n.name)}</span>` : `<button type="button" data-node="${esc(n.id)}">${esc(n.name)}</button><span aria-hidden="true">/</span>`).join('');
      q('[data-graph-home]').disabled = !focus;
      q('[data-graph-map-title]').textContent = focus ? '以所选内容为中心 · 点击连线查看依据' : '教材 → 章节 · 点击节点逐层展开';
      canvas.hidden = listMode; list.hidden = !listMode;
      q('[data-graph-mode]').textContent = listMode ? '图谱视图' : '列表视图';
      q('[data-graph-mode]').setAttribute('aria-pressed', String(listMode));
      q('.atlas-zoom').hidden = listMode;
      list.innerHTML = `<h3>${esc(current.name)}</h3>${model.neighbors.length ? `<ul>${model.neighbors.map(n => `<li>${nodeButton(n, type(n))}</li>`).join('')}</ul>` : '<p class="atlas-empty">暂无下级内容或相邻节点，可以从其他教材或搜索继续浏览。</p>'}`;
      q('[data-graph-more]').hidden = !focus || listMode || model.visible.size >= model.neighbors.length;
      q('[data-graph-count]').textContent = focus ? `显示 ${listMode ? model.neighbors.length : model.visible.size} / ${model.neighbors.length} 项相邻内容 · 全图 ${nodes.size} 个节点可搜索` : `本册 ${model.neighbors.length} 个章节 · 按教材顺序排列`;
      if (!listMode) draw();
      showNode(model.center);
    }
    function navigate(id, overview = false, keyboard = false) {
      if (!nodes.has(id)) return;
      focus = overview ? null : id; book = bookOf(id) || book; limit = 12; zoom = 1;
      clearSearch(); writeLocation(); render();
      canvas.scrollTop = 0;
      canvas.scrollLeft = 0;
      if (keyboard) focusSelection();
    }
    function focusSelection() {
      const selected = listMode ? detail : root.querySelector(`.atlas-node[data-node="${CSS.escape(model.center)}"]`);
      selected?.focus({preventScroll:true});
      selected?.scrollIntoView({block:'nearest', inline:'nearest'});
    }
    function jump(element) { element.scrollIntoView({block:'start', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'}); element.focus({preventScroll:true}); }
    root.addEventListener('click', event => {
      const target = event.target.closest('button,[data-node],[data-edge],a'); if (!target) return;
      if (target.hasAttribute('data-node')) navigate(target.dataset.node, false, event.detail === 0);
      else if (target.hasAttribute('data-book')) navigate(target.dataset.book, true, event.detail === 0);
      else if (target.hasAttribute('data-edge')) { showEdge(target.dataset.edge); if (event.detail === 0 && !target.isConnected) detail.focus(); }
      else if (target.hasAttribute('data-graph-return-node')) { showNode(focus || book); if (event.detail === 0) detail.focus(); }
      else if (target.hasAttribute('data-graph-home')) navigate(book, true, event.detail === 0);
      else if (target.hasAttribute('data-graph-clear')) { clearSearch(); search.focus(); }
      else if (target.hasAttribute('data-graph-search-more')) { const next = searchLimit; searchLimit += 20; updateSearch(); if (event.detail === 0) q('[data-graph-matches]').querySelectorAll('[data-node]')[next]?.focus(); }
      else if (target.hasAttribute('data-graph-mode')) { listMode = !listMode; render(); }
      else if (target.hasAttribute('data-graph-more')) { limit += 12; render(); if (event.detail === 0 && target.hidden) focusSelection(); }
      else if (target.hasAttribute('data-graph-detail-jump')) jump(detail);
      else if (target.hasAttribute('data-graph-map-jump')) jump(canvas);
      else if (target.hasAttribute('data-graph-evidence') && target.getAttribute('href').startsWith('#')) { const section = document.getElementById('learning-evidence'); if (section) section.open = true; }
      else if (target.hasAttribute('data-graph-zoom')) { zoom = target.dataset.graphZoom === 'reset' ? 1 : Math.max(0.9, Math.min(1.8, Math.round((zoom + (target.dataset.graphZoom === 'in' ? 0.15 : -0.15)) * 100) / 100)); draw(); }
    });
    root.addEventListener('keydown', event => {
      if (['Enter', ' '].includes(event.key) && event.target.matches('svg [role="button"]')) { event.preventDefault(); event.target.dispatchEvent(new MouseEvent('click', {bubbles:true, detail:0})); }
      if (event.key === 'Escape' && event.target === search) { clearSearch(); }
    });
    search.addEventListener('input', () => { searchLimit = 20; updateSearch(); });
    kind.addEventListener('change', () => { searchLimit = 20; updateSearch(); });
    q('[data-graph-book-select]').addEventListener('change', event => navigate(event.target.value, true));
    window.addEventListener('popstate', () => { readLocation(); clearSearch(); render(); });
    let previousWidth = 0;
    new ResizeObserver(entries => { const width = Math.round(entries[0].contentRect.width); if (width > 0 && width !== previousWidth) { previousWidth = width; if (!listMode) draw(); } }).observe(canvas);
    readLocation(); render();
  });
  document.querySelectorAll('[data-graph-edges]').forEach(form=>{
    const search=form.querySelector('[data-graph-edge-search]'),status=form.querySelector('[data-graph-edge-status]'),semantic=form.querySelector('[data-graph-semantic-only]'),rows=[...form.querySelectorAll('[data-graph-edge-row]')];
    function filter(){let visible=0;rows.forEach(row=>{const matches=(!search.value.trim()||row.textContent.includes(search.value.trim()))&&(!status.value||row.dataset.status===status.value)&&(!semantic.checked||!['contains','uses_ability','supports_literacy'].includes(row.dataset.kind));row.hidden=!matches;if(matches)visible++;else row.querySelector('[name=ids]').checked=false;});form.querySelector('[data-graph-filter-count]').textContent='当前显示 '+visible+' 条关系；目标归属、能力和素养联系也可在诊断卡中逐项确认。';}
    search.addEventListener('input',filter);status.addEventListener('change',filter);semantic.addEventListener('change',filter);filter();
  });
  const message=document.querySelector('[data-graph-review-message]');
  document.querySelectorAll('[data-shared-goal] select[name="goal_id"]').forEach(select=>select.addEventListener('change',()=>{
    const definition=select.selectedOptions[0].dataset.definition;
    const goal=definition?JSON.parse(definition):null;
    select.closest('form').querySelector('[data-goal-preview]').textContent=goal?goal.definition+' · 适用条件：'+goal.conditions:'请填写新目标的名称、可观察表现和适用条件。';
  }));
  document.querySelectorAll('[data-graph-card],[data-graph-edge],[data-graph-edges],[data-shared-goal]').forEach(form=>form.addEventListener('submit',async e=>{
    e.preventDefault();const fd=new FormData(form),version=Number(form.dataset.version);let action,payload;
    if(form.matches('[data-shared-goal]')){
      action='graph-goal-save';payload={version,card_id:form.dataset.cardId,action_index:Number(form.dataset.index),...Object.fromEntries(fd)};
    }else if(form.matches('[data-graph-card]')){
      action='graph-card-save';const steps=[],mappings=[];
      form.querySelectorAll('[data-graph-step]').forEach(part=>{const value=k=>part.querySelector(`[name="${k}"]`).value;steps.push({stage:value('stage'),prompt:value('prompt'),explanation:value('explanation'),correct:Number(value('correct')),options:[...part.querySelectorAll('[name="option"]')].map(x=>x.value),hints:[...part.querySelectorAll('[name="hint"]')].map(x=>x.value)});mappings.push(Object.fromEntries(['name','definition','knowledge_id','ability_id','literacy_id'].map(k=>[k,value(k)])));});
      payload={version,card_id:form.dataset.cardId,card:{title:fd.get('title'),steps},mappings};
    }else if(form.matches('[data-graph-edge]')){action='graph-edge-save';payload={version,...Object.fromEntries(fd)};}else{action='graph-edge-review';payload={version,ids:fd.getAll('ids'),status:e.submitter.value};}
    const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);message.textContent='正在保存并发布…';
    try{const r=await fetch('api/learning/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error?.message||'保存失败，请重试');message.textContent='已发布第 '+d.result.version+' 版';location.reload();}catch(error){message.textContent=error.message;buttons.forEach(b=>b.disabled=false);message.scrollIntoView({block:'center'});}
  }));
})();
