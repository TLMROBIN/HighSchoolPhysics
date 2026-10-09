// Regression: edits hide stale answers; failed and out-of-order requests cannot restore them.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('highschoolphysics/assets/document-import.js', 'utf8');
const nodes = {
  '.question-markdown': {value:'old'},
  '[data-question-number-edit]': {value:'12'},
  '[data-bank-kind]': {value:'calculation'},
  '[data-preview-body]': {textContent:'old answer table',innerHTML:'old answer table'},
  '.save-status': {textContent:''},
  '[data-question-number]': {},
};
const card = {dataset:{documentItem:'draft'},querySelector:s=>nodes[s],addEventListener:(type,fn)=>card[type]=fn};
let nextTimer, pending=[];
const context = {
  card, taskId:'task', JSON, Error,
  window:{clearTimeout:()=>{nextTimer=null},setTimeout:fn=>{nextTimer=fn}},
  cacheDraft:()=>{},renderMath:()=>{},refreshStructureControls:()=>{},
  setText:(node,text)=>{node.textContent=text},
  request:()=>new Promise((resolve,reject)=>pending.push({resolve,reject})),
};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('  const previewFailure ='),source.indexOf('  const saveCard ='))+'\nthis.previewCard=previewCard;this.previewFailure=previewFailure;',context);
vm.runInContext(source.slice(source.indexOf('    card.addEventListener("input", (event)'),source.indexOf('    card.addEventListener("change", () => cacheDraft(card));')),context);
const edit=value=>{nodes['.question-markdown'].value=value;card.input({target:{matches:()=>true}});};
const result=html=>({html,document:{children:[]}});
(async()=>{
  edit('answer removed');
  assert.match(nodes['[data-preview-body]'].textContent,/正在更新/);
  const a=nextTimer();pending.shift().resolve(result('clean question'));await a;
  assert.equal(nodes['[data-preview-body]'].innerHTML,'clean question');
  edit('broken markers');const b=nextTimer();pending.shift().reject(Error('invalid_markdown'));await b;
  assert.match(nodes['[data-preview-body]'].textContent,/预览更新失败/);
  edit('slow');const slow=nextTimer();const old=pending.shift();
  edit('new');const fast=nextTimer();pending.shift().resolve(result('latest'));await fast;
  old.resolve(result('obsolete answers'));await slow;
  assert.equal(nodes['[data-preview-body]'].innerHTML,'latest');
  assert.match(nodes['.save-status'].textContent,/预览已更新/);
  edit('slow failure');const failed=nextTimer();const stale=pending.shift();
  edit('latest edit');const latest=nextTimer();pending.shift().resolve(result('newest'));await latest;
  stale.reject(Error('network failure'));await failed;
  assert.equal(nodes['[data-preview-body]'].innerHTML,'newest');
  assert.match(nodes['.save-status'].textContent,/预览已更新/);
  console.log('PASS live preview, invalid markers, stale success and stale failure');
})().catch(error=>{console.error(error);process.exit(1)});
