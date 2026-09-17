(() => {
  const root = document.querySelector('[data-followup]');
  if (!root || root.dataset.initialized) return;
  root.dataset.initialized = 'true';
  const dialog = root.querySelector('[data-followup-filters]');
  const trigger = root.querySelector('[data-open-followup-filters]');
  trigger.addEventListener('click', () => dialog.showModal());
  root.querySelector('[data-close-followup-filters]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => trigger.focus());
  if (dialog.querySelector('.errorlist')) dialog.showModal();
})();
