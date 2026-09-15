(() => {
  const root = document.documentElement;
  const theme = document.querySelector("#v2-theme-toggle");
  theme?.addEventListener("click", () => {
    root.dataset.v2Theme = root.dataset.v2Theme === "dark" ? "light" : "dark";
    localStorage.setItem("p7-v2-theme", root.dataset.v2Theme);
  });

  document.addEventListener("click", async event => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    if (!button.dataset.copy) return;
    await navigator.clipboard.writeText(button.dataset.copy);
    const old = button.textContent; button.textContent = "Kopiert";
    setTimeout(() => { button.textContent = old; }, 1100);
  });

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
  const primaryPlaybackTrigger = () => {
    const trigger = document.querySelector("[data-player-primary][data-play-recording]");
    return trigger && !trigger.disabled && trigger.dataset.playUrl ? trigger : null;
  };
  const syncPlayerAvailability = () => {
    if (!playerToggle || audio?.src) return;
    const trigger = primaryPlaybackTrigger();
    playerToggle.disabled = !trigger;
    playerToggle.setAttribute(
      "aria-label",
      trigger ? `Spill ${trigger.dataset.playTitle || "valgt innspilling"}` : "Spill av"
    );
  };
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
    const libraryRow = trigger.closest("[data-library-row]");
    if (libraryRow) void followLibraryRow(libraryRow);
  });
  playerToggle?.addEventListener("click", async () => {
    if (!audio?.src) {
      const trigger = primaryPlaybackTrigger();
      if (trigger) await startPlayback(trigger);
      return;
    }
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
  document.addEventListener("p7:playback-context-changed", syncPlayerAvailability);
  syncPlayerAvailability();

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
    if (row.matches("[data-library-row]")) {
      sessionStorage.setItem(keyboardFocusKey, "true");
      void followLibraryRow(row);
      return;
    }
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
  const libraryInspectorToggles = document.querySelectorAll("[data-toggle-library-inspector]");
  const animateLibraryInspector = layout?.matches(".archive-layout");
  const inspectorMotionMs = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 170;
  let inspectorMotionTimer = 0;
  const setInspector = open => {
    if (!inspector) return;
    inspectorOpeners.forEach(button => { button.hidden = open; });
    libraryInspectorToggles.forEach(button => {
      button.setAttribute("aria-expanded", String(open));
      const label = button.querySelector("[data-inspector-toggle-label]");
      if (label) label.textContent = open ? "Skjul detaljer" : "Vis detaljer";
    });
    if (!animateLibraryInspector) {
      inspector.hidden = !open;
      layout?.classList.toggle("inspector-closed", !open);
      return;
    }
    if (open && !inspector.hidden && !inspector.classList.contains("inspector-leaving")) {
      layout?.classList.remove("inspector-closed");
      return;
    }
    window.clearTimeout(inspectorMotionTimer);
    if (open) {
      layout?.classList.remove("inspector-closed");
      inspector.hidden = false;
      inspector.classList.add("inspector-transitioning", "inspector-leaving");
      requestAnimationFrame(() => requestAnimationFrame(() => {
        inspector.classList.remove("inspector-leaving");
      }));
      inspectorMotionTimer = window.setTimeout(() => {
        inspector.classList.remove("inspector-transitioning");
      }, inspectorMotionMs);
      return;
    }
    inspector.classList.add("inspector-transitioning", "inspector-leaving");
    inspectorMotionTimer = window.setTimeout(() => {
      inspector.hidden = true;
      inspector.classList.remove("inspector-transitioning", "inspector-leaving");
      layout?.classList.add("inspector-closed");
    }, inspectorMotionMs);
  };
  document.addEventListener("click", event => {
    if (event.target.closest("[data-close-inspector]")) setInspector(false);
  });
  inspectorOpeners.forEach(button => button.addEventListener("click", () => setInspector(true)));
  libraryInspectorToggles.forEach(button => button.addEventListener("click", () => {
    const isOpen = !inspector?.hidden && !inspector?.classList.contains("inspector-leaving");
    setInspector(!isOpen);
  }));
  let libraryDetailRequest = 0;
  const followLibraryRow = async row => {
    const requestId = ++libraryDetailRequest;
    libraryRows.forEach(item => {
      const selected = item === row;
      item.classList.toggle("selected", selected);
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    if (!inspector || !row.dataset.rowHref) return;
    try {
      const response = await fetch(row.dataset.rowHref, {
        headers: {"X-Requested-With": "XMLHttpRequest"},
      });
      if (!response.ok || requestId !== libraryDetailRequest) return;
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const nextInspector = page.querySelector("#v2-inspector");
      if (!nextInspector || requestId !== libraryDetailRequest) return;
      inspector.innerHTML = nextInspector.innerHTML;
      history.replaceState({}, "", row.dataset.rowHref);
      setInspector(true);
      alignInspectorWithTable();
    } catch {
      // Playback remains usable if the detail refresh is temporarily unavailable.
    }
  };
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
    next.focus({preventScroll: true});
    next.scrollIntoView({block: "nearest", inline: "nearest"});
    void followLibraryRow(next);
  });
  document.addEventListener("click", event => {
    const button = event.target.closest("[data-tab]");
    if (!button || !inspector?.contains(button)) return;
    document.querySelectorAll("[data-tab]").forEach(item => item.setAttribute("aria-selected", String(item === button)));
    document.querySelectorAll("[data-panel]").forEach(panel => { panel.hidden = panel.dataset.panel !== button.dataset.tab; });
  });
  document.addEventListener("keydown", event => {
    if (!event.target.closest("#v2-inspector .tabs")) return;
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const tabs = [...inspector.querySelectorAll("[data-tab]")];
    const current = tabs.indexOf(document.activeElement);
    const next = (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault(); tabs[next].focus(); tabs[next].click();
  });
  document.addEventListener("pointerdown", event => {
    const handle = event.target.closest("#v2-inspector .resize-handle");
    if (!handle) return;
    event.preventDefault(); handle.setPointerCapture(event.pointerId);
    const move = current => {
      const width = Math.max(300, Math.min(560, innerWidth - current.clientX));
      root.style.setProperty("--inspector", `${width}px`); localStorage.setItem("p7-v2-inspector-width", width);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", () => handle.removeEventListener("pointermove", move), {once:true});
  });
  document.addEventListener("keydown", event => {
    if (!event.target.closest("#v2-inspector .resize-handle")) return;
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

  const recordingFiles = document.querySelector("[data-recording-files]");
  if (recordingFiles) {
    const rows = [...recordingFiles.querySelectorAll("[data-file-row]")];
    const selectFile = (row, moveFocus = false) => {
      if (!row) return;
      const id = row.dataset.fileRow;
      rows.forEach(item => {
        const selected = item === row;
        item.classList.toggle("selected", selected);
        item.setAttribute("aria-selected", String(selected));
        item.tabIndex = selected ? 0 : -1;
      });
      recordingFiles.querySelectorAll("[data-file-detail]").forEach(panel => {
        panel.hidden = panel.dataset.fileDetail !== id;
      });
      recordingFiles.querySelectorAll("[data-file-locations]").forEach(panel => {
        panel.hidden = panel.dataset.fileLocations !== id;
      });
      recordingFiles.querySelectorAll("[data-file-history]").forEach(panel => {
        panel.hidden = panel.dataset.fileHistory !== id;
      });
      const url = new URL(location.href);
      url.searchParams.set("selected_file", id);
      history.replaceState({}, "", url);
      recordingFiles.querySelectorAll("[data-file-return]").forEach(input => {
        input.value = `${url.pathname}${url.search}`;
      });
      if (moveFocus) row.focus({preventScroll: true});
    };
    rows.forEach(row => row.addEventListener("click", event => {
      if (event.target.closest("a, button, input")) return;
      selectFile(row);
    }));
    recordingFiles.addEventListener("keydown", event => {
      const row = event.target.closest("[data-file-row]");
      if (!row || !["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      let index = rows.indexOf(row);
      if (event.key === "ArrowUp") index -= 1;
      if (event.key === "ArrowDown") index += 1;
      if (event.key === "Home") index = 0;
      if (event.key === "End") index = rows.length - 1;
      event.preventDefault();
      const next = rows[Math.max(0, Math.min(rows.length - 1, index))];
      selectFile(next, true);
      next.scrollIntoView({block: "nearest", inline: "nearest"});
    });
  }

})();
