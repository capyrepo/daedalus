const api = path => fetch("/api" + path).then(r => { if (!r.ok) throw r; return r.json(); });

// ---- System metrics ----

async function refresh() {
  try {
    const [sys, nginx] = await Promise.all([api("/system"), api("/nginx")]);

    setGauge("cpu", sys.cpu_percent);

    setGauge("memory", sys.memory_percent);
    document.getElementById("memory-sub").textContent =
      `${sys.memory_used_gb} GB / ${sys.memory_total_gb} GB`;

    const diskList = document.getElementById("disk-list");
    diskList.innerHTML = sys.disks.map(d => `
      <div class="disk-row">
        <div class="disk-path">
          ${esc(d.path)}
          <span class="disk-device">${esc(d.device)}</span>
        </div>
        <div class="disk-meta">
          <span>${d.used_gb} GB used of ${d.total_gb} GB</span>
          <span class="disk-free">${d.free_gb} GB free</span>
        </div>
        <div class="bar" style="margin-top:.4rem">
          <div class="bar-fill ${barClass(d.percent)}" style="width:${d.percent}%"></div>
        </div>
        <div class="disk-pct">${d.percent}%</div>
      </div>`).join("");

    setNetGauge("net-recv", sys.net_recv_kbps);
    setNetGauge("net-sent", sys.net_sent_kbps);

    const runEl = document.getElementById("nginx-running");
    runEl.innerHTML = nginx.running
      ? '<span class="badge up">Active</span>'
      : '<span class="badge down">Down</span>';
    document.getElementById("nginx-conns").textContent = nginx.active_connections;
    document.getElementById("nginx-rps").textContent   = nginx.requests_per_sec.toFixed(2);

    document.getElementById("last-updated").textContent =
      "Updated " + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById("last-updated").textContent = "Error: " + (e?.status || e?.message || e);
  }
}

function setGauge(id, pct) {
  document.getElementById(`${id}-value`).textContent = pct.toFixed(1) + "%";
  const fill = document.getElementById(`${id}-fill`);
  fill.style.width = pct + "%";
  fill.className = "bar-fill " + barClass(pct);
}

function setNetGauge(id, kbps) {
  const label = kbps >= 1024
    ? (kbps / 1024).toFixed(1) + " MB/s"
    : kbps.toFixed(1) + " KB/s";
  document.getElementById(`${id}-value`).textContent = label;
  const pct = Math.min((kbps / 1024) * 100, 100);
  const fill = document.getElementById(`${id}-fill`);
  fill.style.width = pct + "%";
  fill.className = "bar-fill " + barClass(pct);
}

function barClass(pct) {
  return pct >= 85 ? "high" : pct >= 60 ? "medium" : "low";
}

function esc(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// ---- RAID ----

async function refreshRaid() {
  try {
    const arrays = await api("/raid");
    const section = document.getElementById("raid-section");
    if (!arrays.length) { section.style.display = "none"; return; }
    section.style.display = "";

    document.getElementById("raid-arrays").innerHTML = arrays.map(a => {
      const stateClass = a.state === "active" ? "up" : a.state === "degraded" ? "down" : "pending";
      const devDots = a.devices.map(d =>
        `<span class="raid-dev ${d.up ? 'up' : 'down'}" title="${esc(d.name)}">${esc(d.name)}</span>`
      ).join("");

      let syncHtml = "";
      if (a.sync) {
        const eta = a.sync.finish_min >= 60
          ? `${(a.sync.finish_min / 60).toFixed(1)}h`
          : `${Math.round(a.sync.finish_min)}m`;
        const speed = a.sync.speed_kbps >= 1024
          ? `${(a.sync.speed_kbps / 1024).toFixed(0)} MB/s`
          : `${a.sync.speed_kbps} KB/s`;
        syncHtml = `
          <div class="raid-sync">
            <div class="raid-sync-label">
              <span class="raid-sync-op">${a.sync.operation}</span>
              <span>${a.sync.percent.toFixed(1)}%</span>
              <span class="raid-sync-meta">ETA ${eta} &middot; ${speed}</span>
            </div>
            <div class="bar">
              <div class="bar-fill low" style="width:${a.sync.percent}%"></div>
            </div>
          </div>`;
      }

      return `
        <div class="raid-card">
          <div class="raid-header">
            <span class="badge ${stateClass}">${a.state}</span>
            <span class="raid-name">/dev/${esc(a.name)}</span>
            <span class="raid-level">${esc(a.level)}</span>
            <span class="raid-count">${a.devices_active}/${a.devices_total} drives</span>
          </div>
          <div class="raid-devices">${devDots}</div>
          ${syncHtml}
        </div>`;
    }).join("");
  } catch (e) {
    const section = document.getElementById("raid-section");
    section.style.display = "";
    document.getElementById("raid-arrays").innerHTML =
      `<p style="color:#fca5a5;font-size:.85rem">RAID error: ${e?.status || e?.message || e}</p>`;
  }
}

// ---- Top Processes ----

async function refreshProcesses() {
  try {
    const procs = await api("/processes");
    document.getElementById("process-table").innerHTML = `
      <table class="proc-table">
        <thead><tr>
          <th>Name</th><th>PID</th><th>CPU%</th><th>Mem%</th><th>Status</th>
        </tr></thead>
        <tbody>
          ${procs.map(p => `
            <tr>
              <td class="proc-name">${esc(p.name)}</td>
              <td class="proc-pid">${p.pid}</td>
              <td class="proc-cpu ${barClass(p.cpu_percent)}-text">${p.cpu_percent.toFixed(1)}%</td>
              <td>${p.memory_percent.toFixed(2)}%</td>
              <td class="proc-status">${esc(p.status)}</td>
            </tr>`).join("")}
        </tbody>
      </table>`;
  } catch (e) {
    document.getElementById("process-table").textContent = "Error: " + (e?.message || e);
  }
}

// ---- Failed Services ----

async function refreshServices() {
  try {
    const services = await api("/services/failed");
    const section = document.getElementById("services-section");
    if (!services.length) { section.style.display = "none"; return; }
    section.style.display = "";
    document.getElementById("services-list").innerHTML = services.map(s => `
      <div class="service-card">
        <span class="badge down">failed</span>
        <span class="service-name">${esc(s.name)}</span>
        <span class="service-desc">${esc(s.description)}</span>
      </div>`).join("");
  } catch (e) {
    document.getElementById("services-section").style.display = "";
    document.getElementById("services-list").innerHTML =
      `<p style="color:#fca5a5;font-size:.85rem">Error: ${e?.message || e}</p>`;
  }
}

// ---- Terminal ----

let term, termWs, termFit, termOpen = false;

function toggleTerminal() {
  const wrap = document.getElementById('terminal-wrap');
  const btn  = document.getElementById('terminal-toggle');
  if (!termOpen) {
    wrap.style.display = '';
    btn.textContent = 'Hide';
    if (!term) initTerminal();
    else termFit.fit();
    termOpen = true;
  } else {
    wrap.style.display = 'none';
    btn.textContent = 'Show';
    termOpen = false;
  }
}

function initTerminal() {
  term = new Terminal({
    cursorBlink: true,
    scrollback: 5000,
    fontFamily: '"Cascadia Code", "Fira Code", monospace',
    fontSize: 14,
    theme: {
      background: '#0f172a', foreground: '#e2e8f0', cursor: '#38bdf8',
      selectionBackground: '#1e3a5f', black: '#1e293b', brightBlack: '#475569',
    },
  });
  termFit = new FitAddon.FitAddon();
  term.loadAddon(termFit);
  term.open(document.getElementById('terminal-el'));
  termFit.fit();

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  termWs = new WebSocket(`${proto}://${location.host}/ws/terminal`);
  termWs.onopen    = () => termFit.fit();
  termWs.onmessage = e => term.write(e.data);
  termWs.onclose   = () => term.write('\r\n\x1b[31m[Connection closed]\x1b[0m\r\n');
  termWs.onerror   = () => term.write('\r\n\x1b[31m[Connection error]\x1b[0m\r\n');

  term.onData(d => termWs.readyState === 1 && termWs.send(d));
  term.onResize(({rows, cols}) => termWs.readyState === 1 && termWs.send(`\x00resize:${rows}:${cols}`));
  window.addEventListener('resize', () => { if (termOpen) termFit.fit(); });
}

// ---- Boot ----

refresh();
refreshRaid();
refreshProcesses();
refreshServices();
setInterval(refresh, 5000);
setInterval(refreshRaid, 10000);
setInterval(refreshProcesses, 5000);
setInterval(refreshServices, 15000);
