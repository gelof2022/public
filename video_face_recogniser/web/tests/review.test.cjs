const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const face = id => ({id,url:'/face.jpg',video_url:'/video',timestamp_seconds:2,quality:.8,has_embedding:true});
function harness(request = async()=>[]) {
  const handlers={};
  const nodes=new Map();
  const node=key=>{
    if(!nodes.has(key)) nodes.set(key,{innerHTML:'',addEventListener(type,fn){if(key === '#review-content') (handlers[type] ||= []).push(fn);},querySelectorAll:()=>[]});
    return nodes.get(key);
  };
  const context=vm.createContext({$:node,document:{querySelectorAll:()=>[]},escapeHtml:value=>String(value??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),request,message(){},clearTimeout(){},setTimeout(){return 1;}});
  for(const file of ['review.js','scene-review.js','redetection.js']) vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
  return {context,node,handlers};
}

test('Scenes is the default review workflow and allows completion with unknown faces',()=>{
  const {context,node}=harness();
  vm.runInContext(`reviewScenes=[{scene_id:'s',title:'<Video>',people:['Alice'],identified_count:1,unknown_count:2,ignored_count:0,unimported_count:0,complete:false}];renderReview();`,context);
  const html=node('#review-content').innerHTML;
  assert.match(html,/Scene review status/);
  assert.match(html,/0 complete · 1 awaiting review/);
  assert.match(html,/&lt;Video&gt;/);
  assert.match(html,/2 unknown/);
  assert.match(html,/Mark scene complete/);
  assert.match(html,/data-scene-status="open"/);
  assert.doesNotMatch(html.match(/<fieldset[\s\S]*?<\/fieldset>/)[0],/<select/);
  assert.match(html,/data-scene-action="add-person"/);
});

test('multiple status checkboxes filter independently and completed scenes are excluded by default',()=>{
  const {context,node}=harness();
  context.fixtures=[
    {id:'a',scene_id:'s1',scene_title:'AwaitingExample',state:'pending',faces:[face('fa')]},
    {id:'c',scene_id:'s1',scene_title:'CompleteIdentity',state:'assigned',person_id:'p',confirmed_ids:['fc'],faces:[face('fc')]},
    {id:'i',scene_id:'s1',scene_title:'IgnoredExample',state:'ignored',faces:[face('fi')]},
    {id:'done',scene_id:'s2',scene_title:'CompletedScene',state:'pending',faces:[face('fd')]}
  ];
  vm.runInContext(`reviewClusters=fixtures;reviewScenes=[{scene_id:'s2',complete:true}];reviewMode='suggestions';reviewSuggestionFilter=new Set(['outstanding','confirmed']);renderReview();`,context);
  const html=node('#review-content').innerHTML;
  assert.match(html,/AwaitingExample/);assert.match(html,/CompleteIdentity/);
  assert.doesNotMatch(html,/IgnoredExample|CompletedScene/);
  assert.match(html,/data-review-status="outstanding" checked/);
  assert.match(html,/data-review-status="confirmed" checked/);
  assert.doesNotMatch(html,/review-status-filter/);
  vm.runInContext(`reviewIncludeCompleted=true;renderReview();`,context);
  assert.match(node('#review-content').innerHTML,/CompletedScene/);
});

test('per-face choices clearly separate query and references and confirm one exact face',async()=>{
  const requests=[];
  const {context,node,handlers}=harness(async(url,options)=>{
    requests.push({url,options});
    if(url.includes('/face-suggestions'))return [{face_id:'query',has_embedding:true,candidates:[0,1,2].map(i=>({person_id:`p${i}`,person_name:`Person ${i}`,similarity:.2-i/10,reference_count:1,representatives:[face('reference')]}))}];
    return [];
  });
  const output={innerHTML:''};
  const card={dataset:{reviewCluster:'c'},querySelector:()=>output,querySelectorAll:()=>[]};
  context.fixture={id:'c',state:'pending',revision:1,faces:[face('query')]};
  vm.runInContext(`reviewClusters=[fixture];reviewMode='suggestions';`,context);
  await context.refreshClusterSuggestions(card,context.fixture);
  assert.match(output.innerHTML,/top 3 of up to 3 choices/);
  assert.match(output.innerHTML,/Face being identified/);
  assert.equal((output.innerHTML.match(/data-review-action="confirm-face"/g)||[]).length,3);
  assert.equal((output.innerHTML.match(/data-face="query"/g)||[]).length,3);
  assert.doesNotMatch(output.innerHTML,/data-face="reference"/);
  const button={dataset:{reviewAction:'confirm-face',face:'query',person:'p1'},closest:selector=>selector==='form'?null:card};
  for(const handler of handlers.click||[])await handler({target:{closest:selector=>selector==='button'?button:null}});
  const assignment=requests.find(r=>r.url.endsWith('/assign'));
  assert.deepEqual(JSON.parse(assignment.options.body),{revision:1,person_id:'p1',face_ids:['query'],allow_same_frame:false});
  assert.ok(requests[0].url.includes('min_similarity=-1'));
});

test('scene completion submits the displayed revision and fingerprint without confirming unknown faces',async()=>{
  const requests=[];
  const {context}=harness(async(url,options)=>{requests.push({url,options});return [];});
  vm.runInContext(`reviewBatchId='b';reviewScenes=[{scene_id:'s',revision:2,fingerprint:'hash',unknown_count:5}];`,context);
  await context.handleSceneAction({dataset:{sceneAction:'complete',scene:'s'}});
  const request=requests.find(row=>row.options?.method==='POST');
  assert.equal(request.url,'/api/v1/review/batches/b/scenes/s/completion');
  assert.deepEqual(JSON.parse(request.options.body),{revision:2,fingerprint:'hash',complete:true});
  assert.ok(!requests.some(row=>row.url.endsWith('/assign')));
});

test('People retains reference correction and cluster cards retain partial confirmation controls',()=>{
  const {context,node}=harness();
  context.fixture={id:'c',state:'pending',scene_title:'Video',faces:[face('f')]};
  vm.runInContext(`reviewClusters=[fixture];reviewMode='clusters';renderReview();`,context);
  assert.match(node('#review-content').innerHTML,/Confirm selected faces/);
  assert.match(node('#review-content').innerHTML,/Split selected/);
  vm.runInContext(`reviewMode='people';reviewPeople=[{id:'p',name:'Alice',reference_count:1,representatives:[]}];renderReview();`,context);
  assert.match(node('#review-content').innerHTML,/Review reference faces/);
  assert.match(node('#review-content').innerHTML,/Create person/);
});

test('outstanding scene groups share one box with scene-local Clusters and Suggestions tools',()=>{
  const {context,node}=harness();
  context.fixtures=[{id:'c1',scene_id:'s',state:'pending',faces:[face('f1')]},{id:'c2',scene_id:'s',state:'pending',faces:[face('f2')]}];
  vm.runInContext(`reviewClusters=fixtures;reviewSceneId='s';reviewScenes=[{scene_id:'s',title:'Video',people:[],identified_count:0,unknown_count:2,ignored_count:0,unimported_count:0,complete:false}];renderReview();`,context);
  const html=node('#review-content').innerHTML;
  assert.equal((html.match(/class="scene-outstanding-box"/g)||[]).length,1);
  assert.equal((html.match(/data-scene-action="select-cluster"/g)||[]).length,2);
  assert.equal((html.match(/data-review-cluster=/g)||[]).length,1);
  assert.match(html,/data-scene-action="mode-clusters"/);
  assert.match(html,/data-scene-action="mode-suggestions"/);
  assert.match(html,/Regroup outstanding faces/);
  const index=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  assert.doesNotMatch(index,/data-review-tab="clusters"|data-review-tab="suggestions"/);
});

test('scene header loads all frame pages and exposes timestamp playback links in a scrollable row',async()=>{
  const calls=[];
  const {context}=harness(async url=>{
    calls.push(url);
    const count=url.includes('offset=0')?200:1;
    return Array.from({length:count},(_,i)=>({url:'/frame.jpg',video_url:'/video',timestamp_seconds:i}));
  });
  vm.runInContext(`reviewBatchId='b';reviewSceneId='s';`,context);
  await context.loadSceneFrames();
  assert.equal(calls.length,2);
  assert.ok(calls.every(url=>url.includes('scene_id=s')));
  assert.ok(calls[1].includes('offset=200'));
  const html=context.sceneFramesHeader({scene_id:'s'});
  assert.match(html,/Extracted frames · 201/);
  assert.match(html,/class="frame-row"/);
  assert.match(html,/data-review-video href="\/video#t=1"/);
});

test('overview scene box contains scrollable frames followed by only confirmed identified faces',()=>{
  const {context}=harness();
  context.faces=[face('confirmed'),face('unconfirmed')];
  vm.runInContext(`reviewBatchId='b';reviewFrameCache.set('b/s',[{url:'/frame.jpg',video_url:'/video',timestamp_seconds:1}]);reviewClusters=[{scene_id:'s',state:'assigned',person_id:'p',person_name:'Alice',confirmed_ids:['confirmed'],faces}];`,context);
  const html=context.sceneOverviewBox({scene_id:'s',title:'Video',complete:false,people:['Alice'],identified_count:1,unknown_count:1,ignored_count:0,unimported_count:0});
  assert.match(html,/class="scene-card-media"/);
  const strip=html.split('scene-all-faces')[1].split('scene-card-summary')[0];
  assert.match(strip,/data-face-frame="confirmed"/);
  assert.match(strip,/data-face-frame="unconfirmed"/);
  assert.ok(strip.indexOf('View in frame') < strip.indexOf('Play at this frame'));
  assert.match(strip,/class="face-frame-actions"/);
  assert.ok(html.indexOf('Extracted frames') < html.indexOf('Identified faces'));
  assert.match(html,/class="identified-face-row"/);
  assert.match(html,/<span>Alice<\/span>/);
  assert.match(html,/data-review-video href="\/video#t=1"/);
});

test('confidence slider does not start detection; explicit trigger sends its value',async()=>{
  const requests=[];
  const {context,node,handlers}=harness(async(url,options)=>{requests.push({url,options});return null;});
  vm.runInContext(`reviewBatchId='b';reviewSceneId='s';reviewScenes=[{scene_id:'s'}];`,context);
  for(const handler of handlers.input||[]) handler({target:{id:'scene-detection-threshold',value:'0.35'}});
  assert.equal(requests.length,0);
  assert.equal(node('#scene-detection-value').textContent,'0.35');
  node('#scene-detection-threshold').value='0.35';
  await context.handleRedetectionAction({dataset:{redetectAction:'start'}});
  const request=requests.find(row=>row.options?.method==='POST');
  assert.equal(request.url,'/api/v1/review/batches/b/scenes/s/redetection');
  assert.deepEqual(JSON.parse(request.options.body),{threshold:0.35});
});

test('ready detection preview offers selecting additions and keeps unmatched originals',()=>{
  const {context}=harness();
  context.proposal={...face('new-face'),detector_confidence:.4};
  vm.runInContext(`reviewBatchId='b';redetectionRuns.set('b/s',{id:'run',state:'ready',threshold:.35,summary:{frames:3,new:1,matched_existing:2,existing_not_detected:1},proposals:[proposal]});`,context);
  const html=context.redetectionPanel({scene_id:'s'});
  assert.match(html,/type="range"/);
  assert.match(html,/data-redetect-face="new-face"/);
  assert.match(html,/Add selected new faces/);
  assert.match(html,/Existing faces are kept in all cases/);
  assert.match(html,/Discard preview/);
});

test('identified people collapse beneath actions and status has a distinct colour class',()=>{
  const {context}=harness();
  context.fixtures=[{scene_id:'s',person_id:'p',person_name:'Alice',state:'assigned',confirmed_ids:['a','b'],faces:[face('a'),face('b'),face('unknown')]}];
  const html=vm.runInContext(`reviewClusters=fixtures;sceneOverviewBox({scene_id:'s',title:'Scene',complete:true,people:['Alice'],unimported_count:0})`,context);
  assert.ok(html.indexOf('Reopen scene') < html.indexOf('scene-identified-rows'));
  assert.match(html, /scene-status is-complete/);
  assert.match(html, /<details class="scene-person-row"[^>]*><summary>/);
  assert.match(html, /<span>Alice<\/span>/);
  assert.doesNotMatch(html, /<details[^>]* open/);
  assert.match(html, /data-face-frame="a"/);
  assert.doesNotMatch(html.split('scene-identified-rows')[1], /data-face-frame="unknown"/);
});

test('candidate frame viewer uses preview endpoint before evidence exists',async()=>{
  const urls=[];
  const {context,node}=harness(async url=>{urls.push(url);return {url:'/generated/frame.jpg',bbox:[10,20,30,40]};});
  node('#face-frame-dialog').showModal=()=>{};
  await context.showFaceFrame('candidate','/api/v1/review/redetections/run/candidates/candidate/frame');
  assert.equal(urls[0],'/api/v1/review/redetections/run/candidates/candidate/frame');
  assert.equal(node('#face-frame-image').src,'/generated/frame.jpg');
  assert.match(context.reviewFace({...face('candidate'),frame_context_url:urls[0]}),/data-frame-context="\/api\/v1\/review\/redetections\/run\/candidates\/candidate\/frame"/);
});

test('cluster confirmation defaults to every face and respects deselection',async()=>{
  const person={person_id:'p',person_name:'Alice',similarity:.8,representatives:[face('reference')]};
  const {context}=harness(async url=>url.includes('/face-suggestions')?[]:[person]);
  const cluster={id:'c',scene_id:'s',state:'pending',faces:[face('a'),face('b')],confirmed_ids:[]};
  const html=context.clusterArticle(cluster,true);
  assert.match(html,/data-evidence="a" checked/);
  assert.match(html,/data-evidence="b" checked/);
  const output={innerHTML:''};
  await context.refreshClusterSuggestions({querySelector:()=>output},cluster);
  assert.match(output.innerHTML,/data-review-action="confirm" data-person="p"/);
  assert.match(output.innerHTML,/Confirm selected faces as Alice/);
  const selected=context.requireSelectedFaces({querySelectorAll:()=>[{dataset:{evidence:'a'}}]});
  assert.deepEqual([...selected],['a']);
  assert.throws(()=>context.requireSelectedFaces({querySelectorAll:()=>[]}),/Select at least one face/);
});

test('review controls remove redundant confirmation, allow low slider values and advance right',()=>{
  const {context}=harness();
  assert.doesNotMatch(context.assignmentControls({state:'pending'}),/Confirm all faces/);
  assert.equal(context.nextClusterToRight([{id:'left'},{id:'current'},{id:'right'}],'current'),'right');
  assert.equal(context.nextClusterToRight([{id:'only'}],'only'),'');
  const html=context.sceneOutstandingBox({scene_id:'s',complete:false},[]);
  assert.match(html,/type="range" min="0.10" max="0.50"/);
  assert.match(html,/manually merged/);
});

test('quick suggestions reject stale responses after selection changes',async()=>{
  const pending=[];
  const {context}=harness(()=>new Promise(resolve=>pending.push(resolve)));
  context.URLSearchParams=URLSearchParams;
  context.fixtures=[{id:'c',faces:[face('a'),face('b')]}];
  vm.runInContext('reviewClusters=fixtures',context);
  const output={innerHTML:''};
  const card={dataset:{suggestionScene:'s'},querySelector:()=>output};
  const first=context.loadQuickSuggestions(card);
  const second=context.loadQuickSuggestions(card);
  pending[1]([]);await second;
  pending[0]([{person_id:'p',person_name:'Stale',similarity:1,representatives:[]}]);await first;
  assert.doesNotMatch(output.innerHTML,/Stale/);
  assert.match(output.innerHTML,/No further eligible people/);
});

test('mirror exception is opt in and conflict previews identify both observations',async()=>{
  const {context}=harness();
  assert.match(context.mirrorControls(),/data-allow-same-frame/);
  assert.doesNotMatch(context.mirrorControls(),/checked/);
  const output={innerHTML:''};
  context.showAssignmentConflicts({querySelector:()=>output},{code:'same_frame_conflict',message:'Check these faces',conflicts:[{faces:[{...face('selected'),already_confirmed:false},{...face('existing'),already_confirmed:true}]}]});
  assert.match(output.innerHTML,/Selected face/);
  assert.match(output.innerHTML,/Already confirmed/);
  assert.match(output.innerHTML,/data-face-frame="selected"/);
  assert.match(output.innerHTML,/data-face-frame="existing"/);
});

test('confirmation displays structured conflicts and retries with explicit mirror override',async()=>{
  const requests=[];
  let override=false;
  const {context,handlers}=harness(async(url,options)=>{
    requests.push({url,options});
    if(url.endsWith('/assign') && !JSON.parse(options.body).allow_same_frame) {
      const error=new Error('Same frame');
      error.detail={code:'same_frame_conflict',message:'Check mirror',conflicts:[{faces:[face('query'),{...face('existing'),already_confirmed:true}]}]};
      throw error;
    }
    return [];
  });
  const output={innerHTML:''};
  const card={dataset:{reviewCluster:'c'},querySelector:selector=>selector==='[data-allow-same-frame]'?{checked:override}:output,querySelectorAll:()=>[{dataset:{evidence:'query'}}]};
  const button={dataset:{reviewAction:'confirm',person:'p'},closest:selector=>selector==='form'?null:card};
  context.fixture={id:'c',revision:1,faces:[face('query')]};
  vm.runInContext('reviewClusters=[fixture];loadReview=async()=>{}',context);
  const event={target:{closest:selector=>selector==='button'?button:null}};
  for(const handler of handlers.click) await handler(event);
  assert.match(output.innerHTML,/Already confirmed/);
  assert.equal(JSON.parse(requests[0].options.body).allow_same_frame,false);
  override=true;
  for(const handler of handlers.click) await handler(event);
  assert.equal(JSON.parse(requests[1].options.body).allow_same_frame,true);
});

test('scene overview has one compact row per person with scene and People faces',async()=>{
  const requests=[];
  const proposals=['John','Paul'].map(name=>({person_id:name,person_name:name,token:`token-${name}`,query_face:face(`scene-${name}`),reference_face:face(`ref-${name}`),similarity:.7,supporting_frames:2}));
  const {context}=harness(async url=>{requests.push(url);return proposals;});
  const output={innerHTML:''};
  const card={dataset:{suggestionScene:'scene'},querySelector:()=>output};
  await context.loadQuickSuggestions(card);
  assert.match(requests[0],/\/scenes\/scene\/person-suggestions$/);
  assert.equal((output.innerHTML.match(/data-proposal-person=/g)||[]).length,2);
  assert.equal((output.innerHTML.match(/<img /g)||[]).length,4);
  assert.equal((output.innerHTML.match(/>Confirm</g)||[]).length,2);
  assert.equal((output.innerHTML.match(/>Reject</g)||[]).length,2);
  assert.match(output.innerHTML,/Best scene face for John/);
  assert.match(output.innerHTML,/People reference for John/);
  assert.doesNotMatch(output.innerHTML,/cluster|selected|checkbox/i);
  assert.equal(context.sceneQuickSuggestions({scene_id:'scene',complete:true}),'');
  const html=context.sceneQuickSuggestions({scene_id:'scene'});
  assert.match(html,/data-suggestion-scene="scene"/);
  assert.doesNotMatch(html,/data-review-cluster/);
  assert.doesNotMatch(html,/Refresh suggestions|retry-quick/);
});

test('scene person decisions submit the exact proposal and rejection refreshes in place',async()=>{
  const requests=[];
  const {context}=harness(async(url,options)=>{requests.push({url,options});return [];});
  const output={innerHTML:''};
  const card={dataset:{suggestionScene:'scene'},querySelector:()=>output};
  const row={dataset:{proposalPerson:'p',proposalToken:'token'}};
  const button={dataset:{sceneAction:'reject-person-proposal'},closest:selector=>selector==='[data-suggestion-scene]'?card:row};
  await context.handleSceneAction(button);
  assert.match(requests[0].url,/person-suggestions\/reject$/);
  assert.deepEqual(JSON.parse(requests[0].options.body),{person_id:'p',token:'token'});
  assert.match(requests[1].url,/person-suggestions$/);
  vm.runInContext('loadReview=async()=>{}',context);
  button.dataset.sceneAction='confirm-person-proposal';
  await context.handleSceneAction(button);
  assert.match(requests[2].url,/person-suggestions\/confirm$/);
});

test('large Review batch loads only five visible scenes and scopes clusters and frames',async()=>{
  const requests=[];
  const scenes=Array.from({length:80},(_,i)=>({scene_id:`s${i}`,title:`Scene ${i}`,complete:false,people:[],manual_people:[],cluster_ids:[],identified_count:0,unknown_count:10,ignored_count:0,unimported_count:0}));
  const {context,node}=harness(async url=>{
    requests.push(url);
    if(url==='/api/v1/batches') return [{id:'b',name:'Batch 3'}];
    if(url.includes('/scenes?'))return scenes;
    return [];
  });
  await context.loadReview('b');
  const clusterRequest=requests.find(url=>url.includes('/clusters?'));
  assert.equal((clusterRequest.match(/scene_ids=/g)||[]).length,5);
  assert.doesNotMatch(clusterRequest,/s5(?:&|$)/);
  assert.equal(requests.filter(url=>url.includes('/frames?')).length,5);
  assert.ok(requests.filter(url=>url.includes('/frames?')).every(url=>url.includes('scene_id=')));
  assert.match(node('#review-content').innerHTML,/Page 1 of 16/);
  assert.doesNotMatch(node('#review-content').innerHTML,/<h3>Scene 5<\/h3>/);
  requests.length=0;
  await context.handleSceneAction({dataset:{sceneAction:'next-page'}});
  assert.match(requests.find(url=>url.includes('/clusters?')),/scene_ids=s5/);
  assert.match(node('#review-content').innerHTML,/Page 2 of 16/);
  requests.length=0;
  vm.runInContext('loadRedetection=async()=>{}',context);
  await context.handleSceneAction({dataset:{sceneAction:'open',scene:'s6'}});
  assert.match(requests.find(url=>url.includes('/clusters?')),/\?scene_ids=s6$/);
  assert.equal(requests.filter(url=>url.includes('/frames?')).length,1);
});
