(() => {
  const root = document.documentElement;
  const theme = document.querySelector("#v2-theme-toggle");
  theme?.addEventListener("click", () => {
    root.dataset.v2Theme = root.dataset.v2Theme === "dark" ? "light" : "dark";
    localStorage.setItem("p7-v2-theme", root.dataset.v2Theme);
  });

  document.querySelectorAll("[data-copy]").forEach(button => button.addEventListener("click", async () => {
    if (!button.dataset.copy) return;
    await navigator.clipboard.writeText(button.dataset.copy);
    const old = button.textContent; button.textContent = "Kopiert";
    setTimeout(() => { button.textContent = old; }, 1100);
  }));

  const inspector = document.querySelector("#v2-inspector");
  const layout = inspector?.parentElement;
  const inspectorOpeners = document.querySelectorAll("[data-open-inspector]");
  const setInspector = open => {
    if (!inspector) return;
    inspector.hidden = !open;
    layout?.classList.toggle("inspector-closed", !open);
    inspectorOpeners.forEach(button => { button.hidden = open; });
  };
  document.querySelector("[data-close-inspector]")?.addEventListener("click", () => {
    setInspector(false);
  });
  inspectorOpeners.forEach(button => button.addEventListener("click", () => setInspector(true)));
  document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => {
    document.querySelectorAll("[data-tab]").forEach(item => item.setAttribute("aria-selected", String(item === button)));
    document.querySelectorAll("[data-panel]").forEach(panel => { panel.hidden = panel.dataset.panel !== button.dataset.tab; });
  }));
  document.querySelector(".tabs")?.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const tabs = [...event.currentTarget.querySelectorAll("[data-tab]")];
    const current = tabs.indexOf(document.activeElement);
    const next = (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault(); tabs[next].focus(); tabs[next].click();
  });
  const handle = inspector?.querySelector(".resize-handle");
  handle?.addEventListener("pointerdown", event => {
    event.preventDefault(); handle.setPointerCapture(event.pointerId);
    const move = current => {
      const width = Math.max(300, Math.min(560, innerWidth - current.clientX));
      root.style.setProperty("--inspector", `${width}px`); localStorage.setItem("p7-v2-inspector-width", width);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", () => handle.removeEventListener("pointermove", move), {once:true});
  });
  handle?.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const current = parseInt(getComputedStyle(root).getPropertyValue("--inspector"), 10) || 360;
    const width = Math.max(300, Math.min(560, current + (event.key === "ArrowLeft" ? 16 : -16)));
    root.style.setProperty("--inspector", `${width}px`);
    localStorage.setItem("p7-v2-inspector-width", width);
    event.preventDefault();
  });
  const storedWidth = localStorage.getItem("p7-v2-inspector-width");
  if (storedWidth) root.style.setProperty("--inspector", `${storedWidth}px`);

  const filterToggle = document.querySelector("[data-collapse='filters']");
  filterToggle?.addEventListener("click", () => {
    const page = filterToggle.closest(".archive-layout");
    const closed = page.classList.toggle("filters-closed");
    filterToggle.textContent = closed ? "›" : "‹";
    filterToggle.setAttribute("aria-label", closed ? "Vis filtre" : "Skjul filtre");
  });

  const scrollKey = `p7-v2-scroll:${location.pathname}${location.search.replace(/([?&])selected=[^&]*/, "$1")}`;
  const scroller = document.querySelector(".table-scroll");
  if (scroller) {
    scroller.scrollTop = Number(sessionStorage.getItem(scrollKey) || 0);
    document.querySelectorAll(".row-link").forEach(link => link.addEventListener("click", () => sessionStorage.setItem(scrollKey, scroller.scrollTop)));
  }

  const form = document.querySelector("#track-grid-form");
  if (!form) return;
  const body = document.querySelector("#track-rows");
  const total = form.querySelector("[name='tracks-TOTAL_FORMS']");
  const template = document.querySelector("#empty-track-row");
  const dirtyCount = document.querySelector("#dirty-count");
  let dirty = new Set();
  const markDirty = row => {
    row.classList.add("dirty"); row.querySelector(".row-state")?.replaceChildren("Ulagret");
    dirty.add(row); dirtyCount.textContent = `${dirty.size} ulagrede rad${dirty.size === 1 ? "" : "er"}`;
  };
  body.addEventListener("input", event => markDirty(event.target.closest("tr")));
  body.addEventListener("change", event => markDirty(event.target.closest("tr")));
  const updateTrackInspector = row => {
    if (!row) return;
    const index = row.dataset.index;
    const value = name => row.querySelector(`[name='tracks-${index}-${name}']`)?.value || "";
    document.querySelectorAll(".track-row.active").forEach(item => item.classList.remove("active"));
    row.classList.add("active");
    const releaseTitle = value("title_override"), recordingTitle = value("recording_title");
    document.querySelector("#inspector-track-title")?.replaceChildren(releaseTitle || recordingTitle || "Nytt spor");
    document.querySelector("#inspector-common-title")?.replaceChildren(recordingTitle || "Ikke valgt");
    document.querySelector("#inspector-position")?.replaceChildren(`${value("disc_number") ? `Plate ${value("disc_number")} · ` : ""}${value("side")}${value("track_number") || value("sequence_number")}`);
    document.querySelector("#inspector-artist")?.replaceChildren(value("artists") || "Uavklart");
    document.querySelector("#inspector-isrc")?.replaceChildren(value("isrc") || "Ikke registrert");
  };
  body.addEventListener("focusin", event => updateTrackInspector(event.target.closest("tr")));
  body.addEventListener("click", event => updateTrackInspector(event.target.closest("tr")));

  function addRow(values = []) {
    const index = Number(total.value); const fragment = template.content.cloneNode(true);
    const row = fragment.querySelector("tr"); row.innerHTML = row.innerHTML.replaceAll("__prefix__", index);
    const names = ["disc_number","side","track_number","title_override","artists","composers","lyricists","duration","isrc"];
    names.forEach((name, offset) => { const input = row.querySelector(`[name='tracks-${index}-${name}']`); if (input) input.value = values[offset] || ""; });
    const title = row.querySelector(`[name='tracks-${index}-recording_title']`); if (title) title.value = values[3] || "";
    row.querySelector(`[name='tracks-${index}-sequence_number']`).value = body.querySelectorAll("tr").length + 1;
    body.append(row); total.value = index + 1; markDirty(row); return row;
  }
  document.querySelector("[data-add-row]")?.addEventListener("click", () => addRow().querySelector("input:not([type=hidden])")?.focus());
  document.querySelector("[data-add-five]")?.addEventListener("click", () => { for (let i=0;i<5;i++) addRow(); });
  const pastePanel = document.querySelector(".paste-panel");
  document.querySelector("[data-open-paste]")?.addEventListener("click", () => { pastePanel.hidden=false; pastePanel.querySelector("textarea").focus(); });
  document.querySelector("[data-close-paste]")?.addEventListener("click", () => { pastePanel.hidden=true; });
  document.querySelector("[data-apply-paste]")?.addEventListener("click", () => {
    const area = pastePanel.querySelector("textarea");
    area.value.split(/\r?\n/).filter(line => line.trim()).forEach(line => addRow(line.split("\t")));
    area.value=""; pastePanel.hidden=true;
  });
  document.querySelector("[data-cancel-grid]")?.addEventListener("click", () => { if (!dirty.size || confirm("Forkast ulagrede endringer?")) location.reload(); });
  addEventListener("beforeunload", event => { if (dirty.size && !form.dataset.submitting) { event.preventDefault(); event.returnValue=""; } });
  form.addEventListener("submit", event => {
    if (form.dataset.submitting) { event.preventDefault(); return; }
    form.dataset.submitting="true"; form.querySelector("button[type=submit]").disabled=true;
  });
  body.addEventListener("keydown", event => {
    if (event.target.tagName !== "INPUT") return;
    const row = event.target.closest("tr");
    const inputs = [...row.querySelectorAll("input:not([type=hidden]):not([type=checkbox])")];
    const column = inputs.indexOf(event.target);
    if (event.ctrlKey && event.key === "Enter") {
      event.preventDefault(); form.requestSubmit(); return;
    }
    if (event.ctrlKey && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
      event.preventDefault(); inputs[column + (event.key === "ArrowRight" ? 1 : -1)]?.focus(); return;
    }
    if ((event.ctrlKey && ["ArrowUp", "ArrowDown"].includes(event.key)) || event.key === "Enter") {
      event.preventDefault();
      let targetRow;
      if (event.key === "ArrowUp" || (event.key === "Enter" && event.shiftKey)) targetRow = row.previousElementSibling;
      else targetRow = row.nextElementSibling || addRow();
      const targetInputs = targetRow ? [...targetRow.querySelectorAll("input:not([type=hidden]):not([type=checkbox])")] : [];
      targetInputs[column]?.focus();
    }
  });
  document.addEventListener("keydown", event => {
    if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "n") {
      event.preventDefault(); addRow().querySelector("input:not([type=hidden])")?.focus(); return;
    }
    if (event.key !== "Escape") return;
    document.querySelectorAll(".search-results").forEach(result => { result.hidden = true; });
    if (pastePanel && !pastePanel.hidden) pastePanel.hidden = true;
  });
  body.addEventListener("click", async event => {
    const existing = event.target.closest("[data-use-recording]");
    if (existing) {
      const row=existing.closest("tr"), index=row.dataset.index;
      row.querySelector(`[name='tracks-${index}-recording_id']`).value=existing.dataset.useRecording;
      row.querySelector(`[name='tracks-${index}-recording_title']`).value=existing.dataset.title; markDirty(row); return;
    }
    const search = event.target.closest("[data-search-recording]"); if (!search) return;
    const row=search.closest("tr"), index=row.dataset.index, input=row.querySelector(`[name='tracks-${index}-recording_title']`), results=row.querySelector(".search-results");
    const response=await fetch(`${window.P7_V2.recordingSearch}?q=${encodeURIComponent(input.value)}`); const data=await response.json();
    results.replaceChildren(...data.results.map(item => { const button=document.createElement("button"); button.type="button"; button.textContent=`${item.title} — ${item.artist}${item.isrc ? ` · ${item.isrc}`:""}`; button.addEventListener("click",()=>{row.querySelector(`[name='tracks-${index}-recording_id']`).value=item.id; input.value=item.title; results.hidden=true; markDirty(row);}); return button;})); results.hidden=false;
  });
})();
