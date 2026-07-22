"use strict";
/* Helena — painel de desktop. Vanilla JS, mesma base de auth/api de webui.js
 * (arquivo separado de propósito: o Electron carrega só esta página, sem
 * puxar a lógica de chat/configurações junto). */

const TOKEN_KEY = "helena_token";
const POLL_MS = 4000;
let pollTimer = null;

function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  const resp = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 401 && auth) {
    setToken(null);
    stopPolling();
    showLogin();
    throw new Error("sessão expirada");
  }
  let data = null;
  try {
    data = await resp.json();
  } catch (_) {
    data = null;
  }
  if (!resp.ok) {
    throw new Error((data && data.error) || `erro HTTP ${resp.status}`);
  }
  return data;
}

function toast(message, kind = "ok") {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = `toast ${kind}`;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 4000);
}

function showLogin() {
  document.getElementById("login-screen").hidden = false;
  document.getElementById("app-screen").hidden = true;
}

function showApp() {
  document.getElementById("login-screen").hidden = true;
  document.getElementById("app-screen").hidden = false;
}

async function boot() {
  if (!getToken()) {
    showLogin();
    return;
  }
  try {
    const { user } = await api("/account/me");
    document.getElementById("user-name").textContent = user.name || user.email || "";
    showApp();
    startPolling();
  } catch (err) {
    showLogin();
  }
}

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("login-email").value.trim();
  const password = document.getElementById("login-password").value;
  const errorEl = document.getElementById("login-error");
  errorEl.hidden = true;
  try {
    const data = await api("/auth/login", { method: "POST", body: { email, password }, auth: false });
    setToken(data.access_token);
    document.getElementById("user-name").textContent = data.user.name || data.user.email || "";
    showApp();
    startPolling();
  } catch (err) {
    errorEl.textContent = err.message || "falha no login";
    errorEl.hidden = false;
  }
});

document.getElementById("logout-btn").addEventListener("click", () => {
  setToken(null);
  stopPolling();
  showLogin();
});

// ---------- polling + render ----------

function startPolling() {
  stopPolling();
  refresh();
  pollTimer = setInterval(refresh, POLL_MS);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : s;
  return div.innerHTML;
}

function fmtBytes(n) {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1)} ${units[i]}`;
}

function fmtAgo(iso) {
  if (!iso) return "nunca";
  const diffMs = Date.now() - new Date(iso).getTime();
  const s = Math.max(0, Math.floor(diffMs / 1000));
  if (s < 60) return `há ${s}s`;
  if (s < 3600) return `há ${Math.floor(s / 60)}min`;
  if (s < 86400) return `há ${Math.floor(s / 3600)}h`;
  return `há ${Math.floor(s / 86400)}d`;
}

function setGauge(prefix, percent, extraText) {
  const fill = document.getElementById(`gauge-${prefix}`);
  const text = document.getElementById(`gauge-${prefix}-text`);
  const pct = percent == null ? 0 : Math.max(0, Math.min(100, percent));
  fill.style.width = `${pct}%`;
  fill.classList.remove("warn", "danger");
  if (pct >= 90) fill.classList.add("danger");
  else if (pct >= 70) fill.classList.add("warn");
  text.textContent = percent == null ? "indisponível" : `${pct.toFixed(0)}%${extraText ? " — " + extraText : ""}`;
}

function renderSystem(system) {
  setGauge("cpu", system.cpu_percent);
  if (system.memory) {
    setGauge("mem", system.memory.percent, `${fmtBytes(system.memory.used)} / ${fmtBytes(system.memory.total)}`);
  } else {
    setGauge("mem", null);
  }
  if (system.disk) {
    setGauge("disk", system.disk.percent, `${fmtBytes(system.disk.used)} / ${fmtBytes(system.disk.total)}`);
  } else {
    setGauge("disk", null);
  }
}

function userCard(u) {
  const badge = u.shell_full_control ? "⚡ controle absoluto" : (u.is_principal ? "★ principal" : "normal");
  return `
    <div class="session-card user">
      <div class="title">👤 ${escapeHtml(u.name || u.email || ("usuário " + u.id))}</div>
      <div class="meta">${escapeHtml(badge)}</div>
      <div class="meta">visto ${fmtAgo(u.last_seen_at)}</div>
      <div class="meta">${u.active_jobs} job(s) ativo(s)</div>
    </div>
  `;
}

function jobCard(j) {
  return `
    <div class="session-card job">
      <div class="title">⚙️ ${escapeHtml(j.type)}</div>
      <div class="meta">${escapeHtml(j.title)}</div>
      <div class="meta">status: ${escapeHtml(j.status)} · iniciado ${fmtAgo(j.created_at)}</div>
    </div>
  `;
}

function renderGrid(users, jobs) {
  const grid = document.getElementById("grid-sessions");
  if (!users.length && !jobs.length) {
    grid.innerHTML = '<div class="session-empty">nenhuma sessão ainda.</div>';
    return;
  }
  grid.innerHTML = users.map(userCard).join("") + jobs.map(jobCard).join("");
}

function renderProcesses(processes) {
  const tbody = document.getElementById("proc-tbody");
  tbody.innerHTML = processes.map((p) => `
    <tr>
      <td>${p.pid}</td>
      <td>${escapeHtml(p.name || "")}</td>
      <td>${p.cpu_percent.toFixed(1)}</td>
      <td>${p.memory_percent.toFixed(1)}</td>
    </tr>
  `).join("");
}

async function refresh() {
  try {
    const data = await api("/dashboard/overview");
    renderSystem(data.system || {});
    renderGrid(data.users || [], data.jobs || []);
    renderProcesses(data.processes || []);
    document.getElementById("last-update").textContent =
      "atualizado " + new Date().toLocaleTimeString("pt-BR");
  } catch (err) {
    if (err.message !== "sessão expirada") {
      toast("falha ao atualizar o painel: " + err.message, "error");
    }
  }
}

boot();
