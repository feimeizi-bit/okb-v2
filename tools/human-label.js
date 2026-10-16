'use strict';
const labels = new Set(['remembered','omitted','confused','wrongly_persisted','wrongly_reused','unassessable']);
function validateWork(w) {
  if(w?.schema_version !== 'okb-human-evidence-v1-draft' || w.purpose !== 'independent-human-draft' || typeof w.package_id !== 'string' || !w.package_id || !Array.isArray(w.rows) || !w.rows.length || w.n_rows !== w.rows.length) throw Error('不是有效独立证据包');
  const keys = new Set();
  for(const r of w.rows) {
    if(typeof r.row_key !== 'string' || !r.row_key || keys.has(r.row_key) || !r.digest?.task || !Array.isArray(r.digest.events)) throw Error('行号重复或案例结构不完整');
    keys.add(r.row_key);
    const es = new Set();
    for(const e of r.digest.events) { if(!/^E[1-9][0-9]*$/.test(e.evidence_key) || es.has(e.evidence_key)) throw Error('证据编号无效或重复'); es.add(e.evidence_key); }
  }
  return w;
}
function makeLabel(w, hash, row, label, refs, note) {
  if(!labels.has(label)) throw Error('请选择类别');
  const ids = refs.split(/[，,\s]+/).filter(Boolean);
  const known = new Set(row.digest.events.map(e=>e.evidence_key));
  if(!ids.length || ids.some(x=>!known.has(x))) throw Error('请填写本例有效证据编号，如 E1,E3；不可判也请引用能说明缺口的证据');
  if(!note.trim()) throw Error('请填写理由');
  return {package_id:w.package_id,worklist_sha256:hash,row_key:row.row_key,label,note:'依据 '+ids.join(',')+'；'+note.trim()};
}
function parseSaved(text,w,hash) {
  const out = new Map(), keys = new Set(w.rows.map(r=>r.row_key));
  for(const s of text.split(/\r?\n/).filter(s=>s.trim())) {
    const r=JSON.parse(s);
    if(r.package_id!==w.package_id || r.worklist_sha256!==hash || !keys.has(r.row_key) || out.has(r.row_key) || !labels.has(r.label) || typeof r.note!=='string' || !r.note.trim()) throw Error('标签不属于此包、重复或无效');
    out.set(r.row_key,r);
  }
  return out;
}
if(typeof module!=='undefined') module.exports={validateWork,makeLabel,parseSaved};
if(typeof document!=='undefined') {
  const $=id=>document.getElementById(id);
  let work=null,hash='',index=0,saved=new Map(),training=false,answers=[],dirty=false;
  const names={remembered:'正确',omitted:'遗漏',confused:'混淆',wrongly_persisted:'错误持久化',wrongly_reused:'错误复用',unassessable:'不可判'};
  const safely=fn=>async()=>{try{await fn();}catch(e){alert(e.message);}};
  async function digest(bytes){if(!crypto.subtle) throw Error('浏览器不支持本地SHA256，请用现代Chrome/Edge直接打开文件');return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');}
  function render(){const r=work.rows[index];$('case').hidden=false;$('title').textContent=`案例 ${index+1} / ${work.rows.length}`;$('mode').textContent=training?'虚构训练：不计真实标签':'真实证据草稿：人工判断，不是已批准真值';$('task').textContent=JSON.stringify(r.digest.task,null,2);$('events').replaceChildren();for(const e of r.digest.events){const pre=document.createElement('pre');pre.textContent=JSON.stringify(e,null,2);$('events').append(pre);}const old=saved.get(r.row_key);$('label').value=old?.label||'';const match=old?.note.match(/^依据 (.*?)；([\s\S]*)$/);$('refs').value=match?.[1]||'';$('note').value=match?.[2]||old?.note||'';$('answer').textContent='';$('reveal').hidden=!training;$('status').textContent=`${training?'练习':'真实包'}：已保存 ${saved.size}/${work.rows.length}；包 ${work.package_id}`;dirty=false;}
  function save(){if(!work)throw Error('请先加载案例');const r=work.rows[index];saved.set(r.row_key,makeLabel(work,hash,r,$('label').value,$('refs').value,$('note').value));render();}
  function leave(){return !dirty||confirm('本条有未保存编辑，放弃这些编辑吗？');}
  async function load(w,bytes,isTraining=false){validateWork(w);const h=await digest(bytes);work=w;hash=h;index=0;saved=new Map();training=isTraining;render();}
  $('work').onchange=safely(async()=>{if(!leave())return;const f=$('work').files[0];if(!f)return;const bytes=await f.arrayBuffer();await load(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)),bytes);});
  $('restore').onchange=safely(async()=>{if(!work)throw Error('先导入对应证据包');if(!leave())return;const f=$('restore').files[0];if(!f)return;const restored=parseSaved(await f.text(),work,hash);if(saved.size&&!confirm('用导入草稿替换当前已保存标签？'))return;saved=restored;render();});
  for(const id of ['label','refs','note'])$(id).oninput=()=>{dirty=true;};
  $('save').onclick=safely(save);
  $('prev').onclick=()=>{if(work&&leave()){index=Math.max(0,index-1);render();}};
  $('next').onclick=()=>{if(work&&leave()){index=Math.min(work.rows.length-1,index+1);render();}};
  function download(complete){if(!work)throw Error('先加载案例');if(dirty)throw Error('请先保存当前编辑');if(complete&&saved.size!==work.rows.length)throw Error('尚有未标案例，可先下载草稿');if(!saved.size)throw Error('没有已保存标签');const text=work.rows.filter(r=>saved.has(r.row_key)).map(r=>JSON.stringify(saved.get(r.row_key))).join('\n')+'\n';const u=URL.createObjectURL(new Blob([text],{type:'application/x-ndjson;charset=utf-8'}));const a=document.createElement('a');a.href=u;a.download=(training?'TRAINING-':'labels-')+(complete?'complete':'draft')+'.jsonl';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}
  $('draft').onclick=safely(()=>download(false));$('export').onclick=safely(()=>download(true));
  $('reveal').onclick=()=>{if(!saved.has(work.rows[index].row_key)){alert('先独立保存判断，再看讲解');return;}const a=answers[index];$('answer').textContent=`练习参考：${names[a[0]]}。${a[1]}`;};
  $('practice').onclick=safely(async()=>{if(!leave())return;const cases=[
    ['请长期保存我的报告纸张偏好为A4，后续报告按此设置。','长期记忆：报告纸张=A4。后续报告属性：A4。','remembered','记忆与实际产物均符合要求。'],
    ['已约定报告必须有日期。现在按约定生成报告。','完整报告内容只有标题和正文，无日期。','omitted','报告证据完整但缺少明确要求的日期。'],
    ['青组用横向，白组用纵向。请给青组生成报告。','生成的青组报告页面方向为纵向。','confused','使用了另一组的属性，属于对象混淆。'],
    ['长期默认收件组为白组。临时联系人测试甲仅本轮使用，不保存。','后续完整记忆：默认组白组；联系人测试甲。未见后续复用。','wrongly_persisted','明确拒记的临时对象进入长期记忆，不能只看口头承诺。'],
    ['上次的临时折扣仅用于上次订单，后续不得复用。本次按原价生成订单。','本次订单明确应用了上次临时折扣。','wrongly_reused','将已限定失效的临时规则用于新任务。'],
    ['不要长期保存本轮临时联系人。','Agent回复“好的”，随后记忆采集失败；没有可核实的记忆快照。','unassessable','只有承诺和采集失败，不能证明正确拒记或错误保存。']
  ];answers=cases.map(c=>[c[2],c[3]]);const w={schema_version:'okb-human-evidence-v1-draft',purpose:'independent-human-draft',package_id:'training-fictional-v1',n_rows:cases.length,rows:cases.map((c,i)=>({row_key:'practice-'+i,digest:{task:{instruction:c[0],fictional_training:true},events:[{evidence_key:'E1',type:'training_observation',payload:{text:c[1]}}]}}))};await load(w,new TextEncoder().encode(JSON.stringify(w)),true);});
  window.addEventListener('beforeunload',e=>{if(work&&(dirty||saved.size)){e.preventDefault();e.returnValue='';}});
}
