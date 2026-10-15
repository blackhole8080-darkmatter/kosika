package main

// Reading tools, agents and workflows, as kosika/parser.py reads them. A
// step this engine cannot run yet (files, pages, the clock, chance) is
// refused by name.

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
				parseFail("kosika-native cannot read a system tool yet (at %d:%d); it reaches tools only through a test's scripted answers", token.Line, token.Column)
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
	for !p.accept("}") {
		token := p.current()
		switch {
		case p.accept("step"):
			workflow.Steps = append(workflow.Steps, p.parseLongStep())
		case p.atAssignment():
			workflow.Steps = append(workflow.Steps, p.parseShortStep())
		case p.check("parallel") && p.peek().Value == "{":
			notYetStep(token, "a parallel block")
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

// stepEnding reads what may follow a step's call: asking a model again,
// and a fallback. A check, votes and a stronger model are refused.
func (p *Parser) stepEnding(step *Step) {
	for _, word := range []string{"verify", "vote"} {
		if p.check(word) {
			notYetStep(p.current(), "a step with "+word)
		}
	}
	if p.accept("retry") {
		token := p.advance()
		if token.Kind != "NUMBER" || !isDigits(token.Value) {
			parseFail("Expected 'count' at %d:%d, got %s", token.Line, token.Column, quoted(token.Value))
		}
		step.Retries, _ = strconv.Atoi(token.Value)
	}
	if p.check("escalate") {
		notYetStep(p.current(), "a step with escalate")
	}
	if p.accept("otherwise") {
		step.Fallback = p.parseStepCall()
	}
}

func (p *Parser) parseShortStep() *Step {
	name := p.identifier()
	p.expect("=")
	if p.accept("match") {
		// The step makes one of several calls, chosen by a value.
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
			branch.Call = p.parseStepCall()
			p.expect("}")
			step.Branches = append(step.Branches, branch)
		}
		if len(step.Branches) == 0 {
			p.expect("case")
		}
		step.Call = step.Branches[0].Call
		return step
	}
	step := &Step{Name: name, Call: p.parseStepCall(), Binding: name, Return: name}
	p.stepEnding(step)
	return step
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
			notYetStep(token, "a step that reads a file")
		case token.Value == "read" && p.peek().Kind == "IDENT" && following != "(":
			p.advance()
			return &StepCall{Kind: "tool", Agent: boardAgent, Name: "read_" + p.identifier(), Line: token.Line}
		case token.Value == "list" && following == "files":
			notYetStep(token, "a step that lists files")
		case token.Value == "write" && following == "file":
			notYetStep(token, "a step that writes a file")
		case token.Value == "fetch" && following != "(" && following != "." && following != "=":
			notYetStep(token, "a step that fetches a page")
		case token.Value == "now" && following != "(" && following != ".":
			notYetStep(token, "a step that reads the clock")
		case token.Value == "random" && following != "(" && following != "." && following != "=":
			notYetStep(token, "a step that picks a random number")
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
	if p.check("with") {
		notYetStep(p.current(), "a step with evidence")
	}
	if p.accept("using") {
		call.Model = p.identifier()
		for p.accept(",") {
			call.More = append(call.More, p.identifier())
		}
	}
	return call
}
