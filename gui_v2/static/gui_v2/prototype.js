(() => {
  const root = document.documentElement;
  document.addEventListener("click", async event => {
    if (event.target.closest("#v2-theme-toggle")) {
      root.dataset.v2Theme = root.dataset.v2Theme === "dark" ? "light" : "dark";
      localStorage.setItem("p7-v2-theme", root.dataset.v2Theme);
      return;
    }
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
  const playerCover = document.querySelector("[data-player-cover]");
  const playerPrevious = document.querySelector("[data-player-previous]");
  const playerNext = document.querySelector("[data-player-next]");
  const playerVolume = document.querySelector("[data-player-volume]");
  const playerMute = document.querySelector("[data-player-mute]");
  const playerElapsed = document.querySelector("[data-player-elapsed]");
  const playerDuration = document.querySelector("[data-player-duration]");
  const playerQueue = document.querySelector("[data-player-queue]");
  const queuePanel = document.querySelector("[data-player-queue-panel]");
  const queueList = document.querySelector("[data-player-queue-list]");
  const queueLabel = document.querySelector("[data-player-queue-context]");
  const playerNotice = document.querySelector("[data-player-notice]");
  const recordingLinks = [...document.querySelectorAll("[data-player-recording-link]")];
  let followLibraryRow = async () => {};
  let playingRecording = "";
  let playingArtist = "";
  let queue = [];
  let queueIndex = -1;
  let queueContext = {type: "SINGLE", key: "", label: "Enkeltinnspilling"};
  let queueTransitioning = false;
  let lastFailedSource = "";
  let noticeTimer;
  const librarySignature = () => {
    const url = new URL(location.href);
    url.searchParams.delete("selected");
    return `${url.pathname}?${url.searchParams}`;
  };
  const trackFromTrigger = trigger => ({
    playUrl: trigger.dataset.playUrl || "",
    playRecordingId: trigger.dataset.playRecordingId || "",
    playTitle: trigger.dataset.playTitle || "Innspilling",
    playArtist: trigger.dataset.playArtist || "",
    playCoverUrl: trigger.dataset.playCoverUrl || "",
    playCoverAlt: trigger.dataset.playCoverAlt || "",
    playMessage: trigger.dataset.playMessage || "Ingen spillbar radiofil",
  });
  const trackFromRow = row => {
    const button = row.querySelector("[data-play-recording]");
    const data = row.dataset;
    const editedRecording = row.matches(".track-row") ? row.querySelector("[name$='-recording_id']")?.value : null;
    const matchesSavedRecording = editedRecording === null || editedRecording === (data.playbackRecordingId || "");
    return trackFromTrigger({dataset: {
      playUrl: matchesSavedRecording ? (data.playbackUrl || button?.dataset.playUrl || "") : "",
      playRecordingId: matchesSavedRecording ? (data.playbackRecordingId || button?.dataset.playRecordingId || "") : "",
      playTitle: data.playbackTitle || button?.dataset.playTitle || row.querySelector("[name$='-recording_title']")?.value || "Spor uten innspilling",
      playArtist: data.playbackArtist || button?.dataset.playArtist || "",
      playCoverUrl: data.playCoverUrl || button?.dataset.playCoverUrl || "",
      playCoverAlt: data.playCoverAlt || button?.dataset.playCoverAlt || "",
      playMessage: matchesSavedRecording ? (data.playbackMessage || "Ingen spillbar radiofil") : "Lagre sporkoblingen før avspilling",
    }});
  };
  const renderQueue = () => {
    if (!queueList || !queueLabel) return;
    queueLabel.textContent = queueContext.label;
    queueList.replaceChildren();
    queue.forEach((track, index) => {
      const item = document.createElement("li");
      item.setAttribute("aria-current", String(index === queueIndex));
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.queueIndex = String(index);
      button.disabled = !track.playUrl;
      const title = document.createElement("strong");
      title.textContent = `${index + 1}. ${track.playTitle}`;
      const marker = document.createElement("small");
      marker.textContent = index === queueIndex ? "Spilles nå" : "";
      const detail = document.createElement("span");
      detail.textContent = track.playUrl ? (track.playArtist || "Uavklart artist") : track.playMessage;
      button.append(title, marker, detail);
      item.append(button);
      queueList.append(item);
    });
  };
  const notify = message => {
    if (!playerNotice) return;
    clearTimeout(noticeTimer);
    playerNotice.textContent = message;
    playerNotice.hidden = !message;
    if (message) noticeTimer = setTimeout(() => { playerNotice.hidden = true; }, 3800);
  };
  const updateQueueControls = () => {
    if (playerPrevious) playerPrevious.disabled = queueIndex < 0 || !queue.slice(0, queueIndex).some(track => track.playUrl);
    if (playerNext) playerNext.disabled = queueIndex < 0 || !queue.slice(queueIndex + 1).some(track => track.playUrl);
    if (playerQueue) playerQueue.disabled = !queue.length;
    renderQueue();
  };
  const captureQueue = trigger => {
    const libraryRow = trigger.closest?.("[data-library-row]") ||
      (document.body.classList.contains("music-library-page")
        ? document.querySelector("[data-library-row].selected") : null);
    const releaseForm = document.querySelector("#track-grid-form[data-release-id]");
    const releaseRow = trigger.closest?.(".track-row") || releaseForm?.querySelector(".track-row.active");
    if (libraryRow && trackFromRow(libraryRow).playRecordingId === trigger.dataset.playRecordingId) {
      const rows = [...document.querySelectorAll("[data-library-row]")].filter(item => !item.hidden);
      queue = rows.map(trackFromRow);
      queueIndex = rows.indexOf(libraryRow);
      queueContext = {type: "MUSIC_LIBRARY", key: librarySignature(), label: "Musikkarkiv"};
    } else if (releaseRow && releaseForm && trackFromRow(releaseRow).playRecordingId === trigger.dataset.playRecordingId) {
      const rows = [...releaseForm.querySelectorAll("#track-rows > .track-row")]
        .filter(item => !item.hidden && !item.querySelector("[name$='-remove']:checked"));
      queue = rows.map(trackFromRow);
      queueIndex = rows.indexOf(releaseRow);
      queueContext = {type: "RELEASE", key: releaseForm.dataset.releaseId, label: "Utgivelsens sporliste"};
    } else if (queue[queueIndex]?.playRecordingId === trigger.dataset.playRecordingId &&
               queue[queueIndex]?.playUrl === trigger.dataset.playUrl) {
      return; // A play button on the Recording page must not replace its existing context.
    } else {
      queue = [trackFromTrigger(trigger)];
      queueIndex = 0;
      queueContext = {type: "SINGLE", key: "", label: "Enkeltinnspilling"};
    }
    updateQueueControls();
  };
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
    playerSubtitle.textContent = playingArtist || "Uavklart artist";
    if (playerElapsed) playerElapsed.textContent = timeText(audio.currentTime);
    if (playerDuration) playerDuration.textContent = timeText(duration);
    if (playerProgress) {
      playerProgress.max = String(duration || 0);
      playerProgress.value = String(audio.currentTime || 0);
      playerProgress.disabled = !duration;
    }
  };
  const updatePlayerCover = trigger => {
    if (!playerCover) return;
    playerCover.replaceChildren();
    if (trigger.dataset.playCoverUrl) {
      const cover = document.createElement("img");
      cover.src = trigger.dataset.playCoverUrl;
      cover.alt = trigger.dataset.playCoverAlt || "";
      playerCover.append(cover);
      return;
    }
    const placeholder = document.createElement("span");
    placeholder.textContent = "♫";
    placeholder.setAttribute("aria-hidden", "true");
    playerCover.append(placeholder);
  };
  const startPlayback = async (trigger, preserveQueue = false) => {
    if (!audio || !trigger.dataset.playUrl) return false;
    if (!preserveQueue) captureQueue(trigger);
    const details = queue[queueIndex]?.playRecordingId === trigger.dataset.playRecordingId &&
      queue[queueIndex]?.playUrl === trigger.dataset.playUrl ? queue[queueIndex] : trigger.dataset;
    setPlayerHidden(false);
    const recordingId = details.playRecordingId || "";
    if (playingRecording !== recordingId || audio.dataset.playUrl !== details.playUrl) {
      audio.pause();
      playingRecording = recordingId;
      playingArtist = details.playArtist || "";
      audio.dataset.playUrl = details.playUrl;
      audio.src = details.playUrl;
      playerTitle.textContent = details.playTitle || "Innspilling";
      playerSubtitle.textContent = playingArtist || "Uavklart artist";
      updatePlayerCover({dataset: details});
      const urlTemplate = player?.dataset.recordingUrlTemplate;
      recordingLinks.forEach(link => {
        if (urlTemplate && /^[\da-f]{8}(-[\da-f]{4}){3}-[\da-f]{12}$/i.test(recordingId)) {
          link.href = urlTemplate.replace("00000000-0000-0000-0000-000000000000", recordingId);
          link.setAttribute("aria-label", `Åpne innspilling: ${details.playTitle || "Innspilling"}`);
        } else link.removeAttribute("href");
      });
      playerToggle.disabled = false;
      playerProgress.disabled = true;
      lastFailedSource = "";
      audio.load();
    }
    try {
      await audio.play();
    } catch {
      playerSubtitle.textContent = "Radiofilen kunne ikke leses eller spilles.";
      updatePlaybackButtons();
      return false;
    }
    updatePlaybackButtons();
    return true;
  };
  document.addEventListener("click", event => {
    const trigger = event.target.closest("[data-play-recording]");
    if (!trigger || trigger.disabled) return;
    event.preventDefault();
    event.stopPropagation();
    const releaseRow = trigger.closest(".track-row");
    if (releaseRow && !trackFromRow(releaseRow).playUrl) {
      notify("Lagre sporkoblingen før avspilling.");
      return;
    }
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
  const syncMute = () => {
    if (!audio || !playerMute) return;
    const muted = audio.muted || audio.volume === 0;
    playerMute.textContent = muted ? "🔇" : "🔊";
    playerMute.setAttribute("aria-label", muted ? "Slå på lyd" : "Demp lyd");
    playerMute.setAttribute("title", muted ? "Slå på lyd" : "Demp lyd");
    playerMute.setAttribute("aria-pressed", String(muted));
  };
  if (audio && playerVolume) {
    const storedVolume = localStorage.getItem("p7-v2-player-volume");
    const savedVolume = storedVolume === null ? 1 : Number(storedVolume);
    audio.volume = Number.isFinite(savedVolume) && savedVolume >= 0 && savedVolume <= 1 ? savedVolume : 1;
    playerVolume.value = String(audio.volume);
    playerVolume.addEventListener("input", () => {
      audio.volume = Number(playerVolume.value);
      if (audio.volume > 0) audio.muted = false;
      localStorage.setItem("p7-v2-player-volume", String(audio.volume));
      syncMute();
    });
    playerMute?.addEventListener("click", () => {
      audio.muted = !audio.muted;
      if (!audio.muted && audio.volume === 0) {
        audio.volume = 1;
        playerVolume.value = "1";
        localStorage.setItem("p7-v2-player-volume", "1");
      }
      syncMute();
    });
    syncMute();
  }
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
  const highlightCurrentLibraryRow = async () => {
    if (queueContext.type !== "MUSIC_LIBRARY" || queueContext.key !== librarySignature()) return;
    const row = [...document.querySelectorAll("[data-library-row]")].find(
      item => trackFromRow(item).playRecordingId === queue[queueIndex]?.playRecordingId
    );
    if (row) await followLibraryRow(row);
  };
  const moveInQueue = async offset => {
    if (queueTransitioning) return;
    queueTransitioning = true;
    let skipped = 0;
    try {
      for (let index = queueIndex + offset; index >= 0 && index < queue.length; index += offset) {
        if (!queue[index].playUrl) { skipped++; continue; }
        queueIndex = index;
        updateQueueControls();
        if (await startPlayback({dataset: queue[index]}, true)) {
          if (skipped) notify(`Hoppet over ${skipped} utilgjengelig${skipped === 1 ? " spor" : "e spor"}.`);
          await highlightCurrentLibraryRow();
          return;
        }
        queue[index].playUrl = "";
        queue[index].playMessage = "Kunne ikke spille radiofilen";
        updateQueueControls();
        skipped++;
      }
      if (skipped) notify(`Ingen flere spillbare spor. ${skipped} ble hoppet over.`);
    } finally {
      queueTransitioning = false;
    }
  };
  const playQueueIndex = async index => {
    if (!queue[index]?.playUrl || queueTransitioning) return;
    queueIndex = index;
    updateQueueControls();
    await startPlayback({dataset: queue[index]}, true);
    await highlightCurrentLibraryRow();
  };
  playerPrevious?.addEventListener("click", () => void moveInQueue(-1));
  playerNext?.addEventListener("click", () => void moveInQueue(1));
  const setQueueOpen = open => {
    if (!queuePanel || !playerQueue) return;
    queuePanel.hidden = !open;
    playerQueue.setAttribute("aria-expanded", String(open));
    if (open) renderQueue();
  };
  playerQueue?.addEventListener("click", () => setQueueOpen(queuePanel.hidden));
  queuePanel?.addEventListener("click", event => {
    if (event.target.closest("[data-player-queue-close]")) {
      setQueueOpen(false);
      playerQueue.focus();
      return;
    }
    const index = event.target.closest("[data-queue-index]")?.dataset.queueIndex;
    if (index !== undefined) void playQueueIndex(Number(index));
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && queuePanel && !queuePanel.hidden) {
      setQueueOpen(false);
      playerQueue.focus();
    }
  });
  audio?.addEventListener("ended", () => {
    updatePlaybackButtons();
    void moveInQueue(1);
  });
  audio?.addEventListener("error", () => {
    playerToggle.textContent = "▶";
    playerSubtitle.textContent = "Radiofilen kunne ikke leses eller spilles.";
    updatePlaybackButtons();
    if (queueTransitioning || audio.dataset.playUrl === lastFailedSource) return;
    lastFailedSource = audio.dataset.playUrl;
    if (queue[queueIndex]?.playUrl === audio.dataset.playUrl) {
      queue[queueIndex].playUrl = "";
      queue[queueIndex].playMessage = "Kunne ikke spille radiofilen";
      updateQueueControls();
    }
    if (queue.slice(queueIndex + 1).some(track => track.playUrl)) {
      notify("Et spor kunne ikke spilles. Prøver neste.");
      void moveInQueue(1);
    }
  });
  document.addEventListener("p7:playback-context-changed", syncPlayerAvailability);
  document.addEventListener("p7:page-changed", () => {
    syncPlayerAvailability();
    updatePlaybackButtons();
  });
  syncPlayerAvailability();

  let pageController;
  const initPage = () => {
  pageController?.abort();
  pageController = new AbortController();
  const pageSignal = pageController.signal;
  const libraryRows = [...document.querySelectorAll("[data-library-row]")];
  const goTo = url => {
    if (window.P7_V2?.navigate) void window.P7_V2.navigate(url);
    else location.assign(url);
  };
  const autoSubmitFilters = document.querySelector("[data-auto-submit-filters]");
  autoSubmitFilters?.addEventListener("change", event => {
    if (!event.target.matches("select, input[type='checkbox'], input[type='radio']")) return;
    autoSubmitFilters.requestSubmit();
  });

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
    goTo(row.dataset.rowHref);
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
  window.addEventListener("resize", alignInspectorWithTable, {signal: pageSignal});
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
  }, {signal: pageSignal});
  inspectorOpeners.forEach(button => button.addEventListener("click", () => setInspector(true)));
  libraryInspectorToggles.forEach(button => button.addEventListener("click", () => {
    const isOpen = !inspector?.hidden && !inspector?.classList.contains("inspector-leaving");
    setInspector(!isOpen);
  }));
  let libraryDetailRequest = 0;
  followLibraryRow = async row => {
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
      if (!response.ok || pageSignal.aborted || requestId !== libraryDetailRequest) return;
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const nextInspector = page.querySelector("#v2-inspector");
      if (!nextInspector || pageSignal.aborted || requestId !== libraryDetailRequest) return;
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
      event.preventDefault(); goTo(row.dataset.detailHref); return;
    }
    if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
    const index = libraryRows.indexOf(row);
    const next = libraryRows[Math.max(0, Math.min(libraryRows.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
    event.preventDefault();
    sessionStorage.setItem(keyboardFocusKey, "true");
    next.focus({preventScroll: true});
    next.scrollIntoView({block: "nearest", inline: "nearest"});
    void followLibraryRow(next);
  }, {signal: pageSignal});
  document.addEventListener("click", event => {
    const button = event.target.closest("[data-tab]");
    if (!button || !inspector?.contains(button)) return;
    document.querySelectorAll("[data-tab]").forEach(item => item.setAttribute("aria-selected", String(item === button)));
    document.querySelectorAll("[data-panel]").forEach(panel => { panel.hidden = panel.dataset.panel !== button.dataset.tab; });
  }, {signal: pageSignal});
  document.addEventListener("keydown", event => {
    if (!event.target.closest("#v2-inspector .tabs")) return;
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const tabs = [...inspector.querySelectorAll("[data-tab]")];
    const current = tabs.indexOf(document.activeElement);
    const next = (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault(); tabs[next].focus(); tabs[next].click();
  }, {signal: pageSignal});
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
  }, {signal: pageSignal});
  document.addEventListener("keydown", event => {
    if (!event.target.closest("#v2-inspector .resize-handle")) return;
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const current = parseInt(getComputedStyle(root).getPropertyValue("--inspector"), 10) || 360;
    const width = Math.max(300, Math.min(560, current + (event.key === "ArrowLeft" ? 16 : -16)));
    root.style.setProperty("--inspector", `${width}px`);
    localStorage.setItem("p7-v2-inspector-width", width);
    event.preventDefault();
  }, {signal: pageSignal});
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
  }, {signal: pageSignal});

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
    recordingFiles.querySelectorAll("[data-select-file]").forEach(button => {
      button.addEventListener("click", () => {
        const row = rows.find(item => item.dataset.fileRow === button.dataset.selectFile);
        selectFile(row, true);
      });
    });
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

  };
  window.P7_V2 ||= {};
  window.P7_V2.initPage = initPage;
  initPage();
})();
