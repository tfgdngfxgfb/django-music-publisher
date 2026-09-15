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

  const audio = document.querySelector("#v2-audio");
  const playerToggle = document.querySelector("[data-player-toggle]");
  const playerTitle = document.querySelector("[data-player-title]");
  const playerSubtitle = document.querySelector("[data-player-subtitle]");
  const playerProgress = document.querySelector("[data-player-progress]");
  let playingRecording = "";
  let playingArtist = "";
  const timeText = value => {
    if (!Number.isFinite(value)) return "0:00";
    const seconds = Math.max(0, Math.floor(value));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  };
  const updatePlaybackButtons = () => {
    document.querySelectorAll("[data-play-recording]").forEach(button => {
      const active = button.dataset.playRecordingId === playingRecording && audio && !audio.paused;
      button.classList.toggle("playing", active);
      button.setAttribute("aria-pressed", String(active));
    });
  };
  const updatePlayerTime = () => {
    if (!audio || !playerSubtitle || !playingRecording) return;
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    playerSubtitle.textContent = `${playingArtist || "Uavklart artist"} · ${timeText(audio.currentTime)} / ${timeText(duration)}`;
    if (playerProgress) {
      playerProgress.max = String(duration || 0);
      playerProgress.value = String(audio.currentTime || 0);
      playerProgress.disabled = !duration;
    }
  };
  const startPlayback = async trigger => {
    if (!audio || !trigger.dataset.playUrl) return;
    setPlayerHidden(false);
    const recordingId = trigger.dataset.playRecordingId || "";
    if (playingRecording !== recordingId || audio.dataset.playUrl !== trigger.dataset.playUrl) {
      audio.pause();
      playingRecording = recordingId;
      playingArtist = trigger.dataset.playArtist || "";
      audio.dataset.playUrl = trigger.dataset.playUrl;
      audio.src = trigger.dataset.playUrl;
      playerTitle.textContent = trigger.dataset.playTitle || "Innspilling";
      playerSubtitle.textContent = `${playingArtist || "Uavklart artist"} · åpner radiofil …`;
      playerToggle.disabled = false;
      playerProgress.disabled = true;
      audio.load();
    }
    try {
      await audio.play();
    } catch {
      playerSubtitle.textContent = "Radiofilen kunne ikke leses eller spilles.";
    }
    updatePlaybackButtons();
  };
  document.addEventListener("click", event => {
    const trigger = event.target.closest("[data-play-recording]");
    if (!trigger || trigger.disabled) return;
    event.preventDefault();
    event.stopPropagation();
    startPlayback(trigger);
  });
  playerToggle?.addEventListener("click", async () => {
    if (!audio?.src) return;
    if (audio.paused) {
      try { await audio.play(); } catch { playerSubtitle.textContent = "Radiofilen kunne ikke leses eller spilles."; }
    } else audio.pause();
  });
  playerProgress?.addEventListener("input", () => {
    if (audio && Number.isFinite(audio.duration)) audio.currentTime = Number(playerProgress.value);
  });
  audio?.addEventListener("play", () => {
    playerToggle.textContent = "Ⅱ";
    playerToggle.setAttribute("aria-label", "Pause");
    updatePlaybackButtons();
  });
  audio?.addEventListener("pause", () => {
    playerToggle.textContent = "▶";
    playerToggle.setAttribute("aria-label", "Spill av");
    updatePlaybackButtons();
  });
  audio?.addEventListener("loadedmetadata", updatePlayerTime);
  audio?.addEventListener("timeupdate", updatePlayerTime);
  audio?.addEventListener("ended", updatePlaybackButtons);
  audio?.addEventListener("error", () => {
    playerToggle.textContent = "▶";
    playerSubtitle.textContent = "Radiofilen kunne ikke leses eller spilles.";
    updatePlaybackButtons();
  });

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
  const alignInspectorWithTable = () => {
    if (!inspector || !layout) return;
    const table = layout.querySelector(".table-scroll");
    const offset = table
      ? Math.max(0, table.getBoundingClientRect().top - layout.getBoundingClientRect().top)
      : 0;
    layout.style.setProperty("--inspector-offset", `${offset}px`);
  };
  alignInspectorWithTable();
  window.addEventListener("resize", alignInspectorWithTable);
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
    filterToggle.querySelector("[data-filter-direction]").textContent = closed ? "›" : "‹";
    const label = closed ? "Vis filtre" : "Skjul filtre";
    filterToggle.querySelector("[data-filter-label]").textContent = label;
    filterToggle.setAttribute("aria-label", label);
    filterToggle.setAttribute("title", label);
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
  }
  if (sessionStorage.getItem(keyboardFocusKey) === "true") {
    document.querySelector("[data-library-row].selected")?.focus({preventScroll: true});
  }

})();
