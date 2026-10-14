// Kosika's native engine. It needs no Python.
//
//	kosika-native tokens FILE.kos    the file's tokens, one JSON object a line
//	kosika-native test FILE.kos      run the tests the file carries
//	kosika-native test --json FILE.kos   the same, as JSON
//
// It has the core of the language: records, functions and tests, which
// reach nothing outside the program. Workflows, agents, tools and models
// are not here yet, and a file that has them is refused by name. It is
// built up stage by stage against the conformance suite.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
)

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

func read(path string) string {
	source, err := os.ReadFile(path)
	if err != nil {
		fail("cannot read %s: %v", path, err)
	}
	return string(source)
}

func main() {
	args := os.Args[1:]
	asJSON := false
	if len(args) == 3 && args[1] == "--json" {
		asJSON, args = true, []string{args[0], args[2]}
	}
	if len(args) != 2 {
		fail("usage: kosika-native tokens FILE.kos | kosika-native test [--json] FILE.kos")
	}
	switch args[0] {
	case "tokens":
		tokens, lexErr := Lex(read(args[1]))
		out := bufio.NewWriter(os.Stdout)
		encoder := json.NewEncoder(out)
		encoder.SetEscapeHTML(false)
		for _, token := range tokens {
			if err := encoder.Encode(token); err != nil {
				fail("%v", err)
			}
		}
		out.Flush()
		if lexErr != nil {
			fail("%v", lexErr)
		}
	case "test":
		program, err := Parse(read(args[1]))
		if err != nil {
			fail("Check failed: %s: %v", args[1], err)
		}
		results := RunTests(program)
		failed := 0
		for _, result := range results {
			if !result.Passed {
				failed++
			}
		}
		if asJSON {
			encoder := json.NewEncoder(os.Stdout)
			encoder.SetEscapeHTML(false)
			_ = encoder.Encode(results)
		} else {
			fmt.Println(TestsText(results))
		}
		if failed > 0 {
			os.Exit(1)
		}
	default:
		fail("kosika-native cannot %q yet", args[0])
	}
}
