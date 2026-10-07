/* Read-only presentation of published evidence. Never infer knowledge mastery. */
(()=>{
 const root=document.querySelector('[data-review-workspace]');if(!root)return;
 const data=JSON.parse(root.querySelector('[data-review-data]').textContent);
 const find=name=>root.querySelector(`[data-review-${name}]`);
 const controls={class:find('class'),search:find('search'),state:find('state'),sort:find('sort'),size:find('size')};
 let view='students',page=1;
 const node=(tag,text='',cls='')=>{const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;};
 const stages=['未完成有效验证','验证 1 次','验证 2 次','三次已巩固'];
 const bar=values=>{
  const box=node('div','','review-bar'),total=values.reduce((a,b)=>a+b,0);
  box.setAttribute('role','img');box.setAttribute('aria-label',values.map((n,i)=>`${stages[i]} ${n} 条`).join('，'));
  values.forEach((n,i)=>{if(n){const part=node('span',n>0?String(n):'',`stage-${i}`);part.style.width=(100*n/total)+'%';part.title=`${stages[i]}：${n} 条`;box.append(part);}});
  if(!total)box.append(node('span','无错题记录','review-no-wrong'));return box;
 };
 const rate=(correct,total)=>{
  const box=node('div','','review-rate');
  if(!total){box.append(node('span','暂无已确认作答','review-muted'));return box;}
  const pct=100*correct/total,track=node('span','','review-rate-track'),fill=node('span');fill.style.width=pct+'%';track.append(fill);
  box.append(track,node('span',`${pct.toFixed(1)}% (${correct}/${total})`));return box;
 };
 const cell=value=>{const td=node('td');td.append(value instanceof Node?value:node('span',String(value)));return td;};
 const table=(headers,rows)=>{const wrap=node('div','','review-table-scroll'),t=node('table'),head=node('thead'),tr=node('tr');headers.forEach(text=>{const th=node('th',text);th.scope='col';tr.append(th);});head.append(tr);t.append(head);const body=node('tbody');rows.forEach(values=>{const row=node('tr');values.forEach(v=>row.append(cell(v)));body.append(row);});t.append(body);wrap.append(t);return wrap;};
 const stat=(label,value,cls='')=>{const el=node('span','',`review-stat ${cls}`);el.append(node('strong',String(value)),node('span',label));return el;};
 const topicEvidence=topics=>table(['知识点 / 不同题数','首次作答正确率','独立验证答对 / 次数','待确认记录','三次已巩固题数'],topics.map(t=>[`${t.tag} · ${t.questions} 题`,rate(t.correct,t.initial),`${t.verified} / ${t.verify}`,t.pending,t.consolidated]));
 const studentRow=s=>{
  const details=node('details','','review-student'),summary=node('summary'),identity=node('span','','review-identity');
  identity.append(node('strong',s.name),node('small',`${s.class_name}${s.student_no?' · '+s.student_no:''}`));
  const distribution=node('span','','review-distribution');distribution.append(bar(s.stages),node('small',`${s.wrong} 条错题记录 · ${s.stages[1]+s.stages[2]+s.stages[3]} 条已开始有效验证`));
  const counts=node('span','','review-row-counts');counts.append(stat('到期',s.due,s.due?'is-due':''),stat('待确认',s.pending,s.pending?'is-pending':''),stat('已巩固',s.consolidated));
  summary.append(identity,distribution,counts,node('span','查看详情','review-detail-label'));details.append(summary);
  const content=node('div','','review-detail');
  content.append(node('h3',`${s.name}的知识点证据`),node('p',s.stages.map((n,i)=>`${stages[i]} ${n} 条`).join(' · '),'review-stage-caption'));
  if(s.pending){const link=node('a','前往确认重做结果');link.href='#review';link.addEventListener('click',()=>{document.getElementById('review').open=true;});content.append(link);}
  if(s.topics.length)content.append(topicEvidence([...s.topics].sort((a,b)=>(a.initial?a.correct/a.initial:1)-(b.initial?b.correct/b.initial:1)||a.tag.localeCompare(b.tag,'zh-CN'))));
  else content.append(node('p','暂无带知识点标签的已发布作答记录。'));
  details.append(content);return details;
 };
 const selectedClass=()=>data.filter(s=>!controls.class.value||s.class_name===controls.class.value);
 const overview=students=>{
  const box=find('overview');box.replaceChildren();
  if(!students.length){box.append(node('p','暂无已发布考试的学生复习记录。发布考试结果后，这里会显示复习分布。','review-empty'));return;}
  const total=[0,0,0,0];students.forEach(s=>s.stages.forEach((v,i)=>{total[i]+=v;}));
  const counts=node('div','','review-overview-counts');counts.append(stat('有作答记录的学生',students.length),stat('有到期题的学生',students.filter(s=>s.due>0).length,'is-due'),stat('重做待确认记录',students.reduce((n,s)=>n+s.pending,0),'is-pending'),stat('三次已巩固记录',total[3]));box.append(counts);
  const chart=node('div','','review-overview-chart');chart.append(bar(total),node('p',total.map((n,i)=>`${stages[i]} ${n} 条`).join(' · ')));box.append(chart);
 };
 const aggregate=students=>{
  const groups=new Map();students.forEach(s=>s.topics.forEach(t=>{
   if(!groups.has(t.id))groups.set(t.id,{id:t.id,tag:t.tag,initial:0,correct:0,verify:0,verified:0,pending:0,consolidated:0,questions:0,members:[]});
   const g=groups.get(t.id);['initial','correct','verify','verified','pending','consolidated','questions'].forEach(key=>{g[key]+=t[key];});g.members.push({student:s,evidence:t});
  }));return [...groups.values()];
 };
 const topicRow=t=>{
  const details=node('details','','review-topic'),summary=node('summary'),identity=node('span','','review-identity');identity.append(node('strong',t.tag),node('small',`${t.members.length} 名学生 · ${t.questions} 个学生题目样本`));
  const measures=node('span','','review-topic-rates');const initial=node('span');initial.append(node('small','首次作答'),rate(t.correct,t.initial));const verify=node('span');verify.append(node('small','独立验证'),rate(t.verified,t.verify));measures.append(initial,verify);
  summary.append(identity,measures,node('span',`待确认 ${t.pending} · 已巩固 ${t.consolidated}`,'review-topic-counts'),node('span','查看学生','review-detail-label'));details.append(summary);
  const content=node('div','','review-detail');content.append(node('p','按首次作答正确率从低到高排列；这里只展示证据，不自动判定知识点已掌握。','review-note'));
  const members=[...t.members].sort((a,b)=>(a.evidence.initial?a.evidence.correct/a.evidence.initial:1)-(b.evidence.initial?b.evidence.correct/b.evidence.initial:1));
  content.append(table(['学生','不同题数','首次作答正确率','独立验证答对 / 次数','待确认','三次已巩固题数'],members.map(m=>{
   const b=node('button',`${m.student.class_name} · ${m.student.name}`,'review-student-link');b.type='button';b.addEventListener('click',()=>{
    switchView('students');controls.state.value='';controls.search.value=m.student.student_no||m.student.name;page=1;render();
    const target=[...find('list').children].find(el=>el.dataset.studentId===m.student.id);if(target){target.open=true;target.querySelector('summary').focus();target.scrollIntoView({block:'nearest'});}
   });const e=m.evidence;return[b,e.questions,rate(e.correct,e.initial),`${e.verified} / ${e.verify}`,e.pending,e.consolidated];
  })));details.append(content);return details;
 };
 const optionSets={
  students:{state:[['','全部学生'],['due','有到期题'],['pending','重做待确认'],['started','已开始有效验证'],['unstarted','尚无有效验证'],['consolidated','有已巩固题']],sort:[['due','到期题优先'],['pending','待确认优先'],['name','班级 / 姓名'],['consolidated','已巩固题优先']]},
  topics:{state:[['','全部知识点'],['weak','首次作答正确率低于 60%'],['pending','有待确认记录'],['unverified','暂无独立验证'],['consolidated','有已巩固题']],sort:[['weak','首次正确率从低到高'],['pending','待确认优先'],['name','知识点名称'],['coverage','涉及学生从多到少']]}
 };
 const switchView=next=>{
  view=next;page=1;controls.search.value='';controls.search.placeholder=view==='students'?'输入学生姓名或学号':'输入知识点名称';
  ['state','sort'].forEach(key=>{controls[key].replaceChildren(...optionSets[view][key].map(([value,label])=>{const o=node('option',label);o.value=value;return o;}));});
  root.querySelectorAll('[data-review-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.reviewView===view)));
 };
 const render=()=>{
  const scoped=selectedClass();overview(scoped);const query=controls.search.value.trim().toLocaleLowerCase(),state=controls.state.value,sort=controls.sort.value;
  let items=view==='students'?scoped.filter(s=>{
   if(query&&!`${s.name} ${s.student_no}`.toLocaleLowerCase().includes(query))return false;
   const started=s.stages[1]+s.stages[2]+s.stages[3];return !state||(state==='due'&&s.due>0)||(state==='pending'&&s.pending>0)||(state==='started'&&started>0)||(state==='unstarted'&&s.wrong>0&&started===0)||(state==='consolidated'&&s.consolidated>0);
  }):aggregate(scoped).filter(t=>(!query||t.tag.toLocaleLowerCase().includes(query))&&(!state||(state==='weak'&&t.initial>0&&t.correct/t.initial<.6)||(state==='pending'&&t.pending>0)||(state==='unverified'&&t.verify===0)||(state==='consolidated'&&t.consolidated>0)));
  items.sort((a,b)=>{
   if(sort==='weak'){const diff=(a.initial?a.correct/a.initial:2)-(b.initial?b.correct/b.initial:2);if(diff)return diff;}
   else if(sort==='coverage'){if(a.members.length!==b.members.length)return b.members.length-a.members.length;}
   else if(sort!=='name'&&a[sort]!==b[sort])return b[sort]-a[sort];
   return view==='students'?a.class_name.localeCompare(b.class_name,'zh-CN')||a.name.localeCompare(b.name,'zh-CN')||a.id.localeCompare(b.id):a.tag.localeCompare(b.tag,'zh-CN')||a.id.localeCompare(b.id);
  });
  const size=Number(controls.size.value),pages=Math.max(1,Math.ceil(items.length/size));page=Math.max(1,Math.min(page,pages));
  const list=find('list');list.replaceChildren();items.slice((page-1)*size,page*size).forEach(item=>{const row=view==='students'?studentRow(item):topicRow(item);if(view==='students')row.dataset.studentId=item.id;list.append(row);});
  if(!items.length)list.append(node('p',data.length?'没有符合条件的结果。可更换班级、搜索词或重置筛选。':'暂无已发布考试的学生复习记录。','review-empty'));
  find('count').textContent=`${view==='students'?'学生复习情况':'知识点掌握依据'} · 共 ${items.length} ${view==='students'?'名学生':'个知识点'}${items.length?`，当前显示 ${(page-1)*size+1}—${Math.min(page*size,items.length)}`:''}`;
  find('page-label').textContent=`第 ${page} / ${pages} 页`;
  root.querySelector('[data-review-page="prev"]').disabled=page===1;root.querySelector('[data-review-page="next"]').disabled=page===pages;
 };
 Object.values(controls).forEach(el=>el.addEventListener(el===controls.search?'input':'change',()=>{page=1;render();}));
 root.querySelectorAll('[data-review-view]').forEach(b=>b.addEventListener('click',()=>{switchView(b.dataset.reviewView);render();}));
 root.querySelectorAll('[data-review-page]').forEach(b=>b.addEventListener('click',()=>{page+=b.dataset.reviewPage==='prev'?-1:1;render();find('count').scrollIntoView({block:'nearest'});}));
 find('reset').addEventListener('click',()=>{controls.class.value='';switchView(view);render();});render();
})();
