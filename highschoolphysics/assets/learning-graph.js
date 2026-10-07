(() => {
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  document.querySelectorAll('[data-learning-graph]').forEach(root=>{
    const data=JSON.parse(root.querySelector('[data-graph-data]').textContent),nodes=new Map(data.nodes.map(n=>[n.id,n]));
    const search=root.querySelector('[data-graph-search]'),kind=root.querySelector('[data-graph-kind]'),select=root.querySelector('[data-graph-focus]'),mode=root.querySelector('[data-graph-mode]');
    const canvas=root.querySelector('[data-graph-canvas]'),list=root.querySelector('[data-graph-list]'),detail=root.querySelector('[data-graph-detail]'),count=root.querySelector('[data-graph-count]');
    let focus=new URLSearchParams(location.search).get('focus')||data.evidence.find(e=>e.status==='本步待检查')?.node_id||data.evidence[0]?.node_id||data.nodes.find(n=>n.kind==='objective')?.id||data.nodes[0]?.id,listMode=false;
    const observations=id=>data.evidence.filter(e=>e.node_id===id);
    function options(){
      const text=search.value.trim().toLowerCase(),filtered=data.nodes.filter(n=>(!kind.value||n.kind===kind.value)&&(!text||(n.name+' '+n.definition).toLowerCase().includes(text)));
      select.innerHTML=filtered.map(n=>`<option value="${esc(n.id)}">${esc(data.kinds[n.kind])} · ${esc(n.name)}</option>`).join('');
      if(filtered.some(n=>n.id===focus))select.value=focus;
      else focus=filtered[0]?.id;
      render();
    }
    function showNode(id){
      const n=nodes.get(id);if(!n)return;
      const own=observations(id),parent=nodes.get(n.parent),relations=data.edges.filter(e=>e.source===id||e.target===id);
      detail.innerHTML=`<h3>${esc(n.name)}</h3><p class="graph-kind">${esc(data.kinds[n.kind])}</p><p>${esc(n.definition)}</p>${parent?`<p>所属内容：<button type="button" data-node="${esc(parent.id)}">${esc(parent.name)}</button></p>`:''}<h4>我的诊断观察</h4>${own.length?own.map(e=>`<p><strong>${esc(e.status)}</strong><br>${esc(e.label)}<br>${e.objective_name?`${e.status==='尚未观察'?'关联目标':'诊断时目标'}：${esc(e.objective_name)}<br>`:''}<a href="${esc(e.url)}">回到本题诊断与重做</a></p>`).join(''):'<p>尚未观察。相关错题并不意味着这个节点整体薄弱。</p>'}<h4>相连的内容</h4>${relations.length?relations.map(e=>{const other=nodes.get(e.source===id?e.target:e.source);return `<details><summary>${esc(data.relations[e.kind])} · ${esc(other.name)}</summary><p>${esc(e.reason)}</p>${e.conditions?`<p>适用条件：${esc(e.conditions)}</p>`:''}<p>${esc(data.statuses[e.status])}<br>依据：${esc(e.locator)}</p><button type="button" data-node="${esc(other.id)}">以此为中心展开</button></details>`;}).join(''):'<p>暂无已整理的语义关系，可通过所属目录继续查找。</p>'}`;
    }
    function showEdge(id){
      const e=data.edges.find(e=>e.id===id);if(!e)return;
      detail.innerHTML=`<h3>${esc(data.relations[e.kind])}</h3><p>${esc(nodes.get(e.source).name)} ${['related','confusable'].includes(e.kind)?'↔':'→'} ${esc(nodes.get(e.target).name)}</p><p>${esc(e.reason)}</p><p>适用条件：${esc(e.conditions||'按来源任务理解')}</p><p>${esc(data.statuses[e.status])}</p><p>依据：${esc(e.locator)}</p><button type="button" data-node="${esc(e.source)}">展开起点</button><button type="button" data-node="${esc(e.target)}">展开终点</button>`;
    }
    function render(){
      if(!nodes.has(focus)){canvas.innerHTML='';list.innerHTML='';detail.innerHTML='<p>没有匹配的内容，请调整名称或类型。</p>';count.textContent='暂无匹配内容';return;}
      const neighborEdges=data.edges.filter(e=>e.source===focus||e.target===focus).sort((a,b)=>(a.kind==='contains')-(b.kind==='contains'));
      const neighbors=[...new Set(neighborEdges.map(e=>e.source===focus?e.target:e.source))].slice(0,12),visible=[focus,...neighbors];
      const positions=new Map([[focus,{x:450,y:310}]]);neighbors.forEach((id,i)=>{const a=-Math.PI/2+i*2*Math.PI/neighbors.length;positions.set(id,{x:450+310*Math.cos(a),y:310+238*Math.sin(a)});});
      const lines=data.edges.filter(e=>visible.includes(e.source)&&visible.includes(e.target));
      const svgLines=lines.map(e=>{
        const a=positions.get(e.source),b=positions.get(e.target),dx=b.x-a.x,dy=b.y-a.y,len=Math.hypot(dx,dy),ux=dx/len,uy=dy/len;
        const x1=a.x+ux*82,y1=a.y+uy*36,x2=b.x-ux*82,y2=b.y-uy*36;
        return `<g role="button" tabindex="0" data-edge="${esc(e.id)}" aria-label="${esc(data.relations[e.kind]+'：'+nodes.get(e.source).name+'与'+nodes.get(e.target).name)}"><title>${esc(data.relations[e.kind]+'：'+e.reason)}</title><line class="graph-edge-hit" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"></line><line class="graph-edge ${e.status==='curated'?'candidate':''}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" marker-end="url(#graph-arrow)" ${['related','confusable'].includes(e.kind)?'marker-start="url(#graph-arrow-back)"':''}></line></g>`;
      }).join('');
      const svgNodes=visible.map(id=>{
        const n=nodes.get(id),p=positions.get(id),chars=Array.from(n.name),label=[chars.slice(0,10).join(''),chars.slice(10,20).join('')+(chars.length>20?'…':'')];
        const own=observations(id),status=own.some(e=>e.status==='本步待检查')?'待检查':own.some(e=>e.status==='借助帮助完成')?'借助帮助':own.some(e=>e.status==='本步独立通过')?'本步通过':'尚未观察';
        const shape=n.kind==='ability'?'<ellipse cx="0" cy="0" rx="78" ry="33"/>':n.kind==='literacy'?'<path d="M-83 0L0-42L83 0L0 42Z"/>':`<rect x="-82" y="-34" width="164" height="68" rx="${n.kind==='objective'?20:5}"/>`;
        return `<g class="graph-node ${id===focus?'selected':''} ${status==='待检查'?'needs-check':''}" transform="translate(${p.x} ${p.y})" role="button" tabindex="0" data-node="${esc(id)}" aria-label="${esc(data.kinds[n.kind]+'：'+n.name+'，'+status)}"><title>${esc(n.name)}</title>${shape}<text text-anchor="middle" y="${label[1]?-9:-4}">${esc(label[0])}</text>${label[1]?`<text text-anchor="middle" y="9">${esc(label[1])}</text>`:''}<text text-anchor="middle" class="graph-node-status" y="${label[1]?27:17}">${esc(data.kinds[n.kind])} · ${status}</text></g>`;
      }).join('');
      canvas.innerHTML=`<svg viewBox="0 0 900 620" aria-label="局部知识关联星图"><defs><marker id="graph-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill="#41556f"/></marker><marker id="graph-arrow-back" markerWidth="8" markerHeight="8" refX="1" refY="4" orient="auto"><path d="M8 0L0 4L8 8" fill="#41556f"/></marker></defs>${svgLines}${svgNodes}</svg>`;
      list.innerHTML=`<h3>${esc(nodes.get(focus).name)}的相邻内容</h3><ul>${neighbors.map(id=>`<li><button type="button" data-node="${esc(id)}">${esc(data.kinds[nodes.get(id).kind])} · ${esc(nodes.get(id).name)}</button><p>${esc(nodes.get(id).definition)}</p></li>`).join('')}</ul>`;
      canvas.hidden=listMode;list.hidden=!listMode;mode.textContent=listMode?'切换到星图':'切换到列表';mode.setAttribute('aria-pressed',String(listMode));
      count.textContent=`当前展开 ${visible.length} 个节点；相邻内容共 ${new Set(neighborEdges.map(e=>e.source===focus?e.target:e.source)).size} 个。全部 ${data.nodes.length} 个节点可通过查找访问。图谱第 ${data.version} 版。${canvas.scrollWidth>canvas.clientWidth&&!listMode?'可横向滑动查看，也可切换到列表。':''}`;
      if(!listMode)canvas.scrollLeft=Math.max(0,(canvas.scrollWidth-canvas.clientWidth)/2);
      showNode(focus);
    }
    function navigate(id){focus=id;search.value='';kind.value='';options();const u=new URL(location.href);u.searchParams.set('focus',id);history.replaceState(null,'',u);root.querySelector(`.graph-node[data-node="${CSS.escape(id)}"]`)?.focus({preventScroll:true});}
    root.addEventListener('click',e=>{const n=e.target.closest('[data-node]'),edge=e.target.closest('[data-edge]');if(n)navigate(n.dataset.node);else if(edge)showEdge(edge.dataset.edge);});
    root.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)&&e.target.matches('svg [role="button"]')){e.preventDefault();e.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}});
    select.addEventListener('change',()=>navigate(select.value));search.addEventListener('input',options);kind.addEventListener('change',options);mode.addEventListener('click',()=>{listMode=!listMode;render();});options();
  });
  document.querySelectorAll('[data-graph-edges]').forEach(form=>{
    const search=form.querySelector('[data-graph-edge-search]'),status=form.querySelector('[data-graph-edge-status]'),semantic=form.querySelector('[data-graph-semantic-only]'),rows=[...form.querySelectorAll('[data-graph-edge-row]')];
    function filter(){let visible=0;rows.forEach(row=>{const matches=(!search.value.trim()||row.textContent.includes(search.value.trim()))&&(!status.value||row.dataset.status===status.value)&&(!semantic.checked||!['contains','uses_ability','supports_literacy'].includes(row.dataset.kind));row.hidden=!matches;if(matches)visible++;else row.querySelector('[name=ids]').checked=false;});form.querySelector('[data-graph-filter-count]').textContent='当前显示 '+visible+' 条关系；目标归属、能力和素养联系也可在诊断卡中逐项确认。';}
    search.addEventListener('input',filter);status.addEventListener('change',filter);semantic.addEventListener('change',filter);filter();
  });
  const message=document.querySelector('[data-graph-review-message]');
  document.querySelectorAll('[data-graph-card],[data-graph-edge],[data-graph-edges]').forEach(form=>form.addEventListener('submit',async e=>{
    e.preventDefault();const fd=new FormData(form),version=Number(form.dataset.version);let action,payload;
    if(form.matches('[data-graph-card]')){
      action='graph-card-save';const steps=[],mappings=[];
      form.querySelectorAll('[data-graph-step]').forEach(part=>{const value=k=>part.querySelector(`[name="${k}"]`).value;steps.push({stage:value('stage'),prompt:value('prompt'),explanation:value('explanation'),correct:Number(value('correct')),options:[...part.querySelectorAll('[name="option"]')].map(x=>x.value),hints:[...part.querySelectorAll('[name="hint"]')].map(x=>x.value)});mappings.push(Object.fromEntries(['name','definition','knowledge_id','ability_id','literacy_id'].map(k=>[k,value(k)])));});
      payload={version,card_id:form.dataset.cardId,card:{title:fd.get('title'),steps},mappings};
    }else if(form.matches('[data-graph-edge]')){action='graph-edge-save';payload={version,...Object.fromEntries(fd)};}else{action='graph-edge-review';payload={version,ids:fd.getAll('ids'),status:e.submitter.value};}
    const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);message.textContent='正在保存并发布…';
    try{const r=await fetch('api/learning/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error?.message||'保存失败，请重试');message.textContent='已发布第 '+d.result.version+' 版';location.reload();}catch(error){message.textContent=error.message;buttons.forEach(b=>b.disabled=false);message.scrollIntoView({block:'center'});}
  }));
})();
