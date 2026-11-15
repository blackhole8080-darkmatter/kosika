package main

// What any phone can do with nothing installed but Kosika, an iPhone in
// iSH included: say what it is, show a message, and keep and test Kosika
// programs in one folder, ~/kosika. Each is asked about like any other.

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"time"
)

// phoneWork is the one folder a paired device may put programs in.
func phoneWork() string {
	if dir := os.Getenv("KOSIKA_WORK"); dir != "" {
		return dir
	}
	home, err := os.UserHomeDir()
	if err != nil {
		home = "."
	}
	return filepath.Join(home, "kosika")
}

var programName = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,39}\.kos$`)

func checkProgramName(name string) error {
	if !programName.MatchString(name) {
		return errors.New("must be a name like quiz_1.kos: small letters, digits, _ or -, ending .kos")
	}
	return nil
}

func readDevice() (any, error) {
	if out, err := exec.Command("getprop", "ro.product.model").Output(); err == nil && strings.TrimSpace(string(out)) != "" {
		return "Android, " + strings.TrimSpace(string(out)), nil
	}
	if out, err := exec.Command("uname", "-sm").Output(); err == nil {
		system := strings.TrimSpace(string(out))
		if _, err := os.Stat("/proc/ish"); err == nil {
			system += " (iSH on an iPhone or iPad)"
		}
		return system, nil
	}
	return runtime.GOOS + "/" + runtime.GOARCH, nil
}

func readFiles() (any, error) {
	paths, _ := filepath.Glob(filepath.Join(phoneWork(), "*.kos"))
	names := []string{}
	for _, path := range paths {
		names = append(names, filepath.Base(path))
	}
	sort.Strings(names)
	return names, nil
}

func showOnScreen(args map[string]string) (any, error) {
	line := strings.Repeat("─", 44)
	fmt.Printf("\n  %s\n  %s\n  %s\n\n", line, args["text"], line)
	return "shown", nil
}

func saveProgram(args map[string]string) (any, error) {
	if err := os.MkdirAll(phoneWork(), 0o755); err != nil {
		return nil, err
	}
	path := filepath.Join(phoneWork(), args["name"])
	text := args["text"]
	if !strings.HasSuffix(text, "\n") {
		text += "\n"
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		return nil, err
	}
	return fmt.Sprintf("saved %s (%d bytes)", path, len(text)), nil
}

// testProgram runs this same program's own `test` on a saved program, so
// no other program and no shell is ever started.
func testProgram(args map[string]string) (any, error) {
	path := filepath.Join(phoneWork(), args["name"])
	if _, err := os.Stat(path); err != nil {
		return nil, fmt.Errorf("there is no %s in %s", args["name"], phoneWork())
	}
	self, err := os.Executable()
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, self, "test", path)
	command.Dir = phoneWork()
	out, runErr := command.CombinedOutput()
	text := strings.TrimSpace(string(out))
	// The answer must fit the relay: keep the end, where the summary is.
	if len(text) > 1500 {
		text = "…" + text[len(text)-1500:]
	}
	if ctx.Err() != nil {
		return nil, errors.New("the tests took longer than 60 seconds")
	}
	if runErr != nil && text == "" {
		return nil, runErr
	}
	return text, nil
}

func init() {
	phoneActions["show"] = phoneAction{"show a message on its screen", []string{"text"},
		checkEach(map[string]func(string) error{"text": textUnder(500)}), nil, showOnScreen}
	phoneActions["save"] = phoneAction{"save a Kosika program in ~/kosika", []string{"name", "text"},
		checkEach(map[string]func(string) error{"name": checkProgramName, "text": textUnder(2000)}), nil, saveProgram}
	phoneActions["test"] = phoneAction{"run the tests of a Kosika program in ~/kosika", []string{"name"},
		checkEach(map[string]func(string) error{"name": checkProgramName}), nil, testProgram}
}
