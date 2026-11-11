// 在宿主预先注入兼容 callTool 时，页面仍必须完成标准初始化握手。
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(require('path').join(__dirname,'../src/dashboard.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const calls=[],listeners={},elements=new Map();let legacyCalls=0;
function element(){return {value:'pending',textContent:'',append(){},className:'',style:{}}}
const parent={postMessage(message){calls.push(message);if(message.id){const result=message.method==='ui/initialize'?{protocolVersion:'2026-01-26',hostCapabilities:{serverTools:{}}}:{structuredContent:{jobs:[]}};queueMicrotask(()=>listeners.message({source:parent,data:{jsonrpc:'2.0',id:message.id,result}}))}}};
const context={console,setTimeout,clearTimeout,queueMicrotask,setInterval:()=>1,clearInterval(){},location:{hash:''},
 window:{parent,openai:{callTool(){legacyCalls++;throw Error('must use standard bridge')}},addEventListener(name,fn){listeners[name]=fn}},
 document:{querySelector(selector){if(!elements.has(selector))elements.set(selector,element());return elements.get(selector)},createElement:element,documentElement:{scrollHeight:400,scrollWidth:600},body:{}},ResizeObserver:class{observe(){}}};
vm.runInNewContext(script,context);
setImmediate(()=>{assert(calls.some(x=>x.method==='ui/initialize'));assert(calls.some(x=>x.method==='ui/notifications/initialized'));assert(calls.some(x=>x.method==='tools/call'));assert.equal(legacyCalls,0);assert.equal(elements.get('#error').textContent,'暂无待恢复任务；已登记的工作仍由后台守候，可切换“全部任务”查看。');console.log('PASS injected legacy interface does not bypass MCP Apps handshake');});
