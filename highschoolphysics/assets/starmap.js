(() => {
  const COLORS = {weak:'#ff5d6c', shaky:'#ffb547', solid:'#3ddc97', none:'#56627f'};
  const LABEL = {weak:'薄弱', shaky:'待巩固', solid:'掌握较好', none:'题目不足'};
  const EDGE = {tree:'#8fb0ff', cotest:'#7fd3ff', ability:'#c59bff'};
  const FONT = 'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct = x => x == null || Number.isNaN(x) ? '—' : Math.round(x * 100) + '%';
  const rgba = (c, a) => { const n = parseInt(c.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  document.querySelectorAll('[data-starmap-autosubmit]').forEach(select => select.addEventListener('change', () => {
    if (select.name === 'class_id') { const s = select.form.querySelector('[name="student_id"]'); if (s) s.value = ''; }
    select.form.requestSubmit();
  }));

  document.querySelectorAll('[data-starmap]').forEach(root => {
    const DATA = JSON.parse(root.querySelector('[data-starmap-data]').textContent);
    const q = s => root.querySelector(s);
    const classMode = DATA.mode === 'class';

    // ---------- graph model ----------
    const nodes = DATA.nodes.map(n => ({...n, children:[]}));
    const byId = new Map(nodes.map(n => [n.id, n]));
    nodes.forEach(n => { if (n.parent && byId.has(n.parent) && n.parent !== n.id) byId.get(n.parent).children.push(n); });
    const sortKey = n => String(n.sort || n.id);
    const bySort = (a, b) => sortKey(a).localeCompare(sortKey(b), 'zh', {numeric:true});
    nodes.forEach(n => n.children.sort(bySort));
    const books = nodes.filter(n => n.kind === 'knowledge' && !(n.parent && byId.has(n.parent)))
      .sort((a, b) => (a.name.startsWith('必修') ? 0 : 1) - (b.name.startsWith('必修') ? 0 : 1) || bySort(a, b));
    const abilities = nodes.filter(n => n.kind === 'ability').sort(bySort);
    const ancestors = id => { const out = [], seen = new Set(); let n = byId.get(id); while (n && !seen.has(n.id)) { seen.add(n.id); out.push(n.id); n = byId.get(n.parent); } return out; };
    const depth = n => ancestors(n.id).length;           // 1 = book, 2 = chapter, 3 = section …
    nodes.forEach(n => { n.depth = n.kind === 'ability' ? 0 : depth(n); });
    const typeName = n => n.kind === 'ability' ? '能力' : ['', '教材', '章节', '知识点'][n.depth] || '知识点';

    const edges = [];
    nodes.forEach(n => { if (n.kind === 'knowledge' && n.parent && byId.has(n.parent)) edges.push({s:n.parent, t:n.id, kind:'tree', w:1}); });
    DATA.edges.forEach(e => { if (byId.has(e.s) && byId.has(e.t) && e.s !== e.t) edges.push(e); });
    const adj = new Map(nodes.map(n => [n.id, []]));
    edges.forEach(e => { adj.get(e.s).push(e); adj.get(e.t).push(e); });

    // ---------- layout: books = galaxies, chapters = constellations, abilities = central ring ----------
    const R_BOOK = Math.max(640, books.length * 110), R_CH = 175, BOOK_LABEL = R_CH + 105;
    const placeChildren = (p, base, radius) => {
      const k = p.children.length;
      p.children.forEach((s, t) => {
        const a = base - Math.PI * 0.8 + (k === 1 ? Math.PI * 0.8 : t * Math.PI * 1.6 / (k - 1));
        s.x = p.x + Math.cos(a) * radius; s.y = p.y + Math.sin(a) * radius;
        if (s.children.length) placeChildren(s, a, 22 + s.children.length * 3);
      });
    };
    books.forEach((b, i) => {
      const a = -Math.PI / 2 + i * Math.PI * 2 / Math.max(1, books.length);
      b.x = Math.cos(a) * R_BOOK; b.y = Math.sin(a) * R_BOOK; b.angle = a;
      const m = b.children.length;
      b.children.forEach((c, j) => {
        const ca = a + Math.PI + (j + 0.5) * Math.PI * 2 / m, r = R_CH * (m <= 3 ? 0.75 : 1);
        c.x = b.x + Math.cos(ca) * r; c.y = b.y + Math.sin(ca) * r;
        placeChildren(c, ca, 34 + c.children.length * 5);
      });
    });
    abilities.forEach(a => {
      let x = 0, y = 0;
      adj.get(a.id).forEach(e => { const o = byId.get(e.s === a.id ? e.t : e.s); if (o.kind === 'knowledge' && o.x != null) { x += o.x * e.w; y += o.y * e.w; } });
      a.pref = x || y ? Math.atan2(y, x) : 0;
    });
    abilities.sort((a, b) => a.pref - b.pref).forEach((a, i) => {
      const ang = -Math.PI / 2 + i * Math.PI * 2 / abilities.length, r = 250 + (i % 2) * 80;
      a.x = Math.cos(ang) * r; a.y = Math.sin(ang) * r;
    });
    nodes.forEach((n, i) => { if (n.x == null) { n.x = Math.cos(i) * 120; n.y = Math.sin(i) * 120; } });
    for (let it = 0; it < 40; it++) {
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const p = nodes[i], o = nodes[j], dx = o.x - p.x, dy = o.y - p.y, d = Math.hypot(dx, dy) || 0.01;
        if (d < 26) { const f = (26 - d) / 2 / d; p.x -= dx * f; p.y -= dy * f; o.x += dx * f; o.y += dy * f; }
      }
    }

    // ---------- evidence → mastery ----------
    let mode = classMode ? 'absolute' : 'relative';
    const layers = {tree:true, cotest:true, ability:true};
    let stats = new Map();
    function compute() {
      stats = new Map();
      const stat = id => { if (!stats.has(id)) stats.set(id, {n:0, c:0, exp:0, qs:0, items:[]}); return stats.get(id); };
      DATA.records.forEach(r => {
        const item = DATA.questions[r[0]]; if (!item) return;
        const correct = classMode ? r[1] : r[1] === 'c' ? 1 : 0, attempts = classMode ? r[2] : 1;
        if (!attempts) return;
        const targets = new Set();
        item.k.forEach(k => ancestors(k).forEach(x => targets.add(x)));
        item.a.forEach(a => targets.add(a));
        targets.forEach(id => { const s = stat(id); s.n += attempts; s.c += correct; s.exp += (item.cr ?? 0.5) * attempts; s.qs += 1; s.items.push({key:r[0], outcome:classMode ? correct / attempts : r[1]}); });
      });
      stats.forEach(s => {
        s.rate = s.c / s.n; s.classRate = s.exp / s.n; s.delta = s.rate - s.classRate;
        const relative = mode === 'relative' && !classMode;
        s.level = s.qs < 2 ? 'none' : relative ? (s.delta < -0.15 ? 'weak' : s.delta < -0.05 ? 'shaky' : 'solid')
                                               : (s.rate < 0.45 ? 'weak' : s.rate < 0.7 ? 'shaky' : 'solid');
        s.score = relative ? s.delta : s.rate - 0.6;
      });
    }
    const levelOf = id => stats.get(id)?.level || 'none';

    // ---------- canvas & camera ----------
    const stage = q('[data-starmap-stage]'), cv = q('[data-starmap-canvas]'), ctx = cv.getContext('2d'), tip = q('[data-starmap-tip]');
    let W = 0, H = 0, dpr = 1, cam = {x:0, y:0, k:0.5}, target = null, selected = null, hover = null, dirty = true;
    const bg = Array.from({length:380}, (_, i) => ({x:Math.abs(Math.sin(i * 12.9898) * 43758.5453 % 1), y:Math.abs(Math.sin(i * 78.233) * 12345.678 % 1), r:i % 7 ? 0.6 : 1.3, p:i}));
    function resize() {
      dpr = window.devicePixelRatio || 1; W = stage.clientWidth; H = stage.clientHeight;
      cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + 'px'; cv.style.height = H + 'px'; dirty = true;
    }
    const toScreen = (x, y) => [(x - cam.x) * cam.k + W / 2, (y - cam.y) * cam.k + H / 2];
    const toWorld = (sx, sy) => [(sx - W / 2) / cam.k + cam.x, (sy - H / 2) / cam.k + cam.y];
    function fitAll() {
      const pts = [...nodes, ...books.map(b => ({x:b.x + Math.cos(b.angle) * (BOOK_LABEL + 120 * Math.abs(Math.cos(b.angle))), y:b.y + Math.sin(b.angle) * (BOOK_LABEL + 20)}))];
      const xs = pts.map(n => n.x), ys = pts.map(n => n.y);
      const w = Math.max(...xs) - Math.min(...xs) + 60, h = Math.max(...ys) - Math.min(...ys) + 60;
      return {x:(Math.max(...xs) + Math.min(...xs)) / 2, y:(Math.max(...ys) + Math.min(...ys)) / 2, k:Math.max(0.12, Math.min(W / w, H / h))};
    }
    const clampK = k => Math.max(0.12, Math.min(4, k));
    function flyTo(x, y, k) { target = {x, y, k:clampK(k)}; if (reduced) { cam = {...target}; target = null; } dirty = true; }
    function focusSet() {
      if (!selected) return null;
      const set = new Set([selected]);
      adj.get(selected).forEach(e => { set.add(e.s); set.add(e.t); });
      return set;
    }
    function radius(n) {
      const s = stats.get(n.id);
      if (n.kind === 'ability') return 7 + Math.min(5, Math.sqrt(s?.qs || 0) * 0.6);
      if (n.depth === 1) return 9;
      if (n.depth === 2) return 5.5;
      return s ? 2.6 + Math.min(4.5, Math.sqrt(s.qs) * 1.1) : 2;
    }
    function star(x, y, r, color) {
      ctx.beginPath();
      for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4 - Math.PI / 2, rr = i % 2 ? r * 0.42 : r; ctx.lineTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr); }
      ctx.closePath(); ctx.fillStyle = color; ctx.fill();
    }
    function glow(x, y, r, color, alpha) {
      if (r <= 0 || alpha <= 0) return;
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, rgba(color, alpha)); g.addColorStop(1, rgba(color, 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    }
    const t0 = performance.now();
    function frame(now) {
      requestAnimationFrame(frame);
      if (!W || !H) return;
      if (target) {
        cam.x += (target.x - cam.x) * 0.14; cam.y += (target.y - cam.y) * 0.14; cam.k *= Math.pow(target.k / cam.k, 0.14);
        if (Math.abs(target.x - cam.x) < 0.5 && Math.abs(target.y - cam.y) < 0.5 && Math.abs(target.k / cam.k - 1) < 0.002) { cam = {...target}; target = null; }
        dirty = true;
      }
      if (!dirty && reduced) return;
      dirty = false;
      const t = reduced ? 0 : (now - t0) / 1000;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
      bg.forEach(s => {
        const x = ((s.x * W * 1.4 - cam.x * cam.k * 0.05) % W + W) % W, y = ((s.y * H * 1.4 - cam.y * cam.k * 0.05) % H + H) % H;
        ctx.fillStyle = `rgba(200,210,255,${0.25 + 0.2 * Math.sin(t * 1.3 + s.p)})`; ctx.fillRect(x, y, s.r, s.r);
      });
      const fs = focusSet(), k = cam.k;
      ctx.globalCompositeOperation = 'lighter';
      nodes.forEach(n => {
        if (n.kind !== 'knowledge' || n.depth !== 2) return;
        const pts = [n, ...n.children], cx = pts.reduce((a, p) => a + p.x, 0) / pts.length, cy = pts.reduce((a, p) => a + p.y, 0) / pts.length;
        const lv = levelOf(n.id), [sx, sy] = toScreen(cx, cy), r = (58 + n.children.length * 7) * k;
        const a = {weak:0.34, shaky:0.22, solid:0.13, none:0.05}[lv] + (lv === 'weak' ? 0.06 * Math.sin(t * 2.2) : 0);
        glow(sx, sy, r, COLORS[lv], fs && !fs.has(n.id) ? a * 0.35 : a);
      });
      ctx.globalCompositeOperation = 'source-over';
      edges.forEach(e => {
        if (!layers[e.kind]) return;
        const a = byId.get(e.s), b = byId.get(e.t), [x1, y1] = toScreen(a.x, a.y), [x2, y2] = toScreen(b.x, b.y);
        const lit = fs && (e.s === selected || e.t === selected);
        let alpha = e.kind === 'tree' ? 0.28 : e.kind === 'cotest' ? Math.min(0.5, 0.1 + 0.07 * e.w) : Math.min(0.22, 0.03 + 0.02 * e.w);
        if (fs) alpha = lit ? Math.min(0.95, alpha * 3 + 0.25) : alpha * 0.12;
        if (alpha < 0.01) return;
        ctx.strokeStyle = rgba(EDGE[e.kind], alpha); ctx.lineWidth = (e.kind === 'tree' ? 0.9 : Math.min(3, 0.5 + 0.25 * e.w)) * (lit ? 1.6 : 1);
        ctx.beginPath(); ctx.moveTo(x1, y1);
        if (e.kind === 'tree') ctx.lineTo(x2, y2);
        else { const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, bend = e.kind === 'cotest' ? 0.22 : 0.08; ctx.quadraticCurveTo(mx - (y2 - y1) * bend, my + (x2 - x1) * bend, x2, y2); }
        ctx.stroke();
      });
      const grow = Math.max(0.7, Math.min(1.8, Math.sqrt(k * 1.6)));
      nodes.forEach(n => {
        const [x, y] = toScreen(n.x, n.y); if (x < -40 || y < -40 || x > W + 40 || y > H + 40) return;
        const lv = levelOf(n.id), col = COLORS[lv], r = radius(n) * grow;
        ctx.globalAlpha = fs && !fs.has(n.id) ? 0.2 : 1;
        if (lv !== 'none') glow(x, y, r * (lv === 'weak' ? 4.2 + 0.6 * Math.sin(t * 3 + n.x) : 3.2), col, lv === 'weak' ? 0.55 : 0.35);
        if (n.kind === 'ability') star(x, y, r * 1.5, col);
        else if (n.depth <= 2) { ctx.strokeStyle = col; ctx.lineWidth = n.depth === 1 ? 2.4 : 1.6; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke(); ctx.fillStyle = rgba(col, 0.35); ctx.fill(); }
        else {
          ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
          if (lv !== 'none') { ctx.fillStyle = 'rgba(255,255,255,.75)'; ctx.beginPath(); ctx.arc(x, y, r * 0.35, 0, Math.PI * 2); ctx.fill(); }
        }
        if (n.id === selected || n.id === hover) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, r + 5, 0, Math.PI * 2); ctx.stroke(); }
        ctx.globalAlpha = 1;
      });
      // labels in priority order; a label that would overlap one already drawn is skipped
      ctx.textBaseline = 'top';
      const placed = [], prio = n => n.id === selected || n.id === hover ? 0 : n.depth === 1 ? 1 : n.kind === 'ability' ? 2 : levelOf(n.id) === 'weak' ? 3 : n.depth === 2 ? 4 : 5;
      [...nodes].sort((a, b) => prio(a) - prio(b)).forEach(n => {
        const lv = levelOf(n.id), inFocus = fs && fs.has(n.id);
        let show = n.kind === 'ability' || n.depth === 1 || (n.depth === 2 && k > 0.42) || (n.depth >= 3 && (k > 1.15 || (k > 0.75 && lv === 'weak'))) || inFocus || n.id === hover;
        if (fs && !inFocus && n.depth !== 1) show = false;
        if (!show) return;
        const [x, y] = toScreen(n.x, n.y); if (x < -200 || y < -200 || x > W + 200 || y > H + 200) return;
        const size = n.depth === 1 ? 16 : n.kind === 'ability' ? 13 : n.depth === 2 ? 12.5 : 12;
        ctx.font = `${n.depth === 1 || n.kind === 'ability' ? 600 : 400} ${size}px ${FONT}`;
        const label = n.depth === 1 || n.name.length <= 12 ? n.name : n.name.slice(0, 12) + '…';
        let lx = x, ly = y + radius(n) * 1.6 + 6, align = 'center';
        if (n.depth === 1 && n.angle != null) { const d = BOOK_LABEL * k, c = Math.cos(n.angle); lx = x + c * d; ly = y + Math.sin(n.angle) * d - 8; align = c > 0.3 ? 'left' : c < -0.3 ? 'right' : 'center'; }
        const w = ctx.measureText(label).width, left = align === 'left' ? lx : align === 'right' ? lx - w : lx - w / 2;
        const box = [left - 3, ly - 2, left + w + 3, ly + size + 3];
        if (prio(n) > 0 && placed.some(b => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) return;
        placed.push(box); ctx.textAlign = align;
        ctx.lineWidth = 3.5; ctx.strokeStyle = 'rgba(5,8,20,.85)'; ctx.strokeText(label, lx, ly);
        ctx.fillStyle = n.depth === 1 ? '#e9edff' : n.kind === 'ability' ? '#e6d9ff' : lv === 'weak' ? '#ffd0d5' : '#cfd8f7';
        ctx.globalAlpha = fs && !inFocus ? 0.35 : 1; ctx.fillText(label, lx, ly); ctx.globalAlpha = 1;
      });
    }

    // ---------- pointer interaction ----------
    function pick(sx, sy) {
      let best = null, bd = 18;
      nodes.forEach(n => { const [x, y] = toScreen(n.x, n.y), d = Math.hypot(x - sx, y - sy) - radius(n); if (d < bd) { bd = d; best = n; } });
      return best;
    }
    const pointers = new Map(); let drag = null, moved = false, pinch = null;
    const point = e => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    cv.addEventListener('pointerdown', e => {
      cv.setPointerCapture(e.pointerId); const [x, y] = point(e); pointers.set(e.pointerId, [x, y]); moved = false; target = null;
      if (pointers.size === 1) drag = {x, y, cx:cam.x, cy:cam.y};
      if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = {d:Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, k:cam.k}; drag = null; }
      stage.classList.add('is-dragging');
    });
    cv.addEventListener('pointermove', e => {
      const [px, py] = point(e);
      if (pointers.has(e.pointerId)) pointers.set(e.pointerId, [px, py]);
      if (pinch && pointers.size === 2) { const [a, b] = [...pointers.values()]; cam.k = clampK(pinch.k * Math.hypot(a[0] - b[0], a[1] - b[1]) / pinch.d); moved = true; dirty = true; return; }
      if (drag) { const dx = px - drag.x, dy = py - drag.y; if (Math.hypot(dx, dy) > 4) moved = true; cam.x = drag.cx - dx / cam.k; cam.y = drag.cy - dy / cam.k; tip.hidden = true; dirty = true; return; }
      const n = pick(px, py), id = n ? n.id : null;
      if (id !== hover) { hover = id; dirty = true; }
      if (n) {
        const s = stats.get(n.id);
        tip.innerHTML = `<b>${esc(n.name)}</b> · ${typeName(n)}<br>` + (s ? `答对 ${Math.round(s.c)} / ${Math.round(s.n)}（${pct(s.rate)}）` + (classMode ? '' : ` · 同班同题 ${pct(s.classRate)}`) + `<br><span style="color:${COLORS[s.level]}">● ${LABEL[s.level]}</span> · 涉及 ${s.qs} 题` : '还没有相关题目');
        tip.style.left = Math.min(W - 270, px + 14) + 'px'; tip.style.top = Math.min(H - 80, py + 14) + 'px'; tip.hidden = false;
      } else tip.hidden = true;
    });
    const up = e => {
      pointers.delete(e.pointerId); stage.classList.remove('is-dragging');
      if (pointers.size < 2) pinch = null;
      if (drag && !moved) { const [x, y] = point(e), n = pick(x, y); select(n ? n.id : null, false); }
      drag = null;
    };
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    cv.addEventListener('pointerleave', () => { hover = null; tip.hidden = true; dirty = true; });
    cv.addEventListener('wheel', e => {
      e.preventDefault(); target = null;
      const [sx, sy] = point(e), [wx, wy] = toWorld(sx, sy);
      cam.k = clampK(cam.k * Math.exp(-e.deltaY * 0.0015));
      cam.x = wx - (sx - W / 2) / cam.k; cam.y = wy - (sy - H / 2) / cam.k; dirty = true;
    }, {passive:false});
    root.querySelectorAll('[data-zoom]').forEach(b => b.addEventListener('click', () => flyTo(cam.x, cam.y, cam.k * (b.dataset.zoom === 'in' ? 1.4 : 1 / 1.4))));

    function select(id, fly = true) {
      selected = id && byId.has(id) ? id : null; dirty = true;
      if (selected && fly) { const n = byId.get(selected); flyTo(n.x, n.y, n.depth === 1 ? 1.0 : n.depth === 2 ? 1.5 : 1.8); }
      renderPanel();
    }

    // ---------- side panel ----------
    const panel = q('[data-starmap-panel]');
    const row = (id, extra = '') => {
      const n = byId.get(id), s = stats.get(id), lv = levelOf(id);
      return `<button type="button" class="starmap-row" data-go="${esc(id)}"><i class="dot ${lv}"></i><span class="name">${esc(n.name)}</span><span class="meta">${esc(extra || (s ? pct(s.rate) + ' · ' + s.qs + '题' : '未考'))}</span></button>`;
    };
    const describe = s => {
      if (!s) return '还没有考过这个内容，星星保持暗色。';
      if (classMode) return `全班在相关 ${s.qs} 道题上的正确率为 ${pct(s.rate)}。`;
      const d = Math.round(s.delta * 100), who = DATA.audience === 'student' ? '你' : '该生';
      return `在相关的 ${s.qs} 道题里${who}答对 ${s.c} 道（${pct(s.rate)}）；同班同学做同样这些题的平均正确率是 ${pct(s.classRate)}，${who}${d >= 0 ? '高出' : '低了'} ${Math.abs(d)} 个百分点。`;
    };
    const relative = () => mode === 'relative' && !classMode;
    function overview() {
      const sections = nodes.filter(n => n.kind === 'knowledge' && n.depth >= 3), count = {weak:0, shaky:0, solid:0, none:0};
      sections.forEach(n => count[levelOf(n.id)]++);
      const rank = list => list.filter(n => ['weak', 'shaky'].includes(levelOf(n.id))).sort((a, b) => stats.get(a.id).score - stats.get(b.id).score);
      const chapters = rank(nodes.filter(n => n.kind === 'knowledge' && n.depth === 2)).slice(0, 6);
      const points = rank(sections.filter(n => stats.get(n.id)?.qs >= 3)).slice(0, 8);
      const abil = rank(abilities).slice(0, 5);
      const chapterMeta = n => { const s = stats.get(n.id); return relative() ? '比同班低 ' + Math.round(-s.delta * 100) + '%' : pct(s.rate) + ' · ' + s.qs + '题'; };
      const scope = classMode ? `${esc(DATA.class_name || '班级')} · ${DATA.class_size} 人 · ${DATA.assessments} 次已发布测评` : `已发布测评首次作答 · ${DATA.records.length} 题 · ${DATA.assessments} 次测评`;
      panel.innerHTML = `<p class="starmap-caption">${scope}</p><h3>${esc(DATA.label)}</h3>
        <div class="starmap-counts">${['weak', 'shaky', 'solid', 'none'].map(l => `<div><strong style="color:${l === 'none' ? 'inherit' : COLORS[l]}">${count[l]}</strong><span>${LABEL[l]}</span></div>`).join('')}</div>
        <p class="starmap-note">共 ${sections.length} 个知识点${relative() ? '，颜色按“和同班同学做同样题目”对比，排除试题难易的影响。' : '，颜色按正确率：低于 45% 为薄弱，45%–70% 为待巩固。'}</p>
        <h4>最需要关注的区域</h4>${chapters.map(n => row(n.id, chapterMeta(n))).join('') || '<p class="starmap-note">暂无明显薄弱的章节。</p>'}
        <h4>优先复习的知识点</h4>${points.map(n => row(n.id)).join('') || '<p class="starmap-note">暂无。</p>'}
        <h4>能力短板</h4>${abil.map(n => row(n.id)).join('') || '<p class="starmap-note">暂无明显短板。</p>'}
        <details class="starmap-help"><summary>怎么看这张图？</summary><ul>
          <li>外圈的星系是教材，每团星座是一个章节，小星星是具体知识点；中心一圈 ✦ 是学科能力。</li>
          <li>星星越大越亮，相关题目越多；暗灰色表示还没考到或题目不足 2 道，不下结论。</li>
          <li>星云颜色代表整个章节区域的表现，红色闪烁的区域需要先补。</li>
          <li>蓝色细线是教材脉络；青色弧线表示两个知识点经常在同一道题里一起考；紫色线表示这个知识点的题需要用到哪项能力。</li>
          <li>一道综合题会同时计入它标注的所有知识点和能力，单个知识点要结合相关题目一起看；章节星云汇总的题目更多，更稳定。</li>
        </ul></details>`;
    }
    function renderPanel() {
      if (!selected) return overview();
      const n = byId.get(selected), s = stats.get(n.id), lv = levelOf(n.id), other = e => e.s === n.id ? e.t : e.s;
      const path = ancestors(n.id).slice(1).reverse().map(id => `<button type="button" class="starmap-crumb" data-go="${esc(id)}">${esc(byId.get(id).name)}</button>`).join(' / ');
      const links = adj.get(n.id);
      const kn = links.filter(e => e.kind === 'cotest').sort((a, b) => b.w - a.w);
      const ab = links.filter(e => e.kind === 'ability' && !e.aa).sort((a, b) => b.w - a.w);
      const aa = links.filter(e => e.aa).sort((a, b) => b.w - a.w);
      const weakNeighbours = kn.filter(e => levelOf(other(e)) === 'weak').length;
      const items = (s?.items || []).map(it => ({...it, q:DATA.questions[it.key]}))
        .sort((a, b) => classMode ? a.outcome - b.outcome : (a.outcome === 'c') - (b.outcome === 'c') || (b.q.cr ?? 0) - (a.q.cr ?? 0));
      const qRow = it => {
        const name = `${esc(it.q.title)} · 第 ${esc(it.q.no)} 题`;
        if (classMode) { const l = it.outcome < 0.45 ? 'weak' : it.outcome < 0.7 ? 'shaky' : 'solid'; return `<li><a href="${esc(it.q.url)}">${name}</a><span style="color:${COLORS[l]}">${pct(it.outcome)}</span></li>`; }
        const ok = it.outcome === 'c', mark = ok ? '✓' : it.outcome === 'b' ? '空' : '✗';
        const flag = !ok && (it.q.cr ?? 0) >= 0.7 ? '<em class="starmap-flag">多数人答对</em>' : '';
        return `<li><a href="${esc(it.q.url)}"><b class="${ok ? 'ok' : 'bad'}">${mark}</b> ${name}</a><span>${flag}班级 ${pct(it.q.cr)}</span></li>`;
      };
      panel.innerHTML = `<button type="button" class="starmap-back" data-go="">← 返回概况</button>
        <p class="starmap-caption">${path || typeName(n)}</p>
        <h3>${esc(n.name)} <span class="starmap-chip">${typeName(n)}</span></h3>
        <div class="starmap-score" style="border-left-color:${COLORS[lv]}">
          <div><strong style="color:${lv === 'none' ? 'inherit' : COLORS[lv]}">${s ? pct(s.rate) : '—'}</strong><span>${classMode ? '全班正确率' : '正确率'}</span><em class="starmap-chip" style="color:${COLORS[lv]};border-color:${COLORS[lv]}">${LABEL[lv]}</em></div>
          <p>${describe(s)}</p>${s && s.qs >= 2 && s.qs < 4 ? '<p class="starmap-warn">题目还比较少，结论仅供参考。</p>' : ''}
        </div>
        ${weakNeighbours && lv === 'weak' ? `<p class="starmap-alert">与它经常一起考的知识点中有 ${weakNeighbours} 个也偏弱，可能是一片连锁薄弱区域，建议一起复习。</p>` : ''}
        ${n.children.length ? `<h4>下级内容 <small>${n.children.length}</small></h4>${n.children.map(c => row(c.id)).join('')}` : ''}
        ${kn.length ? `<h4>知识关联 <small>常在同一道题里一起考</small></h4>${kn.slice(0, 10).map(e => row(other(e), '同考 ' + e.w + ' 题')).join('')}` : ''}
        ${ab.length ? `<h4>${n.kind === 'ability' ? '用到这项能力的知识点' : '需要的能力'}</h4>${ab.slice(0, 10).map(e => row(other(e), e.w + ' 题')).join('')}` : ''}
        ${aa.length ? `<h4>常一起用到的能力</h4>${aa.slice(0, 6).map(e => row(other(e), '同用 ' + e.w + ' 题')).join('')}` : ''}
        ${items.length ? `<h4>相关题目 <small>${classMode ? '按班级正确率排序' : '错题在前'}</small></h4><ul class="starmap-questions">${items.slice(0, 16).map(qRow).join('')}</ul>${items.length > 16 ? `<p class="starmap-note">另有 ${items.length - 16} 题</p>` : ''}` : ''}
        <p class="starmap-note">依据：题目的知识点和能力标签，以及${classMode ? '全班' : ''}已发布测评中的首次作答；同一道题的多个版本或小问只计一次。</p>`;
    }
    panel.addEventListener('click', e => {
      const b = e.target.closest('[data-go]'); if (!b) return;
      select(b.dataset.go || null);
      if (e.detail === 0) panel.focus({preventScroll:true});
      if (matchMedia('(max-width: 900px)').matches && b.dataset.go) stage.scrollIntoView({block:'nearest', behavior:reduced ? 'instant' : 'smooth'});
    });

    // ---------- toolbar ----------
    const modes = q('[data-starmap-modes]');
    if (classMode) { modes.querySelector('[data-mode="relative"]').disabled = true; modes.querySelectorAll('[data-mode]').forEach(b => b.setAttribute('aria-pressed', b.dataset.mode === mode)); }
    modes.addEventListener('click', e => {
      const b = e.target.closest('[data-mode]'); if (!b || b.disabled) return;
      mode = b.dataset.mode; modes.querySelectorAll('[data-mode]').forEach(x => x.setAttribute('aria-pressed', x === b));
      compute(); renderPanel(); dirty = true;
    });
    root.querySelectorAll('[data-layer]').forEach(cb => cb.addEventListener('change', () => { layers[cb.dataset.layer] = cb.checked; dirty = true; }));
    q('[data-starmap-reset]').addEventListener('click', () => { select(null); const f = fitAll(); flyTo(f.x, f.y, f.k); });
    const search = q('[data-starmap-search]'), results = q('[data-starmap-results]');
    search.addEventListener('input', () => {
      const term = search.value.trim(); results.hidden = !term; if (!term) return;
      const hits = nodes.filter(n => n.name.includes(term)).slice(0, 20);
      results.innerHTML = hits.length ? hits.map(n => row(n.id, typeName(n))).join('') : '<span class="starmap-note">没有找到</span>';
    });
    search.addEventListener('keydown', e => { if (e.key === 'Escape') { search.value = ''; results.hidden = true; } if (e.key === 'Enter') { e.preventDefault(); results.querySelector('[data-go]')?.click(); } });
    results.addEventListener('click', e => { const b = e.target.closest('[data-go]'); if (b) { e.preventDefault(); select(b.dataset.go); results.hidden = true; search.value = ''; } });

    new ResizeObserver(() => { const first = !W; resize(); if (first && W) { cam = fitAll(); } }).observe(stage);
    compute(); renderPanel(); requestAnimationFrame(frame);
  });
})();
