package main

// Standing permissions and the keeper: what lets Kosika agents run on
// their own, safely.
//
// A standing permission is a rule a person sets once, in a file:
//
//	{"rules": [{"tool": "write_file", "path_prefix": "reports/", "note": "the digest may write its reports"}]}
//
// `run --standing FILE` approves an action that changes something only
// when a rule names its tool and, if the rule gives a path_prefix, the
// action's path starts with it. Anything else still waits for a person
// (or `--approve`). Each use is said on the error output and recorded in
// the run's journal like any approval.
//
// The keeper runs agents on a schedule and keeps them going:
//
//	kosika-native keep KEEPER.json
//
//	{"jobs": [{"name": "study-pack", "program": "examples/agents/crew/main.kos",
//	           "workflow": "pack", "input": {"ask": {...}}, "every_minutes": 60,
//	           "times": 0, "options": ["--model", "...", "--standing", "rules.json"]}],
//	 "log": "keeper.runs"}
//
// Each job runs as its own process of this engine, at most one at a time
// per job, every `every_minutes`; `times` (0 for ever) caps how often. A
// run that fails or crashes is written down and tried again at its next
// turn; one that waits for approval is written down as waiting. Every run
// leaves its output, its trace and a line in LOG/keeper.jsonl.

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type standingRule struct {
	Tool       string `json:"tool"`
	PathPrefix string `json:"path_prefix"`
	Note       string `json:"note"`
}

func readStanding(file string) ([]standingRule, string) {
	raw, err := os.ReadFile(file)
	if err != nil {
		return nil, fmt.Sprintf("cannot read the standing permissions: %v", err)
	}
	var given struct {
		Rules []standingRule `json:"rules"`
	}
	if json.Unmarshal(raw, &given) != nil {
		return nil, "the standing permissions must be {\"rules\": [{\"tool\": ..., \"path_prefix\": ...}]}"
	}
	for _, rule := range given.Rules {
		if strings.TrimSpace(rule.Tool) == "" {
			return nil, "every standing permission names its tool"
		}
	}
	return given.Rules, ""
}

// standingGrant approves a pending action when a rule covers it.
func standingGrant(request *Record, rules []standingRule) *approvalRecord {
	if request == nil || len(rules) == 0 {
		return nil
	}
	tool := showText(request.Fields["tool"])
	path := ""
	if arguments, ok := request.Fields["arguments"].(*Record); ok {
		if given, ok := arguments.Fields["path"].(string); ok {
			path = filepath.ToSlash(filepath.Clean(given))
		}
	}
	for _, rule := range rules {
		if rule.Tool != tool {
			continue
		}
		if rule.PathPrefix != "" && !strings.HasPrefix(path, filepath.ToSlash(rule.PathPrefix)) {
			continue
		}
		reason := "standing permission for " + tool
		if rule.Note != "" {
			reason += ": " + rule.Note
		}
		fmt.Fprintf(os.Stderr, "Allowed by a standing permission: %s %s\n", tool, path)
		return &approvalRecord{showText(request.Fields["plan_hash"]), showText(request.Fields["action_hash"]), "standing permission", reason}
	}
	return nil
}

type keeperJob struct {
	Name         string          `json:"name"`
	Program      string          `json:"program"`
	Workflow     string          `json:"workflow"`
	Input        json.RawMessage `json:"input"`
	EveryMinutes float64         `json:"every_minutes"`
	Times        int             `json:"times"`
	Options      []string        `json:"options"`
}

type keeperPlan struct {
	Jobs []keeperJob `json:"jobs"`
	Log  string      `json:"log"`
}

func keepCommand(args []string) int {
	raw, err := os.ReadFile(args[0])
	if err != nil {
		fmt.Fprintf(os.Stderr, "keep: %v\n", err)
		return 2
	}
	var plan keeperPlan
	if json.Unmarshal(raw, &plan) != nil || len(plan.Jobs) == 0 {
		fmt.Fprintln(os.Stderr, "keep: the plan is {\"jobs\": [{\"name\", \"program\", \"workflow\", \"input\", \"every_minutes\"}], \"log\": FOLDER}")
		return 2
	}
	base := filepath.Dir(args[0])
	if plan.Log == "" {
		plan.Log = "keeper.runs"
	}
	logDir := plan.Log
	if !filepath.IsAbs(logDir) {
		logDir = filepath.Join(base, logDir)
	}
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "keep: %v\n", err)
		return 2
	}
	self, err := os.Executable()
	if err != nil {
		fmt.Fprintf(os.Stderr, "keep: %v\n", err)
		return 2
	}
	for _, job := range plan.Jobs {
		if job.Name == "" || !workflowName.MatchString(strings.ReplaceAll(job.Name, "-", "_")) || job.Program == "" || job.Workflow == "" || job.EveryMinutes <= 0 {
			fmt.Fprintf(os.Stderr, "keep: job %q needs a simple name, a program, a workflow and every_minutes above 0\n", job.Name)
			return 2
		}
		// The program must check before it is kept.
		program := job.Program
		if !filepath.IsAbs(program) {
			program = filepath.Join(base, program)
		}
		loadTo(program, os.Stderr)
	}
	fmt.Fprintf(os.Stderr, "Keeping %d job(s); every run is written to %s\n", len(plan.Jobs), filepath.Join(logDir, "keeper.jsonl"))
	done := make(chan string)
	for _, job := range plan.Jobs {
		go keepJob(self, base, logDir, job, done)
	}
	for range plan.Jobs {
		name := <-done
		fmt.Fprintf(os.Stderr, "Job %s has run as often as it was asked to\n", name)
	}
	return 0
}

// keepJob runs one job at its times, one run after another, for as long as
// it is asked to.
func keepJob(self, base, logDir string, job keeperJob, done chan<- string) {
	every := time.Duration(job.EveryMinutes * float64(time.Minute))
	for count := 1; job.Times == 0 || count <= job.Times; count++ {
		started := time.Now()
		stamp := started.Format("20060102-150405")
		folder := filepath.Join(logDir, job.Name)
		_ = os.MkdirAll(folder, 0o755)
		input := filepath.Join(folder, stamp+".input.json")
		_ = os.WriteFile(input, job.Input, 0o600)
		program := job.Program
		if !filepath.IsAbs(program) {
			program = filepath.Join(base, program)
		}
		trace := filepath.Join(folder, stamp+".trace.jsonl")
		command := exec.Command(self, append([]string{"run", program, "--workflow", job.Workflow, "--input", input, "--json", "--trace", trace}, job.Options...)...)
		command.Dir = base
		out, _ := os.Create(filepath.Join(folder, stamp+".output.json"))
		problems, _ := os.Create(filepath.Join(folder, stamp+".log"))
		command.Stdout, command.Stderr = out, problems
		err := command.Run()
		out.Close()
		problems.Close()
		code := 0
		if exit, ok := err.(*exec.ExitError); ok {
			code = exit.ExitCode()
		} else if err != nil {
			code = -1
		}
		outcome := map[int]string{0: "done", 2: "failed", 3: "waiting for approval"}[code]
		if outcome == "" {
			outcome = "crashed"
		}
		line, _ := json.Marshal(map[string]any{
			"job": job.Name, "run": count, "started": started.Format(time.RFC3339),
			"seconds": int(time.Since(started).Seconds()), "outcome": outcome, "exit": code,
			"output": filepath.Join(job.Name, stamp+".output.json"), "trace": filepath.Join(job.Name, stamp+".trace.jsonl"),
		})
		if file, err := os.OpenFile(filepath.Join(logDir, "keeper.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
			file.Write(append(line, '\n'))
			file.Close()
		}
		fmt.Fprintf(os.Stderr, "%s  %s run %d: %s (%ds)\n", time.Now().Format("15:04:05"), job.Name, count, outcome, int(time.Since(started).Seconds()))
		if job.Times != 0 && count >= job.Times {
			break
		}
		if wait := every - time.Since(started); wait > 0 {
			time.Sleep(wait)
		}
	}
	done <- job.Name
}
