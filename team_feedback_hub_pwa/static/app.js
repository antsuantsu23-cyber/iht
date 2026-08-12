(() => {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/service-worker.js', {scope: '/'}).catch(() => {}));
  }

  let installPrompt = null;
  const buttons = () => [document.getElementById('install-app'), document.getElementById('install-app-top')].filter(Boolean);
  const setVisible = (visible) => buttons().forEach((b) => { b.hidden = !visible; });
  const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    installPrompt = event;
    if (!isStandalone()) setVisible(true);
  });

  window.addEventListener('load', () => {
    if (!isStandalone()) setVisible(true);
  });

  buttons().forEach((button) => button.addEventListener('click', async () => {
    if (installPrompt) {
      installPrompt.prompt();
      await installPrompt.userChoice;
      installPrompt = null;
      setVisible(false);
      return;
    }
    window.alert('Use your browser menu and choose Install app, Add to Dock, or Add to Home Screen.');
  }));

  window.addEventListener('appinstalled', () => {
    installPrompt = null;
    setVisible(false);
  });
})();
