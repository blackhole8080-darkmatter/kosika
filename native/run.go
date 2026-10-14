package main

// `kosika-native run`: run one workflow of a program, with its inputs read
// from a JSON file, asking a real model where the program asks one. It
// takes the reference's flags for this and prints what `kosika run` prints:
// the workflow's result, as Python writes it. It writes no trace yet.

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

func runCommand(args []string) int {
	path := args[0]
	options := map[string]string{"--workflow": "main", "--model-protocol": "openai", "--model-timeout": "60", "--max-output-tokens": "1024"}
	known := map[string]bool{"--workflow": true, "--input": true, "--model": true, "--model-url": true, "--model-protocol": true, "--model-timeout": true, "--model-binding": true, "--max-output-tokens": true}
	for i := 1; i < len(args); i++ {
		name, value, joined := strings.Cut(args[i], "=")
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
	program := load(path)
	providers, problem := modelBindings(program, options, protocol, seconds, maxOutput)
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
	checker := newChecker(program)
	workflow := checker.workflows[options["--workflow"]]
	if workflow == nil {
		fmt.Fprintf(os.Stderr, "Workflow failed: Unknown workflow %s\n", pyRepr(options["--workflow"]))
		return 2
	}
	code := "budget"
	result, stopped, failure := func() (result Value, stopped, failure string) {
		defer func() {
			if caught := recover(); caught != nil {
				switch problem := caught.(type) {
				case *RunError:
					failure = problem.Message
				case *permissionError:
					failure = problem.Message
				case *budgetError:
					stopped = problem.Message
					if problem.Code != "" {
						code = problem.Code
					}
				default:
					panic(caught)
				}
			}
		}()
		return newRun(program, map[string]*scriptedTool{}, providers, workflow).workflow(workflow, inputs, 0), "", ""
	}()
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
	fmt.Println(pyReprAny(result))
	return 0
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
