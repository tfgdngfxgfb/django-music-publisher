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
  document.querySelector("[data-close-inspector]")?.addEventListener("click", () => {
    inspector.hidden = true; sessionStorage.setItem("p7-v2-inspector", "closed");
  });
  document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => {
    document.querySelectorAll("[data-tab]").forEach(item => item.setAttribute("aria-selected", String(item === button)));
    document.querySelectorAll("[data-panel]").forEach(panel => { panel.hidden = panel.dataset.panel !== button.dataset.tab; });
  }));
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
  const storedWidth = localStorage.getItem("p7-v2-inspector-width");
  if (storedWidth) root.style.setProperty("--inspector", `${storedWidth}px`);

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
    if (event.key !== "Enter" || event.target.tagName !== "INPUT") return;
    event.preventDefault(); const row = event.target.closest("tr"); const inputs = [...row.querySelectorAll("input:not([type=hidden]):not([type=checkbox])")];
    const column = inputs.indexOf(event.target); const next = row.nextElementSibling || addRow(); const nextInputs=[...next.querySelectorAll("input:not([type=hidden]):not([type=checkbox])")]; nextInputs[column]?.focus();
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
