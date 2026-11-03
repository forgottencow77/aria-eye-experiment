const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(['presentationPhase','phaseCountdown','phaseProgress'].map(id=>[id,{
  textContent:'',attributes:{},max:1,value:0,
  setAttribute(name,value){this.attributes[name]=value},removeAttribute(name){delete this.attributes[name]}
}]));
let clock=0;const timers=[];
const context=vm.createContext({
  URLSearchParams,location:{search:'',hash:''},window:{addEventListener(){}},
  document:{getElementById:id=>nodes.get(id)||null},performance:{now:()=>clock},
  setTimeout:(fn,ms)=>{timers.push({fn,at:clock+ms});return timers.length},clearTimeout(){},
});
let script=fs.readFileSync('experiment-participant.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
script=script.replace(/\nload\(\);\s*$/,'');vm.runInContext(script,context);
const run=source=>vm.runInContext(source,context);
function nextTick(){const timer=timers.shift();assert(timer,'Expected a pending countdown tick');clock=timer.at;timer.fn()}
(async()=>{
  const bar=nodes.get('phaseProgress'),text=nodes.get('phaseCountdown');
  for(const [label,duration] of [['開始までお待ちください',2000],['表情へ移行中',500],['表情を表示中',5000],['基準に戻しています',500]]){
    const started=clock;
    const promise=run(`waitPresentationPhase(${JSON.stringify(label)},${duration})`);
    assert.equal(nodes.get('presentationPhase').textContent,label);
    assert.equal(bar.max,duration);assert.equal(bar.value,duration);
    let previous=duration;
    while(timers.length){
      nextTick();const expected=Math.max(0,duration-(clock-started));
      assert.equal(bar.value,expected);assert(bar.value<=previous);previous=bar.value;
      assert.match(text.textContent,/^残り \d+\.\d 秒$/);
    }
    await promise;
    assert.equal(clock-started,duration);assert.equal(bar.value,0);
    assert.equal(text.textContent,'残り 0.0 秒');
    run("showPresentationPhase('回答画面を準備しています')");
    assert.equal(text.textContent,'処理中');
    assert.equal(bar.attributes['aria-valuetext'],'処理中');
    assert(!Object.hasOwn(bar.attributes,'value'),'Network waits should be indeterminate');
    assert.equal(timers.length,0);
  }
  let promise=run("waitPresentationPhase('表情を表示中',5000)");
  run("hardwareHeartbeatError=new Error('lost')");nextTick();
  await assert.rejects(promise,/実機との接続/);assert.equal(timers.length,0);
  run('hardwareHeartbeatError=null;abortPending=false');
  promise=run("waitPresentationPhase('表情を表示中',5000)");
  run('abortPending=true');nextTick();
  await assert.rejects(promise,/中断/);assert.equal(timers.length,0);
  console.log('PASS: monotonic countdown, shrinking phase bar, untimed network waits, cancellation');
})().catch(error=>{console.error(error);process.exitCode=1});
