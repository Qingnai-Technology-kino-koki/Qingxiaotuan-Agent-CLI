const { execFileSync } = require("child_process");
const node = "C:/Users/28726/.workbuddy/binaries/node/versions/22.22.2/node.exe";
function call(exe, method, params){
  const req = JSON.stringify({id:1, method, params}) + "\n";
  const out = execFileSync(node, [exe], {input: req, encoding:"utf8"});
  const lines = out.split("\n").filter(Boolean);
  for(let i=lines.length-1;i>=0;i--){ try{ const m=JSON.parse(lines[i]); if(m.id===1) return m; }catch{} }
  return {ok:false,error:"no-response"};
}
// agent-sdk tools
const a = call("ext/ts/dist/agent-sdk/main.js","tools",{});
console.log("agent-sdk tools:", a.ok? a.result.tools.join(", ") : a.error);
// mcp-client list (未连接应报错)
const m = call("ext/ts/dist/mcp-client/main.js","list",{});
console.log("mcp-client list (no conn expected error):", m.ok? "OK":"error="+m.error);
