(() => {
  async function browse(section, params) {
    section.browseController?.abort();
    const controller = new AbortController();
    section.browseController = controller;
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
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const replacement = doc.querySelector("[data-file-picker]");
      if (!replacement || replacement.id !== section.id) {
        throw new Error("Filvisningen kunne ikke oppdateres.");
      }
      section.replaceWith(replacement);
    } catch (error) {
      if (error.name === "AbortError") return;
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
        section.removeAttribute("aria-busy");
      }
    }
  }

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-file-picker] form[method='get']");
    if (!form) return;
    event.preventDefault();
    browse(form.closest("[data-file-picker]"), new URLSearchParams(new FormData(form)));
  });

  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-file-picker] [data-picker-folder]");
    if (!link) return;
    event.preventDefault();
    const section = link.closest("[data-file-picker]");
    browse(section, new URL(link.href).searchParams);
  });
})();
