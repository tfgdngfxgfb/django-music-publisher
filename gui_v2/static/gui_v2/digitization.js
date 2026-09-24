(() => {
  const workspace = document.querySelector('[data-digitization]');
  if (!workspace || workspace.dataset.digitizationInitialized) return;
  workspace.dataset.digitizationInitialized = 'true';
  window.P7_V2 ||= {};
  window.P7_V2.digitizationController?.abort();
  const controller = new AbortController();
  window.P7_V2.digitizationController = controller;
  const workspacePath = location.pathname;
  const steps = ['metadata', 'raw', 'masters', 'links', 'radio', 'history'];
  let activeStep = steps.includes(workspace.dataset.step) ? workspace.dataset.step : 'metadata';
  const showStep = (step, updateUrl = true) => {
    if (!steps.includes(step)) return;
    activeStep = step;
    workspace.querySelectorAll('[data-digitization-step]').forEach(section => {section.hidden = section.dataset.digitizationStep !== step;});
    workspace.querySelectorAll('[data-step-link]').forEach(link => {
      if (link.dataset.stepLink === step) link.setAttribute('aria-current', 'step');
      else link.removeAttribute('aria-current');
    });
    const inspector = workspace.querySelector('.recording-files-inspector');
    if (inspector) inspector.hidden = !['raw', 'masters', 'links'].includes(step);
    if (step === 'raw' && rawRows.length) selectRaw(rawRows.find(row => row.getAttribute('aria-selected') === 'true') || rawRows[0]);
    if (step === 'masters' && rows.length) select(rows.find(row => row.getAttribute('aria-selected') === 'true') || rows[0]);
    if (step === 'links' && rows.length) select(rows.find(row => row.getAttribute('aria-selected') === 'true') || rows[0]);
    workspace.querySelector('[data-step-previous]').disabled = steps.indexOf(step) === 0;
    workspace.querySelector('[data-step-next]').disabled = ['radio', 'history'].includes(step);
    workspace.querySelectorAll('form[method="post"]').forEach(form => {
      let input = form.querySelector('[name=step]');
      if (!input) {input = document.createElement('input'); input.type = 'hidden'; input.name = 'step'; form.append(input);}
      input.value = step;
    });
    if (updateUrl) {
      const url = new URL(location.href); url.searchParams.set('step', step); url.hash = '';
      history.pushState(null, '', url);
    }
    workspace.querySelectorAll('[data-management-register]').forEach(link => {
      const url = new URL(link.href);
      url.searchParams.set('return', `${location.pathname}?step=${step}`);
      link.href = url.toString();
    });
    document.dispatchEvent(new Event('digitization:step'));
  };
  workspace.querySelectorAll('[data-step-link]').forEach(link => link.addEventListener('click', event => {event.preventDefault(); showStep(link.dataset.stepLink);}));
  workspace.querySelector('[data-step-next]').addEventListener('click', () => showStep(steps[steps.indexOf(activeStep) + 1]));
  workspace.querySelector('[data-step-previous]').addEventListener('click', () => showStep(steps[steps.indexOf(activeStep) - 1]));
  window.addEventListener('popstate', () => {
    if (workspace.isConnected && location.pathname === workspacePath) showStep(new URL(location.href).searchParams.get('step') || 'metadata', false);
  }, {signal: controller.signal});
  document.addEventListener('p7:page-changed', () => {
    if (!workspace.isConnected) controller.abort();
  }, {signal: controller.signal});
  const historySearch = workspace.querySelector('[data-history-search]');
  if (historySearch) {
    const historyRows = [...workspace.querySelectorAll('[data-history-row]')];
    const historyCount = workspace.querySelector('[data-history-count]');
    const historyEmpty = workspace.querySelector('[data-history-empty]');
    const filterHistory = () => {
      const query = historySearch.value.trim().toLocaleLowerCase();
      let visible = 0;
      historyRows.forEach(row => {
        row.hidden = !!query && !row.textContent.toLocaleLowerCase().includes(query);
        if (!row.hidden) visible += 1;
      });
      historyCount.textContent = `${visible} av ${historyRows.length} vist`;
      historyEmpty.hidden = visible !== 0;
    };
    historySearch.addEventListener('input', filterHistory);
    filterHistory();
  }
  const draftKey = `digitization:draft:${location.pathname}`;
  const storage = {
    get: key => {try {return sessionStorage.getItem(key);} catch {return null;}},
    set: (key, value) => {try {sessionStorage.setItem(key, value);} catch {}},
    remove: key => {try {sessionStorage.removeItem(key);} catch {}},
  };
  const bulkForm = workspace.querySelector('[data-bulk-form]');
  if (workspace.dataset.clearDraft === 'true') {
    storage.remove(draftKey);
    const cleanUrl = new URL(location.href);
    cleanUrl.searchParams.delete('applied');
    history.replaceState(null, '', cleanUrl);
  }
  const saveDraft = () => {
    if (!bulkForm) return;
    const fields = [...bulkForm.querySelectorAll('input[name],select[name],textarea[name]')]
      .filter(field => !['csrfmiddlewaretoken', 'step'].includes(field.name))
      .map(field => ({name: field.name, type: field.type, value: field.value, checked: field.checked}));
    storage.set(draftKey, JSON.stringify(fields));
  };
  if (bulkForm) {
    try {
      const fields = JSON.parse(storage.get(draftKey) || '[]');
      const controls = [...bulkForm.querySelectorAll('input[name],select[name],textarea[name]')];
      fields.forEach(saved => {
        if (saved.name === 'step') return;
        const target = controls.find(field => field.name === saved.name && (!['checkbox','radio'].includes(saved.type) || field.value === saved.value));
        if (!target) return;
        if (['checkbox','radio'].includes(saved.type)) target.checked = Boolean(saved.checked);
        else target.value = saved.value;
      });
    } catch {
      storage.remove(draftKey);
    }
    bulkForm.addEventListener('input', saveDraft);
    bulkForm.addEventListener('change', saveDraft);
    bulkForm.addEventListener('submit', saveDraft);
  }
  const rows = [...workspace.querySelectorAll('[data-digitization-row]')];
  const rawRows = [...workspace.querySelectorAll('[data-raw-row]')];
  const masterListRows = [...workspace.querySelectorAll('[data-master-list-row]')];
  const select = row => {
    rows.forEach(item => {const active = item === row; item.classList.toggle('selected', active); item.setAttribute('aria-selected', String(active)); item.tabIndex = active ? 0 : -1;});
    masterListRows.forEach(item => item.classList.toggle('selected', item.dataset.masterListRow === row.dataset.digitizationRow));
    rawRows.forEach(item => {item.classList.remove('selected'); item.setAttribute('aria-selected', 'false'); item.tabIndex = -1;});
    workspace.querySelectorAll('[data-raw-detail]').forEach(panel => {panel.hidden = true;});
    workspace.querySelector('.digitization-empty-detail').hidden = true;
    workspace.querySelectorAll('[data-digitization-detail]').forEach(panel => {panel.hidden = panel.dataset.digitizationDetail !== row.dataset.digitizationRow;});
    storage.set(`digitization:${location.pathname}`, row.dataset.digitizationRow);
  };
  const selectRaw = row => {
    rows.forEach(item => {item.classList.remove('selected'); item.setAttribute('aria-selected', 'false'); item.tabIndex = -1;});
    rawRows.forEach(item => {const active = item === row; item.classList.toggle('selected', active); item.setAttribute('aria-selected', String(active)); item.tabIndex = active ? 0 : -1;});
    workspace.querySelector('.digitization-empty-detail').hidden = true;
    workspace.querySelectorAll('[data-digitization-detail]').forEach(panel => {panel.hidden = true;});
    workspace.querySelectorAll('[data-raw-detail]').forEach(panel => {panel.hidden = panel.dataset.rawDetail !== row.dataset.rawRow;});
  };
  rawRows.forEach(row => {
    row.addEventListener('click', () => selectRaw(row));
    row.addEventListener('keydown', event => {
      if (event.target.closest('input,select,textarea,button,a,summary')) return;
      if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); selectRaw(row); return;}
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const index = rawRows.indexOf(row);
      const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? rawRows.length - 1 : Math.max(0, Math.min(rawRows.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)));
      const next = rawRows[nextIndex]; selectRaw(next); next.focus({preventScroll: true}); next.scrollIntoView({block: 'nearest'});
    });
  });
  rows.forEach(row => row.addEventListener('click', () => select(row)));
  masterListRows.forEach(item => {
    const selectMaster = () => {
      const row = rows.find(candidate => candidate.dataset.digitizationRow === item.dataset.masterListRow);
      if (row) select(row);
    };
    item.addEventListener('click', selectMaster);
    item.addEventListener('keydown', event => {
      if (event.target.closest('input,select,textarea,button,a,summary')) return;
      if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); selectMaster();}
    });
  });
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
    const selectAll = workspace.querySelector('[data-select-all]');
    if (selectAll) {selectAll.checked = rows.length > 0 && selected.length === rows.length; selectAll.indeterminate = selected.length > 0 && selected.length < rows.length;}
    workspace.querySelectorAll('[data-bulk-action]').forEach(button => {
      button.disabled = !selected.length || (button.value === 'select_master' && selected.some(row => row.dataset.canSelect !== 'true'));
    });
  };
  workspace.addEventListener('change', updateSelection);
  workspace.querySelector('[data-select-all]')?.addEventListener('change', event => {rows.forEach(row => {row.querySelector('[name=assets]').checked = event.target.checked;}); updateSelection();});
  workspace.querySelector('[data-accept-suggestions]')?.addEventListener('click', () => {
    rows.forEach(row => {
      const track = row.dataset.suggestedTrack, source = row.dataset.suggestedSource;
      row.querySelector('[name=assets]').checked = Boolean(track && source);
      if (track && source) {
        row.querySelector('[name^=track_]').value = track;
        row.querySelector('[name^=source_]').value = source;
      }
    });
    updateSelection(); saveDraft();
  });
  workspace.querySelectorAll('[data-choose-raw]').forEach(button => button.addEventListener('click', () => {
    const row = button.closest('[data-digitization-row]');
    rows.forEach(item => {item.querySelector('[name=assets]').checked = item === row;});
    select(row);
    updateSelection();
    saveDraft();
    const source = workspace.querySelector('.digitization-toolbar select[name=source]');
    source?.scrollIntoView({block: 'center'});
    source?.focus();
  }));
  workspace.querySelectorAll('[data-select-master]').forEach(link => link.addEventListener('click', event => {
    const row = rows.find(item => item.dataset.digitizationRow === link.dataset.selectMaster);
    if (!row) return;
    event.preventDefault();
    showStep('links');
    select(row);
    row.focus({preventScroll: true});
    row.scrollIntoView({block: 'nearest'});
  }));
  const saved = storage.get(`digitization:${location.pathname}`);
  const initial = rows.find(row => row.dataset.digitizationRow === saved) || rows[0];
  if (initial) select(initial);
  else if (rawRows.length) selectRaw(rawRows[0]);
  updateSelection();
  workspace.querySelectorAll('[data-recording-picker]').forEach(picker => {
    const row = picker.closest('[data-digitization-row]');
    const track = row.querySelector('[name^=track_]');
    const title = row.querySelector('[name^=new_title_]');
    title.addEventListener('input', () => {
      if (title.value.trim()) {track.value = ''; picker.querySelector('[data-recording-id]').value = ''; picker.querySelector('[data-recording-choice]').textContent = '';}
      saveDraft();
    });
    track.addEventListener('change', () => {
      if (track.value) {title.value = ''; picker.querySelector('[data-recording-id]').value = ''; picker.querySelector('[data-recording-choice]').textContent = '';}
      saveDraft();
    });
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
            button.addEventListener('click', () => {track.value = ''; title.value = ''; picker.querySelector('[data-recording-id]').value = recording.id; picker.querySelector('[data-recording-choice]').textContent = button.textContent; results.replaceChildren(); saveDraft();}); results.append(button);
          }
        } catch {if (request === serial) results.textContent = 'Søket kunne ikke fullføres.';}
      }, 180);
    });
  });
  showStep(activeStep, false);
})();
