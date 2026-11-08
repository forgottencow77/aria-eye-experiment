const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),requests=[];
function node(id){if(!nodes.has(id))nodes.set(id,{textContent:'',innerHTML:'',disabled:false,onclick:null,classList:{add(){},remove(){}},querySelector(){return null}});return nodes.get(id)}
const context=vm.createContext({URLSearchParams,location:{search:'?session=test',hash:'#access=test'},document:{getElementById:node,querySelectorAll:()=>[]},window:{addEventListener(){},scrollTo(){}},performance:{now:()=>10000},setTimeout,clearInterval(){},fetch:async(path,options)=>{
 const body=JSON.parse(options.body);requests.push({path,body});
 return {ok:true,json:async()=>({ok:true,status:'running',progress:{completed:1,total:42},needs_break:false})};
}});
const script=fs.readFileSync('experiment-participant.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\nload\(\);\s*$/,'');
vm.runInContext(script,context);const run=source=>vm.runInContext(source,context);
(async()=>{
 run("view={status:'running',consented:true,hardware_mode:'mock',progress:{completed:0,total:42},active:{phase:'answer',kind:'stimulus',presentation_id:'reloaded-attempt',response_elapsed_ms:1250}};renderState()");
 assert.equal(run('answerPresentationId'),'reloaded-attempt');
 assert.equal(run('responseStarted'),8750);
 run("selectedEmotion='anger';selectedConfidence=3;responseLatency=1500");
 await run('submitAnswer(false)');
 assert.equal(requests.at(-1).body.presentation_id,'reloaded-attempt');
 run("answerPresentationId='new-attempt';showAnswer(false);selectedEmotion='joy';selectedConfidence=2;responseLatency=1000");
 await run('submitAnswer(false)');assert.equal(requests.at(-1).body.presentation_id,'new-attempt');
 console.log('PASS: restored answer retains its presentation ID and new answers use new IDs');
})().catch(e=>{console.error(e);process.exitCode=1});
