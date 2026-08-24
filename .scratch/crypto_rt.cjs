const { execFileSync } = require("child_process");
const exe = "E:/Qingxiaotuan Agent CLI/ext/dist/bin/qxt_crypto.exe";
function call(method, params){
  const req = JSON.stringify({id:1, method, params}) + "\n";
  const out = execFileSync(exe, [], {input: req, encoding:"utf8"});
  // 取最后一行 json
  const lines = out.split("\n").filter(Boolean);
  for(let i=lines.length-1;i>=0;i--){ try{ const m=JSON.parse(lines[i]); if(m.id===1) return m; }catch{} }
  return {id:1,ok:false,error:"no-response"};
}
const d = call("derive",{passphrase:"pw"});
console.log("derive:", JSON.stringify(d));
if(!d.ok) process.exit(1);
const s = call("seal",{passphrase:"pw", salt_b64:d.result.salt_b64, plaintext:"青小团秘密 hello"});
console.log("seal:", JSON.stringify(s).slice(0,200));
if(!s.ok) process.exit(1);
const u = call("unseal",{passphrase:"pw", salt_b64:d.result.salt_b64, blob:s.result.blob ?? s.result.blob});
console.log("unseal:", JSON.stringify(u).slice(0,200));
