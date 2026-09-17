(() => {
  const form = document.querySelector('[data-rights-form]');
  if (!form || form.dataset.initialized) return;
  form.dataset.initialized = 'true';
  const update = block => {
    const type = block.querySelector('[data-rights-field="right_type"] select');
    const mode = block.querySelector('[data-rights-field="territory_mode"] select');
    const show = (name, visible) => {
      const field = block.querySelector(`[data-rights-field="${name}"]`);
      if (field) field.hidden = !visible && !field.hasAttribute('data-has-errors');
    };
    if (type) {
      show('share', type.value === 'master_ownership');
      show('release_scope', type.value !== 'master_ownership');
    }
    if (mode) show('territories', mode.value !== 'world');
  };
  form.querySelectorAll('[data-claim-fields]').forEach(update);
  form.addEventListener('change', event => {
    const block = event.target.closest('[data-claim-fields]');
    if (block) update(block);
  });
  form.querySelector('[data-add-replacement]')?.addEventListener('click', event => {
    const total = form.querySelector('[name="positions-TOTAL_FORMS"]');
    const index = Number(total.value);
    if (index >= 20) return;
    const template = form.querySelector('[data-empty-replacement]');
    const target = form.querySelector('[data-replacements]');
    target.insertAdjacentHTML('beforeend', template.innerHTML.replaceAll('__prefix__', String(index)));
    total.value = index + 1;
    update(target.lastElementChild);
    target.lastElementChild.querySelector('input:not([type="hidden"]),select:not(:disabled)')?.focus();
    event.currentTarget.disabled = index + 1 >= 20;
  });
})();
