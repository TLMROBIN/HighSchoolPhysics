(()=>{
 'use strict';
 const request=async(action,p)=>{const r=await fetch('api/learning/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const d=await r.json();if(!r.ok||d.ok===false)throw Error(d.error?.message||d.message||'保存失败，请重试');return d.result||d;};
 const key=()=>globalThis.crypto?.randomUUID?.()||'request-'+Date.now()+'-'+Math.random().toString(36).slice(2);
 const payload=f=>{const p={};for(const[k,v]of new FormData(f)){if(k in p)p[k]=[].concat(p[k],v);else p[k]=v;}return p;};
 const labels={correct:'本次正确',wrong:'本次做错了',blank:'空白',pending:'答案尚待确认'};
 document.querySelectorAll('[data-wrong-causes]').forEach(panel=>{
  const buttons=[...panel.querySelectorAll('[data-cause]')],status=panel.querySelector('[role=status]');let busy=false;
  panel.addEventListener('click',async e=>{
   const button=e.target.closest('[data-cause]');if(!button||busy)return;
   const code=button.dataset.cause,selected=buttons.filter(b=>b.getAttribute('aria-pressed')==='true').map(b=>b.dataset.cause);
   let causes=selected.includes(code)?selected.filter(x=>x!==code):[...selected,code];
   if(!selected.includes(code))causes=code==='unsure'?['unsure']:causes.filter(x=>x!=='unsure');
   busy=true;buttons.forEach(b=>b.disabled=true);status.textContent='正在保存…';
   try{
    const d=await request('wrong-causes',{wrong_id:panel.dataset.wrongCauses,scope_key:panel.dataset.scopeKey,revision:Number(panel.dataset.revision),causes,request_key:key()});
    panel.dataset.revision=d.revision;buttons.forEach(b=>b.setAttribute('aria-pressed',String(d.causes.includes(b.dataset.cause))));
    status.textContent=d.causes.length?'已保存：'+buttons.filter(b=>d.causes.includes(b.dataset.cause)).map(b=>b.textContent).join('、'):'已取消所有标记';
   }catch(err){status.textContent='保存未确认：'+err.message+'。请刷新后核对。';}
   finally{busy=false;buttons.forEach(b=>b.disabled=false);}
  });
 });
 const compactFilters=matchMedia('(max-width:1100px)');
 const viewportHeight=()=>document.documentElement.style.setProperty('--student-viewport-height',Math.min(innerHeight,globalThis.visualViewport?.height||innerHeight)+'px');
 viewportHeight();addEventListener('resize',viewportHeight);globalThis.visualViewport?.addEventListener('resize',viewportHeight);
 document.querySelectorAll('.student-filter-drawer').forEach(drawer=>{
  const storageKey='hsp-filter-sidebar-'+drawer.dataset.filterScope;
  const layout=drawer.closest('.student-library-layout'),backdrop=layout.querySelector('.student-filter-backdrop'),summary=drawer.querySelector(':scope>summary');
  try{const saved=localStorage.getItem(storageKey);drawer.open=!compactFilters.matches&&(saved===null||saved==='open');}catch{drawer.open=!compactFilters.matches;}
  const sync=()=>{backdrop.hidden=!(drawer.open&&compactFilters.matches);summary.setAttribute('aria-expanded',String(drawer.open));try{localStorage.setItem(storageKey,drawer.open?'open':'closed');}catch{}};
  const close=()=>{drawer.open=false;sync();summary.focus();};
  drawer.addEventListener('toggle',sync);backdrop.addEventListener('click',close);
  drawer.addEventListener('keydown',e=>{if(e.key==='Escape'&&drawer.open){e.preventDefault();close();}if(e.key==='Tab'&&drawer.open&&compactFilters.matches){const nodes=[summary,...drawer.querySelectorAll('input,select,button,a,details>summary')].filter(n=>n.getClientRects().length&&!n.disabled);const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
  compactFilters.addEventListener('change',()=>{if(compactFilters.matches)close();else sync();});sync();
 });
 document.querySelectorAll('.student-workspace .learning-form').forEach(f=>{
  const action=f.dataset.action;if(!['bank-start','bank-add-wrong','student-preferences','wrong-mastered'].includes(action))return;
  const requestKey=key();f.addEventListener('submit',async e=>{
   e.preventDefault();e.stopImmediatePropagation();if(action==='wrong-mastered'&&!confirm('确认已掌握这道题？确认后整道题将移出错题本，不再提示复习。首次作答和重做记录会保留。'))return;const status=f.querySelector('[role=status]'),button=f.querySelector('button[type=submit],button:not([type])');button.disabled=true;status.textContent='正在保存…';
   try{const d=await request(action,{...payload(f),request_key:requestKey,...(action==='wrong-mastered'?{confirmed:true}:{})});location.assign(d.url||'app');}
   catch(err){status.textContent=err.message;button.disabled=false;}
  });
 });
 document.querySelectorAll('.student-practice-form').forEach(f=>{
  const status=f.querySelector('[role=status]'),id=f.elements.wrong_id?.value||f.elements.trial_id?.value;
  const draft='hsp-student-practice-'+id;let requestKey=key();
  try{const saved=JSON.parse(sessionStorage.getItem(draft)||'null');if(saved){requestKey=saved.request_key;f.querySelectorAll('input[name],select[name]').forEach(input=>{if(input.type==='hidden')return;const value=saved[input.name];if(value===undefined)return;if(input.type==='checkbox'||input.type==='radio')input.checked=[].concat(value||[]).includes(input.value);else input.value=value;});}}catch{}
  const save=()=>{try{sessionStorage.setItem(draft,JSON.stringify({...payload(f),request_key:requestKey}));}catch{}};
  f.addEventListener('input',save);
  f.addEventListener('submit',async e=>{
   e.preventDefault();save();const buttons=[...f.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);status.textContent='正在保存…';
   const p={...payload(f),request_key:requestKey};if(e.submitter?.name==='self_outcome')p.self_outcome=e.submitter.value;
   try{
    const d=await request(f.dataset.action,p);sessionStorage.removeItem(draft);status.textContent=labels[d.outcome]||'已保存';if(d.parts)status.textContent+=' · '+d.parts.map(part=>part.label+'：'+labels[part.outcome]).join('；');
    if(p.self_outcome)status.textContent+=' · 学生自评';
    f.querySelectorAll('input,select,button').forEach(input=>input.disabled=true);
    const article=f.closest('.student-practice'),next=article.querySelector('.practice-next');next.hidden=false;next.textContent=f.dataset.action==='bank-submit'?'返回题库':(f.dataset.next.includes('completed=1')?'完成本轮复习':'下一题');
    if(f.dataset.action==='bank-submit'&&['wrong','blank'].includes(d.outcome)){
     const box=document.createElement('div'),text=document.createElement('p'),add=document.createElement('button'),skip=document.createElement('a'),feedback=document.createElement('p');
     box.className='student-add-wrong';text.textContent='是否将这道题加入错题本？';add.textContent='加入错题本';add.type='button';skip.href='app?module=bank';skip.textContent='暂不加入';feedback.setAttribute('role','status');box.append(text,add,skip,feedback);status.after(box);
     add.addEventListener('click',async()=>{add.disabled=true;try{await request('bank-add-wrong',{trial_id:d.trial_id});feedback.textContent='已加入错题本';}catch(err){feedback.textContent=err.message;add.disabled=false;}});
    }
    next.focus();
   }catch(err){status.textContent=err.message;buttons.forEach(b=>b.disabled=false);}
  });
 });
 document.querySelectorAll('[data-student-solution]').forEach(button=>button.addEventListener('click',async()=>{
  const article=button.closest('.student-practice'),out=article.querySelector('.solution-output');button.disabled=true;out.textContent='正在加载答案…';
  try{const action=button.dataset.studentSolution,p=action==='bank-solution'?{trial_id:button.dataset.id}:{wrong_id:button.dataset.id};const d=await request(action,p);
   if(d.solution_html){out.innerHTML=d.solution_html;window.HSPRenderQuestionMath?.(out);}else out.textContent='参考答案：'+(Array.isArray(d.answer)?d.answer.join('、'):String(d.answer||'尚未导入'))+'；解析：'+(d.analysis||'尚未导入');
   const self=article.querySelector('[data-self-controls],[data-group-controls]');if(self){self.hidden=!(d.can_self_report??d.solution_available??Boolean(d.solution_html||d.answer));}
   button.textContent='答案与解析已显示';
  }catch(err){out.textContent=err.message;button.disabled=false;}
 }));
})();
