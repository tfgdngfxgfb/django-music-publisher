(() => {
  const button = document.querySelector("#v2-theme-toggle");
  if (!button) return;
  button.addEventListener("click", () => {
    const next = document.documentElement.dataset.v2Theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.v2Theme = next;
    localStorage.setItem("p7-v2-theme", next);
  });
})();
