(() => {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/service-worker.js', {scope: '/'}).catch(() => {}));
  }

  let installPrompt = null;
  const installButtons = () => [document.getElementById('install-app'), document.getElementById('install-app-top')].filter(Boolean);
  const setInstallVisible = (visible) => installButtons().forEach((b) => { b.hidden = !visible; });
  const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    installPrompt = event;
    if (!isStandalone()) setInstallVisible(true);
  });

  window.addEventListener('load', () => {
    if (!isStandalone()) setInstallVisible(true);
  });

  installButtons().forEach((button) => button.addEventListener('click', async () => {
    if (installPrompt) {
      installPrompt.prompt();
      await installPrompt.userChoice;
      installPrompt = null;
      setInstallVisible(false);
      return;
    }
    window.alert('Use your browser menu and choose Install app, Add to Dock, or Add to Home Screen.');
  }));

  window.addEventListener('appinstalled', () => {
    installPrompt = null;
    setInstallVisible(false);
  });

  const updateSelectTone = (select) => {
    if (!select) return;
    if (select.name === 'status') {
      [...select.classList].filter((c) => c.startsWith('status-value-')).forEach((c) => select.classList.remove(c));
      select.classList.add(`status-value-${select.value}`);
    }
    if (select.name === 'priority') {
      [...select.classList].filter((c) => c.startsWith('priority-value-')).forEach((c) => select.classList.remove(c));
      select.classList.add(`priority-value-${select.value}`);
    }
  };

  const maybeRemoveTicketFromFilteredView = (form) => {
    const view = form.dataset.view || '';
    const status = form.querySelector('[name="status"]')?.value || '';
    const assignee = form.querySelector('[name="assigned_to"]')?.value || '';
    const currentUser = form.dataset.currentUser || '';
    let shouldRemove = false;
    if (view === 'active' && ['resolved', 'closed'].includes(status)) shouldRemove = true;
    if (view === 'completed' && !['resolved', 'closed'].includes(status)) shouldRemove = true;
    if (view === 'mine' && assignee !== currentUser) shouldRemove = true;
    if (!shouldRemove) return;
    const group = form.closest('[data-ticket-group]');
    if (!group) return;
    group.classList.add('ticket-leaving');
    window.setTimeout(() => {
      group.remove();
      const count = document.getElementById('ticket-count');
      if (count) count.textContent = String(Math.max(0, Number(count.textContent || 0) - 1));
    }, 420);
  };

  const saveQuickForm = async (form, changedControl) => {
    if (!form || form.dataset.saving === '1') return;
    form.dataset.saving = '1';
    const state = form.querySelector('[data-save-state]');
    if (state) {
      state.textContent = 'Saving…';
      state.className = 'quick-save-state saving';
    }
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: {'X-Requested-With': 'XMLHttpRequest'},
        credentials: 'same-origin'
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      updateSelectTone(changedControl);
      if (state) {
        state.textContent = 'Saved';
        state.className = 'quick-save-state saved';
      }
      window.setTimeout(() => {
        if (state && state.textContent === 'Saved') state.textContent = '';
      }, 1800);
      maybeRemoveTicketFromFilteredView(form);
    } catch (error) {
      if (state) {
        state.textContent = 'Not saved';
        state.className = 'quick-save-state error';
      }
    } finally {
      form.dataset.saving = '0';
    }
  };

  document.querySelectorAll('[data-quick-control]').forEach((control) => {
    control.addEventListener('change', () => {
      const formId = control.getAttribute('form');
      const form = formId ? document.getElementById(formId) : control.closest('form');
      saveQuickForm(form, control);
    });
  });

  const setTicketExpanded = (ticketId, open) => {
    const button = document.querySelector(`[data-ticket-toggle="${ticketId}"]`);
    const row = document.getElementById(`ticket-detail-${ticketId}`);
    if (!button || !row) return;
    row.hidden = !open;
    button.setAttribute('aria-expanded', open ? 'true' : 'false');
    button.textContent = open ? 'Hide' : 'View';
    const group = button.closest('[data-ticket-group]');
    if (group) group.classList.toggle('is-open', open);
  };

  document.querySelectorAll('[data-ticket-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
      const ticketId = button.dataset.ticketToggle;
      const row = document.getElementById(`ticket-detail-${ticketId}`);
      setTicketExpanded(ticketId, !!row?.hidden);
    });
  });

  window.addEventListener('load', () => {
    const match = window.location.hash.match(/^#ticket-(\d+)$/);
    if (match) {
      setTicketExpanded(match[1], true);
      const group = document.querySelector(`[data-ticket-group="${match[1]}"]`);
      if (group) window.setTimeout(() => group.scrollIntoView({behavior: 'smooth', block: 'center'}), 120);
    }
  });

  const dropzoneTransfers = new WeakMap();
  const allowedFilesFromClipboard = (clipboardData) => {
    const files = [];
    if (!clipboardData) return files;
    for (const item of clipboardData.items || []) {
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
    return files.length ? files : [...(clipboardData.files || [])];
  };

  const setupDropzone = (zone) => {
    const input = zone.querySelector('.dropzone-input');
    const preview = zone.querySelector('[data-dropzone-preview]');
    if (!input || !preview || typeof DataTransfer === 'undefined') return;
    let transfer = new DataTransfer();
    dropzoneTransfers.set(zone, transfer);

    const render = () => {
      preview.innerHTML = '';
      [...transfer.files].forEach((file, index) => {
        const item = document.createElement('div');
        item.className = 'dropzone-file';
        if (file.type.startsWith('image/')) {
          const img = document.createElement('img');
          const url = URL.createObjectURL(file);
          img.src = url;
          img.alt = '';
          img.onload = () => URL.revokeObjectURL(url);
          item.appendChild(img);
        } else {
          const icon = document.createElement('span');
          icon.className = 'file-chip-icon';
          icon.textContent = 'FILE';
          item.appendChild(icon);
        }
        const label = document.createElement('span');
        label.className = 'dropzone-file-name';
        label.textContent = file.name || 'Pasted image';
        item.appendChild(label);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'dropzone-remove';
        remove.setAttribute('aria-label', `Remove ${file.name || 'file'}`);
        remove.textContent = '×';
        remove.addEventListener('click', (event) => {
          event.stopPropagation();
          const next = new DataTransfer();
          [...transfer.files].forEach((existing, i) => { if (i !== index) next.items.add(existing); });
          transfer = next;
          dropzoneTransfers.set(zone, transfer);
          input.files = transfer.files;
          render();
        });
        item.appendChild(remove);
        preview.appendChild(item);
      });
      zone.classList.toggle('has-files', transfer.files.length > 0);
    };

    const addFiles = (files) => {
      const existing = new Set([...transfer.files].map((f) => `${f.name}-${f.size}-${f.lastModified}`));
      [...files].forEach((file) => {
        const key = `${file.name}-${file.size}-${file.lastModified}`;
        if (!existing.has(key)) {
          transfer.items.add(file);
          existing.add(key);
        }
      });
      input.files = transfer.files;
      render();
    };
    zone._addFiles = addFiles;

    zone.addEventListener('click', (event) => {
      if (event.target.closest('.dropzone-remove')) return;
      input.click();
    });
    zone.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        input.click();
      }
    });
    input.addEventListener('click', (event) => event.stopPropagation());
    input.addEventListener('change', () => {
      const selected = [...input.files];
      addFiles(selected);
    });
    ['dragenter', 'dragover'].forEach((name) => zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add('is-dragging');
    }));
    ['dragleave', 'drop'].forEach((name) => zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.remove('is-dragging');
    }));
    zone.addEventListener('drop', (event) => addFiles(event.dataTransfer?.files || []));
    zone.addEventListener('paste', (event) => {
      const files = allowedFilesFromClipboard(event.clipboardData);
      if (files.length) {
        event.preventDefault();
        addFiles(files);
      }
    });
  };

  document.querySelectorAll('[data-dropzone]').forEach(setupDropzone);

  document.addEventListener('paste', (event) => {
    const activeTag = document.activeElement?.tagName?.toLowerCase();
    if (['input', 'textarea'].includes(activeTag)) return;
    const files = allowedFilesFromClipboard(event.clipboardData);
    if (!files.length) return;
    let zone = document.activeElement?.closest?.('[data-dropzone]');
    if (!zone) zone = document.querySelector('.large-dropzone');
    if (!zone) zone = document.querySelector('.ticket-detail-row:not([hidden]) [data-dropzone]');
    if (zone && typeof zone._addFiles === 'function') {
      event.preventDefault();
      zone._addFiles(files);
      zone.focus();
    }
  });


  const themeMetaColors = {pink: '#a66d78', white: '#ffffff', black: '#0d0d0f'};
  const applyTheme = (theme) => {
    if (!['pink', 'white', 'black'].includes(theme)) return;
    document.body.dataset.theme = theme;
    const meta = document.getElementById('theme-color-meta');
    if (meta) meta.setAttribute('content', themeMetaColors[theme]);
    document.querySelectorAll('[data-theme-picker]').forEach((picker) => { picker.value = theme; });
    document.querySelectorAll('[data-theme-card]').forEach((card) => card.classList.toggle('selected', card.dataset.themeCard === theme));
  };

  const saveTheme = async (theme) => {
    const csrf = document.body.dataset.csrf || '';
    applyTheme(theme);
    const data = new FormData();
    data.append('theme', theme);
    data.append('csrf_token', csrf);
    try {
      const response = await fetch('/account/theme', {
        method: 'POST', body: data, credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch (error) {
      window.alert('Theme could not be saved. Refresh the page and try again.');
    }
  };

  document.querySelectorAll('[data-theme-picker]').forEach((picker) => {
    picker.addEventListener('change', () => saveTheme(picker.value));
  });
  document.querySelectorAll('[data-theme-card]').forEach((card) => {
    card.addEventListener('click', () => saveTheme(card.dataset.themeCard));
  });
  applyTheme(document.body.dataset.theme || 'pink');

  document.querySelectorAll('[data-print-report]').forEach((button) => {
    button.addEventListener('click', () => window.print());
  });
})();
