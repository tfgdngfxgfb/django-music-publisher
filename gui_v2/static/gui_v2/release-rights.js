(() => {
  const root = document.querySelector('[data-rights-matrix]');
  if (!root || root.dataset.initialized || !root.querySelector('[data-bulk-dialog]')) return;
  root.dataset.initialized = 'true';
  const rows = [...root.querySelectorAll('[data-matrix-row]')];
  const dialog = root.querySelector('[data-bulk-dialog]');
  const content = root.querySelector('[data-bulk-content]');
  const inspector = root.querySelector('[data-inspector]');
  const search = root.querySelector('[data-matrix-search]');
  const filter = root.querySelector('[data-matrix-filter]');
  let trigger, active, inFlight = false;
  const selected = () => rows.filter(r => r.querySelector('[data-select-row]').checked);
  const remember = () => {
    const url = new URL(location.href);
    url.searchParams.set('tab', 'rights');
    [['track', active || ''], ['q', search.value], ['filter', filter.value === 'all' ? '' : filter.value]].forEach(([key, value]) => value ? url.searchParams.set(key, value) : url.searchParams.delete(key));
    history.replaceState(history.state, '', url);
    root.querySelectorAll('[data-recording-rights]').forEach(a => {
      const target = new URL(a.href); target.searchParams.set('return', url.pathname + url.search); a.href = target;
    });
  };
  const selectionChanged = () => {
    const chosen = selected();
    root.querySelector('[data-selection-bar]').hidden = !chosen.length;
    root.querySelector('[data-selection-count]').textContent = `${new Set(chosen.map(r => r.dataset.recording)).size} innspillinger valgt (${chosen.length} sporplasser)`;
    const visible = rows.filter(r => !r.hidden), all = root.querySelector('[data-select-visible]');
    all.checked = !!visible.length && visible.every(r => r.querySelector('[data-select-row]').checked);
    all.indeterminate = !all.checked && visible.some(r => r.querySelector('[data-select-row]').checked);
  };
  const applyFilter = () => {
    for (const row of rows) {
      const key = filter.value, data = row.dataset;
      const matches = key === 'all' || key === 'followup' && data.followup === 'true' || key === 'unmanaged' && data.unmanaged === 'true' || key === 'ownership' && ['unresolved', 'disputed'].includes(data.ownership) || ['administration', 'distribution'].includes(key) && data[key] === '0';
      row.hidden = !matches || !row.textContent.toLocaleLowerCase().includes(search.value.toLocaleLowerCase());
    }
    remember(); selectionChanged();
  };
  const inspect = row => {
    active = row.dataset.track;
    rows.forEach(r => r.classList.toggle('selected', r === row));
    root.querySelector('[data-inspector-content]').replaceChildren(root.querySelector(`[data-inspector-template="${active}"]`).content.cloneNode(true));
    inspector.hidden = false;
    root.querySelector('[data-matrix-layout]').classList.add('has-inspector');
    remember();
  };
  const setupForm = () => {
    const form = content.querySelector('form'), kind = form?.querySelector('[name=right_type]');
    if (!kind || kind.type === 'hidden') return;
    const update = () => {
      const ownership = kind.value === 'master_ownership';
      const share = form.querySelector('[name=share]'), scope = form.querySelector('[name=legal_scope]');
      share.disabled = !ownership; share.required = ownership;
      if (ownership && !share.value) share.value = '100';
      scope.disabled = ownership; scope.required = !ownership;
      form.querySelector('[data-bulk-field=share]').hidden = !ownership;
      form.querySelector('[data-bulk-field=legal_scope]').hidden = ownership;
      const territories = form.querySelector('[data-bulk-field=territories]');
      const world = form.querySelector('[name=territory_mode]').value === 'world';
      territories.hidden = world;
      territories.querySelectorAll('input').forEach(i => { i.disabled = world; });
    };
    form.addEventListener('change', update); update();
  };
  const load = async (url, options = {}) => {
    if (inFlight) return;
    inFlight = true; content.setAttribute('aria-busy', 'true');
    const buttonStates = [...content.querySelectorAll('button')].map(b => [b, b.disabled]);
    buttonStates.forEach(([b]) => { b.disabled = true; });
    try {
      const response = await fetch(url, {...options, headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'});
      if (response.redirected) throw new Error('Økten kan ha utløpt. Last siden på nytt.');
      if (response.headers.get('content-type')?.includes('application/json') && response.ok) {
        const result = await response.json(); dialog.close();
        await window.P7_V2.navigate(location.href);
        const status = document.querySelector('[data-matrix-result]');
        if (status) { status.hidden = false; status.textContent = `${result.applied} uverifiserte rettighetskrav registrert.`; }
        return;
      }
      if (![200, 400].includes(response.status)) throw new Error('Registreringen ble avvist. Kontroller tilgang og skriveinnstillinger.');
      content.innerHTML = await response.text(); setupForm();
      const heading = content.querySelector('h2');
      if (heading) { heading.tabIndex = -1; heading.focus(); }
    } catch (error) {
      // Keep entered values on network errors; never turn a failed apply into a new apply.
      let alert = content.querySelector('[data-fetch-error]');
      if (!alert) { alert = document.createElement('p'); alert.dataset.fetchError = ''; alert.setAttribute('role', 'alert'); content.prepend(alert); }
      alert.textContent = error.message;
      buttonStates.forEach(([b, disabled]) => { b.disabled = disabled; });
    } finally { inFlight = false; content.removeAttribute('aria-busy'); }
  };
  const open = (element, chosenRows) => {
    const ids = [...new Set(chosenRows.map(r => r.dataset.recording))];
    if (!ids.length) return;
    trigger = element;
    const url = new URL(root.dataset.bulkUrl, location.href);
    ids.forEach(id => url.searchParams.append('recordings', id));
    root.querySelector('[data-placement-count]').textContent = `${chosenRows.length} valgte sporplasser · ${ids.length} unike innspillinger. Hver innspilling behandles én gang.`;
    content.textContent = 'Laster registreringsskjema …'; dialog.showModal(); void load(url);
  };
  root.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (button?.matches('[data-inspect]')) inspect(button.closest('[data-matrix-row]'));
    else if (button?.matches('[data-close-inspector]')) {
      active = null; inspector.hidden = true; root.querySelector('[data-matrix-layout]').classList.remove('has-inspector'); rows.forEach(r => r.classList.remove('selected')); remember();
    } else if (button?.matches('[data-clear-selection]')) { rows.forEach(r => { r.querySelector('[data-select-row]').checked = false; }); selectionChanged(); }
    else if (button?.matches('[data-reset-filter]')) { search.value = ''; filter.value = 'all'; applyFilter(); }
    else if (button?.matches('[data-bulk-open]')) open(button, selected());
    else if (button?.matches('[data-register-one]')) open(button, [rows.find(r => r.dataset.recording === button.dataset.registerOne)]);
    else if (button?.matches('[data-close-bulk]') && !inFlight) dialog.close();
    else if (event.target.closest('[data-matrix-row]') && !event.target.closest('a,input,button')) inspect(event.target.closest('[data-matrix-row]'));
  });
  root.addEventListener('change', event => {
    if (event.target.matches('[data-select-visible]')) rows.filter(r => !r.hidden).forEach(r => { r.querySelector('[data-select-row]').checked = event.target.checked; });
    if (event.target.matches('[data-select-row],[data-select-visible]')) selectionChanged();
  });
  dialog.addEventListener('cancel', event => { if (inFlight) event.preventDefault(); });
  dialog.addEventListener('close', () => trigger?.focus());
  content.addEventListener('submit', event => {
    event.preventDefault();
    if (!inFlight) void load(event.target.action, {method: 'POST', body: new FormData(event.target, event.submitter)});
  });
  search.addEventListener('input', applyFilter); filter.addEventListener('change', applyFilter);
  const params = new URLSearchParams(location.search);
  search.value = params.get('q') || ''; filter.value = params.get('filter') || 'all';
  if (!filter.value) filter.value = 'all';
  const initial = rows.find(r => r.dataset.track === params.get('track'));
  if (initial) inspect(initial);
  applyFilter();
})();
