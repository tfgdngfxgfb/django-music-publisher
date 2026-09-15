(() => {
  const workspace = document.querySelector('[data-digitization]');
  if (!workspace) return;
  const rows = [...workspace.querySelectorAll('[data-digitization-row]')];
  const select = row => {
    rows.forEach(item => {const active = item === row; item.classList.toggle('selected', active); item.setAttribute('aria-selected', String(active)); item.tabIndex = active ? 0 : -1;});
    workspace.querySelector('.digitization-empty-detail').hidden = true;
    workspace.querySelectorAll('[data-digitization-detail]').forEach(panel => {panel.hidden = panel.dataset.digitizationDetail !== row.dataset.digitizationRow;});
    sessionStorage.setItem(`digitization:${location.pathname}`, row.dataset.digitizationRow);
  };
  rows.forEach(row => row.addEventListener('click', () => select(row)));
  workspace.addEventListener('keydown', event => {
    if (event.target.closest('input,select,textarea,button,a,summary')) return;
    const row = event.target.closest('[data-digitization-row]');
    if (!row) return;
    if (event.key === ' ') {event.preventDefault(); const checkbox = row.querySelector('[name=assets]'); checkbox.checked = !checkbox.checked; updateSelection(); return;}
    if (!['ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
    event.preventDefault();
    let index = rows.indexOf(row) + (event.key === 'ArrowDown' ? 1 : -1);
    if (event.key === 'Home') index = 0;
    if (event.key === 'End') index = rows.length - 1;
    const next = rows[Math.max(0, Math.min(rows.length - 1, index))];
    select(next); next.focus({preventScroll:true}); next.scrollIntoView({block:'nearest'});
  });
  const updateSelection = () => {
    const selected = rows.filter(row => row.querySelector('[name=assets]').checked);
    const count = workspace.querySelector('[data-selection-count]');
    if (count) count.textContent = `${selected.length} valgt`;
    workspace.querySelectorAll('[data-bulk-action]').forEach(button => {
      button.disabled = !selected.length || (button.value === 'select_master' && selected.some(row => row.dataset.canSelect !== 'true'));
    });
  };
  workspace.addEventListener('change', updateSelection);
  workspace.querySelector('[data-select-all]')?.addEventListener('change', event => {rows.forEach(row => {row.querySelector('[name=assets]').checked = event.target.checked;}); updateSelection();});
  workspace.querySelectorAll('[data-select-master]').forEach(link => link.addEventListener('click', () => {const row = rows.find(item => item.dataset.digitizationRow === link.dataset.selectMaster); if (row) select(row);}));
  const saved = sessionStorage.getItem(`digitization:${location.pathname}`);
  const initial = rows.find(row => row.dataset.digitizationRow === saved) || rows[0];
  if (initial) select(initial);
  updateSelection();
  workspace.querySelectorAll('[data-recording-picker]').forEach(picker => {
    let serial = 0, timer;
    const input = picker.querySelector('[data-recording-query]');
    const results = picker.querySelector('[data-recording-results]');
    input.addEventListener('input', () => {
      clearTimeout(timer); const request = ++serial;
      picker.querySelector('[data-recording-id]').value = '';
      results.replaceChildren();
      if (input.value.trim().length < 2) return;
      timer = setTimeout(async () => {
        try {
          const response = await fetch(`${window.P7_V2.recordingSearch}?q=${encodeURIComponent(input.value)}`);
          const data = await response.json();
          if (request !== serial) return;
          for (const recording of data.results || []) {
            const button = document.createElement('button'); button.type = 'button'; button.textContent = `${recording.title} · ${recording.artist || ''}`;
            button.addEventListener('click', () => {picker.querySelector('[data-recording-id]').value = recording.id; picker.querySelector('[data-recording-choice]').textContent = button.textContent; results.replaceChildren();}); results.append(button);
          }
        } catch {if (request === serial) results.textContent = 'Søket kunne ikke fullføres.';}
      }, 180);
    });
  });
})();
