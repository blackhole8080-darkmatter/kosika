package main

// serve: a Kosika program's workflows as a small web service on this
// computer, so Chat, Studio, a website or another program can ask for
// one without anything in between.
//
//	kosika-native serve FILE.kos [--port 8790] [run options]
//
// POST /run/WORKFLOW with the workflow's input as JSON ({"ask": {...}})
// runs it as `run` would, with the run options given here, and answers
// {"ok": true, "output": ...}. A run that fails answers {"ok": false,
// "error": ...}; one that needs approval answers 409 with what it asks to
// do, since a service cannot say yes for anyone. GET /health answers ok.
//
// Each request runs in its own process of this engine: one run cannot
// disturb another, and a fault in one is that request's alone. At most
// two run at once; more wait their turn. The service listens on this
// computer only.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

var workflowName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

func serveCommand(args []string) int {
	path, err := filepath.Abs(args[0])
	if err != nil {
		fmt.Fprintf(os.Stderr, "serve: %v\n", err)
		return 2
	}
	port := "8790"
	var passed []string
	for i := 1; i < len(args); i++ {
		name, value, joined := strings.Cut(args[i], "=")
		if name == "--port" {
			if !joined {
				if i+1 >= len(args) {
					fmt.Fprintln(os.Stderr, "--port needs a value")
					return 2
				}
				i++
				value = args[i]
			}
			port = value
			continue
		}
		if name == "--workflow" || name == "--input" || name == "--json" || name == "--trace" {
			fmt.Fprintf(os.Stderr, "serve chooses %s for each request; leave it out\n", name)
			return 2
		}
		passed = append(passed, args[i])
	}
	// The program must check before anything is served.
	loadTo(path, os.Stderr)
	self, err := os.Executable()
	if err != nil {
		fmt.Fprintf(os.Stderr, "serve: %v\n", err)
		return 2
	}
	turns := make(chan struct{}, 2)
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		answer(w, http.StatusOK, map[string]any{"ok": true, "program": filepath.Base(path)})
	})
	mux.HandleFunc("/run/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			answer(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "send the input with POST"})
			return
		}
		workflow := strings.TrimPrefix(r.URL.Path, "/run/")
		if !workflowName.MatchString(workflow) {
			answer(w, http.StatusNotFound, map[string]any{"ok": false, "error": "name a workflow: /run/NAME"})
			return
		}
		body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		var input map[string]any
		if err != nil || json.Unmarshal(body, &input) != nil {
			answer(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "the input must be one JSON object, such as {\"ask\": {...}}"})
			return
		}
		turns <- struct{}{}
		defer func() { <-turns }()
		status, reply := runOnce(self, path, workflow, body, passed)
		answer(w, status, reply)
	})
	address := "127.0.0.1:" + port
	fmt.Fprintf(os.Stderr, "Serving %s on http://%s (POST /run/WORKFLOW)\n", filepath.Base(path), address)
	server := &http.Server{Addr: address, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	if err := server.ListenAndServe(); err != nil {
		fmt.Fprintf(os.Stderr, "serve: %v\n", err)
		return 2
	}
	return 0
}

// runOnce runs one workflow in a process of its own, in a folder of its
// own that keeps its trace, and says what came of it.
func runOnce(self, path, workflow string, input []byte, passed []string) (int, map[string]any) {
	work, err := os.MkdirTemp("", "kosika-serve-")
	if err != nil {
		return http.StatusInternalServerError, map[string]any{"ok": false, "error": err.Error()}
	}
	defer os.RemoveAll(work)
	given := filepath.Join(work, "input.json")
	if err := os.WriteFile(given, input, 0o600); err != nil {
		return http.StatusInternalServerError, map[string]any{"ok": false, "error": err.Error()}
	}
	command := exec.Command(self, append([]string{"run", path, "--workflow", workflow, "--input", given, "--json", "--trace", filepath.Join(work, "trace.jsonl")}, passed...)...)
	command.Dir = work
	command.Stdin = nil
	var out, problems bytes.Buffer
	command.Stdout, command.Stderr = &out, &problems
	err = command.Run()
	code := 0
	if exit, ok := err.(*exec.ExitError); ok {
		code = exit.ExitCode()
	} else if err != nil {
		return http.StatusInternalServerError, map[string]any{"ok": false, "error": err.Error()}
	}
	printed := strings.TrimSpace(out.String())
	switch code {
	case 0:
		var output any
		if json.Unmarshal([]byte(printed), &output) != nil {
			return http.StatusInternalServerError, map[string]any{"ok": false, "error": "the run's answer was not JSON"}
		}
		return http.StatusOK, map[string]any{"ok": true, "output": output}
	case 3:
		var asked any
		_ = json.Unmarshal([]byte(printed), &asked)
		return http.StatusConflict, map[string]any{"ok": false, "error": "this run asks for approval before it acts; run it where a person can say yes", "approval": asked}
	default:
		said := strings.TrimSpace(problems.String())
		if lines := strings.Split(said, "\n"); len(lines) > 0 {
			// The run's own words: its last lines, which say why.
			for i, line := range lines {
				if strings.HasPrefix(line, "Workflow ") || strings.HasPrefix(line, "Check failed") {
					said = strings.Join(lines[i:], "\n")
					break
				}
			}
		}
		if len(said) > 2000 {
			said = said[len(said)-2000:]
		}
		return http.StatusUnprocessableEntity, map[string]any{"ok": false, "error": said}
	}
}

func answer(w http.ResponseWriter, status int, body map[string]any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	encoder := json.NewEncoder(w)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(body)
}
