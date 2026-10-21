package main

// Reading tools, agents and workflows, as kosika/parser.py reads them. A
// step this engine cannot run yet (fetching a page) is refused by name.

import (
	"strconv"
	"strings"
)

func (p *Parser) stringOrIdentifier() string {
	token := p.current()
	if token.Kind != "STRING" && token.Kind != "IDENT" {
		parseFail("Expected string or identifier at %d:%d", token.Line, token.Column)
	}
	p.advance()
	return token.Value
}

func notYetStep(token Token, what string) {
	parseFail("kosika-native cannot run %s yet (at %d:%d); it runs steps that call a function, run a workflow, use a tool, or ask a model or an agent", what, token.Line, token.Column)
}

func (p *Parser) parseTool() *ToolDecl {
	p.expect("tool")
	tool := &ToolDecl{Name: p.identifier()}
	p.expect("{")
	values := map[string]string{}
	for !p.accept("}") {
		key := p.identifier()
		if key == "source" {
			if p.check("system") {
				token := p.advance()
				kind := p.stringOrIdentifier()
				if hasName(systemKinds, kind) && !systemKindsHere[kind] {
					parseFail("kosika-native cannot read a system %s tool yet (at %d:%d); it reaches files and programs, and other tools through MCP servers", kind, token.Line, token.Column)
				}
				values["source"] = systemPrefix + kind
				continue
			}
			p.expect("mcp")
			values["source"] = p.stringOrIdentifier()
			continue
		}
		p.expect(":")
		values[key] = p.stringOrIdentifier()
	}
	var missing []string
	for _, key := range []string{"access", "operation", "output", "source"} {
		if _, ok := values[key]; !ok {
			missing = append(missing, key)
		}
	}
	if len(missing) > 0 {
		parseFail("Tool %s is missing: %s", pyRepr(tool.Name), strings.Join(missing, ", "))
	}
	tool.Source, tool.Operation, tool.Access = values["source"], values["operation"], values["access"]
	tool.Output, tool.Input = values["output"], values["input"]
	return tool
}

func (p *Parser) parseNameList() []string {
	p.expect("[")
	var names []string
	for !p.accept("]") {
		names = append(names, p.identifier())
		p.accept(",")
	}
	return names
}

func (p *Parser) parseAgent() *AgentDecl {
	p.expect("agent")
	agent := &AgentDecl{Name: p.identifier(), MaxSteps: 8, MaxToolCalls: 256, MaxModelCalls: 64}
	p.expect("{")
	seen := map[string]bool{}
	for !p.accept("}") {
		key := p.identifier()
		if seen[key] {
			parseFail("Duplicate agent field %s", pyRepr(key))
		}
		seen[key] = true
		p.expect(":")
		switch key {
		case "can":
			agent.Can = p.parseNameList()
		case "cannot":
			agent.Cannot = p.parseNameList()
		case "model":
			agent.Model = p.identifier()
		case "instructions":
			text := p.stringOrIdentifier()
			agent.Instructions = &text
		case "max_steps", "max_tool_calls", "max_model_calls":
			token := p.advance()
			if token.Kind != "NUMBER" || !isDigits(token.Value) {
				parseFail("Agent %s must be a positive integer", key)
			}
			n, _ := strconv.Atoi(token.Value)
			switch key {
			case "max_steps":
				agent.MaxSteps = n
			case "max_tool_calls":
				agent.MaxToolCalls = n
			default:
				agent.MaxModelCalls = n
			}
		case "deadline_seconds":
			token := p.advance()
			if token.Kind != "NUMBER" {
				parseFail("Agent deadline_seconds must be a positive number")
			}
			seconds, _ := strconv.ParseFloat(token.Value, 64)
			agent.Deadline = &seconds
		case "plan":
			agent.Plan = p.identifier()
			if agent.Plan != "first" && agent.Plan != "off" {
				parseFail("Agent plan must be `first` or `off`")
			}
		case "check":
			agent.Check = p.identifier()
		default:
			parseFail("Unknown agent field %s", pyRepr(key))
		}
	}
	return agent
}

func (p *Parser) parseWorkflow() *WorkflowDecl {
	p.expect("workflow")
	workflow := &WorkflowDecl{Name: p.identifier()}
	p.expect("(")
	for !p.accept(")") {
		name := p.identifier()
		p.expect(":")
		workflow.Params = append(workflow.Params, Param{name, p.identifier()})
		if !p.check(")") {
			p.expect(",")
		}
	}
	p.expect("->")
	workflow.Out = p.identifier()
	p.expect("{")
	groups := 0
	for !p.accept("}") {
		token := p.current()
		switch {
		case p.accept("step"):
			workflow.Steps = append(workflow.Steps, p.parseLongStep())
		case p.atAssignment() || p.atTypedStep():
			workflow.Steps = append(workflow.Steps, p.parseShortStep())
		case p.check("parallel") && p.peek().Value == "{":
			// `parallel { a = ...  b = ... }`: steps that may run at once.
			// This engine runs them in the order written, which gives the
			// trace the reference keeps.
			p.advance()
			p.advance()
			groups++
			for !p.accept("}") {
				if !p.atAssignment() && !p.atTypedStep() {
					inner := p.current()
					parseFail("Unexpected workflow item %s at %d:%d", quoted(inner.Value), inner.Line, inner.Column)
				}
				step := p.parseShortStep()
				step.Group = groups
				workflow.Steps = append(workflow.Steps, step)
			}
		case p.accept("require"):
			p.expect("approval")
			workflow.Approval = true
		case p.accept("budget"):
			workflow.Budget = append(workflow.Budget, p.parseBudget()...)
		default:
			parseFail("Unexpected workflow item %s at %d:%d", quoted(token.Value), token.Line, token.Column)
		}
	}
	return workflow
}

func (p *Parser) parseBudget() []Budget {
	var items []Budget
	for {
		token := p.advance()
		if token.Kind != "NUMBER" {
			parseFail("Expected 'amount' at %d:%d, got %s", token.Line, token.Column, quoted(token.Value))
		}
		amount := numberLiteral(token.Value, false)
		switch {
		case p.accept("model"):
			p.expect("calls")
			items = append(items, Budget{"model_calls", amount})
		case p.accept("tool"):
			p.expect("calls")
			items = append(items, Budget{"tool_calls", amount})
		default:
			p.expect("seconds")
			items = append(items, Budget{"seconds", amount})
		}
		if !p.accept(",") {
			return items
		}
	}
}

// stepEnding reads what may follow a step's call: the program's check of
// a model's answer, votes, asking a model again, a stronger model, and a
// fallback.
func (p *Parser) stepEnding(step *Step) {
	if p.accept("verify") {
		step.Verify = p.parseStepCall()
	}
	step.Votes = p.stepCount("vote")
	step.Retries = p.stepCount("retry")
	if p.accept("escalate") {
		step.Escalate = p.identifier()
	}
	if p.check("learn") && p.peek().Value != "=" && p.peek().Value != ":" {
		p.advance()
		step.Learns = true
		if p.accept("by") {
			step.LearnBy = p.expression()
		}
	}
	if p.accept("otherwise") {
		step.Fallback = p.parseStepCall()
	}
}

// stepCount reads an optional `vote N` or `retry N`.
func (p *Parser) stepCount(word string) int {
	if !p.accept(word) {
		return 0
	}
	token := p.advance()
	if token.Kind != "NUMBER" || !isDigits(token.Value) {
		parseFail("Expected 'count' at %d:%d, got %s", token.Line, token.Column, quoted(token.Value))
	}
	count, _ := strconv.Atoi(token.Value)
	return count
}

func (p *Parser) parseShortStep() *Step {
	name := p.identifier()
	written := ""
	if p.accept(":") {
		written = p.parseTypeName(0)
	}
	p.expect("=")
	if written == "" && p.check("match") {
		if step := p.parseMatchStep(name); step != nil {
			return step
		}
	}
	step := &Step{Name: name, Call: p.parseStepOrValue(), Binding: name, BindingType: written, Return: name}
	p.stepEnding(step)
	return step
}

// parseMatchStep reads a step that makes one of several calls, chosen by a
// value. A case may give a value instead of a call, typed from the calls
// beside it. When no case makes a call, as `size = match n { case 1 { "one" }
// case _ { "many" } }`, the step is a plain value, an ordinary `match`: nil
// is given back and the parser is where it was.
func (p *Parser) parseMatchStep(name string) (found *Step) {
	start := p.index
	defer func() {
		if caught := recover(); caught != nil {
			if !isPlainParseError(caught) {
				panic(caught)
			}
			found = nil
		}
		if found != nil {
			for _, branch := range found.Branches {
				if !isValueCall(branch.Call) {
					return
				}
			}
		}
		found = nil
		p.index = start
	}()
	p.expect("match")
	step := &Step{Name: name, Binding: name, Return: name, Subject: p.parseOr()}
	p.expect("{")
	for !p.accept("}") {
		p.expect("case")
		branch := Branch{Any: true}
		if !p.accept("_") {
			token := p.advance()
			branch.Any = false
			switch {
			case token.Kind == "STRING":
				branch.Pattern = token.Value
			case token.Kind == "NUMBER":
				branch.Pattern = numberLiteral(token.Value, false)
			case token.Kind == "IDENT" && (token.Value == "true" || token.Value == "false"):
				branch.Pattern = token.Value == "true"
			default:
				parseFail("Expected 'value' at %d:%d, got %s", token.Line, token.Column, quoted(token.Value))
			}
		}
		p.expect("{")
		branch.Call = p.parseStepOrValue()
		p.expect("}")
		step.Branches = append(step.Branches, branch)
	}
	if len(step.Branches) == 0 {
		p.expect("case")
	}
	step.Call = step.Branches[0].Call
	return step
}

// callWords are words that, followed by a name, can only begin a step's
// call.
var callWords = map[string]bool{"classify": true, "extract": true, "summarize": true, "decide": true, "use_model": true, "ask": true, "execute": true, "recall": true, "remember": true, "call": true, "post": true, "fetch": true, "read": true, "write": true, "list": true}

// afterACall is what may follow a step's call on its line: its modifiers,
// or the end of a block.
var afterACall = map[string]bool{"verify": true, "vote": true, "retry": true, "escalate": true, "learn": true, "otherwise": true, "}": true}

// parseStepOrValue reads a step's call, or else a plain value: `count =
// before + 1`, `size = if big { 2 } else { 1 }`. A value is worked out by
// code, like a function the step calls; LowerValues makes it one. What
// reads as a call followed by more of an expression on its line, as
// `f(x) + 1`, is a value too.
func (p *Parser) parseStepOrValue() *StepCall {
	start := p.index
	token := p.current()
	following := p.peek()
	if token.Kind == "IDENT" && callWords[token.Value] && (following.Kind == "IDENT" || following.Kind == "STRING") && following.Line == token.Line {
		// `recall notes under k`: a call however it is written, so a
		// mistake in it is reported as one.
		return p.parseStepCall()
	}
	if call := p.tryStepCall(); call != nil {
		after, ended := p.current(), p.tokens[p.index-1]
		if after.Line != ended.Line || after.Kind == "EOF" || afterACall[after.Value] {
			return call
		}
	}
	p.index = start
	return &StepCall{Kind: "function", Name: valueCall, Args: []Arg{{"value", p.expression()}}, Line: token.Line}
}

// isPlainParseError is true of a parse error that only says what was
// expected, not of one saying this engine cannot run something yet.
func isPlainParseError(caught any) bool {
	failure, ok := caught.(*ParseError)
	return ok && !strings.HasPrefix(failure.Message, "kosika-native cannot")
}

// tryStepCall is parseStepCall, or nil where what follows is not a call.
func (p *Parser) tryStepCall() (call *StepCall) {
	defer func() {
		if caught := recover(); caught != nil {
			if !isPlainParseError(caught) {
				panic(caught)
			}
			call = nil
		}
	}()
	return p.parseStepCall()
}

func (p *Parser) parseLongStep() *Step {
	step := &Step{Name: p.identifier()}
	p.expect("{")
	p.expect("let")
	step.Binding = p.identifier()
	if p.accept(":") {
		step.BindingType = p.parseTypeName(0)
	}
	p.expect("=")
	step.Call = p.parseStepCall()
	p.stepEnding(step)
	p.expect("return")
	step.Return = p.identifier()
	p.expect("}")
	return step
}

var thinkingVerbs = map[string]bool{"classify": true, "extract": true, "summarize": true, "decide": true}

func (p *Parser) parseStepCall() *StepCall {
	token := p.current()
	following := p.peek().Value
	if p.check("{") {
		// A value where a call belongs: the reference makes it a call of a
		// function that gives it.
		return &StepCall{Kind: "function", Name: valueCall, Args: []Arg{{"value", p.expression()}}, Line: token.Line}
	}
	if token.Kind == "IDENT" {
		switch {
		case token.Value == "remember" && following != "(":
			// `remember note in customers under key`: a call of the
			// memory's tool; see stores.go.
			p.advance()
			value := p.expression()
			p.expect("in")
			name := p.identifier()
			p.expect("under")
			return &StepCall{Kind: "tool", Agent: memoryAgent, Name: "remember_" + name, Args: []Arg{{"key", p.expression()}, {"value", value}}, Line: token.Line}
		case token.Value == "post" && following != "(":
			// `post remark to desk as critic`: a call of the board's tool.
			p.advance()
			body := p.expression()
			p.expect("to")
			name := p.identifier()
			p.expect("as")
			author := p.identifier()
			return &StepCall{Kind: "tool", Agent: boardAgent, Name: "post_" + name, Args: []Arg{{"author", &Literal{author}}, {"body", body}}, Line: token.Line}
		case token.Value == "read" && following == "file":
			// Files in one line: see direct.go.
			p.advance()
			p.advance()
			return &StepCall{Kind: "tool", Agent: directAgent, Name: "file_read", Args: []Arg{{"path", p.expression()}}, Line: token.Line}
		case token.Value == "read" && p.peek().Kind == "IDENT" && following != "(":
			p.advance()
			return &StepCall{Kind: "tool", Agent: boardAgent, Name: "read_" + p.identifier(), Line: token.Line}
		case token.Value == "list" && following == "files":
			p.advance()
			p.advance()
			return &StepCall{Kind: "tool", Agent: directAgent, Name: "file_list", Args: []Arg{{"path", p.expression()}}, Line: token.Line}
		case token.Value == "write" && following == "file":
			p.advance()
			p.advance()
			target := p.expression()
			p.expect("with")
			return &StepCall{Kind: "tool", Agent: directAgent, Name: "file_write", Args: []Arg{{"path", target}, {"text", p.expression()}}, Line: token.Line}
		case token.Value == "fetch" && following != "(" && following != "." && following != "=":
			notYetStep(token, "a step that fetches a page")
		case token.Value == "now" && following != "(" && following != ".":
			// `today = now`, `roll = random 1 to 6`: see ambient.go.
			p.advance()
			return &StepCall{Kind: "tool", Agent: worldAgent, Name: clockTool, Line: token.Line}
		case token.Value == "random" && following != "(" && following != "." && following != "=":
			p.advance()
			low := p.expression()
			p.expect("to")
			return &StepCall{Kind: "tool", Agent: worldAgent, Name: randomTool, Args: []Arg{{"low", low}, {"high", p.expression()}}, Line: token.Line}
		case token.Value == "recall" && following != "(":
			p.advance()
			name := p.identifier()
			p.expect("under")
			return &StepCall{Kind: "tool", Agent: memoryAgent, Name: "recall_" + name, Args: []Arg{{"key", p.expression()}}, Line: token.Line}
		}
	}
	if token.Kind == "IDENT" && thinkingVerbs[token.Value] && following != "(" {
		return p.parseThinkCall()
	}
	if p.accept("ask") {
		name := p.identifier()
		p.expect("(")
		return &StepCall{Kind: "agent", Name: name, Args: p.parseArguments(), Line: token.Line}
	}
	if p.accept("use_model") {
		name := p.identifier()
		p.expect("(")
		return &StepCall{Kind: "model", Name: name, Args: p.parseArguments(), Line: token.Line}
	}
	if p.accept("call") {
		name := p.identifier()
		p.expect("(")
		return &StepCall{Kind: "workflow", Name: name, Args: p.parseArguments(), Line: token.Line}
	}
	if p.current().Kind == "IDENT" && p.current().Value != "execute" {
		name := p.identifier()
		p.expect("(")
		return &StepCall{Kind: "function", Name: name, Args: p.parseArguments(), Line: token.Line}
	}
	p.expect("execute")
	agent := p.identifier()
	p.expect(".")
	tool := p.identifier()
	p.expect("(")
	return &StepCall{Kind: "tool", Name: tool, Agent: agent, Args: p.parseArguments(), Line: token.Line}
}

// parseThinkCall reads a thinking step: a verb, its subject, and its
// keyword parts. It is made a model call before it is checked.
func (p *Parser) parseThinkCall() *StepCall {
	token := p.advance()
	call := &StepCall{Kind: "think", Verb: token.Value, Line: token.Line}
	if call.Verb == "extract" {
		call.Record = p.identifier()
		p.expect("from")
	}
	subject := p.expression()
	switch call.Verb {
	case "classify":
		p.expect("into")
		call.Args = []Arg{{"text", subject}, {"choices", p.expression()}}
	case "decide":
		p.expect("given")
		call.Args = []Arg{{"question", subject}, {"given", p.expression()}}
	default:
		call.Args = []Arg{{"text", subject}}
	}
	if p.accept("with") {
		p.expect("evidence")
		call.Evidence = true
	}
	if p.accept("using") {
		call.Model = p.identifier()
		for p.accept(",") {
			call.More = append(call.More, p.identifier())
		}
	}
	return call
}
