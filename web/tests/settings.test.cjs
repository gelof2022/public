const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
test('settings load global defaults, save numeric values and change theme', async () => {
  const fields = Object.fromEntries(['detection_threshold','recognition_threshold','grouping_threshold','theme','stash_playback_fallback'].map(name => [name,{type:name==='theme'?'select':name==='stash_playback_fallback'?'checkbox':'number'}]));
  let handler, saved;
  const form = {elements:{namedItem:name=>fields[name]},addEventListener:(_,fn)=>handler=fn};
  const result = {};
  const root = {dataset:{},style:{}};
  const context = vm.createContext({document:{documentElement:root,querySelector:id=>id==='#preferences-form'?form:result},
    reviewGroupingThreshold:0.5, reviewMinimumSimilarity:-1,
    request:async (_,options) => options ? (saved=JSON.parse(options.body)) : ({detection_threshold:0.65,recognition_threshold:0.3,grouping_threshold:0.4,theme:'light',stash_playback_fallback:true})});
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../settings.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(root.dataset.theme,'light');
  assert.equal(context.reviewMinimumSimilarity,0.3);
  fields.detection_threshold.value='0.7'; fields.theme.value='dark';
  await handler({preventDefault(){}});
  assert.equal(saved.detection_threshold,0.7);
  assert.equal(root.dataset.theme,'dark');
  assert.equal(result.textContent,'Settings saved.');
});
