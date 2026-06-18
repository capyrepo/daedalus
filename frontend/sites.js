async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401 || res.status === 403) {
    window.location = '/login';
    return null;
  }
  return res;
}

function onTypeChange() {
  const type = document.getElementById('site-type').value;
  document.getElementById('port-group').style.display =
    (type === 'python' || type === 'node') ? '' : 'none';
}

function setMsg(text, isError) {
  const el = document.getElementById('form-msg');
  el.textContent = text;
  el.className = isError ? 'error-msg' : 'success-msg';
}

async function createSite() {
  const name = document.getElementById('site-name').value.trim();
  const type = document.getElementById('site-type').value;
  const portEl = document.getElementById('site-port');
  const port = portEl.value ? parseInt(portEl.value) : null;
  const btn = document.getElementById('create-btn');

  setMsg('', false);

  if (!name) { setMsg('Subdomain is required.', true); return; }
  if ((type === 'python' || type === 'node') && !port) {
    setMsg('Port is required for ' + type + ' apps.', true); return;
  }

  btn.disabled = true;
  btn.textContent = 'Creating…';

  const res = await api('POST', '/api/sites', { name, type, port });
  btn.disabled = false;
  btn.textContent = 'Create Site';

  if (!res) return;
  if (res.ok) {
    document.getElementById('site-name').value = '';
    document.getElementById('site-port').value = '';
    setMsg(`https://${name}.ent3.tech is live.`, false);
    loadSites();
  } else {
    const data = await res.json().catch(() => ({}));
    setMsg(data.detail || 'Failed to create site.', true);
  }
}

async function toggleSite(name, enable) {
  const action = enable ? 'enable' : 'disable';
  const res = await api('POST', `/api/sites/${name}/${action}`);
  if (!res) return;
  if (res.ok) {
    loadSites();
  } else {
    const data = await res.json().catch(() => ({}));
    alert(data.detail || `Failed to ${action} site.`);
  }
}

async function deleteSite(name, subdomain) {
  if (!confirm(`Delete ${subdomain}?\n\nThis removes the nginx config and stops its service. Files in /var/www/ or ~/apps/ are kept.`)) return;
  const res = await api('DELETE', `/api/sites/${name}`);
  if (!res) return;
  if (res.ok) {
    loadSites();
  } else {
    const data = await res.json().catch(() => ({}));
    alert(data.detail || 'Failed to delete site.');
  }
}

function renderSites(sites) {
  const el = document.getElementById('sites-list');
  if (!sites.length) {
    el.innerHTML = '<div class="empty-state">No sites yet — create one above.</div>';
    return;
  }
  el.innerHTML = sites.map(s => {
    const namePart = s.subdomain.replace(/\.ent3\.tech$/, '');
    const portLabel = s.port ? `<span class="site-port">:${s.port}</span>` : '';
    const typeClass = s.type;
    const typeLabel = s.type === 'node' ? 'Node.js' : s.type.charAt(0).toUpperCase() + s.type.slice(1);
    const dotClass = s.enabled ? 'on' : 'off';
    const toggleLabel = s.enabled ? 'Disable' : 'Enable';
    const toggleAction = s.enabled ? 'false' : 'true';
    return `
      <div class="site-card${s.enabled ? '' : ' disabled'}">
        <div class="site-info">
          <div class="status-dot ${dotClass}"></div>
          <span class="site-subdomain">${s.subdomain}</span>
          <span class="type-badge ${typeClass}">${typeLabel}</span>
          ${portLabel}
        </div>
        <div class="site-actions">
          <button class="btn-toggle" onclick="toggleSite('${namePart}', ${toggleAction})">${toggleLabel}</button>
          <button class="btn-delete" onclick="deleteSite('${namePart}', '${s.subdomain}')">Delete</button>
        </div>
      </div>`;
  }).join('');
}

async function loadSites() {
  const res = await api('GET', '/api/sites');
  if (!res) return;
  if (res.ok) {
    renderSites(await res.json());
  } else {
    document.getElementById('sites-list').innerHTML =
      '<div class="empty-state">Failed to load sites.</div>';
  }
}

loadSites();
