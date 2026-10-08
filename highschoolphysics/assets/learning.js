/* Drafts remain until server acknowledgement; retries reuse the same request key. */
(()=>{
 document.querySelectorAll('.exam-stat-filter').forEach(form=>{
  form.addEventListener('submit',()=>{form.action='exams'+location.hash;});
  form.querySelector('[data-exam-select-all]')?.addEventListener('click',()=>{form.querySelectorAll('input[type=checkbox]').forEach(input=>{input.checked=true;});form.requestSubmit();});
  form.querySelectorAll('input[type=checkbox]').forEach(input=>input.addEventListener('change',()=>form.requestSubmit()));
 });
 const openExamQuestion=hash=>{
  if(!/^#exam-question-\d+$/.test(hash))return;
  const target=document.getElementById(hash.slice(1));if(!target)return;
  const studentNav=document.querySelector('.student-exam-nav');
  if(studentNav){
   const group=target.closest('.assessment-question-group');
   document.querySelectorAll('[id^="student-exam-group-"]').forEach(article=>{article.hidden=article!==group;});
   document.querySelector('.student-exam-empty').hidden=true;
   studentNav.querySelectorAll('[data-exam-question]').forEach(link=>link.setAttribute('aria-expanded',String(link.hash===hash)));
   const selected=studentNav.querySelector(`a[href="${hash}"]`),box=studentNav.querySelector('.student-exam-nav-scroll');
   if(selected){const cell=selected.closest('th');box.scrollLeft=Math.max(0,cell.offsetLeft-box.clientWidth/2+cell.offsetWidth/2);}
  }
  target.closest('.assessment-question-details').open=true;
  document.querySelectorAll('[data-exam-question]').forEach(link=>{if(link.hash===hash)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');});
  (studentNav?target.closest('.assessment-question-group'):target).scrollIntoView({block:'start'});target.focus({preventScroll:true});
 };
 const studentExamNav=document.querySelector('.student-exam-nav');
 if(studentExamNav){
  const updateExamNav=()=>{
   const top=document.querySelector('.topbar')?.getBoundingClientRect().height||0;
   studentExamNav.style.top=top+'px';
   document.querySelector('.learning').style.setProperty('--student-exam-scroll-offset',(top+studentExamNav.getBoundingClientRect().height+16)+'px');
  };
  new ResizeObserver(updateExamNav).observe(studentExamNav);
  const topbar=document.querySelector('.topbar');if(topbar)new ResizeObserver(updateExamNav).observe(topbar);
  updateExamNav();
 }
 document.querySelectorAll('[data-exam-question]').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();history.replaceState(null,'',link.hash);openExamQuestion(link.hash);}));
 window.addEventListener('hashchange',()=>openExamQuestion(location.hash));
 openExamQuestion(location.hash);
 // Close sibling categories even on WebViews without support for details[name].
 document.querySelectorAll('.answer-category').forEach(category=>category.addEventListener('toggle',()=>{
  if(category.open)category.parentElement.querySelectorAll('.answer-category').forEach(other=>{if(other!==category)other.open=false;});
 }));
 document.querySelectorAll('[data-tag-tab]').forEach(button=>button.addEventListener('click',()=>{
  document.querySelectorAll('[data-tag-panel]').forEach(panel=>{panel.hidden=panel.dataset.tagPanel!==button.dataset.tagTab;});
  document.querySelectorAll('[data-tag-tab]').forEach(tab=>tab.setAttribute('aria-pressed',String(tab===button)));
  document.getElementById('exam-tag-statistics')?.scrollIntoView({block:'start'});
 }));
 document.querySelectorAll('[data-tag-panel]').forEach(panel=>{
  const box=panel.querySelector('.tag-chart-scroll'),range=panel.querySelector('[data-chart-scroll-range]');if(!box||!range)return;
  const update=()=>{const max=Math.max(0,box.scrollWidth-box.clientWidth);range.max=max;range.value=box.scrollLeft;range.disabled=max===0;panel.querySelectorAll('[data-chart-scroll]').forEach(button=>{button.disabled=max===0||(button.dataset.chartScroll==='left'?box.scrollLeft<=0:box.scrollLeft>=max-1);});};
  range.addEventListener('input',()=>{box.scrollLeft=Number(range.value);update();});
  box.addEventListener('scroll',update,{passive:true});
  panel.querySelectorAll('[data-chart-scroll]').forEach(button=>button.addEventListener('click',()=>{box.scrollBy({left:(button.dataset.chartScroll==='left'?-1:1)*box.clientWidth*.7,behavior:'smooth'});}));
  new ResizeObserver(update).observe(box);update();
 });
 document.querySelectorAll('[data-card-viewer]').forEach(viewer=>{
  const viewport=viewer.querySelector('.review-card-viewport'),img=viewport.querySelector('img'),output=viewer.querySelector('[data-viewer-scale]');
  let scale=1,base=1,x=0,y=0,drag=null;
  const paint=()=>{img.style.transform=`translate(${x}px,${y}px) scale(${scale})`;output.textContent=Math.round(scale/base*100)+'%';};
  const fit=()=>{if(!img.naturalWidth||!viewport.clientWidth)return;base=Math.min(viewport.clientWidth/img.naturalWidth,viewport.clientHeight/img.naturalHeight);scale=base;x=(viewport.clientWidth-img.naturalWidth*scale)/2;y=(viewport.clientHeight-img.naturalHeight*scale)/2;paint();};
  const zoom=(factor,cx=viewport.clientWidth/2,cy=viewport.clientHeight/2)=>{const next=Math.max(base*.5,Math.min(base*8,scale*factor)),ratio=next/scale;x=cx-(cx-x)*ratio;y=cy-(cy-y)*ratio;scale=next;paint();};
  img.addEventListener('load',fit);if(img.complete)fit();viewer.closest('.exam-card-details')?.addEventListener('toggle',e=>{if(e.target.open)fit();});
  viewer.querySelector('[data-viewer-page]').addEventListener('change',e=>{img.src=e.target.value;});
  viewer.querySelectorAll('[data-viewer-zoom]').forEach(button=>button.addEventListener('click',()=>zoom(button.dataset.viewerZoom==='in'?1.25:.8)));
  viewer.querySelector('[data-viewer-reset]').addEventListener('click',fit);
  viewport.addEventListener('pointerdown',e=>{if(e.button!==0)return;drag={id:e.pointerId,x:e.clientX,y:e.clientY};viewport.setPointerCapture(e.pointerId);viewport.classList.add('dragging');});
  viewport.addEventListener('pointermove',e=>{if(!drag||drag.id!==e.pointerId)return;x+=e.clientX-drag.x;y+=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;paint();});
  const stop=()=>{drag=null;viewport.classList.remove('dragging');};
  viewport.addEventListener('pointerup',stop);viewport.addEventListener('pointercancel',stop);viewport.addEventListener('lostpointercapture',stop);
  viewport.addEventListener('wheel',e=>{e.preventDefault();const rect=viewport.getBoundingClientRect();zoom(e.deltaY<0?1.1:1/1.1,e.clientX-rect.left,e.clientY-rect.top);},{passive:false});
  viewport.addEventListener('keydown',e=>{if(e.key==='+'||e.key==='=')zoom(1.25);else if(e.key==='-')zoom(.8);else if(e.key==='ArrowLeft')x+=30;else if(e.key==='ArrowRight')x-=30;else if(e.key==='ArrowUp')y+=30;else if(e.key==='ArrowDown')y-=30;else return;e.preventDefault();paint();});
  new ResizeObserver(()=>{if(scale===base)fit();}).observe(viewport);
 });
 const request=async(action,p)=>{const r=await fetch('api/learning/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const d=await r.json();if(!r.ok||d.ok===false)throw Error(d.error?.message||d.message||'提交失败，请重试');return d.result||d;};
 const fileBase64=file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('文件读取失败'));reader.readAsDataURL(file);});
 const upload=async(action,p)=>{const text=JSON.stringify(p);if(new TextEncoder().encode(text).length<700000)return request(action,p);let uploadId='';const size=200000,total=Math.ceil(text.length/size);for(let index=0;index<total;index++){const result=await request('response-upload-chunk',{upload_id:uploadId,index,total,text:text.slice(index*size,(index+1)*size)});uploadId=result.upload_id;}return request(action,{upload_id:uploadId});};
 document.querySelectorAll('[data-exam-grade]').forEach(select=>{const form=select.closest('form'),labels=[...form.querySelectorAll('[data-exam-classes] label[data-grade]')];const update=()=>{labels.forEach(label=>{const checkbox=label.querySelector('input');const visible=label.dataset.grade===select.value;label.hidden=!visible;checkbox.disabled=!visible;if(!visible)checkbox.checked=false;});const count=labels.filter(label=>!label.hidden&&label.querySelector('input').checked).length;form.querySelector('[data-exam-scope]').textContent=count?'已选择 '+count+' 个班级，默认这些班级全部学生参加考试。':'未选择班级，默认'+select.value+'全年级全部学生参加考试。';};select.addEventListener('change',update);labels.forEach(label=>label.addEventListener('change',update));update();});
 document.querySelectorAll('.student-answer-cards').forEach(section=>{
  const classFilter=section.querySelector('[data-card-class]'),search=section.querySelector('[data-card-search]'),studentSelect=section.querySelector('[data-card-student]'),status=section.querySelector('[data-card-selection-status]');
  const panels=[...section.querySelectorAll('[data-student-id]')];
  const showStudent=()=>{
   panels.forEach(panel=>panel.hidden=panel.dataset.studentId!==studentSelect.value);
   status.textContent=studentSelect.value?'已显示所选学生的答题卡和作答记录。':(studentSelect.options.length>1?'匹配 '+(studentSelect.options.length-1)+' 位学生，请选择学生查看。':'未找到匹配的学生，请调整班级或姓名。');
  };
  const filterStudents=()=>{
   const current=studentSelect.value,query=search.value.trim().toLocaleLowerCase();
   const matches=panels.filter(panel=>(!classFilter.value||panel.dataset.className===classFilter.value)&&panel.dataset.studentName.toLocaleLowerCase().includes(query));
   studentSelect.replaceChildren(new Option(matches.length?'请选择学生':'没有匹配的学生',''));
   matches.forEach(panel=>studentSelect.add(new Option(panel.dataset.studentName+' · '+panel.dataset.className,panel.dataset.studentId)));
   if(matches.some(panel=>panel.dataset.studentId===current))studentSelect.value=current;
   else if(query&&matches.length===1)studentSelect.value=matches[0].dataset.studentId;
   showStudent();
  };
  classFilter.addEventListener('change',filterStudents);search.addEventListener('input',filterStudents);studentSelect.addEventListener('change',showStudent);
  filterStudents();
  const selectLinkedStudent=id=>{const panel=panels.find(p=>p.dataset.studentId===id);if(!panel)return;classFilter.value=panel.dataset.className;search.value='';filterStudents();studentSelect.value=id;showStudent();section.scrollIntoView({block:'start'});};
  document.querySelectorAll('[data-show-student-card]').forEach(link=>link.addEventListener('click',e=>{e.preventDefault();selectLinkedStudent(link.dataset.showStudentCard);}));
  const linked=panels.find(panel=>'#'+panel.id===location.hash);
  if(linked){studentSelect.value=linked.dataset.studentId;showStudent();}
 });
 document.querySelectorAll('.learning-form').forEach(f=>{
  const all=f.querySelector('[data-bulk-all]');if(!all)return;
  const items=[...f.querySelectorAll('[data-bulk-item]')],count=f.querySelector('[data-bulk-count]'),button=f.querySelector('[data-bulk-submit]');
  const update=()=>{const selected=items.filter(i=>i.checked).length;all.checked=selected===items.length;all.indeterminate=selected>0&&selected<items.length;count.textContent='已选'+selected+(f.dataset.action==='missing-students-batch'?'人':'项');button.disabled=selected===0;};
  all.addEventListener('change',()=>{items.forEach(i=>{i.checked=all.checked;});update();});items.forEach(i=>i.addEventListener('change',update));update();
 });
 document.querySelectorAll('.learning-form').forEach(f=>{
 const action=f.dataset.action, status=f.querySelector('[role=status]');
 if(action==='question'){
  const fill=f.querySelector('[data-fill-rule]'),quantity=f.querySelector('[data-quantity-rule]');
  const updateRule=()=>{if(!fill)return;const enabled=f.elements.question_type.value==='fill';fill.hidden=!enabled;fill.disabled=!enabled;const numeric=enabled&&f.elements.fill_match.value==='numeric_quantity';quantity.hidden=!numeric;quantity.querySelectorAll('input,select').forEach(input=>input.disabled=!numeric);};
  f.elements.question_type.addEventListener('change',updateRule);f.elements.fill_match.addEventListener('change',updateRule);updateRule();
 }
 const outcomeLabel=x=>({correct:'正确',wrong:'错误',blank:'空白',pending:'待确认'}[x]||x);
 const reasonLabel=x=>({external_conflict:'外部结果与规则不一致',external_confirmation:'外部结论需要教师确认',external_pending:'外部记录尚未确认',answer_rule:'规则无法确定',invalid_options:'选项无效或单选多选',numeric_format:'数值格式待确认',unit_required:'缺少必填单位',unit_unknown:'单位暂不支持',unit_mismatch:'单位量纲不一致',score_maximum_missing:'仅得分，未提供满分',unit_conversion_disabled:'本题未允许单位换算',precision_review:'有效数字需教师核对'}[x]||'');
 let previewToken='',scanJobId='',scanFiles=[];let scanBusy=false,fileBusy=false;
 const showScanProgress=(d)=>{const box=f.querySelector('.scan-progress');if(!box)return;box.hidden=false;const p=d.progress||{};const bar=box.querySelector('progress');if(typeof p.percent==='number')bar.value=Math.max(0,Math.min(100,p.percent));else bar.removeAttribute('value');box.dataset.state=d.status||'running';box.querySelector('.scan-progress-label').textContent=d.status==='failed'?'识别失败':(d.already_saved?'识别结果已保存':(p.label||'正在识别'));box.querySelector('.scan-progress-count').textContent=p.total_pages?('已完成 '+p.processed_pages+' / '+p.total_pages+' 页'+(p.current_page&&d.status==='running'?' · 当前第 '+p.current_page+' 页':'')+(typeof p.percent==='number'?' · '+p.percent+'%':'')):'等待获取总页数';};

 const previewOutput=f.querySelector('.response-preview');
 const renderPreview=d=>{if(!previewOutput)return;previewOutput.replaceChildren();if(d.records){const table=document.createElement('table');const head=table.insertRow();['学生 / 作答序号','原始答案','规范答案','规则建议','外部结果','得分 / 满分','有效结果 / 异常'].forEach(x=>{const th=document.createElement('th');th.textContent=x;head.append(th);});d.records.forEach(r=>{const row=table.insertRow();[r.student_name+' / '+r.number,r.raw_answer||(r.score_only?'未导入答案（仅得分）':'空白'),r.normalized_answer||(r.score_only?'未导入答案（仅得分）':'空白'),outcomeLabel(r.proposed_outcome),r.supplied_outcome?outcomeLabel(r.supplied_outcome):'未提供',r.score==null?'—':String(r.score)+(r.max_score==null?'':' / '+r.max_score),outcomeLabel(r.outcome)+' '+reasonLabel(r.category)].forEach(x=>row.insertCell().textContent=x);});const scroll=document.createElement('div');scroll.style.overflowX='auto';scroll.append(table);previewOutput.append(scroll);}if(d.impact){const impact=d.impact;[['首次答案',impact.initial_answer],['原有效答案 → 新答案',impact.old_answer+' → '+impact.new_answer],['原结果 → 新结果',outcomeLabel(impact.old_outcome)+' → '+outcomeLabel(impact.new_outcome)],['其他有效错误来源',impact.other_wrong_sources],['复习影响',impact.practice],['统计影响',impact.statistics]].forEach(([label,value])=>{const line=document.createElement('p');line.textContent=label+'：'+value;previewOutput.append(line);});}};
 const draftKey='hsp-learning-'+action+'-'+(f.elements.wrong_id?.value||f.elements.assessment_id?.value||'');
 const payload=()=>{const p={};for(const [k,v]of new FormData(f)){if(v instanceof File)continue;if(k in p)p[k]=[].concat(p[k],v);else p[k]=v;}return p;};
 let key=globalThis.crypto?.randomUUID?.()||('request-'+Date.now()+'-'+Math.random().toString(36).slice(2));let image='';
 if(action==='submit'){
  try{let d=JSON.parse(sessionStorage.getItem(draftKey)||'null');if(d){key=d.request_key;f.querySelectorAll('[name=answer]').forEach(e=>{if(e.type==='checkbox'||e.type==='radio')e.checked=[].concat(d.answer||[]).includes(e.value);else e.value=d.answer||'';});}}catch{}
  f.addEventListener('input',()=>sessionStorage.setItem(draftKey,JSON.stringify({...payload(),request_key:key})));
 }
 f.addEventListener('input',()=>{if(['answers','scan-upload','response-correct','response-review','response-review-batch'].includes(action)){previewToken='';key=globalThis.crypto?.randomUUID?.()||('request-'+Date.now()+'-'+Math.random().toString(36).slice(2));f.querySelector('.confirm-answers')?.setAttribute('hidden','');previewOutput?.replaceChildren();const progress=f.querySelector('.scan-progress');if(progress)progress.hidden=true;scanJobId='';}});
 const run=async(confirm=false)=>{if(action==='submit')sessionStorage.setItem(draftKey,JSON.stringify({...payload(),request_key:key}));if(action==='assessment'&&!f.elements.paper_id?.value&&!JSON.parse(f.dataset.paperQuestionOrder||'[]').length){status.textContent='先从题库中选择至少一道完整题目。';return;}const buttons=f.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);const scanInput=f.querySelector('.response-scan-files');if(scanInput)scanInput.disabled=true;status.textContent='处理中…';try{const requestPayload=payload();if(action==='response-review-batch'){requestPayload.records=[...f.querySelectorAll('[data-review-row]')].filter(row=>row.querySelector('[data-bulk-item]').checked).map(row=>({response_id:row.dataset.responseId,expected_decision_id:row.dataset.decisionId,answer:row.querySelector('[name=answer]').value,outcome:row.querySelector('[name=outcome]').value}));delete requestPayload.answer;delete requestPayload.outcome;if(!requestPayload.records.length)throw Error('请勾选要确认的作答');if(requestPayload.records.some(r=>!r.outcome))throw Error('请为每条已勾选作答选择核对后的结果');}if(action==='missing-students-batch'&&!requestPayload.student_ids)throw Error('请勾选要确认缺考的学生');if(action==='assessment')requestPayload.questions=JSON.parse(f.dataset.paperQuestionOrder||'[]');let d;if(fileBusy||scanBusy)throw Error('文件正在处理，请稍候');if(action==='scan-upload'){if(confirm){d=await request('answers',{assessment_id:requestPayload.assessment_id,scan_job_id:scanJobId,confirm:true,preview_token:previewToken});}else{const files=[...f.querySelector('.response-scan-files').files];if(!files.length||files.length>30||files.reduce((sum,file)=>sum+file.size,0)>20*1024*1024)throw Error('请选择最多30个文件，总大小不超过20MB');scanBusy=true;showScanProgress({progress:{label:'上传扫描件'}});try{scanFiles=await Promise.all(files.map(async file=>({name:file.name,data:await fileBase64(file)})));d=await upload('scan-upload',{assessment_id:requestPayload.assessment_id,files:scanFiles,request_key:key});scanJobId=d.job_id;sessionStorage.setItem('hsp-scan-'+requestPayload.assessment_id,scanJobId);await pollScan();return;}finally{scanBusy=false;if(scanInput)scanInput.disabled=false;buttons.forEach(button=>button.disabled=false);}}}else{d=await upload(action,{...requestPayload,image,request_key:key,confirm,preview_token:previewToken});}status.textContent=d.message||(d.outcome?({'correct':'本次正确','wrong':'需再练','blank':'空白','pending':'待教师确认'}[d.outcome]+' · '+(d.purpose==='verify'?'独立验证':'学习练习，不计验证次数')):'已保存');if(d.preview){previewToken=d.preview_token;renderPreview(d);const confirmButton=f.querySelector('.confirm-answers');if(confirmButton)confirmButton.hidden=false;}else if(action==='submit'){sessionStorage.removeItem(draftKey);const a=document.createElement('a');a.href='app';a.textContent=' 返回首页，查看下一题';status.append(a);f.querySelectorAll('input,textarea,button').forEach(e=>e.disabled=true);return;}else{if(action==='scan-upload')sessionStorage.removeItem('hsp-scan-'+f.elements.assessment_id.value);f.querySelector('.confirm-answers')?.setAttribute('hidden','');const a=document.createElement('a');a.href=d.url||location.href;a.textContent=['answers','scan-upload','response-review','response-review-batch','missing-students-batch','response-correct','publish'].includes(action)?' 刷新查看已保存结果':' 查看创建结果';status.append(a);}}catch(e){status.textContent=e.message+'；输入已保留，可重试。';}if(scanInput)scanInput.disabled=false;buttons.forEach(b=>b.disabled=false);};
 const pollScan=async()=>{for(;;){const d=await request('scan-status',{assessment_id:f.elements.assessment_id.value,job_id:scanJobId});showScanProgress(d);if(d.status==='failed'){sessionStorage.removeItem('hsp-scan-'+f.elements.assessment_id.value);throw Error(d.error||'识别失败');}if(d.status==='completed'){previewToken=d.preview_token;renderPreview(d);f.querySelector('.confirm-answers').hidden=!!d.already_saved;if(d.already_saved)sessionStorage.removeItem('hsp-scan-'+f.elements.assessment_id.value);status.textContent=d.message;return;}status.textContent=d.status==='queued'?'扫描任务等待识别…':'正在识别学生、提取答案与自动批改…';await new Promise(resolve=>setTimeout(resolve,5000));}};
 if(action==='scan-upload'){scanJobId=sessionStorage.getItem('hsp-scan-'+f.elements.assessment_id.value)||'';if(scanJobId){scanBusy=true;f.querySelector('.response-scan-files').disabled=true;f.querySelectorAll('button').forEach(button=>button.disabled=true);pollScan().catch(error=>status.textContent=error.message).finally(()=>{scanBusy=false;f.querySelector('.response-scan-files').disabled=false;f.querySelectorAll('button').forEach(button=>button.disabled=false);});}}
 f.querySelector('[data-response-template]')?.addEventListener('click',()=>{const data='\ufeff学生姓名,题号,学生答案,得分,满分,班级,结果\r\n';const url=URL.createObjectURL(new Blob([data],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='作答情况导入模板.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
 f.querySelector('.question-image')?.addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;const bitmap=await createImageBitmap(file),canvas=document.createElement('canvas');const scale=Math.min(1,1500/bitmap.width);canvas.width=bitmap.width*scale;canvas.height=bitmap.height*scale;canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);image=canvas.toDataURL('image/png').split(',')[1];if(image.length>850000){image='';throw Error('图片较大，请裁剪到单题后再上传');}status.textContent='原题图已准备好';}catch(e){status.textContent=e.message;}});
 f.addEventListener('submit',e=>{e.preventDefault();run();});f.querySelector('.confirm-answers')?.addEventListener('click',()=>run(true));f.querySelector('.answers-file')?.addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;fileBusy=true;status.textContent='正在读取表格…';try{if(file.size>8*1024*1024)throw Error('表格文件需小于8MB');const d=await upload('answers-file',{file_name:file.name,file_data:await fileBase64(file)});f.elements.csv.value=d.csv;f.elements.csv.dispatchEvent(new Event('input',{bubbles:true}));status.textContent='表格已读取，请预览';}catch(error){status.textContent=error.message;}finally{fileBusy=false;}});
 });
 document.querySelectorAll('.response-history').forEach(button=>button.addEventListener('click',async()=>{const out=button.nextElementSibling;try{const d=await request('response-history',{response_id:button.dataset.id});out.replaceChildren();d.evidence.forEach(e=>{const p=document.createElement('p');p.textContent='原始证据：'+(e.raw_answer??'历史未保留')+(e.imported_score==null?'':' · 得分：'+e.imported_score+(e.imported_max_score==null?'':' / '+e.imported_max_score))+' · 来源：'+(e.source_name||({legacy:'历史证据',teacher_correction:'教师更正',manual_import:'人工录入'}[e.extraction_method]||e.extraction_method))+' · 行：'+(e.source_row||'未记录');out.append(p);if(e.source_asset_id){const link=document.createElement('a');link.href='exam-media?id='+encodeURIComponent(e.source_asset_id);link.textContent='查看原答题图';out.append(link);}});d.decisions.forEach(r=>{const p=document.createElement('p');p.textContent=new Date(r.created_at).toLocaleString('zh-CN')+' · '+r.answer+' · '+({correct:'正确',wrong:'错误',blank:'空白',pending:'待确认'}[r.outcome]||r.outcome)+' · '+({legacy:'历史确认',rule:'规则核对',external_rule_agreement:'外部与规则一致',teacher_review:'教师复核',teacher_correction:'教师更正'}[r.method]||r.method)+' · '+r.reason;out.append(p);});}catch(e){out.textContent=e.message;}}));
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
    const difficulty=card.querySelector('.question-difficulty-list');if(difficulty)item.append(difficulty.cloneNode(true));
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
