(() => {
  let requestNumber = 0;
  let pendingRequest;
  let loadingTimer;
  const loadingStatus = document.createElement("span");
  loadingStatus.className = "navigation-loading";
  loadingStatus.setAttribute("role", "status");
  loadingStatus.setAttribute("aria-live", "polite");
  loadingStatus.hidden = true;
  document.body.append(loadingStatus);

  const eligible = value => {
    const url = new URL(value, location.href);
    return url.origin === location.origin && ["http:", "https:"].includes(url.protocol);
  };

  const runPageScripts = async main => {
    for (const script of main.querySelectorAll("script[src]")) {
      if (!eligible(script.src)) continue;
      const replacement = document.createElement("script");
      replacement.src = script.src;
      await new Promise(resolve => {
        replacement.onload = resolve;
        replacement.onerror = resolve;
        script.replaceWith(replacement);
      });
    }
  };

  const navigate = async (value, options = {}) => {
    const url = new URL(value, location.href);
    if (!eligible(url.href)) {
      location.assign(url.href);
      return;
    }
    if (url.pathname === location.pathname && url.search === location.search && url.hash) {
      location.assign(url.href);
      return;
    }
    const currentRequest = ++requestNumber;
    const startedAt = performance.now();
    pendingRequest?.abort();
    pendingRequest = new AbortController();
    clearTimeout(loadingTimer);
    loadingStatus.textContent = `Åpner ${options.label || "siden"} …`;
    loadingTimer = setTimeout(() => { loadingStatus.hidden = false; }, 180);
    document.querySelector("#v2-main")?.setAttribute("aria-busy", "true");
    let contentReplaced = false;
    try {
      const response = await fetch(url.href, {
        credentials: "same-origin",
        headers: {Accept: "text/html"},
        signal: pendingRequest.signal,
      });
      if (currentRequest !== requestNumber) return;
      if (!response.ok || !response.headers.get("content-type")?.includes("text/html")) {
        location.assign(url.href);
        return;
      }
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const main = page.querySelector("#v2-main");
      const header = page.querySelector(".app-header");
      const strip = page.querySelector(".prototype-strip");
      if (page.body.dataset.p7Shell !== "gui-v2" || !main || !header || !strip) {
        location.assign(url.href);
        return;
      }
      const leaving = new Event("p7:before-navigation", {cancelable: true});
      if (!document.dispatchEvent(leaving)) return;
      if (!options.popstate) {
        history.replaceState({...history.state, p7Scroll: scrollY}, "", location.href);
      }
      const wasHidden = document.body.classList.contains("player-hidden");
      document.querySelector(".app-header").replaceWith(header);
      document.querySelector(".prototype-strip").replaceWith(strip);
      document.querySelector("#v2-main").replaceWith(main);
      contentReplaced = true;
      document.querySelector(".messages")?.remove();
      const messages = page.querySelector(".messages");
      if (messages) main.before(messages);
      document.body.className = page.body.className;
      if (wasHidden) document.body.classList.add("player-hidden");
      document.title = page.title;
      if (!options.popstate) history.pushState({p7Scroll: 0}, "", response.url);
      window.P7_V2.initPage();
      window.P7_V2.initGrid();
      await runPageScripts(main);
      document.dispatchEvent(new Event("p7:page-changed"));
      scrollTo(0, options.popstate ? options.scroll || 0 : 0);
      main.focus({preventScroll: true});
      if (!options.autoReduced) {
        const reducedUrl = window.P7_V2.reduceSlowLibraryPage?.(performance.now() - startedAt);
        if (reducedUrl) void navigate(reducedUrl, {autoReduced: true, label: "færre rader"});
      }
    } catch (error) {
      if (currentRequest !== requestNumber) return;
      if (!contentReplaced) location.assign(url.href);
      else console.error("Kunne ikke fullføre sideoppdateringen.", error);
    } finally {
      if (currentRequest === requestNumber) {
        clearTimeout(loadingTimer);
        loadingStatus.hidden = true;
        document.querySelector("#v2-main")?.removeAttribute("aria-busy");
        pendingRequest = null;
      }
    }
  };

  window.P7_V2 ||= {};
  window.P7_V2.navigate = navigate;

  document.addEventListener("click", event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest("a[href]");
    if (!link || link.target || link.hasAttribute("download") || link.dataset.noP7Nav !== undefined) return;
    if (!eligible(link.href)) return;
    const url = new URL(link.href);
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    event.preventDefault();
    void navigate(url.href, {label: link.textContent.trim() || "siden"});
  });

  document.addEventListener("submit", event => {
    const form = event.target;
    if (event.defaultPrevented || form.method.toLowerCase() !== "get" || form.target || form.dataset.noP7Nav !== undefined) return;
    const url = new URL(form.action || location.href, location.href);
    if (!eligible(url.href)) return;
    const fields = event.submitter ? new FormData(form, event.submitter) : new FormData(form);
    url.search = new URLSearchParams(fields).toString();
    event.preventDefault();
    void navigate(url.href);
  });

  addEventListener("popstate", event => {
    void navigate(location.href, {popstate: true, scroll: event.state?.p7Scroll || 0});
  });
  addEventListener("load", () => {
    const timing = performance.getEntriesByType("navigation")[0];
    const reducedUrl = window.P7_V2.reduceSlowLibraryPage?.(timing?.responseEnd || 0);
    if (reducedUrl) void navigate(reducedUrl, {autoReduced: true, label: "færre rader"});
  }, {once: true});
})();
