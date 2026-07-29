const bootstrap = window.__SCRATCHLINK_BOOTSTRAP__ || {};

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
  menuCopyExtensionButton: document.getElementById('menu-copy-extension-button'),
  menuCopyLinkButton: document.getElementById('menu-copy-link-button'),
  menuDeleteButton: document.getElementById('menu-delete-button')
};

let state = {
  connections: [],
  menuConnectionId: null
};

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
  elements.menuOverlay.classList.remove('hidden');
}

function closeMenu() {
  state.menuConnectionId = null;
  elements.menuOverlay.classList.add('hidden');
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
elements.menuCopyExtensionButton.addEventListener('click', () => copyMenuExtensionLink().catch(handleError));
elements.menuCopyLinkButton.addEventListener('click', () => copyMenuConnectionLink().catch(handleError));
elements.menuDeleteButton.addEventListener('click', () => deleteMenuConnection().catch(handleError));

refreshState().catch(handleError);
setInterval(() => {
  refreshState().catch(() => {});
}, 2500);
