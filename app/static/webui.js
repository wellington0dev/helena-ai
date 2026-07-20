"use strict";
/* Helena — página web (chat + configuração). Vanilla JS, sem dependências,
 * fala só com a própria API da Helena (mesma origem). */

const TOKEN_KEY = "helena_token";
let currentUser = null;
let ollamaCatalogCache = [];
let pullPollTimer = null;

// ---------- infra ----------

function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function api(path, { method = "GET", body, auth = true, raw = false } = {}) {
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
    showLogin();
    throw new Error("sessão expirada");
  }
  if (raw) return resp;
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

// ---------- login/logout ----------

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
    currentUser = user;
    document.getElementById("user-name").textContent = user.name || user.email || "";
    showApp();
    await Promise.all([loadHistory(), loadSettings()]);
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
    currentUser = data.user;
    document.getElementById("user-name").textContent = data.user.name || data.user.email || "";
    showApp();
    await Promise.all([loadHistory(), loadSettings()]);
  } catch (err) {
    errorEl.textContent = err.message || "falha no login";
    errorEl.hidden = false;
  }
});

document.getElementById("logout-btn").addEventListener("click", () => {
  setToken(null);
  currentUser = null;
  showLogin();
});

// ---------- tabs ----------

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ---------- tema ----------

const THEME_KEY = "helena_theme";

function effectiveTheme() {
  const set = document.documentElement.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function updateThemeIcon() {
  const btn = document.getElementById("theme-toggle");
  const dark = effectiveTheme() === "dark";
  btn.textContent = dark ? "☀️" : "🌙";
  btn.title = dark ? "Mudar para tema claro" : "Mudar para tema escuro";
}

document.getElementById("theme-toggle").addEventListener("click", () => {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
  updateThemeIcon();
});

// se o usuário nunca escolheu manualmente, segue a troca de tema do SO
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (!localStorage.getItem(THEME_KEY)) updateThemeIcon();
});

updateThemeIcon();

// ---------- chat ----------

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : s;
  return div.innerHTML;
}

async function openMedia(mediaUrl) {
  try {
    const resp = await api(`/media/${currentUser.id}/${mediaUrl}`, { raw: true });
    const blob = await resp.blob();
    window.open(URL.createObjectURL(blob), "_blank");
  } catch (err) {
    toast("não consegui abrir a mídia: " + err.message, "error");
  }
}

function renderMessage(msg) {
  const list = document.getElementById("chat-messages");
  const bubble = document.createElement("div");
  bubble.className = `bubble ${msg.role === "user" ? "user" : "assistant"}`;
  bubble.textContent = msg.content || (msg.role === "user" ? "" : "(sem texto)");
  if (msg.media_url) {
    const link = document.createElement("a");
    link.className = "media-link";
    link.textContent = `📎 ${msg.media_type || "mídia"}`;
    link.href = "#";
    link.addEventListener("click", (e) => { e.preventDefault(); openMedia(msg.media_url); });
    bubble.appendChild(document.createElement("br"));
    bubble.appendChild(link);
  }
  list.appendChild(bubble);
  list.scrollTop = list.scrollHeight;
  return bubble;
}

async function loadHistory() {
  const list = document.getElementById("chat-messages");
  list.innerHTML = "";
  try {
    const { messages } = await api("/messages?limit=50");
    messages.forEach(renderMessage);
  } catch (err) {
    toast("não consegui carregar o histórico: " + err.message, "error");
  }
}

document.getElementById("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("chat-input");
  const content = input.value.trim();
  if (!content) return;
  const sendBtn = document.getElementById("chat-send");

  renderMessage({ role: "user", content });
  input.value = "";
  input.style.height = "auto";
  sendBtn.disabled = true;
  const pending = renderMessage({ role: "assistant", content: "digitando..." });
  pending.classList.add("pending");

  try {
    const data = await api("/messages", { method: "POST", body: { content } });
    pending.remove();
    (data.replies || []).forEach(renderMessage);
  } catch (err) {
    pending.textContent = "erro: " + err.message;
    pending.classList.remove("pending");
  } finally {
    sendBtn.disabled = false;
  }
});

const chatInput = document.getElementById("chat-input");
chatInput.addEventListener("input", () => {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 128) + "px";
});
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    document.getElementById("chat-form").requestSubmit();
  }
});

// ---------- configurações ----------

const EDITABLE_FIELDS = [
  "LLM_PROVIDER", "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_IMAGE_MODEL",
  "GEMINI_TTS_MODEL", "GEMINI_TTS_VOICE", "OLLAMA_HOST", "OLLAMA_MANAGED",
  "HELENA_PORT", "HELENA_HOST", "HELENA_DESKTOP_NOTIFICATIONS",
];
const INFO_LABELS = {
  JWT_SECRET_KEY: "segredo JWT",
  DATABASE_URL: "URL do banco",
  HELENA_DATA_DIR: "diretório de dados",
  HELENA_MEDIA_DIR: "diretório de mídia",
};

function updateProviderVisibility() {
  const provider = document.getElementById("s-LLM_PROVIDER").value;
  document.getElementById("gemini-card").hidden = provider !== "gemini";
  document.getElementById("ollama-card").hidden = provider !== "ollama";
}

document.getElementById("s-LLM_PROVIDER").addEventListener("change", () => {
  updateProviderVisibility();
  if (document.getElementById("s-LLM_PROVIDER").value === "ollama") loadOllamaModels();
});

async function loadSettings() {
  try {
    const { values, info } = await api("/settings");
    document.getElementById("s-LLM_PROVIDER").value = values.LLM_PROVIDER || "gemini";
    document.getElementById("s-GEMINI_API_KEY").value = "";
    document.getElementById("s-GEMINI_API_KEY").placeholder =
      values.GEMINI_API_KEY ? `configurado (${values.GEMINI_API_KEY}) — em branco não mexe` : "não configurado";
    ["GEMINI_MODEL", "GEMINI_IMAGE_MODEL", "GEMINI_TTS_MODEL", "GEMINI_TTS_VOICE",
     "OLLAMA_HOST", "HELENA_PORT", "HELENA_HOST"].forEach((key) => {
      document.getElementById(`s-${key}`).value = values[key] || "";
    });
    document.getElementById("s-HELENA_DESKTOP_NOTIFICATIONS").checked = values.HELENA_DESKTOP_NOTIFICATIONS !== "0";
    document.getElementById("s-OLLAMA_MANAGED").checked = values.OLLAMA_MANAGED !== "0";

    const infoList = document.getElementById("settings-info");
    infoList.innerHTML = "";
    Object.entries(info).forEach(([key, configured]) => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${INFO_LABELS[key] || key}</span><span>${configured ? "✓ configurado" : "— não configurado"}</span>`;
      infoList.appendChild(li);
    });

    updateProviderVisibility();
    if (values.LLM_PROVIDER === "ollama") loadOllamaModels();
  } catch (err) {
    toast("não consegui carregar as configurações: " + err.message, "error");
  }
}

const RATING_LABEL = { green: "adequado", yellow: "roda, mas custa desempenho", red: "não recomendado" };

function renderCatalog() {
  const box = document.getElementById("ollama-catalog");
  box.innerHTML = "";
  ollamaCatalogCache.forEach((m) => {
    const row = document.createElement("div");
    row.className = "model-row" + (m.active ? " active" : "");
    row.innerHTML = `
      <span class="dot ${m.rating}" title="${RATING_LABEL[m.rating]}"></span>
      <div class="model-info">
        <div class="model-name">${escapeHtml(m.name)}</div>
        <div class="model-meta">${m.params_b}B · ~${m.est_gb.toFixed(1)}GB · ${RATING_LABEL[m.rating]}
          ${m.active ? '<span class="badge">ativo</span>' : ""}
          ${m.installed ? '<span class="badge">baixado</span>' : ""}
        </div>
      </div>
    `;
    const useBtn = document.createElement("button");
    useBtn.className = "small";
    useBtn.textContent = m.active ? "ativo" : (m.installed ? "usar" : "baixar e usar");
    useBtn.disabled = m.active;
    useBtn.addEventListener("click", () => useModel(m.name));
    row.appendChild(useBtn);
    box.appendChild(row);
  });
}

async function loadOllamaModels() {
  try {
    const { hardware, catalog } = await api("/settings/ollama/models");
    ollamaCatalogCache = catalog;
    const gpu = hardware.gpu_vram_gb ? `${hardware.gpu_vram_gb}GB VRAM` : "sem GPU detectada (CPU)";
    document.getElementById("ollama-hw").textContent =
      `Hardware: ${hardware.ram_gb}GB RAM, ${hardware.cpu_count} CPUs, ${gpu}`;
    renderCatalog();
  } catch (err) {
    toast("não consegui listar modelos do Ollama: " + err.message, "error");
  }
}

async function useModel(name) {
  const progress = document.getElementById("ollama-pull-progress");
  const label = document.getElementById("ollama-pull-label");
  const model = ollamaCatalogCache.find((m) => m.name === name);
  try {
    if (!model || !model.installed) {
      progress.hidden = false;
      label.textContent = `baixando ${name}...`;
      await api("/settings/ollama/pull", { method: "POST", body: { name } });
      await waitForPull(name);
    }
    label.textContent = `testando ${name}...`;
    progress.hidden = false;
    const test = await api("/settings/ollama/test", { method: "POST", body: { name } });
    progress.hidden = true;
    if (!test.ok) {
      toast(`modelo baixou mas não roda: ${test.detail}`, "error");
      return;
    }
    toast(`${name} testado e pronto — clique "Salvar e reiniciar" pra ativar.`, "ok");
    ollamaCatalogCache.forEach((m) => { m.active = m.name === name; });
    pendingOllamaModel = name;
    renderCatalog();
  } catch (err) {
    progress.hidden = true;
    toast("falha: " + err.message, "error");
  }
}

let pendingOllamaModel = null;

function waitForPull(name) {
  return new Promise((resolve, reject) => {
    clearInterval(pullPollTimer);
    pullPollTimer = setInterval(async () => {
      try {
        const status = await api(`/settings/ollama/pull/status?name=${encodeURIComponent(name)}`);
        if (status.status === "done") {
          clearInterval(pullPollTimer);
          resolve();
        } else if (status.status === "error") {
          clearInterval(pullPollTimer);
          reject(new Error(status.detail || "falha ao baixar"));
        }
      } catch (err) {
        clearInterval(pullPollTimer);
        reject(err);
      }
    }, 1500);
  });
}

// ---------- salvar + reiniciar ----------

document.getElementById("save-restart-btn").addEventListener("click", async () => {
  const btn = document.getElementById("save-restart-btn");
  const status = document.getElementById("settings-status");
  const body = {};
  EDITABLE_FIELDS.forEach((key) => {
    const el = document.getElementById(`s-${key}`);
    if (!el) return;
    body[key] = el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value;
  });
  if (pendingOllamaModel) body.OLLAMA_MODEL = pendingOllamaModel;
  if (!body.GEMINI_API_KEY) delete body.GEMINI_API_KEY; // em branco = não mexer

  const before = (await api("/settings")).values;
  const oldPort = before.HELENA_PORT;
  const oldHost = before.HELENA_HOST;

  btn.disabled = true;
  status.textContent = "salvando...";
  try {
    await api("/settings", { method: "PUT", body });
    status.textContent = "reiniciando...";
    await api("/settings/restart", { method: "POST" });

    const newPort = body.HELENA_PORT || oldPort;
    const newHost = body.HELENA_HOST || oldHost;
    if (newPort !== oldPort || (newHost !== oldHost && newHost !== "0.0.0.0")) {
      const displayHost = newHost === "0.0.0.0" ? location.hostname : newHost;
      status.innerHTML = `reiniciado — acesse <a href="http://${displayHost}:${newPort}/">http://${displayHost}:${newPort}/</a>`;
      btn.disabled = false;
      return;
    }
    await pollHealthAndReload(status);
  } catch (err) {
    status.textContent = "erro: " + err.message;
    btn.disabled = false;
  }
});

async function pollHealthAndReload(status) {
  const deadline = Date.now() + 30000;
  await new Promise((r) => setTimeout(r, 1500)); // dá um respiro pro processo antigo cair
  while (Date.now() < deadline) {
    try {
      const resp = await fetch("/health", { cache: "no-store" });
      if (resp.ok) {
        status.textContent = "de volta! recarregando...";
        setTimeout(() => location.reload(), 800);
        return;
      }
    } catch (_) {
      // servidor ainda de pé de novo — continua tentando
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  status.textContent = "não voltou a tempo — confira 'helena status' no terminal.";
}

// ---------- boot ----------

boot();
