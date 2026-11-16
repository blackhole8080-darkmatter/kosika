// Kosika Studio's installer and uninstaller.
//
// Built with -tags payload it carries the whole app (payload.zip) and is
// "Kosika Studio Setup.exe". Built without it, it carries nothing and is the
// uninstall.exe that the installer puts beside the app.
//
// It installs for this person only, so it needs no administrator:
//
//	%LOCALAPPDATA%\Programs\Kosika Studio   the app
//	Start menu and Desktop                 "Kosika Studio" shortcuts
//	Apps & features                        an entry with an Uninstall button
//
// Flags: /S (or --quiet) asks nothing; --dir D installs elsewhere;
// --agents PATH tells the app where kosika-agents is; --no-shortcuts and
// --no-registry leave the Start menu, Desktop and Apps & features alone
// (for trying an install in a scratch folder); --uninstall removes it.
package main

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

const (
	name       = "Kosika Studio"
	exeName    = "Kosika Studio.exe"
	uninstKey  = `Software\Microsoft\Windows\CurrentVersion\Uninstall\KosikaStudio`
	publisher  = "Kosika"
	appVersion = "0.1"
)

var quiet bool

// idYes is what MessageBox gives back when Yes is pressed.
const idYes int32 = 6

func say(text string, flags uint32) int32 {
	if quiet {
		fmt.Println(text)
		return idYes
	}
	t, _ := windows.UTF16PtrFromString(name)
	m, _ := windows.UTF16PtrFromString(text)
	answer, _ := windows.MessageBox(0, m, t, flags)
	return answer
}

func fail(text string) {
	say(text, windows.MB_OK|windows.MB_ICONERROR)
	os.Exit(1)
}

func defaultDir() string {
	base := os.Getenv("LOCALAPPDATA")
	return filepath.Join(base, "Programs", name)
}

// dataDir is the app's settings folder; KOSIKA_STUDIO_DATA moves it, for tries.
func dataDir() string {
	if told := os.Getenv("KOSIKA_STUDIO_DATA"); told != "" {
		return told
	}
	return filepath.Join(os.Getenv("LOCALAPPDATA"), name)
}

func main() {
	var dir, agents string
	var uninstall, noShortcuts, noRegistry, fromTemp, open bool
	flag.StringVar(&dir, "dir", "", "where to install")
	flag.StringVar(&agents, "agents", "", "the kosika-agents folder")
	flag.BoolVar(&uninstall, "uninstall", false, "remove Kosika Studio")
	flag.BoolVar(&noShortcuts, "no-shortcuts", false, "make no Start menu or Desktop shortcut")
	flag.BoolVar(&noRegistry, "no-registry", false, "add no Apps & features entry")
	flag.BoolVar(&fromTemp, "from-temp", false, "internal: the uninstaller's copy of itself")
	flag.BoolVar(&open, "open", false, "open Kosika Studio when done, without asking")
	flag.BoolVar(&quiet, "quiet", false, "ask nothing")
	args := os.Args[1:]
	for i, a := range args {
		if strings.EqualFold(a, "/S") {
			args[i] = "--quiet"
		}
	}
	_ = flag.CommandLine.Parse(args)

	if len(payload) == 0 {
		uninstall = true // this build is the uninstaller
	}
	if uninstall {
		if dir == "" {
			exe, _ := os.Executable()
			dir = filepath.Dir(exe)
		}
		runUninstall(dir, fromTemp)
		return
	}
	if dir == "" {
		dir = defaultDir()
	}
	runInstall(dir, agents, !noShortcuts, !noRegistry, open)
}

// ------------------------------------------------------------------ install

func runInstall(dir, agents string, shortcuts, register, open bool) {
	if say("Install Kosika Studio?\n\nIt goes in "+dir+" and gets a Start menu and Desktop icon. Your agents and settings are not touched.",
		windows.MB_YESNO|windows.MB_ICONQUESTION) != idYes {
		return
	}
	if running(filepath.Join(dir, exeName)) {
		fail("Kosika Studio is open. Close it, then run the installer again.")
	}
	reader, err := zip.NewReader(bytes.NewReader(payload), int64(len(payload)))
	if err != nil {
		fail("The installer is damaged: " + err.Error())
	}
	// A fresh install replaces the app's own folders, never the settings,
	// which live elsewhere (%LOCALAPPDATA%\Kosika Studio).
	for _, sub := range []string{"app", "app.old", "runtime", "engine"} {
		_ = os.RemoveAll(filepath.Join(dir, sub))
	}
	if err := unzip(reader, dir); err != nil {
		fail("Could not install: " + err.Error())
	}
	if agents != "" {
		writeAgentsHome(agents)
	}
	if shortcuts {
		if err := makeShortcuts(dir); err != nil {
			say("Kosika Studio is installed, but its shortcuts could not be made: "+err.Error(), windows.MB_OK|windows.MB_ICONWARNING)
		}
	}
	if register {
		if err := registerUninstall(dir); err != nil {
			say("Kosika Studio is installed, but it could not be added to Apps & features: "+err.Error(), windows.MB_OK|windows.MB_ICONWARNING)
		}
	}
	if open || (!quiet && say("Kosika Studio is installed. Open it now?", windows.MB_YESNO|windows.MB_ICONINFORMATION) == idYes) {
		cmd := exec.Command(filepath.Join(dir, exeName))
		cmd.Dir = dir
		_ = cmd.Start()
	}
}

func writeAgentsHome(agents string) {
	path := filepath.Join(dataDir(), "settings.json")
	_ = os.MkdirAll(filepath.Dir(path), 0o755)
	settings := map[string]any{}
	if data, err := os.ReadFile(path); err == nil {
		_ = json.Unmarshal(data, &settings)
	}
	absolute, _ := filepath.Abs(agents)
	settings["agents_home"] = absolute
	data, _ := json.MarshalIndent(settings, "", "  ")
	_ = os.WriteFile(path, data, 0o644)
}

func unzip(reader *zip.Reader, dest string) error {
	root, _ := filepath.Abs(dest)
	for _, file := range reader.File {
		target := filepath.Join(root, filepath.FromSlash(file.Name))
		if target != root && !strings.HasPrefix(target, root+string(os.PathSeparator)) {
			return fmt.Errorf("unsafe path in payload: %s", file.Name)
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

// running says whether the app at PATH is open: Windows will not let an
// open program's file be renamed.
func running(path string) bool {
	if _, err := os.Stat(path); err != nil {
		return false
	}
	if err := os.Rename(path, path+".check"); err != nil {
		return true
	}
	_ = os.Rename(path+".check", path)
	return false
}

func shortcutPlaces() []string {
	var places []string
	profile := os.Getenv("USERPROFILE")
	for _, desktop := range []string{filepath.Join(profile, "Desktop"), filepath.Join(profile, "OneDrive", "Desktop")} {
		if info, err := os.Stat(desktop); err == nil && info.IsDir() {
			places = append(places, filepath.Join(desktop, name+".lnk"))
			break
		}
	}
	if roaming := os.Getenv("APPDATA"); roaming != "" {
		places = append(places, filepath.Join(roaming, "Microsoft", "Windows", "Start Menu", "Programs", name+".lnk"))
	}
	return places
}

// The shortcut is made by Windows' own shell object. Every value is handed
// over in the environment, never pasted into the command.
const makeLink = "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:K_LNK); " +
	"$s.TargetPath = $env:K_TARGET; $s.WorkingDirectory = $env:K_DIR; " +
	"$s.IconLocation = $env:K_TARGET + ',0'; " +
	"$s.Description = 'Kosika Studio: build, run and watch AI agents'; $s.Save()"

func makeShortcuts(dir string) error {
	for _, place := range shortcutPlaces() {
		cmd := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", makeLink)
		cmd.Env = append(os.Environ(), "K_LNK="+place, "K_TARGET="+filepath.Join(dir, exeName), "K_DIR="+dir)
		cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: windows.CREATE_NO_WINDOW}
		if out, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("%s: %v %s", place, err, strings.TrimSpace(string(out)))
		}
	}
	return nil
}

func folderSize(dir string) uint32 {
	var total int64
	_ = filepath.Walk(dir, func(_ string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() {
			total += info.Size()
		}
		return nil
	})
	return uint32(total / 1024)
}

func registerUninstall(dir string) error {
	key, _, err := registry.CreateKey(registry.CURRENT_USER, uninstKey, registry.ALL_ACCESS)
	if err != nil {
		return err
	}
	defer key.Close()
	uninstaller := filepath.Join(dir, "uninstall.exe")
	values := map[string]string{
		"DisplayName":          name,
		"DisplayIcon":          filepath.Join(dir, exeName) + ",0",
		"DisplayVersion":       appVersion,
		"Publisher":            publisher,
		"InstallLocation":      dir,
		"UninstallString":      `"` + uninstaller + `"`,
		"QuietUninstallString": `"` + uninstaller + `" /S`,
		"InstallDate":          time.Now().Format("20060102"),
	}
	for k, v := range values {
		if err := key.SetStringValue(k, v); err != nil {
			return err
		}
	}
	_ = key.SetDWordValue("NoModify", 1)
	_ = key.SetDWordValue("NoRepair", 1)
	_ = key.SetDWordValue("EstimatedSize", folderSize(dir))
	return nil
}

// ------------------------------------------------------------------ uninstall

func runUninstall(dir string, fromTemp bool) {
	if !fileExists(filepath.Join(dir, exeName)) {
		fail("Kosika Studio is not installed in " + dir)
	}
	if !fromTemp {
		if say("Remove Kosika Studio from this computer?\n\nYour agents and Studio's settings are kept.",
			windows.MB_YESNO|windows.MB_ICONQUESTION) != idYes {
			return
		}
		if running(filepath.Join(dir, exeName)) {
			fail("Kosika Studio is open. Close it, then uninstall again.")
		}
		// A program cannot delete the folder it runs from, so the
		// uninstaller hands the work to a copy of itself in the temp folder.
		self, _ := os.Executable()
		copyPath := filepath.Join(os.TempDir(), fmt.Sprintf("kosika-studio-uninstall-%d.exe", time.Now().UnixNano()))
		if err := copyFile(self, copyPath); err == nil {
			args := []string{"--uninstall", "--from-temp", "--dir", dir}
			if quiet {
				args = append(args, "--quiet")
			}
			cmd := exec.Command(copyPath, args...)
			if cmd.Start() == nil {
				return
			}
		}
	}
	time.Sleep(700 * time.Millisecond) // let the first uninstaller exit
	for _, place := range shortcutPlaces() {
		if target, _ := linkPointsInto(place, dir); target {
			_ = os.Remove(place)
		}
	}
	_ = registry.DeleteKey(registry.CURRENT_USER, uninstKey)
	var err error
	for attempt := 0; attempt < 10; attempt++ {
		if err = os.RemoveAll(dir); err == nil {
			break
		}
		time.Sleep(500 * time.Millisecond)
	}
	if err != nil {
		// What is left is this uninstaller itself, when it could not run
		// from a temp copy: Windows' own shell removes the folder once it
		// has exited.
		self, _ := os.Executable()
		if strings.HasPrefix(strings.ToLower(self), strings.ToLower(filepath.Clean(dir))+string(os.PathSeparator)) {
			cleanup := exec.Command("cmd", "/c", "ping -n 3 127.0.0.1 >nul & rd /s /q \""+dir+"\"")
			cleanup.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: windows.CREATE_NO_WINDOW}
			if cleanup.Start() == nil {
				err = nil
			}
		}
	}
	if err != nil {
		say("Kosika Studio is mostly removed, but some files are still in "+dir+": "+err.Error(), windows.MB_OK|windows.MB_ICONWARNING)
		return
	}
	say("Kosika Studio is removed. Your agents and settings ("+dataDir()+") are kept.", windows.MB_OK|windows.MB_ICONINFORMATION)
}

// linkPointsInto says whether the shortcut at PLACE opens something in DIR,
// so the uninstaller never deletes a shortcut it did not make.
func linkPointsInto(place, dir string) (bool, error) {
	if !fileExists(place) {
		return false, nil
	}
	cmd := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command",
		"(New-Object -ComObject WScript.Shell).CreateShortcut($env:K_LNK).TargetPath")
	cmd.Env = append(os.Environ(), "K_LNK="+place)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: windows.CREATE_NO_WINDOW}
	out, err := cmd.Output()
	if err != nil {
		return false, err
	}
	target := strings.ToLower(filepath.Clean(strings.TrimSpace(string(out))))
	return strings.HasPrefix(target, strings.ToLower(filepath.Clean(dir))+string(os.PathSeparator)), nil
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func copyFile(from, to string) error {
	data, err := os.ReadFile(from)
	if err != nil {
		return err
	}
	return os.WriteFile(to, data, 0o755)
}
