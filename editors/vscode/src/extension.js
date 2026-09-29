const vscode = require("vscode");
const { execFile, spawn } = require("child_process");
const path = require("path");

const output = vscode.window.createOutputChannel("Kosika");
let diagnosticCollection;
let lspClient;
const openDocuments = new Set();
const pendingSyncs = new Map();
const documentGenerations = new Map();
const SYNC_DELAY_MS = 150;

function activeKosikaFile() {
  const document = vscode.window.activeTextEditor?.document;
  if (!document || document.languageId !== "kosika") {
    throw new Error("Open a Kosika .kos file first.");
  }
  return document.uri.fsPath;
}

function workspaceRoot(filePath) {
  return vscode.workspace.getWorkspaceFolder(vscode.Uri.file(filePath))?.uri.fsPath
    || path.dirname(filePath);
}

function configuredWorkspaceRoot() {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || process.cwd();
}

function skillArguments(root) {
  const configured = vscode.workspace
    .getConfiguration("kosika")
    .get("skillDirectories", []);
  if (!Array.isArray(configured)) {
    return [];
  }
  return configured.flatMap((directory) => [
    "--skill-dir",
    path.isAbsolute(directory) ? directory : path.join(root, directory),
  ]);
}

function modelArguments() {
  const configuration = vscode.workspace.getConfiguration("kosika");
  const model = configuration.get("modelName", "").trim();
  if (!model) {
    return [];
  }
  return [
    "--model-url",
    configuration.get("modelUrl", "http://localhost:11434/v1"),
    "--model",
    model,
  ];
}

function runKosika(filePath, argumentsList) {
  const configuration = vscode.workspace.getConfiguration("kosika");
  const pythonPath = configuration.get("pythonPath", "python");
  const root = workspaceRoot(filePath);
  return new Promise((resolve, reject) => {
    execFile(
      pythonPath,
      ["-m", "kosika.cli", ...argumentsList],
      { cwd: root, maxBuffer: 4 * 1024 * 1024 },
      (error, stdout, stderr) => {
        const result = `${stdout}${stderr ? `\n${stderr}` : ""}`.trim();
        output.appendLine(`$ ${pythonPath} -m kosika.cli ${argumentsList.join(" ")}`);
        if (result) {
          output.appendLine(result);
        }
        if (error) {
          reject(new Error(result || error.message));
          return;
        }
        resolve(stdout.trim());
      },
    );
  });
}

class KosikaLspClient {
  constructor(pythonPath, root, onNotification) {
    this.pending = new Map();
    this.activeRequests = new Map();
    this.nextId = 1;
    this.onNotification = onNotification;
    this.child = spawn(pythonPath, ["-m", "kosika.lsp"], {
      cwd: root,
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.child.stdout.on("data", (chunk) => this.read(chunk));
    this.child.stderr.on("data", (chunk) => {
      output.appendLine(`[kosika-lsp] ${chunk.toString().trimEnd()}`);
    });
    this.child.on("error", (error) => {
      output.appendLine(`[kosika-lsp] ${error.message}`);
      this.rejectPending(error);
    });
    this.child.on("exit", (code, signal) => {
      if (code !== 0 && signal !== "SIGTERM") {
        output.appendLine(`[kosika-lsp] exited with code ${code}`);
      }
      this.rejectPending(new Error("Kosika LSP process exited"));
    });
    this.ready = this.initialize();
  }

  async initialize() {
    const rootUri = vscode.Uri.file(configuredWorkspaceRoot()).toString();
    await this.request("initialize", {
      processId: process.pid,
      rootUri,
      capabilities: {},
      workspaceFolders: [{ uri: rootUri, name: path.basename(configuredWorkspaceRoot()) }],
    });
    this.notify("initialized", {});
  }

  send(message) {
    const body = JSON.stringify(message);
    const header = `Content-Length: ${Buffer.byteLength(body, "utf8")}\r\n\r\n`;
    this.child.stdin.write(header + body, "utf8");
  }

  request(method, params, key = null) {
    if (key && this.activeRequests.has(key)) {
      this.cancelRequest(this.activeRequests.get(key));
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, key });
      if (key) {
        this.activeRequests.set(key, id);
      }
      this.send({ jsonrpc: "2.0", id, method, params });
    });
  }

  cancelRequest(id) {
    const pending = this.pending.get(id);
    if (!pending) {
      return;
    }
    this.pending.delete(id);
    if (pending.key && this.activeRequests.get(pending.key) === id) {
      this.activeRequests.delete(pending.key);
    }
    this.notify("$/cancelRequest", { id });
    pending.resolve(null);
  }

  notify(method, params) {
    this.send({ jsonrpc: "2.0", method, params });
  }

  read(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    while (true) {
      const separator = this.buffer.indexOf("\r\n\r\n");
      if (separator < 0) {
        return;
      }
      const header = this.buffer.subarray(0, separator).toString("ascii");
      const match = /Content-Length:\s*(\d+)/i.exec(header);
      if (!match) {
        this.rejectPending(new Error("Kosika LSP returned an invalid header"));
        return;
      }
      const length = Number(match[1]);
      const start = separator + 4;
      if (this.buffer.length < start + length) {
        return;
      }
      const body = this.buffer.subarray(start, start + length).toString("utf8");
      this.buffer = this.buffer.subarray(start + length);
      this.handle(JSON.parse(body));
    }
  }

  handle(message) {
    if (message.id !== undefined && this.pending.has(message.id)) {
      const pending = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (pending.key && this.activeRequests.get(pending.key) === message.id) {
        this.activeRequests.delete(pending.key);
      }
      if (message.error) {
        pending.reject(new Error(message.error.message || "Kosika LSP request failed"));
      } else {
        pending.resolve(message.result);
      }
      return;
    }
    if (message.method) {
      this.onNotification(message.method, message.params || {});
    }
  }

  rejectPending(error) {
    for (const pending of this.pending.values()) {
      pending.reject(error);
    }
    this.pending.clear();
    this.activeRequests.clear();
  }

  async stop() {
    if (!this.child || this.child.killed) {
      return;
    }
    try {
      await this.ready;
      await this.request("shutdown", null);
      this.notify("exit", null);
    } catch (error) {
      output.appendLine(`[kosika-lsp] shutdown: ${error.message}`);
    } finally {
      this.child.kill();
    }
  }

  dispose() {
    void this.stop();
  }
}

function toRange(value) {
  return new vscode.Range(
    value.start.line,
    value.start.character,
    value.end.line,
    value.end.character,
  );
}

function completionKind(kind) {
  return {
    keyword: vscode.CompletionItemKind.Keyword,
    struct: vscode.CompletionItemKind.Struct,
    function: vscode.CompletionItemKind.Function,
    tool: vscode.CompletionItemKind.Method,
    agent: vscode.CompletionItemKind.Class,
    swarm: vscode.CompletionItemKind.Class,
    model: vscode.CompletionItemKind.Interface,
    workflow: vscode.CompletionItemKind.Function,
  }[kind] || vscode.CompletionItemKind.Text;
}

function updateDiagnostics(params) {
  const uri = vscode.Uri.parse(params.uri);
  const diagnostics = (params.diagnostics || []).map((item) => {
    const diagnostic = new vscode.Diagnostic(
      toRange(item.range),
      item.message,
      item.severity === 2
        ? vscode.DiagnosticSeverity.Warning
        : vscode.DiagnosticSeverity.Error,
    );
    diagnostic.source = item.source || "Kosika";
    return diagnostic;
  });
  diagnosticCollection.set(uri, diagnostics);
}

function currentDocument(uri) {
  return vscode.workspace.textDocuments.find(
    (document) => document.uri.toString() === uri,
  );
}

function bumpDocumentGeneration(uri) {
  documentGenerations.set(uri, (documentGenerations.get(uri) || 0) + 1);
}

function sendDocument(uri, opening = false) {
  const document = currentDocument(uri);
  if (!document) {
    return;
  }
  if (opening || !openDocuments.has(uri)) {
    openDocuments.add(uri);
    lspClient.notify("textDocument/didOpen", {
      textDocument: {
        uri,
        languageId: "kosika",
        version: document.version,
        text: document.getText(),
      },
    });
    return;
  }
  lspClient.notify("textDocument/didChange", {
    textDocument: { uri, version: document.version },
    contentChanges: [{ text: document.getText() }],
  });
}

function finishPendingSync(uri, pending) {
  if (pendingSyncs.get(uri) !== pending) {
    return;
  }
  pendingSyncs.delete(uri);
  pending.resolve();
}

function flushDocument(uri) {
  const pending = pendingSyncs.get(uri);
  if (!pending) {
    return Promise.resolve();
  }
  if (pending.timer) {
    clearTimeout(pending.timer);
    pending.timer = null;
  }
  lspClient.ready.then(() => sendDocument(uri)).catch((error) => {
    output.appendLine(`[kosika-lsp] sync: ${error.message}`);
  }).then(() => finishPendingSync(uri, pending));
  return pending.promise;
}

function waitForPendingSync(uri) {
  return pendingSyncs.get(uri)?.promise || Promise.resolve();
}

function lspRequest(method, params, key = null) {
  if (!lspClient) {
    return Promise.resolve(null);
  }
  return lspClient.ready.then(() => lspClient.request(method, params, key));
}

function lspRequestForDocument(document, method, params) {
  const uri = document.uri.toString();
  return waitForPendingSync(uri).then(() => {
    const generation = documentGenerations.get(uri) || 0;
    return lspRequest(method, params, `${uri}:${method}`).then((result) => (
      documentGenerations.get(uri) === generation ? result : null
    ));
  });
}

function syncDocument(document) {
  if (!lspClient || document.languageId !== "kosika") {
    return;
  }
  const uri = document.uri.toString();
  bumpDocumentGeneration(uri);
  if (!openDocuments.has(uri)) {
    openDocuments.add(uri);
    lspClient.ready.then(() => sendDocument(uri, true)).catch((error) => {
      output.appendLine(`[kosika-lsp] sync: ${error.message}`);
    });
    return;
  }
  let pending = pendingSyncs.get(uri);
  if (!pending) {
    let resolve;
    const promise = new Promise((done) => {
      resolve = done;
    });
    pending = { promise, resolve, timer: null };
    pendingSyncs.set(uri, pending);
  }
  if (pending.timer) {
    clearTimeout(pending.timer);
  }
  pending.timer = setTimeout(() => {
    pending.timer = null;
    lspClient.ready.then(() => sendDocument(uri)).catch((error) => {
      output.appendLine(`[kosika-lsp] sync: ${error.message}`);
    }).then(() => finishPendingSync(uri, pending));
  }, SYNC_DELAY_MS);
}

function saveDocument(document) {
  if (!lspClient || document.languageId !== "kosika") {
    return;
  }
  const uri = document.uri.toString();
  flushDocument(uri).then(() => lspClient.ready).then(() => {
    lspClient.notify("textDocument/didSave", { textDocument: { uri } });
  }).catch((error) => output.appendLine(`[kosika-lsp] save: ${error.message}`));
}

function closeDocument(document) {
  if (!lspClient || document.languageId !== "kosika") {
    return;
  }
  const uri = document.uri.toString();
  const pending = pendingSyncs.get(uri);
  if (pending) {
    if (pending.timer) {
      clearTimeout(pending.timer);
    }
    pendingSyncs.delete(uri);
    pending.resolve();
  }
  bumpDocumentGeneration(uri);
  openDocuments.delete(uri);
  documentGenerations.delete(uri);
  lspClient.ready.then(() => {
    lspClient.notify("textDocument/didClose", { textDocument: { uri } });
  });
}

function registerWorkspaceWatcher(context) {
  const pattern = new vscode.RelativePattern(
    configuredWorkspaceRoot(),
    "**/*.kos",
  );
  const watcher = vscode.workspace.createFileSystemWatcher(pattern);
  const notify = (uri, type) => {
    if (!lspClient) {
      return;
    }
    lspClient.ready.then(() => {
      lspClient.notify("workspace/didChangeWatchedFiles", {
        changes: [{ uri: uri.toString(), type }],
      });
    }).catch((error) => output.appendLine(`[kosika-lsp] watch: ${error.message}`));
  };
  context.subscriptions.push(
    watcher,
    watcher.onDidCreate((uri) => notify(uri, 1)),
    watcher.onDidChange((uri) => notify(uri, 2)),
    watcher.onDidDelete((uri) => notify(uri, 3)),
  );
}

function registerLanguageFeatures(context) {
  const selector = { language: "kosika", scheme: "file" };
  context.subscriptions.push(
    vscode.languages.registerDocumentSymbolProvider(selector, {
      async provideDocumentSymbols(document) {
        const items = await lspRequestForDocument(document, "textDocument/documentSymbol", {
          textDocument: { uri: document.uri.toString() },
        });
        return (items || []).map((item) => new vscode.DocumentSymbol(
          item.name,
          item.detail || "",
          completionKind(item.kind),
          toRange(item.range),
          toRange(item.selectionRange),
        ));
      },
    }),
    vscode.languages.registerHoverProvider(selector, {
      async provideHover(document, position) {
        const item = await lspRequestForDocument(document, "textDocument/hover", {
          textDocument: { uri: document.uri.toString() },
          position,
        });
        if (!item) {
          return null;
        }
        return new vscode.Hover(
          new vscode.MarkdownString(item.contents.value || ""),
          item.range ? toRange(item.range) : undefined,
        );
      },
    }),
    vscode.languages.registerDefinitionProvider(selector, {
      async provideDefinition(document, position) {
        const items = await lspRequestForDocument(document, "textDocument/definition", {
          textDocument: { uri: document.uri.toString() },
          position,
        });
        return (items || []).map((item) => new vscode.Location(
          vscode.Uri.parse(item.uri),
          toRange(item.range),
        ));
      },
    }),
    vscode.languages.registerReferenceProvider(selector, {
      async provideReferences(document, position, context) {
        const items = await lspRequestForDocument(document, "textDocument/references", {
          textDocument: { uri: document.uri.toString() },
          position,
          context: { includeDeclaration: context.includeDeclaration },
        });
        return (items || []).map((item) => new vscode.Location(
          vscode.Uri.parse(item.uri),
          toRange(item.range),
        ));
      },
    }),
    vscode.languages.registerCompletionItemProvider(selector, {
      async provideCompletionItems(document, position) {
        const result = await lspRequestForDocument(document, "textDocument/completion", {
          textDocument: { uri: document.uri.toString() },
          position,
        });
        return (result?.items || []).map((item) => {
          const completion = new vscode.CompletionItem(
            item.label,
            completionKind(item.kind),
          );
          completion.detail = item.detail;
          return completion;
        });
      },
    }),
  );
}

async function checkFile() {
  try {
    const filePath = activeKosikaFile();
    await runKosika(filePath, ["check", filePath]);
    vscode.window.showInformationMessage("Kosika check passed.");
    output.show(true);
  } catch (error) {
    vscode.window.showErrorMessage(`Kosika check failed: ${error.message}`);
    output.show(true);
  }
}

async function explainFile() {
  try {
    const filePath = activeKosikaFile();
    const task = await vscode.window.showInputBox({
      prompt: "What should Kosika explain?",
      value: "Explain this Kosika file and its agent permissions.",
    });
    if (!task) {
      return;
    }
    await runKosika(filePath, [
      "assistant",
      filePath,
      "--task",
      task,
      "--json",
      ...modelArguments(),
    ]);
    vscode.window.showInformationMessage("Kosika explanation is available in the Kosika output panel.");
    output.show(true);
  } catch (error) {
    vscode.window.showErrorMessage(`Kosika explanation failed: ${error.message}`);
    output.show(true);
  }
}

async function generateMindmap() {
  try {
    const filePath = activeKosikaFile();
    const mermaid = await runKosika(filePath, ["mindmap", filePath, "--format", "mermaid"]);
    const document = await vscode.workspace.openTextDocument({
      language: "markdown",
      content: `\`\`\`mermaid\n${mermaid}\n\`\`\`\n`,
    });
    await vscode.window.showTextDocument(document, { preview: false });
  } catch (error) {
    vscode.window.showErrorMessage(`Kosika mindmap failed: ${error.message}`);
    output.show(true);
  }
}

async function generateUnifiedMindmap() {
  try {
    const filePath = activeKosikaFile();
    const root = workspaceRoot(filePath);
    const mermaid = await runKosika(filePath, [
      "unified",
      filePath,
      "--project",
      root,
      "--format",
      "mermaid",
    ]);
    const document = await vscode.workspace.openTextDocument({
      language: "markdown",
      content: `\`\`\`mermaid\n${mermaid}\n\`\`\`\n`,
    });
    await vscode.window.showTextDocument(document, { preview: false });
  } catch (error) {
    vscode.window.showErrorMessage(`Kosika unified mindmap failed: ${error.message}`);
    output.show(true);
  }
}

async function inspectSkills() {
  try {
    const filePath = activeKosikaFile();
    const task = await vscode.window.showInputBox({
      prompt: "Task used for automatic skill selection",
      value: "Review this Kosika workflow for safety and clarity.",
    });
    if (!task) {
      return;
    }
    const root = workspaceRoot(filePath);
    await runKosika(filePath, [
      "skills",
      "--task",
      task,
      "--format",
      "text",
      ...skillArguments(root),
    ]);
    vscode.window.showInformationMessage("Kosika skill selection is available in the Kosika output panel.");
    output.show(true);
  } catch (error) {
    vscode.window.showErrorMessage(`Kosika skill selection failed: ${error.message}`);
    output.show(true);
  }
}

function activate(context) {
  diagnosticCollection = vscode.languages.createDiagnosticCollection("kosika");
  const configuration = vscode.workspace.getConfiguration("kosika");
  lspClient = new KosikaLspClient(
    configuration.get("pythonPath", "python"),
    configuredWorkspaceRoot(),
    (method, params) => {
      if (method === "textDocument/publishDiagnostics") {
        updateDiagnostics(params);
      }
    },
  );
  context.subscriptions.push(
    output,
    diagnosticCollection,
    { dispose: () => lspClient?.dispose() },
    vscode.workspace.onDidOpenTextDocument(syncDocument),
    vscode.workspace.onDidChangeTextDocument((event) => syncDocument(event.document)),
    vscode.workspace.onDidSaveTextDocument(saveDocument),
    vscode.workspace.onDidCloseTextDocument(closeDocument),
    vscode.commands.registerCommand("kosika.check", checkFile),
    vscode.commands.registerCommand("kosika.explain", explainFile),
    vscode.commands.registerCommand("kosika.mindmap", generateMindmap),
    vscode.commands.registerCommand("kosika.unifiedMindmap", generateUnifiedMindmap),
    vscode.commands.registerCommand("kosika.skills", inspectSkills),
  );
  registerWorkspaceWatcher(context);
  registerLanguageFeatures(context);
  for (const document of vscode.workspace.textDocuments) {
    syncDocument(document);
  }
}

function deactivate() {
  return lspClient?.stop();
}

module.exports = { activate, deactivate };
