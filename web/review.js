let reviewMode = 'scenes';
let reviewBatchId = '';
let reviewClusters = [];
let reviewPeople = [];
let reviewLoadVersion = 0;
let reviewClusterFilter = new Set(['outstanding']);
let reviewSuggestionFilter = new Set(['outstanding']);
let reviewScenes = [];
let reviewIncludeCompleted = false;
let reviewMinimumSimilarity = -1;
let reviewIncludeRejected = false;
let reviewReferenceFaces = new Map();

async function loadReview(batchId = reviewBatchId) {
  const version = ++reviewLoadVersion;
  if (batchId !== reviewBatchId) { reviewScenePage = 1; reviewSceneId = ''; reviewFrameCache.clear(); }
  const [batches, people] = await Promise.all([request('/api/v1/batches'), request('/api/v1/review/people')]);
  if (version !== reviewLoadVersion) return;
  reviewPeople = people;
  reviewBatchId = batches.some(batch => batch.id === batchId) ? batchId : (batches[0]?.id || '');
  $('#review-batch').innerHTML = batches.length ? batches.map(batch => `<option value="${escapeHtml(batch.id)}" ${batch.id === reviewBatchId ? 'selected' : ''}>${escapeHtml(batch.name)}</option>`).join('') : '<option value="">No batches</option>';
  const scenes = reviewBatchId ? await request(`/api/v1/review/batches/${reviewBatchId}/scenes?include_cluster_ids=false`) : [];
  if (version !== reviewLoadVersion) return;
  reviewScenes = scenes;
  if (reviewSceneId && !reviewScenes.some(scene => scene.scene_id === reviewSceneId)) reviewSceneId = '';
  const ids = reviewSceneId ? [reviewSceneId] : visibleReviewScenes().map(scene => scene.scene_id);
  const query = reviewMode === 'scenes' ? '?' + ids.map(id => `scene_ids=${encodeURIComponent(id)}`).join('&') : '';
  const clusters = reviewBatchId && reviewMode !== 'people' && (reviewMode !== 'scenes' || ids.length)
    ? await request(`/api/v1/review/batches/${reviewBatchId}/clusters${query}`) : [];
  if (version !== reviewLoadVersion) return;
  reviewClusters = clusters;
  if (reviewMode === 'scenes') {
    await loadReviewFrames(true);
    if (reviewSceneId) await loadRedetection();
  }
  if (version !== reviewLoadVersion) return;
  $('#review-import').disabled = !reviewBatchId;
  renderReview();
  if (reviewMode === 'suggestions' || (reviewMode === 'scenes' && reviewSceneMode === 'suggestions')) await refreshVisibleSuggestions();
}

async function openReviewBatch(batchId) {
  document.querySelectorAll('.tab-button').forEach(button => button.classList.toggle('active', button.dataset.tab === 'review'));
  document.querySelectorAll('.tab-panel').forEach(panel => { panel.hidden = panel.id !== 'tab-review'; panel.classList.toggle('active', !panel.hidden); });
  reviewMode = 'scenes';
  try { await loadReview(batchId); }
  catch (error) { message($('#review-result'), error.message, true); }
}

function reviewFace(face, selectable = false, checked = false) {
  const image = `<img src="${escapeHtml(face.url)}" loading="lazy" alt="Face observation" />`;
  return `<figure>${selectable ? `<label><input type="checkbox" data-evidence="${escapeHtml(face.id)}" ${checked ? 'checked' : ''} aria-label="Select face at ${face.timestamp_seconds.toFixed(2)} seconds" /><span>Select this face</span>${image}</label>` : image}<figcaption>${face.timestamp_seconds.toFixed(2)}s · Quality ${Math.round(face.quality * 100)}/100<br><span class="face-frame-actions"><button class="frame-context-link" data-face-frame="${escapeHtml(face.id)}" data-frame-context="${escapeHtml(face.frame_context_url || '')}">View in frame</button><span aria-hidden="true"> · </span><a data-review-video href="${escapeHtml(face.video_url)}#t=${face.timestamp_seconds}">Play at this frame</a></span></figcaption></figure>`;
}

function personOptions(selected) {
  return '<option value="">Unassigned</option>' + reviewPeople.map(person => `<option value="${escapeHtml(person.id)}" ${person.id === selected ? 'selected' : ''}>${escapeHtml(person.name)}</option>`).join('');
}

function mirrorControls() {
  return '<label class="mirror-option"><input type="checkbox" data-allow-same-frame />Allow same person twice in a frame (mirror/reflection)</label><div class="assignment-conflicts" role="alert"></div>';
}

function allowsSameFrame(card) {
  return Boolean(card.querySelector('[data-allow-same-frame]')?.checked);
}

function suggestionEvidenceLabel(person) {
  return `${person.reference_source === 'current_scene' ? 'Confirmed in this video' : person.has_scene_reference ? 'Also confirmed in this video' : 'People reference'}${person.weak_match ? ' · Weak face match — check carefully' : ''}`;
}

function showAssignmentConflicts(card, detail) {
  const output = card?.querySelector('.assignment-conflicts');
  if (!output || detail?.code !== 'same_frame_conflict') return;
  output.innerHTML = `<p>${escapeHtml(detail.message)}</p>${detail.conflicts.map(group => `<section><h5>Conflicting faces at ${group.faces[0].timestamp_seconds.toFixed(2)}s</h5><div class="identified-face-row">${group.faces.map(face => `<div><p>${face.already_confirmed ? 'Already confirmed' : 'Selected face'}</p>${reviewFace(face)}</div>`).join('')}</div></section>`).join('')}`;
  output.scrollIntoView?.({block:'nearest'});
}

function assignmentControls(cluster) {
  if (cluster.state === 'ignored') return '<p>Ignored in this batch. Excluded from suggestions and reference faces.</p><button data-review-action="reopen">Restore to review</button>';
  return `${mirrorControls()}<div class="review-toolbar"><label>Assign person<select class="review-person">${personOptions(cluster.person_id)}</select></label><button data-review-action="assign">Confirm selected faces</button><button class="secondary" data-review-action="unconfirm">Unconfirm selected faces</button><label>New person<input class="review-new-person" maxlength="255" placeholder="Person's name" /></label><button data-review-action="create-assign">Create and confirm selected</button><button class="secondary" data-review-action="unresolved">Leave unresolved</button>${cluster.state === 'unresolved' ? '<button class="secondary" data-review-action="reopen">Reopen</button>' : ''}<button class="secondary" data-review-action="ignore-all">Ignore entire cluster</button></div>`;
}

function suggestionControls(cluster) {
  return cluster.state !== 'ignored' ? '<button class="secondary" data-review-action="suggest">Refresh person suggestions</button><div class="suggestion-results"><p>Refresh to compare with the latest confirmed assignments.</p></div>' : '';
}

function selectedFaceIds(card) {
  return [...new Set([...card.querySelectorAll('[data-evidence]:checked')].map(input => input.dataset.evidence))];
}

function requireSelectedFaces(card) {
  const ids = selectedFaceIds(card);
  if (!ids.length) throw new Error('Select at least one face to confirm.');
  return ids;
}

async function refreshClusterSuggestions(card, cluster) {
  const output = card.querySelector('.suggestion-results');
  output.innerHTML = '<p role="status">Calculating top 3 choices for each face…</p>';
  try {
    const [people, rows] = await Promise.all([
      request(`/api/v1/review/clusters/${cluster.id}/suggestions?min_similarity=${reviewMinimumSimilarity}&include_rejected=${reviewIncludeRejected}&allow_same_frame=${allowsSameFrame(card)}`),
      request(`/api/v1/review/clusters/${cluster.id}/face-suggestions?min_similarity=${reviewMinimumSimilarity}&include_rejected=${reviewIncludeRejected}&allow_same_frame=${allowsSameFrame(card)}`)
    ]);
    const clusterChoices = `<section class="cluster-person-suggestions"><h4>Confirm this cluster</h4><p>All faces start selected. Deselect exceptions in the face row above, then choose a person.</p><div class="person-choice-row">${people.slice(0,3).map(person => `<section class="person-suggestion"><h4>${escapeHtml(person.person_name)} · ${person.similarity.toFixed(3)}</h4><p>${suggestionEvidenceLabel(person)}</p><div class="face-grid">${person.representatives.slice(0,1).map(face => reviewFace(face)).join('')}</div><button data-review-action="confirm" data-person="${escapeHtml(person.person_id)}">Confirm selected faces as ${escapeHtml(person.person_name)}</button></section>`).join('') || '<p>No eligible cluster suggestions. Choose a person using the assignment controls above.</p>'}</div></section>`;
    output.innerHTML = clusterChoices + '<details><summary>Individual face suggestions</summary>' + (rows.map(row => {
      const face = cluster.faces.find(item => item.id === row.face_id);
      if (!face) return '';
      return `<section class="face-suggestion-row"><h4>Face at ${face.timestamp_seconds.toFixed(2)}s — top ${row.candidates.length} of up to 3 choices</h4><div class="suggestion-query"><h5>Face being identified</h5>${reviewFace(face)}</div><div class="person-choice-row">${row.candidates.map(person => `<section class="person-suggestion"><h4>${escapeHtml(person.person_name)} · ${person.similarity.toFixed(3)}</h4><p>${suggestionEvidenceLabel(person)}${person.previously_rejected ? ' · previously rejected for this cluster' : ''}</p><div class="face-grid">${person.representatives.slice(0, 1).map(reference => reviewFace(reference)).join('')}</div><button data-review-action="confirm-face" data-face="${escapeHtml(face.id)}" data-person="${person.person_id}">Confirm this face as ${escapeHtml(person.person_name)}</button></section>`).join('')}</div>${row.candidates.length ? '' : `<p>${row.has_embedding ? 'No eligible reference people at this setting. Confirm reference examples in People, lower the minimum similarity, or include rejected people.' : 'No recognition embedding for this face. Run Group faces and import latest groups.'}</p>`}</section>`;
    }).join('') || '<p>No faces to compare.</p>') + '</details>';
  } catch (error) {
    output.innerHTML = `<p role="alert">Could not refresh suggestions: ${escapeHtml(error.message)}</p>`;
    throw error;
  }
}

async function refreshVisibleSuggestions() {
  const version = reviewLoadVersion;
  for (const card of $('#review-content').querySelectorAll('[data-review-cluster]')) {
    if (version !== reviewLoadVersion) return;
    const cluster = reviewClusters.find(item => item.id === card.dataset.reviewCluster);
    if (cluster && cluster.state !== 'ignored' && clusterStatus(cluster) !== 'confirmed') await refreshClusterSuggestions(card, cluster);
  }
}

function clusterStatus(cluster) {
  if (cluster.state === 'ignored') return 'ignored';
  if (cluster.state === 'unresolved') return 'unresolved';
  const confirmed = new Set(cluster.confirmed_ids || []);
  return cluster.person_id && cluster.faces.length && cluster.faces.every(face => confirmed.has(face.id)) ? 'confirmed' : 'outstanding';
}

function reviewProgress() {
  const counts = {confirmed:0, outstanding:0, unresolved:0, ignored:0};
  let confirmedFaces = 0, outstandingFaces = 0;
  for (const cluster of reviewClusters) {
    counts[clusterStatus(cluster)]++;
    if (cluster.state === 'ignored' || cluster.state === 'unresolved') continue;
    const ids = new Set(cluster.person_id ? cluster.confirmed_ids || [] : []);
    confirmedFaces += cluster.faces.filter(face => ids.has(face.id)).length;
    outstandingFaces += cluster.faces.filter(face => !ids.has(face.id)).length;
  }
  return `<div class="review-progress" role="status"><strong>Batch review progress</strong><p>${counts.confirmed} confirmed clusters · ${counts.outstanding} outstanding · ${counts.unresolved} unresolved · ${counts.ignored} ignored</p><p>${confirmedFaces} confirmed faces · ${outstandingFaces} faces awaiting confirmation (excluding unresolved and ignored)</p><small>Current review totals include manual and suggested assignments. Splitting a cluster changes the cluster count.</small></div>`;
}

function reviewFilter(values) {
  return `<fieldset class="review-checkboxes"><legend>Show cluster statuses</legend>${[['outstanding','Outstanding'],['confirmed','Confirmed'],['unresolved','Unresolved'],['ignored','Ignored']].map(([key,label]) => `<label><input type="checkbox" data-review-status="${key}" ${values.has(key) ? 'checked' : ''} />${label}</label>`).join('')}</fieldset><label><input type="checkbox" id="review-include-completed" ${reviewIncludeCompleted ? 'checked' : ''} />Include completed scenes</label>`;
}

function suggestionSettings() {
  return `<div class="review-toolbar"><label>Minimum similarity<input id="review-minimum-similarity" type="number" min="-1" max="1" step="0.05" value="${reviewMinimumSimilarity}" /></label><label><input id="review-include-rejected" type="checkbox" ${reviewIncludeRejected ? 'checked' : ''} />Include previously rejected people</label><span>−1 shows the full score range. Up to three people per face; fewer if fewer reference identities exist.</span></div>`;
}

function clusterArticle(cluster, suggestionsView = false) {
  const status = clusterStatus(cluster);
  const faceSelection = `<h4>Faces to review</h4><p>All faces are selected by default. Deselect any exceptions before confirming a person.</p><div class="face-grid">${cluster.faces.map(face => reviewFace(face, status !== 'ignored', status !== 'ignored')).join('')}</div><p class="selection-count" aria-live="polite">${status === 'ignored' ? 0 : cluster.faces.length} selected</p>`;
  return `<article data-review-cluster="${cluster.id}"><h3>${escapeHtml(cluster.scene_title)} · ${status}</h3>${suggestionsView ? faceSelection : `<label class="review-merge-choice"><input type="checkbox" data-merge-cluster="${cluster.id}" ${status === 'ignored' ? 'disabled' : ''} /> Select for merge</label><details><summary class="review-cluster-summary">${cluster.faces[0] ? `<img src="${escapeHtml(cluster.faces[0].url)}" alt="Cluster representative" />` : ''}<span>${cluster.faces.length} faces · ${escapeHtml(cluster.person_name || status)} · ${(cluster.confirmed_ids || []).length} confirmed</span></summary>${faceSelection}<div class="review-toolbar" ${status === 'ignored' ? 'hidden' : ''}><button class="secondary" data-review-action="split">Split selected into a cluster</button><button class="secondary" data-review-action="remove">Remove selected into separate faces</button><button class="secondary" data-review-action="ignore">Ignore selected faces</button></div></details>`}${suggestionsView && status !== 'ignored' ? '<button class="secondary" data-review-action="ignore">Ignore selected faces</button>' : ''}${assignmentControls(cluster)}${status === 'outstanding' ? suggestionControls(cluster) : ''}</article>`;
}

function renderReview() {
  ++reviewLoadVersion;
  document.querySelectorAll('[data-review-tab]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.reviewTab === reviewMode)));
  if (reviewMode === 'scenes') { renderSceneReview(); return; }
  if (reviewMode === 'people') {
    $('#review-content').innerHTML = `<article><h3>People</h3><form id="review-create-person" class="review-inline"><label>Name<input name="name" required maxlength="255" placeholder="Person's name" /></label><button>Create person</button></form><p>These are stored confirmations, including faces selected by Confirm all. If any reference is incorrect, review and remove it below. Unconfirmed observations are excluded from these previews.</p></article>${reviewPeople.map(person => `<article data-review-person="${escapeHtml(person.id)}"><form data-rename-person="${escapeHtml(person.id)}" class="review-inline"><label>Name<input name="name" required maxlength="255" value="${escapeHtml(person.name)}" /></label><button class="secondary">Rename</button></form><p>${person.reference_count} stored confirmed faces${person.review_required_count ? ` · ${person.review_required_count} labelled faces still need review in Clusters` : ''}</p><div class="face-grid">${person.representatives.map(face => reviewFace(face)).join('')}</div><button data-reference-action="load">Review reference faces</button><div class="reference-review"></div></article>`).join('')}`;
    return;
  }
  const suggestionsView = reviewMode === 'suggestions';
  const filter = suggestionsView ? reviewSuggestionFilter : reviewClusterFilter;
  const completed = new Set(reviewScenes.filter(scene => scene.complete).map(scene => scene.scene_id));
  const visible = reviewClusters.filter(cluster => filter.has(clusterStatus(cluster)) && (reviewIncludeCompleted || !completed.has(cluster.scene_id)));
  $('#review-content').innerHTML = reviewProgress() + suggestionSettings() + `<div class="review-toolbar">${reviewFilter(filter)}${suggestionsView ? '<button id="review-refresh-suggestions" class="secondary">Refresh all suggestions</button>' : '<button id="review-merge" class="secondary">Merge selected clusters</button>'}</div><p>${suggestionsView ? 'Outstanding is the default queue. Completed, ignored and unresolved clusters are excluded unless selected in the filter. Suggestions are candidates, not confirmations.' : 'Outstanding hides confirmed and ignored clusters. Use the filter to review any status. Merge only within one video; merged clusters need fresh confirmation.'}</p>${visible.length ? visible.map(cluster => clusterArticle(cluster, suggestionsView)).join('') : '<article><p>No clusters match this filter. If this batch has not been imported, choose Import latest groups.</p></article>'}<details><summary>Decision history</summary><button id="review-history" class="secondary">Load history</button><div id="review-history-content"></div></details>`;
}

$('#review-content').addEventListener('change', async event => {
  if (event.target.dataset.reviewStatus || ['review-include-completed','review-minimum-similarity','review-include-rejected'].includes(event.target.id)) {
    if (event.target.dataset.reviewStatus) {
      const values = reviewMode === 'suggestions' ? reviewSuggestionFilter : reviewClusterFilter;
      if (event.target.checked) values.add(event.target.dataset.reviewStatus);
      else values.delete(event.target.dataset.reviewStatus);
    } else if (event.target.id === 'review-include-completed') reviewIncludeCompleted = event.target.checked;
    else if (event.target.id === 'review-include-rejected') reviewIncludeRejected = event.target.checked;
    else {
      const value = Number(event.target.value);
      if (!Number.isFinite(value) || value < -1 || value > 1) { message($('#review-result'), 'Similarity must be between -1 and 1.', true); return; }
      reviewMinimumSimilarity = value;
    }
    renderReview();
    if (reviewMode === 'suggestions' || (reviewMode === 'scenes' && reviewSceneMode === 'suggestions')) {
      try { await refreshVisibleSuggestions(); } catch (error) { message($('#review-result'), error.message, true); }
    }
    return;
  }
  if (event.target.hasAttribute?.('data-allow-same-frame')) {
    const card = event.target.closest('[data-review-cluster]');
    card.querySelector('.assignment-conflicts').innerHTML = '';
    if (card.querySelector('.suggestion-results')) await refreshClusterSuggestions(card,reviewClusters.find(c => c.id === card.dataset.reviewCluster));
    return;
  }
  if (event.target.dataset.evidence) {
    const card = event.target.closest('[data-review-cluster]');
    for (const input of card.querySelectorAll('[data-evidence]')) {
      if (input.dataset.evidence === event.target.dataset.evidence) input.checked = event.target.checked;
    }
    for (const label of card.querySelectorAll('.selection-count')) label.textContent = `${selectedFaceIds(card).length} selected`;
  }
});

async function loadReferenceFaces(card, personId) {
  const output = card.querySelector('.reference-review');
  output.innerHTML = '<p role="status">Loading stored reference faces…</p>';
  const faces = await request(`/api/v1/review/people/${personId}/references`);
  reviewReferenceFaces.set(personId, faces);
  const visibleFaces = card.dataset?.referenceScene ? faces.filter(face => face.scene_id === card.dataset.referenceScene) : faces;
  output.innerHTML = `<p>Select incorrect references to remove their confirmation in every listed batch. They return to outstanding review; images are preserved.</p><div class="face-grid">${visibleFaces.map(face => `<div><label><input type="checkbox" data-reference-face="${escapeHtml(face.id)}" /> Remove this reference</label>${reviewFace(face)}<p>Confirmed in: ${face.confirmations.map(item => escapeHtml(item.batch_name)).join(', ')}</p></div>`).join('')}</div>${visibleFaces.length ? '<button data-reference-action="remove">Unconfirm selected references</button>' : '<p>No confirmed reference faces.</p>'}`;
}

$('#review-batch').addEventListener('change', event => loadReview(event.target.value).catch(error => message($('#review-result'), error.message, true)));
$('#review-refresh').addEventListener('click', () => loadReview().catch(error => message($('#review-result'), error.message, true)));
document.querySelectorAll('[data-review-tab]').forEach(button => button.addEventListener('click', async () => { reviewMode = button.dataset.reviewTab; try { await loadReview(); } catch (error) { message($('#review-result'), error.message, true); } }));
$('#review-import').addEventListener('click', async event => {
  const batchId = reviewBatchId;
  event.currentTarget.disabled = true;
  try {
    const result = await request(`/api/v1/review/batches/${batchId}/prepare`, {method:'POST'});
    message($('#review-result'), `Imported ${result.created} new clusters. ${result.scenes_needing_grouping ? `${result.scenes_needing_grouping} video(s) need Group faces first.` : 'Existing decisions preserved.'}`);
    await loadReview(batchId);
  } catch (error) { message($('#review-result'), error.message, true); }
  finally { $('#review-import').disabled = !reviewBatchId; }
});

$('#review-content').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  const name = form.elements.name.value.trim();
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    const id = form.dataset.renamePerson;
    await request(`/api/v1/review/people${id ? `/${id}` : ''}`, {method:id ? 'PATCH' : 'POST', body:JSON.stringify({name})});
    message($('#review-result'), id ? 'Name updated.' : `Created ${name}. Assign clusters in the Clusters tab.`);
    await loadReview();
  } catch (error) { message($('#review-result'), error.message, true); }
  finally { button.disabled = false; }
});

$('#review-content').addEventListener('click', async event => {
  const video = event.target.closest('[data-review-video]');
  if (video) { event.preventDefault(); openScenePlayer(video.getAttribute('href')); return; }
  const button = event.target.closest('button');
  if (!button || button.closest("form")) return;
  button.disabled = true;
  let nextFaceToFocus = '';
  try {
    if (button.dataset.faceFrame) { await showFaceFrame(button.dataset.faceFrame, button.dataset.frameContext); return; }
    if (button.dataset.redetectAction) { await handleRedetectionAction(button); return; }
    if (button.dataset.sceneAction) { await handleSceneAction(button); return; }
    if (button.dataset.referenceAction) {
      const card = button.closest('[data-review-person]');
      const personId = card.dataset.reviewPerson;
      if (button.dataset.referenceAction === 'load') { await loadReferenceFaces(card, personId); return; }
      const face_ids = [...card.querySelectorAll('[data-reference-face]:checked')].map(input => input.dataset.referenceFace);
      if (!face_ids.length) throw new Error('Select incorrect reference faces to unconfirm.');
      const selected = (reviewReferenceFaces.get(personId) || []).filter(face => face_ids.includes(face.id));
      const revisions = Object.fromEntries(selected.flatMap(face => face.confirmations.map(item => [item.cluster_id, item.revision])));
      await request(`/api/v1/review/people/${personId}/references/remove`, {method:'POST',body:JSON.stringify({face_ids,revisions})});
      message($('#review-result'), `Removed confirmation from ${face_ids.length} reference faces. They are back in outstanding review.`);
      await loadReview();
      return;
    }
    if (button.id === 'review-refresh-suggestions') { await loadReview(); return; }
    if (button.id === 'review-history') {
      const history = await request(`/api/v1/review/batches/${reviewBatchId}/history`);
      $('#review-history-content').innerHTML = history.length ? `<ul>${history.map(row => `<li>${escapeHtml(row.created_at)} · ${escapeHtml(reviewHistoryLabel(row))}</li>`).join('')}</ul>` : '<p>No decisions yet.</p>';
      return;
    }
    if (button.id === 'review-merge') {
      const ids = [...$('#review-content').querySelectorAll('[data-merge-cluster]:checked')].map(input => input.dataset.mergeCluster);
      const revisions = Object.fromEntries(ids.map(id => [id, reviewClusters.find(cluster => cluster.id === id).revision]));
      await request('/api/v1/review/clusters/merge', {method:'POST',body:JSON.stringify({revisions})});
    } else {
      const card = button.closest('[data-review-cluster]');
      if (!card) return;
      const cluster = reviewClusters.find(item => item.id === card.dataset.reviewCluster);
      const action = button.dataset.reviewAction;
      const base = `/api/v1/review/clusters/${cluster.id}`;
      if (action === 'suggest') {
        await refreshClusterSuggestions(card, cluster);
        return;
      }
      if (action === 'confirm-face') {
        await request(`${base}/assign`, {method:'POST',body:JSON.stringify({revision:cluster.revision,person_id:button.dataset.person,face_ids:[button.dataset.face],allow_same_frame:allowsSameFrame(card)})});
      } else if (action === 'ignore' || action === 'ignore-all') {
        const face_ids = action === 'ignore-all' ? cluster.faces.map(face => face.id) : [...card.querySelectorAll('[data-evidence]:checked')].map(input => input.dataset.evidence);
        await request(`${base}/ignore`, {method:'POST',body:JSON.stringify({revision:cluster.revision,face_ids})});
        const remaining = cluster.faces.filter(face => !face_ids.includes(face.id));
        if (!remaining.length) {
          const visible = reviewClusters.filter(c => c.scene_id === cluster.scene_id && c.state !== 'ignored' && clusterStatus(c) !== 'confirmed');
          reviewSelectedCluster = nextClusterToRight(largestClustersFirst(visible),cluster.id);
        } else {
          reviewSelectedCluster = cluster.id;
          const lastIgnored = Math.max(...cluster.faces.map((face,index) => face_ids.includes(face.id) ? index : -1));
          nextFaceToFocus = (cluster.faces.slice(lastIgnored+1).find(face => !face_ids.includes(face.id)) || remaining[0]).id;
        }

      } else if (action === 'create-assign') {
        const face_ids = requireSelectedFaces(card);
        const name = card.querySelector('.review-new-person').value.trim();
        if (!name) throw new Error('Enter a name for the new person.');
        const person = await request('/api/v1/review/people', {method:'POST',body:JSON.stringify({name})});
        await request(`${base}/assign`, {method:'POST',body:JSON.stringify({revision:cluster.revision,person_id:person.id,face_ids,allow_same_frame:allowsSameFrame(card)})});
      } else if (action === 'split' || action === 'remove') {
        const face_ids = [...card.querySelectorAll('[data-evidence]:checked')].map(input => input.dataset.evidence);
        await request(`${base}/split`, {method:'POST',body:JSON.stringify({revision:cluster.revision,face_ids,separate_each:action === 'remove'})});
      } else if (action === 'reject') {
        await request(`${base}/reject/${button.dataset.person}`, {method:'POST',body:JSON.stringify({revision:cluster.revision})});
      } else {
        const isConfirmation = ['confirm', 'confirm-all', 'assign', 'assign-all'].includes(action);
        const person_id = action.startsWith('confirm') ? button.dataset.person : action.startsWith('assign') ? (card.querySelector('.review-person').value || null) : null;
        if (isConfirmation && !person_id) throw new Error('Choose a person to confirm. Use Unconfirm selected faces to remove confirmation.');
        const payload = {revision:cluster.revision,person_id,allow_same_frame:allowsSameFrame(card),state:action === 'unresolved' ? 'unresolved' : 'pending'};
        if (action.endsWith('-all')) payload.all_faces = true;
        else if (isConfirmation || action === 'unconfirm') payload.face_ids = requireSelectedFaces(card);
        await request(`${base}/assign`, {method:'POST',body:JSON.stringify(payload)});
      }
    }
    message($('#review-result'), 'Review decision saved.');
    await loadReview();
    if (nextFaceToFocus) {
      const next = document.querySelector(`[data-evidence="${nextFaceToFocus}"]`);
      next?.focus();
      next?.scrollIntoView({block:'nearest',inline:'nearest'});
    }
  } catch (error) { showAssignmentConflicts(button.closest('[data-review-cluster]'),error.detail); message($('#review-result'), error.message, true); }
  finally { button.disabled = false; }
});


function reviewHistoryLabel(row) {
  const details = row.details || {};
  const personId = details.after?.person_id || details.person_id;
  const person = reviewPeople.find(item => item.id === personId)?.name || 'person';
  if (row.action === 'remove_reference') return `Removed ${details.face_ids.length} incorrect reference confirmations`;
  if (row.action === 'reference_returned') return 'Returned removed references to outstanding review';
  if (row.action === 'ignore_faces') return `Ignored ${details.ignored.member_ids.length} faces`;
  if (row.action === 'confirm_faces') return `Explicitly confirmed ${details.confirmed.confirmed_ids.length} faces`;
  if (row.action === 'assign') return `Assigned ${details.after.member_ids.length} faces to ${person}`;
  if (row.action === 'reject_scene_person') return `Rejected scene suggestion: ${person}`;
  if (row.action === 'reject_suggestion') return `Rejected suggestion: ${person}`;
  if (row.action === 'merge') return `Merged ${details.before.length} clusters for fresh review`;
  if (row.action === 'split' || row.action === 'remove_faces') return `Separated faces into ${details.created.length} new cluster(s)`;
  return ({import:'Imported cluster',pending:'Reopened as unassigned',unresolved:'Left unresolved'})[row.action] || row.action.replaceAll('_',' ');
}

async function showFaceFrame(faceId, contextUrl) {
  const data = await request(contextUrl || `/api/v1/review/evidence/${encodeURIComponent(faceId)}/frame`);
  const dialog = $('#face-frame-dialog');
  const image = $('#face-frame-image');
  const overlay = $('#face-frame-overlay');
  overlay.innerHTML = '';
  $('#face-frame-error').textContent = '';
  image.onload = () => {
    overlay.setAttribute('viewBox', `0 0 ${image.naturalWidth} ${image.naturalHeight}`);
    const [x,y,width,height] = data.bbox.map(Number);
    if ([x,y,width,height].every(Number.isFinite)) overlay.innerHTML = `<rect x="${x}" y="${y}" width="${width}" height="${height}" fill="none" stroke="#ffe600" stroke-width="2" vector-effect="non-scaling-stroke" />`;
  };
  image.onerror = () => { $('#face-frame-error').textContent = 'The extracted frame could not be loaded.'; };
  image.src = data.url;
  dialog.showModal();
}
$('#close-face-frame').addEventListener('click', () => $('#face-frame-dialog').close());
