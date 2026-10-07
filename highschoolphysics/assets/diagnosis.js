(() => {
  const requestKey=()=>globalThis.crypto?.randomUUID?.()||'diagnosis-'+Date.now()+'-'+Math.random().toString(36).slice(2);
  const labels={condition:'题意与条件',model:'模型与规律',plan:'思路与步骤',execution:'列式、计算或检验',unsure:'说不清卡在哪里'};
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  document.querySelectorAll('[data-diagnosis-wrong]').forEach(panel=>{
    const body=panel.querySelector('[data-diagnosis-body]'),target=panel.querySelector('[data-diagnosis-target]');
    let state=null,busy=false,generation=0;
    async function call(action,p={}) {
      const response=await fetch('api/learning/diagnosis-'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({wrong_id:panel.dataset.diagnosisWrong,question_id:target.value,...p})});
      const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error?.message||result.message||'操作失败');return result.result;
    }
    function render(result) {
      state=result.state;
      if(!result.available){body.innerHTML='<p>'+esc(result.message)+'</p>';return;}
      if(!state){body.innerHTML=`<form data-diagnosis-start><label>回想第一次做题，最接近你的情况<select name="self_report">${Object.entries(labels).map(([k,v])=>`<option value="${k}" ${k==='unsure'?'selected':''}>${v}</option>`).join('')}</select></label><label>当时想到了什么、停在哪一步？（可不填）<textarea name="note" maxlength="300" rows="2" placeholder="用一句话记下当时的思路"></textarea></label><div class="diagnosis-actions"><button name="mode" value="quick">快速定位</button><button name="mode" value="deep" class="secondary">深入诊断</button></div><p class="diagnosis-note">先不看解析；不确定也可以选“说不清”。</p></form>`;return;}
      let html=`<p class="diagnosis-progress">${state.mode==='quick'?'快速定位':'深入诊断'} · ${Math.min(state.cursor+1,state.total)}/${state.total} · 自我判断：${esc(labels[state.self_report])}</p>`;
      if(state.note)html+=`<details><summary>当时的思路</summary><p>${esc(state.note)}</p></details>`;
      if(state.confirmation)html+=`<p class="diagnosis-note">已记录你的反馈：${esc({agree:'比较符合',different:'不太符合',unsure:'还说不清'}[state.confirmation])}</p>`;
      if(state.step){const s=state.step;html+=`<h4>${esc(s.stage)}</h4><p>${esc(s.prompt)}</p>`;
        if(!s.answered)html+=`<form data-diagnosis-answer><fieldset><legend>选择最符合物理规律的一项</legend>${s.options.map((o,i)=>`<label class="diagnosis-option"><input type="radio" name="answer" value="${i}" required><span>${esc(o)}</span></label>`).join('')}</fieldset><button>检查这一步</button></form>`;
        if(s.hint)html+=`<p class="diagnosis-hint">提示 ${s.hint_level}：${esc(s.hint)}</p>`;
        if(state.feedback)html+=`<p class="diagnosis-feedback">${state.feedback.passed?'这一步已通过':'这一步还需要检查'}${state.feedback.assisted?'（看过提示）':''}。${esc(state.feedback.explanation)}</p><button data-diagnosis-event="next">${state.cursor+1===state.total?'查看诊断小结':'继续下一步'}</button>`;
        if(!s.answered&&s.can_hint)html+='<button class="secondary" data-diagnosis-event="hint">给我一点提示</button>';
        html+='<button class="secondary" data-diagnosis-event="finish">结束诊断，继续重做</button>';
      } else {
        const problems=state.findings.filter(f=>!f.passed||f.assisted);
        html+=`<h4>${problems.length?'建议优先回看的环节':'本次检查小结'}</h4><p>${!state.findings.length?'本次尚未完成检查，暂无足够诊断依据，可以直接重做。':problems.length?problems.map(f=>esc(f.stage)+(f.assisted?'（借助提示）':'')).join('、'):'已检查的步骤暂无明显困难，仍需通过独立重做验证。'}</p>`;
        if(state.status==='ended')html+='<p class="diagnosis-note">本次提前结束，定位依据还不完整。</p>';
        html+='<p>'+esc(state.message)+'</p><details><summary>查看各步依据</summary>'+state.findings.map(f=>`<p><strong>${esc(f.stage)}：${f.passed?'通过':'待检查'}</strong> ${esc(f.explanation)}</p>`).join('')+'</details>';
        html+='<p>这个定位符合你的真实卡点吗？</p><div class="diagnosis-actions"><button class="secondary" data-diagnosis-confirm="agree">比较符合</button><button class="secondary" data-diagnosis-confirm="different">不太符合</button><button class="secondary" data-diagnosis-confirm="unsure">还说不清</button></div>';
      }
      if(state.mode==='quick')html+='<button class="secondary" data-diagnosis-event="deepen">改为深入诊断</button>';
      if(state.assisted)html+='<p class="diagnosis-note">本次诊断提供了思路帮助，今天接着重做会记为学习练习，独立复习再验证掌握。</p>';
      body.innerHTML=html;
    }
    async function perform(action,p) {
      if(busy)return;busy=true;target.disabled=true;
      body.querySelectorAll('button').forEach(b=>b.disabled=true);
      try{render(await call(action,p));}catch(e){let output=body.querySelector('[data-diagnosis-error]');if(!output){output=document.createElement('p');output.dataset.diagnosisError='';output.setAttribute('role','alert');body.append(output);}output.textContent=e.message;body.querySelectorAll('button').forEach(b=>b.disabled=false);}finally{busy=false;target.disabled=false;}
    }
    body.addEventListener('submit',e=>{e.preventDefault();const form=e.target;if(form.matches('[data-diagnosis-start]'))perform('start',{mode:e.submitter.value,self_report:form.elements.self_report.value,note:form.elements.note.value});else if(form.matches('[data-diagnosis-answer]'))perform('event',{session_id:state.session_id,event:'answer',answer:Number(form.elements.answer.value),cursor:state.cursor,request_key:requestKey()});});
    body.addEventListener('click',e=>{const b=e.target.closest('[data-diagnosis-event],[data-diagnosis-confirm]');if(!b)return;perform('event',{session_id:state.session_id,event:b.dataset.diagnosisEvent||'finish',confirmation:b.dataset.diagnosisConfirm||'',cursor:state.cursor,request_key:requestKey()});});
    async function load(){const current=++generation;body.textContent='正在读取诊断…';try{const r=await call('state');if(current===generation)render(r);}catch(e){if(current===generation)body.textContent=e.message;}}
    target.addEventListener('change',load);load();
  });
})();
