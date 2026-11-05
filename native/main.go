// Kosika's native engine. It needs no Python.
//
//	kosika-native tokens FILE.kos    the file's tokens, one JSON object a line
//	kosika-native test FILE.kos      run the tests the file carries
//	kosika-native test --json FILE.kos   the same, as JSON
//	kosika-native run FILE.kos [--workflow NAME] [--input FILE.json]
//	    [--model NAME] [--model-url URL] [--model-protocol openai|ollama]
//	    [--model-timeout SECONDS]    run a workflow, asking a real model
//	kosika-native replay TRACE.jsonl  check a run's trace, running nothing
//	kosika-native capabilities      the conformance capabilities it reads
//
// It is built up stage by stage against the conformance suite; what it
// cannot do yet it refuses by name. See README.md.
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

// load reads, lowers and checks a program, as the reference's load_program
// does, and ends the command when it cannot be run. Why the checker refused
// it is said on the output, as the conformance suite reports it.
func load(path string) *Program {
	return loadTo(path, os.Stdout)
}

// loadTo is load, saying why the checker refused a program on refusals:
// `run` says it on the error output, as the reference's command line does.
func loadTo(path string, refusals *os.File) *Program {
	program, failure := loadModules(path)
	if failure != nil && failure.parse {
		fail("Check failed: %s", failure.message)
	}
	if failure != nil {
		fmt.Fprintf(refusals, "Check failed: %s\n", failure.message)
		os.Exit(2)
	}
	stored, refused := LowerStores(LowerDirect(LowerAmbient(program)))
	if refused != "" {
		fmt.Fprintf(refusals, "Check failed: %s\n", refused)
		os.Exit(2)
	}
	lowered, refused := LowerThinking(stored)
	if refused != "" {
		fmt.Fprintf(refusals, "Check failed: %s\n", refused)
		os.Exit(2)
	}
	program = FillDefaults(LowerValues(lowered))
	// A program the checker refuses is not run, and says why, with exit
	// code 2.
	if refused := Check(program); refused != nil {
		fmt.Fprintf(refusals, "Check failed: %s\n", refused)
		os.Exit(2)
	}
	return program
}

// capabilities are the conformance surfaces this engine reads, as the
// suite's programs name them on their first line.
var capabilities = []string{"choice"}

func main() {
	args := os.Args[1:]
	if len(args) == 1 && args[0] == "capabilities" {
		for _, capability := range capabilities {
			fmt.Println(capability)
		}
		return
	}
	if len(args) >= 2 && args[0] == "run" {
		os.Exit(runCommand(args[1:]))
	}
	if len(args) >= 2 && args[0] == "serve" {
		os.Exit(serveCommand(args[1:]))
	}
	if len(args) == 2 && args[0] == "replay" {
		os.Exit(replayCommand(args[1]))
	}
	if len(args) == 3 && args[0] == "readable" {
		// What the web tool reads from a saved page, as one JSON object:
		// for tests that hold it to the reference's reading.
		body := string([]byte(read(args[2])))
		result := map[string]string{}
		switch args[1] {
		case "html", "text":
			result["title"], result["text"] = readablePage(body, args[1] == "html")
		case "feed":
			result["text"] = readableFeed(body)
		default:
			fail("usage: kosika-native readable html|text|feed FILE")
		}
		encoder := json.NewEncoder(os.Stdout)
		encoder.SetEscapeHTML(false)
		_ = encoder.Encode(result)
		return
	}
	asJSON := false
	if len(args) == 3 && args[1] == "--json" {
		asJSON, args = true, []string{args[0], args[2]}
	}
	if len(args) != 2 {
		fail("usage: kosika-native tokens FILE.kos | kosika-native test [--json] FILE.kos | kosika-native run FILE.kos [options] | kosika-native serve FILE.kos [--port N] [run options] | kosika-native capabilities")
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
		results := RunTests(load(args[1]))
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
