document.addEventListener("DOMContentLoaded", () => {
  const body = document.body;
  const navToggle = document.getElementById("nav-toggle");
  const navClose = document.querySelector("[data-nav-close]");
  const sideNav = document.getElementById("app-navigation");

  const setNavigation = (open) => {
    body.classList.toggle("nav-open", open);
    navToggle?.setAttribute("aria-expanded", String(open));
  };

  navToggle?.addEventListener("click", () => {
    setNavigation(!body.classList.contains("nav-open"));
  });
  navClose?.addEventListener("click", () => setNavigation(false));
  sideNav?.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => setNavigation(false));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setNavigation(false);
  });

  const toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const next =
        document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("p7-theme", next);
    });
  }

  const globalSearch = document.getElementById("global-search");
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      if (!globalSearch) return;
      event.preventDefault();
      globalSearch.focus();
      globalSearch.select();
    }
  });

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.dataset.copy);
      const original = button.textContent;
      button.textContent = "Kopiert";
      window.setTimeout(() => { button.textContent = original; }, 1400);
    });
  });

  const helpTips = [...document.querySelectorAll(".help-tip")];
  const closeHelpTips = (except = null) => {
    helpTips.forEach((tip) => {
      if (tip === except) return;
      tip.classList.remove("is-open");
      tip.querySelector(".help-trigger")?.setAttribute("aria-expanded", "false");
    });
  };
  helpTips.forEach((tip) => {
    const trigger = tip.querySelector(".help-trigger");
    trigger?.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = !tip.classList.contains("is-open");
      closeHelpTips(tip);
      tip.classList.toggle("is-open", open);
      trigger.setAttribute("aria-expanded", String(open));
    });
  });
  document.addEventListener("click", () => closeHelpTips());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeHelpTips();
  });

  const initRecordingAutocomplete = (input) => {
    if (input.dataset.autocompleteReady === "true") return;
    input.dataset.autocompleteReady = "true";
    const hiddenName = input.name.replace(
      "existing_recording_search",
      "recording",
    );
    const hidden = document.querySelector(`[name="${hiddenName}"]`);
    const results = document.createElement("div");
    results.className = "autocomplete-results";
    input.parentElement.appendChild(results);
    let timer;

    input.addEventListener("input", () => {
      if (hidden) hidden.value = "";
      clearTimeout(timer);
      results.replaceChildren();
      if (input.value.trim().length < 2) return;

      timer = setTimeout(async () => {
        const response = await fetch(
          `/arbeid/api/innspillinger/?q=${encodeURIComponent(input.value)}`,
        );
        const data = await response.json();
        data.results.forEach((item) => {
          const option = document.createElement("button");
          option.type = "button";
          option.innerHTML = "<strong></strong><span></span>";
          option.querySelector("strong").textContent = item.text;
          option.querySelector("span").textContent = item.meta || item.id;
          option.addEventListener("click", () => {
            hidden.value = item.id;
            input.value = item.text;
            results.replaceChildren();
          });
          results.appendChild(option);
        });
      }, 180);
    });
  };
  document.querySelectorAll(".recording-autocomplete").forEach(
    initRecordingAutocomplete,
  );

  const trackEntry = document.querySelector("[data-track-entry]");
  if (trackEntry) {
    const rowsContainer = trackEntry.querySelector("[data-track-rows]");
    const totalForms = trackEntry.querySelector('[name$="-TOTAL_FORMS"]');
    const maxForms = Number(
      trackEntry.querySelector('[name$="-MAX_NUM_FORMS"]')?.value || 25,
    );
    const rowTemplate = trackEntry.querySelector("[data-track-empty-row]");
    const firstSequence = Number(trackEntry.dataset.nextSequence || 1);

    const rows = () => [...rowsContainer.querySelectorAll("[data-track-row]")];
    const updateRowNumbers = () => {
      rows().forEach((row, index) => {
        const number = row.querySelector("[data-track-row-number]");
        if (number) number.textContent = String(index + 1);
      });
    };
    const setSequence = (row) => {
      const sequence = row.querySelector('[data-track-field="sequence_number"]');
      if (sequence && !sequence.value) {
        sequence.value = String(firstSequence + rows().indexOf(row));
      }
    };
    const addRow = () => {
      const index = Number(totalForms.value);
      if (index >= maxForms) return null;
      const markup = rowTemplate.innerHTML
        .replaceAll("__prefix__", String(index))
        .replaceAll("__row_number__", String(index + 1));
      rowsContainer.insertAdjacentHTML("beforeend", markup);
      totalForms.value = String(index + 1);
      const row = rows().at(-1);
      row.querySelectorAll(".recording-autocomplete").forEach(
        initRecordingAutocomplete,
      );
      updateRowNumbers();
      return row;
    };
    const ensureRows = (count) => {
      while (rows().length < count && rows().length < maxForms) addRow();
      return rows();
    };

    trackEntry.querySelector("[data-add-track-row]")?.addEventListener(
      "click",
      () => {
        const row = addRow();
        row?.querySelector('[data-track-field="disc_number"]')?.focus();
      },
    );

    rowsContainer.addEventListener("input", (event) => {
      const field = event.target.closest("[data-track-field]");
      if (field?.value.trim()) setSequence(field.closest("[data-track-row]"));
    });
    rowsContainer.addEventListener("change", (event) => {
      const field = event.target.closest("[data-track-field]");
      if (field?.value) setSequence(field.closest("[data-track-row]"));
    });
    rowsContainer.addEventListener("keydown", (event) => {
      const field = event.target.closest("[data-track-field]");
      if (!field || event.key !== "Enter" || event.ctrlKey || event.metaKey) return;
      event.preventDefault();
      const currentRow = field.closest("[data-track-row]");
      const fieldName = field.dataset.trackField;
      const allRows = rows();
      const direction = event.shiftKey ? -1 : 1;
      let targetRow = allRows[allRows.indexOf(currentRow) + direction];
      if (!targetRow && direction > 0) targetRow = addRow();
      targetRow?.querySelector(`[data-track-field="${fieldName}"]`)?.focus();
    });
    rowsContainer.addEventListener("click", (event) => {
      const button = event.target.closest("[data-use-recording]");
      if (!button) return;
      const row = button.closest("[data-track-row]");
      row.querySelector(".recording-id").value = button.dataset.useRecording;
      row.querySelector(".recording-autocomplete").value =
        button.dataset.recordingTitle;
      row.querySelector('[data-track-field="new_recording_title"]').value = "";
      const forceCreate = row.querySelector('[name$="-force_create"]');
      if (forceCreate) forceCreate.checked = false;
      row.querySelector(".track-candidates")?.remove();
      setSequence(row);
    });

    const pasteArea = trackEntry.querySelector("[data-track-paste]");
    trackEntry.querySelector("[data-apply-track-paste]")?.addEventListener(
      "click",
      () => {
        let pastedRows = pasteArea.value
          .split(/\r?\n/)
          .map((line) => line.split("\t").map((cell) => cell.trim()))
          .filter((cells) => cells.some(Boolean));
        if (!pastedRows.length) return;
        const headerTerms = pastedRows[0]
          .slice(0, 7)
          .map((cell) => cell.toLocaleLowerCase("nb"))
          .filter((cell) =>
            /^(plate|disc|side|spor|spornummer|track|sportittel|artist|varighet|isrc)$/.test(
              cell,
            ),
          );
        if (headerTerms.length >= 2) pastedRows = pastedRows.slice(1);
        pastedRows = pastedRows.slice(0, maxForms);
        const availableRows = ensureRows(pastedRows.length);
        pastedRows.forEach((cells, index) => {
          const row = availableRows[index];
          const values = {
            disc_number: cells[0] || "",
            side: cells[1] || "",
            track_number: cells[2] || "",
            title_override: cells[3] || "",
            duration_display: cells[5] || "",
            new_isrc: cells[6] || "",
          };
          Object.entries(values).forEach(([name, value]) => {
            const field = row.querySelector(`[data-track-field="${name}"]`);
            if (field) field.value = value;
          });
          const recordingId = row.querySelector(".recording-id");
          const newTitle = row.querySelector(
            '[data-track-field="new_recording_title"]',
          );
          if (!recordingId.value && !newTitle.value) newTitle.value = cells[3] || "";

          const artist = row.querySelector('[data-track-field="artist_identity"]');
          const warning = row.querySelector("[data-track-paste-warning]");
          const artistName = cells[4] || "";
          const matchingOption = [...artist.options].find(
            (option) =>
              option.text.trim().toLocaleLowerCase("nb") ===
              artistName.toLocaleLowerCase("nb"),
          );
          artist.value = matchingOption?.value || "";
          warning.hidden = !artistName || Boolean(matchingOption);
          warning.textContent = warning.hidden
            ? ""
            : `Fant ikke artistidentiteten «${artistName}». Velg eller opprett den før lagring.`;
          setSequence(row);
        });
        availableRows[0]?.querySelector('[data-track-field="disc_number"]')?.focus();
      },
    );

    let isSubmitting = false;
    trackEntry.addEventListener("submit", (event) => {
      const unresolvedArtist = [...trackEntry.querySelectorAll(
        "[data-track-paste-warning]",
      )].find((warning) => !warning.hidden);
      if (unresolvedArtist) {
        event.preventDefault();
        if (!unresolvedArtist.textContent.includes("Sporlisten er ikke lagret")) {
          unresolvedArtist.textContent =
            `${unresolvedArtist.textContent} Sporlisten er ikke lagret.`;
        }
        unresolvedArtist
          .closest("[data-track-row]")
          ?.querySelector('[data-track-field="artist_identity"]')
          ?.focus();
        return;
      }
      if (isSubmitting) {
        event.preventDefault();
        return;
      }
      isSubmitting = true;
      const submit = trackEntry.querySelector("[data-track-submit]");
      submit.disabled = true;
      submit.textContent = "Lagrer sporlisten …";
      trackEntry.setAttribute("aria-busy", "true");
    });
  }
});

document.querySelectorAll(".catalogue-cover").forEach((image) => {
  image.addEventListener("error", () => image.remove());
  if (image.complete && !image.naturalWidth) image.remove();
});
const inspectorLinks = document.querySelectorAll(".inspector-tabs a");
function selectInspector(link) {
  inspectorLinks.forEach((item) => {
    const active = item === link;
    item.setAttribute("aria-current", String(active));
    document.querySelector(item.getAttribute("href")).hidden = !active;
  });
}
inspectorLinks.forEach((link) => link.addEventListener("click", (event) => {
  event.preventDefault();
  selectInspector(link);
}));
if (inspectorLinks.length) selectInspector(inspectorLinks[0]);
