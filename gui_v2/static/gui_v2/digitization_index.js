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

(() => {
  const dashboard = document.querySelector('[data-digitization-dashboard]');
  if (!dashboard) return;
  const rows = [...dashboard.querySelectorAll('[data-dashboard-row]')];
  const select = row => {
    rows.forEach(item => {
      const active = item === row;
      item.classList.toggle('selected', active);
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
    });
    dashboard.querySelectorAll('[data-dashboard-detail]').forEach(panel => {
      panel.hidden = panel.dataset.dashboardDetail !== row.dataset.dashboardRow;
    });
  };
  rows.forEach(row => {
    row.addEventListener('click', () => select(row));
    row.addEventListener('keydown', event => {
      if (event.target.closest('a,button,input,select')) return;
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); select(row); return;
      }
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const index = rows.indexOf(row);
      const next = rows[Math.max(0, Math.min(rows.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))];
      select(next); next.focus({preventScroll: true}); next.scrollIntoView({block: 'nearest'});
    });
  });
  if (rows.length) select(rows[0]);
})();
