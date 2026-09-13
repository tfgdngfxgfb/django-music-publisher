document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("p7-theme", next);
    });
  }
  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = "Kopiert";
    });
  });
  document.querySelectorAll(".recording-autocomplete").forEach((input) => {
    const hiddenName = input.name.replace("existing_recording_search", "recording");
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
        const response = await fetch(`/arbeid/api/innspillinger/?q=${encodeURIComponent(input.value)}`);
        const data = await response.json();
        data.results.forEach((item) => {
          const option = document.createElement("button");
          option.type = "button";
          option.innerHTML = `<strong></strong><span></span>`;
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
