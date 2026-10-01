/* Drafts remain until server acknowledgement; retries reuse the same request key. */
(()=>{
 const request=async(action,p)=>{const r=await fetch('api/learning/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const d=await r.json();if(!r.ok||d.ok===false)throw Error(d.error?.message||d.message||'提交失败，请重试');return d.result||d;};
 document.querySelectorAll('.learning-form').forEach(f=>{
 const action=f.dataset.action, status=f.querySelector('[role=status]');
 if(action==='question'){
  const fill=f.querySelector('[data-fill-rule]'),quantity=f.querySelector('[data-quantity-rule]');
  const updateRule=()=>{if(!fill)return;const enabled=f.elements.question_type.value==='fill';fill.hidden=!enabled;fill.disabled=!enabled;const numeric=enabled&&f.elements.fill_match.value==='numeric_quantity';quantity.hidden=!numeric;quantity.querySelectorAll('input,select').forEach(input=>input.disabled=!numeric);};
  f.elements.question_type.addEventListener('change',updateRule);f.elements.fill_match.addEventListener('change',updateRule);updateRule();
 }
 const outcomeLabel=x=>({correct:'正确',wrong:'错误',blank:'空白',pending:'待确认'}[x]||x);
 const reasonLabel=x=>({external_conflict:'外部结果与规则不一致',external_confirmation:'外部结论需要教师确认',external_pending:'外部记录尚未确认',answer_rule:'规则无法确定',invalid_options:'选项无效或单选多选',numeric_format:'数值格式待确认',unit_required:'缺少必填单位',unit_unknown:'单位暂不支持',unit_mismatch:'单位量纲不一致',unit_conversion_disabled:'本题未允许单位换算',precision_review:'有效数字需教师核对'}[x]||'');
 let previewToken='';
 const previewOutput=f.querySelector('.response-preview');
 const renderPreview=d=>{if(!previewOutput)return;previewOutput.replaceChildren();if(d.records){const table=document.createElement('table');const head=table.insertRow();['学生 / 作答序号','原始答案','规范答案','规则建议','外部结果','有效结果 / 异常'].forEach(x=>{const th=document.createElement('th');th.textContent=x;head.append(th);});d.records.forEach(r=>{const row=table.insertRow();[r.student_name+' / '+r.number,r.raw_answer||'空白',r.normalized_answer||'空白',outcomeLabel(r.proposed_outcome),r.supplied_outcome?outcomeLabel(r.supplied_outcome):'未提供',outcomeLabel(r.outcome)+' '+reasonLabel(r.category)].forEach(x=>row.insertCell().textContent=x);});const scroll=document.createElement('div');scroll.style.overflowX='auto';scroll.append(table);previewOutput.append(scroll);}if(d.impact){const impact=d.impact;[['首次答案',impact.initial_answer],['原有效答案 → 新答案',impact.old_answer+' → '+impact.new_answer],['原结果 → 新结果',outcomeLabel(impact.old_outcome)+' → '+outcomeLabel(impact.new_outcome)],['其他有效错误来源',impact.other_wrong_sources],['复习影响',impact.practice],['统计影响',impact.statistics]].forEach(([label,value])=>{const line=document.createElement('p');line.textContent=label+'：'+value;previewOutput.append(line);});}};
 const draftKey='hsp-learning-'+action+'-'+(f.elements.wrong_id?.value||f.elements.assessment_id?.value||'');
 const payload=()=>{const p={};for(const [k,v]of new FormData(f)){if(k in p)p[k]=[].concat(p[k],v);else p[k]=v;}return p;};
 let key=globalThis.crypto?.randomUUID?.()||('request-'+Date.now()+'-'+Math.random().toString(36).slice(2));let image='';
 if(action==='submit'){
  try{let d=JSON.parse(sessionStorage.getItem(draftKey)||'null');if(d){key=d.request_key;f.querySelectorAll('[name=answer]').forEach(e=>{if(e.type==='checkbox'||e.type==='radio')e.checked=[].concat(d.answer||[]).includes(e.value);else e.value=d.answer||'';});}}catch{}
  f.addEventListener('input',()=>sessionStorage.setItem(draftKey,JSON.stringify({...payload(),request_key:key})));
 }
 f.addEventListener('input',()=>{if(['answers','response-correct','response-review'].includes(action)){previewToken='';key=globalThis.crypto?.randomUUID?.()||('request-'+Date.now()+'-'+Math.random().toString(36).slice(2));f.querySelector('.confirm-answers')?.setAttribute('hidden','');previewOutput?.replaceChildren();}});
 const run=async(confirm=false)=>{if(action==='submit')sessionStorage.setItem(draftKey,JSON.stringify({...payload(),request_key:key}));if(action==='assessment'&&!JSON.parse(f.dataset.paperQuestionOrder||'[]').length){status.textContent='先从题库中选择至少一道完整题目。';return;}const buttons=f.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);status.textContent='处理中…';try{const requestPayload=payload();if(action==='assessment')requestPayload.questions=JSON.parse(f.dataset.paperQuestionOrder||'[]');const d=await request(action,{...requestPayload,image,request_key:key,confirm,preview_token:previewToken});status.textContent=d.message||(d.outcome?({'correct':'本次正确','wrong':'需再练','blank':'空白','pending':'待教师确认'}[d.outcome]+' · '+(d.purpose==='verify'?'独立验证':'学习练习，不计验证次数')):'已保存');if(d.preview){previewToken=d.preview_token;renderPreview(d);const confirmButton=f.querySelector('.confirm-answers');if(confirmButton)confirmButton.hidden=false;}else if(action==='submit'){sessionStorage.removeItem(draftKey);const a=document.createElement('a');a.href='app';a.textContent=' 返回首页，查看下一题';status.append(a);f.querySelectorAll('input,textarea,button').forEach(e=>e.disabled=true);return;}else{f.querySelector('.confirm-answers')?.setAttribute('hidden','');const a=document.createElement('a');a.href=d.url||location.href;a.textContent=['answers','response-review','response-correct','publish'].includes(action)?' 刷新查看已保存结果':' 查看创建结果';status.append(a);}}catch(e){status.textContent=e.message+'；输入已保留，可重试。';}buttons.forEach(b=>b.disabled=false);};
 f.querySelector('.question-image')?.addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;const bitmap=await createImageBitmap(file),canvas=document.createElement('canvas');const scale=Math.min(1,1500/bitmap.width);canvas.width=bitmap.width*scale;canvas.height=bitmap.height*scale;canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);image=canvas.toDataURL('image/png').split(',')[1];if(image.length>850000){image='';throw Error('图片较大，请裁剪到单题后再上传');}status.textContent='原题图已准备好';}catch(e){status.textContent=e.message;}});
 f.addEventListener('submit',e=>{e.preventDefault();run();});f.querySelector('.confirm-answers')?.addEventListener('click',()=>run(true));f.querySelector('.answers-file')?.addEventListener('change',async e=>{if(e.target.files[0]){f.elements.csv.value=await e.target.files[0].text();f.elements.csv.dispatchEvent(new Event('input',{bubbles:true}));}});
 });
 document.querySelectorAll('.response-history').forEach(button=>button.addEventListener('click',async()=>{const out=button.nextElementSibling;try{const d=await request('response-history',{response_id:button.dataset.id});out.replaceChildren();d.evidence.forEach(e=>{const p=document.createElement('p');p.textContent='原始证据：'+(e.raw_answer??'历史未保留')+' · 来源：'+(e.source_name||({legacy:'历史证据',teacher_correction:'教师更正',manual_import:'人工录入'}[e.extraction_method]||e.extraction_method))+' · 行：'+(e.source_row||'未记录');out.append(p);if(e.source_asset_id){const link=document.createElement('a');link.href='exam-media?id='+encodeURIComponent(e.source_asset_id);link.textContent='查看原答题图';out.append(link);}});d.decisions.forEach(r=>{const p=document.createElement('p');p.textContent=new Date(r.created_at).toLocaleString('zh-CN')+' · '+r.answer+' · '+({correct:'正确',wrong:'错误',blank:'空白',pending:'待确认'}[r.outcome]||r.outcome)+' · '+({legacy:'历史确认',rule:'规则核对',external_rule_agreement:'外部与规则一致',teacher_review:'教师复核',teacher_correction:'教师更正'}[r.method]||r.method)+' · '+r.reason;out.append(p);});}catch(e){out.textContent=e.message;}}));
 document.querySelectorAll('.teacher-paper-builder').forEach(form=>{
  const cards=[...form.querySelectorAll('[data-question-card]')];
  const filters=form.querySelector('[data-question-filters]');
  const basket=form.querySelector('[data-paper-basket]');
  const preview=form.querySelector('[data-paper-preview]');
  const selectedCount=form.querySelector('[data-paper-selection-count]');
  const filterCount=form.querySelector('[data-question-filter-count]');
  const pageInfo=form.querySelector('[data-question-page-info]');
  const pagePrev=form.querySelector('[data-question-page-prev]');
  const pageNext=form.querySelector('[data-question-page-next]');
  const pageSize=12;
  let currentPage=1;
  const order=[];
  const byKey=new Map(cards.map(card=>[card.dataset.groupKey,card]));
  const idsFor=card=>{try{return JSON.parse(card.dataset.questionIds||'[]');}catch{return[];}};
  const toggleCard=(card,selected)=>{
   const toggle=card.querySelector('[data-group-toggle]');
   if(!toggle||toggle.disabled)return;
   toggle.checked=selected;
   card.classList.toggle('is-selected',selected);
   card.querySelectorAll('.question-pick-hidden-ids input[name="questions"]').forEach(input=>input.disabled=!selected);
   const index=order.indexOf(card.dataset.groupKey);
   if(selected&&index<0)order.push(card.dataset.groupKey);
   if(!selected&&index>=0)order.splice(index,1);
  };
  const renderOrder=()=>{
   if(!basket||!preview)return;
   basket.replaceChildren();preview.replaceChildren();
   if(!order.length){
    form.dataset.paperQuestionOrder='[]';
    const empty=document.createElement('li');empty.className='paper-basket-empty';empty.textContent='选择左侧整题后会显示在这里。';basket.append(empty);
    const note=document.createElement('p');note.textContent='添加题目后显示预览。';preview.append(note);
    selectedCount.textContent='尚未选择题目';return;
   }
   form.dataset.paperQuestionOrder=JSON.stringify(order.flatMap(key=>{const card=byKey.get(key);return card?idsFor(card):[];}));
   let questionCount=0;
   order.forEach((key,index)=>{
    const card=byKey.get(key);if(!card)return;
    const childCount=Number(card.dataset.childCount||1);questionCount+=childCount;
    const item=document.createElement('li');item.className='paper-basket-item';
    const title=document.createElement('span');title.textContent=`${card.dataset.title} · ${childCount} 个小问`;item.append(title);
    const controls=document.createElement('div');controls.className='paper-basket-controls';
    [['up','上移',index===0],['down','下移',index===order.length-1],['remove','移除',false]].forEach(([action,label,disabled])=>{
     const button=document.createElement('button');button.type='button';button.dataset.basketAction=action;button.dataset.groupKey=key;button.textContent=label;button.disabled=disabled;controls.append(button);
    });
    item.append(controls);basket.append(item);
    const section=document.createElement('section');section.className='paper-preview-question';
    const heading=document.createElement('h4');heading.textContent=`试卷第 ${index+1} 题 · ${card.dataset.title}`;section.append(heading);
    const content=card.querySelector('[data-question-preview-body]');if(content)section.append(content.cloneNode(true));
    preview.append(section);
   });
   selectedCount.textContent=`已选 ${order.length} 道大题 · ${questionCount} 个评分小问`;
   if(window.renderMathInElement)window.renderMathInElement(preview,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\[',right:'\\]',display:true},{left:'\\(',right:'\\)',display:false}],throwOnError:false,trust:false,ignoredTags:['script','noscript','style','textarea','pre','code']});
  };
  const matches=(card,name,value)=>{
   if(!value)return true;
   if(name==='search')return(card.dataset.search||'').includes(value.toLocaleLowerCase());
   const field={knowledge:'knowledgeIds',ability:'abilityIds',literacy:'literacyIds',type:'typeIds'}[name];
   return field?(` ${card.dataset[field]||''} `).includes(` ${value} `):true;
  };
  const applyFilters=()=>{
   const values={};filters?.querySelectorAll('[data-question-filter]').forEach(input=>values[input.dataset.questionFilter]=input.value.trim());
   const matching=cards.filter(card=>Object.entries(values).every(([name,value])=>matches(card,name,value)));
   const pageCount=Math.max(1,Math.ceil(matching.length/pageSize));
   currentPage=Math.min(currentPage,pageCount);
   const start=(currentPage-1)*pageSize;
   const end=start+pageSize;
   cards.forEach(card=>{const matchIndex=matching.indexOf(card);card.hidden=matchIndex<start||matchIndex>=end;});
   if(filterCount)filterCount.textContent=`显示 ${matching.length} / ${cards.length} 道大题`;
   if(pageInfo)pageInfo.textContent=`第 ${currentPage} / ${pageCount} 页`;
   if(pagePrev)pagePrev.disabled=currentPage<=1;
   if(pageNext)pageNext.disabled=currentPage>=pageCount;
  };
  cards.forEach(card=>card.querySelector('[data-group-toggle]')?.addEventListener('change',event=>{toggleCard(card,event.target.checked);renderOrder();}));
  filters?.querySelectorAll('[data-question-filter]').forEach(input=>input.addEventListener(input.type==='search'?'input':'change',()=>{currentPage=1;applyFilters();}));
  filters?.querySelector('[data-question-filter-reset]')?.addEventListener('click',()=>{filters.querySelectorAll('[data-question-filter]').forEach(input=>input.value='');currentPage=1;applyFilters();});
  pagePrev?.addEventListener('click',()=>{if(currentPage>1){currentPage-=1;applyFilters();}});
  pageNext?.addEventListener('click',()=>{currentPage+=1;applyFilters();});
  basket?.addEventListener('click',event=>{
   const button=event.target.closest('[data-basket-action]');if(!button)return;
   const index=order.indexOf(button.dataset.groupKey);if(index<0)return;
   if(button.dataset.basketAction==='remove'){const card=byKey.get(button.dataset.groupKey);if(card)toggleCard(card,false);}
   else if(button.dataset.basketAction==='up'&&index>0)[order[index-1],order[index]]=[order[index],order[index-1]];
   else if(button.dataset.basketAction==='down'&&index<order.length-1)[order[index+1],order[index]]=[order[index],order[index+1]];
   renderOrder();
  });
  form.querySelector('[data-clear-paper-selection]')?.addEventListener('click',()=>{cards.forEach(card=>toggleCard(card,false));renderOrder();});
  applyFilters();renderOrder();
 });
 document.querySelectorAll('.learning-solution').forEach(b=>b.addEventListener('click',async()=>{const out=b.nextElementSibling;try{let d=await request('solution',{wrong_id:b.dataset.id});if(d.solution_html){out.innerHTML=d.solution_html;if(window.renderMathInElement)window.renderMathInElement(out,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\[',right:'\\]',display:true},{left:'\\(',right:'\\)',display:false}],throwOnError:false,trust:false,ignoredTags:['script','noscript','style','textarea','pre','code']});}else{out.textContent=d.message+'；标准答案：'+JSON.stringify(d.answer)+'；解析：'+d.analysis;}}catch(e){out.textContent=e.message;}}));
})();
