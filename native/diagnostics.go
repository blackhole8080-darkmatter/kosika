package main

// `check --format json`: the reference's machine-readable report
// (kosika.diagnostics/v1), so an agent reads one shape from either engine.
// A problem is named by the same rules, in the same order, as the
// reference's kosika/diagnostics.py; diagnostic_codes.json is that table,
// written by `python -m kosika.diagnostics` and held equal to it by
// tests/test_diagnostics_parity.py.

import (
	_ "embed"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

//go:embed diagnostic_codes.json
var diagnosticTable []byte

type diagnosticRule struct {
	code    string
	pattern *regexp.Regexp
}

var diagnosticRules, diagnosticHelp = loadDiagnosticTable()

func loadDiagnosticTable() ([]diagnosticRule, map[string]string) {
	var table struct {
		Help  map[string]string `json:"help"`
		Rules [][2]string       `json:"rules"`
	}
	if err := json.Unmarshal(diagnosticTable, &table); err != nil {
		panic("diagnostic_codes.json: " + err.Error())
	}
	rules := make([]diagnosticRule, 0, len(table.Rules))
	for _, rule := range table.Rules {
		rules = append(rules, diagnosticRule{rule[0], regexp.MustCompile(rule[1])})
	}
	return rules, table.Help
}

// diagnosticCode is code_for in kosika/diagnostics.py.
func diagnosticCode(message string) string {
	// A feature this engine does not read yet, and says so by name.
	if strings.HasPrefix(message, "kosika-native cannot ") {
		return "engine/not-yet"
	}
	for _, rule := range diagnosticRules {
		if rule.pattern.MatchString(message) {
			return rule.code
		}
	}
	return "kosika/internal-error"
}

var locatedAt = regexp.MustCompile(` at (\d+):(\d+)`)

// diagnosticRange is where a message says the problem is, `at LINE:COLUMN`,
// as a zero-based LSP range as wide as the token that starts there (one
// character when no token does), as the reference engine reports it.
func diagnosticRange(message string, source string) any {
	found := locatedAt.FindStringSubmatch(message)
	if found == nil {
		return nil
	}
	line, _ := strconv.Atoi(found[1])
	column, _ := strconv.Atoi(found[2])
	width := 1
	if tokens, err := Lex(source); err == nil {
		for _, token := range tokens {
			if token.Line == line && token.Column == column {
				width = len([]rune(token.Value))
				break
			}
		}
	}
	position := func(character int) map[string]int {
		return map[string]int{"line": line - 1, "character": character}
	}
	return map[string]any{"start": position(column - 1), "end": position(column - 1 + width)}
}

func checkJSONCommand(path string) int {
	message := checkOnly(path)
	// A parse error names its file first; the report names it once, apart.
	// On Windows the folder may be spelled another way (a short 8.3 name,
	// or other slashes), so the file is known by its own name.
	if named, rest, found := strings.Cut(message, ": "); found && strings.EqualFold(filepath.Base(filepath.FromSlash(named)), filepath.Base(path)) {
		message = rest
	}
	report := map[string]any{
		"schema": "kosika.diagnostics/v1",
		"path":   path,
		"ok":     message == "",
	}
	diagnostics := []any{}
	codes := []string{}
	if message != "" {
		code := diagnosticCode(message)
		item := map[string]any{
			"code": code, "severity": 1, "message": message,
			"range": diagnosticRange(message, readSource(path)), "path": absolutePath(path),
			"owner": nil, "owner_kind": nil, "data": map[string]any{},
		}
		if help, known := diagnosticHelp[code]; known {
			item["help"] = help
		}
		diagnostics = append(diagnostics, item)
		codes = append(codes, code)
	}
	report["diagnostics"] = diagnostics
	report["codes"] = codes
	report["error_count"] = len(diagnostics)
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(report)
	if message != "" {
		return 2
	}
	return 0
}

func absolutePath(path string) string {
	if absolute, err := os.Getwd(); err == nil && !strings.HasPrefix(path, "/") && !strings.Contains(path, ":\\") {
		return absolute + string(os.PathSeparator) + path
	}
	return path
}

// readSource is a program's text, or nothing when it cannot be read.
func readSource(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return string(data)
}
