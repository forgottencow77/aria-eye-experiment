const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
async function scenario(mode,failure){
  const calls=[],elements=new Map();let timer=null;
  const element=id=>{if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',onclick:null,querySelector:()=>null});return elements.get(id)};
  const context=vm.createContext({URLSearchParams,console,location:{search:'',hash:''},document:{getElementById:element},window:{addEventListener(){}},performance:{now:()=>0},setInterval:fn=>{timer=fn;return 1},clearInterval:()=>{timer=null},setTimeout,fetch:async(path,options)=>{
    const body=JSON.parse(options.body);calls.push({path,body});
    let data={ok:true};
    if(path.endsWith('/trial/start'))data={ok:true,kind:'practice',progress:{completed:0,total:42}};
    if(path.endsWith('/heartbeat')){assert(calls.some(c=>c.body.event==='baseline'),'Heartbeat must follow acquisition');data.active=!failure;}
    return {ok:true,json:async()=>data};
  }});
  let script=fs.readFileSync('experiment-participant.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
  script=script.replace(/\nload\(\);\s*$/,'');
  vm.runInContext(script,context);
  vm.runInContext(`view={hardware_mode:${JSON.stringify(mode)}};let answerShown=false,shownError=null;setScreen=()=>{};showAnswer=()=>{answerShown=true};showError=message=>{shownError=message};const realDelay=delay;`,context);
  context.poll=async ms=>{if(failure==='hold'&&ms===3000&&timer)await timer()};
  vm.runInContext('delay=async ms=>{await poll(ms);await realDelay(0)}',context);
  await vm.runInContext('runPresentation()',context);
  const failed=mode==='hardware'&&Boolean(failure);
  assert.equal(vm.runInContext('answerShown',context),!failed);
  assert.equal(calls.some(c=>c.path.endsWith('/error')),failed);
  assert.equal(calls.some(c=>c.body.event==='response'),!failed);
  assert.equal(timer,null,'Heartbeat must stop at answer or failure');
  if(mode==='mock')assert(!calls.some(c=>c.path.endsWith('/heartbeat')));
}
(async()=>{await scenario('hardware',null);await scenario('hardware','hold');await scenario('hardware','response');await scenario('mock',null);console.log('PASS: baseline-first heartbeat, inactive lease blocks response, final heartbeat check, mock isolation')})().catch(error=>{console.error(error);process.exitCode=1});
