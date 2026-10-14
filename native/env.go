package main

// Environments: what a project's programs may reach, written down once in
// a `kosika.env` beside them, as kosika/environment.py reads it. See that
// file for the form; this one follows it rule for rule and word for word.

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const environmentFile = "kosika.env"

type modelRoute struct {
	model, url, protocol string
}

type mcpRoute struct {
	command  []string
	manifest string
}

type environment struct {
	name, folder string
	models       map[string]modelRoute
	modelOrder   []string
	fixture      string
	mcp          map[string]mcpRoute
	allowFolder  string
	allowShell   bool
	sites        []string
	budget       map[string]Value
	keep         string
}

type envError struct{ message string }

type envReader struct {
	tokens []Token
	index  int
	where  string
}

func (r *envReader) current() Token { return r.tokens[r.index] }

func (r *envReader) fail(format string, args ...any) {
	panic(&envError{r.where + ": " + fmt.Sprintf(format, args...)})
}

func (r *envReader) check(value string) bool {
	token := r.current()
	return (token.Kind == "IDENT" || token.Kind == "SYMBOL" || token.Kind == "OP") && token.Value == value
}

func (r *envReader) accept(value string) bool {
	if r.check(value) {
		r.index++
		return true
	}
	return false
}

func (r *envReader) expect(value string) {
	if !r.check(value) {
		token := r.current()
		r.fail("Expected %s at %d:%d, got %s", pyRepr(value), token.Line, token.Column, pyRepr(token.Value))
	}
	r.index++
}

func (r *envReader) name() string {
	token := r.current()
	if token.Kind != "IDENT" {
		r.fail("Expected a name at %d:%d, got %s", token.Line, token.Column, pyRepr(token.Value))
	}
	r.index++
	return token.Value
}

func (r *envReader) text() string {
	token := r.current()
	if token.Kind != "STRING" {
		r.fail("Expected text in quotes at %d:%d, got %s", token.Line, token.Column, pyRepr(token.Value))
	}
	r.index++
	return token.Value
}

func (r *envReader) number() Value {
	token := r.current()
	if token.Kind != "NUMBER" {
		r.fail("Expected a number at %d:%d, got %s", token.Line, token.Column, pyRepr(token.Value))
	}
	r.index++
	return numberLiteral(token.Value, false)
}

// within is a path of the environment's folder, made whole.
func within(folder, path string) string {
	joined := path
	if !filepath.IsAbs(path) {
		joined = filepath.Join(folder, path)
	}
	if absolute, err := filepath.Abs(joined); err == nil {
		return absolute
	}
	return filepath.Clean(joined)
}

func (r *envReader) command(folder string) []string {
	var parts []string
	if r.accept("[") {
		for !r.accept("]") {
			parts = append(parts, r.text())
			r.accept(",")
		}
	} else {
		parts = strings.Fields(r.text())
	}
	if len(parts) == 0 {
		r.fail("An MCP server needs a command")
	}
	for i, part := range parts {
		// A part that names a file in the environment's folder is given as
		// its full path.
		if info, err := os.Stat(filepath.Join(folder, part)); err == nil && !info.IsDir() {
			parts[i] = within(folder, part)
		}
	}
	return parts
}

func readEnvironments(path string) (found map[string]*environment, problem string) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Sprintf("Cannot read %s: %v", path, err)
	}
	tokens, lexErr := Lex(string(raw))
	if lexErr != nil {
		if stopped, ok := lexErr.(*LexError); ok {
			return nil, fmt.Sprintf("%s: Unexpected character at %d:%d: %s", path, stopped.Line, stopped.Column, pyRepr(stopped.Excerpt))
		}
		return nil, fmt.Sprintf("%s: %v", path, lexErr)
	}
	defer func() {
		if caught := recover(); caught != nil {
			failure, ok := caught.(*envError)
			if !ok {
				panic(caught)
			}
			found, problem = nil, failure.message
		}
	}()
	r := &envReader{tokens: tokens, where: path}
	folder := filepath.Dir(within(".", path))
	found = map[string]*environment{}
	for r.current().Kind != "EOF" {
		r.expect("environment")
		name := r.name()
		if found[name] != nil {
			r.fail("Environment %s is declared twice", pyRepr(name))
		}
		env := &environment{name: name, folder: folder, models: map[string]modelRoute{}, mcp: map[string]mcpRoute{}, budget: map[string]Value{}}
		r.expect("{")
		for !r.accept("}") {
			token := r.current()
			switch {
			case r.accept("model"):
				declared := r.name()
				r.expect(":")
				if _, taken := env.models[declared]; taken {
					r.fail("Environment %s names model %s twice", pyRepr(name), pyRepr(declared))
				}
				route := modelRoute{model: r.text(), protocol: "openai"}
				if r.accept("at") {
					route.url = r.text()
				}
				if r.accept("protocol") {
					route.protocol = r.name()
					if route.protocol != "openai" && route.protocol != "ollama" {
						r.fail("Environment %s model %s protocol must be one of openai, ollama, not %s", pyRepr(name), pyRepr(declared), pyRepr(route.protocol))
					}
				}
				env.models[declared] = route
				env.modelOrder = append(env.modelOrder, declared)
			case r.accept("models"):
				r.expect("answer")
				r.expect("from")
				if env.fixture != "" {
					r.fail("Environment %s answers models from two files", pyRepr(name))
				}
				env.fixture = within(folder, r.text())
			case r.accept("mcp"):
				source := r.name()
				r.expect(":")
				if _, taken := env.mcp[source]; taken {
					r.fail("Environment %s names MCP source %s twice", pyRepr(name), pyRepr(source))
				}
				command := r.command(folder)
				r.expect("pinned")
				env.mcp[source] = mcpRoute{command, within(folder, r.text())}
			case r.accept("allow"):
				switch {
				case r.accept("folder"):
					if env.allowFolder != "" {
						r.fail("Environment %s allows a second folder; it may allow one", pyRepr(name))
					}
					env.allowFolder = within(folder, r.text())
				case r.accept("shell"):
					env.allowShell = true
				case r.accept("site"):
					env.sites = append(env.sites, r.text())
				default:
					word := r.current()
					r.fail("Expected 'folder', 'shell' or 'site' at %d:%d, got %s", word.Line, word.Column, pyRepr(word.Value))
				}
			case r.accept("budget"):
				for {
					amount := r.number()
					var kind string
					switch {
					case r.accept("model"):
						r.expect("calls")
						kind = "model_calls"
					case r.accept("tool"):
						r.expect("calls")
						kind = "tool_calls"
					default:
						r.expect("seconds")
						kind = "seconds"
					}
					label := strings.ReplaceAll(kind, "_", " ")
					if _, taken := env.budget[kind]; taken {
						r.fail("Environment %s budget names %s more than once", pyRepr(name), label)
					}
					ok := false
					if kind == "seconds" {
						ok = toFloat(amount) > 0 && toFloat(amount) <= 86_400
					} else if whole, isWhole := amount.(interface{ Int64() int64 }); isWhole {
						ok = whole.Int64() >= 1 && whole.Int64() <= 1_000_000
					}
					if !ok {
						r.fail("Environment %s budget for %s is out of range, got %s", pyRepr(name), label, pyNumber(amount))
					}
					env.budget[kind] = amount
					if !r.accept(",") {
						break
					}
				}
			case r.accept("keep"):
				if env.keep != "" {
					r.fail("Environment %s keeps its things in two places", pyRepr(name))
				}
				env.keep = within(folder, r.text())
			default:
				r.fail("Unexpected %s at %d:%d in environment %s; an environment has model, models, mcp, allow, budget and keep", pyRepr(token.Value), token.Line, token.Column, pyRepr(name))
			}
		}
		if env.fixture != "" && len(env.models) > 0 {
			r.fail("Environment %s answers models from a file and also names model servers; choose one", pyRepr(name))
		}
		if env.allowShell && env.allowFolder == "" {
			r.fail("Environment %s allows the shell but no folder for it to work in", pyRepr(name))
		}
		found[name] = env
	}
	return found, ""
}

// findEnvironment is the environment `name` in the kosika.env beside a
// program, or in the nearest folder above it that has one.
func findEnvironment(program, name string) (*environment, string) {
	folder := filepath.Dir(within(".", program))
	for {
		candidate := filepath.Join(folder, environmentFile)
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			environments, problem := readEnvironments(candidate)
			if problem != "" {
				return nil, problem
			}
			if environments[name] == nil {
				have := strings.Join(sortedKeys(environments), ", ")
				if have == "" {
					have = "none"
				}
				return nil, fmt.Sprintf("%s has no environment %s; it has: %s", candidate, pyRepr(name), have)
			}
			return environments[name], ""
		}
		parent := filepath.Dir(folder)
		if parent == folder {
			return nil, fmt.Sprintf("No %s found beside %s or in a folder above it", environmentFile, program)
		}
		folder = parent
	}
}

// tracePath is where a run's trace goes: one file a run, named by when it
// began.
func (e *environment) tracePath() string {
	now := time.Now().UTC()
	stamp := now.Format("20060102T150405") + fmt.Sprintf("%06dZ", now.Nanosecond()/1000)
	return filepath.Join(e.keep, "traces", "run-"+stamp+".jsonl")
}

// journalPath is where a run's journal goes: one a run, named by its
// program, workflow and input, so that the same run finds it again.
func (e *environment) journalPath(program, workflow string, inputs map[string]Value) string {
	recorded := newRecord()
	for _, key := range sortedKeys(inputs) {
		recorded.set(key, inputs[key])
	}
	named := pyJSON([]Value{within(".", program), workflow, recorded}, true)
	sum := sha256.Sum256([]byte(named))
	return filepath.Join(e.keep, "journals", hex.EncodeToString(sum[:])[:16]+".json")
}

// environmentDecided are the run options an environment decides; given
// with --env, they are refused.
var environmentDecided = []string{"--model", "--model-url", "--model-binding", "--model-fixture", "--mcp-server", "--mcp-manifest", "--journal", "--binding-id"}

// environmentBindings are the models an environment names, for the models
// a program declares.
func (e *environment) bindings(program *Program, maxOutput int) (map[string]modelProvider, string) {
	if e.fixture != "" {
		return fixtureBindings(e.fixture)
	}
	declared := map[string]bool{}
	for _, model := range program.Models {
		if model.Provider == "" {
			declared[model.Name] = true
		}
	}
	providers := map[string]modelProvider{}
	for _, name := range e.modelOrder {
		if !declared[name] {
			continue
		}
		route := e.models[name]
		url := route.url
		if url == "" {
			url = defaultLocalModelURL
			if route.protocol == "ollama" {
				url = strings.TrimSuffix(url, "/v1")
			}
		}
		providers[name] = newHTTPProvider(url, route.model, route.protocol, 60, maxOutput)
		fmt.Fprintf(os.Stderr, "%s: model %s at %s\n", name, pyRepr(route.model), url)
	}
	return providers, ""
}
