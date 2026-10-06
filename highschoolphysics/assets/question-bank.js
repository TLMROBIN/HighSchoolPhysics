(() => {
  const root = document.querySelector('[data-question-bank]');
  if (!root) return;
  const base = document.body.dataset.basePath || '';
  const jobStorageKey='hsp-bank-tag-jobs:'+root.dataset.actorId;
  const $ = (name) => root.querySelector(`[data-bank-${name}]`);
  const node = (tag, text, className) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; };
  const status = (text) => $('status').textContent = text;
  const api = async (route, payload) => {
    const r = await fetch(base + '/api/question-bank/' + route, payload === undefined ? {cache:'no-store'} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const d = await r.json(); if (!r.ok) throw Error(d.message || d.error?.message || '操作失败'); return d.result;
  };
  const button = (text, fn) => { const b=node('button',text); b.type='button'; b.addEventListener('click',async()=>{b.disabled=true;try {await fn();}catch(e){status(e.message);}finally{b.disabled=false;}}); return b; };
  const math = (n) => window.renderMathInElement?.(n,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\[',right:'\\]',display:true},{left:'\\(',right:'\\)',display:false}],throwOnError:false,trust:false,ignoredTags:['script','noscript','style','textarea','pre','code']});
  const selected = new Set(), suggestions = new Map(), editors = new Map();
  let listing, catalog, offset=0, jobIds=[], polling=false;
  const params=new URLSearchParams(location.search);
  let batch=params.get('batch_id') || '', paper=params.get('paper_id') || '';
  const refreshSelection = () => {
    $('selected').textContent=`已选 ${selected.size} 道小题`;
    root.querySelectorAll('[data-group-ids]').forEach(box=>{const ids=JSON.parse(box.dataset.groupIds); box.checked=ids.every(id=>selected.has(id)); box.indeterminate=!box.checked&&ids.some(id=>selected.has(id));});
    $('save-ai').hidden=![...selected].some(id=>suggestions.has(id));
  };
  const setOptions=(select,rows,empty,value)=>{select.replaceChildren(new Option(empty,''),...rows.map(r=>new Option(r.title+' · '+r.question_count+' 道小题',r.id)));select.value=value;};
  const tagEntry=(unit,inputs)=>({question_id:unit.id,question_version:unit.question_version,expected_revision:unit.tag_revision,...Object.fromEntries(Object.entries(inputs).map(([kind,input])=>[kind,[...input.selectedOptions].map(o=>o.value)]))});
  const adopt=(unit,inputs,candidate)=>{
    for(const [kind,input] of Object.entries(inputs)) {const ids=new Set((candidate[kind+'_tags']||[]).map(t=>t.id)); [...input.options].forEach(o=>o.selected=ids.has(o.value));}
  };
  const tagForm = (unit) => {
    const section=node('section',undefined,'bank-tag-unit');
    section.append(node('h3',unit.label+' · 标签'));
    const note=node('p',unit.tags.length?'当前正式标签：'+unit.tags.map(t=>t.name).join('、'):'尚未标注'); section.append(note);
    const inputs={};
    for (const [kind,title] of [['knowledge','知识点'],['ability','能力'],['literacy','核心素养']]) {
      const label=node('label',title+'（最多 3 个）'), select=node('select');select.multiple=true;select.size=5; select.setAttribute('aria-label',title);select.dataset.tagFamily=kind;
      const chosen=new Set(unit.tags.filter(t=>t.tag_type===kind).map(t=>t.tag_id));
      for (const t of catalog[kind]) select.append(new Option(t.path_text||t.name,t.id,false,chosen.has(t.id)));
      const search=node('input');search.type='search';search.placeholder='搜索'+title;
      search.addEventListener('input',()=>{[...select.options].forEach(o=>o.hidden=!o.text.toLowerCase().includes(search.value.toLowerCase())&&!o.selected);});
      label.append(search,select);section.append(label);inputs[kind]=select;
    }
    const save=button('保存本题标签',async()=>{
      const result=await api('tags',{entries:[tagEntry(unit,inputs)]});unit.tag_revision=result.items[0].revision;unit.tags=result.items[0].tags;
      note.textContent='已保存：'+unit.tags.map(t=>t.name).join('、');suggestions.delete(unit.id);refreshSelection();status(result.message);
    });
    section.append(save);
    const suggestionBox=node('div',undefined,'bank-suggestion');section.append(suggestionBox);
    const showSuggestion=()=>{
      suggestionBox.replaceChildren();const s=suggestions.get(unit.id);if(!s)return;
      suggestionBox.append(node('p','AI 建议（尚未保存）：'+['knowledge_tags','ability_tags','literacy_tags'].flatMap(kind=>(s.candidate[kind]||[]).map(t=>{const label=Object.values(catalog).flat().find(x=>x.id===t.id)?.name||t.id;return label+'：'+t.rationale;})).join('；')));
      suggestionBox.append(button('填入 AI 建议，再手动调整',async()=>adopt(unit,inputs,s.candidate)));
    };
    editors.set(unit.id,{unit,inputs,note,showSuggestion});showSuggestion();
    return section;
  };
  const textField=(form,title,value,onChange,rows=3)=>{const label=node('label',title),input=node('textarea');input.rows=rows;input.value=value||'';input.addEventListener('input',()=>onChange(input.value));label.append(input);form.append(label);return input;};
  const contentEditor = (data,container) => {
    const panel=node('details',undefined,'bank-content-editor');panel.append(node('summary','编辑题干、选项、答案与解析'));
    const form=node('form'), doc=data.document?structuredClone(data.document):null;
    const answer=data.question.answer;
    const reference=answer && !Array.isArray(answer) && typeof answer==='object' ? (answer.answer ?? JSON.stringify(answer)) : answer;
    const fields={stem:data.question.stem,options:{...data.question.options},answer:Array.isArray(reference)?reference.join(','):String(reference??''),analysis:data.question.analysis};
    if(doc){
      textField(form,'完整题干（支持 Markdown / 公式）',doc.stem_md,v=>doc.stem_md=v,6);
      for(const o of doc.options)textField(form,'选项 '+o.key,o.markdown,v=>o.markdown=v);
      textField(form,'整题答案',doc.answer_md,v=>doc.answer_md=v);textField(form,'整题解析',doc.analysis_md,v=>doc.analysis_md=v,5);
      for(const c of doc.children){const title=c.label||c.key;form.append(node('h3',title));textField(form,title+'题干',c.stem_md,v=>c.stem_md=v);for(const o of c.options||[])textField(form,title+'选项 '+o.key,o.markdown,v=>o.markdown=v);textField(form,title+'答案',c.answer_md,v=>c.answer_md=v);textField(form,title+'解析',c.analysis_md,v=>c.analysis_md=v,4);}
    }else{
      textField(form,'题干',fields.stem,v=>fields.stem=v,6);for(const [key,value] of Object.entries(fields.options))textField(form,'选项 '+key,value,v=>fields.options[key]=v);
      textField(form,'答案',fields.answer,v=>fields.answer=v);textField(form,'解析',fields.analysis,v=>fields.analysis=v,5);
    }
    const submit=node('button','保存题目修改');submit.type='submit';form.append(submit);
    form.addEventListener('submit',async e=>{e.preventDefault();submit.disabled=true;try{
      await api('content',{question_id:data.question.id,question_version:data.question.version,expected_content_revision:data.content_revision_id,document:doc,fields});status('题目已保存；已有考试保留原题版本。');data.units.forEach(u=>{editors.delete(u.id);suggestions.delete(u.id);});await openGroup(data.question.id,container);
    }catch(err){status(err.message);}finally{submit.disabled=false;}});
    panel.append(form);return panel;
  };
  const openGroup=async(qid,container)=>{
    const data=await api('question?id='+encodeURIComponent(qid));container.replaceChildren();
    const preview=node('div',undefined,'bank-question-preview');preview.innerHTML=data.html;container.append(preview);math(preview);
    container.append(contentEditor(data,container));
    data.units.forEach(unit=>{
      if(unit.suggestion)suggestions.set(unit.id,unit.suggestion);
      container.append(tagForm(unit));
    });refreshSelection();
  };
  const load=async()=>{
    const query=new URLSearchParams({batch_id:batch,paper_id:paper,search:$('search').value,offset:String(offset)});
    listing=await api('library?'+query);editors.clear();
    setOptions($('batch'),listing.batches.map(b=>({...b,title:b.title+' · '+(b.import_mode==='paper'?'整卷':'零散')})),'全部批次与手工录题',batch);
    setOptions($('paper'),listing.papers,'全部题目',paper);
    $('results').replaceChildren();
    for(const g of listing.groups){
      const card=node('article',undefined,'bank-question-card'),header=node('div',undefined,'bank-card-heading');
      const label=node('label'),box=node('input');box.type='checkbox';box.dataset.groupIds=JSON.stringify(g.question_ids);box.addEventListener('change',()=>{g.question_ids.forEach(id=>box.checked?selected.add(id):selected.delete(id));refreshSelection();});
      label.append(box,node('span',`原题号 ${g.number||'—'} · ${g.question_ids.length} 道小题 · ${g.tagged_count} 道已有标签`));header.append(label);card.append(header);
      card.append(node('p',g.stem.slice(0,180)+(g.stem.length>180?'…':''),'bank-stem-excerpt'));
      const details=node('details');details.append(node('summary','查看完整题目、编辑与标签管理'));const content=node('div');details.append(content);
      details.addEventListener('toggle',async()=>{if(!details.open||details.dataset.loaded)return;details.dataset.loaded='1';content.textContent='正在加载…';try{await openGroup(g.question_ids[0],content);}catch(e){delete details.dataset.loaded;content.textContent=e.message;}});
      card.append(details);$('results').append(card);
    }
    if(!listing.groups.length)$('results').append(node('p','当前范围没有题目。可先导入并确认入库。'));
    $('page').textContent=`第 ${Math.floor(offset/30)+1} / ${Math.max(1,Math.ceil(listing.total/30))} 页 · ${listing.total} 道大题`;
    $('prev').disabled=offset===0;$('next').disabled=offset+30>=listing.total;refreshSelection();
  };
  const run=fn=>async()=>{try{await fn();}catch(e){status(e.message);}};
  $('filter').addEventListener('click',run(async()=>{offset=0;await load();}));
  $('batch').addEventListener('change',run(async()=>{batch=$('batch').value;paper='';offset=0;selected.clear();await load();}));
  $('paper').addEventListener('change',run(async()=>{paper=$('paper').value;batch='';offset=0;selected.clear();await load();}));
  $('select-all').addEventListener('click',()=>{selected.clear();listing.scope_question_ids.forEach(id=>selected.add(id));refreshSelection();});
  $('clear').addEventListener('click',()=>{selected.clear();refreshSelection();});
  $('prev').addEventListener('click',run(async()=>{offset=Math.max(0,offset-30);await load();}));
  $('next').addEventListener('click',run(async()=>{offset+=30;await load();}));
  const poll=async()=>{
    if(polling||!jobIds.length)return;polling=true;
    try{
      const result=await api('jobs?'+jobIds.map(id=>'id='+encodeURIComponent(id)).join('&'));
      let done=0,failed=0,stale=0;
      for(const job of result.jobs){
        if(job.status==='completed')done++;if(job.status==='failed')failed++;
        if(job.result.status==='stale')stale++;
        if(job.result.status==='suggested'&&job.candidate){
          const expected=job.result.expected;
          suggestions.set(job.question_id,{candidate:job.candidate,expected});editors.get(job.question_id)?.showSuggestion();
        }
      }
      const reasons={budget_blocked:'调用预算或每日限额已用完',missing_secret:'未配置模型密钥',missing_model:'未配置模型名称',invalid_model_output:'模型返回的标签无效',llm_provider_not_configured:'大模型配置已停用',secret_unavailable:'模型密钥无法读取'};
      const failures=result.jobs.filter(j=>j.status==='failed').map(j=>reasons[j.error_code]||j.error_code||'生成失败，请重试');
      status(`AI 标签：完成 ${done}/${jobIds.length}；失败 ${failed}；内容或标签已变化 ${stale}。生成结果可调整后保存。`+(failures.length?'\n失败原因：'+[...new Set(failures)].join('；'):''));refreshSelection();
      if(result.jobs.some(j=>['queued','running'].includes(j.status)))setTimeout(poll,2500);
      else {sessionStorage.removeItem(jobStorageKey); jobIds=[];}
    }catch(e){status(e.message+'；稍后继续查询');setTimeout(poll,5000);}finally{polling=false;}
  };
  const generate=async(onlyMissing)=>{
    if(!selected.size)throw Error('请先选择题目或选中当前批次全部题目');
    if(jobIds.length)throw Error('当前 AI 任务仍在进行，请等待完成后再发起');
    const result=await api('generate',{question_ids:[...selected],only_missing:onlyMissing,request_key:crypto.randomUUID()});
    jobIds=[...new Set([...jobIds,...result.job_ids])];sessionStorage.setItem(jobStorageKey,JSON.stringify(jobIds));status(`已排队 ${result.job_ids.length} 道题；跳过 ${result.skipped.length} 道已有标签题。`);await poll();
  };
  $('ai-missing').addEventListener('click',run(()=>generate(true)));$('ai').addEventListener('click',run(()=>generate(false)));
  $('save-ai').addEventListener('click',run(async()=>{
    const entries=[...selected].filter(id=>suggestions.has(id)).map(id=>{const s=suggestions.get(id);return {question_id:id,question_version:s.expected.question_version,expected_revision:s.expected.tag_revision,...Object.fromEntries(['knowledge','ability','literacy'].map(kind=>[kind,(s.candidate[kind+'_tags']||[]).map(t=>t.id)]))};});
    if(!entries.length)throw Error('没有可保存的 AI 建议');
    const result=await api('tags',{entries});entries.forEach(e=>suggestions.delete(e.question_id));status(result.message);refreshSelection();await load();
  }));
  $('save-paper').addEventListener('submit',async e=>{e.preventDefault();try{const title=new FormData(e.currentTarget).get('title');const result=await api('paper',{title,question_ids:[...selected]});paper=result.paper.id;batch='';offset=0;await load();status('试卷已保存，可以前往教师工作台新建考试。');}catch(err){status(err.message);}});
  const loadTokens=async()=>{const result=await api('tokens');$('tokens').replaceChildren();for(const t of result.tokens){const row=node('p',t.name+' · '+t.expires_at+' · '+(t.revoked_at?'已撤销':'有效'));if(!t.revoked_at)row.append(button('撤销',async()=>{await api('tokens/revoke',{id:t.id});await loadTokens();}));$('tokens').append(row);}};
  $('token').addEventListener('submit',async e=>{e.preventDefault();try{const form=new FormData(e.currentTarget);const result=await api('tokens',{name:form.get('name'),days:Number(form.get('days'))});const output=$('token-result');output.replaceChildren(node('p','接入凭证（仅显示一次）：'));const input=node('input');input.value=result.token;input.readOnly=true;input.setAttribute('aria-label','Agent 接入凭证');output.append(input,button('复制凭证',async()=>navigator.clipboard.writeText(result.token)),button('隐藏凭证',async()=>output.replaceChildren()));await loadTokens();}catch(err){status(err.message);}});
  (async()=>{try{catalog=await api('taxonomy');await load();await loadTokens();try{jobIds=JSON.parse(sessionStorage.getItem(jobStorageKey)||'[]');}catch{}if(jobIds.length)await poll();}catch(e){status(e.message);}})();
})();
