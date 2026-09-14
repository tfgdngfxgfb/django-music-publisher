(() => {
  const form = document.querySelector("#track-grid-form");
  if (!form) return;
  const body = document.querySelector("#track-rows");
  const total = form.querySelector("[name='tracks-TOTAL_FORMS']");
  const template = document.querySelector("#empty-track-row");
  const dirtyCount = document.querySelector("#dirty-count");
  const pastePanel = document.querySelector(".paste-panel");
  const fields = ["disc_number", "side", "track_number", "title_override", "recording_title", "artists", "composers", "lyricists", "arrangers", "duration", "isrc"];
  const focusKey = `p7-v2-grid-focus:${location.pathname}`;
  const undo = [];
  let currentCell = null;
  let searchTimer = null;

  const inputOf = cell => cell?.querySelector("input:not([type=hidden]):not([type=checkbox])");
  const cells = () => [...body.querySelectorAll("[data-grid-cell]")];
  const rowCells = row => [...row.querySelectorAll("[data-grid-cell]")];
  const fieldInput = (row, field) => row.querySelector(`[name$='-${field}']`);
  const setRowState = (row, text, kind="muted") => {
    const state = row.querySelector(".row-state");
    if (state) { state.textContent = text; state.className = `status ${kind} row-state`; }
  };
  const dirty = () => {
    const count = body.querySelectorAll(".changed-cell").length;
    dirtyCount.textContent = count ? `${count} endring${count === 1 ? "" : "er"} ikke lagret` : "Ingen ulagrede endringer";
    return count;
  };
  const mark = cell => {
    cell.classList.add("changed-cell");
    const row = cell.closest("tr"); row.classList.add("dirty"); setRowState(row, "Ulagret", "warning"); dirty();
  };
  const serialize = () => {
    const clone = body.cloneNode(true);
    [...body.querySelectorAll("input")].forEach((source, index) => {
      const target = clone.querySelectorAll("input")[index];
      target.setAttribute("value", source.value);
      source.checked ? target.setAttribute("checked", "checked") : target.removeAttribute("checked");
    });
    return {html: clone.innerHTML, total: total.value, focus: currentCell ? [currentCell.closest("tr").rowIndex, rowCells(currentCell.closest("tr")).indexOf(currentCell)] : null};
  };
  const pushUndo = () => { undo.push(serialize()); if (undo.length > 40) undo.shift(); };
  const restore = state => {
    body.innerHTML = state.html; total.value = state.total; prepare(); dirty();
    const rows = [...body.rows], row = state.focus ? rows[state.focus[0] - 1] : rows[0];
    focusCell(rowCells(row)[state.focus?.[1] || 0]);
  };
  const rememberFocus = cell => {
    const row = cell?.closest("tr"); if (!row) return;
    sessionStorage.setItem(focusKey, JSON.stringify([row.sectionRowIndex, rowCells(row).indexOf(cell)]));
  };
  const updateInspector = row => {
    if (!row) return;
    const value = name => fieldInput(row, name)?.value || "";
    body.querySelectorAll(".track-row.active").forEach(item => item.classList.remove("active")); row.classList.add("active");
    document.querySelector("#inspector-track-title")?.replaceChildren(value("title_override") || value("recording_title") || "Nytt spor");
    document.querySelector("#inspector-common-title")?.replaceChildren(value("recording_title") || "Ikke valgt");
    document.querySelector("#inspector-position")?.replaceChildren(`${value("disc_number") ? `Plate ${value("disc_number")} · ` : ""}${value("side")}${value("track_number") || value("sequence_number")}`);
    document.querySelector("#inspector-artist")?.replaceChildren(value("artists") || "Uavklart");
    document.querySelector("#inspector-isrc")?.replaceChildren(value("isrc") || "Ikke registrert");
  };
  function focusCell(cell) {
    if (!cell) return;
    cells().forEach(item => { item.tabIndex = item === cell ? 0 : -1; item.classList.toggle("active-cell", item === cell); });
    currentCell = cell; cell.focus({preventScroll:true}); cell.scrollIntoView({block:"nearest", inline:"nearest"});
    rememberFocus(cell); updateInspector(cell.closest("tr"));
  }
  const closeSearch = cell => { const box = cell?.querySelector(".search-results"); if (box) { box.hidden = true; box.replaceChildren(); } };
  function beginEdit(cell, typed="") {
    const input = inputOf(cell); if (!input || input.disabled) return;
    if (!cell.classList.contains("editing")) {
      pushUndo(); cell.dataset.before = input.value; cell.dataset.wasChanged = String(cell.classList.contains("changed-cell"));
      if (cell.dataset.field === "recording_title") {
        cell.dataset.beforeRecording = fieldInput(cell.closest("tr"), "recording_id").value;
        cell.dataset.beforeForce = fieldInput(cell.closest("tr"), "force_create").value;
      }
    }
    cell.classList.add("editing"); input.tabIndex = 0; input.focus();
    if (typed) { input.value = typed; mark(cell); input.dispatchEvent(new Event("input", {bubbles:true})); }
    else input.select();
  }
  function finishEdit(cell, cancel=false) {
    const input = inputOf(cell); if (!input) return;
    if (cancel && cell.dataset.before !== undefined) {
      input.value = cell.dataset.before;
      if (cell.dataset.field === "recording_title") {
        fieldInput(cell.closest("tr"), "recording_id").value = cell.dataset.beforeRecording || "";
        fieldInput(cell.closest("tr"), "force_create").value = cell.dataset.beforeForce || "";
      }
      if (cell.dataset.wasChanged !== "true") cell.classList.remove("changed-cell");
    }
    cell.classList.remove("editing"); input.tabIndex = -1; delete cell.dataset.before; delete cell.dataset.wasChanged; delete cell.dataset.beforeRecording; delete cell.dataset.beforeForce;
    closeSearch(cell); validate(cell); const row=cell.closest("tr"); row.classList.toggle("dirty",Boolean(row.querySelector(".changed-cell"))); dirty(); focusCell(cell);
  }
  const move = (cell, dr, dc) => {
    const rows = [...body.rows], row = cell.closest("tr"), ri = rows.indexOf(row), ci = rowCells(row).indexOf(cell);
    focusCell(rowCells(rows[Math.max(0, Math.min(rows.length - 1, ri + dr))])?.[Math.max(0, ci + dc)]);
  };
  const next = (cell, delta) => { const list=cells(), i=list.indexOf(cell); focusCell(list[Math.max(0, Math.min(list.length-1, i+delta))]); };
  const error = (cell, message="") => {
    cell.classList.toggle("client-invalid", Boolean(message));
    let node=cell.querySelector(".client-error");
    if (message && !node) { node=document.createElement("small"); node.className="cell-error client-error"; cell.append(node); }
    if (node) { node.textContent=message; if (!message) node.remove(); }
    cell.closest("tr").classList.toggle("invalid", Boolean(cell.closest("tr").querySelector(".client-invalid,.cell-error:not(.client-error)")));
    return !message;
  };
  function validate(cell) {
    const input=inputOf(cell), field=cell.dataset.field, value=input?.value.trim() || "";
    if (field === "duration" && value) {
      const match=value.match(/^(\d+):([0-5]\d)$/); return error(cell, match ? "" : "Bruk mm:ss, for eksempel 03:42.");
    }
    if (field === "isrc" && value) {
      const normalized=value.toUpperCase().replace(/[\s-]/g, "");
      if (!/^[A-Z]{2}[A-Z0-9]{3}\d{7}$/.test(normalized)) return error(cell, "ISRC har ugyldig format.");
      input.value=normalized;
    }
    return error(cell);
  }
  function addRow(values=[], record=true) {
    if (record) pushUndo();
    const index=Number(total.value), fragment=template.content.cloneNode(true), row=fragment.querySelector("tr");
    row.innerHTML=row.innerHTML.replaceAll("__prefix__", index); row.dataset.index=index;
    fieldInput(row, "sequence_number").value=body.rows.length + 1;
    fields.forEach((name, offset) => { const input=fieldInput(row, name); if (input) input.value=values[offset] || ""; });
    body.append(row); total.value=index+1; prepareRow(row); rowCells(row).forEach(mark); return row;
  }
  const applyMatrix = (matrix, start=currentCell, record=true) => {
    if (!start || !matrix.length) return; if (record) pushUndo();
    const rows=[...body.rows], startRow=rows.indexOf(start.closest("tr")), startCol=rowCells(start.closest("tr")).indexOf(start);
    matrix.forEach((values, r) => {
      while (startRow+r >= body.rows.length) addRow([], false);
      const targetRow=body.rows[startRow+r], targetCells=rowCells(targetRow);
      values.forEach((value, c) => { const cell=targetCells[startCol+c], input=inputOf(cell); if (input) { input.value=value; mark(cell); validate(cell); } });
    });
    focusCell(rowCells(body.rows[startRow+matrix.length-1])[Math.min(startCol+matrix.at(-1).length-1, rowCells(body.rows[startRow]).length-1)]);
  };
  const chooseRecording = (cell, item) => {
    const row=cell.closest("tr"), input=inputOf(cell), id=fieldInput(row,"recording_id"), force=fieldInput(row,"force_create");
    input.value=item?.title || input.value; id.value=item?.id || ""; force.value=item ? "" : "on";
    setRowState(row, item ? "Koblet" : "Ny innspilling", item ? "ok" : "cyan"); mark(cell); closeSearch(cell); finishEdit(cell); next(cell,1);
  };
  async function search(cell) {
    const q=inputOf(cell).value.trim(), box=cell.querySelector(".search-results"); if (q.length<2) return closeSearch(cell);
    try {
      const response=await fetch(`${window.P7_V2.recordingSearch}?q=${encodeURIComponent(q)}`), data=await response.json();
      box.replaceChildren(...data.results.map(item => {
        const button=document.createElement("button"); button.type="button"; button.role="option"; button.dataset.result=JSON.stringify(item);
        button.innerHTML=`<strong>${item.title}</strong><span>${item.artist}${item.isrc ? ` · ${item.isrc}` : ""}</span>`; return button;
      }));
      const create=document.createElement("button"); create.type="button"; create.role="option"; create.dataset.create="true"; create.textContent="+ Opprett ny innspilling"; box.append(create);
      box.hidden=false; box.dataset.active="-1";
    } catch { closeSearch(cell); }
  }
  const moveResult = (box, delta) => {
    const options=[...box.querySelectorAll("button")]; let index=Number(box.dataset.active || -1)+delta;
    index=Math.max(0,Math.min(options.length-1,index)); box.dataset.active=index;
    options.forEach((option,i)=>option.classList.toggle("active",i===index)); options[index]?.scrollIntoView({block:"nearest"});
  };
  function prepareRow(row) { rowCells(row).forEach(cell => { cell.tabIndex=-1; const input=inputOf(cell); if (input) input.tabIndex=-1; }); }
  function prepare() { [...body.rows].forEach(prepareRow); const first=cells()[0]; if (first && !currentCell) { first.tabIndex=0; currentCell=first; } }

  body.addEventListener("click", event => {
    const result=event.target.closest(".search-results button"); if (result) { const cell=result.closest("[data-grid-cell]"); chooseRecording(cell, result.dataset.create ? null : JSON.parse(result.dataset.result)); return; }
    const candidate=event.target.closest("[data-use-recording],[data-create-recording]"); if (candidate) { const cell=candidate.closest("[data-grid-cell]"); chooseRecording(cell, candidate.dataset.useRecording ? {id:candidate.dataset.useRecording,title:candidate.dataset.title} : null); return; }
    const cell=event.target.closest("[data-grid-cell]"); if (cell && event.target===cell) focusCell(cell);
  });
  body.addEventListener("dblclick", event => { const cell=event.target.closest("[data-grid-cell]"); if (cell) beginEdit(cell); });
  body.addEventListener("input", event => {
    const cell=event.target.closest("[data-grid-cell]"); if (!cell) return; mark(cell); validate(cell);
    if (cell.dataset.field==="recording_title") { fieldInput(cell.closest("tr"),"recording_id").value=""; fieldInput(cell.closest("tr"),"force_create").value=""; setRowState(cell.closest("tr"),"Ikke koblet"); clearTimeout(searchTimer); searchTimer=setTimeout(()=>search(cell),180); }
    updateInspector(cell.closest("tr"));
  });
  body.addEventListener("change", event => { const row=event.target.closest("tr"); if (row) { row.classList.add("dirty"); setRowState(row,"Ulagret","warning"); } });
  body.addEventListener("paste", event => {
    const cell=event.target.closest("[data-grid-cell]") || currentCell, text=event.clipboardData?.getData("text/plain");
    if (!cell || !text || (!text.includes("\t") && !/[\r\n]/.test(text))) return;
    event.preventDefault(); applyMatrix(text.replace(/\r/g,"").split("\n").filter((line,i,a)=>line || i<a.length-1).map(line=>line.split("\t")),cell);
  });
  body.addEventListener("keydown", event => {
    const cell=event.target.closest("[data-grid-cell]") || currentCell; if (!cell) return;
    const editing=cell.classList.contains("editing"), box=cell.querySelector(".search-results"), boxOpen=box && !box.hidden;
    if (event.ctrlKey && event.key.toLowerCase()==="c" && !editing) { event.preventDefault(); navigator.clipboard.writeText(inputOf(cell)?.value || ""); return; }
    if (event.ctrlKey && event.key.toLowerCase()==="z") { event.preventDefault(); if (undo.length) restore(undo.pop()); return; }
    if (event.ctrlKey && event.key==="Enter") { event.preventDefault(); form.requestSubmit(); return; }
    if (editing && boxOpen && ["ArrowDown","ArrowUp"].includes(event.key)) { event.preventDefault(); moveResult(box,event.key==="ArrowDown"?1:-1); return; }
    if (editing && boxOpen && event.key==="Enter") { const option=[...box.querySelectorAll("button")][Number(box.dataset.active)]; if(option){event.preventDefault();chooseRecording(cell,option.dataset.create?null:JSON.parse(option.dataset.result));return;} }
    if (editing && event.key==="Escape") { event.preventDefault(); finishEdit(cell,true); return; }
    if (editing && event.key==="Enter") { event.preventDefault(); finishEdit(cell); return; }
    if (event.key==="Tab") { event.preventDefault(); if(editing) finishEdit(cell); next(cell,event.shiftKey?-1:1); return; }
    if (!editing && event.key==="Enter") { event.preventDefault(); beginEdit(cell); return; }
    if (!editing && ["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key)) { event.preventDefault(); move(cell,event.key==="ArrowUp"?-1:event.key==="ArrowDown"?1:0,event.key==="ArrowLeft"?-1:event.key==="ArrowRight"?1:0); return; }
    if (!editing && event.key.length===1 && !event.ctrlKey && !event.metaKey) { event.preventDefault(); beginEdit(cell,event.key); }
  });
  document.querySelector("[data-add-row]")?.addEventListener("click",()=>focusCell(rowCells(addRow())[0]));
  document.querySelector("[data-add-five]")?.addEventListener("click",()=>{pushUndo();let first;for(let i=0;i<5;i++)first ||= addRow([],false);focusCell(rowCells(first)[0]);});
  document.querySelector("[data-open-paste]")?.addEventListener("click",()=>{pastePanel.hidden=false;pastePanel.querySelector("textarea").focus();});
  document.querySelector("[data-close-paste]")?.addEventListener("click",()=>pastePanel.hidden=true);
  document.querySelector("[data-apply-paste]")?.addEventListener("click",()=>{const area=pastePanel.querySelector("textarea"), matrix=area.value.replace(/\r/g,"").split("\n").filter(Boolean).map(line=>line.split("\t")); if(matrix.length){pushUndo();const row=addRow([],false);focusCell(rowCells(row)[1]);applyMatrix(matrix,currentCell,false);}area.value="";pastePanel.hidden=true;});
  document.querySelector("[data-cancel-grid]")?.addEventListener("click",()=>{if(!dirty()||confirm("Forkast ulagrede endringer?"))location.reload();});
  addEventListener("beforeunload",event=>{if(dirty()&&!form.dataset.submitting){event.preventDefault();event.returnValue="";}});
  document.addEventListener("keydown",event=>{if(event.ctrlKey&&event.shiftKey&&event.key.toLowerCase()==="n"){event.preventDefault();focusCell(rowCells(addRow())[0]);}});
  form.addEventListener("submit",event=>{
    if(form.dataset.submitting){event.preventDefault();return;}
    const invalid=cells().filter(cell=>!validate(cell)); if(invalid.length){event.preventDefault();focusCell(invalid[0]);return;}
    form.dataset.submitting="true"; sessionStorage.setItem(focusKey,JSON.stringify(currentCell?[currentCell.closest("tr").sectionRowIndex,rowCells(currentCell.closest("tr")).indexOf(currentCell)]:[0,0])); form.querySelector("button[type=submit]").disabled=true;
  });
  prepare();
  try { const [r,c]=JSON.parse(sessionStorage.getItem(focusKey)||"[0,0]"); focusCell(rowCells(body.rows[r]||body.rows[0])?.[c]); } catch { focusCell(cells()[0]); }
})();
