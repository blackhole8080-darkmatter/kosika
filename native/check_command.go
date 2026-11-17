package main

// `check`: read a program and say whether it would run, as the reference's
// `kosika check` does. A program that is well prints `OK: PATH` and ends 0;
// one that is not prints `Check failed: REASON` on the error output and ends
// 2, the code the conformance suite gives a refused program.
//
// The reference adds ` (line N, column M)` after the reason, worked out by
// its own diagnostics engine from the source. This engine's checker reports
// the reason without a position, so the two agree on what is wrong and differ
// only in that suffix.

import (
	"fmt"
	"os"
)

// checkOnly reads, lowers and checks a program, giving back why it cannot run
// and "" when it can. It is loadTo with the reason handed back instead of the
// command ending.
func checkOnly(path string) string {
	program, failure := loadModules(path)
	if failure != nil {
		return failure.message
	}
	stored, refused := LowerStores(LowerDirect(LowerAmbient(program)))
	if refused != "" {
		return refused
	}
	lowered, refused := LowerThinking(stored)
	if refused != "" {
		return refused
	}
	evidenced, refused := LowerModelEvidence(lowered)
	if refused != "" {
		return refused
	}
	// `use_model ... with evidence` is lowered, so the checker sees the same
	// program the run would.
	program = FillDefaults(LowerValues(evidenced))
	if err := Check(program); err != nil {
		return err.Error()
	}
	return ""
}

func checkCommand(path string) int {
	if refused := checkOnly(path); refused != "" {
		fmt.Fprintf(os.Stderr, "Check failed: %s\n", refused)
		return 2
	}
	fmt.Printf("OK: %s\n", path)
	return 0
}
