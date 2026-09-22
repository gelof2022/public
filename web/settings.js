let appPreferences = {detection_threshold:0.8, recognition_threshold:0.2, grouping_threshold:0.5, theme:'dark', stash_playback_fallback:true};
const preferencesForm = document.querySelector('#preferences-form');
function applyPreferences(value) {
  appPreferences = value;
  document.documentElement.dataset.theme = value.theme;
  document.documentElement.style.colorScheme = value.theme;
  reviewGroupingThreshold = value.grouping_threshold;
  reviewMinimumSimilarity = value.recognition_threshold;
  for (const [name, setting] of Object.entries(value)) {
    const input = preferencesForm.elements.namedItem(name);
    if (input.type === 'checkbox') input.checked = setting;
    else input.value = setting;
  }
}
preferencesForm.addEventListener('submit', async event => {
  event.preventDefault();
  const value = {...appPreferences};
  for (const name of Object.keys(value)) {
    const input = preferencesForm.elements.namedItem(name);
    value[name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
  }
  try {
    applyPreferences(await request('/api/v1/preferences', {method:'POST', body:JSON.stringify(value)}));
    document.querySelector('#preferences-result').textContent = 'Settings saved.';
  } catch (error) { document.querySelector('#preferences-result').textContent = error.message; }
});
request('/api/v1/preferences').then(applyPreferences).catch(error => {
  document.querySelector('#preferences-result').textContent = `Unable to load settings: ${error.message}`;
});
