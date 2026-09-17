(() => {
  const form = document.querySelector('[data-managed-onboard]');
  if (!form || form.dataset.initialized) return;
  form.dataset.initialized = 'true';
  const kind = form.querySelector('[name=relationship_type]');
  const territory = form.querySelector('[name=territory_mode]');
  const share = form.querySelector('[name=ownership_share]');
  const toggle = (name, show) => {
    const row = form.querySelector(`[data-managed-field="${name}"]`);
    if (!row) return;
    // Keep invalid submitted values visible so validation never hides errors.
    row.hidden = !show && !row.querySelector('.errorlist');
  };
  const update = () => {
    const ownership = kind.value === 'master_ownership';
    share.disabled = !ownership;
    share.required = ownership;
    if (ownership && !share.value) share.value = '100';
    toggle('ownership_share', kind.value === 'master_ownership');
    toggle('release_scope', kind.value !== 'master_ownership');
    toggle('territories', territory.value !== 'world');
  };
  kind.addEventListener('change', update);
  territory.addEventListener('change', update);
  update();
})();
