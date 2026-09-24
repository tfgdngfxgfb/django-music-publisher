(() => {
  // Browser privacy settings and exhausted quotas must not disable the UI.
  const storage = name => ({
    getItem(key) { try { return window[name].getItem(key); } catch { return null; } },
    setItem(key, value) { try { window[name].setItem(key, value); return true; } catch { return false; } },
    removeItem(key) { try { window[name].removeItem(key); return true; } catch { return false; } },
  });
  window.P7_V2 ||= {};
  window.P7_V2.storage = {local: storage('localStorage'), session: storage('sessionStorage')};
})();
