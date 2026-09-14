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

  const scrollKey = `p7-v2-scroll:${location.pathname}${location.search.replace(/([?&])selected=[^&]*/, "$1")}`;
  const scroller = document.querySelector(".table-scroll");
  if (scroller) {
    scroller.scrollTop = Number(sessionStorage.getItem(scrollKey) || 0);
    document.querySelectorAll(".row-link").forEach(link => link.addEventListener("click", () => sessionStorage.setItem(scrollKey, scroller.scrollTop)));
  }

})();
