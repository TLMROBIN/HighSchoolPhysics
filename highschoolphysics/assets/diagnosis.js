(() => {
  const key=()=>globalThis.crypto?.randomUUID?.()||'diagnosis-'+Date.now()+'-'+Math.random().toString(36).slice(2);
  const reports={knowledge_gap:'相关知识不记得或一直没弄懂',knowledge_retrieval:'知识记得，但当时没想到用它',condition:'题意或关键条件没理解清楚',model:'不知道对应哪种物理模型',plan:'知道规律，但步骤接不起来',execution:'列式、计算或检查结果出了问题',time:'没做到、时间不够或漏答',unsure:'说不清最先卡在哪里'};
  const legacy={condition:'题意与条件',model:'模型与规律',plan:'思路与步骤',execution:'列式、计算或检验',unsure:'说不清卡在哪里'};
  const corrections={forgot:'这一步的知识当时不记得了',never_understood:'这一步的概念一直没理解清楚',not_recalled:'知识记得，但没想到用在这里',missed:'漏看或忽略了关键条件',untranslated:'读到了条件，但不懂它的物理含义',model_unrecognized:'条件理解了，但没认出物理模型',chain_broken:'相关规律都知道，但步骤接不起来',formula_misused:'公式适用、对象或列式不对',arithmetic:'计算、符号或单位出错',unchecked:'结果检查没有发现问题',ran_out:'当时时间不够',not_reached:'当时没做到',omitted:'会做，但漏答了',unsure:'还无法确定'};
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  document.querySelectorAll('[data-diagnosis-wrong]').forEach(panel=>{
    const body=panel.querySelector('[data-diagnosis-body]'),target=panel.querySelector('[data-diagnosis-target]');let state=null,busy=false,generation=0;
    async function call(action,p={}){const r=await fetch('api/learning/diagnosis-'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({protocol_version:2,wrong_id:panel.dataset.diagnosisWrong,question_id:target.value,...p})}),d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error?.message||d.message||'操作失败，请重试');return d.result;}
    function event(name,p={}){return perform('event',{session_id:state.session_id,cursor:state.cursor,event:name,request_key:key(),...p});}
    function choose(result){
      state=result.state;
      if(!result.available){body.innerHTML='<p>'+esc(result.message)+'</p>';return;}
      const report=state?.self_report||'unsure',checked=state?.checked_count||0;
      body.innerHTML=`<form data-diagnosis-start><p>选择这次怎样找卡点。全程只需点选，不影响正常重做。</p><label>回想最初做这道题时，最接近你的情况<select name="self_report" ${checked?'disabled':''}>${Object.entries(reports).map(([k,v])=>`<option value="${k}" ${k===report?'selected':''}>${v}</option>`).join('')}</select></label><div class="diagnosis-actions"><button name="mode" value="quick">粗略诊断</button><button name="mode" value="deep" class="secondary">精细诊断</button></div><p class="diagnosis-note">粗略：重点排查 1—2 个环节，快速归类；精细：沿本题条件理解、模型与规律、步骤衔接、列式检验逐项排查。</p></form>${state?`<p class="diagnosis-note">已有${state.mode==='quick'?'粗略':'精细'}诊断记录${checked?'，已检查 '+checked+' 个环节；选择另一方式会保留已有选择':''}。</p><button class="secondary" data-diagnosis-resume>${state.status==='active'?'继续已有诊断':'查看已有结果'}</button>`:''}`;
    }
    function render(result){
      state=result.state;
      if(!result.available){body.innerHTML='<p>'+esc(result.message)+'</p>';return;}
      if(!state){choose(result);return;}
      const modern=state.protocol_version===2;
      const prior=state.prior_checks||0,total=state.total-prior;
      const progress=state.status==='active'?`本次 ${Math.min(state.cursor-prior+1,total)}/${total}${prior?'（保留之前 '+prior+' 个检查）':''}`:'已检查 '+(state.checked_count??state.findings.length)+' 个环节';
      let html=`<p class="diagnosis-progress">${state.mode==='quick'?'粗略诊断':'精细诊断'} · ${progress} · 最初情况：${esc((modern?reports:legacy)[state.self_report]||'待排查')}</p>`;
      if(!modern)html+='<p class="diagnosis-note">这是此前保存的检查记录，继续完成不会改写原记录。</p>';
      if(state.reflection){
        if(state.step)html+=`<p>刚才检查：${esc(state.step.prompt)}</p>`;
        html+=`<form data-diagnosis-locate><fieldset><legend>${esc(state.reflection.prompt)}</legend>${state.reflection.options.map(o=>`<label class="diagnosis-option"><input type="radio" name="reason" value="${esc(o.value)}" required><span>${esc(o.label)}</span></label>`).join('')}</fieldset><button>记录并继续定位</button></form><p class="diagnosis-note">你的回顾是一条线索，系统还会结合刚才的检查与提示反应。</p><button class="secondary" data-diagnosis-event="finish">结束诊断，继续重做</button>`;
      }else if(state.step){
        const s=state.step;html+=`<h4>${esc(s.stage)}</h4><p>${esc(s.prompt)}</p>`;
        const canAnswer=modern?s.can_answer:!s.answered;
        if(s.hint)html+=`<p class="diagnosis-hint">${s.hint_level===1?'线索提醒':'进一步说明'}：${esc(s.hint)}</p>`;
        if(canAnswer)html+=`<form data-diagnosis-answer><fieldset><legend>${s.answered?'收到提醒后，再试着选一次':'先不看提示，选择你认为合适的一项'}</legend>${s.options.map((o,i)=>`<label class="diagnosis-option"><input type="radio" name="answer" value="${i}" required><span>${esc(o)}</span></label>`).join('')}${modern?'<label class="diagnosis-option"><input type="radio" name="answer" value="-1" required><span>还说不清 / 不知道怎么选</span></label>':''}</fieldset><button>${s.answered?'再检查一次':'检查这一步'}</button></form>`;
        if(state.feedback){const f=state.feedback;html+=`<p class="diagnosis-feedback">${esc(f.message||(f.passed?'这一步已通过。':'这一步仍需检查。'))}${f.assisted?'（借助提示）':''}${f.explanation?esc(f.explanation):''}</p>`;}
        if(modern?s.can_hint:!s.answered&&s.can_hint)html+='<button class="secondary" data-diagnosis-event="hint">给我一点提醒</button>';
        if(modern?s.can_next:state.feedback)html+=`<button data-diagnosis-event="next">${state.cursor+1===state.total?'查看卡点分类':'检查下一环'}</button>`;
        html+='<button class="secondary" data-diagnosis-event="finish">结束诊断，查看已有线索</button>';
      }else if(modern){
        const s=state.summary;
        html+=`<h4>${s.category==='insufficient'?'本次尚未明确定位':'本次更接近：'+esc(s.label)}</h4><p class="diagnosis-note">${esc(s.strength)}</p><h4>为什么这样归类</h4>${s.evidence.length?'<ul>'+s.evidence.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':'<p>已完成的检查尚不足以区分具体错因，不强行归类。</p>'}${s.cautions.map(x=>'<p class="diagnosis-note">'+esc(x)+'</p>').join('')}<h4>下一步怎么做</h4><p>${esc(s.next_action)}</p><details><summary>查看本题思维链的检查情况</summary><ol class="diagnosis-chain">${s.trace.map(t=>`<li><strong>${esc(t.stage)}</strong>：${esc(t.label)}</li>`).join('')}</ol></details><p class="diagnosis-note">${esc(s.message)}</p>`;
        html+='<p>这个分类符合你当时的情况吗？</p><div class="diagnosis-actions"><button class="secondary" data-diagnosis-confirm="agree">比较符合</button><button class="secondary" data-diagnosis-confirm="different">不符合，我要更正</button><button class="secondary" data-diagnosis-confirm="unsure">还说不清</button></div>';
        if(state.confirmation)html+='<p role="status">已记录：'+esc({agree:'比较符合',different:'暂不采用这项定位',unsure:'尚不能确认'}[state.confirmation])+'</p>';
        if(state.confirmation==='different')html+=`<form data-diagnosis-revise><label>更接近你的情况<select name="reason">${Object.entries(corrections).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join('')}</select></label><button>保存我的更正</button></form>`;
      }else{
        html+='<h4>此前检查小结</h4><p>'+esc(state.message)+'</p><details><summary>查看各步依据</summary>'+state.findings.map(f=>`<p><strong>${esc(f.stage)}：${f.passed?'通过':'待检查'}</strong> ${esc(f.explanation)}</p>`).join('')+'</details>';
      }
      if(state.mode==='quick'&&!state.reflection)html+='<button class="secondary" data-diagnosis-event="deepen">继续精细诊断</button>';
      if(state.assisted)html+='<p class="diagnosis-note">本次已有提示或讲解，今天接着重做会记为学习练习；之后独立复习再验证掌握。</p>';
      body.innerHTML=html;
    }
    async function perform(action,p){if(busy)return;busy=true;target.disabled=true;body.querySelectorAll('button').forEach(b=>b.disabled=true);try{render(await call(action,p));}catch(error){let out=body.querySelector('[data-diagnosis-error]');if(!out){out=document.createElement('p');out.dataset.diagnosisError='';out.setAttribute('role','alert');body.append(out);}out.textContent=error.message;body.querySelectorAll('button').forEach(b=>b.disabled=false);}finally{busy=false;target.disabled=false;}}
    body.addEventListener('submit',e=>{e.preventDefault();const f=e.target;if(f.matches('[data-diagnosis-start]')){const p={mode:e.submitter.value,self_report:f.elements.self_report.value};if(state?.protocol_version===2)event('select-mode',p);else if(state){if(p.mode==='deep'&&state.mode==='quick')event('deepen');else render({available:true,state});}else perform('start',p);}else if(f.matches('[data-diagnosis-answer]'))event('answer',{answer:Number(f.elements.answer.value)});else if(f.matches('[data-diagnosis-locate]'))event('locate',{reason:f.elements.reason.value});else if(f.matches('[data-diagnosis-revise]'))event('revise',{reason:f.elements.reason.value});});
    body.addEventListener('click',e=>{if(e.target.closest('[data-diagnosis-resume]')){render({available:true,state});return;}const b=e.target.closest('[data-diagnosis-event],[data-diagnosis-confirm]');if(b)event(b.dataset.diagnosisEvent||'finish',{confirmation:b.dataset.diagnosisConfirm||''});});
    async function load(){const current=++generation;body.textContent='正在读取诊断…';try{const r=await call('state');if(current===generation)choose(r);}catch(error){if(current===generation)body.textContent=error.message;}}
    target.addEventListener('change',()=>{if(panel.open)load();});
    panel.addEventListener('toggle',()=>{if(panel.open)load();});
  });
})();
