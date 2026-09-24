(() => {
  const localStorage = window.P7_V2.storage.local;
  const sessionStorage = window.P7_V2.storage.session;
  let gridController;
  const initGrid = () => {
  gridController?.abort();
  gridController = new AbortController();
  const gridSignal = gridController.signal;
  const form = document.querySelector("#track-grid-form");
  if (!form) return;
  const body = document.querySelector("#track-rows");
  const total = form.querySelector("[name='tracks-TOTAL_FORMS']");
  const template = document.querySelector("#empty-track-row");
  const dirtyCount = document.querySelector("#dirty-count");
  const pastePanel = document.querySelector(".paste-panel");
  const tableWrap = document.querySelector(".track-table-wrap");
  const draftBanner = document.querySelector("[data-draft-banner]");
  const fields = ["disc_number", "side", "track_number", "title_override", "recording_title", "artists", "composers", "lyricists", "arrangers", "duration", "isrc"];
  const storagePrefix = `p7-v2-release:${form.dataset.releaseId}`;
  const focusKey = `${storagePrefix}:focus`;
  const scrollKey = `${storagePrefix}:scroll`;
  const draftKey = `${storagePrefix}:draft`;
  const filterKey = `${storagePrefix}:control-filter`;
  const undo = [];
  let currentCell = null;
  let rangeAnchor = null;
  let searchTimer = null;
  let draftTimer = null;
  let controlFilter = sessionStorage.getItem(filterKey) || "all";
  if (controlFilter === "issues") controlFilter = "now";
  const duplicateTimers = new WeakMap();
  const duplicateRequests = new WeakMap();

  const inputOf = cell => cell?.querySelector("input:not([type=hidden]):not([type=checkbox])");
  const cells = () => [...body.querySelectorAll("[data-grid-cell]")];
  const rowCells = row => [...row.querySelectorAll("[data-grid-cell]")];
  const fieldInput = (row, field) => row?.querySelector(`[name$='-${field}']`);
  const value = (row, field) => fieldInput(row, field)?.value.trim() || "";

  const serialize = () => {
    const clone = body.cloneNode(true);
    const targets = clone.querySelectorAll("input");
    [...body.querySelectorAll("input")].forEach((source, index) => {
      const target = targets[index];
      target.setAttribute("value", source.value);
      source.checked ? target.setAttribute("checked", "checked") : target.removeAttribute("checked");
    });
    const row = currentCell?.closest("tr");
    return {
      html: clone.innerHTML,
      total: total.value,
      focus: row ? [row.sectionRowIndex, rowCells(row).indexOf(currentCell)] : null,
      scroll: tableWrap ? [tableWrap.scrollLeft, tableWrap.scrollTop] : [0, 0],
    };
  };
  const persistDraft = () => {
    if (body.querySelector(".changed-cell")) return localStorage.setItem(draftKey, JSON.stringify({state: serialize(), savedAt: new Date().toISOString()}));
    return true;
  };
  const saveDraft = () => {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(persistDraft, 120);
  };
  const dirty = () => {
    const count = body.querySelectorAll(".changed-cell").length;
    dirtyCount.textContent = count ? `${count} endring${count === 1 ? "" : "er"} ikke lagret` : "Ingen ulagrede endringer";
    saveDraft();
    return count;
  };
  const pushUndo = () => { undo.push(serialize()); if (undo.length > 40) undo.shift(); };
  const rememberFocus = cell => {
    const row = cell?.closest("tr");
    if (row) sessionStorage.setItem(focusKey, JSON.stringify([row.sectionRowIndex, rowCells(row).indexOf(cell)]));
  };
  const clearRange = () => { body.querySelectorAll(".range-cell").forEach(cell => cell.classList.remove("range-cell")); rangeAnchor = null; };

  const positionKey = row => value(row, "track_number") ? `${value(row, "disc_number") || "1"}|${value(row, "side").toUpperCase()}|${value(row, "track_number")}` : "";
  function controlReasons(row, positionCounts = null, sequenceCounts = null) {
    const now = [], later = [];
    if (fieldInput(row, "remove")?.checked) {
      if (row.dataset.hasFiles === "true") now.push("Filkobling hindrer sletting");
      return {now, later};
    }
    if (!value(row, "recording_id") && !value(row, "recording_title") && !value(row, "title_override")) now.push("Velg eller opprett innspilling");
    if (!value(row, "sequence_number") || row.querySelector(".client-invalid,[data-field='sequence_number'] .cell-error")) now.push("Ugyldig sporplassering");
    if (sequenceCounts?.get(value(row, "sequence_number")) > 1) now.push("Sorteringsrekkefølgen brukes flere ganger");
    const key = positionKey(row);
    if (key && positionCounts?.get(key) > 1) later.push("Plate/side/spornummer bør kontrolleres");
    if (row.dataset.duplicateStatus === "pending") now.push("Kontrollerer mulige innspillinger");
    if (row.dataset.duplicateStatus === "error") now.push("Kandidatkontroll mislyktes");
    if (row.dataset.duplicateStatus === "blocking") now.push("ISRC finnes allerede");
    if (row.dataset.duplicateStatus === "matches" && !value(row, "force_create")) now.push("Velg eller avklar mulig dublett");
    if (row.querySelector(".cell-error:not(.client-error)")) now.push("Valideringsfeil må rettes");
    if (!value(row, "artists")) later.push("Artist mangler");
    if (!value(row, "isrc")) later.push("ISRC mangler");
    if (!value(row, "composers") && !value(row, "lyricists")) later.push("Opphavere mangler");
    return {now: [...new Set(now)], later: [...new Set(later)]};
  }
  function updateControls() {
    const rows = [...body.rows];
    const positionCounts = new Map(), sequenceCounts = new Map();
    rows.filter(row => !fieldInput(row, "remove")?.checked).forEach(row => { const key = positionKey(row), sequence = value(row, "sequence_number"); if (key) positionCounts.set(key, (positionCounts.get(key) || 0) + 1); if (sequence) sequenceCounts.set(sequence, (sequenceCounts.get(sequence) || 0) + 1); });
    let linked = 0, needsNow = 0, needsLater = 0, missingIsrc = 0, missingCreators = 0;
    rows.forEach(row => {
      const reasons = controlReasons(row, positionCounts, sequenceCounts);
      if (value(row, "recording_id")) linked += 1;
      if (!value(row, "isrc")) missingIsrc += 1;
      if (!value(row, "composers") && !value(row, "lyricists")) missingCreators += 1;
      if (reasons.now.length) needsNow += 1;
      if (reasons.later.length) needsLater += 1;
      row.dataset.hasIssues = reasons.now.length || reasons.later.length ? "true" : "false";
      row.hidden = (controlFilter === "now" && !reasons.now.length) || (controlFilter === "later" && !reasons.later.length);
      const button = row.querySelector("[data-control-status]");
      if (button) {
        button.textContent = reasons.now.length ? `Avklar nå (${reasons.now.length})` : reasons.later.length ? `Senere (${reasons.later.length})` : "Klar";
        button.title = [...reasons.now.map(reason => `Nå: ${reason}`), ...reasons.later.map(reason => `Senere: ${reason}`)].join("\n") || "Ingen kjente kontrollbehov";
        button.className = `control-pill row-state ${reasons.now.length ? "warning" : reasons.later.length ? "deferred" : "ok"}`;
      }
    });
    document.querySelector("[data-summary-total]")?.replaceChildren(String(rows.length));
    document.querySelector("[data-summary-now]")?.replaceChildren(String(needsNow));
    document.querySelector("[data-summary-later]")?.replaceChildren(String(needsLater));
    document.querySelector("[data-summary-linked]")?.replaceChildren(String(linked));
    document.querySelector("[data-summary-isrc]")?.replaceChildren(String(missingIsrc));
    document.querySelector("[data-summary-creators]")?.replaceChildren(String(missingCreators));
    document.querySelectorAll("[data-control-filter]").forEach(button => button.classList.toggle("active", button.dataset.controlFilter === controlFilter));
  }
  const mark = cell => {
    cell.classList.add("changed-cell");
    cell.closest("tr").classList.add("dirty");
    updateControls(); dirty();
  };
  const updateInspector = row => {
    if (!row) return;
    body.querySelectorAll(".track-row.active").forEach(item => item.classList.remove("active"));
    row.classList.add("active");
    document.querySelector("#inspector-track-title")?.replaceChildren(value(row, "title_override") || value(row, "recording_title") || "Nytt spor");
    document.querySelector("#inspector-common-title")?.replaceChildren(value(row, "recording_title") || "Ikke valgt");
    document.querySelector("#inspector-position")?.replaceChildren(`${value(row, "disc_number") ? `Plate ${value(row, "disc_number")} · ` : ""}${value(row, "side")}${value(row, "track_number") || value(row, "sequence_number")}`);
    document.querySelector("#inspector-artist")?.replaceChildren(value(row, "artists") || "Uavklart");
    document.querySelector("#inspector-isrc")?.replaceChildren(value(row, "isrc") || "Ikke registrert");
    const counts = new Map(), sequences = new Map();
    [...body.rows].filter(item => !fieldInput(item, "remove")?.checked).forEach(item => { const key = positionKey(item), sequence = value(item, "sequence_number"); if (key) counts.set(key, (counts.get(key) || 0) + 1); if (sequence) sequences.set(sequence, (sequences.get(sequence) || 0) + 1); });
    const reasons = controlReasons(row, counts, sequences);
    document.querySelector("#inspector-control")?.replaceChildren([...reasons.now.map(reason => `Avklar nå: ${reason}`), ...reasons.later.map(reason => `Senere: ${reason}`)].join(" · ") || "Ingen kjente kontrollbehov");
    const recordingLink = document.querySelector("#inspector-recording-link"), recordingId = value(row, "recording_id");
    if (recordingLink) {
      recordingLink.hidden = !recordingId;
      if (recordingId) recordingLink.href = `${recordingLink.dataset.urlTemplate.replace("00000000-0000-0000-0000-000000000000", recordingId)}?return=${encodeURIComponent(window.location.pathname + window.location.search)}`;
    }
    const play = document.querySelector("#inspector-play");
    const playbackStatus = document.querySelector("#inspector-playback-status");
    if (play) {
      const savedRecording = row.dataset.playbackRecordingId || "";
      play.dataset.playUrl = savedRecording === value(row, "recording_id") ? (row.dataset.playbackUrl || "") : "";
      play.dataset.playRecordingId = row.dataset.playbackRecordingId || "";
      play.dataset.playTitle = row.dataset.playbackTitle || value(row, "recording_title") || "";
      play.dataset.playArtist = row.dataset.playbackArtist || value(row, "artists") || "";
      play.dataset.playCoverUrl = row.dataset.playCoverUrl || "";
      play.dataset.playCoverAlt = row.dataset.playCoverAlt || "";
      play.disabled = !play.dataset.playUrl;
      play.setAttribute("aria-label", play.dataset.playUrl ? `Spill ${play.dataset.playTitle}` : (row.dataset.playbackMessage || "Ingen spillbar radiofil"));
    }
    if (playbackStatus) playbackStatus.textContent = row.dataset.playbackUrl ? "" : (row.dataset.playbackMessage || "Sporet er ikke koblet til en spillbar radiofil.");
    document.dispatchEvent(new CustomEvent("p7:playback-context-changed"));
  };
  function focusCell(cell, keepRange = false) {
    if (!cell || cell.closest("tr")?.hidden) return;
    if (!keepRange) clearRange();
    cells().forEach(item => { item.tabIndex = item === cell ? 0 : -1; item.classList.toggle("active-cell", item === cell); });
    currentCell = cell;
    cell.focus({preventScroll: true});
    cell.scrollIntoView({block: "nearest", inline: "nearest"});
    rememberFocus(cell); updateInspector(cell.closest("tr"));
  }
  const restore = state => {
    body.innerHTML = state.html; total.value = state.total; currentCell = null; prepare(); updateControls(); dirty();
    const row = body.rows[state.focus?.[0] || 0] || body.rows[0];
    focusCell(rowCells(row)?.[state.focus?.[1] || 0]);
    if (tableWrap && state.scroll) { tableWrap.scrollLeft = state.scroll[0]; tableWrap.scrollTop = state.scroll[1]; }
  };
  const closeSearch = cell => { const box = cell?.querySelector(".search-results"); if (box) { box.hidden = true; box.replaceChildren(); } };
  function beginEdit(cell, typed = "") {
    const input = inputOf(cell); if (!input || input.disabled) return;
    if (!cell.classList.contains("editing")) {
      pushUndo(); cell.dataset.before = input.value; cell.dataset.wasChanged = String(cell.classList.contains("changed-cell"));
      if (cell.dataset.field === "recording_title") { cell.dataset.beforeRecording = value(cell.closest("tr"), "recording_id"); cell.dataset.beforeForce = value(cell.closest("tr"), "force_create"); }
    }
    cell.classList.add("editing"); input.tabIndex = 0; input.focus();
    if (typed) { input.value = typed; mark(cell); input.dispatchEvent(new Event("input", {bubbles: true})); } else input.select();
  }
  function finishEdit(cell, cancel = false) {
    const input = inputOf(cell); if (!input) return;
    if (cancel && cell.dataset.before !== undefined) {
      input.value = cell.dataset.before;
      if (cell.dataset.field === "recording_title") { fieldInput(cell.closest("tr"), "recording_id").value = cell.dataset.beforeRecording || ""; fieldInput(cell.closest("tr"), "force_create").value = cell.dataset.beforeForce || ""; }
      if (cell.dataset.wasChanged !== "true") cell.classList.remove("changed-cell");
    }
    cell.classList.remove("editing"); input.tabIndex = -1;
    delete cell.dataset.before; delete cell.dataset.wasChanged; delete cell.dataset.beforeRecording; delete cell.dataset.beforeForce;
    closeSearch(cell); validate(cell);
    const row = cell.closest("tr"); row.classList.toggle("dirty", Boolean(row.querySelector(".changed-cell")));
    updateControls(); dirty(); focusCell(cell);
  }
  const move = (cell, dr, dc, keepRange = false) => {
    const rows = [...body.rows].filter(row => !row.hidden), row = cell.closest("tr"), ri = rows.indexOf(row), ci = rowCells(row).indexOf(cell);
    const target = rowCells(rows[Math.max(0, Math.min(rows.length - 1, ri + dr))])?.[Math.max(0, ci + dc)];
    if (keepRange) {
      rangeAnchor ||= cell;
      focusCell(target, true);
      const allRows = [...body.rows], a = allRows.indexOf(rangeAnchor.closest("tr")), b = allRows.indexOf(target.closest("tr")), column = rowCells(target.closest("tr")).indexOf(target);
      body.querySelectorAll(".range-cell").forEach(item => item.classList.remove("range-cell"));
      for (let i = Math.min(a, b); i <= Math.max(a, b); i += 1) rowCells(allRows[i])[column]?.classList.add("range-cell");
    } else focusCell(target);
  };
  const next = (cell, delta) => { const list = cells().filter(item => !item.closest("tr").hidden), i = list.indexOf(cell); focusCell(list[Math.max(0, Math.min(list.length - 1, i + delta))]); };
  const error = (cell, message = "") => {
    cell.classList.toggle("client-invalid", Boolean(message));
    let node = cell.querySelector(".client-error");
    if (message && !node) { node = document.createElement("small"); node.className = "cell-error client-error"; cell.append(node); }
    if (node) { node.textContent = message; if (!message) node.remove(); }
    cell.closest("tr").classList.toggle("invalid", Boolean(cell.closest("tr").querySelector(".client-invalid,.cell-error:not(.client-error)")));
    return !message;
  };
  function validate(cell) {
    const input = inputOf(cell), field = cell.dataset.field, text = input?.value.trim() || "";
    if (field === "duration" && text) return error(cell, /^\d+:[0-5]\d$/.test(text) ? "" : "Bruk mm:ss, for eksempel 03:42.");
    if (field === "isrc" && text) {
      const normalized = text.toUpperCase().replace(/[\s-]/g, "");
      if (!/^[A-Z]{2}[A-Z0-9]{3}\d{7}$/.test(normalized)) return error(cell, "ISRC har ugyldig format.");
      input.value = normalized;
    }
    return error(cell);
  }
  function prepareRow(row) { rowCells(row).forEach(cell => { cell.tabIndex = -1; const input = inputOf(cell); if (input) input.tabIndex = -1; }); }
  function prepare() { [...body.rows].forEach(prepareRow); const first = cells()[0]; if (first && !currentCell) { first.tabIndex = 0; currentCell = first; } }
  function renumber(record = true) {
    if (record) pushUndo();
    [...body.rows].forEach((row, index) => { const input = fieldInput(row, "sequence_number"), wanted = String(index + 1); if (input.value !== wanted) { input.value = wanted; mark(input.closest("[data-grid-cell]")); } });
    updateControls();
  }
  function addRow(values = [], record = true, before = null) {
    if (record) pushUndo();
    if (controlFilter !== "all") { controlFilter = "all"; sessionStorage.setItem(filterKey, "all"); }
    const index = Number(total.value), fragment = template.content.cloneNode(true), row = fragment.querySelector("tr");
    row.innerHTML = row.innerHTML.replaceAll("__prefix__", index); row.dataset.index = index;
    fieldInput(row, "sequence_number").value = body.rows.length + 1;
    fields.forEach((name, offset) => { const input = fieldInput(row, name); if (input) input.value = values[offset] || ""; });
    before ? body.insertBefore(row, before) : body.append(row); total.value = index + 1; prepareRow(row); rowCells(row).forEach(mark); renumber(false); return row;
  }
  const duplicateCurrent = () => {
    const source = currentCell?.closest("tr"); if (!source) return;
    pushUndo(); const values = fields.map(name => value(source, name));
    const row = addRow(values, false, source.nextElementSibling);
    fieldInput(row, "recording_id").value = value(source, "recording_id");
    fieldInput(row, "force_create").value = value(source, "force_create");
    renumber(false); focusCell(rowCells(row)[0]);
  };
  const moveCurrentRow = direction => {
    const row = currentCell?.closest("tr"), sibling = direction === "up" ? row?.previousElementSibling : row?.nextElementSibling;
    if (!row || !sibling) return; pushUndo();
    direction === "up" ? body.insertBefore(row, sibling) : body.insertBefore(sibling, row);
    renumber(false); focusCell(rowCells(row)[0]);
  };
  const fillDown = () => {
    let selected = [...body.querySelectorAll(".range-cell")];
    if (selected.length < 2 && currentCell) {
      const rows = [...body.rows], index = rows.indexOf(currentCell.closest("tr")), column = rowCells(currentCell.closest("tr")).indexOf(currentCell);
      if (index > 0) selected = [rowCells(rows[index - 1])[column], currentCell];
    }
    if (selected.length < 2) return;
    pushUndo(); const source = inputOf(selected[0])?.value || "";
    selected.slice(1).forEach(cell => { const input = inputOf(cell); if (input) { input.value = source; mark(cell); validate(cell); } });
    updateControls();
  };
  const applyMatrix = (matrix, start = currentCell, record = true) => {
    if (!start || !matrix.length) return; if (record) pushUndo();
    const rows = [...body.rows], startRow = rows.indexOf(start.closest("tr")), startCol = rowCells(start.closest("tr")).indexOf(start);
    matrix.forEach((values, r) => {
      while (startRow + r >= body.rows.length) addRow([], false);
      const targetRow = body.rows[startRow + r], targetCells = rowCells(targetRow);
      values.forEach((text, c) => { const cell = targetCells[startCol + c], input = inputOf(cell); if (input) { input.value = text; mark(cell); validate(cell); } });
    });
    focusCell(rowCells(body.rows[startRow + matrix.length - 1])[Math.min(startCol + matrix.at(-1).length - 1, rowCells(body.rows[startRow]).length - 1)]);
  };
  const chooseRecording = (cell, item) => {
    const row = cell.closest("tr"), input = inputOf(cell), id = fieldInput(row, "recording_id"), force = fieldInput(row, "force_create");
    input.value = item?.title || input.value; id.value = item?.id || ""; force.value = item ? "" : "on";
    const trackTitle = fieldInput(row, "title_override");
    if (item && trackTitle.value.trim().toLocaleLowerCase() === item.title.trim().toLocaleLowerCase()) {
      trackTitle.value = "";
      mark(trackTitle.closest("[data-grid-cell]"));
    }
    duplicateRequests.set(row, (duplicateRequests.get(row) || 0) + 1);
    clearTimeout(duplicateTimers.get(row));
    row.dataset.duplicateStatus = "";
    row.querySelector("[data-candidate-panel]")?.setAttribute("hidden", "");
    row.querySelector("[data-server-candidates]")?.setAttribute("hidden", "");
    cell.querySelectorAll(".cell-error").forEach(node => node.remove());
    mark(cell); closeSearch(cell); finishEdit(cell); next(cell, 1);
  };
  function checkCandidates(row) {
    const panel = row.querySelector("[data-candidate-panel]");
    if (!panel) return;
    const title = value(row, "recording_title") || (!value(row, "recording_id") ? value(row, "title_override") : "");
    const request = (duplicateRequests.get(row) || 0) + 1;
    duplicateRequests.set(row, request);
    panel.replaceChildren();
    if (value(row, "recording_id") || !title) {
      panel.hidden = true;
      row.dataset.duplicateStatus = "";
      updateControls();
      return;
    }
    row.dataset.duplicateStatus = "pending";
    panel.hidden = false;
    panel.textContent = "Kontrollerer eksisterende innspillinger …";
    updateControls();
    const params = new URLSearchParams({match: "exact", q: title, isrc: value(row, "isrc"), duration: value(row, "duration")});
    fetch(`${window.P7_V2.recordingSearch}?${params}`).then(response => {
      if (!response.ok) throw new Error("lookup failed");
      return response.json();
    }).then(data => {
      if (duplicateRequests.get(row) !== request) return;
      const matches = data.results || [];
      panel.replaceChildren();
      row.querySelector("[data-server-candidates]")?.setAttribute("hidden", "");
      row.querySelectorAll("[data-field='recording_title'] .cell-error").forEach(node => {
        if (node.textContent.includes("Mulig dublett")) node.remove();
      });
      row.classList.toggle("invalid", Boolean(row.querySelector(".client-invalid,.cell-error")));
      if (!matches.length) {
        panel.hidden = true;
        row.dataset.duplicateStatus = "clear";
        updateControls();
        return;
      }
      const blocking = matches.some(item => item.blocking);
      row.dataset.duplicateStatus = blocking ? "blocking" : "matches";
      const heading = document.createElement("strong");
      heading.textContent = blocking ? "Samme ISRC finnes allerede – velg innspillingen eller avklar ISRC." : "Mulige eksisterende innspillinger – kontroller nå:";
      panel.append(heading);
      matches.forEach(item => {
        const entry = document.createElement("div");
        const label = document.createElement("span");
        label.textContent = `${item.title}${item.artist ? ` · ${item.artist}` : ""}${item.signals?.length ? ` · ${item.signals.join(", ")}` : ""}`;
        const use = document.createElement("button");
        use.type = "button";
        use.dataset.useRecording = item.id;
        use.dataset.title = item.title;
        use.textContent = "Bruk denne innspillingen";
        const open = document.createElement("a");
        open.href = item.url;
        open.target = "_blank";
        open.rel = "noopener";
        open.textContent = "Åpne og kontroller ↗";
        entry.append(label, use, open);
        panel.append(entry);
      });
      if (!blocking) {
        const create = document.createElement("button");
        create.type = "button";
        create.dataset.createRecording = "true";
        create.textContent = "Jeg har kontrollert – opprett separat innspilling";
        panel.append(create);
      }
      updateControls();
    }).catch(() => {
      if (duplicateRequests.get(row) !== request) return;
      row.dataset.duplicateStatus = "error";
      panel.textContent = "Kandidatkontrollen feilet. Prøv igjen før lagring.";
      updateControls();
    });
  }
  function scheduleCandidates(row) {
    clearTimeout(duplicateTimers.get(row));
    duplicateRequests.set(row, (duplicateRequests.get(row) || 0) + 1);
    if (!row.querySelector("[data-candidate-panel]")) return;
    if (value(row, "recording_id")) {
      checkCandidates(row);
      return;
    }
    row.dataset.duplicateStatus = "pending";
    const panel = row.querySelector("[data-candidate-panel]");
    panel.hidden = false;
    panel.textContent = "Kontrollerer eksisterende innspillinger …";
    updateControls();
    duplicateTimers.set(row, setTimeout(() => checkCandidates(row), 220));
  }
  async function search(cell) {
    const q = inputOf(cell).value.trim(), box = cell.querySelector(".search-results"); if (q.length < 2) return closeSearch(cell);
    try {
      const response = await fetch(`${window.P7_V2.recordingSearch}?q=${encodeURIComponent(q)}`), data = await response.json();
      box.replaceChildren(...data.results.map(item => { const button = document.createElement("button"); button.type = "button"; button.role = "option"; button.dataset.result = JSON.stringify(item); const title = document.createElement("strong"), detail = document.createElement("span"); title.textContent = item.title; detail.textContent = `${item.artist}${item.isrc ? ` · ${item.isrc}` : ""}`; button.append(title, detail); return button; }));
      box.hidden = !data.results?.length; box.dataset.active = "-1";
    } catch { closeSearch(cell); }
  }
  const moveResult = (box, delta) => {
    const options = [...box.querySelectorAll("button")]; let index = Number(box.dataset.active || -1) + delta;
    index = Math.max(0, Math.min(options.length - 1, index)); box.dataset.active = index;
    options.forEach((option, i) => option.classList.toggle("active", i === index)); options[index]?.scrollIntoView({block: "nearest"});
  };

  body.addEventListener("click", event => {
    const result = event.target.closest(".search-results button"); if (result) { const cell = result.closest("[data-grid-cell]"); chooseRecording(cell, result.dataset.create ? null : JSON.parse(result.dataset.result)); return; }
    const candidate = event.target.closest("[data-use-recording],[data-create-recording]"); if (candidate) { const cell = candidate.closest("[data-grid-cell]"); chooseRecording(cell, candidate.dataset.useRecording ? {id: candidate.dataset.useRecording, title: candidate.dataset.title} : null); return; }
    const moveTitle = event.target.closest("[data-move-track-title]"); if (moveTitle) {
      const row = moveTitle.closest("tr"), source = fieldInput(row, "title_override"), target = fieldInput(row, "recording_title");
      if (!source.value.trim()) { focusCell(source.closest("[data-grid-cell]")); return; }
      pushUndo(); target.value = source.value.trim(); source.value = "";
      fieldInput(row, "recording_id").value = ""; fieldInput(row, "force_create").value = "";
      mark(source.closest("[data-grid-cell]")); mark(target.closest("[data-grid-cell]"));
      scheduleCandidates(row); focusCell(target.closest("[data-grid-cell]")); return;
    }
    if (event.target.closest("[data-candidate-panel] a,[data-server-candidates] a")) persistDraft();
    const control = event.target.closest("[data-control-status]"); if (control) { updateInspector(control.closest("tr")); return; }
    const cell = event.target.closest("[data-grid-cell]"); if (cell && event.target === cell) focusCell(cell);
  });
  body.addEventListener("dblclick", event => { const cell = event.target.closest("[data-grid-cell]"); if (cell) beginEdit(cell); });
  body.addEventListener("input", event => {
    const cell = event.target.closest("[data-grid-cell]"); if (!cell) return; mark(cell); validate(cell);
    cell.querySelectorAll(".cell-error:not(.client-error)").forEach(node => node.remove());
    if (cell.dataset.field === "recording_title") {
      fieldInput(cell.closest("tr"), "recording_id").value = "";
      fieldInput(cell.closest("tr"), "force_create").value = "";
      cell.closest("tr").querySelector("[data-server-candidates]")?.setAttribute("hidden", "");
      clearTimeout(searchTimer); searchTimer = setTimeout(() => search(cell), 180);
    }
    if (cell.dataset.field === "title_override" && !value(cell.closest("tr"), "recording_id") && !value(cell.closest("tr"), "recording_title")) fieldInput(cell.closest("tr"), "force_create").value = "";
    if (["recording_title", "title_override", "isrc", "duration"].includes(cell.dataset.field)) scheduleCandidates(cell.closest("tr"));
    updateControls(); updateInspector(cell.closest("tr"));
  });
  body.addEventListener("change", event => { const row = event.target.closest("tr"); if (row) { row.classList.add("dirty"); const cell = event.target.closest("[data-grid-cell]") || rowCells(row)[0]; mark(cell); updateControls(); } });
  body.addEventListener("paste", event => {
    const cell = event.target.closest("[data-grid-cell]") || currentCell, text = event.clipboardData?.getData("text/plain");
    if (!cell || !text || (!text.includes("\t") && !/[\r\n]/.test(text))) return;
    event.preventDefault(); applyMatrix(text.replace(/\r/g, "").split("\n").filter((line, i, all) => line || i < all.length - 1).map(line => line.split("\t")), cell);
  });
  body.addEventListener("keydown", event => {
    const cell = event.target.closest("[data-grid-cell]") || currentCell; if (!cell) return;
    const editing = cell.classList.contains("editing"), box = cell.querySelector(".search-results"), boxOpen = box && !box.hidden;
    if (event.ctrlKey && event.key.toLowerCase() === "c" && !editing) { event.preventDefault(); navigator.clipboard.writeText(inputOf(cell)?.value || ""); return; }
    if (event.ctrlKey && event.key.toLowerCase() === "z") { event.preventDefault(); if (undo.length) restore(undo.pop()); return; }
    if (event.ctrlKey && event.key.toLowerCase() === "d" && !editing) { event.preventDefault(); fillDown(); return; }
    if (event.ctrlKey && event.key === "Enter") { event.preventDefault(); form.requestSubmit(); return; }
    if (editing && boxOpen && ["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); moveResult(box, event.key === "ArrowDown" ? 1 : -1); return; }
    if (editing && boxOpen && event.key === "Enter") { const option = [...box.querySelectorAll("button")][Number(box.dataset.active)]; if (option) { event.preventDefault(); chooseRecording(cell, option.dataset.create ? null : JSON.parse(option.dataset.result)); return; } }
    if (editing && event.key === "Escape") { event.preventDefault(); finishEdit(cell, true); return; }
    if (editing && event.key === "Enter") { event.preventDefault(); finishEdit(cell); return; }
    if (event.key === "Tab") { event.preventDefault(); if (editing) finishEdit(cell); next(cell, event.shiftKey ? -1 : 1); return; }
    if (!editing && event.key === "Enter") { event.preventDefault(); beginEdit(cell); return; }
    if (!editing && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) { event.preventDefault(); move(cell, event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0, event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : 0, event.shiftKey && ["ArrowUp", "ArrowDown"].includes(event.key)); return; }
    if (!editing && event.key.length === 1 && !event.ctrlKey && !event.metaKey) { event.preventDefault(); beginEdit(cell, event.key); }
  });

  document.querySelector("[data-add-row]")?.addEventListener("click", () => focusCell(rowCells(addRow())[0]));
  document.querySelector("[data-insert-row]")?.addEventListener("click", () => focusCell(rowCells(addRow([], true, currentCell?.closest("tr")))[0]));
  document.querySelector("[data-duplicate-row]")?.addEventListener("click", duplicateCurrent);
  document.querySelectorAll("[data-move-row]").forEach(button => button.addEventListener("click", () => moveCurrentRow(button.dataset.moveRow)));
  document.querySelector("[data-renumber]")?.addEventListener("click", () => renumber());
  document.querySelector("[data-fill-down]")?.addEventListener("click", fillDown);
  document.querySelector("[data-add-five]")?.addEventListener("click", () => { pushUndo(); let first; for (let i = 0; i < 5; i += 1) first ||= addRow([], false); focusCell(rowCells(first)[0]); });
  document.querySelector("[data-open-paste]")?.addEventListener("click", () => { pastePanel.hidden = false; pastePanel.querySelector("textarea").focus(); });
  document.querySelector("[data-close-paste]")?.addEventListener("click", () => { pastePanel.hidden = true; focusCell(currentCell); });
  document.querySelector("[data-apply-paste]")?.addEventListener("click", () => { const area = pastePanel.querySelector("textarea"), matrix = area.value.replace(/\r/g, "").split("\n").filter(Boolean).map(line => line.split("\t")); if (matrix.length) { pushUndo(); const row = addRow([], false); focusCell(rowCells(row)[1]); applyMatrix(matrix, currentCell, false); } area.value = ""; pastePanel.hidden = true; });
  document.querySelector("[data-cancel-grid]")?.addEventListener("click", () => { if (!dirty() || confirm("Forkast ulagrede endringer og det lokale utkastet?")) { localStorage.removeItem(draftKey); location.reload(); } });
  document.querySelectorAll("[data-control-filter]").forEach(button => button.addEventListener("click", () => { controlFilter = button.dataset.controlFilter; sessionStorage.setItem(filterKey, controlFilter); updateControls(); const first = [...body.rows].find(row => !row.hidden); if (first) focusCell(rowCells(first)[0]); }));
  document.querySelector("[data-restore-draft]")?.addEventListener("click", () => { try { const draft = JSON.parse(localStorage.getItem(draftKey)); if (draft?.state) restore(draft.state); } catch { localStorage.removeItem(draftKey); } draftBanner.hidden = true; });
  document.querySelector("[data-discard-draft]")?.addEventListener("click", () => { localStorage.removeItem(draftKey); draftBanner.hidden = true; });
  tableWrap?.addEventListener("scroll", () => sessionStorage.setItem(scrollKey, JSON.stringify([tableWrap.scrollLeft, tableWrap.scrollTop])), {passive: true});
  document.querySelectorAll(".release-layout a[href]").forEach(link => link.addEventListener("click", () => {
    form.dataset.internalNavigation = !dirty() || persistDraft() ? "true" : "";
  }));
  addEventListener("beforeunload", event => { if (dirty() && !form.dataset.submitting && !form.dataset.internalNavigation) { event.preventDefault(); event.returnValue = ""; } }, {signal: gridSignal});
  document.addEventListener("p7:before-navigation", event => {
    if (dirty() && !persistDraft() && !confirm("Nettleseren kan ikke lagre utkastet. Forlate siden og forkaste ulagrede endringer?")) event.preventDefault();
  }, {signal: gridSignal});
  document.addEventListener("keydown", event => { if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "n") { event.preventDefault(); focusCell(rowCells(addRow())[0]); } }, {signal: gridSignal});
  form.addEventListener("submit", event => {
    if (form.dataset.submitting) { event.preventDefault(); return; }
    const invalid = cells().filter(cell => !validate(cell)); if (invalid.length) { event.preventDefault(); controlFilter = "all"; sessionStorage.setItem(filterKey, "all"); updateControls(); focusCell(invalid[0]); return; }
    const unresolved = [...body.rows].find(row => ["pending", "error", "blocking"].includes(row.dataset.duplicateStatus) || (row.dataset.duplicateStatus === "matches" && !value(row, "force_create")));
    if (unresolved) { event.preventDefault(); controlFilter = "all"; sessionStorage.setItem(filterKey, "all"); updateControls(); focusCell(unresolved.querySelector("[data-field='recording_title']")); return; }
    form.dataset.submitting = "true"; rememberFocus(currentCell); form.querySelector("button[type=submit]").disabled = true;
  });

  prepare(); updateControls();
  [...body.rows].forEach(row => { if (!value(row, "recording_id") && (value(row, "recording_title") || value(row, "title_override"))) scheduleCandidates(row); });
  if (form.dataset.gridSaved) {
    localStorage.removeItem(draftKey);
    const url = new URL(location.href); url.searchParams.delete("grid_saved"); history.replaceState({}, "", url);
    form.querySelector("[name='return_query']").value = url.searchParams.toString();
  } else if (localStorage.getItem(draftKey)) draftBanner.hidden = false;
  try { const [r, c] = JSON.parse(sessionStorage.getItem(focusKey) || "[0,0]"); focusCell(rowCells(body.rows[r] || body.rows[0])?.[c]); } catch { focusCell(cells()[0]); }
  try { const [left, top] = JSON.parse(sessionStorage.getItem(scrollKey) || "[0,0]"); if (tableWrap) { tableWrap.scrollLeft = left; tableWrap.scrollTop = top; } } catch {}
  };
  window.P7_V2 ||= {};
  window.P7_V2.initGrid = initGrid;
  initGrid();
})();
