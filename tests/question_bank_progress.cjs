// Run with: node tests/question_bank_progress.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('highschoolphysics/assets/question-bank.js', 'utf8')
  .replace("  $('select-all').addEventListener", "  listing={scope_question_ids:['q1','q2','q3']};\n  $('select-all').addEventListener")
  .replace('  (async()=>{try{catalog=', '  return; (async()=>{try{catalog=');

async function checkProgress() {
  const nodes = new Map(), timers = [], storage = new Map();
  const root = {dataset:{actorId:'teacher'},querySelector(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {value:'',listeners:{},
      addEventListener(event,fn){this.listeners[event]=fn;}});
    return nodes.get(selector);
  },querySelectorAll(){return []}};
  const node = name => nodes.get(`[data-bank-${name}]`);
  let state = 'queued', submissions = 0;
  const context = {document:{body:{dataset:{basePath:'/physics'}},querySelector(){return root;}},
    location:{search:''},URLSearchParams,Uint8Array,Date,Math,
    sessionStorage:{setItem(k,v){storage.set(k,v);},removeItem(k){storage.delete(k);}},
    setTimeout(fn,ms){timers.push({fn,ms});},
    fetch:async(url,options)=>{
      if(url.endsWith('/generate')) {
        submissions++;
        const payload=JSON.parse(options.body);
        assert.match(payload.request_key,/^[a-zA-Z0-9_-]{8,96}$/);
        return {ok:true,json:async()=>({result:{job_ids:['j1','j2','j3'],skipped:[]}})};
      }
      if(state==='offline') throw Error('network unavailable');
      const jobs=state==='queued'?['queued','queued','queued']:
        state==='running'?['completed','running','queued']:['completed','failed','completed'];
      return {ok:true,json:async()=>({result:{jobs:jobs.map((status,i)=>({id:'j'+(i+1),
        question_id:'q'+(i+1),question_number:String(i+1),status,
        error_code:status==='failed'?'budget_blocked':'',
        result:{status:state==='finished'&&i===2?'stale':status==='completed'?'suggested':''},
        candidate:status==='completed'&&i===0?{}:null}))}})};
    }};
  vm.runInNewContext(source,context);
  node('select-all').listeners.click();
  await node('ai').listeners.click();
  assert.match(node('ai-summary').textContent,/等待后台生成.*0\/3.*0%/);
  assert.match(node('ai-counts').textContent,/等待 3 · 生成中 0/);
  assert.doesNotMatch(node('ai-detail').textContent,/调整后采用/);
  assert.equal(node('ai').disabled,true);
  await node('ai').listeners.click();
  assert.equal(submissions,1,'active jobs must not be submitted again');
  assert.equal(timers[0].ms,2500);
  state='running';await timers.shift().fn();
  assert.equal(node('ai-bar').value,1);
  assert.match(node('ai-summary').textContent,/正在生成.*1\/3.*33%/);
  assert.match(node('ai-detail').textContent,/原题号 2/);
  state='offline';await timers.shift().fn();
  assert.equal(node('ai-bar').value,1,'network errors retain last known progress');
  assert.match(node('status').textContent,/后台任务可能仍在继续/);
  assert.equal(timers[0].ms,5000);
  state='finished';await timers.shift().fn();
  assert.equal(node('ai-bar').value,3);
  assert.match(node('ai-summary').textContent,/已结束.*100%/);
  assert.match(node('ai-counts').textContent,/完成 1 · 失败 1 · 需重新生成 1/);
  assert.match(node('status').textContent,/调用预算或每日限额已用完/);
  assert.equal(node('ai').disabled,false);
  assert.equal(storage.size,0);
  assert.equal(timers.length,0);
  console.log('PASS queued, running, network retry, mixed completion and duplicate submission');
}
checkProgress().catch(error=>{console.error(error);process.exit(1);});
