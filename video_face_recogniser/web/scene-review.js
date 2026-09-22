let reviewSceneId = '';
let reviewFrameCache = new Map();
let reviewSceneMode = 'clusters';
let reviewSelectedCluster = '';
let reviewGroupingThreshold = 0.50;
let reviewSceneStatuses = new Set(['open']);
let reviewScenePage = 1;
const reviewScenesPerPage = 5;

function visibleReviewScenes() {
  const matches = reviewScenes.filter(scene => reviewSceneStatuses.has(scene.complete ? 'complete' : 'open'));
  reviewScenePage = Math.max(1,Math.min(reviewScenePage,Math.ceil(matches.length/reviewScenesPerPage) || 1));
  return matches.slice((reviewScenePage-1)*reviewScenesPerPage,reviewScenePage*reviewScenesPerPage);
}

async function loadSceneFrames(force = false, sceneId = reviewSceneId, batchId = reviewBatchId) {
  if (!sceneId || !batchId) return;
  const key = `${batchId}/${sceneId}`;
  if (!force && reviewFrameCache.has(key)) return;
  const frames = [];
  for (let offset = 0; ; offset += 200) {
    const page = await request(`/api/v1/batches/${batchId}/frames?scene_id=${encodeURIComponent(sceneId)}&limit=200&offset=${offset}`);
    frames.push(...page);
    if (page.length < 200) break;
  }
  reviewFrameCache.set(key, frames);
}

async function loadReviewFrames(force = false) {
  if (reviewSceneId) return loadSceneFrames(force);
  if (!reviewBatchId || !reviewScenes.length) return;
  await Promise.all(visibleReviewScenes().map(scene => loadSceneFrames(force,scene.scene_id,reviewBatchId)));
}

function sceneIdentifiedRows(scene) {
  const people = new Map();
  for (const cluster of reviewClusters.filter(item => item.scene_id === scene.scene_id && item.person_id && item.state === 'assigned')) {
    if (!people.has(cluster.person_id)) people.set(cluster.person_id, {id:cluster.person_id,name:cluster.person_name,faces:new Map()});
    const confirmed = new Set(cluster.confirmed_ids || []);
    for (const face of cluster.faces) if (confirmed.has(face.id)) people.get(cluster.person_id).faces.set(face.id,face);
  }
  const rows = [...people.values()].filter(person => person.faces.size);
  return `<section class="scene-identified-rows"><h4>Identified faces</h4>${rows.map(person => `<details class="scene-person-row" data-review-person="${escapeHtml(person.id)}" data-reference-scene="${scene.scene_id}"><summary><img src="${escapeHtml([...person.faces.values()][0].url)}" loading="lazy" alt="${escapeHtml(person.name)}" /><span>${escapeHtml(person.name)}</span></summary><div class="identified-face-row" tabindex="0" aria-label="Identified faces for ${escapeHtml(person.name)}; scroll horizontally">${[...person.faces.values()].map(face => reviewFace(face)).join('')}</div><button class="secondary" data-reference-action="load">Correct identified faces</button><div class="reference-review"></div></details>`).join('') || '<p>No identified faces yet.</p>'}</section>`;
}

function sceneAllFacesRow(scene) {
  const faces = new Map();
  for (const cluster of reviewClusters.filter(item => item.scene_id === scene.scene_id)) {
    for (const face of cluster.faces) faces.set(face.id, face);
  }
  const ordered = [...faces.values()].sort((a,b) => a.timestamp_seconds - b.timestamp_seconds || a.id.localeCompare(b.id));
  return `<section class="scene-all-faces"><h4>All scene faces</h4><div class="identified-face-row" tabindex="0" aria-label="All scene faces; scroll horizontally">${ordered.map(face => reviewFace(face)).join('') || '<p>No faces detected yet.</p>'}</div></section>`;
}

function manualScenePeople(scene) {
  return `<section class="manual-scene-people"><h4>People tagged manually</h4>${(scene.manual_people || []).map(person => `<p>${escapeHtml(person.name)} <button class="secondary" data-scene-action="remove-person" data-scene="${scene.scene_id}" data-person="${escapeHtml(person.id)}">Remove</button></p>`).join('')}<label>Person in this scene<select class="scene-manual-person">${personOptions('')}</select></label><button data-scene-action="add-person" data-scene="${scene.scene_id}">Add person to scene</button><p class="hint">Use when you recognise someone without a visible face. This does not create a face reference. Create new people in the People tab.</p></section>`;
}

let sceneSuggestionObserver;


function outstandingFaceCount(cluster) {
  const confirmed = new Set(cluster.person_id ? cluster.confirmed_ids || [] : []);
  return cluster.faces.filter(face => !confirmed.has(face.id)).length;
}

function largestClustersFirst(clusters) {
  return [...clusters].sort((a,b) => outstandingFaceCount(b)-outstandingFaceCount(a) || a.id.localeCompare(b.id));
}

function sceneQuickSuggestions(scene) {
  if (scene.complete) return '';
  return `<section class="scene-quick-suggestions" data-suggestion-scene="${scene.scene_id}"><h4>Suggested people in this scene</h4><p class="hint">Confirm the displayed face to identify this person in the scene. Other faces remain available for review.</p><div class="quick-person-choices"><p role="status">Loading suggestions…</p></div></section>`;
}

async function loadQuickSuggestions(card) {
  const output = card.querySelector('.quick-person-choices');
  const generation = (card.suggestionGeneration || 0) + 1;
  card.suggestionGeneration = generation;
  output.innerHTML = '<p role="status">Loading suggestions…</p>';
  try {
    const people = await request(`/api/v1/review/batches/${reviewBatchId}/scenes/${card.dataset.suggestionScene}/person-suggestions`);
    if (card.suggestionGeneration !== generation) return;
    output.innerHTML = people.map(person => `<div class="scene-person-proposal" data-proposal-person="${escapeHtml(person.person_id)}" data-proposal-token="${escapeHtml(person.token)}"><button class="proposal-face" data-face-frame="${escapeHtml(person.query_face.id)}" title="View scene face in frame"><img src="${escapeHtml(person.query_face.url)}" alt="Best scene face for ${escapeHtml(person.person_name)}" loading="lazy" /></button><button class="proposal-face" data-face-frame="${escapeHtml(person.reference_face.id)}" title="View People reference in frame"><img src="${escapeHtml(person.reference_face.url)}" alt="People reference for ${escapeHtml(person.person_name)}" loading="lazy" /></button><div class="proposal-name"><strong>${escapeHtml(person.person_name)}</strong><small>${person.alternative_after_rejection ? 'Alternative after rejection · ' : ''}${person.weak_match ? 'Weak match · ' : ''}Similarity ${person.similarity.toFixed(3)}${person.supporting_frames ? ' · Supported elsewhere in scene' : ''}</small></div><button data-scene-action="confirm-person-proposal">Confirm</button><button class="secondary" data-scene-action="reject-person-proposal">Reject</button></div>`).join('') || '<p>No further eligible people suggested. Open Review scene to assign someone manually.</p>';
  } catch (error) { if (card.suggestionGeneration !== generation) return; output.innerHTML = `<p role="alert">${escapeHtml(error.message)}</p>`; }
}

function observeQuickSuggestions() {
  if (typeof IntersectionObserver === 'undefined') return;
  sceneSuggestionObserver?.disconnect();
  sceneSuggestionObserver = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      sceneSuggestionObserver.unobserve(entry.target);
      loadQuickSuggestions(entry.target);
    }
  }, {rootMargin:'100px'});
  document.querySelectorAll('.scene-quick-suggestions').forEach(card => sceneSuggestionObserver.observe(card));
}

function sceneOverviewBox(scene, opened = false) {
  return `<article class="scene-overview-card"><div class="scene-card-media">${sceneFramesHeader(scene)}${sceneAllFacesRow(scene)}</div><div class="scene-card-summary"><h3>${escapeHtml(scene.title)}</h3><span class="scene-status ${scene.complete ? 'is-complete' : 'is-open'}">${scene.complete ? 'Complete' : 'Awaiting review'}</span><p>${sceneCounts(scene)}</p><p>Identified people: ${scene.people.map(escapeHtml).join(', ') || 'None yet'}</p>${scene.changed_since_completion ? '<p>Changed since last completion — review again.</p>' : ''}${opened ? `<a data-review-video href="${escapeHtml(scene.video_url)}">Play scene</a>` : `<button data-scene-action="open" data-scene="${scene.scene_id}">Review scene</button>`}${sceneCompletionButton(scene)}${sceneIdentifiedRows(scene)}${!opened ? sceneQuickSuggestions(scene) : ''}${manualScenePeople(scene)}${scene.unimported_count ? '<p>Import latest groups before completing this scene.</p>' : ''}</div></article>`;
}

function sceneFramesHeader(scene) {
  const frames = reviewFrameCache.get(`${reviewBatchId}/${scene.scene_id}`) || [];
  return `<section class="scene-header-frames"><h4>Extracted frames · ${frames.length}</h4><div class="frame-row" tabindex="0" aria-label="Extracted scene frames; scroll horizontally">${frames.map(frame => `<figure><a data-review-video href="${escapeHtml(frame.video_url)}#t=${frame.timestamp_seconds}" aria-label="Play scene at ${frame.timestamp_seconds.toFixed(2)} seconds"><img src="${escapeHtml(frame.url)}" loading="lazy" alt="Extracted scene frame" /></a><figcaption>${frame.timestamp_seconds.toFixed(2)}s</figcaption></figure>`).join('') || '<p>No extracted frames in this batch for this scene yet.</p>'}</div></section>`;
}

function sceneCompletionButton(scene) {
  return `<button data-scene-action="${scene.complete ? 'reopen' : 'complete'}" data-scene="${scene.scene_id}" ${scene.unimported_count ? 'disabled' : ''}>${scene.complete ? 'Reopen scene' : 'Mark scene complete'}</button>`;
}

function sceneCounts(scene) {
  return `${scene.identified_count} identified face observations · ${scene.unknown_count} unknown · ${scene.ignored_count} ignored${scene.unimported_count ? ` · ${scene.unimported_count} not yet imported` : ''}`;
}

function nextClusterToRight(clusters, id) {
  const index = clusters.findIndex(cluster => cluster.id === id);
  return clusters[index+1]?.id || clusters.find(cluster => cluster.id !== id)?.id || '';
}

function sceneOutstandingBox(scene, clusters) {
  clusters = largestClustersFirst(clusters);
  const selected = clusters.find(cluster => cluster.id === reviewSelectedCluster) || clusters[0];
  const cards = clusters.map(cluster => {
    const face = cluster.faces[0];
    return `<div class="scene-cluster-card"><button class="cluster-card" data-scene-action="select-cluster" data-cluster="${cluster.id}" aria-expanded="${cluster.id === selected?.id}">${face ? `<img src="${escapeHtml(face.url)}" alt="Outstanding cluster" />` : ''}<span>${cluster.faces.length} faces${cluster.state === 'unresolved' ? ' · unresolved' : ''}</span></button><label><input type="checkbox" data-merge-cluster="${cluster.id}" />Merge selection</label></div>`;
  }).join('');
  const panel = selected ? `<div class="scene-cluster-detail" data-review-cluster="${selected.id}"><h4>Selected cluster · ${selected.faces.length} faces</h4><div class="face-grid">${selected.faces.map(face => reviewFace(face,true,true)).join('')}</div><p class="selection-count">${selected.faces.length} selected · Deselect any exceptions before confirming.</p><div class="review-toolbar"><button class="secondary" data-review-action="split">Split selected into a cluster</button><button class="secondary" data-review-action="ignore">Ignore selected faces</button></div>${assignmentControls(selected)}${reviewSceneMode === 'suggestions' ? suggestionSettings() + suggestionControls(selected) : ''}</div>` : '<p>No outstanding reviewed clusters remain.</p>';
  return `<article class="scene-outstanding-box"><h3>Outstanding faces · ${clusters.length} clusters</h3><p>All outstanding groups for this scene are together here. Select a group to review its faces. Identify the key people; other faces can remain unknown.</p><nav class="review-navigation" aria-label="Scene review tools"><button data-scene-action="mode-clusters" aria-pressed="${reviewSceneMode === 'clusters'}">Clusters</button><button data-scene-action="mode-suggestions" aria-pressed="${reviewSceneMode === 'suggestions'}">Suggestions</button></nav><div class="review-toolbar"><button id="review-merge" class="secondary" ${clusters.length < 2 ? 'disabled' : ''}>Merge selected clusters</button><label>Grouping similarity <output id="scene-grouping-value">${reviewGroupingThreshold.toFixed(2)}</output><input id="scene-grouping-threshold" type="range" min="0.10" max="0.50" step="0.01" value="${reviewGroupingThreshold}" /></label><button data-scene-action="regroup" data-scene="${scene.scene_id}" ${scene.complete ? 'disabled' : ''}>Regroup outstanding faces</button></div><p class="hint">Lower grouping similarity makes broader groups. Regroup replaces pending unassigned groups, including manual splits; manually merged, confirmed, ignored, unresolved and labelled groups are preserved. Faces without embeddings remain separate.</p><div class="cluster-row">${cards}</div>${panel}</article>`;
}

function renderSceneReview() {
  const complete = reviewScenes.filter(scene => scene.complete).length;
  const progress = `<div class="review-progress"><strong>Scene review status</strong><p>${complete} complete · ${reviewScenes.length - complete} awaiting review · ${reviewScenes.length} scenes total</p><p>Complete means the key people in the video have been reviewed. Unknown background faces can remain.</p></div>`;
  const scene = reviewScenes.find(item => item.scene_id === reviewSceneId);
  if (scene) {
    const clusters = reviewClusters.filter(cluster => cluster.scene_id === scene.scene_id);
    const unknown = [];
    const ignored = [];
    for (const cluster of clusters) {
      if (cluster.state === 'ignored') { ignored.push(...cluster.faces); continue; }
      const confirmed = new Set(cluster.person_id ? cluster.confirmed_ids || [] : []);
      if (cluster.faces.some(face => !confirmed.has(face.id))) unknown.push(cluster);
    }
    $('#review-content').innerHTML = progress + `<button class="secondary" data-scene-action="back">All scenes</button>${sceneOverviewBox(scene,true)}<div id="scene-redetection-panel">${redetectionPanel(scene)}</div>${sceneOutstandingBox(scene, unknown)}<details><summary>Ignored faces (${ignored.length})</summary><div class="face-grid">${ignored.map(face => reviewFace(face)).join('')}</div><button class="secondary" data-reference-action="load">Correct identified faces</button><div class="reference-review"></div></details>`;
    return;
  }
  const visible = visibleReviewScenes();
  const total = reviewScenes.filter(scene => reviewSceneStatuses.has(scene.complete ? 'complete' : 'open')).length;
  const pages = Math.max(1,Math.ceil(total/reviewScenesPerPage));
  const navigation = `<nav class="review-toolbar" aria-label="Review scene pages"><button class="secondary" data-scene-action="previous-page" ${reviewScenePage===1 ? 'disabled' : ''}>Previous scenes</button><span>Page ${reviewScenePage} of ${pages} · ${total} matching scenes</span><button class="secondary" data-scene-action="next-page" ${reviewScenePage===pages ? 'disabled' : ''}>Next scenes</button></nav>`;
  $('#review-content').innerHTML = progress + `<fieldset class="review-checkboxes"><legend>Show scenes</legend><label><input type="checkbox" data-scene-status="open" ${reviewSceneStatuses.has('open') ? 'checked' : ''} />Awaiting review</label><label><input type="checkbox" data-scene-status="complete" ${reviewSceneStatuses.has('complete') ? 'checked' : ''} />Complete</label></fieldset>${navigation}${visible.map(scene => sceneOverviewBox(scene)).join('') || '<p>No scenes match the selected statuses.</p>'}${navigation}`;
  observeQuickSuggestions();
}

async function handleSceneAction(button) {
  const action = button.dataset.sceneAction;
  if (action === 'confirm-person-proposal' || action === 'reject-person-proposal') {
    const card = button.closest('[data-suggestion-scene]');
    const row = button.closest('[data-proposal-person]');
    const decision = action === 'confirm-person-proposal' ? 'confirm' : 'reject';
    await request(`/api/v1/review/batches/${reviewBatchId}/scenes/${card.dataset.suggestionScene}/person-suggestions/${decision}`, {
      method:'POST',body:JSON.stringify({person_id:row.dataset.proposalPerson,token:row.dataset.proposalToken})});
    if (decision === 'reject') await loadQuickSuggestions(card);
    else await loadReview();
    return;
  }
  if (action === 'previous-page' || action === 'next-page') {
    reviewScenePage += action === 'next-page' ? 1 : -1;
    await loadReview();
    return;
  }
  if (action === 'open' || action === 'back') {
    reviewSceneId = action === 'open' ? button.dataset.scene : '';
    reviewSelectedCluster = '';
    reviewSceneMode = 'clusters';
    await loadReview();
    return;
  }
  if (action === 'mode-clusters' || action === 'mode-suggestions' || action === 'select-cluster') {
    if (action === 'select-cluster') reviewSelectedCluster = button.dataset.cluster;
    else reviewSceneMode = action === 'mode-clusters' ? 'clusters' : 'suggestions';
    renderReview();
    if (reviewSceneMode === 'suggestions') await refreshVisibleSuggestions();
    return;
  }
  const scene = reviewScenes.find(item => item.scene_id === button.dataset.scene);
  if (action === 'add-person' || action === 'remove-person') {
    const person_id = action === 'remove-person' ? button.dataset.person : button.closest('.manual-scene-people').querySelector('.scene-manual-person').value;
    if (!person_id) throw new Error('Choose a person first.');
    await request(`/api/v1/review/batches/${reviewBatchId}/scenes/${scene.scene_id}/manual-person`,{method:'POST',body:JSON.stringify({person_id,present:action === 'add-person',revision:scene.revision,fingerprint:scene.fingerprint})});
    await loadReview();
    return;
  }
  if (action === 'regroup') {
    const result = await request(`/api/v1/review/batches/${reviewBatchId}/scenes/${scene.scene_id}/regroup`, {
      method:'POST',body:JSON.stringify({revision:scene.revision,fingerprint:scene.fingerprint,threshold:reviewGroupingThreshold})});
    message($('#review-result'), `Regrouped ${result.before} pending groups into ${result.after}. ${result.without_embedding} faces have no embedding and remain separate.`);
    await loadReview();
    return;
  }
  await request(`/api/v1/review/batches/${reviewBatchId}/scenes/${scene.scene_id}/completion`, {
    method:'POST',body:JSON.stringify({revision:scene.revision,fingerprint:scene.fingerprint,complete:action === 'complete'})});
  message($('#review-result'), action === 'complete' ? 'Scene complete. Unknown faces remain unchanged.' : 'Scene reopened for review.');
  await loadReview();
}

$('#review-content').addEventListener('change', async event => {
  if (event.target.id === 'scene-grouping-threshold') {
    const value = Number(event.target.value);
    if (Number.isFinite(value) && value >= 0.10 && value <= 0.50) reviewGroupingThreshold = value;
    else { event.target.value = reviewGroupingThreshold; message($('#review-result'), 'Grouping similarity must be between 0.10 and 0.50.', true); }
    return;
  }
  if (!event.target.dataset.sceneStatus) return;
  if (event.target.checked) reviewSceneStatuses.add(event.target.dataset.sceneStatus);
  else reviewSceneStatuses.delete(event.target.dataset.sceneStatus);
  reviewScenePage = 1;
  try { await loadReview(); } catch (error) { message($('#review-result'),error.message,true); }
});

$('#review-content').addEventListener('input', event => {
  if (event.target.id === 'scene-grouping-threshold') {
    reviewGroupingThreshold = Number(event.target.value);
    $('#scene-grouping-value').textContent = reviewGroupingThreshold.toFixed(2);
  }
});
