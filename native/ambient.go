package main

// The clock and chance, as kosika/ambient.py has them: `today = now` and
// `roll = random 1 to 6` are workflow steps, lowered to two tools that only
// look, used through a generated agent named `world`. What the clock said
// and what the dice gave are written in the trace like any tool's answer.
// Neither needs a grant; a test scripts them like any tool.

import (
	"crypto/rand"
	"fmt"
	"math/big"
	"sort"
	"time"
)

const (
	worldAgent    = "world"
	clockTool     = "clock_now"
	randomTool    = "random_number"
	momentRecord  = "Moment"
	rangeRecord   = "RandomRange"
	numberRecord  = "RandomNumber"
	maxRandomSpan = 1_000_000_000
)

var ambientRecords = map[string][]Param{
	momentRecord: {{"date", "Text"}, {"time", "Text"}, {"weekday", "Text"}, {"year", "Number"}, {"month", "Number"},
		{"day", "Number"}, {"hour", "Number"}, {"minute", "Number"}, {"epoch", "Number"}},
	rangeRecord:  {{"low", "Number"}, {"high", "Number"}},
	numberRecord: {{"value", "Number"}},
}

var ambientTools = map[string]*ToolDecl{
	clockTool:  {Name: clockTool, Source: "world:clock", Operation: "now", Access: "observe", Output: momentRecord},
	randomTool: {Name: randomTool, Source: "world:random", Operation: "number", Access: "observe", Output: numberRecord, Input: rangeRecord},
}

// ambientCalls are which of the two tools a program's workflows use.
func ambientCalls(program *Program) []string {
	used := map[string]bool{}
	for _, workflow := range program.Workflows {
		for _, step := range workflow.Steps {
			calls := []*StepCall{step.Call, step.Fallback}
			for _, branch := range step.Branches {
				calls = append(calls, branch.Call)
			}
			for _, call := range calls {
				if call != nil && call.Kind == "tool" && call.Agent == worldAgent && ambientTools[call.Name] != nil {
					used[call.Name] = true
				}
			}
		}
	}
	return sortedKeys(used)
}

// ambientNames are the names a module may use because it has `now` or
// `random` steps.
func ambientNames(program *Program) map[string]bool {
	used := ambientCalls(program)
	names := map[string]bool{}
	if len(used) == 0 {
		return names
	}
	names[worldAgent] = true
	for _, name := range used {
		names[name] = true
		if name == clockTool {
			names[momentRecord] = true
		} else {
			names[rangeRecord], names[numberRecord] = true, true
		}
	}
	return names
}

// LowerAmbient adds the records, tools and agent that `now` and `random`
// stand for. A name the program already declares is left as written.
func LowerAmbient(program *Program) *Program {
	used := ambientCalls(program)
	if len(used) == 0 {
		return program
	}
	for _, agent := range program.Agents {
		if agent.Name == worldAgent {
			return program
		}
	}
	known, taken := map[string]bool{}, map[string]bool{}
	for _, record := range program.Records {
		known[record.Name] = true
	}
	for _, tool := range program.Tools {
		taken[tool.Name] = true
	}
	var wanted []string
	for _, name := range used {
		if name == clockTool {
			wanted = append(wanted, momentRecord)
		} else {
			wanted = append(wanted, rangeRecord, numberRecord)
		}
	}
	lowered := *program
	lowered.Records = append([]*RecordDecl{}, program.Records...)
	for _, name := range wanted {
		if !known[name] {
			lowered.Records = append(lowered.Records, &RecordDecl{Name: name, Fields: ambientRecords[name]})
		}
	}
	lowered.Tools = append([]*ToolDecl{}, program.Tools...)
	sort.Strings(used)
	for _, name := range used {
		if !taken[name] {
			tool := *ambientTools[name]
			lowered.Tools = append(lowered.Tools, &tool)
		}
	}
	lowered.Agents = append(append([]*AgentDecl{}, program.Agents...),
		&AgentDecl{Name: worldAgent, Can: used, MaxSteps: 1, MaxToolCalls: 256, MaxModelCalls: 64})
	return &lowered
}

var weekdays = []string{"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}

// moment is this moment, in the computer's own time zone, as a Moment.
func moment() Value {
	now := time.Now()
	local := now.Local()
	record := newRecord()
	record.set("date", local.Format("2006-01-02"))
	record.set("time", local.Format("15:04"))
	record.set("weekday", weekdays[(int(local.Weekday())+6)%7])
	record.set("year", bigInt(local.Year()))
	record.set("month", bigInt(int(local.Month())))
	record.set("day", bigInt(local.Day()))
	record.set("hour", bigInt(local.Hour()))
	record.set("minute", bigInt(local.Minute()))
	record.set("epoch", bigInt(int(now.Unix())))
	return record
}

// draw is a whole number from low to high, both ends included.
func draw(arguments *Record) Value {
	whole := func(v Value) (*big.Int, bool) {
		switch x := v.(type) {
		case *big.Int:
			return x, true
		case float64:
			if x == float64(int64(x)) {
				return big.NewInt(int64(x)), true
			}
		}
		return nil, false
	}
	low, lowOK := whole(arguments.Fields["low"])
	high, highOK := whole(arguments.Fields["high"])
	if !lowOK || !highOK {
		runFail("random needs whole numbers: random 1 to 6")
	}
	if high.Cmp(low) < 0 {
		runFail(fmt.Sprintf("random needs the smaller number first, got %s to %s", pyNumber(arguments.Fields["low"]), pyNumber(arguments.Fields["high"])))
	}
	span := new(big.Int).Sub(high, low)
	if span.Cmp(big.NewInt(maxRandomSpan)) > 0 {
		runFail("random can span at most a thousand million numbers")
	}
	picked, err := rand.Int(rand.Reader, new(big.Int).Add(span, big.NewInt(1)))
	if err != nil {
		runFail(err.Error())
	}
	result := newRecord()
	result.set("value", new(big.Int).Add(low, picked))
	return result
}
