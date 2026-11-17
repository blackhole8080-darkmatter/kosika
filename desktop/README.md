# Kosika Studio, the installed app

`desktop/` builds **Kosika Studio Setup.exe**: one file that installs Studio as
a real Windows app, with its own window, icon, Start menu entry and
uninstaller, and the runtime it needs bundled inside.

```
python desktop/build.py        # on Windows, with Go, git and Python 3.12
```

The result is `desktop/build/Kosika Studio Setup.exe` (about 37 MB).

## What gets installed

For this person only, so no administrator is needed:

| Where | What |
| --- | --- |
| `%LOCALAPPDATA%\Programs\Kosika Studio\Kosika Studio.exe` | the native launcher (`desktop/app`, Go) |
| `...\app\` | Studio's code, a copy of this repo, stamped with `app\VERSION` |
| `...\runtime\python\` | a private Python 3.12 (embeddable build, plus tkinter, tree-sitter, pytest) |
| `...\engine\kosika-native.exe` | the Go engine, handed to Studio as `KOSIKA_NATIVE` |
| `...\uninstall.exe` | the uninstaller (`desktop/setup` built without its payload) |
| Start menu, Desktop | "Kosika Studio" shortcuts |
| Apps & features | an entry with an Uninstall button (`HKCU\...\Uninstall\KosikaStudio`) |
| `%LOCALAPPDATA%\Kosika Studio\` | the log, `settings.json` (`agents_home`), the window's data; kept on uninstall |

## How it runs

The launcher opens a native window whose page is drawn by Windows' WebView2
control, shows a start-up screen, runs `python -m kosika.cli start
--no-browser` from the bundled runtime with no console, and shows Studio when
the server prints its address. The server sits in a job object, so it stops
when the window closes, however the app ends.

`KOSIKA_AGENTS_HOME` comes from the environment or `settings.json`; the
installer's `--agents PATH` writes it.

## Updates

On start the launcher asks GitHub for the newest commit on `studio`. When it
differs from `app\VERSION`, Studio's window shows **Update to the latest**.
Updating downloads that commit, builds the Go engine from it when Go is on the
computer (the installed engine is kept otherwise), swaps the code in (the old
copy stays as `app.old`), and restarts Studio. `KOSIKA_STUDIO_NO_UPDATE=1`
turns the check off.

## Installer flags

`/S` asks nothing · `--dir D` installs elsewhere · `--agents PATH` · `--open`
· `--no-shortcuts` and `--no-registry` for a scratch install ·
`KOSIKA_STUDIO_DATA` moves the settings folder (for tries).
