(() => {
  const matrix = document.querySelector('[data-release-matrix]');
  if (!matrix) return;
  const rows = [...matrix.querySelectorAll('[data-matrix-row]')];
  const search = matrix.querySelector('[data-matrix-search]');
  const filter = matrix.querySelector('[data-matrix-filter]');
  const requestedFilter = new URLSearchParams(location.search).get('filter');
  if ([...filter.options].some(option => option.value === requestedFilter)) filter.value = requestedFilter;
  const count = matrix.querySelector('[data-matrix-count]');
  let selected = null;
  const select = row => {
    selected = row;
    rows.forEach(item => {
      const active = item === row;
      item.classList.toggle('selected', active);
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
    });
    matrix.querySelectorAll('[data-matrix-detail]').forEach(panel => {
      panel.hidden = panel.dataset.matrixDetail !== row?.dataset.matrixRow;
    });
  };
  const applyFilter = () => {
    const term = search.value.trim().toLocaleLowerCase();
    const mode = filter.value;
    rows.forEach(row => {
      const kind = row.dataset.matrixFilterKey;
      const matches = {
        all: true,
        issues: kind !== 'complete',
        missing_master: row.dataset.hasBatchMaster !== 'true',
        missing_selected: row.dataset.hasSelected !== 'true',
        no_radio: row.dataset.hasCurrent !== 'true',
        previous_master: row.dataset.currentState === 'from_previous_master',
        unknown_lineage: row.dataset.currentState === 'lineage_unknown',
        candidate: row.dataset.hasCandidate === 'true',
        generation_failed: row.dataset.generationState === 'failed',
      }[mode];
      row.hidden = Boolean(term && !row.cells[1].textContent.toLocaleLowerCase().includes(term)) ||
        !matches;
    });
    const visible = rows.filter(row => !row.hidden);
    if (!visible.includes(selected)) select(visible[0] || null);
    count.textContent = `${visible.length} av ${rows.length} spor`;
    matrix.querySelector('[data-matrix-empty]').hidden = visible.length > 0;
  };
  rows.forEach(row => {
    row.addEventListener('click', () => select(row));
    row.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); select(row); return;
      }
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const visible = rows.filter(item => !item.hidden);
      const index = visible.indexOf(row);
      const next = event.key === 'Home' ? visible[0] : event.key === 'End' ? visible.at(-1) :
        visible[Math.max(0, Math.min(visible.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))];
      select(next); next.focus({preventScroll: true}); next.scrollIntoView({block: 'nearest'});
    });
  });
  search.addEventListener('input', applyFilter);
  filter.addEventListener('change', applyFilter);
  matrix.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !selected) return;
    matrix.querySelectorAll('[data-matrix-detail]').forEach(panel => {panel.hidden = true;});
    selected.focus({preventScroll: true});
  });
  applyFilter();
})();
