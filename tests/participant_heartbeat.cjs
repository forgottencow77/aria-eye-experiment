const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
async function scenario(mode,failure){
  const calls=[],delays=[],cues=[],elements=new Map();let timer=null;
  const element=id=>{if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',onclick:null,querySelector:()=>null,setAttribute(){},removeAttribute(){}});return elements.get(id)};
  const context=vm.createContext({URLSearchParams,console,location:{search:'',hash:''},document:{getElementById:element,querySelectorAll:()=>[]},window:{addEventListener(){}},performance:{now:()=>0},setInterval:fn=>{timer=fn;return 1},clearInterval:()=>{timer=null},setTimeout,fetch:async(path,options)=>{
    const body=JSON.parse(options.body);calls.push({path,body});
    let data={ok:true};
    if(path.endsWith('/trial/start')){assert.equal(body.protocol_version,2);data={ok:true,kind:'practice',progress:{completed:0,total:42}};}
    if(path.endsWith('/heartbeat')){assert(calls.some(c=>c.body.event==='instruction'),'Heartbeat must follow acquisition');data.active=!failure;}
    return {ok:true,json:async()=>data};
  }});
  let script=fs.readFileSync('experiment-participant.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
  script=script.replace(/\nload\(\);\s*$/,'');
  vm.runInContext(script,context);
  vm.runInContext(`view={hardware_mode:${JSON.stringify(mode)},presentation_protocol:{version:2}};let answerShown=false,shownError=null;showTrialScreen=()=>{};showAnswer=()=>{answerShown=true};showError=message=>{shownError=message};const realDelay=delay;`,context);
  context.captureCue=type=>cues.push({type,lastEvent:calls.filter(c=>c.body.event).at(-1)?.body.event});
  vm.runInContext('playCue=captureCue',context);
  context.poll=async ms=>{delays.push(ms);if(failure==='hold'&&ms===5000&&timer)await timer()};
  vm.runInContext('delay=async ms=>{await poll(ms);await realDelay(0)}',context);
  await vm.runInContext('runPresentation()',context);
  const failed=mode==='hardware'&&Boolean(failure);
  assert.equal(vm.runInContext('answerShown',context),!failed);
  assert.equal(calls.some(c=>c.path.endsWith('/error')),failed);
  assert.equal(calls.some(c=>c.body.event==='response'),!failed);
  if(failed||mode==='mock')assert.equal(timer,null);else{assert.equal(typeof timer,'function','Keep B0 while answering');await timer()}
  assert.deepEqual(cues,failed?[{type:'start',lastEvent:'instruction'}]:[{type:'start',lastEvent:'instruction'},{type:'answer',lastEvent:'response'}]);
  if(!failed)assert.deepEqual(delays,[2000,500,5000,500],'No extra baseline wait');
  if(mode==='mock')assert(!calls.some(c=>c.path.endsWith('/heartbeat')));
}
(async()=>{await scenario('hardware',null);await scenario('hardware','hold');await scenario('hardware','response');await scenario('mock',null);console.log('PASS: baseline-first heartbeat, inactive lease blocks response, final heartbeat check, mock isolation')})().catch(error=>{console.error(error);process.exitCode=1});
