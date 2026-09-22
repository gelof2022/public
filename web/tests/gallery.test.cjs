const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('gallery loads subsequent pages and renders escaped scene links', async () => {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: '', dataset: {}, listeners: {}, addEventListener(type, handler) { this.listeners[type] = handler; }, querySelectorAll: () => [],
      scrollIntoView() {}, classList: { toggle() {} },
    });
    return elements.get(id);
  };
  const calls = [];
  const frame = (time) => ({ scene_id: 'scene', scene_title: '<holiday>',
    scene_duration_seconds: 125, scene_thumbnail_url: null, video_url: '/api/v1/scenes/scene/video',
    url: '/generated/frame.jpg', timestamp_seconds: time });
  const context = vm.createContext({
    document: { querySelector: element, querySelectorAll: () => [] },
    setInterval() {}, setTimeout() {}, clearTimeout() {}, URLSearchParams,
    fetch: async (path) => {
      if (path.includes('/face-groups')) return {ok: true, json: async () => []};
      if (path.includes('/faces?')) return { ok: true, json: async () => [{ scene_id: 'scene', timestamp_seconds: 12.5, video_url: '/api/v1/scenes/scene/video', url: '/generated/face.jpg', quality: 0.25, detector_confidence: 0.95, usable: false, quality_details: { face_size_px: 20, sharpness: 15, exposure: 0.4 } }] };
      if (!path.includes('/frames?')) throw new Error('Startup request ignored');
      calls.push(path);
      return { ok: true, json: async () => path.includes('offset=0')
        ? Array.from({ length: 1000 }, (_, i) => frame(i)) : [frame(1000)] };
    },
  });
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../app.js'), 'utf8'), context);
  await vm.runInContext('showFrames("batch")', context);
  assert.equal(calls.length, 2);
  const html = element('#frame-gallery').innerHTML;
  assert.equal((html.match(/<figure>/g) || []).length, 1002);
  assert.match(html, /Faces \(1\)/);
  assert.match(html, /Quality 25\/100/);
  assert.match(html, /Detection score 0.950/);
  assert.match(html, /Small face/);
  const actions = vm.runInContext('batchActions({id:"b",state:"video_processing_complete",frame_count:2})', context);
  assert.ok(actions.indexOf("Detect faces") < actions.indexOf("View faces"));
  assert.match(html, /&lt;holiday&gt;/);
  assert.match(html, /<a[^>]+href="\/api\/v1\/scenes\/scene\/video"[^>]+target="_blank"/);
  assert.match(html, /scene-duration">· 2:05/);
  assert.match(html, /href="\/api\/v1\/scenes\/scene\/video#t=1000"/);
  assert.match(html, /aria-label="Play scene at 1000.00 seconds"/);
  assert.doesNotMatch(html, /src="null"/);
  assert.equal(element('#frames-section').hidden, false);
  let prevented = false, opened = false, played = false;
  element('#video-dialog').showModal = () => { opened = true; };
  element('#scene-player').play = () => { played = true; return Promise.resolve(); };
  element('#frame-gallery').listeners.click({
    target: { closest: (selector) => selector !== 'a[href]' ? null : ({ getAttribute: () => '/api/v1/scenes/scene/video#t=12.5' }) },
    preventDefault: () => { prevented = true; },
  });
  element('#scene-player').onloadedmetadata();
  assert.equal(prevented, true);
  assert.equal(opened, true);
  assert.equal(played, true);
  assert.equal(element('#scene-player').currentTime, 12.5);
  assert.equal(element('#scene-player').src, '/api/v1/scenes/scene/video');
  element('#scene-player').listeners.error();
  assert.equal(element('#scene-player').src, '/api/v1/scenes/scene/stash-video');
  element('#scene-player').onloadedmetadata();
  assert.equal(element('#scene-player').currentTime, 12.5);
  element('#scene-player').listeners.error();
  assert.match(element('#video-error').textContent, /could not be played/);
  vm.runInContext('openScenePlayer("/api/v1/scenes/other/video#t=2")', context);
  element('#scene-player').currentTime = 0;
  element('#scene-player').listeners.error();
  assert.equal(element('#scene-player').src, '/api/v1/scenes/other/stash-video');
  element('#scene-player').onloadedmetadata();
  assert.equal(element('#scene-player').currentTime, 2);
  const panels = [{dataset: {scenePanel: 'frames'}, hidden: false}, {dataset: {scenePanel: 'faces'}, hidden: true}];
  const buttons = ['frames', 'faces'].map(view => ({dataset: {sceneView: view}, setAttribute(key, value) { this[key] = value; }}));
  const row = {querySelectorAll: selector => selector === '[data-scene-view]' ? buttons : panels};
  buttons[1].closest = () => row;
  element('#frame-gallery').listeners.click({target: {closest: () => buttons[1]}});
  assert.equal(panels[0].hidden, true);
  assert.equal(panels[1].hidden, false);
  assert.equal(buttons[1]['aria-pressed'], 'true');
  await vm.runInContext('showFaces("batch")', context);
  assert.match(element('#frame-gallery').innerHTML, /data-scene-panel="frames" hidden/);
  const grouped = vm.runInContext('renderFaceGroups([{id:"f",timestamp_seconds:4,video_url:"/video",url:"/face.jpg",quality:0.8,detector_confidence:0.9,usable:true,quality_details:{}}], {status:"ready",groups:[{face_ids:["f"],representative_ids:["f"]}],excluded_face_ids:[]})', context);
  assert.match(grouped, /class="cluster-row"/);
  const card = grouped.match(/<button[^>]+class="cluster-card"[\s\S]*?<\/button>/)[0];
  assert.match(card, /<span>1 face<\/span>/);
  assert.doesNotMatch(card, /4.00s|Quality|Detection score/);
  const clusterPanels = [{dataset: {clusterPanel: '0'}, hidden: true}, {dataset: {clusterPanel: '1'}, hidden: true}];
  const clusterButtons = ['0', '1'].map(id => ({dataset: {cluster: id}, 'aria-expanded': 'false', getAttribute(key) { return this[key]; }, setAttribute(key, value) { this[key] = value; }}));
  const browser = {querySelectorAll: selector => selector === '[data-cluster]' ? clusterButtons : clusterPanels};
  clusterButtons.forEach(button => { button.closest = () => browser; });
  const clickCluster = index => element('#frame-gallery').listeners.click({target: {closest: selector => selector === '[data-cluster]' ? clusterButtons[index] : null}});
  clickCluster(0);
  assert.equal(clusterPanels[0].hidden, false);
  clickCluster(1);
  assert.equal(clusterPanels[0].hidden, true);
  assert.equal(clusterPanels[1].hidden, false);
  clickCluster(1);
  assert.equal(clusterPanels[1].hidden, true);
  assert.match(grouped, /All observations \(1\)/);
  assert.match(grouped, /Group representative/);
});
