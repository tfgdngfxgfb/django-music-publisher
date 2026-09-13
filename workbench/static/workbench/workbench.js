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

  document.querySelectorAll(".recording-autocomplete").forEach((input) => {
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
  });
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
