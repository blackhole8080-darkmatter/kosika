# Kosika Windows installer

The packaged app bundles Electron, Kosika and its Python runtime. End users do
not need Python, Node, a browser, or this repository. Build on Windows x64:

```powershell
python -m venv desktop/.venv
desktop/.venv/Scripts/python -m pip install -r desktop/requirements-build.txt
cd desktop
npm ci
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1
```

The installer is `release/Kosika-Setup-0.1.0-x64.exe`. It installs per user,
creates **Kosika App** shortcuts (distinct from the old launcher), and provides
normal Windows uninstall support. This preview is unsigned; no certificate or
automatic updater is configured. Do not describe it as a signed production release.

The app owns its bundled engine process and shuts it down when the window closes.
It uses loopback port 8793 and refuses to use an unrelated service if that port
is occupied. The older browser launcher on port 8792 can coexist.

Drafts live in the Electron profile at `%APPDATA%/KosikaApp` and survive ordinary
uninstall/reinstall. Export important work as a backup. Old browser drafts are
not migrated automatically; use workspace export/import to move them.

The window is sandboxed, has no Node integration or privileged preload bridge,
denies permission prompts and external navigation, and uses the existing API
Host/Origin/token checks. Fixture mode is the default. This installer does not
bundle an AI model, enable live providers, or grant external tools.

Verification:

```powershell
# From repository root, exercise the actual bundled engine without source access.
$env:KOSIKA_TEST_HOST = (Resolve-Path desktop/host-dist/KosikaHost/KosikaHost.exe).Path
python -m unittest discover -s tests -p test_packaged_host.py -v
Remove-Item Env:KOSIKA_TEST_HOST
# Launch the built app normally, or capture its window and exit for a build check.
desktop/release/win-unpacked/Kosika.exe --smoke-output=C:/absolute/path/smoke
```

The smoke check writes a PNG and JSON receipt. The installer should additionally
be exercised with install, launch, close, and uninstall on Windows before distribution.
