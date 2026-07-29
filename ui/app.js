const bootstrap = window.__SCRATCHLINK_BOOTSTRAP__ || {};
const STATE_REFRESH_MS = 1500;
const SCREEN_REFRESH_MS = 200;

const elements = {
  heroStatus: document.getElementById('hero-status'),
  connectionGrid: document.getElementById('connection-grid'),
  toast: document.getElementById('toast'),
  newConnectionButton: document.getElementById('new-connection-button'),
  menuOverlay: document.getElementById('menu-overlay'),
  menuTitle: document.getElementById('menu-title'),
  menuId: document.getElementById('menu-id'),
  menuCloseButton: document.getElementById('menu-close-button'),
  menuToggleButton: document.getElementById('menu-toggle-button'),
  menuRenameButton: document.getElementById('menu-rename-button'),
  menuTokenButton: document.getElementById('menu-token-button'),
  menuViewScreenButton: document.getElementById('menu-view-screen-button'),
  menuCopyExtensionButton: document.getElementById('menu-copy-extension-button'),
  menuCopyLinkButton: document.getElementById('menu-copy-link-button'),
  menuDeleteButton: document.getElementById('menu-delete-button'),
  screenOverlay: document.getElementById('screen-overlay'),
  screenCloseButton: document.getElementById('screen-close-button'),
  screenTitle: document.getElementById('screen-title'),
  screenStage: document.getElementById('screen-stage'),
  screenCanvas: document.getElementById('screen-canvas'),
  screenObjects: document.getElementById('screen-objects')
};

let state = {
  connections: [],
  menuConnectionId: null,
  screenConnectionId: null
};
const previousAnalyticsValues = new Map();

function adminHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-ScratchLink-Admin': bootstrap.adminToken || ''
  };
}

async function api(path, options = {}) {
  const separator = path.includes('?') ? '&' : '?';
  const url = `${path}${separator}token=${encodeURIComponent(bootstrap.adminToken || '')}`;
  const response = await fetch(url, {
    method: options.method || 'GET',
    headers: {
      ...adminHeaders(),
      ...(options.headers || {})
    },
    body: options.body ? JSON.stringify(options.body) : undefined
  });

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch (_error) {
      const text = await response.text();
      message = text || message;
    }
    throw new Error(message);
  }

  return response.json();
}

function shortenId(value) {
  if (!value || value.length <= 18) {
    return value || '';
  }
  return `${value.slice(0, 8)}...${value.slice(-6)}`;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    elements.toast.classList.remove('show');
  }, 2400);
}

async function copyText(value, successMessage) {
  await navigator.clipboard.writeText(value);
  showToast(successMessage);
}

function getMenuConnection() {
  return state.connections.find((item) => item.id === state.menuConnectionId) || null;
}

function renderGrid() {
  elements.connectionGrid.innerHTML = '';
  elements.heroStatus.textContent = 'Cloudflare Tunnel is live';

  if (!state.connections.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.textContent = 'No connections yet. Create one to get started.';
    elements.connectionGrid.append(empty);
    return;
  }

  state.connections.forEach((connection) => {
    const card = document.createElement('article');
    card.className = `connection-card ${connection.enabled ? 'enabled' : 'disabled'}`;
    card.innerHTML = `
      <div class="connection-card-inner">
        <div class="connection-top">
          <span class="status-pill">${connection.enabled ? 'Active' : 'Inactive'}</span>
        </div>
        <h3 class="connection-name"></h3>
        <p class="connection-id"></p>
        <div class="connection-active"></div>
      </div>
    `;

    card.querySelector('.connection-name').textContent = connection.name;
    card.querySelector('.connection-id').textContent = shortenId(connection.id);
    card.querySelector('.connection-active').textContent = `Status: ${connection.enabled ? 'Active' : 'Inactive'}`;
    card.addEventListener('click', () => openMenu(connection.id));

    elements.connectionGrid.append(card);
  });
}

function openMenu(connectionId) {
  state.menuConnectionId = connectionId;
  const connection = getMenuConnection();
  if (!connection) {
    return;
  }
  elements.menuTitle.textContent = connection.name;
  elements.menuId.textContent = connection.id;
  elements.menuToggleButton.textContent = connection.enabled ? 'Turn Off' : 'Turn On';
  elements.menuTokenButton.textContent = connection.hasOpenRouterKey ? 'Edit OpenRouter Key' : 'Add OpenRouter Key';
  elements.menuOverlay.classList.remove('hidden');
}

function closeMenu() {
  state.menuConnectionId = null;
  elements.menuOverlay.classList.add('hidden');
}

function openScreenViewer() {
  const connection = getMenuConnection();
  if (!connection) {
    showToast('Choose a connection first.');
    return;
  }
  state.screenConnectionId = connection.id;
  elements.screenTitle.textContent = `${connection.name} Screen`;
  elements.screenOverlay.classList.remove('hidden');
  refreshScreenState().catch(handleError);
}

function closeScreenViewer() {
  state.screenConnectionId = null;
  elements.screenOverlay.classList.add('hidden');
}

async function refreshScreenState() {
  if (!state.screenConnectionId) {
    return;
  }
  const screen = await api(`/admin/screen/${state.screenConnectionId}`);
  renderScreen(screen);
}

function renderScreen(screen) {
  const mode = screen.mode || 'objects';
  if (mode === 'pixels') {
    renderPixelScreen(screen);
  } else if (mode === 'analytics') {
    renderAnalyticsScreen(screen);
  } else {
    renderObjectScreen(screen);
  }
}

function renderObjectScreen(screen) {
  elements.screenStage.classList.add('objects-mode');
  elements.screenCanvas.classList.add('hidden');
  elements.screenObjects.className = 'screen-objects';
  elements.screenObjects.innerHTML = '';
  elements.screenObjects.style.width = '640px';
  elements.screenObjects.style.height = '420px';
  elements.screenStage.style.width = '640px';
  elements.screenStage.style.height = '420px';

  (screen.objects || []).forEach((item) => {
    let node;
    if (item.kind === 'button') {
      node = document.createElement('button');
      node.className = 'screen-widget button';
      node.textContent = item.text || item.id;
      node.style.background = item.background || '#ffffff';
      node.style.color = item.color || '#17324d';
      node.addEventListener('click', () => pressScreenButton(item.id).catch(handleError));
    } else if (item.kind === 'text') {
      node = document.createElement('div');
      node.className = 'screen-widget text';
      node.textContent = item.text || '';
      node.style.color = item.color || '#17324d';
      node.style.fontSize = `${item.fontSize || 18}px`;
    } else {
      node = document.createElement('div');
      node.className = 'screen-widget box';
      node.style.background = item.background || '#cccccc';
    }

    node.style.left = `${item.x || 0}px`;
    node.style.top = `${item.y || 0}px`;
    if (item.width) {
      node.style.width = `${item.width}px`;
    }
    if (item.height) {
      node.style.height = `${item.height}px`;
    }
    elements.screenObjects.append(node);
  });
}

function renderPixelScreen(screen) {
  const imageDataUri = String(screen.imageDataUri || '').trim();
  if (imageDataUri) {
    renderImageScreen(imageDataUri, screen);
    return;
  }

  const width = Math.max(1, Number(screen.width) || 64);
  const height = Math.max(1, Number(screen.height) || 64);
  const scale = Math.max(4, Math.floor(Math.min(720 / width, 520 / height, 12)));

  elements.screenStage.classList.remove('objects-mode');
  elements.screenCanvas.classList.remove('hidden');
  elements.screenObjects.className = 'screen-objects';
  elements.screenObjects.innerHTML = '';
  elements.screenStage.style.width = `${width * scale}px`;
  elements.screenStage.style.height = `${height * scale}px`;

  const canvas = elements.screenCanvas;
  canvas.width = width;
  canvas.height = height;
  canvas.style.width = `${width * scale}px`;
  canvas.style.height = `${height * scale}px`;
  canvas.style.imageRendering = 'pixelated';

  const context = canvas.getContext('2d');
  context.clearRect(0, 0, width, height);
  const pixels = screen.pixels || {};
  Object.entries(pixels).forEach(([key, color]) => {
    const [xText, yText] = key.split(',');
    const x = Number(xText);
    const y = Number(yText);
    if (Number.isFinite(x) && Number.isFinite(y)) {
      context.fillStyle = color || '#000000';
      context.fillRect(x, y, 1, 1);
    }
  });
}

function renderImageScreen(imageDataUri, screen) {
  const fallbackWidth = Math.max(1, Number(screen.width) || 64);
  const fallbackHeight = Math.max(1, Number(screen.height) || 64);

  elements.screenStage.classList.remove('objects-mode');
  elements.screenCanvas.classList.remove('hidden');
  elements.screenObjects.className = 'screen-objects';
  elements.screenObjects.innerHTML = '';

  const canvas = elements.screenCanvas;
  const context = canvas.getContext('2d');
  canvas.width = fallbackWidth;
  canvas.height = fallbackHeight;
  context.clearRect(0, 0, fallbackWidth, fallbackHeight);

  const image = new Image();
  image.onload = () => {
    const width = Math.max(1, image.naturalWidth || fallbackWidth);
    const height = Math.max(1, image.naturalHeight || fallbackHeight);
    const scale = Math.max(1, Math.floor(Math.min(720 / width, 520 / height, 12)));

    elements.screenStage.style.width = `${width * scale}px`;
    elements.screenStage.style.height = `${height * scale}px`;
    canvas.width = width;
    canvas.height = height;
    canvas.style.width = `${width * scale}px`;
    canvas.style.height = `${height * scale}px`;
    canvas.style.imageRendering = 'pixelated';
    context.clearRect(0, 0, width, height);
    context.drawImage(image, 0, 0, width, height);
  };
  image.onerror = () => {
    const scale = Math.max(4, Math.floor(Math.min(720 / fallbackWidth, 520 / fallbackHeight, 12)));
    elements.screenStage.style.width = `${fallbackWidth * scale}px`;
    elements.screenStage.style.height = `${fallbackHeight * scale}px`;
    canvas.style.width = `${fallbackWidth * scale}px`;
    canvas.style.height = `${fallbackHeight * scale}px`;
  };
  image.src = imageDataUri;
}

function renderAnalyticsScreen(screen) {
  elements.screenStage.classList.remove('objects-mode');
  elements.screenCanvas.classList.add('hidden');
  elements.screenObjects.innerHTML = '';
  elements.screenObjects.className = 'screen-analytics';
  elements.screenObjects.style.width = '100%';
  elements.screenObjects.style.height = '100%';
  elements.screenStage.style.width = '920px';
  elements.screenStage.style.height = 'auto';

  const analytics = screen.analytics || [];
  if (!analytics.length) {
    const empty = document.createElement('div');
    empty.className = 'analytics-empty';
    empty.textContent = 'No analytics yet.';
    elements.screenObjects.append(empty);
    return;
  }

  analytics.forEach((item) => {
    const card = document.createElement('div');
    card.className = `analytics-card ${item.kind === 'progress' ? 'progress' : 'value'}`;

    const name = document.createElement('div');
    name.className = 'analytics-name';
    name.textContent = item.name || item.id;

    const value = document.createElement('div');
    value.className = 'analytics-value';
    if (item.kind === 'progress') {
      const numericValue = Math.max(0, Math.min(100, Number(item.value) || 0));
      value.textContent = `${numericValue}%`;
    } else {
      value.textContent = item.value || '';
    }

    const id = document.createElement('div');
    id.className = 'analytics-id';
    id.textContent = item.id || '';

    card.append(name, value);
    if (item.kind === 'progress') {
      const numericValue = Math.max(0, Math.min(100, Number(item.value) || 0));
      const previousValue = previousAnalyticsValues.has(item.id) ? previousAnalyticsValues.get(item.id) : numericValue;
      const bar = document.createElement('div');
      bar.className = 'analytics-progress';
      const fill = document.createElement('div');
      fill.className = 'analytics-progress-fill';
      fill.style.width = `${previousValue}%`;
      bar.append(fill);
      card.append(bar);
      requestAnimationFrame(() => {
        fill.style.width = `${numericValue}%`;
      });
    }
    card.append(id);
    elements.screenObjects.append(card);
    if (item.kind === 'progress') {
      previousAnalyticsValues.set(item.id, Math.max(0, Math.min(100, Number(item.value) || 0)));
    } else {
      previousAnalyticsValues.delete(item.id);
    }
  });

  const activeIds = new Set(analytics.filter((item) => item.kind === 'progress').map((item) => item.id));
  Array.from(previousAnalyticsValues.keys()).forEach((id) => {
    if (!activeIds.has(id)) {
      previousAnalyticsValues.delete(id);
    }
  });
}

async function pressScreenButton(objectId) {
  if (!state.screenConnectionId) {
    return;
  }
  await api(`/admin/screen/${state.screenConnectionId}/press/${encodeURIComponent(objectId)}`, { method: 'POST' });
  showToast(`Pressed ${objectId}.`);
}

async function refreshState() {
  const data = await api('/admin/state');
  state.connections = data.connections || [];
  renderGrid();
}

async function createConnection() {
  const name = window.prompt('Choose a name for the new connection:');
  if (name === null) {
    return;
  }
  const data = await api('/admin/connections', { method: 'POST', body: { name } });
  await refreshState();
  await copyText(data.connection.extensionUrl, 'New connection created and connection link copied.');
}

async function renameMenuConnection() {
  const connection = getMenuConnection();
  if (!connection) {
    showToast('Choose a connection first.');
    return;
  }
  const name = window.prompt('Choose a new name:', connection.name);
  if (name === null) {
    return;
  }
  await api(`/admin/connections/${connection.id}/rename`, { method: 'POST', body: { name } });
  await refreshState();
  openMenu(connection.id);
  showToast('Connection renamed.');
}

async function editMenuToken() {
  const connection = getMenuConnection();
  if (!connection) {
    showToast('Choose a connection first.');
    return;
  }
  const openRouterKey = window.prompt('Paste the OpenRouter API key for this connection. Leave it blank to remove it:', connection.openrouterKey || '');
  if (openRouterKey === null) {
    return;
  }
  await api(`/admin/connections/${connection.id}/openrouter-key`, { method: 'POST', body: { openrouter_key: openRouterKey } });
  await refreshState();
  openMenu(connection.id);
  showToast(openRouterKey.trim() ? 'OpenRouter key updated.' : 'OpenRouter key removed.');
}

async function toggleMenuConnection() {
  const connection = getMenuConnection();
  if (!connection) {
    showToast('Choose a connection first.');
    return;
  }
  await api(`/admin/connections/${connection.id}/toggle`, { method: 'POST' });
  await refreshState();
  openMenu(connection.id);
  showToast(connection.enabled ? 'Connection turned off.' : 'Connection turned on.');
}

async function copyMenuConnectionLink() {
  const connection = getMenuConnection();
  if (!connection) {
    showToast('Choose a connection first.');
    return;
  }
  await copyText(connection.extensionUrl, 'Connection link copied.');
}

async function copyMenuExtensionLink() {
  const connection = getMenuConnection();
  if (!connection) {
    showToast('Choose a connection first.');
    return;
  }
  await copyText(connection.extensionUrl, 'Extension link copied.');
}

async function deleteMenuConnection() {
  const connection = getMenuConnection();
  if (!connection) {
    showToast('Choose a connection first.');
    return;
  }
  const confirmed = window.confirm(`Delete "${connection.name}"? Projects using it will stop working until you add a new connection.`);
  if (!confirmed) {
    return;
  }
  await api(`/admin/connections/${connection.id}`, { method: 'DELETE' });
  closeMenu();
  await refreshState();
  showToast('Connection deleted.');
}

function handleError(error) {
  console.error(error);
  showToast(error.message || 'Something went wrong.');
}

elements.newConnectionButton.addEventListener('click', () => createConnection().catch(handleError));
elements.menuCloseButton.addEventListener('click', closeMenu);
elements.menuOverlay.addEventListener('click', (event) => {
  if (event.target === elements.menuOverlay) {
    closeMenu();
  }
});
elements.menuToggleButton.addEventListener('click', () => toggleMenuConnection().catch(handleError));
elements.menuRenameButton.addEventListener('click', () => renameMenuConnection().catch(handleError));
elements.menuTokenButton.addEventListener('click', () => editMenuToken().catch(handleError));
elements.menuViewScreenButton.addEventListener('click', openScreenViewer);
elements.menuCopyExtensionButton.addEventListener('click', () => copyMenuExtensionLink().catch(handleError));
elements.menuCopyLinkButton.addEventListener('click', () => copyMenuConnectionLink().catch(handleError));
elements.menuDeleteButton.addEventListener('click', () => deleteMenuConnection().catch(handleError));
elements.screenCloseButton.addEventListener('click', closeScreenViewer);
elements.screenOverlay.addEventListener('click', (event) => {
  if (event.target === elements.screenOverlay) {
    closeScreenViewer();
  }
});

refreshState().catch(handleError);
setInterval(() => {
  refreshState().catch(() => {});
}, STATE_REFRESH_MS);

setInterval(() => {
  if (state.screenConnectionId) {
    refreshScreenState().catch(() => {});
  }
}, SCREEN_REFRESH_MS);
