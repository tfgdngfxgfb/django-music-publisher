(() => {
  const form = document.querySelector('[data-batch-form]');
  const button = form?.querySelector('[data-use-release-title]');
  if (!button) return;
  button.addEventListener('click', () => {
    const release = form.querySelector('[name=release]');
    const title = form.querySelector('[name=title]');
    const option = release?.selectedOptions[0];
    if (!option?.value || !title) return;
    title.value = option.textContent.trim();
    title.focus();
  });
})();
