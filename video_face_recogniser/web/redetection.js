let redetectionRuns = new Map();
let redetectionThresholds = new Map();
let redetectionTimer;

function redetectionKey() { return `${reviewBatchId}/${reviewSceneId}`; }

async function loadRedetection() {
  if (!reviewSceneId || !reviewBatchId) return;
  clearTimeout(redetectionTimer);
  const batchId = reviewBatchId, sceneId = reviewSceneId, key = `${batchId}/${sceneId}`;
  const run = await request(`/api/v1/review/batches/${batchId}/scenes/${sceneId}/redetection`);
  redetectionRuns.set(key, run);
  if (run && ['queued','processing'].includes(run.state)) {
    redetectionTimer = setTimeout(async () => {
      if (reviewMode !== 'scenes' || key !== redetectionKey()) return;
      try {
        await loadRedetection();
        const scene = reviewScenes.find(item => item.scene_id === sceneId);
        const panel = $('#scene-redetection-panel');
        if (scene && panel && key === redetectionKey()) panel.innerHTML = redetectionPanel(scene);
      } catch (error) { message($('#review-result'), error.message, true); }
    }, 2500);
  }
}

function redetectionPanel(scene) {
  const key = `${reviewBatchId}/${scene.scene_id}`;
  const run = redetectionRuns.get(key);
  const threshold = redetectionThresholds.get(key) ?? run?.threshold ?? (typeof appPreferences === "undefined" ? 0.8 : appPreferences.detection_threshold);
  const busy = run && ['queued','processing'].includes(run.state);
  let result = '';
  if (busy) result = `<p role="status">${run.state === 'queued' ? 'Queued' : 'Re-detecting'} · ${run.progress_current}/${run.progress_total} existing frames</p><progress value="${run.progress_current}" max="${Math.max(1,run.progress_total)}"></progress>`;
  if (run?.state === 'failed') result = `<p role="alert">${escapeHtml(run.error || 'Re-detection failed')}</p>`;
  if (run?.state === 'accepted') result = `<p>Added ${run.summary.accepted} new faces to outstanding review. Existing faces and decisions were kept.</p>`;
  if (run?.state === 'discarded') result = '<p>Preview discarded. Scene review is unchanged.</p>';
  if (run?.state === 'ready') {
    const summary = run.summary;
    result = `<section><h4>Preview at confidence ${run.threshold.toFixed(2)}</h4><p>${summary.frames} existing frames · ${summary.new} new candidates · ${summary.matched_existing} existing faces detected again · ${summary.existing_not_detected} existing faces not detected this time</p><p>Existing faces are kept in all cases. Select only new faces you want to add to this scene’s review. Nothing is identified automatically.</p>${run.proposals.length ? `<label><input type="checkbox" id="redetection-select-all" />Select all new candidates</label><div class="face-grid">${run.proposals.map(face => `<div><label><input type="checkbox" data-redetect-face="${escapeHtml(face.id)}" />Add this face</label>${reviewFace(face)}<p>Detection score ${face.detector_confidence.toFixed(3)}</p></div>`).join('')}</div><button data-redetect-action="apply">Add selected new faces</button>` : '<p>No additional faces were found. You can discard this preview or try a lower confidence.</p>'}</section>`;
  }
  return `<article class="redetection-panel"><h3>Re-detect faces in this scene</h3><label for="scene-detection-threshold">Detection confidence: <output id="scene-detection-value">${threshold.toFixed(2)}</output></label><input id="scene-detection-threshold" type="range" min="0.10" max="0.99" step="0.01" value="${threshold}" /><p>Lower confidence finds more potential faces and more false detections. Uses current extracted frames only. The slider does not start detection.</p><div class="review-toolbar"><button class="secondary" data-redetect-action="reset">Reset to default</button><button data-redetect-action="start" ${busy ? 'disabled' : ''}>Re-detect faces</button><button class="secondary" data-redetect-action="refresh">Refresh preview</button></div>${result}${run && ['ready','failed'].includes(run.state) ? '<button class="secondary" data-redetect-action="discard">Discard preview</button>' : ''}</article>`;
}

async function handleRedetectionAction(button) {
  const action = button.dataset.redetectAction, key = redetectionKey();
  if (action === 'reset') {
    const threshold = typeof appPreferences === 'undefined' ? 0.8 : appPreferences.detection_threshold;
    redetectionThresholds.set(key,threshold);
    $('#scene-detection-threshold').value = threshold;
    $('#scene-detection-value').textContent = threshold.toFixed(2);
    return;
  }
  if (action === 'start') {
    const threshold = Number($('#scene-detection-threshold').value);
    redetectionThresholds.set(key,threshold);
    await request(`/api/v1/review/batches/${reviewBatchId}/scenes/${reviewSceneId}/redetection`, {method:'POST',body:JSON.stringify({threshold})});
  } else if (action === 'apply') {
    const run = redetectionRuns.get(key);
    const face_ids = [...$('#scene-redetection-panel').querySelectorAll('[data-redetect-face]:checked')].map(input => input.dataset.redetectFace);
    if (!face_ids.length) throw new Error('Select new faces to add, or discard the preview.');
    const result = await request(`/api/v1/review/redetections/${run.id}/apply`, {method:'POST',body:JSON.stringify({face_ids})});
    message($('#review-result'), `Added ${result.accepted} new faces. Existing confirmations and ignored faces are unchanged.`);
    await loadReview();
    return;
  } else if (action === 'discard') {
    await request(`/api/v1/review/redetections/${redetectionRuns.get(key).id}/discard`, {method:'POST'});
  }
  await loadRedetection();
  const scene = reviewScenes.find(item => item.scene_id === reviewSceneId);
  if (scene) $('#scene-redetection-panel').innerHTML = redetectionPanel(scene);
}

$('#review-content').addEventListener('input', event => {
  if (event.target.id !== 'scene-detection-threshold') return;
  const value = Number(event.target.value);
  redetectionThresholds.set(redetectionKey(),value);
  $('#scene-detection-value').textContent = value.toFixed(2);
});
$('#review-content').addEventListener('change', event => {
  if (event.target.id !== 'redetection-select-all') return;
  for (const input of $('#scene-redetection-panel').querySelectorAll('[data-redetect-face]')) input.checked = event.target.checked;
});
