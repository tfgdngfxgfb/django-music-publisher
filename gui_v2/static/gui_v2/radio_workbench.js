(() => {
  const workspace = document.querySelector('[data-radio-workbench]');
  if (!workspace) return;
  const rows = [...workspace.querySelectorAll('[data-radio-row]')];
  const select = row => {
    rows.forEach(item => {
      const active = item === row;
      item.classList.toggle('selected', active);
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
    });
    workspace.querySelectorAll('[data-radio-detail]').forEach(detail => {
      detail.hidden = detail.dataset.radioDetail !== row.dataset.radioRow;
    });
  };
  rows.forEach(row => {
    row.addEventListener('click', event => {
      if (!event.target.closest('a,button,input')) select(row);
    });
    row.addEventListener('keydown', event => {
      if (event.target.closest('a,button,input')) return;
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); select(row); return;
      }
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const index = rows.indexOf(row) + (event.key === 'ArrowDown' ? 1 : -1);
      const next = rows[Math.max(0, Math.min(rows.length - 1, index))];
      select(next); next.focus({preventScroll: true}); next.scrollIntoView({block: 'nearest'});
    });
  });
  if (rows.length) select(rows[0]);
})();
