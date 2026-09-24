(() => {
  window.P7_V2 ||= {};
  if (window.P7_V2.initFilePickers) {
    window.P7_V2.initFilePickers();
    return;
  }
  const selections = new Map();
  const pending = new Set();
  const folderKey = (section) => {
    if (section.dataset.folderKey) return section.dataset.folderKey;
    const form = section.querySelector("form[method='get']");
    return JSON.stringify([section.dataset.browseUrl, section.id, form.elements.root_key.value, form.elements.relative_path.value]);
  };
  const syncSelection = (section) => {
    const key = folderKey(section);
    const selected = new Set(selections.get(key) || []);
    const visible = new Set();
    section.querySelectorAll('input[type=checkbox][name=filenames]').forEach(input => {
      visible.add(input.value);
      if (input.checked && !input.disabled) selected.add(input.value);
      else selected.delete(input.value);
    });
    selections.set(key, [...selected]);
    const choices = [...section.querySelectorAll('input[type=checkbox][name=filenames]:not(:disabled)')];
    const selectAll = section.querySelector('[data-picker-select-all]');
    if (selectAll) {
      const checked = choices.filter(input => input.checked).length;
      selectAll.checked = choices.length > 0 && checked === choices.length;
      selectAll.indeterminate = checked > 0 && checked < choices.length;
      selectAll.disabled = choices.length === 0;
    }
    const form = section.querySelector('form[method=post]');
    if (!form) return;
    form.querySelectorAll('[data-picker-kept]').forEach(input => input.remove());
    [...selected].filter(name => !visible.has(name)).forEach(name => {
      const input = document.createElement('input'); input.type = 'hidden'; input.name = 'filenames'; input.value = name; input.dataset.pickerKept = ''; form.append(input);
    });
    let count = form.querySelector('[data-picker-count]');
    if (!count) {count = document.createElement('p'); count.dataset.pickerCount = ''; count.setAttribute('role', 'status'); form.prepend(count);}
    const hiddenCount = [...selected].filter(name => !visible.has(name)).length;
    count.textContent = `${selected.size} ${selected.size === 1 ? 'fil' : 'filer'} valgt i mappen${hiddenCount ? ` (${hiddenCount} utenfor dette søket)` : ''}.`;
    const register = form.querySelector("[data-picker-register]");
    if (register) register.disabled = selected.size === 0;
  };
  async function browse(section, params) {
    clearTimeout(section.searchTimer);
    if (!section.isConnected) return;
    syncSelection(section);
    section.browseController?.abort();
    const controller = new AbortController();
    section.browseController = controller;
    pending.add(section);
    const url = new URL(section.dataset.browseUrl, location.origin);
    url.search = params.toString();
    section.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(url, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("Mappen kunne ikke åpnes.");
      const html = await response.text();
      if (controller.signal.aborted || !section.isConnected) return;
      const doc = new DOMParser().parseFromString(html, "text/html");
      const replacement = doc.querySelector("[data-file-picker]");
      if (!replacement || replacement.id !== section.id) {
        throw new Error("Filvisningen kunne ikke oppdateres.");
      }
      replacement.hidden = section.hidden;
      section.replaceWith(replacement);
      const selected = selections.get(folderKey(replacement)) || [];
      replacement.querySelectorAll("[name=filenames]").forEach(input => {input.checked = !input.disabled && selected.includes(input.value);});
      replacement.dataset.folderKey = folderKey(replacement);
      syncSelection(replacement);
      if (!replacement.hidden && params.has("file_q")) {
        const search = replacement.querySelector("[data-picker-search]");
        search?.focus({preventScroll: true});
        search?.setSelectionRange(search.value.length, search.value.length);
      }
    } catch (error) {
      if (error.name === "AbortError" || controller.signal.aborted || !section.isConnected) return;
      let notice = section.querySelector("[data-picker-error]");
      if (!notice) {
        notice = document.createElement("p");
        notice.className = "notice error";
        notice.dataset.pickerError = "";
        notice.setAttribute("role", "alert");
        section.append(notice);
      }
      notice.textContent = error.message;
    } finally {
      if (section.browseController === controller) {
        pending.delete(section);
        section.removeAttribute("aria-busy");
      }
    }
  }

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-file-picker] form[method='get']");
    if (!form) return;
    event.preventDefault();
    const params = new URLSearchParams(new FormData(form));
    if (event.submitter?.name) params.set(event.submitter.name, event.submitter.value);
    browse(form.closest("[data-file-picker]"), params);
  }, {capture: true});

  document.addEventListener("input", (event) => {
    if (!event.target.matches("[data-file-picker] [data-picker-search]")) return;
    const form = event.target.form;
    const section = form.closest("[data-file-picker]");
    clearTimeout(section.searchTimer);
    section.browseController?.abort();
    const params = new URLSearchParams(new FormData(form));
    section.searchTimer = setTimeout(() => browse(section, params), 250);
    pending.add(section);
  });

  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest("[data-file-picker] [data-picker-folder]");
    if (!link) return;
    event.preventDefault();
    const section = link.closest("[data-file-picker]");
    browse(section, new URL(link.href).searchParams);
  }, {capture: true});
  document.addEventListener("change", (event) => {
    const section = event.target.closest("[data-file-picker]");
    if (!section) return;
    if (event.target.matches("[data-picker-select-all]")) {
      section.querySelectorAll('input[type=checkbox][name=filenames]:not(:disabled)').forEach(input => {input.checked = event.target.checked;});
    }
    if (event.target.name === "root_key") {
      clearTimeout(section.searchTimer);
      const params = new URLSearchParams(new FormData(event.target.form));
      params.set("relative_path", "."); params.set("suggest", "1");
      browse(section, params);
    }
    else syncSelection(section);
  });
  const loadVisible = () => document.querySelectorAll('[data-picker-auto="true"]').forEach(section => {
    if (section.hidden) return;
    section.dataset.pickerAuto = "false";
    section.dataset.folderKey = folderKey(section);
    const params = new URLSearchParams(new FormData(section.querySelector("form[method='get']")));
    params.set("suggest", "1");
    browse(section, params);
  });
  document.addEventListener("digitization:step", loadVisible);
  const init = () => {
    for (const section of pending) {
      if (section.isConnected) continue;
      clearTimeout(section.searchTimer);
      section.browseController?.abort();
      pending.delete(section);
    }
    // Keep selection only in the current workspace, never in another batch.
    const urls = new Set([...document.querySelectorAll('[data-file-picker]')].map(section => section.dataset.browseUrl));
    for (const key of selections.keys()) {
      if (!urls.has(JSON.parse(key)[0])) selections.delete(key);
    }
    loadVisible();
    document.querySelectorAll('[data-file-picker]:not([data-picker-auto="true"])').forEach(syncSelection);
  };
  window.P7_V2.initFilePickers = init;
  document.addEventListener('p7:page-changed', init);
  init();
})();
