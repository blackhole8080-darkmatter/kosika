// Kosika Studio, the installed app.
//
// This is the program the Start menu and Desktop icons open. It is a native
// Windows program with a window of its own; the page inside it is drawn by
// the system's WebView2 control, not by a browser. It starts Studio's server
// from the runtime installed beside it (a private Python and the Go engine),
// shows Studio when the server is ready, and stops the server when the window
// closes. On start it asks GitHub whether the studio branch has moved on, and
// if it has, offers "Update to the latest" inside the window.
//
// Layout of an install (see desktop/README.md):
//
//	Kosika Studio.exe     this program
//	app\                  Studio's code: a copy of the kosika repo, and VERSION
//	runtime\python\       the private Python that runs it
//	engine\kosika-native.exe  the Go engine
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"

	webview2 "github.com/jchv/go-webview2"
	"golang.org/x/sys/windows"
)

const title = "Kosika Studio"

// installDir is the folder this program sits in.
func installDir() string {
	exe, err := os.Executable()
	if err != nil {
		return "."
	}
	return filepath.Dir(exe)
}

// dataDir is where the app keeps its log, settings and the window's own data.
func dataDir() string {
	if told := os.Getenv("KOSIKA_STUDIO_DATA"); told != "" {
		_ = os.MkdirAll(told, 0o755)
		return told
	}
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		base, _ = os.UserHomeDir()
	}
	dir := filepath.Join(base, "Kosika Studio")
	_ = os.MkdirAll(dir, 0o755)
	return dir
}

type settings struct {
	AgentsHome string `json:"agents_home,omitempty"`
	SkipUpdate string `json:"skip_update,omitempty"`
}

func loadSettings() settings {
	var s settings
	if data, err := os.ReadFile(filepath.Join(dataDir(), "settings.json")); err == nil {
		_ = json.Unmarshal(data, &s)
	}
	return s
}

func saveSettings(s settings) {
	data, _ := json.MarshalIndent(s, "", "  ")
	_ = os.WriteFile(filepath.Join(dataDir(), "settings.json"), data, 0o644)
}

func messageBox(text string) {
	t, _ := windows.UTF16PtrFromString(title)
	m, _ := windows.UTF16PtrFromString(text)
	windows.MessageBox(0, m, t, windows.MB_OK|windows.MB_ICONERROR)
}

// ------------------------------------------------------------------ the server

// server is Studio's Python server, run as a child that cannot outlive us.
type server struct {
	mu  sync.Mutex
	cmd *exec.Cmd
	job windows.Handle
}

// newJob makes a job object that kills every process in it when the app's
// last handle to it closes, so the server never outlives the window, even
// when the app is ended from Task Manager.
func newJob() windows.Handle {
	job, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		return 0
	}
	info := windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION{}
	info.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	_, _ = windows.SetInformationJobObject(job, windows.JobObjectExtendedLimitInformation,
		uintptr(unsafe.Pointer(&info)), uint32(unsafe.Sizeof(info)))
	return job
}

func (s *server) environment(dir string, set settings) []string {
	env := os.Environ()
	add := func(key, value string) {
		for i, kv := range env {
			if strings.HasPrefix(strings.ToUpper(kv), strings.ToUpper(key)+"=") {
				env[i] = key + "=" + value
				return
			}
		}
		env = append(env, key+"="+value)
	}
	app := filepath.Join(dir, "app")
	add("KOSIKA_HOME", app)
	add("KOSIKA_STUDIO_INSTALLED", dir)
	if engine := filepath.Join(dir, "engine", "kosika-native.exe"); fileExists(engine) {
		add("KOSIKA_NATIVE", engine)
	}
	if set.AgentsHome != "" && os.Getenv("KOSIKA_AGENTS_HOME") == "" {
		add("KOSIKA_AGENTS_HOME", set.AgentsHome)
	}
	add("PYTHONUTF8", "1")
	add("PYTHONUNBUFFERED", "1") // the address is read from its output as it is printed
	add("PYTHONDONTWRITEBYTECODE", "1")
	return env
}

// start runs `python -m kosika.cli start --no-browser` and returns Studio's
// address once the server has printed it.
func (s *server) start(dir string, set settings, log io.Writer) (string, error) {
	python := filepath.Join(dir, "runtime", "python", "python.exe")
	if !fileExists(python) {
		return "", fmt.Errorf("the app's Python is missing: %s", python)
	}
	cmd := exec.Command(python, "-m", "kosika.cli", "start", "--no-browser")
	cmd.Dir = filepath.Join(dir, "app")
	cmd.Env = s.environment(dir, set)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: windows.CREATE_NO_WINDOW}
	out, err := cmd.StdoutPipe()
	if err != nil {
		return "", err
	}
	cmd.Stderr = log
	if err := cmd.Start(); err != nil {
		return "", err
	}
	s.mu.Lock()
	s.cmd = cmd
	if s.job == 0 {
		s.job = newJob()
	}
	if s.job != 0 {
		if h, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE, false, uint32(cmd.Process.Pid)); err == nil {
			_ = windows.AssignProcessToJobObject(s.job, h)
			windows.CloseHandle(h)
		}
	}
	s.mu.Unlock()

	found := make(chan string, 1)
	go func() {
		reader := bufio.NewScanner(out)
		sent := false
		for reader.Scan() {
			line := reader.Text()
			fmt.Fprintln(log, line)
			trimmed := strings.TrimSpace(line)
			if !sent && strings.HasPrefix(trimmed, "http://127.0.0.1:") {
				found <- trimmed
				sent = true
			}
		}
		if !sent {
			close(found)
		}
	}()
	select {
	case url, ok := <-found:
		if !ok {
			_ = cmd.Wait()
			return "", fmt.Errorf("Studio's server stopped before it was ready")
		}
		return url, nil
	case <-time.After(90 * time.Second):
		s.stop()
		return "", fmt.Errorf("Studio's server did not start within 90 seconds")
	}
}

func (s *server) stop() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.cmd != nil && s.cmd.Process != nil {
		_ = s.cmd.Process.Kill()
		_, _ = s.cmd.Process.Wait()
	}
	s.cmd = nil
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

// ------------------------------------------------------------------ the window

func main() {
	dir := installDir()
	data := dataDir()
	logFile, err := os.Create(filepath.Join(data, "studio.log"))
	if err != nil {
		logFile = os.Stderr
	}
	defer logFile.Close()
	fmt.Fprintf(logFile, "Kosika Studio %s, installed in %s\n", installedVersion(dir).short(), dir)

	set := loadSettings()
	w := webview2.NewWithOptions(webview2.WebViewOptions{
		AutoFocus: true,
		DataPath:  filepath.Join(data, "window"),
		WindowOptions: webview2.WindowOptions{
			Title:  title,
			Width:  1280,
			Height: 860,
			IconId: 1,
			Center: true,
		},
	})
	if w == nil {
		messageBox("Kosika Studio needs the Microsoft Edge WebView2 Runtime, which Windows 10 and 11 normally have.\n\nInstall it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 and open Kosika Studio again.")
		os.Exit(1)
	}
	defer w.Destroy()
	w.SetSize(960, 640, webview2.HintMin)
	w.SetSize(1280, 860, webview2.HintNone)
	w.SetHtml(waitingPage("Starting Kosika Studio…"))

	srv := &server{}
	up := &updater{dir: dir, data: data, log: logFile, settings: &set}
	up.bind(w, srv)
	w.Init(updateBarScript)

	go up.check()
	go func() {
		url, err := srv.start(dir, set, logFile)
		if err != nil {
			fmt.Fprintln(logFile, "start:", err)
			w.Dispatch(func() {
				w.SetHtml(waitingPage("Kosika Studio could not start: " + htmlEscape(err.Error()) +
					"<br><small>The details are in " + htmlEscape(filepath.Join(data, "studio.log")) + "</small>"))
			})
			return
		}
		fmt.Fprintln(logFile, "Studio is at", strings.SplitN(url, "?", 2)[0])
		w.Dispatch(func() { w.Navigate(url) })
	}()

	w.Run()
	srv.stop()
}

func htmlEscape(text string) string {
	r := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;")
	return r.Replace(text)
}

// waitingPage is what the window shows before Studio's page is ready, in
// Studio's own Graphite colours.
func waitingPage(message string) string {
	return `<!doctype html><html><head><meta charset="utf-8"><style>
html,body{height:100%;margin:0;background:#f6f4ef;color:#1d1b18;font:15px/1.5 "Segoe UI",system-ui,sans-serif}
@media (prefers-color-scheme:dark){html,body{background:#1d1b18;color:#f6f4ef}}
.c{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;text-align:center;padding:0 24px}
.mark{width:56px;height:56px;border-radius:14px;background:#1d1b18;display:grid;place-items:center}
@media (prefers-color-scheme:dark){.mark{background:#2a2723}}
.ring{width:32px;height:32px;border-radius:50%;border:4px solid #e8813a;box-sizing:border-box;display:grid;place-items:center;animation:p 1.6s ease-in-out infinite}
.dot{width:10px;height:10px;border-radius:50%;background:#f6f4ef}
@keyframes p{50%{transform:scale(.86)}}
small{color:#9a958b}
</style></head><body><div class="c"><div class="mark"><div class="ring"><div class="dot"></div></div></div><div>` +
		message + `</div></div></body></html>`
}
