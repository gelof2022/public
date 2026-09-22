const $ = (selector) => document.querySelector(selector);
const selectedScenes = new Set();
let currentScenes = [];
let scenePage = 1;
const scenePageSize = 40;

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : data.detail?.message || JSON.stringify(data.detail || data));
    error.detail = data.detail;
    throw error;
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function message(target, text, error = false) {
  target.textContent = text;
  target.className = `message ${error ? "error" : "success"}`;
}

function formatBytes(value) {
  if (value == null) return "Unknown size";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = Number(value), unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function formatDuration(seconds) {
  if (seconds == null) return "Unknown duration";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}` : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function sceneLabel(scene) {
  return scene.title || scene.source_path.split("/").pop() || `Stash scene ${scene.stash_scene_id}`;
}

function filterParams(includePage = true) {
  const params = new URLSearchParams();
  const values = {
    query: $("#scene-search").value.trim(), processed: $("#scene-status").value,
    date_from: $("#date-from").value, date_to: $("#date-to").value,
    review_batch: $("#scene-review-batch").value, awaiting_review: $("#scene-awaiting-review").checked ? "true" : "",
  };
  Object.entries(values).forEach(([key, value]) => { if (value && value !== "all") params.set(key, value); });
  if (includePage) { params.set("page", scenePage); params.set("per_page", scenePageSize); }
  return params;
}

function metadataDetails(scene) {
  const tags = { ...(scene.media_metadata?.format?.tags || {}), ...(scene.media_metadata?.video?.tags || {}) };
  const tagRows = Object.entries(tags).slice(0, 8).map(([key, value]) => `<dt>${escapeHtml(key.replaceAll("_", " "))}</dt><dd>${escapeHtml(value)}</dd>`).join("");
  return `<details class="scene-details"><summary>Technical details</summary><dl>
    <dt>Path</dt><dd>${escapeHtml(scene.source_path)}</dd>
    <dt>Stash ID</dt><dd>${escapeHtml(scene.stash_scene_id)}</dd>
    <dt>Video</dt><dd>${escapeHtml(scene.video_codec || "unknown")} · ${escapeHtml(scene.media_format || "unknown format")}</dd>
    <dt>Audio</dt><dd>${escapeHtml(scene.audio_codec || "unknown")}</dd>
    <dt>Bit rate</dt><dd>${scene.bit_rate ? `${(scene.bit_rate / 1000000).toFixed(2)} Mbps` : "unknown"}</dd>
    <dt>Created</dt><dd>${escapeHtml(scene.source_created_at || "unknown")}</dd>
    <dt>Modified</dt><dd>${escapeHtml(scene.source_modified_at || "unknown")}</dd>
    <dt>Probed</dt><dd>${escapeHtml(scene.probed_at || "not yet")}</dd>${tagRows}
  </dl></details>`;
}

function renderScene(scene) {
  const status = scene.processed ? `${scene.frame_count} frames extracted` : "Not processed";
  const dimensions = scene.width && scene.height ? `${scene.width}×${scene.height}` : "Unknown resolution";
  return `<div class="scene-card ${selectedScenes.has(scene.id) ? "selected" : ""}">
    <input type="checkbox" value="${scene.id}" ${selectedScenes.has(scene.id) ? "checked" : ""} />
    <div class="scene-thumb">${scene.thumbnail_url ? `<img src="${scene.thumbnail_url}" loading="lazy" alt="" />` : "<span>No thumbnail</span>"}</div>
    <div class="scene-content">
      <div class="scene-title"><strong>${escapeHtml(sceneLabel(scene))}</strong><span class="processing-chip ${scene.processed ? "processed" : ""}">${status}</span></div>
      <p>${escapeHtml(scene.scene_date || "Undated")} · ${formatDuration(scene.duration_seconds)} · ${dimensions} · ${scene.frame_rate ? `${Number(scene.frame_rate).toFixed(2)} fps` : "unknown fps"} · ${formatBytes(scene.file_size)}</p>
      ${metadataDetails(scene)}
    </div>
  </div>`;
}

function updateSelectedCount() {
  $("#selected-count").textContent = `${selectedScenes.size.toLocaleString()} scene${selectedScenes.size === 1 ? "" : "s"} selected`;
}

async function loadHealth() {
  try {
    const health = await request("/health");
    $("#health").textContent = `API ${health.version} · ready`;
    $("#app-version").textContent = `v${health.version}`;
    $("#health").className = "status ready";
  } catch { $("#health").textContent = "API unavailable"; $("#health").className = "status error"; }
}

async function loadSettings() {
  const settings = await request("/api/v1/settings");
  $("#settings").innerHTML = `<dt>URL</dt><dd>${escapeHtml(settings.stash_url || "Not configured")}</dd>
    <dt>API key</dt><dd>${settings.stash_api_key_configured ? "Configured" : "Not configured"}</dd>
    <dt>Path map</dt><dd><code>${escapeHtml(settings.stash_path_prefix)} → ${escapeHtml(settings.media_path_prefix)}</code></dd>
    <dt>App data</dt><dd><code>${escapeHtml(settings.appdata_path)}</code></dd>`;
}

async function loadScenes() {
  const data = await request(`/api/v1/scenes?${filterParams()}`);
  currentScenes = data.items;
  $("#scene-count").textContent = data.total.toLocaleString();
  $("#scene-picker").innerHTML = data.items.length ? data.items.map(renderScene).join("") : '<p class="empty">No matching scenes.</p>';
  const pageCount = Math.max(1, Math.ceil(data.total / scenePageSize));
  if (scenePage > pageCount) { scenePage = pageCount; return loadScenes(); }
  $("#scene-page").textContent = `Page ${scenePage} of ${pageCount} · ${data.total.toLocaleString()} results`;
  $("#scene-prev").disabled = scenePage <= 1;
  $("#scene-next").disabled = scenePage >= pageCount;
  $("#scene-picker").querySelectorAll("input[type=checkbox]").forEach((input) => input.addEventListener("change", () => {
    if (input.checked) selectedScenes.add(input.value); else selectedScenes.delete(input.value);
    input.closest(".scene-card").classList.toggle("selected", input.checked);
    updateSelectedCount();
  }));
  $("#scene-picker").querySelectorAll("img").forEach((image) => image.addEventListener("error", () => {
    image.closest(".scene-thumb").innerHTML = "<span>Thumbnail unavailable</span>";
  }));
}

async function selectMatching(overrides = {}, label = "matches") {
  const params = filterParams(false);
  Object.entries(overrides).forEach(([key, value]) => params.set(key, value));
  const result = await request(`/api/v1/scenes/ids?${params}`);
  result.ids.forEach((id) => selectedScenes.add(id));
  updateSelectedCount(); await loadScenes();
  message($("#batch-result"), `Added ${result.ids.length.toLocaleString()} ${label}.`);
}

function batchActions(batch) {
  if (batch.archived) return `<div class="batch-actions"><button data-restore="${batch.id}">Restore batch</button></div>`;
  const canProcess = ["created", "failed", "video_processing_complete"].includes(batch.state);
  const stage = name => `class="${batch.stages?.[name] ? 'stage-complete' : 'stage-pending'}" title="${batch.stages?.[name] ? 'Complete' : 'Outstanding or needs refresh'}"`;
  return `<div class="batch-actions">${canProcess ? `<button ${stage('frames')} data-process="${batch.id}">${batch.state === "video_processing_complete" ? "Reprocess" : "Start processing"}</button>` : ""}
    ${batch.frame_count ? `<button class="secondary" data-frames="${batch.id}">View frames</button>` : ""}
    ${batch.frame_count && ["video_processing_complete", "failed"].includes(batch.state) ? `<button ${stage('detection')} data-detect="${batch.id}">Detect faces</button>` : ""}
    ${batch.frame_count && ["video_processing_complete", "failed"].includes(batch.state) ? `<button ${stage('grouping')} data-group="${batch.id}">Group faces</button>` : ""}
    ${batch.frame_count ? `<button class="secondary" data-faces="${batch.id}">View faces</button>` : ""}
    ${batch.frame_count ? `<button ${stage('review')} data-review="${batch.id}">Review batch</button>` : ""}
    ${!["queued", "processing", "committing", "committed"].includes(batch.state) ? `<button class="secondary" data-archive="${batch.id}">Archive</button><button class="danger-button" data-delete="${batch.id}">Delete</button>` : ""}</div>`;
}

let batchesLoading = false;
async function loadBatches() {
  if (batchesLoading) return;
  batchesLoading = true;
  try {
  const batches = await request(`/api/v1/batches?include_archived=${Boolean($('#show-archived-batches')?.checked)}`);
  const batchFilter = $('#scene-review-batch');
  const selectedBatch = batchFilter.value;
  batchFilter.innerHTML = '<option value="">Latest active batch per scene</option>' + batches.filter(batch => !batch.archived).map(batch => `<option value="${batch.id}">${escapeHtml(batch.name)}</option>`).join('');
  batchFilter.value = batches.some(batch => !batch.archived && batch.id===selectedBatch) ? selectedBatch : '';
  if (!$('#tab-batches').hidden) for (let offset=0; offset<batches.length; offset+=2) await Promise.all(batches.slice(offset,offset+2).map(async batch => {
    try { batch.stages = await request(`/api/v1/batches/${batch.id}/stages`); }
    catch { batch.stages = {}; }
  }));
  $("#batch-list").innerHTML = batches.length ? batches.map((batch) => {
    const percent = batch.progress_total ? Math.round(batch.progress_current / batch.progress_total * 100) : 0;
    const errors = batch.errors.map((item) => `<li><strong>${escapeHtml(item.title || item.source_path.split("/").pop())}</strong><span>${escapeHtml(item.error)}</span></li>`).join("");
    return `<section class="batch-row"><div class="batch-main"><div class="scene-title"><strong>${escapeHtml(batch.name)}</strong><span class="processing-chip ${batch.state === "video_processing_complete" ? "processed" : ""}">${escapeHtml(batch.state.replaceAll("_", " "))}</span></div>
      <p>${batch.scene_count} scenes · ${batch.frame_count} frames</p><progress value="${batch.progress_current}" max="${batch.progress_total || 1}"></progress>
      <small>${batch.latest_job_type === "face_grouping" ? "Face embeddings / grouping" : batch.latest_job_type === "face_detection" ? "Face detection" : "Frame extraction"}: ${percent}% (${batch.progress_current}/${batch.progress_total}) · ${escapeHtml(batch.latest_job_state || "not started")}</small>
      ${batch.failed_scene_count ? `<details class="batch-errors" open><summary>${batch.failed_scene_count} scene errors</summary><ul>${errors}</ul></details>` : batch.error ? `<p class="error-detail">${escapeHtml(batch.error)}</p>` : ""}
      </div>${batchActions(batch)}</section>`;
  }).join("") : '<p class="empty">No batches yet.</p>';
  } finally { batchesLoading = false; }
}

async function showFrames(batchId, selectedTab = "frames") {
  const frames = [];
  for (let offset = 0; ; offset += 1000) {
    const page = await request(`/api/v1/batches/${batchId}/frames?limit=1000&offset=${offset}`);
    frames.push(...page);
    if (page.length < 1000) break;
  }
  const faces = [];
  for (let offset = 0; ; offset += 1000) {
    const page = await request(`/api/v1/batches/${batchId}/faces?limit=1000&offset=${offset}`);
    faces.push(...page);
    if (page.length < 1000) break;
  }
  const grouping = await request(`/api/v1/batches/${batchId}/face-groups`);
  const groupsByScene = new Map(grouping.map(item => [item.scene_id, item]));
  const facesByScene = new Map();
  faces.forEach(face => {
    if (!facesByScene.has(face.scene_id)) facesByScene.set(face.scene_id, []);
    facesByScene.get(face.scene_id).push(face);
  });
  $("#frames-section").hidden = false;
  const groups = new Map();
  frames.forEach((frame) => { if (!groups.has(frame.scene_id)) groups.set(frame.scene_id, []); groups.get(frame.scene_id).push(frame); });
  $("#frame-gallery").innerHTML = frames.length ? [...groups.values()].map((sceneFrames) => {
    const scene = sceneFrames[0];
    const sceneFaces = facesByScene.get(scene.scene_id) || [];
    const tabs = `<div class="scene-view-tabs" role="group" aria-label="${escapeHtml(scene.scene_title || scene.scene_id)} views"><button type="button" data-scene-view="frames" aria-pressed="${selectedTab === 'frames'}">Frames (${sceneFrames.length})</button><button type="button" data-scene-view="faces" aria-pressed="${selectedTab === 'faces'}">Faces (${sceneFaces.length})</button></div>`;
    return `<section class="scene-frame-group"><div class="scene-frame-heading">${scene.scene_thumbnail_url ? `<img src="${escapeHtml(scene.scene_thumbnail_url)}" loading="lazy" alt="" />` : ""}<div><h4>${escapeHtml(scene.scene_title || scene.scene_id)} <span class="scene-duration">· ${formatDuration(scene.scene_duration_seconds)}</span></h4><a class="secondary play-scene" href="${escapeHtml(scene.video_url)}" target="_blank" rel="noopener">Play scene</a></div></div>${tabs}<div class="frame-row" data-scene-panel="frames" ${selectedTab !== "frames" ? "hidden" : ""}>${sceneFrames.map((frame) => `<figure><a href="${escapeHtml(frame.video_url)}#t=${frame.timestamp_seconds}" target="_blank" rel="noopener" aria-label="Play scene at ${frame.timestamp_seconds.toFixed(2)} seconds"><img src="${escapeHtml(frame.url)}" loading="lazy" alt="Extracted video frame" /></a><figcaption>${frame.timestamp_seconds.toFixed(2)}s</figcaption></figure>`).join("")}</div><div data-scene-panel="faces" ${selectedTab !== "faces" ? "hidden" : ""}>${sceneFaces.length ? `<p class="face-score-note">Quality scores describe image quality; detection scores describe how strongly the detector considers the region a face. Neither identifies a person.</p>${renderFaceGroups(sceneFaces, groupsByScene.get(scene.scene_id))}` : '<p class="empty">No face observations available for this video. If detection has not finished, run Detect faces and reopen this batch.</p>'}</div></section>`;
  }).join("") : '<p class="empty">No frames available.</p>';
  $("#frames-section").scrollIntoView({ behavior: "smooth" });
}

document.querySelectorAll(".tab-button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".tab-button").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll(".tab-panel").forEach((panel) => { panel.hidden = panel.id !== `tab-${button.dataset.tab}`; panel.classList.toggle("active", !panel.hidden); });
  if (button.dataset.tab === "batches") loadBatches();
  if (button.dataset.tab === "review") loadReview().catch(error => message($("#review-result"), error.message, true));
}));

let searchTimer;
[$("#scene-search"), $("#scene-status"), $("#date-from"), $("#date-to"), $("#scene-review-batch"), $("#scene-awaiting-review")].forEach((input) => input.addEventListener("input", () => {
  clearTimeout(searchTimer); searchTimer = setTimeout(() => { scenePage = 1; loadScenes().catch(() => {}); }, 250);
}));
$("#scene-prev").addEventListener("click", () => { scenePage -= 1; loadScenes(); });
$("#scene-next").addEventListener("click", () => { scenePage += 1; loadScenes(); });
$("#select-page").addEventListener("click", () => { currentScenes.forEach((scene) => selectedScenes.add(scene.id)); updateSelectedCount(); loadScenes(); });
$("#select-all").addEventListener("click", () => selectMatching({}, "matching scenes"));
$("#select-next").addEventListener("click", () => selectMatching({ processed: "unprocessed", limit: $("#quick-count").value }, "unprocessed scenes"));
$("#clear-selection").addEventListener("click", () => { selectedScenes.clear(); updateSelectedCount(); loadScenes(); });

$("#batch-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const target = $("#batch-result");
  if (!selectedScenes.size) return message(target, "Select at least one scene.", true);
  try {
    const batch = await request("/api/v1/batches", { method: "POST", body: JSON.stringify({ name: $("#batch-name").value, scene_ids: [...selectedScenes] }) });
    message(target, `Created “${batch.name}” with ${batch.scene_count} scenes.`); selectedScenes.clear(); updateSelectedCount(); await loadScenes(); await loadBatches();
  } catch (error) { message(target, error.message, true); }
});

$("#batch-list").addEventListener("click", async (event) => {
  if (event.target.dataset.archive || event.target.dataset.restore) {
    const button = event.target;
    button.disabled = true;
    try {
      await request(`/api/v1/batches/${button.dataset.archive || button.dataset.restore}/archive?archived=${Boolean(button.dataset.archive)}`,{method:'POST'});
      await loadBatches();
    } catch (error) { message($('#batches-result'),error.message,true); button.disabled = false; }
    return;
  }
  if (event.target.dataset.review) { await openReviewBatch(event.target.dataset.review); return; }
  if (event.target.dataset.group) {
    event.target.disabled = true;
    try { await request(`/api/v1/batches/${event.target.dataset.group}/group-faces`, { method: "POST" }); await loadBatches(); }
    catch (error) { message($("#batches-result"), error.message, true); event.target.disabled = false; }
  }
  if (event.target.dataset.detect) {
    event.target.disabled = true;
    try { await request(`/api/v1/batches/${event.target.dataset.detect}/detect-faces`, { method: "POST" }); await loadBatches(); }
    catch (error) { message($("#batches-result"), error.message, true); event.target.disabled = false; }
  }
  if (event.target.dataset.faces) {
    try { await showFaces(event.target.dataset.faces); }
    catch (error) { message($("#batches-result"), error.message, true); }
  }
  if (event.target.dataset.process) {
    event.target.disabled = true;
    try { await request(`/api/v1/batches/${event.target.dataset.process}/process`, { method: "POST" }); await loadBatches(); }
    catch (error) { message($("#batches-result"), error.message, true); event.target.disabled = false; }
  }
  if (event.target.dataset.frames) {
    event.target.disabled = true;
    try { await showFrames(event.target.dataset.frames); }
    catch (error) { message($("#batches-result"), error.message, true); }
    finally { event.target.disabled = false; }
  }
  if (event.target.dataset.delete && window.confirm("Delete this batch? Generated frames remain available for reuse.")) {
    event.target.disabled = true;
    try { await request(`/api/v1/batches/${event.target.dataset.delete}`, { method: "DELETE" }); await loadBatches(); }
    catch (error) { message($("#batches-result"), error.message, true); event.target.disabled = false; }
  }
});

$("#test-stash").addEventListener("click", async () => {
  const target = $("#stash-result"); message(target, "Testing connection…");
  try { const result = await request("/api/v1/stash/test", { method: "POST" }); message(target, result.connected ? `Connected to Stash ${result.version}` : result.error, !result.connected); }
  catch (error) { message(target, error.message, true); }
});

$("#sync-stash").addEventListener("click", async (event) => {
  event.currentTarget.disabled = true; const target = $("#stash-result"); message(target, "Syncing scene metadata…");
  try { const result = await request("/api/v1/stash/sync", { method: "POST" }); message(target, `Synced: ${result.imported} new, ${result.updated} updated, ${result.skipped_without_files} skipped.`); scenePage = 1; await loadScenes(); }
  catch (error) { message(target, error.message, true); } finally { event.currentTarget.disabled = false; }
});

$("#refresh").addEventListener("click", loadBatches);
Promise.allSettled([loadHealth(), loadSettings(), loadScenes(), loadBatches()]);
setInterval(() => { if (!document.hidden && !$('#tab-batches').hidden) loadBatches().catch(() => {}); }, 10000);


$("#frame-gallery").addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-scene-view]");
  if (toggle) {
    const row = toggle.closest(".scene-frame-group");
    row.querySelectorAll("[data-scene-view]").forEach(button => button.setAttribute("aria-pressed", String(button === toggle)));
    row.querySelectorAll("[data-scene-panel]").forEach(panel => { panel.hidden = panel.dataset.scenePanel !== toggle.dataset.sceneView; });
    return;
  }
  const cluster = event.target.closest("[data-cluster]");
  if (cluster) {
    const browser = cluster.closest(".cluster-browser");
    const opening = cluster.getAttribute("aria-expanded") !== "true";
    browser.querySelectorAll("[data-cluster]").forEach(button => button.setAttribute("aria-expanded", String(opening && button === cluster)));
    browser.querySelectorAll("[data-cluster-panel]").forEach(panel => { panel.hidden = !opening || panel.dataset.clusterPanel !== cluster.dataset.cluster; });
    return;
  }
  const link = event.target.closest("a[href]");
  if (!link) return;
  event.preventDefault();
  openScenePlayer(link.getAttribute("href"));
});

let playbackFallback = false;
let playbackTimestamp = 0;
let playbackSource = "";
function openScenePlayer(href) {
  const player = $("#scene-player");
  const [url, fragment = ""] = href.split("#");
  const timestamp = Number(new URLSearchParams(fragment).get("t")) || 0;
  playbackFallback = false;
  playbackTimestamp = timestamp;
  playbackSource = url;
  $("#video-error").textContent = "";
  player.onloadedmetadata = () => { player.currentTime = timestamp; };
  player.src = url;
  $("#video-dialog").showModal();
  player.play().catch(() => {
    if (!player.error) $("#video-error").textContent = "Press Play to start the video.";
  });
}
$("#scene-player").addEventListener("error", () => {
  const player = $("#scene-player");
  if (playbackSource && !playbackFallback && /\/video$/.test(playbackSource) &&
      (typeof appPreferences === "undefined" || appPreferences.stash_playback_fallback)) {
    playbackFallback = true;
    const timestamp = player.currentTime || playbackTimestamp;
    $("#video-error").textContent = "Trying Stash playback…";
    player.onloadedmetadata = () => { player.currentTime = timestamp; $("#video-error").textContent = "Playing through Stash"; };
    player.src = playbackSource.replace(/\/video$/, "/stash-video");
    player.play().catch(() => {});
    return;
  }
  $("#video-error").textContent = "This video could not be played. Its codec may not be supported by this browser, or the media file may be unavailable.";
});
$("#close-video").addEventListener("click", () => $("#video-dialog").close());
$("#video-dialog").addEventListener("close", () => {
  const player = $("#scene-player");
  playbackSource = "";
  player.onloadedmetadata = null;
  player.pause();
  player.removeAttribute("src");
  player.load();
});


async function showFaces(batchId) {
  return showFrames(batchId, "faces");
}

function renderFace(face) {
  const details = face.quality_details || {};
  const number = (value, digits = 0) => Number.isFinite(value) ? value.toFixed(digits) : "Unknown";
  const reasons = [];
  if (details.face_size_px < 32) reasons.push("Small face");
  if (details.sharpness < 20) reasons.push("Blurred / little detail");
  if (details.exposure < 0.5) reasons.push("Poor exposure");
  return `<figure><a href="${escapeHtml(face.video_url)}#t=${face.timestamp_seconds}" aria-label="Play scene at ${face.timestamp_seconds.toFixed(2)} seconds"><img src="${escapeHtml(face.url)}" loading="lazy" alt="Detected face" /></a><figcaption><strong>${face.timestamp_seconds.toFixed(2)}s · ${face.usable ? "Usable" : "Low quality"}</strong><br>Quality ${number(face.quality * 100)}/100<br>Detection score ${number(face.detector_confidence, 3)}${reasons.length ? `<br>${reasons.join(" · ")}` : ""}<details><summary>Quality details</summary><dl><dt>Face size</dt><dd>${number(details.face_size_px)} px</dd><dt>Sharpness</dt><dd>${number(details.sharpness, 1)}</dd><dt>Exposure</dt><dd>${number(details.exposure * 100)}% midtones</dd></dl></details></figcaption></figure>`;
}


function renderFaceGroups(faces, grouping) {
  const all = `<details class="all-observations" ${grouping?.status === 'ready' ? '' : 'open'}><summary>All observations (${faces.length})</summary><div class="face-grid">${faces.map(renderFace).join("")}</div></details>`;
  if (grouping?.status !== "ready") return `<p>${grouping?.status === 'stale' ? 'Observations changed. Run Group faces to refresh these groups.' : 'Run Group faces to generate embeddings and group similar faces within this video.'}</p>${all}`;
  const byId = new Map(faces.map(face => [face.id, face]));
  const cards = [];
  const panels = [];
  grouping.groups.forEach((group, index) => {
    const members = group.face_ids.map(id => byId.get(id)).filter(Boolean);
    if (!members.length) return;
    const representative = group.representative_ids.map(id => byId.get(id)).find(Boolean) || members[0];
    cards.push(`<button type="button" class="cluster-card" data-cluster="${index}" aria-expanded="false" aria-label="Show cluster ${index + 1}, ${members.length} faces"><img src="${escapeHtml(representative.url)}" loading="lazy" alt="Group representative" /><span>${members.length} face${members.length === 1 ? '' : 's'}</span></button>`);
    panels.push(`<section class="cluster-members" data-cluster-panel="${index}" hidden><h5>${members.length} face${members.length === 1 ? '' : 's'} in this cluster</h5><div class="face-grid">${members.map(renderFace).join("")}</div></section>`);
  });
  const groups = `<div class="cluster-browser"><div class="cluster-row">${cards.join("")}</div>${panels.join("")}</div>`;
  const excluded = grouping.excluded_face_ids.map(id => byId.get(id)).filter(Boolean);
  return `<p class="face-score-note">Suggested groups within this video only. These are not confirmed identities; uncertain matches remain separate.</p>${groups}${excluded.length ? `<details><summary>Excluded from grouping · ${excluded.length} low-quality observations</summary><div class="face-grid">${excluded.map(renderFace).join("")}</div></details>` : ''}${all}`;
}

$('#export-stash-people').addEventListener('click', async event => {
  const button = event.currentTarget;
  button.disabled = true;
  message($('#export-stash-result'), 'Syncing people to Stash…');
  try {
    const result = await request('/api/v1/stash/export-people', {method:'POST'});
    message($('#export-stash-result'), `${result.scenes_synced} scenes synced; ${result.created} performers created; ${result.matched} existing performers matched.${result.errors.length ? ' Issues: ' + result.errors.join('; ') : ''}`, result.errors.length > 0);
  } catch (error) { message($('#export-stash-result'), error.message, true); }
  finally { button.disabled = false; }
});

$('#show-archived-batches').addEventListener('change', () => loadBatches().catch(error => message($('#batches-result'),error.message,true)));
