package main

// Keeping the app up to date with the studio branch on GitHub.
//
// On start the app asks GitHub for the newest commit on studio. When it is
// not the one installed, Studio's window shows a small bar offering "Update
// to the latest". Updating downloads that commit, builds the Go engine from
// it when Go is on this computer (otherwise the installed engine is kept),
// swaps the new code in, and restarts Studio. The previous code is kept as
// app.old, so a bad update can be put back by hand.

import (
	"archive/zip"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	webview2 "github.com/jchv/go-webview2"
	"golang.org/x/sys/windows"
)

const (
	repo   = "blackhole8080-darkmatter/kosika"
	branch = "studio"
)

// version is what app\VERSION holds: the commit the installed code came from.
type version struct {
	SHA     string `json:"sha"`
	Date    string `json:"date,omitempty"`
	Message string `json:"message,omitempty"`
}

func (v version) short() string {
	if len(v.SHA) >= 7 {
		return v.SHA[:7]
	}
	if v.SHA == "" {
		return "(unknown version)"
	}
	return v.SHA
}

func installedVersion(dir string) version {
	var v version
	if data, err := os.ReadFile(filepath.Join(dir, "app", "VERSION")); err == nil {
		_ = json.Unmarshal(data, &v)
	}
	return v
}

type offer struct {
	Available bool   `json:"available"`
	Busy      bool   `json:"busy"`
	Current   string `json:"current"`
	Latest    string `json:"latest"`
	Message   string `json:"message"`
	Date      string `json:"date"`
	Error     string `json:"error,omitempty"`
}

type updater struct {
	dir, data string
	log       io.Writer
	settings  *settings

	mu        sync.Mutex
	latest    version
	checked   bool
	dismissed bool
	busy      bool

	w   webview2.WebView
	srv *server
}

func firstLine(text string) string {
	line := strings.TrimSpace(strings.SplitN(text, "\n", 2)[0])
	if len(line) > 90 {
		line = line[:87] + "…"
	}
	return line
}

func (u *updater) state() offer {
	u.mu.Lock()
	defer u.mu.Unlock()
	current := installedVersion(u.dir)
	o := offer{Current: current.short(), Busy: u.busy}
	if u.checked && u.latest.SHA != "" {
		o.Latest = u.latest.short()
		o.Message = firstLine(u.latest.Message)
		o.Date = u.latest.Date
		o.Available = !u.dismissed && u.latest.SHA != current.SHA
	}
	return o
}

// latestCommit asks GitHub for the newest commit on the studio branch.
func latestCommit() (version, error) {
	request, _ := http.NewRequest("GET", "https://api.github.com/repos/"+repo+"/commits/"+branch, nil)
	request.Header.Set("Accept", "application/vnd.github+json")
	request.Header.Set("User-Agent", "kosika-studio-app")
	client := &http.Client{Timeout: 15 * time.Second}
	response, err := client.Do(request)
	if err != nil {
		return version{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return version{}, fmt.Errorf("GitHub answered %s", response.Status)
	}
	var body struct {
		SHA    string `json:"sha"`
		Commit struct {
			Message   string `json:"message"`
			Committer struct {
				Date string `json:"date"`
			} `json:"committer"`
		} `json:"commit"`
	}
	if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
		return version{}, err
	}
	return version{SHA: body.SHA, Message: body.Commit.Message, Date: body.Commit.Committer.Date}, nil
}

func (u *updater) check() {
	if os.Getenv("KOSIKA_STUDIO_NO_UPDATE") != "" {
		return
	}
	latest, err := latestCommit()
	if err != nil {
		fmt.Fprintln(u.log, "update check:", err)
		return
	}
	u.mu.Lock()
	u.latest, u.checked = latest, true
	u.mu.Unlock()
	fmt.Fprintf(u.log, "studio on GitHub is at %s; installed is %s\n", latest.short(), installedVersion(u.dir).short())
	u.push()
}

// push tells the page what is known, for when the check ends after it loaded.
func (u *updater) push() {
	state, _ := json.Marshal(u.state())
	u.w.Dispatch(func() {
		u.w.Eval("window.__kosikaOffer && window.__kosikaOffer(" + string(state) + ")")
	})
}

func (u *updater) bind(w webview2.WebView, srv *server) {
	u.w, u.srv = w, srv
	_ = w.Bind("kosikaUpdateInfo", func() offer { return u.state() })
	_ = w.Bind("kosikaUpdateLater", func() offer {
		u.mu.Lock()
		u.dismissed = true
		u.mu.Unlock()
		return u.state()
	})
	_ = w.Bind("kosikaUpdateNow", func() offer {
		u.mu.Lock()
		if u.busy || u.latest.SHA == "" {
			u.mu.Unlock()
			return u.state()
		}
		u.busy = true
		target := u.latest
		u.mu.Unlock()
		go u.apply(target)
		return u.state()
	})
}

func (u *updater) show(message string) {
	u.w.Dispatch(func() { u.w.SetHtml(waitingPage(message)) })
}

// apply downloads TARGET, swaps it in and restarts Studio.
func (u *updater) apply(target version) {
	defer func() {
		u.mu.Lock()
		u.busy = false
		u.mu.Unlock()
	}()
	u.show("Downloading Kosika Studio " + target.short() + "…")
	fresh, engine, err := u.prepare(target)
	if err != nil {
		fmt.Fprintln(u.log, "update:", err)
		u.show("The update did not finish: " + htmlEscape(err.Error()) + "<br><small>Restarting the version you had…</small>")
		time.Sleep(2 * time.Second)
		u.restart()
		return
	}
	u.show("Installing " + target.short() + "…")
	u.srv.stop()
	time.Sleep(500 * time.Millisecond) // let Windows let go of the old files
	if err := u.swap(fresh, engine); err != nil {
		fmt.Fprintln(u.log, "update swap:", err)
		u.show("The update could not be put in place: " + htmlEscape(err.Error()) + "<br><small>Restarting the version you had…</small>")
		time.Sleep(2 * time.Second)
	} else {
		fmt.Fprintln(u.log, "updated to", target.short())
	}
	u.restart()
}

func (u *updater) restart() {
	u.show("Starting Kosika Studio…")
	url, err := u.srv.start(u.dir, *u.settings, u.log)
	if err != nil {
		u.show("Kosika Studio could not start: " + htmlEscape(err.Error()))
		return
	}
	u.w.Dispatch(func() { u.w.Navigate(url) })
}

// prepare downloads and unpacks TARGET and builds its engine. It returns the
// unpacked code folder and the new engine ("" when the old one is kept).
func (u *updater) prepare(target version) (string, string, error) {
	work := filepath.Join(u.data, "updates", target.SHA)
	_ = os.RemoveAll(work)
	if err := os.MkdirAll(work, 0o755); err != nil {
		return "", "", err
	}
	archive := filepath.Join(work, "source.zip")
	if err := download("https://codeload.github.com/"+repo+"/zip/"+target.SHA, archive); err != nil {
		return "", "", err
	}
	code := filepath.Join(work, "app")
	if err := unzipStripped(archive, code); err != nil {
		return "", "", err
	}
	_ = os.Remove(archive)
	if !fileExists(filepath.Join(code, "kosika", "__init__.py")) {
		return "", "", fmt.Errorf("the download does not hold Kosika's code")
	}
	stamp, _ := json.MarshalIndent(target, "", "  ")
	if err := os.WriteFile(filepath.Join(code, "VERSION"), stamp, 0o644); err != nil {
		return "", "", err
	}
	engine := ""
	if goTool, err := exec.LookPath("go"); err == nil && fileExists(filepath.Join(code, "native", "go.mod")) {
		u.show("Building the Go engine for " + target.short() + "…")
		out := filepath.Join(work, "kosika-native.exe")
		build := exec.Command(goTool, "build", "-trimpath", "-o", out, ".")
		build.Dir = filepath.Join(code, "native")
		build.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: windows.CREATE_NO_WINDOW}
		build.Stdout, build.Stderr = u.log, u.log
		// Go's scratch files go beside the download, not in the temp folder.
		build.Env = append(os.Environ(), "GOTMPDIR="+work, "CGO_ENABLED=0")
		if err := build.Run(); err != nil {
			fmt.Fprintln(u.log, "engine build failed, keeping the installed engine:", err)
		} else {
			engine = out
		}
	} else {
		fmt.Fprintln(u.log, "Go is not on this computer; keeping the installed engine")
	}
	return code, engine, nil
}

func (u *updater) swap(fresh, engine string) error {
	app := filepath.Join(u.dir, "app")
	old := filepath.Join(u.dir, "app.old")
	_ = os.RemoveAll(old)
	if err := os.Rename(app, old); err != nil {
		return err
	}
	if err := os.Rename(fresh, app); err != nil {
		_ = os.Rename(old, app)
		return err
	}
	if engine != "" {
		current := filepath.Join(u.dir, "engine", "kosika-native.exe")
		_ = os.Remove(current + ".old")
		_ = os.Rename(current, current+".old")
		if err := os.Rename(engine, current); err != nil {
			_ = os.Rename(current+".old", current)
			return err
		}
	}
	return nil
}

func download(url, path string) error {
	request, _ := http.NewRequest("GET", url, nil)
	request.Header.Set("User-Agent", "kosika-studio-app")
	client := &http.Client{Timeout: 5 * time.Minute}
	response, err := client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return fmt.Errorf("GitHub answered %s", response.Status)
	}
	file, err := os.Create(path)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = io.Copy(file, response.Body)
	return err
}

// unzipStripped unpacks a GitHub source archive into DEST, dropping the
// archive's single top folder.
func unzipStripped(archive, dest string) error {
	reader, err := zip.OpenReader(archive)
	if err != nil {
		return err
	}
	defer reader.Close()
	root, _ := filepath.Abs(dest)
	for _, file := range reader.File {
		parts := strings.SplitN(file.Name, "/", 2)
		if len(parts) < 2 || parts[1] == "" {
			continue
		}
		target := filepath.Join(root, filepath.FromSlash(parts[1]))
		if !strings.HasPrefix(target, root+string(os.PathSeparator)) {
			return fmt.Errorf("the archive holds an unsafe path: %s", file.Name)
		}
		if file.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		in, err := file.Open()
		if err != nil {
			return err
		}
		out, err := os.Create(target)
		if err != nil {
			in.Close()
			return err
		}
		_, err = io.Copy(out, in)
		in.Close()
		out.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

// updateBarScript runs in every page the window shows. On Studio's page it
// puts a small card in the corner when a newer studio is ready.
const updateBarScript = `(function(){
  if (location.protocol !== 'http:') return;
  function card(info){
    var old = document.getElementById('kosika-update'); if (old) old.remove();
    if (!info || !info.available) return;
    var box = document.createElement('div');
    box.id = 'kosika-update';
    box.setAttribute('role','status');
    box.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:2147483647;max-width:360px;' +
      'background:var(--panel,#fff);color:var(--ink,#1d1b18);border:1px solid var(--line,#e3dfd6);' +
      'border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.14);padding:14px 16px;font:13px/1.45 "Segoe UI",system-ui,sans-serif';
    var head = document.createElement('div');
    head.style.cssText = 'font-weight:600;margin-bottom:4px';
    head.textContent = 'A newer Kosika Studio is ready';
    var body = document.createElement('div');
    body.style.cssText = 'color:var(--soft,#6c675f);margin-bottom:12px';
    body.textContent = info.current + ' → ' + info.latest + (info.message ? ': ' + info.message : '');
    var row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;justify-content:flex-end';
    var later = document.createElement('button');
    later.textContent = 'Not now';
    later.style.cssText = 'border:1px solid var(--line,#e3dfd6);background:transparent;color:inherit;border-radius:8px;padding:6px 12px;cursor:pointer;font:inherit';
    later.onclick = function(){ window.kosikaUpdateLater().then(card); };
    var go = document.createElement('button');
    go.textContent = 'Update to the latest';
    go.style.cssText = 'border:0;background:var(--accent,#b4532a);color:#fff;border-radius:8px;padding:6px 12px;cursor:pointer;font:inherit;font-weight:600';
    go.onclick = function(){ go.disabled = later.disabled = true; go.textContent = 'Updating…'; window.kosikaUpdateNow(); };
    row.appendChild(later); row.appendChild(go);
    box.appendChild(head); box.appendChild(body); box.appendChild(row);
    document.body.appendChild(box);
  }
  window.__kosikaOffer = card;
  function ask(){ if (window.kosikaUpdateInfo) window.kosikaUpdateInfo().then(card); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ask); else ask();
})();`
