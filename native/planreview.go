package main

// Reading an agent's plan before a person is shown it, as review_agent_plan
// in kosika/runtime.py does: what is plainly wrong with the files it would
// write (plan_problems in kosika/skills.py), whether a Kosika file it writes
// checks and its tests pass (kosika_problems in kosika/code_checks.py), and
// what the agent's own `check` says. A plan with something wrong is sent
// back to the model, twice at most; one still wrong is shown with what is
// wrong.
//
// A Python file in a plan is not read here: telling whether text is Python
// needs a Python parser, which this engine does not carry. Such a plan is
// refused by name.

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// planRetries is how many times a plan with something wrong is sent back.
const planRetries = 2

// maxPlanTests is how many of a planned Kosika file's tests are run.
const maxPlanTests = 40

var planToken = regexp.MustCompile(`[a-z0-9]+`)

func planTokens(text string) []string {
	return planToken.FindAllString(strings.ToLower(text), -1)
}

// fileBefore is a file as it is before the plan, read with the agent's own
// reading tool, or nil when it is not there or the agent has none. What is
// read is kept, so a step begun again after a person answered checks its
// plan against the same text.
func (r *run) fileBefore(changes *agentChanges, path string) Value {
	scope := append(append([]string{}, changes.scope...), planTool+":before", path)
	if r.session != nil {
		if kept, ok := r.session.remembered(scope).(*Record); ok {
			return kept.Fields["text"]
		}
	}
	var text Value
	for _, name := range changes.agent.Can {
		reader := r.checker.tools[name]
		if reader.Source != "system:files" || reader.Operation != "read" || !r.systemBound(reader) {
			continue
		}
		arguments := newRecord()
		arguments.set("path", path)
		func() {
			defer func() {
				if recover() != nil {
					text = nil
				}
			}()
			if got, ok := r.fileTool(reader, arguments).(*Record); ok {
				if found, ok := got.Fields["text"].(string); ok {
					text = found
				}
			}
		}()
		break
	}
	if r.session != nil {
		kept := newRecord()
		kept.set("text", text)
		r.session.save(scope, "note", kept)
	}
	return text
}

// reviewPlan is what is wrong with a plan, in plain words; nothing when
// nothing is.
func (r *run) reviewPlan(changes *agentChanges, planned []plannedChange) []string {
	var problems []string
	asked := strings.Join(planTokens(changes.request), " ")
	seen := map[string]bool{}
	files := map[string]string{}
	var order []string
	for _, change := range planned {
		path, isPath := change.arguments.Fields["path"].(string)
		text, isText := change.arguments.Fields["text"].(string)
		if !isPath || !isText {
			continue
		}
		where := strings.ToLower(strings.TrimPrefix(strings.ReplaceAll(path, `\`, "/"), "./"))
		if seen[where] {
			problems = append(problems, path+": the plan writes this file twice; write it once")
			continue
		}
		seen[where] = true
		if pyStrip(text) == "" {
			problems = append(problems, path+": the file would be empty")
			continue
		}
		if strings.Contains(text, `\n`) && !strings.Contains(text, "\n") {
			problems = append(problems, path+": line breaks were written as the two characters backslash and n; use real line breaks")
			continue
		}
		said := planTokens(text)
		if len(said) >= 4 && strings.Contains(asked, strings.Join(said, " ")) {
			problems = append(problems, path+": the file only repeats the request; write what was asked for")
			continue
		}
		if strings.HasSuffix(strings.ToLower(path), ".py") {
			panic(&permissionError{fmt.Sprintf("kosika-native cannot check a plan that writes Python files yet: %s is one", pyRepr(path))})
		}
		files[path] = text
		order = append(order, path)
	}
	problems = append(problems, kosikaProblems(order, files)...)
	if changes.agent.Check == "" {
		return problems
	}
	// The program's own check, written in Kosika: it is given every file
	// the plan would write, with the file as it is now, and says what is
	// wrong.
	var given []Value
	for _, change := range planned {
		path, isPath := change.arguments.Fields["path"].(string)
		text, isText := change.arguments.Fields["text"].(string)
		if !isPath || !isText {
			continue
		}
		was := r.fileBefore(changes, path)
		before, _ := was.(string)
		file := newRecord()
		file.set("path", path)
		file.set("text", text)
		file.set("before", before)
		file.set("exists", was != nil)
		given = append(given, file)
	}
	if given == nil {
		given = []Value{}
	}
	check := r.machine.functions[changes.agent.Check]
	arguments := map[string]Value{check.Params[0].Name: changes.request, check.Params[1].Name: given}
	said, _ := r.machine.invoke(check, arguments, nil, nil, 0).([]Value)
	known := map[string]bool{}
	for _, problem := range problems {
		known[problem] = true
	}
	for _, line := range said {
		if text, ok := line.(string); ok && pyStrip(text) != "" && !known[text] {
			problems = append(problems, text)
		}
	}
	return problems
}

// kosikaProblems is what is wrong with the Kosika files of a plan: a file
// that does not check, or a test in it that fails. The files are written to
// a folder made for the purpose, each at its own path, so that one may
// import another; a file that imports one the plan does not hold is not
// judged.
func kosikaProblems(order []string, files map[string]string) []string {
	var kos []string
	planned := map[string]string{}
	for _, path := range order {
		if strings.HasSuffix(strings.ToLower(path), ".kos") {
			clean := strings.TrimPrefix(strings.ReplaceAll(path, `\`, "/"), "./")
			if _, has := planned[clean]; !has {
				kos = append(kos, clean)
			}
			planned[clean] = files[path]
		}
	}
	if len(kos) == 0 {
		return nil
	}
	scratch, err := os.MkdirTemp("", "kosika-plan-")
	if err != nil {
		return nil
	}
	defer os.RemoveAll(scratch)
	top := realPath(scratch)
	placed := map[string]string{}
	var placedOrder []string
	for _, path := range kos {
		target := filepath.Join(top, filepath.FromSlash(path))
		if !inside(target, top) || target == top {
			continue
		}
		if os.MkdirAll(filepath.Dir(target), 0o755) != nil || os.WriteFile(target, []byte(planned[path]), 0o644) != nil {
			continue
		}
		placed[path] = target
		placedOrder = append(placedOrder, path)
	}
	var problems []string
	for _, path := range placedOrder {
		target := placed[path]
		program, problem := loadChecked(target)
		if problem != "" {
			if strings.HasPrefix(problem, "No such module: ") {
				// It imports a file that is not part of the plan.
				continue
			}
			said := strings.ReplaceAll(strings.ReplaceAll(problem, target+": ", ""), target, path)
			problems = append(problems, fmt.Sprintf("%s: it is not valid Kosika: %s", path, clipText(said, 400)))
			continue
		}
		results, failure := testsOf(program)
		if failure != "" {
			problems = append(problems, fmt.Sprintf("%s: its tests could not be run: %s", path, clipText(failure, 300)))
			continue
		}
		if len(results) > maxPlanTests {
			results = results[:maxPlanTests]
		}
		var failed []TestResult
		for _, result := range results {
			if !result.Passed {
				failed = append(failed, result)
			}
		}
		if len(failed) > 0 {
			line := fmt.Sprintf(`%s: its test "%s" fails at line %d: %s`, path, failed[0].Name, failed[0].Line, clipText(failed[0].Message, 300))
			if len(failed) > 1 {
				line += fmt.Sprintf(" (%d tests fail in all)", len(failed))
			}
			problems = append(problems, line)
		}
	}
	return problems
}

func clipText(text string, most int) string {
	if runes := []rune(text); len(runes) > most {
		return string(runes[:most])
	}
	return text
}

// loadChecked reads, lowers and checks a program as load in main.go does,
// giving back why it cannot be run instead of ending the command.
func loadChecked(path string) (program *Program, problem string) {
	defer func() {
		if caught := recover(); caught != nil {
			if failure, ok := caught.(error); ok {
				program, problem = nil, failure.Error()
				return
			}
			program, problem = nil, fmt.Sprint(caught)
		}
	}()
	loaded, failure := loadModules(path)
	if failure != nil {
		return nil, failure.message
	}
	stored, refused := LowerStores(LowerDirect(LowerAmbient(loaded)))
	if refused != "" {
		return nil, refused
	}
	lowered, refused := LowerThinking(stored)
	if refused != "" {
		return nil, refused
	}
	program = FillDefaults(LowerValues(lowered))
	if err := Check(program); err != nil {
		return nil, err.Error()
	}
	return program, ""
}

// testsOf runs a program's tests, giving back why they could not be run
// instead of failing with it.
func testsOf(program *Program) (results []TestResult, failure string) {
	defer func() {
		if caught := recover(); caught != nil {
			results, failure = nil, fmt.Sprint(caught)
			if problem, ok := caught.(*RunError); ok {
				failure = problem.Message
			}
		}
	}()
	return RunTests(program), ""
}
