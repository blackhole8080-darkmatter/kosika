package main

// `kosika-native run`: run one workflow of a program, with its inputs read
// from a JSON file, asking a real model where the program asks one. It
// takes the reference's flags for this and prints what `kosika run` prints:
// the workflow's result, as Python writes it, and where its trace is.

import (
	"bufio"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func runCommand(args []string) int {
	path := args[0]
	options := map[string]string{"--workflow": "main", "--model-protocol": "openai", "--model-timeout": "60", "--max-output-tokens": "1024"}
	options["--mcp-source"] = "local_stdio"
	options["--trace"] = "run_trace.jsonl"
	flags := map[string]bool{"--approve": true, "--allow-shell": true}
	known := map[string]bool{"--allow-folder": true, "--memory": true, "--env": true, "--journal": true, "--binding-id": true, "--approval-record": true, "--approved-by": true, "--trace": true, "--model-fixture": true, "--mcp-server": true, "--mcp-manifest": true, "--mcp-source": true, "--workflow": true, "--input": true, "--model": true, "--model-url": true, "--model-protocol": true, "--model-timeout": true, "--model-binding": true, "--max-output-tokens": true}
	for i := 1; i < len(args); i++ {
		name, value, joined := strings.Cut(args[i], "=")
		if flags[name] && !joined {
			options[name] = "yes"
			continue
		}
		if !known[name] {
			fmt.Fprintf(os.Stderr, "kosika-native run does not take %s\n", args[i])
			return 2
		}
		if !joined {
			if i+1 >= len(args) {
				fmt.Fprintf(os.Stderr, "%s needs a value\n", name)
				return 2
			}
			i++
			value = args[i]
		}
		options[name] = value
	}
	protocol := options["--model-protocol"]
	if protocol != "openai" && protocol != "ollama" {
		fmt.Fprintf(os.Stderr, "--model-protocol must be openai or ollama, not %s\n", protocol)
		return 2
	}
	seconds, err := strconv.ParseFloat(options["--model-timeout"], 64)
	if err != nil || seconds <= 0 {
		fmt.Fprintln(os.Stderr, "--model-timeout must be a positive number of seconds")
		return 2
	}
	maxOutput, err := strconv.Atoi(options["--max-output-tokens"])
	if err != nil || maxOutput < 1 {
		fmt.Fprintln(os.Stderr, "--max-output-tokens must be a positive integer")
		return 2
	}
	// The program is checked first, as the reference checks it, before
	// anything it would reach is looked at.
	program := load(path)
	var env *environment
	if name := options["--env"]; name != "" {
		for _, flag := range environmentDecided {
			if _, given := options[flag]; given {
				fmt.Fprintf(os.Stderr, "Workflow failed: %s cannot be used with --env; name it in the environment\n", flag)
				return 2
			}
		}
		found, problem := findEnvironment(path, name)
		if problem != "" {
			fmt.Fprintf(os.Stderr, "Workflow failed: %s\n", problem)
			return 2
		}
		env = found
		if options["--trace"] == "run_trace.jsonl" && env.keep != "" {
			options["--trace"] = env.tracePath()
		}
	}
	// The folder the program's memories are kept in, when granted.
	memory := options["--memory"]
	if env != nil && env.keep != "" {
		memory = filepath.Join(env.keep, "memory")
	}
	servers := map[string]*mcpServer{}
	if env != nil {
		for _, source := range sortedKeys(env.mcp) {
			route := env.mcp[source]
			manifest, problem := loadManifest(route.manifest)
			if problem != "" {
				fmt.Fprintf(os.Stderr, "Workflow failed: %s\n", problem)
				return 2
			}
			servers[source] = &mcpServer{route.command, manifest}
		}
	}
	if options["--mcp-server"] != "" {
		if options["--mcp-manifest"] == "" {
			fmt.Fprintln(os.Stderr, "--mcp-manifest is required when --mcp-server is used")
			return 2
		}
		manifest, problem := loadManifest(options["--mcp-manifest"])
		if problem != "" {
			fmt.Fprintf(os.Stderr, "Workflow failed: %s\n", problem)
			return 2
		}
		servers[options["--mcp-source"]] = &mcpServer{serverCommand(options["--mcp-server"]), manifest}
	} else if options["--mcp-manifest"] != "" {
		fmt.Fprintln(os.Stderr, "--mcp-manifest requires --mcp-server")
		return 2
	}
	var approvals []approvalRecord
	if file := options["--approval-record"]; file != "" {
		record, problem := readApproval(file)
		if problem != "" {
			fmt.Fprintf(os.Stderr, "Workflow failed: %s\n", problem)
			return 2
		}
		approvals = append(approvals, record)
	}
	journalPath, bindingID := options["--journal"], options["--binding-id"]
	if options["--approve"] != "" && journalPath == "" {
		// Approval needs a journal to remember what was approved between
		// the pause and the resume; an asked-for run keeps one for itself.
		scratch, err := os.MkdirTemp("", "kosika-approve-")
		if err != nil {
			fmt.Fprintf(os.Stderr, "Workflow failed: %v\n", err)
			return 2
		}
		defer os.RemoveAll(scratch)
		journalPath = filepath.Join(scratch, "run.json")
		if bindingID == "" {
			bindingID = "interactive"
		}
	}
	// An environment that keeps things keeps the run's journal too.
	keeps := env != nil && env.keep != ""
	if (journalPath == "") != (bindingID == "") || (len(approvals) > 0 && journalPath == "" && !keeps) {
		fmt.Fprintln(os.Stderr, "Workflow failed: Use --journal with --binding-id; approvals also require a journal")
		return 2
	}
	providers, problem := modelBindings(program, options, protocol, seconds, maxOutput)
	if env != nil {
		providers, problem = env.bindings(program, maxOutput)
	}
	if problem == "" && options["--model-fixture"] != "" {
		if len(providers) > 0 || options["--model-url"] != "" {
			problem = "Use either --model-fixture or an explicit live model binding"
		} else {
			providers, problem = fixtureBindings(options["--model-fixture"])
		}
	}
	if problem != "" {
		fmt.Fprintf(os.Stderr, "Workflow failed: %s\n", problem)
		return 2
	}
	inputs := map[string]Value{}
	if file := options["--input"]; file != "" {
		value, unreadable := parseStrictJSON(read(file))
		record, isObject := value.(*Record)
		if unreadable != "" || !isObject {
			fmt.Fprintln(os.Stderr, "Workflow failed: Workflow input must be a JSON object")
			return 2
		}
		for _, key := range record.Keys {
			inputs[key] = record.Fields[key]
		}
	}
	// The folder files tools may reach, and whether programs may be started
	// in it: granted by the person running the program, or its environment.
	folder, allowShell := options["--allow-folder"], options["--allow-shell"] != ""
	if env != nil {
		folder, allowShell = env.allowFolder, env.allowShell
	}
	if folder == "" && allowShell {
		fmt.Fprintln(os.Stderr, "Workflow failed: allowing shell also needs a granted folder to run in")
		return 2
	}
	if folder != "" {
		granted := realPath(folder)
		if granted == "" || !isDir(granted) {
			fmt.Fprintf(os.Stderr, "Workflow failed: granted folder %s is not an existing folder\n", pyRepr(folder))
			return 2
		}
		folder = granted
	}
	if env != nil && env.keep != "" {
		// A run keeps a journal of its own, named by what it runs, so one
		// paused for an approval is found again when run again.
		journalPath, bindingID = env.journalPath(path, options["--workflow"], inputs), env.name
	}
	checker := newChecker(program)
	workflow := checker.workflows[options["--workflow"]]
	if workflow == nil {
		fmt.Fprintf(os.Stderr, "Workflow failed: Unknown workflow %s\n", pyRepr(options["--workflow"]))
		return 2
	}
	code := "budget"
	var running *run
	var pending *Record
	var session *journal
	if journalPath != "" {
		session = openJournal(journalPath, bindingID, approvals)
	}
	started := time.Now()
	permission := false
	attempt := func() (result Value, stopped, failure string) {
		running = newRun(program, map[string]*scriptedTool{}, providers, workflow)
		running.servers = servers
		if memory != "" {
			running.memory = newMemoryStore(memory)
		}
		running.folder, running.allowShell = folder, allowShell
		if env != nil {
			// An environment's budget only tightens the workflow's.
			if amount, given := env.budget["model_calls"]; given && int(toFloat(amount)) < running.maxModels {
				running.maxModels = int(toFloat(amount))
			}
			if amount, given := env.budget["tool_calls"]; given && int(toFloat(amount)) < running.maxTools {
				running.maxTools = int(toFloat(amount))
			}
			if amount, given := env.budget["seconds"]; given {
				running.deadline, running.started = toFloat(amount), started
			}
		}
		running.traced = true
		running.hash = programHash(read(path))
		running.machine.tracing = newTracer(running.checker)
		running.machine.tracing.events = &[]Value{}
		running.session = session
		pending = nil
		defer func() {
			if caught := recover(); caught != nil {
				switch problem := caught.(type) {
				case *RunError:
					failure = problem.Message
				case *permissionError:
					failure, permission = problem.Message, true
				case *budgetError:
					stopped = problem.Message
					if problem.Code != "" {
						code = problem.Code
					}
				case *approvalPending:
					pending = problem.request
				default:
					panic(caught)
				}
			}
		}()
		if session != nil {
			session.lock()
			defer session.unlock()
			// The run the journal is for: this program, workflow, input
			// and the tools it may reach.
			plan := newRecord()
			plan.set("program", running.hash)
			plan.set("workflow", workflow.Name)
			recorded := newRecord()
			for _, key := range sortedKeys(inputs) {
				recorded.set(key, inputs[key])
			}
			plan.set("inputs", recorded)
			reached := newRecord()
			for source, server := range servers {
				commands := []Value{}
				for _, part := range server.command {
					commands = append(commands, part)
				}
				reached.set(source, commands)
			}
			plan.set("mcp", reached)
			session.bind(plan)
		}
		result, events := running.workflowEvents(workflow, inputs, 0)
		running.events = &events
		return result, "", ""
	}
	result, stopped, failure := attempt()
	for pending != nil && options["--approve"] != "" {
		granted := askApproval(pending, options["--approved-by"])
		if granted == nil {
			break
		}
		session.approvals = append(session.approvals, *granted)
		result, stopped, failure = attempt()
	}
	if pending != nil {
		fmt.Println(pyJSONIndent(withStatus(pending), "  ", ""))
		return 3
	}
	var events []Value
	if running.events != nil {
		events = *running.events
	}
	if stopped != "" || failure != "" {
		// A run that ends early is written down too, as far as it got.
		kind, kindType := "runtime_error", failureType(failure)
		if permission {
			kindType = "PermissionError"
		}
		if stopped != "" {
			kind, kindType = code, "ExecutionError"
		}
		if len(events) == 0 {
			events = append(events, event("run_start", "workflow", workflow.Name, "trace_version", bigInt(5)))
		}
		scope := []Value{workflow.Name}
		if running.failingStep != "" {
			// The step it failed in, begun, and its call, if it was made.
			scope = append(scope, running.failingStep)
			events = append(events, event("step_start", "step", running.failingStep))
			if running.inFlight != nil {
				events = append(events, running.inFlight)
			}
		}
		problem := newRecord()
		problem.set("code", kind)
		problem.set("type", kindType)
		events = append(events, event("run_failed", "workflow", workflow.Name, "scope", scope,
			"error", problem, "execution", running.execution(started)))
		_ = writeTrace(options["--trace"], events)
	}
	if stopped != "" {
		fmt.Fprintf(os.Stderr, "Workflow stopped (%s): %s\n", code, stopped)
		return 2
	}
	if failure != "" {
		hint := ""
		if strings.Contains(failure, "has no authorized host binding") && len(program.Models) > 0 {
			hint = "\nThis program declares a model, and a declaration alone grants no access. Add --model NAME to use a local model (default endpoint " + defaultLocalModelURL + ")."
		}
		fmt.Fprintf(os.Stderr, "Workflow failed: %s%s\n", failure, hint)
		return 2
	}
	if err := writeTrace(options["--trace"], events); err != nil {
		fmt.Fprintf(os.Stderr, "Workflow failed: %v\n", err)
		return 2
	}
	if env != nil && journalPath != "" {
		// The run is finished: there is nothing to begin again. Its
		// approvals' ledger stays, so an approval is never used twice.
		os.Remove(journalPath)
	}
	fmt.Println(pyReprAny(result))
	fmt.Printf("Trace: %s\n", options["--trace"])
	return 0
}

// fixtureBindings answers each model a program declares from a file of
// its answers, as the reference's --model-fixture does: offline.
func fixtureBindings(path string) (map[string]modelProvider, string) {
	value, unreadable := parseStrictJSON(read(path))
	fixture, ok := value.(*Record)
	if unreadable != "" || !ok {
		return nil, "Model fixture must map model names to lists of output records"
	}
	providers := map[string]modelProvider{}
	for _, name := range fixture.Keys {
		answers, ok := fixture.Fields[name].([]Value)
		allRecords := ok && len(answers) > 0
		for _, answer := range answers {
			if _, isRecord := answer.(*Record); !isRecord {
				allRecords = false
			}
		}
		if !allRecords {
			return nil, fmt.Sprintf("Model fixture %s requires a nonempty list of records", pyRepr(name))
		}
		providers[name] = &scriptedProvider{&scriptedTool{name: name, answers: answers}}
	}
	return providers, ""
}

// modelBindings grants the models a program declares to a model server, as
// the reference's --model, --model-url and --model-binding do. With
// --model alone every declared model is answered by it; `--model
// fast,strong=big` answers the one called strong with big.
func modelBindings(program *Program, options map[string]string, protocol string, seconds float64, maxOutput int) (map[string]modelProvider, string) {
	providers := map[string]modelProvider{}
	modelName, url, binding := options["--model"], options["--model-url"], options["--model-binding"]
	if modelName == "" && url == "" && binding == "" {
		return providers, ""
	}
	if modelName == "" {
		return nil, "A live binding needs --model NAME. Add --model-binding to grant one declared model, or omit it to grant every declared model."
	}
	var declared []string
	for _, model := range program.Models {
		if model.Provider == "" {
			declared = append(declared, model.Name)
		}
	}
	granted := declared
	if binding != "" {
		if url == "" {
			return nil, "--model-binding requires --model and --model-url"
		}
		granted = []string{binding}
	} else {
		if len(declared) == 0 {
			return nil, "--model was given, but this program declares no model"
		}
		if url == "" {
			url = os.Getenv("KOSIKA_MODEL_URL")
		}
		if url == "" {
			url = defaultLocalModelURL
			if protocol == "ollama" {
				url = strings.TrimSuffix(url, "/v1")
			}
		}
		fmt.Fprintf(os.Stderr, "Model %s at %s answers for: %s\n", pyRepr(modelName), url, strings.Join(granted, ", "))
	}
	parts := strings.Split(modelName, ",")
	base := strings.TrimSpace(parts[0])
	overrides := map[string]string{}
	for _, part := range parts[1:] {
		name, real, found := strings.Cut(strings.TrimSpace(part), "=")
		name, real = strings.TrimSpace(name), strings.TrimSpace(real)
		if !found || name == "" || real == "" {
			return nil, fmt.Sprintf("--model part %s must look like NAME=MODEL, where NAME is a model the program declares", pyRepr(part))
		}
		if !hasName(granted, name) {
			return nil, fmt.Sprintf("--model names %s, which is not a model this program declares; it declares: %s", pyRepr(name), strings.Join(granted, ", "))
		}
		overrides[name] = real
	}
	if base == "" || strings.Contains(base, "=") {
		return nil, "--model must begin with the model that answers by default"
	}
	for _, name := range granted {
		answering := base
		if real, ok := overrides[name]; ok {
			answering = real
		}
		providers[name] = newHTTPProvider(url, answering, protocol, seconds, maxOutput)
	}
	return providers, ""
}

func withStatus(request *Record) *Record {
	shown := newRecord()
	shown.set("status", "approval_required")
	for _, key := range request.Keys {
		shown.set(key, request.Fields[key])
	}
	return shown
}

// pyJSONIndent is json.dumps(value, indent=2), as the reference prints a
// pending approval.
func pyJSONIndent(v Value, step, at string) string {
	inner := at + step
	switch x := v.(type) {
	case []Value:
		if len(x) == 0 {
			return "[]"
		}
		parts := make([]string, len(x))
		for i, item := range x {
			parts[i] = inner + pyJSONIndent(item, step, inner)
		}
		return "[\n" + strings.Join(parts, ",\n") + "\n" + at + "]"
	case *Record:
		if len(x.Keys) == 0 {
			return "{}"
		}
		parts := make([]string, len(x.Keys))
		for i, key := range x.Keys {
			parts[i] = inner + pyJSONString(key) + ": " + pyJSONIndent(x.Fields[key], step, inner)
		}
		return "{\n" + strings.Join(parts, ",\n") + "\n" + at + "}"
	}
	return pyJSON(v, false)
}

// askApproval shows one pending action and asks the person at the
// terminal about it, as the reference's --approve does. It approves
// exactly this action, once; nothing else.
func askApproval(request *Record, approvedBy string) *approvalRecord {
	if info, err := os.Stdin.Stat(); err != nil || info.Mode()&os.ModeCharDevice == 0 {
		return nil
	}
	var steps []string
	for _, part := range request.Fields["step"].([]Value) {
		steps = append(steps, showText(part))
	}
	fmt.Fprintf(os.Stderr, "\nApproval needed before this action runs:\n  step      : %s\n  tool      : %s\n  arguments : %s\n",
		strings.Join(steps, " / "), showText(request.Fields["tool"]), pyJSON(request.Fields["arguments"], false))
	fmt.Fprint(os.Stderr, "Approve this one action? [y/N] ")
	answer, err := bufio.NewReader(os.Stdin).ReadString('\n')
	if err != nil && answer == "" {
		return nil
	}
	if reply := strings.ToLower(strings.TrimSpace(answer)); reply != "y" && reply != "yes" {
		fmt.Fprintln(os.Stderr, "Refused. Nothing was run.")
		return nil
	}
	if approvedBy == "" {
		if who, err := user.Current(); err == nil {
			approvedBy = who.Username
		}
	}
	return &approvalRecord{showText(request.Fields["plan_hash"]), showText(request.Fields["action_hash"]), approvedBy, "approved at the terminal"}
}
