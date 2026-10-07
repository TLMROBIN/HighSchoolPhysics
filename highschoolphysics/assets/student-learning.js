(()=>{
 'use strict';
 const request=async(action,p)=>{const r=await fetch('api/learning/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const d=await r.json();if(!r.ok||d.ok===false)throw Error(d.error?.message||d.message||'保存失败，请重试');return d.result||d;};
 const key=()=>globalThis.crypto?.randomUUID?.()||'request-'+Date.now()+'-'+Math.random().toString(36).slice(2);
 const payload=f=>{const p={};for(const[k,v]of new FormData(f)){if(k in p)p[k]=[].concat(p[k],v);else p[k]=v;}return p;};
 const labels={correct:'本次正确',wrong:'本次做错了',blank:'空白',pending:'答案尚待确认'};
 document.querySelectorAll('.student-filter-drawer').forEach(drawer=>{
  const storageKey='hsp-filter-sidebar-'+drawer.dataset.filterScope;
  try{const saved=localStorage.getItem(storageKey);drawer.open=saved===null?innerWidth>800:saved==='open';}catch{drawer.open=innerWidth>800;}
  drawer.addEventListener('toggle',()=>{try{localStorage.setItem(storageKey,drawer.open?'open':'closed');}catch{}});
 });
 document.querySelectorAll('.student-workspace .learning-form').forEach(f=>{
  const action=f.dataset.action;if(!['bank-start','bank-add-wrong','student-preferences'].includes(action))return;
  const requestKey=key();f.addEventListener('submit',async e=>{
   e.preventDefault();e.stopImmediatePropagation();const status=f.querySelector('[role=status]'),button=f.querySelector('button[type=submit],button:not([type])');button.disabled=true;status.textContent='正在保存…';
   try{const d=await request(action,{...payload(f),request_key:requestKey});location.assign(d.url||'app');}
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
