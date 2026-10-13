const assert=require('node:assert/strict');
const {validateWork,makeLabel,parseSaved}=require('../human-label.js');
const w={schema_version:'okb-human-evidence-v1-draft',purpose:'independent-human-draft',package_id:'p',n_rows:1,rows:[{row_key:'r',digest:{task:{instruction:'test'},events:[{evidence_key:'E1',payload:{text:'<script>untrusted</script>'}}]}}]};
assert.equal(validateWork(w),w);
assert.throws(()=>validateWork({...w,n_rows:2}));
assert.throws(()=>validateWork({...w,n_rows:2,rows:[w.rows[0],w.rows[0]]}));
for(const name of ['remembered','omitted','confused','wrongly_persisted','wrongly_reused','unassessable']){
 const r=makeLabel(w,'a'.repeat(64),w.rows[0],name,'E1','理由');
 assert.equal(parseSaved(JSON.stringify(r),w,'a'.repeat(64)).get('r').label,name);
 assert.throws(()=>parseSaved(JSON.stringify(r),w,'b'.repeat(64)));
 assert.throws(()=>parseSaved(JSON.stringify(r)+'\n'+JSON.stringify(r),w,'a'.repeat(64)));
}
assert.throws(()=>makeLabel(w,'hash',w.rows[0],'bad','E1','理由'));
assert.throws(()=>makeLabel(w,'hash',w.rows[0],'remembered','E2','理由'));
assert.throws(()=>makeLabel(w,'hash',w.rows[0],'remembered','E1',''));
const fs=require('node:fs');const source=fs.readFileSync(require('node:path').join(__dirname,'../human-label.js'),'utf8');
assert(!/innerHTML|\beval\(|\bfetch\(|XMLHttpRequest|localStorage/.test(source));
console.log('PASS: worklist validation, six labels, package binding, duplicate rejection, refs, note and safe DOM/network static checks');
