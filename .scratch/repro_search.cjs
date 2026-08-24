const { execFileSync } = require("child_process");
const node = "C:/Users/28726/.workbuddy/binaries/node/versions/22.22.2/node.exe";
function call(exe, method, params){
  const req = JSON.stringify({id:1, method, params}) + "\n";
  const out = execFileSync(node, [exe], {input: req, encoding:"utf8", cwd:"E:/Qingxiaotuan Agent CLI"});
  const lines = out.split("\n").filter(Boolean);
  for(let i=lines.length-1;i>=0;i--){ try{ const m=JSON.parse(lines[i]); if(m.id===1) return m; }catch{} }
  return {ok:false,error:"no-response"};
}
const s = call("ext/ts/dist/search/main.js","search",{pattern:"ExternalEngineManager", max:5});
console.log("search ok:", s.ok);
if(s.ok){ console.log("count:", s.result.count); s.result.matches.slice(0,3).forEach(m=>console.log("  "+m.file+":"+m.line+"  "+m.text.trim().slice(0,80))); }
const n = call("ext/ts/dist/notify/main.js","send",{title:"测试",message:"hello",level:"info"});
console.log("notify:", n.ok? "ok method="+n.result.method : n.error);
