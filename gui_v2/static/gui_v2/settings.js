(() => {
  const form = document.querySelector('[data-personal-settings]');
  if (!form || form.dataset.initialized) return;
  form.dataset.initialized = 'true';
  const theme = form.elements.theme;
  const volume = form.elements.volume;
  const autoNext = form.elements.auto_next;
  const feedback = form.querySelector('[data-settings-feedback]');
  const autoNextKey = `p7-v2-auto-next:${form.dataset.userScope}`;
  const output = form.querySelector('[data-volume-value]');
  theme.value = document.documentElement.dataset.v2Theme || 'dark';
  try {
    const saved = localStorage.getItem('p7-v2-player-volume');
    const numeric = saved === null ? 1 : Number(saved);
    volume.value = Number.isFinite(numeric) && numeric >= 0 && numeric <= 1 ? numeric * 100 : 100;
    autoNext.checked = localStorage.getItem(autoNextKey) !== 'false';
  } catch { /* Defaults remain usable without browser storage. */ }
  const updateVolume = () => { output.value = `${volume.value} %`; };
  volume.addEventListener('input', updateVolume);
  updateVolume();
  form.addEventListener('submit', event => {
    event.preventDefault();
    try {
      localStorage.setItem('p7-v2-theme', theme.value);
      localStorage.setItem('p7-v2-player-volume', String(Number(volume.value) / 100));
      localStorage.setItem(autoNextKey, String(autoNext.checked));
      document.documentElement.dataset.v2Theme = theme.value;
      const playerVolume = document.querySelector('[data-player-volume]');
      if (playerVolume) {
        playerVolume.value = String(Number(volume.value) / 100);
        playerVolume.dispatchEvent(new Event('input', {bubbles: true}));
      }
      feedback.textContent = 'Lagret i denne nettleseren.';
    } catch {
      feedback.textContent = 'Nettleseren tillater ikke lagring av innstillingene.';
    }
  });
})();
