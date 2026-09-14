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

  const player = document.querySelector("#v2-player");
  const playerShow = document.querySelector("[data-player-show]");
  const playerHiddenKey = "p7-v2-player-hidden";
  const setPlayerHidden = hidden => {
    if (!player || !playerShow) return;
    player.hidden = hidden;
    playerShow.hidden = !hidden;
    document.body.classList.toggle("player-hidden", hidden);
    localStorage.setItem(playerHiddenKey, String(hidden));
  };
  document.querySelector("[data-player-hide]")?.addEventListener("click", () => setPlayerHidden(true));
  playerShow?.addEventListener("click", () => setPlayerHidden(false));
  if (localStorage.getItem(playerHiddenKey) === "true") setPlayerHidden(true);

  const autoSubmitFilters = document.querySelector("[data-auto-submit-filters]");
  autoSubmitFilters?.addEventListener("change", event => {
    if (!event.target.matches("select, input[type='checkbox'], input[type='radio']")) return;
    autoSubmitFilters.requestSubmit();
  });

  const libraryRows = [...document.querySelectorAll("[data-library-row]")];
  const keyboardFocusKey = `p7-v2-library-keyboard:${location.pathname}`;
  document.querySelectorAll("[data-row-href]").forEach(row => row.addEventListener("click", event => {
    if (event.target.closest("a, button, input, select, textarea, label")) return;
    const scroller = row.closest(".table-scroll");
    const key = `p7-v2-scroll:${location.pathname}${location.search.replace(/([?&])selected=[^&]*/, "$1")}`;
    sessionStorage.setItem(key, scroller?.scrollTop || 0);
    if (row.matches("[data-library-row]")) sessionStorage.setItem(keyboardFocusKey, "true");
    location.href = row.dataset.rowHref;
  }));

  const inspector = document.querySelector("#v2-inspector");
  const layout = inspector?.parentElement;
  const inspectorKey = `p7-v2-inspector-open:${location.pathname}`;
  const inspectorOpeners = document.querySelectorAll("[data-open-inspector]");
  const setInspector = open => {
    if (!inspector) return;
    inspector.hidden = !open;
    layout?.classList.toggle("inspector-closed", !open);
    inspectorOpeners.forEach(button => { button.hidden = open; });
    localStorage.setItem(inspectorKey, String(open));
  };
  document.querySelector("[data-close-inspector]")?.addEventListener("click", () => {
    setInspector(false);
  });
  inspectorOpeners.forEach(button => button.addEventListener("click", () => setInspector(true)));
  if (inspector && localStorage.getItem(inspectorKey) === "false") setInspector(false);
  document.addEventListener("keydown", event => {
    const editing = event.target.closest("input, select, textarea, [contenteditable='true']");
    if (editing) return;
    const row = event.target.closest?.("[data-library-row]") || document.activeElement?.closest?.("[data-library-row]");
    if (event.key === "Escape" && inspector && !inspector.hidden) {
      event.preventDefault(); setInspector(false); row?.focus(); return;
    }
    if (!row || !libraryRows.length) return;
    if (event.key === "Enter") {
      event.preventDefault(); location.href = row.dataset.detailHref; return;
    }
    if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
    const index = libraryRows.indexOf(row);
    const next = libraryRows[Math.max(0, Math.min(libraryRows.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
    event.preventDefault();
    sessionStorage.setItem(keyboardFocusKey, "true");
    location.href = next.dataset.rowHref;
  });
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
    localStorage.setItem(`p7-v2-filters:${location.pathname}`, String(!closed));
  });
  if (filterToggle && localStorage.getItem(`p7-v2-filters:${location.pathname}`) === "false") filterToggle.click();

  const librarySearch = document.querySelector("[data-library-search]");
  document.addEventListener("keydown", event => {
    if (!librarySearch || event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest("input, select, textarea, [contenteditable='true']")) return;
    event.preventDefault();
    librarySearch.focus();
    librarySearch.select();
  });

  const scrollKey = `p7-v2-scroll:${location.pathname}${location.search.replace(/([?&])selected=[^&]*/, "$1")}`;
  const scroller = document.querySelector(".table-scroll");
  if (scroller) {
    scroller.scrollTop = Number(sessionStorage.getItem(scrollKey) || 0);
    document.querySelectorAll(".row-link").forEach(link => link.addEventListener("click", () => sessionStorage.setItem(scrollKey, scroller.scrollTop)));
  }
  if (sessionStorage.getItem(keyboardFocusKey) === "true") {
    document.querySelector("[data-library-row].selected")?.focus({preventScroll: true});
  }

})();
