// Execute the study page's real event logic without a browser or hardware.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
const elements=new Map(),writes=[],storage=new Map();
function element(id){
  if(!elements.has(id))elements.set(id,{value:id==='studyMode'?'manual':'',hidden:false,disabled:false,textContent:'',innerHTML:'',dataset:{},style:{setProperty(){}},listeners:{},addEventListener(name,fn){this.listeners[name]=fn},setAttribute(){}});
  return elements.get(id);
}
const context=vm.createContext({console,location:{pathname:'/study'},document:{getElementById:element,querySelectorAll:()=>[]},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},window:{addEventListener(){}},setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,clearInterval(){},fetch:async(path,options)=>{
  const body=options.body?JSON.parse(options.body):null;
  if(body)writes.push({path,body});
  let data={ok:true};
  if(path==='/api/experiment/config')data={ok:true,config:fixture.config,config_initialized:true};
  if(path==='/api/experiment/preview'){assert.deepEqual(body.config,fixture.config);data={ok:true,trials:fixture.trials}}
  if(path==='/api/live/start')data.token='mock-token';
  return {ok:true,json:async()=>data};
}});
const html=fs.readFileSync('study.html','utf8');
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
const run=source=>vm.runInContext(source,context);
(async()=>{
  await run('initialize()');
  const saved=JSON.parse(run('JSON.stringify(buildServerConfig())'));
  element('studyMode').value='conditions';
  await run('changeStudyMode()');
  assert.equal(run('inspectionTrials.length'),42);
  assert.equal(element('adjuster').hidden,true);
  assert.equal(element('previousCondition').disabled,true);
  assert.equal(writes.filter(w=>w.path.startsWith('/api/live/')).length,0,'Selecting review must not actuate');
  assert.match(element('conditionProgress').textContent,/1 \/ 42/);
  await run('applyToDevice()');
  // Complete the asynchronous initial send before stepping through all slots.
  await new Promise(resolve=>setImmediate(resolve));
  for(let i=0;i<42;i++){
    const trial=JSON.parse(run('JSON.stringify(currentTrial())'));
    const expected=fixture.trials.find(t=>t.trial_slot_id===trial.trial_slot_id).actual_target_values;
    await run('sendDeviceState()');
    const sent=writes.filter(w=>w.path==='/api/live/input').at(-1).body;
    assert.equal(sent.irisColor,expected.iris_hex);
    assert.deepEqual(sent.pose,{x:expected.gaze_horizontal_deg/20,y:expected.gaze_vertical_deg/20,openness:1-expected.eyelid_open_percent/100});
    assert.match(element('conditionProgress').textContent,new RegExp('^'+(i+1)+' / 42'));
    // Hidden editing handlers must not mutate the stored presets either.
    run('updateSliderSettings()');element('resetParams').listeners.click();
    run('stepCondition(1)');
  }
  assert.equal(element('nextCondition').disabled,true);
  assert.equal(run('currentTrial().trial_slot_id'),'surprise__CLG');
  assert.deepEqual(JSON.parse(run('JSON.stringify(buildServerConfig())')),saved);
  assert.equal(writes.filter(w=>w.path==='/api/experiment/config').length,0,'Review must not save configuration');
  assert.equal(writes.filter(w=>w.path.includes('/trial/')||w.path.includes('/answer')).length,0,'Review must not create experiment responses');
  run('stepCondition(-1)');assert.equal(run('currentTrial().trial_slot_id'),'surprise__LG');
  // Direct factor selection and emotion buttons preserve the chosen combination.
  element('conditionSelect').value='L';element('conditionSelect').listeners.change();
  element('emotions').listeners.click({target:{closest:()=>({dataset:{emotion:'anger'}})}});
  assert.equal(run('currentTrial().trial_slot_id'),'anger__L');
  assert.match(element('conditionDuplicates').textContent,/disgust__L/);
  element('studyMode').value='manual';await run('changeStudyMode()');
  assert.equal(run('deviceToken'),null);assert.equal(element('adjuster').hidden,false);
  assert.equal(element('conditionControls').hidden,true);
  assert.equal(writes.filter(w=>w.path==='/api/live/stop').length,1);
  console.log('PASS: 42 condition payloads, B0 substitution, navigation, immutable settings, explicit activation and stop');
})().catch(error=>{console.error(error);process.exitCode=1});
