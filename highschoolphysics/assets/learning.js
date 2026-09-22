/* Drafts remain until server acknowledgement; retries reuse the same request key. */
(()=>{
 const request=async(action,p)=>{const r=await fetch('api/learning/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const d=await r.json();if(!r.ok||d.ok===false)throw Error(d.error?.message||d.message||'提交失败，请重试');return d.result||d;};
 document.querySelectorAll('.learning-form').forEach(f=>{
 const action=f.dataset.action, status=f.querySelector('[role=status]');
 const draftKey='hsp-learning-'+action+'-'+(f.elements.wrong_id?.value||f.elements.assessment_id?.value||'');
 const payload=()=>{const p={};for(const [k,v]of new FormData(f)){if(k in p)p[k]=[].concat(p[k],v);else p[k]=v;}return p;};
 let key=globalThis.crypto?.randomUUID?.()||('request-'+Date.now()+'-'+Math.random().toString(36).slice(2));let image='';
 if(action==='submit'){
  try{let d=JSON.parse(sessionStorage.getItem(draftKey)||'null');if(d){key=d.request_key;f.querySelectorAll('[name=answer]').forEach(e=>{if(e.type==='checkbox'||e.type==='radio')e.checked=[].concat(d.answer||[]).includes(e.value);else e.value=d.answer||'';});}}catch{}
  f.addEventListener('input',()=>sessionStorage.setItem(draftKey,JSON.stringify({...payload(),request_key:key})));
 }
 const run=async(confirm=false)=>{if(action==='submit')sessionStorage.setItem(draftKey,JSON.stringify({...payload(),request_key:key}));const buttons=f.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);status.textContent='处理中…';try{const d=await request(action,{...payload(),image,request_key:key,confirm});status.textContent=d.message||(d.outcome?({'correct':'本次正确','wrong':'需再练','blank':'空白','pending':'待教师确认'}[d.outcome]+' · '+(d.purpose==='verify'?'独立验证':'学习练习，不计验证次数')):'已保存');if(d.preview){f.querySelector('.confirm-answers').hidden=false;}else if(action==='submit'){sessionStorage.removeItem(draftKey);const a=document.createElement('a');a.href='app';a.textContent=' 返回首页，查看下一题';status.append(a);f.querySelectorAll('input,textarea,button').forEach(e=>e.disabled=true);return;}else{const a=document.createElement('a');a.href=d.url||location.href;a.textContent=' 刷新查看结果';status.append(a);}}catch(e){status.textContent=e.message+'；输入已保留，可重试。';}buttons.forEach(b=>b.disabled=false);};
 f.querySelector('.question-image')?.addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;const bitmap=await createImageBitmap(file),canvas=document.createElement('canvas');const scale=Math.min(1,1500/bitmap.width);canvas.width=bitmap.width*scale;canvas.height=bitmap.height*scale;canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);image=canvas.toDataURL('image/png').split(',')[1];if(image.length>850000){image='';throw Error('图片较大，请裁剪到单题后再上传');}status.textContent='原题图已准备好';}catch(e){status.textContent=e.message;}});
 f.addEventListener('submit',e=>{e.preventDefault();run();});f.querySelector('.confirm-answers')?.addEventListener('click',()=>run(true));f.querySelector('.answers-file')?.addEventListener('change',async e=>{if(e.target.files[0])f.elements.csv.value=await e.target.files[0].text();});
 });
 document.querySelectorAll('.learning-solution').forEach(b=>b.addEventListener('click',async()=>{const out=b.nextElementSibling;try{let d=await request('solution',{wrong_id:b.dataset.id});out.textContent=d.message+'；标准答案：'+JSON.stringify(d.answer)+'；解析：'+d.analysis;}catch(e){out.textContent=e.message;}}));
})();
