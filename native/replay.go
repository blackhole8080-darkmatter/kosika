package main

// `kosika-native replay TRACE`: check a run's trace without running
// anything, as kosika/trace_replay.py checks one: every step's events in
// order, every value held to the schema recorded beside it, every model
// answer read again and found to be the outcome recorded, every agent's
// transcript whole. It prints the run's result.

import (
	"fmt"
	"math"
	"math/big"
	"os"
	"sort"
	"strings"
)

type replayError struct{ message string }

func replayFail(format string, args ...any) {
	panic(&replayError{fmt.Sprintf(format, args...)})
}

func replayCommand(path string) int {
	raw, err := os.ReadFile(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Replay rejected: %v\n", err)
		return 2
	}
	result, problem := func() (result Value, problem string) {
		defer func() {
			if caught := recover(); caught != nil {
				failure, ok := caught.(*replayError)
				if !ok {
					panic(caught)
				}
				problem = failure.message
			}
		}()
		var events []Value
		// A trace ends with a newline; there is no event after it.
		text := strings.TrimSuffix(strings.ReplaceAll(string(raw), "\r\n", "\n"), "\n")
		for _, line := range strings.Split(text, "\n") {
			if line == "" {
				replayFail("Expecting value: line 1 column 1 (char 0)")
			}
			value, unreadable := parseStrictJSON(line)
			if unreadable != "" {
				replayFail("%s", unreadable)
			}
			events = append(events, value)
		}
		if first, ok := events[0].(*Record); ok && first.Fields["event"] == "agent_run_start" {
			replayFail("kosika-native cannot replay the trace of an agent run yet")
		}
		return replayEvents(events, 0), ""
	}()
	if problem != "" {
		fmt.Fprintf(os.Stderr, "Replay rejected: %s\n", problem)
		return 2
	}
	fmt.Println(pyReprAny(result))
	return 0
}

func sameJSON(a, b Value) bool { return pyJSON(a, true) == pyJSON(b, true) }

func entry(record *Record, name string) Value { return record.Fields[name] }

func text(record *Record, name string) (string, bool) {
	value, ok := record.Fields[name].(string)
	return value, ok
}

func asRecord(v Value) (*Record, bool) {
	record, ok := v.(*Record)
	return record, ok
}

// schemaValidate is kosika/schema.py's validate: the JSON Schema subset a
// trace's schemas use, with the reference's messages.
func schemaValidate(value, schema Value) {
	if problem := schemaProblem(value, schema, "$", schema); problem != "" {
		replayFail("%s", problem)
	}
}

func jsonTypeMatches(value Value, expected string) bool {
	switch expected {
	case "object":
		_, ok := value.(*Record)
		return ok
	case "array":
		_, ok := value.([]Value)
		return ok
	case "string":
		_, ok := value.(string)
		return ok
	case "number":
		switch x := value.(type) {
		case *big.Int:
			return true
		case float64:
			return !math.IsInf(x, 0) && !math.IsNaN(x)
		}
		return false
	case "integer":
		_, ok := value.(*big.Int)
		return ok
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "null":
		return value == nil
	}
	return false
}

func schemaProblem(value, schemaValue Value, path string, root Value) string {
	schema, ok := schemaValue.(*Record)
	if !ok {
		return ""
	}
	if alternatives, ok := schema.Fields["anyOf"].([]Value); ok {
		var failures []string
		matched := false
		for _, alternative := range alternatives {
			problem := schemaProblem(value, alternative, path, root)
			if problem == "" {
				matched = true
				break
			}
			failures = append(failures, problem)
		}
		if !matched {
			return fmt.Sprintf("%s: matches no allowed form (%s)", path, strings.Join(failures, "; "))
		}
	}
	if ref, has := schema.Fields["$ref"]; has {
		name, isText := ref.(string)
		if !isText || !strings.HasPrefix(name, "#/$defs/") {
			return fmt.Sprintf("%s: unsupported schema reference %s", path, pyReprAny(ref))
		}
		definitions, _ := root.(*Record).Fields["$defs"].(*Record)
		if definitions == nil || definitions.Fields[strings.TrimPrefix(name, "#/$defs/")] == nil {
			return fmt.Sprintf("%s: unknown schema reference %s", path, pyRepr(name))
		}
		return schemaProblem(value, definitions.Fields[strings.TrimPrefix(name, "#/$defs/")], path, root)
	}
	if constant, has := schema.Fields["const"]; has && !valueEqual(value, constant) {
		return fmt.Sprintf("%s: expected constant %s, got %s", path, pyReprAny(constant), pyReprAny(value))
	}
	if allowed, has := schema.Fields["enum"].([]Value); has {
		found := false
		for _, item := range allowed {
			found = found || valueEqual(value, item)
		}
		if !found {
			return fmt.Sprintf("%s: value %s is not in enum", path, pyReprAny(value))
		}
	}
	switch expected := schema.Fields["type"].(type) {
	case []Value:
		matches := false
		var names []string
		for _, item := range expected {
			name, _ := item.(string)
			names = append(names, pyRepr(name))
			matches = matches || jsonTypeMatches(value, name)
		}
		if !matches {
			return fmt.Sprintf("%s: expected one of [%s], got %s", path, strings.Join(names, ", "), pyTypeName(value))
		}
	case string:
		if !jsonTypeMatches(value, expected) {
			return fmt.Sprintf("%s: expected %s, got %s", path, expected, pyTypeName(value))
		}
	}
	if record, ok := value.(*Record); ok {
		required, _ := schema.Fields["required"].([]Value)
		for _, key := range required {
			name, isText := key.(string)
			if _, has := record.Fields[name]; !has || !isText {
				return fmt.Sprintf("%s: missing required property %s", path, pyReprAny(key))
			}
		}
		properties, _ := schema.Fields["properties"].(*Record)
		if properties != nil {
			for _, key := range properties.Keys {
				if child, has := record.Fields[key]; has {
					if problem := schemaProblem(child, properties.Fields[key], path+"."+key, root); problem != "" {
						return problem
					}
				}
			}
		}
		switch additional := schema.Fields["additionalProperties"].(type) {
		case bool:
			if !additional {
				var unknown []string
				for _, key := range record.Keys {
					if properties == nil || properties.Fields[key] == nil {
						if _, declared := properties.Fields[key]; properties == nil || !declared {
							unknown = append(unknown, key)
						}
					}
				}
				if len(unknown) > 0 {
					sort.Strings(unknown)
					return fmt.Sprintf("%s: unknown properties %s", path, pyList(unknown))
				}
			}
		case *Record:
			for _, key := range record.Keys {
				declared := false
				if properties != nil {
					_, declared = properties.Fields[key]
				}
				if !declared {
					if problem := schemaProblem(record.Fields[key], additional, path+"."+key, root); problem != "" {
						return problem
					}
				}
			}
		}
	}
	if items, ok := value.([]Value); ok {
		if itemSchema, ok := schema.Fields["items"].(*Record); ok {
			for i, item := range items {
				if problem := schemaProblem(item, itemSchema, fmt.Sprintf("%s[%d]", path, i), root); problem != "" {
					return problem
				}
			}
		}
	}
	return ""
}

func acceptsNull(schema Value) bool {
	record, ok := schema.(*Record)
	if !ok {
		return false
	}
	if record.Fields["type"] == "null" {
		return true
	}
	options, _ := record.Fields["anyOf"].([]Value)
	for _, option := range options {
		if acceptsNull(option) {
			return true
		}
	}
	return false
}

// fillAbsentSchema is fill_absent over a recorded schema.
func fillAbsentSchema(value, schemaValue Value) Value {
	schema, ok := schemaValue.(*Record)
	if !ok {
		return value
	}
	if items, ok := value.([]Value); ok {
		filled := make([]Value, len(items))
		for i, item := range items {
			filled[i] = fillAbsentSchema(item, schema.Fields["items"])
		}
		return filled
	}
	record, ok := value.(*Record)
	if !ok {
		return value
	}
	properties, ok := schema.Fields["properties"].(*Record)
	if !ok {
		options, _ := schema.Fields["anyOf"].([]Value)
		for _, option := range options {
			if optionRecord, ok := option.(*Record); ok {
				if _, has := optionRecord.Fields["properties"].(*Record); has {
					return fillAbsentSchema(value, option)
				}
			}
		}
		return value
	}
	filled := copyValue(record).(*Record)
	for _, name := range properties.Keys {
		if current, has := filled.Fields[name]; has {
			filled.Fields[name] = fillAbsentSchema(current, properties.Fields[name])
		} else if acceptsNull(properties.Fields[name]) {
			filled.set(name, nil)
		}
	}
	return filled
}

func validateFunctionEvents(events []Value, version, depth int) {
	if len(events) > 0 && depth >= 128 {
		replayFail("Nested function replay exceeds depth limit")
	}
	if len(events)%2 != 0 {
		replayFail("Function expression trace is incomplete")
	}
	for _, item := range events {
		if _, ok := item.(*Record); !ok {
			replayFail("Function expression trace is incomplete")
		}
	}
	for offset := 0; offset < len(events); offset += 2 {
		call, returned := events[offset].(*Record), events[offset+1].(*Record)
		name, named := text(call, "function")
		_, hasArguments := entry(call, "arguments").(*Record)
		_, hasInput := entry(call, "input_schema").(*Record)
		_, hasOutput := entry(call, "output_schema").(*Record)
		if entry(call, "event") != "function_call" || entry(returned, "event") != "function_return" || !named ||
			entry(returned, "function") != name || !hasArguments || !hasInput || !hasOutput {
			replayFail("Function expression trace is mismatched")
		}
		schemaValidate(entry(call, "arguments"), entry(call, "input_schema"))
		schemaValidate(entry(returned, "result"), entry(call, "output_schema"))
		if version >= 5 {
			argumentEvents, argumentsOK := entry(call, "expression_events").([]Value)
			bodyEvents, bodyOK := entry(returned, "events").([]Value)
			if !argumentsOK || !bodyOK {
				replayFail("Function trace requires nested event lists")
			}
			validateFunctionEvents(argumentEvents, version, depth)
			validateFunctionEvents(bodyEvents, version, depth+1)
		}
	}
}

func replayEvents(events []Value, depth int) Value {
	if depth > 32 {
		replayFail("Nested replay exceeds workflow depth limit")
	}
	if len(events) < 6 {
		replayFail("Trace does not contain a completed run")
	}
	for _, item := range events {
		if _, ok := item.(*Record); !ok {
			replayFail("Trace does not contain a completed run")
		}
	}
	start, finish := events[0].(*Record), events[len(events)-1].(*Record)
	if entry(start, "event") != "run_start" || entry(finish, "event") != "run_complete" {
		replayFail("Trace must start and finish with one completed run")
	}
	workflowName, named := text(start, "workflow")
	if !named || entry(finish, "workflow") != workflowName {
		replayFail("Trace workflow mismatch")
	}
	version := 1
	if given, has := start.Fields["trace_version"]; has {
		whole, ok := given.(*big.Int)
		if !ok || !whole.IsInt64() || whole.Int64() < 1 || whole.Int64() > 5 {
			replayFail("Unsupported trace version")
		}
		version = int(whole.Int64())
	}
	middle := events[1 : len(events)-1]
	if len(middle)%4 != 0 {
		replayFail("Trace contains an incomplete step")
	}
	seen := map[string]bool{}
	var result Value
	for offset := 0; offset < len(middle); offset += 4 {
		step, call, returned, completed := middle[offset].(*Record), middle[offset+1].(*Record), middle[offset+2].(*Record), middle[offset+3].(*Record)
		kind, isText := text(call, "event")
		if !isText {
			replayFail("Trace event name must be text")
		}
		key, known := map[string]string{"model_call": "model", "agent_call": "agent", "workflow_call": "workflow", "function_call": "function", "tool_call": "tool"}[kind]
		if !known {
			key = "tool"
		}
		stepName, stepNamed := text(step, "step")
		callName, callNamed := text(call, key)
		_, hasArguments := entry(call, "arguments").(*Record)
		if entry(step, "event") != "step_start" || !known || entry(returned, "event") != key+"_return" ||
			entry(completed, "event") != "step_complete" || !stepNamed || entry(completed, "step") != stepName ||
			seen[stepName] || !callNamed || entry(returned, key) != callName || !hasArguments {
			replayFail("Trace contains mismatched or reordered step events")
		}
		seen[stepName] = true
		result = entry(returned, "result")
		if _, isRecord := result.(*Record); (key != "function" && !isRecord) || !sameJSON(result, entry(completed, "result")) {
			replayFail("Trace step outcome mismatch")
		}
		if version >= 5 {
			expressionEvents, ok := entry(call, "expression_events").([]Value)
			if !ok {
				replayFail("Trace call requires function expression events")
			}
			validateFunctionEvents(expressionEvents, version, 0)
		}
		if key == "workflow" {
			nested, ok := entry(returned, "events").([]Value)
			if !ok {
				replayFail("Workflow trace requires child events")
			}
			childResult := replayEvents(nested, depth+1)
			first, _ := nested[0].(*Record)
			if entry(first, "workflow") != callName || !sameJSON(entry(first, "inputs"), entry(call, "arguments")) ||
				!sameJSON(entry(first, "program_hash"), entry(start, "program_hash")) || !sameJSON(childResult, result) {
				replayFail("Nested workflow trace mismatch")
			}
		}
		if key == "model" || key == "agent" {
			replayAnswer(key, call, returned, result, version)
		}
		if key == "tool" && version >= 3 {
			_, hasInput := entry(call, "input_schema").(*Record)
			_, hasOutput := entry(call, "output_schema").(*Record)
			if !hasInput || !hasOutput {
				replayFail("Tool trace requires input and output schemas")
			}
			schemaValidate(entry(call, "arguments"), entry(call, "input_schema"))
			schemaValidate(result, entry(call, "output_schema"))
		}
		if key == "function" {
			if version < 4 {
				replayFail("Function traces require trace version 4")
			}
			_, hasInput := entry(call, "input_schema").(*Record)
			_, hasOutput := entry(call, "output_schema").(*Record)
			if !hasInput || !hasOutput {
				replayFail("Function trace requires input and output schemas")
			}
			schemaValidate(entry(call, "arguments"), entry(call, "input_schema"))
			schemaValidate(result, entry(call, "output_schema"))
			if version >= 5 {
				body, ok := entry(returned, "events").([]Value)
				if !ok {
					replayFail("Function trace requires body events")
				}
				validateFunctionEvents(body, version, 1)
			}
		}
	}
	if !sameJSON(result, entry(finish, "result")) {
		replayFail("Trace final outcome mismatch")
	}
	if version >= 2 {
		if _, ok := entry(start, "output_schema").(*Record); !ok {
			replayFail("Trace requires its workflow output schema")
		}
		schemaValidate(result, entry(start, "output_schema"))
	}
	return result
}

// replayAnswer reads a model's or an agent's answer again: it must be the
// outcome recorded.
func replayAnswer(key string, call, returned *Record, result Value, version int) {
	_, hasInput := entry(call, "input_schema").(*Record)
	_, hasOutput := entry(call, "output_schema").(*Record)
	if !hasInput || !hasOutput {
		replayFail("Model trace requires input and output schemas")
	}
	schemaValidate(entry(call, "arguments"), entry(call, "input_schema"))
	schemaValidate(result, entry(call, "output_schema"))
	content, hasContent := text(returned, "content")
	if entry(returned, "finish_reason") != "stop" || !hasContent {
		replayFail("Model trace contains an incomplete response")
	}
	parsed, unreadable := parseStrictJSON(content)
	if unreadable != "" {
		replayFail("%s", unreadable)
	}
	answered := fillAbsentSchema(parsed, entry(call, "output_schema"))
	if kind, quoted := call.Fields["evidence"]; quoted {
		// The model answered with the record plus its quotes. Replay proves
		// the quotes again from the recorded input.
		answer, isRecord := answered.(*Record)
		recorded, resultIsRecord := result.(*Record)
		if !isRecord || !resultIsRecord {
			replayFail("Evidence trace requires a record answer")
		}
		var quotes Value
		answered, quotes = splitEvidence(answer)
		if !sameJSON(statedQuotes(quotes), statedQuotes(returned.Fields["evidence"])) {
			replayFail("Model response and recorded evidence differ")
		}
		given := map[string]Value{}
		if arguments, ok := entry(call, "arguments").(*Record); ok {
			given = arguments.Fields
		}
		kindName, _ := kind.(string)
		if problem := evidenceProblem(kindName, given, recorded, quotes); problem != "" {
			replayFail("Trace evidence does not hold: %s", problem)
		}
	}
	if key == "agent" && takesDid(entry(call, "output_schema")) {
		shown := didFrom(entry(returned, "messages"), entry(call, "changes"))
		record, ok := result.(*Record)
		if !ok || !sameJSON(record.Fields["did"], shown) {
			replayFail("Agent trace claims changes its calls do not show")
		}
		if answer, ok := answered.(*Record); ok {
			answer = copyValue(answer).(*Record)
			answer.set("did", shown)
			answered = answer
		}
	}
	if !sameJSON(answered, result) {
		replayFail("Model response and recorded outcome differ")
	}
	if recorded, verified := returned.Fields["verified"]; verified {
		// The program's own check of this answer was recorded as the
		// function call it was. It must be well formed and must have found
		// nothing wrong, or the answer would not be here.
		checked, ok := recorded.([]Value)
		if !ok || len(checked) != 2 {
			replayFail("Verified answer requires its recorded check")
		}
		validateFunctionEvents(checked, version, 0)
		if returnedCheck, ok := checked[1].(*Record); !ok || returnedCheck.Fields["result"] != "" {
			replayFail("Trace keeps an answer its own check refused")
		}
	}
	if key == "agent" {
		validateAgentTranscript(call, returned)
	}
}

func takesDid(schemaValue Value) bool {
	schema, _ := schemaValue.(*Record)
	if schema == nil {
		return false
	}
	properties, _ := schema.Fields["properties"].(*Record)
	if properties == nil {
		return false
	}
	did, _ := properties.Fields["did"].(*Record)
	if did == nil || did.Fields["type"] != "array" {
		return false
	}
	items, _ := did.Fields["items"].(*Record)
	return items != nil && items.Fields["type"] == "string"
}

// didFrom is what an agent changed, read from its transcript: did_from.
func didFrom(messagesValue, changingValue Value) Value {
	changing := map[string]bool{}
	if names, ok := changingValue.([]Value); ok {
		for _, name := range names {
			if text, ok := name.(string); ok {
				changing[text] = true
			}
		}
	}
	did := []Value{}
	messages, ok := messagesValue.([]Value)
	if !ok {
		return did
	}
	results := map[string]Value{}
	for _, item := range messages {
		message, ok := item.(*Record)
		if !ok || message.Fields["role"] != "tool" {
			continue
		}
		content, _ := message.Fields["content"].(string)
		if told, problem := parseStrictJSON(content); problem == "" {
			results[showText(message.Fields["tool_call_id"])] = told
		}
	}
	for _, item := range messages {
		message, ok := item.(*Record)
		if !ok || message.Fields["role"] != "assistant" {
			continue
		}
		calls, _ := message.Fields["tool_calls"].([]Value)
		for _, callValue := range calls {
			call, ok := callValue.(*Record)
			name, _ := call.Fields["name"].(string)
			if !ok || !changing[name] {
				continue
			}
			told, has := results[showText(call.Fields["id"])]
			if !has {
				continue
			}
			if record, ok := told.(*Record); ok && len(record.Keys) == 1 && record.Keys[0] == "error" {
				continue
			}
			arguments, _ := call.Fields["arguments"].(*Record)
			if arguments != nil {
				if path, ok := arguments.Fields["path"].(string); ok && path != "" {
					did = append(did, name+" "+path)
					continue
				}
			}
			did = append(did, name)
		}
	}
	return did
}

// validateAgentTranscript checks an agent's recorded exchange, as
// validate_agent_transcript does.
func validateAgentTranscript(call, returned *Record) {
	messages, ok := entry(returned, "messages").([]Value)
	tools, toolsOK := entry(call, "tools").(*Record)
	if !ok || len(messages) < 3 || !toolsOK {
		replayFail("Agent trace requires messages and granted tool schemas")
	}
	for _, item := range messages {
		message, ok := item.(*Record)
		if !ok {
			replayFail("Invalid agent message")
		}
		role := message.Fields["role"]
		if role == "system" || role == "user" || role == "tool" {
			if _, isText := message.Fields["content"].(string); !isText {
				replayFail("Agent transcript message content must be text")
			}
		}
	}
	limit, ok := entry(call, "max_steps").(*big.Int)
	if !ok || limit.Int64() < 1 || limit.Int64() > 64 {
		replayFail("Invalid agent step limit")
	}
	steps, ok := entry(returned, "model_steps").(*big.Int)
	if !ok {
		replayFail("Invalid recorded model step count")
	}
	for _, key := range tools.Keys {
		schemas, ok := tools.Fields[key].(*Record)
		_, inputOK := schemas.Fields["input_schema"].(*Record)
		_, outputOK := schemas.Fields["output_schema"].(*Record)
		if !ok || !inputOK || !outputOK {
			replayFail("Invalid recorded tool schemas")
		}
	}
	at := 0
	for at < len(messages) && messages[at].(*Record).Fields["role"] == "system" {
		at++
	}
	if at == 0 || at >= len(messages) {
		replayFail("Agent transcript must begin with system and input messages")
	}
	if messages[at].(*Record).Fields["role"] != "user" {
		replayFail("Agent transcript must place input after system messages")
	}
	content, _ := messages[at].(*Record).Fields["content"].(string)
	input, problem := parseStrictJSON(content)
	if problem != "" {
		replayFail("%s", problem)
	}
	if !sameJSON(input, entry(call, "arguments")) {
		replayFail("Agent transcript input mismatch")
	}
	index, modelSteps := at+1, 0
	seenIDs := map[string]bool{}
	for index < len(messages) {
		message := messages[index].(*Record)
		index++
		modelSteps++
		requests, isList := message.Fields["tool_calls"].([]Value)
		if message.Fields["role"] != "assistant" || !isList {
			replayFail("Invalid agent assistant message")
		}
		if len(requests) == 0 {
			if index != len(messages) || !sameJSON(message.Fields["content"], entry(returned, "content")) {
				replayFail("Agent final message mismatch")
			}
			if int64(modelSteps) != steps.Int64() || int64(modelSteps) > limit.Int64() {
				replayFail("Agent step limit mismatch")
			}
			return
		}
		for _, requestValue := range requests {
			request, ok := requestValue.(*Record)
			if !ok {
				replayFail("Invalid recorded agent tool call")
			}
			name, named := request.Fields["name"].(string)
			if _, granted := tools.Fields[name]; !named || !granted {
				replayFail("Agent transcript uses an undeclared tool")
			}
			id, hasID := request.Fields["id"].(string)
			if !hasID || id == "" || seenIDs[id] {
				replayFail("Agent transcript has invalid tool call IDs")
			}
			seenIDs[id] = true
			if index >= len(messages) {
				replayFail("Agent transcript is missing a tool result")
			}
			outcome := messages[index].(*Record)
			index++
			if outcome.Fields["role"] != "tool" || outcome.Fields["name"] != name || outcome.Fields["tool_call_id"] != id {
				replayFail("Agent transcript tool result mismatch")
			}
			toldText, _ := outcome.Fields["content"].(string)
			told, problem := parseStrictJSON(toldText)
			if problem != "" {
				replayFail("%s", problem)
			}
			if record, ok := told.(*Record); ok && len(record.Keys) == 1 && record.Keys[0] == "error" {
				if _, isText := record.Fields["error"].(string); isText {
					continue
				}
			}
			schemas := tools.Fields[name].(*Record)
			schemaValidate(request.Fields["arguments"], schemas.Fields["input_schema"])
			schemaValidate(told, schemas.Fields["output_schema"])
		}
	}
	replayFail("Agent transcript has no final answer")
}
