const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function harness(fetcher=async()=>({ok:true,json:async()=>[]})) {
  const nodes=new Map();
  const node=id=>{
    if(!nodes.has(id))nodes.set(id,{value:'',checked:false,hidden:true,innerHTML:'',addEventListener(){},querySelectorAll:()=>[]});
    return nodes.get(id);
  };
  let poll;
  const context=vm.createContext({document:{hidden:false,querySelector:node,querySelectorAll:()=>[]},URLSearchParams,
    fetch:fetcher,setInterval(fn){poll=fn;},setTimeout(){},clearTimeout(){}});
  const code=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8').replace('Promise.allSettled([loadHealth(), loadSettings(), loadScenes(), loadBatches()]);','');
  vm.runInContext(code,context);
  return {context,node,poll:()=>poll()};
}

test('Workflow page and select-all filters both carry Awaiting Review and batch scope',()=>{
  const {context,node}=harness();
  node('#scene-awaiting-review').checked=true;
  node('#scene-review-batch').value='batch3';
  for(const paged of [true,false]) {
    const params=context.filterParams(paged);
    assert.equal(params.get('awaiting_review'),'true');
    assert.equal(params.get('review_batch'),'batch3');
    assert.equal(params.has('page'),paged);
  }
});

test('batch refreshes cannot overlap and hidden Batches tab does not poll stages',async()=>{
  let release;
  const calls=[];
  const {context,node,poll}=harness(async url=>{
    calls.push(url);
    await new Promise(resolve=>{release=resolve;});
    return {ok:true,json:async()=>[]};
  });
  const first=context.loadBatches();
  await context.loadBatches();
  assert.equal(calls.length,1);
  release();await first;
  poll();
  assert.equal(calls.length,1);
  node('#tab-batches').hidden=false;
  const second=context.loadBatches();
  await context.loadBatches();
  assert.equal(calls.length,2);
  release();await second;
});
