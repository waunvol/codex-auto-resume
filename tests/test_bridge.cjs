// 在宿主预先注入兼容 callTool 时，页面仍必须完成标准初始化握手。
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(require('path').join(__dirname,'../src/dashboard.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const calls=[],listeners={},elements=new Map();let legacyCalls=0,proxy='http://127.0.0.1:7890',saveError=false,jobs=[];
function element(){return {value:'pending',textContent:'',children:[],append(...nodes){this.children.push(...nodes)},className:'',style:{},hidden:true,disabled:false}}
const parent={postMessage(message){calls.push(message);if(message.id){let result;if(message.method==='ui/initialize')result={protocolVersion:'2026-01-26',hostCapabilities:{serverTools:{}}};else if(message.params.name==='set_resume_proxy'){if(saveError)result={isError:true,content:[{type:'text',text:'proxy validation failed'}]};else{proxy=message.params.arguments.proxy;result={structuredContent:{network:{proxy,environment_override:false}}}}}else if(message.params.name==='set_resume_control')result={structuredContent:{control:message.params.arguments}};else result={structuredContent:{jobs,capabilities:{desktop_unlock:true},network:{proxy,environment_override:false}}};queueMicrotask(()=>listeners.message({source:parent,data:{jsonrpc:'2.0',id:message.id,result}}))}}};
const context={console,setTimeout,clearTimeout,queueMicrotask,setInterval:()=>1,clearInterval(){},location:{hash:''},
 window:{parent,openai:{callTool(){legacyCalls++;throw Error('must use standard bridge')}},addEventListener(name,fn){listeners[name]=fn}},
 document:{querySelector(selector){if(!elements.has(selector))elements.set(selector,element());return elements.get(selector)},createElement:element,documentElement:{scrollHeight:400,scrollWidth:600},body:{}},ResizeObserver:class{observe(){}}};
vm.runInNewContext(script,context);
setImmediate(async()=>{
 try{
  assert(calls.some(x=>x.method==='ui/initialize'));assert(calls.some(x=>x.method==='ui/notifications/initialized'));assert.equal(legacyCalls,0);
  assert.equal(elements.get('#error').textContent,'暂无待恢复任务；切换“全部任务”可查看工作中、已结束及历史轮次。');
  assert.equal(elements.get('#network').hidden,false);assert.equal(elements.get('#proxy-mode').value,'custom');
  const url=elements.get('#proxy-url'),mode=elements.get('#proxy-mode'),save=elements.get('#proxy-save');
  assert.equal(url.value,proxy);url.value='http://127.0.0.1:17890';
  await elements.get('#refresh').onclick();assert.equal(url.value,'http://127.0.0.1:17890','refresh preserves unfinished input');
  await save.onclick();assert.equal(proxy,'http://127.0.0.1:17890');assert(elements.get('#proxy-feedback').textContent.includes('已保存'));
  mode.value='inherit';mode.onchange();assert.equal(url.disabled,true);await save.onclick();assert.equal(proxy,'inherit');
  mode.value='direct';mode.onchange();await save.onclick();assert.equal(proxy,'direct');
  mode.value='custom';mode.onchange();url.value='';const before=calls.length;await save.onclick();assert.equal(calls.length,before,'empty URL stays local');
  url.value='bad';saveError=true;await save.onclick();assert.equal(elements.get('#proxy-feedback').textContent,'proxy validation failed');assert.equal(save.disabled,false);
  assert(!calls.some(x=>x.method==='tools/call'&&x.params.name==='set_resume_control'),'global settings do not modify tasks');
  jobs=[{session:'migration',turn:'old',status:'blocked',is_latest:false},{session:'migration',turn:'new',status:'complete',is_latest:true},{session:'other',turn:'current',status:'blocked',is_latest:true}];
  await elements.get('#refresh').onclick();const rows=elements.get('#jobs').children;assert.equal(rows.length,3);assert.deepEqual(rows.map(x=>x.hidden),[true,true,false],'old blocked round is hidden in default view');
  elements.get('#filter').value='all';await elements.get('#refresh').onclick();assert(rows.every(x=>!x.hidden));assert(rows[0].children[1].textContent.startsWith('历史轮次'));assert(rows[1].children[1].textContent.startsWith('本轮已结束'));
  jobs[2].status='waiting_thread';jobs[2].runner_alive=true;await elements.get('#refresh').onclick();const row=rows[2],release=row.children.find(x=>x.textContent==='释放聊天锁'),consent=row.children.find(x=>x.className==='unlock-option').children[0];assert.equal(consent.checked,false);row.children[3].value='auto';await release.onclick();assert.equal(consent.checked,true);const saved=calls.filter(x=>x.method==='tools/call'&&x.params.name==='set_resume_control').at(-1);assert.equal(saved.params.arguments.session,'other');assert.equal(saved.params.arguments.desktop_unlock,true);
  console.log('PASS MCP Apps handshake, embedded proxy controls, refresh preservation and error feedback');
 }catch(error){console.error(error);process.exitCode=1}
});
