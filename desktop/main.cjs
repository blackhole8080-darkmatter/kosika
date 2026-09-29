"use strict";
const { app, BrowserWindow, dialog, session } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

const PORT = 8793;
const ORIGIN = `http://127.0.0.1:${PORT}`;
// Keep packaged drafts separate from the earlier browser-based launcher.
app.setPath("userData", path.join(app.getPath("appData"), "KosikaApp"));
let host;
let window;
let quitting = false;
let failed = false;

function fail(message) {
  if (failed || quitting) return;
  failed = true;
  dialog.showErrorBox("Kosika could not start", message);
  app.quit();
}

function startHost() {
  return new Promise((resolve, reject) => {
    const executable = path.join(process.resourcesPath, "host", "KosikaHost.exe");
    host = spawn(executable, ["--port", String(PORT)], {
      cwd: path.dirname(executable),
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PYTHONUTF8: "1" },
    });
    let output = "";
    let errors = "";
    let ready = false;
    const timer = setTimeout(() => reject(new Error("The local engine did not start within 30 seconds.")), 30000);
    host.stderr.on("data", chunk => { errors = (errors + chunk).slice(-8000); });
    host.stdin.on("error", () => {});
    host.on("error", error => { clearTimeout(timer); reject(error); });
    host.on("exit", code => {
      clearTimeout(timer);
      const message = `The local engine stopped (${code}).\n${errors}`;
      if (!ready) reject(new Error(message));
      else if (!quitting) fail(message);
    });
    host.stdout.on("data", chunk => {
      if (ready) return;
      output += chunk;
      if (output.length > 4096) {
        clearTimeout(timer);
        reject(new Error("Invalid startup response from the local engine."));
        return;
      }
      const newline = output.indexOf("\n");
      if (newline < 0) return;
      try {
        const status = JSON.parse(output.slice(0, newline));
        if (status.ready !== true || status.port !== PORT) throw new Error("Unexpected local engine address.");
        ready = true;
        clearTimeout(timer);
        resolve();
      } catch (error) { clearTimeout(timer); reject(error); }
    });
  });
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (window) {
      if (window.isMinimized()) window.restore();
      window.show();
      window.focus();
    }
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", event => {
    if (quitting) return;
    quitting = true;
    if (host && host.exitCode === null && host.pid) {
      event.preventDefault();
      const timeout = setTimeout(() => { host.kill(); app.exit(); }, 5000);
      host.once("exit", () => { clearTimeout(timeout); app.exit(); });
      host.stdin.end();
    }
  });
  app.whenReady().then(async () => {
    await startHost();
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    session.defaultSession.setPermissionCheckHandler(() => false);
    session.defaultSession.webRequest.onBeforeRequest((details, callback) => {
      const url = new URL(details.url);
      callback({ cancel: url.origin !== ORIGIN && !["blob:", "data:"].includes(url.protocol) });
    });
    window = new BrowserWindow({
      title: "Kosika", width: 1440, height: 950, minWidth: 1000, minHeight: 700,
      show: false, backgroundColor: "#090c14", autoHideMenuBar: true,
      webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true },
    });
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event, url) => {
      if (new URL(url).origin !== ORIGIN) event.preventDefault();
    });
    await window.loadURL(ORIGIN);
    window.show();
    // Build verification only: capture the packaged UI and exit cleanly.
    const smoke = process.argv.find(arg => arg.startsWith("--smoke-output="));
    if (smoke) {
      const output = path.resolve(smoke.slice("--smoke-output=".length));
      await new Promise(resolve => setTimeout(resolve, 1000));
      const title = await window.webContents.executeJavaScript("document.title");
      const capture = await window.webContents.capturePage();
      fs.writeFileSync(output + ".png", capture.toPNG());
      fs.writeFileSync(output + ".json", JSON.stringify({ title, packaged: app.isPackaged, pythonBundled: true, url: window.webContents.getURL() }));
      app.quit();
    }
  }).catch(error => fail(error.message));
}
