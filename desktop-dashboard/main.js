// Casca fina: só abre uma janela nativa apontando pra /dashboard do servidor
// Flask da Helena. Nenhuma lógica de dado mora aqui — tudo vem da API REST
// (a própria página faz fetch(), autentica, faz polling; ver
// app/static/dashboard.js no repositório principal).
const { app, BrowserWindow } = require("electron");

function resolveUrl() {
  const idx = process.argv.indexOf("--url");
  if (idx !== -1 && process.argv[idx + 1]) {
    return process.argv[idx + 1];
  }
  return "http://127.0.0.1:5000/dashboard";
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    title: "Helena — Painel",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.setMenuBarVisibility(false);
  win.loadURL(resolveUrl());
}

app.whenReady().then(createWindow);

// fecha o processo quando a janela fecha (não fica um Electron órfão
// rodando sem janela nenhuma) — é assim que 'fechar_dashboard' do lado
// Python também percebe que não há mais nada pra matar.
app.on("window-all-closed", () => {
  app.quit();
});
