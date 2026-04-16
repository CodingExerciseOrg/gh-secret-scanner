/* ------------------------------------------------------------------ */
/* app.js — GitHub Actions Secret Scanner frontend                     */
/* ------------------------------------------------------------------ */

"use strict";

// ------------------------------------------------------------------
// Tab switching
// ------------------------------------------------------------------

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`panel-${btn.dataset.tab}`).classList.add("active");

    if (btn.dataset.tab === "repos")    loadRepos();
    if (btn.dataset.tab === "findings") loadFindings();
  });
});

// ------------------------------------------------------------------
// Setup tab — auth method toggle
// ------------------------------------------------------------------

document.querySelectorAll("input[name='auth_method']").forEach(r => {
  r.addEventListener("change", toggleAuthFields);
});

function toggleAuthFields() {
  const method = document.querySelector("input[name='auth_method']:checked").value;
  document.getElementById("row-token").style.display   = method === "token" ? "" : "none";
  document.getElementById("section-app").style.display = method === "app"   ? "" : "none";

  // When switching to app auth, reset sensitive fields to empty so the user
  // knows they must fill them in — never carry over a "saved" placeholder
  // from a previous token-auth session.
  if (method === "app") {
    const pk = document.getElementById("private-key");
    if (!pk.value) pk.placeholder = "";
  }

  // When switching to token auth, reset the token field placeholder too
  if (method === "token") {
    const tk = document.getElementById("token");
    if (!tk.value) tk.placeholder = "ghp_…";
  }
}

// ------------------------------------------------------------------
// Setup tab — scanner mode toggle
// ------------------------------------------------------------------

document.getElementById("scanner-mode").addEventListener("change", toggleScannerPath);

function toggleScannerPath() {
  const mode = document.getElementById("scanner-mode").value;
  const show = mode === "test";
  document.getElementById("row-scanner-path").style.display  = show ? "" : "none";
  document.getElementById("note-scanner-path").style.display = show ? "" : "none";
}

// ------------------------------------------------------------------
// Setup tab — load existing config on page load
// ------------------------------------------------------------------

async function loadConfig() {
  try {
    const cfg = await apiFetch("/api/config");
    document.getElementById("org").value = cfg.org || "";
    document.querySelector(`input[name='auth_method'][value='${cfg.auth_method || "token"}']`).checked = true;
    document.getElementById("scanner-mode").value = cfg.scanner_mode || "mock";
    document.getElementById("scanner-path").value = cfg.scanner_path || "";

    // Token auth
    if (cfg.token_set && cfg.auth_method === "token") {
      document.getElementById("token").placeholder = "••••••••  (token saved)";
    }

    // App auth — restore non-secret fields
    if (cfg.auth_method === "app") {
      document.getElementById("app-id").value          = cfg.app_id || "";
      document.getElementById("installation-id").value = cfg.installation_id || "";
      if (cfg.private_key_set) {
        document.getElementById("private-key").placeholder = "(private key saved)";
      }
    }

    toggleAuthFields();
    toggleScannerPath();
  } catch (_) { /* first launch — no config yet */ }
}

// ------------------------------------------------------------------
// Setup tab — save config
// ------------------------------------------------------------------

document.getElementById("btn-save").addEventListener("click", saveConfig);

async function saveConfig() {
  const feedback = document.getElementById("setup-feedback");
  const btn      = document.getElementById("btn-save");
  const method   = document.querySelector("input[name='auth_method']:checked").value;

  setFeedback(feedback, "", "");
  btn.disabled = true;

  const body = {
    org:                document.getElementById("org").value.trim(),
    auth_method:        method,
    scanner_mode:       document.getElementById("scanner-mode").value,
    scanner_path:       document.getElementById("scanner-path").value.trim(),
  };

  if (method === "token") {
    body.token = document.getElementById("token").value.trim();
  } else {
    body.app_id          = document.getElementById("app-id").value.trim();
    body.installation_id = document.getElementById("installation-id").value.trim();
    body.private_key     = document.getElementById("private-key").value.trim();
  }

  try {
    await apiFetch("/api/config", { method: "POST", body: JSON.stringify(body) });
    setFeedback(feedback, "✅ Config saved — scan triggered.", "ok");

    // Clear sensitive fields and replace with masked placeholders
    if (method === "token") {
      document.getElementById("token").value       = "";
      document.getElementById("token").placeholder = "••••••••  (token saved)";
    } else {
      document.getElementById("private-key").value       = "";
      document.getElementById("private-key").placeholder = "(private key saved)";
    }
  } catch (err) {
    setFeedback(feedback, `❌ ${err.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------
// Repositories tab
// ------------------------------------------------------------------

document.getElementById("btn-refresh-repos").addEventListener("click", loadRepos);

let _reposData   = [];
let _repoSortCol = "name";
let _repoSortAsc = true;

async function loadRepos() {
  const status = document.getElementById("repos-status");
  const btn    = document.getElementById("btn-refresh-repos");
  status.innerHTML = '<span class="spinner"></span> Loading…';
  btn.disabled = true;

  try {
    _reposData = await apiFetch("/api/repos");
    renderRepos();
    status.textContent = "";
  } catch (err) {
    status.textContent = `❌ ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

function renderRepos() {
  const tbody = document.getElementById("repos-body");
  const count = document.getElementById("repo-count");

  if (!_reposData.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-msg">No repositories found.</td></tr>`;
    count.textContent = "";
    return;
  }

  const sorted = [..._reposData].sort((a, b) => {
    const av = (a[_repoSortCol] || "").toString().toLowerCase();
    const bv = (b[_repoSortCol] || "").toString().toLowerCase();
    return _repoSortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
  });

  tbody.innerHTML = sorted.map(r => `
    <tr>
      <td>${esc(r.name)}</td>
      <td>${esc(r.visibility)}</td>
      <td>${esc(r.language || "—")}</td>
      <td>${esc(r.default_branch)}</td>
      <td>${esc(r.updated_at)}</td>
    </tr>
  `).join("");

  count.textContent = `${_reposData.length} repositories`;

  document.querySelectorAll("#repos-table thead th").forEach(th => {
    th.classList.remove("sorted-asc", "sorted-desc");
    if (th.dataset.col === _repoSortCol) {
      th.classList.add(_repoSortAsc ? "sorted-asc" : "sorted-desc");
    }
  });
}

// Column sorting
document.querySelectorAll("#repos-table thead th").forEach(th => {
  th.addEventListener("click", () => {
    if (_repoSortCol === th.dataset.col) {
      _repoSortAsc = !_repoSortAsc;
    } else {
      _repoSortCol = th.dataset.col;
      _repoSortAsc = true;
    }
    renderRepos();
  });
});

// ------------------------------------------------------------------
// Findings tab
// ------------------------------------------------------------------

document.getElementById("btn-refresh-findings").addEventListener("click", loadFindings);
document.getElementById("btn-dismiss-findings").addEventListener("click", () => clearFindings("dismiss"));
document.getElementById("btn-reset-findings").addEventListener("click",   () => clearFindings("reset"));

let _findingsData = [];
let _findingsMap  = {};   // fid → finding object (avoids inline JSON in HTML)

async function loadFindings() {
  try {
    _findingsData = await apiFetch("/api/findings");
    renderFindings();
  } catch (err) {
    document.getElementById("findings-tree").innerHTML =
      `<p class="empty-msg" style="color:var(--danger)">❌ ${esc(err.message)}</p>`;
  }
}

async function clearFindings(mode = "dismiss") {
  const msg = mode === "reset"
    ? "Clear all findings and re-scan all runs from the beginning?"
    : "Dismiss all findings? Already-scanned runs won't be re-scanned.";
  if (!confirm(msg)) return;
  try {
    await apiFetch(`/api/findings?mode=${mode}`, { method: "DELETE" });
    _findingsData = [];
    _findingsMap  = {};
    _lastFindingsCount = -1;  // force reload when findings come back
    renderFindings();
    document.getElementById("finding-detail").classList.remove("visible");
  } catch (err) {
    alert(`Failed to clear: ${err.message}`);
  }
}

function renderFindings() {
  const tree  = document.getElementById("findings-tree");
  const count = document.getElementById("findings-count");
  _findingsMap = {};
  updateBadge(_findingsData.length);

  if (!_findingsData.length) {
    count.textContent = "";
    tree.innerHTML = `<p class="empty-msg">No findings yet. Run a scan to populate this view.</p>`;
    return;
  }

  count.textContent = `${_findingsData.length} finding(s)`;

  // Group: repo → run_id → [findings]
  const grouped = {};
  for (const f of _findingsData) {
    const repo   = f.repo   || "unknown";
    const run_id = f.run_id || 0;
    if (!grouped[repo])         grouped[repo] = {};
    if (!grouped[repo][run_id]) grouped[repo][run_id] = [];
    grouped[repo][run_id].push(f);
  }

  let html = "";
  let fidCounter = 0;

  for (const repo of Object.keys(grouped).sort()) {
    const runs  = grouped[repo];
    const total = Object.values(runs).reduce((s, fs) => s + fs.length, 0);
    const repoId = `rg-${fidCounter++}`;

    html += `
      <div class="repo-group">
        <div class="repo-header" onclick="toggleGroup('${repoId}')">
          📁 ${esc(repo)}
          <span class="count">${total} finding(s)</span>
        </div>
        <div id="${repoId}">
    `;

    for (const run_id of Object.keys(runs).sort()) {
      const fs      = runs[run_id];
      const runName = fs[0].run_name || `run #${run_id}`;
      const runId   = `rg-${fidCounter++}`;

      html += `
        <div class="run-group">
          <div class="run-header" onclick="toggleGroup('${runId}')">
            ⚙ ${esc(runName)} <span style="color:var(--muted);font-weight:400">#${run_id}</span>
            <span class="count">${fs.length} finding(s)</span>
          </div>
          <div class="finding-rows" id="${runId}">
      `;

      fs.forEach(f => {
        const fid = `f-${fidCounter++}`;
        _findingsMap[fid] = f;   // store reference — no JSON in HTML
        html += `
          <div class="finding-row" id="${fid}" data-fid="${fid}">
            <span class="secret-type">${esc(f.secret_type || "—")}</span>
            <span class="line">line ${f.line_number ?? "?"}</span>
            <span class="step">${esc(f.step || "—")}</span>
            <span class="matched">${esc(f.matched_text || "")}</span>
          </div>
        `;
      });

      html += `</div></div>`;
    }

    html += `</div></div>`;
  }

  tree.innerHTML = html;

  // Attach click handlers after DOM is built
  tree.querySelectorAll(".finding-row").forEach(row => {
    row.addEventListener("click", () => selectFinding(row.dataset.fid));
  });
}

function toggleGroup(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = el.style.display === "none" ? "" : "none";
}

function selectFinding(fid) {
  document.querySelectorAll(".finding-row").forEach(r => r.classList.remove("selected"));
  document.getElementById(fid)?.classList.add("selected");

  const f = _findingsMap[fid];
  if (!f) return;

  const detail = document.getElementById("finding-detail");
  const grid   = document.getElementById("detail-grid");

  const rows = [
    ["Repository",              f.repo],
    ["Run ID",                  f.run_id],
    ["Run name",                f.run_name],
    ...(f.step ? [["Step", f.step]] : []),
    ["Line",                    f.line_number],
    ["Secret type",             f.secret_type],
    ["Matched text (redacted)", f.matched_text],
  ];

  grid.innerHTML = rows.map(([k, v]) =>
    `<span class="key">${esc(k)}:</span><span class="value">${esc(String(v ?? ""))}</span>`
  ).join("");

  detail.classList.add("visible");
}

// ------------------------------------------------------------------
// Status bar — poll /api/status every 3 seconds
// ------------------------------------------------------------------

function updateBadge(n) {
  document.getElementById("findings-badge").textContent = `${n} finding${n === 1 ? "" : "s"}`;
}

let _lastFindingsCount = 0;

async function pollStatus() {
  try {
    const s = await apiFetch("/api/status");
    document.getElementById("status-msg").textContent = s.message || "Ready.";
    const m   = Math.floor(s.seconds_until_poll / 60);
    const sec = String(s.seconds_until_poll % 60).padStart(2, "0");
    document.getElementById("countdown").textContent = `Next poll in ${m}:${sec}`;
    updateBadge(s.findings_count);

    // Auto-refresh findings whenever the count changes (regardless of active tab)
    if (s.findings_count !== _lastFindingsCount) {
      _lastFindingsCount = s.findings_count;
      loadFindings();
    }
  } catch (_) { /* server may be starting up */ }
}

setInterval(pollStatus, 3000);

// ------------------------------------------------------------------
// Utilities
// ------------------------------------------------------------------

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

function setFeedback(el, msg, type) {
  el.textContent = msg;
  el.className   = `feedback ${type}`;
}

// ------------------------------------------------------------------
// Init
// ------------------------------------------------------------------

loadConfig();
pollStatus();
