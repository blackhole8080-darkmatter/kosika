package main

// Checking tools, agents and workflows, and the tests that run workflows,
// as kosika/checker.py and kosika/testing.py do.

import (
	"fmt"
	"math"
	"math/big"
	"sort"
	"strings"
)

var allowedAccess = map[string]bool{"observe": true, "analyze": true, "prepare": true, "execute": true}

func (c *checker) checkValueSteps() {
	for _, workflow := range c.program.Workflows {
		for _, step := range workflow.Steps {
			if step.Call.Kind == "function" && step.Call.Name == valueCall {
				checkFail("Step %s has a value where a call is needed; a value may stand after 'otherwise' or in a 'case', beside a call whose result type it takes", pyRepr(step.Name))
			}
		}
	}
}

// pyNumber writes a budget's amount as Python's str() does.
func pyNumber(v Value) string {
	if f, ok := v.(float64); ok {
		text := formatFloat(f)
		if !strings.ContainsAny(text, ".en") {
			text += ".0"
		}
		return text
	}
	return formatNumber(v)
}

func (c *checker) checkBudgets() {
	for _, workflow := range c.program.Workflows {
		counts := map[string]int{}
		for _, item := range workflow.Budget {
			counts[item.Kind]++
		}
		for _, item := range workflow.Budget {
			label := strings.ReplaceAll(item.Kind, "_", " ")
			if counts[item.Kind] > 1 {
				checkFail("Workflow %s budget names %s more than once", pyRepr(workflow.Name), label)
			}
			var ok bool
			if item.Kind == "seconds" {
				ok = toFloat(item.Amount) > 0 && toFloat(item.Amount) <= 86_400
			} else if whole, isWhole := item.Amount.(*big.Int); isWhole {
				ok = whole.Sign() > 0 && whole.Cmp(big.NewInt(1_000_000)) <= 0
			}
			if !ok {
				checkFail("Workflow %s budget for %s is out of range, got %s", pyRepr(workflow.Name), label, pyNumber(item.Amount))
			}
		}
		for _, step := range workflow.Steps {
			if step.Retries == 0 {
				continue
			}
			if step.Call.Kind != "model" {
				checkFail("Step %s cannot retry: only a model step (use_model or a thinking step) can be asked again", pyRepr(step.Name))
			}
			if step.Retries < 1 || step.Retries > 5 {
				checkFail("Step %s cannot retry: the count must be from 1 to 5, got %d", pyRepr(step.Name), step.Retries)
			}
		}
	}
}

func finiteBetween(value *float64, low, high float64) bool {
	return value == nil || (!math.IsInf(*value, 0) && !math.IsNaN(*value) && *value >= low && *value <= high)
}

// checkModels: what each model takes and gives, and its parameters.
func (c *checker) checkModels() {
	if len(c.models) != len(c.program.Models) {
		checkFail("Duplicate model declaration")
	}
	for _, model := range c.program.Models {
		name := pyRepr(model.Name)
		if c.records[model.Input] == nil || c.records[model.Output] == nil {
			checkFail("Model %s requires known input and output records", name)
		}
		if model.Deployment != nil && *model.Deployment == "" {
			checkFail("Model %s deployment must not be empty", name)
		}
		if !finiteBetween(model.Temperature, 0, 2) {
			checkFail("Model %s temperature must be between 0 and 2", name)
		}
		if model.MaxOutputTokens != nil && (*model.MaxOutputTokens < 1 || *model.MaxOutputTokens > 1_000_000) {
			checkFail("Model %s max_output_tokens must be between 1 and 1000000", name)
		}
		for _, parameter := range []struct {
			name      string
			value     *float64
			low, high float64
		}{{"top_p", model.TopP, 0, 1}, {"frequency_penalty", model.FrequencyPenalty, -2, 2}, {"presence_penalty", model.PresencePenalty, -2, 2}} {
			if !finiteBetween(parameter.value, parameter.low, parameter.high) {
				checkFail("Model %s %s must be between %d and %d", name, parameter.name, int(parameter.low), int(parameter.high))
			}
		}
		unique := len(uniqueNames(model.Stop)) == len(model.Stop)
		for _, stop := range model.Stop {
			if n := len([]rune(stop)); n < 1 || n > 256 {
				unique = false
			}
		}
		if len(model.Stop) > 4 || !unique {
			checkFail("Model %s stop must contain up to four unique non-empty strings of at most 256 characters", name)
		}
	}
}

// checkWorkflowCalls: no workflow runs itself, and none runs too deep.
func (c *checker) checkWorkflowCalls() {
	heights := map[string]int{}
	active := map[string]bool{}
	var visit func(string) int
	visit = func(name string) int {
		if active[name] {
			checkFail("Recursive workflow call involving %s", pyRepr(name))
		}
		if height, done := heights[name]; done {
			return height
		}
		workflow := c.workflows[name]
		if workflow == nil {
			checkFail("Unknown workflow %s", pyRepr(name))
		}
		if len(active) >= 32 {
			checkFail("Workflow call depth exceeds 32")
		}
		active[name] = true
		height := 1
		for _, step := range workflow.Steps {
			if step.Call.Kind == "workflow" {
				height = max(height, 1+visit(step.Call.Name))
			}
		}
		delete(active, name)
		if height > 32 {
			checkFail("Workflow call depth exceeds 32")
		}
		heights[name] = height
		return height
	}
	for _, workflow := range c.program.Workflows {
		visit(workflow.Name)
	}
}

func (c *checker) checkTools() {
	for _, tool := range c.program.Tools {
		if !allowedAccess[tool.Access] {
			checkFail("Tool %s has invalid access level %s", pyRepr(tool.Name), pyRepr(tool.Access))
		}
		if c.records[tool.Output] == nil {
			checkFail("Tool %s returns unknown record %s", pyRepr(tool.Name), pyRepr(tool.Output))
		}
		if tool.Input != "" && c.records[tool.Input] == nil {
			checkFail("Tool %s accepts unknown record %s", pyRepr(tool.Name), pyRepr(tool.Input))
		}
	}
}

// planCheckFits: an agent's check is fn NAME(request: Text, files: R[]) ->
// Text[], where R has exactly the fields of a file a plan would write.
func (c *checker) planCheckFits(name string) bool {
	var decl *FuncDecl
	for _, function := range c.program.Functions {
		if function.Name == name {
			decl = function
			break
		}
	}
	if decl == nil || len(decl.Params) != 2 || decl.Out != "Text[]" {
		return false
	}
	if decl.Params[0].Type != "Text" || !strings.HasSuffix(decl.Params[1].Type, "[]") {
		return false
	}
	record := c.records[strings.TrimSuffix(decl.Params[1].Type, "[]")]
	if record == nil || len(record.Fields) != 4 {
		return false
	}
	want := map[string]string{"path": "Text", "text": "Text", "before": "Text", "exists": "Bool"}
	got := map[string]string{}
	for _, field := range record.Fields {
		got[field.Name] = field.Type
	}
	if len(got) != len(want) {
		return false
	}
	for key, value := range want {
		if got[key] != value {
			return false
		}
	}
	return true
}

func (c *checker) checkAgents() {
	for _, agent := range c.program.Agents {
		name := pyRepr(agent.Name)
		if agent.Model != "" && c.models[agent.Model] == nil {
			checkFail("Agent %s uses unknown model %s", name, pyRepr(agent.Model))
		}
		if agent.Instructions != nil && strings.TrimSpace(*agent.Instructions) == "" {
			checkFail("Agent %s instructions must not be empty", name)
		}
		if agent.Deadline != nil && (math.IsInf(*agent.Deadline, 0) || math.IsNaN(*agent.Deadline) || *agent.Deadline <= 0 || *agent.Deadline > 86_400) {
			checkFail("Agent %s deadline_seconds must be between 0 and 86400", name)
		}
		if agent.MaxSteps < 1 || agent.MaxSteps > 64 {
			checkFail("Agent %s max_steps must be between 1 and 64", name)
		}
		if agent.Plan == "first" {
			if agent.Model == "" {
				checkFail("Agent %s plans first, so it needs a model to write the plan", name)
			}
			// A name that is not a tool is reported as that, further on.
			allTools, changes := true, false
			for _, tool := range agent.Can {
				declared := c.tools[tool]
				allTools = allTools && declared != nil
				changes = changes || (declared != nil && declared.Access == "execute")
			}
			if allTools && !changes {
				checkFail("Agent %s plans first but can change nothing: give it a tool with `access: execute`, or remove `plan: first`", name)
			}
			if agent.MaxSteps < 2 {
				checkFail("Agent %s plans first, so max_steps must be at least 2: one turn to plan and one to answer", name)
			}
		}
		if agent.Check != "" {
			found := false
			for _, function := range c.program.Functions {
				found = found || function.Name == agent.Check
			}
			if !found {
				checkFail("Agent %s check %s is not a function declared in the program", name, pyRepr(agent.Check))
			}
			if !c.planCheckFits(agent.Check) {
				checkFail("Agent %s check %s must be a function `fn NAME(request: Text, files: R[]) -> Text[]`, where R is a record with exactly the fields {path: Text, text: Text, before: Text, exists: Bool}", name, pyRepr(agent.Check))
			}
		}
		if agent.MaxToolCalls < 1 || agent.MaxToolCalls > 1_000_000 {
			checkFail("Agent %s max_tool_calls must be between 1 and 1000000", name)
		}
		if agent.MaxModelCalls < 1 || agent.MaxModelCalls > 1_000_000 {
			checkFail("Agent %s max_model_calls must be between 1 and 1000000", name)
		}
		for _, tool := range agent.Can {
			if c.tools[tool] == nil {
				checkFail("Agent %s can use unknown tool %s", name, pyRepr(tool))
			}
		}
		var overlap []string
		for _, tool := range uniqueNames(agent.Can) {
			if hasName(agent.Cannot, tool) {
				overlap = append(overlap, tool)
			}
		}
		sort.Strings(overlap)
		if len(overlap) > 0 {
			checkFail("Agent %s both allows and denies %s", name, pyList(overlap))
		}
	}
}

// stepParams are the names a step's call takes, for filling in unnamed
// arguments: as kosika/sugar.py finds them, by the called name alone.
func (c *checker) stepParams(call *StepCall) []string {
	switch call.Kind {
	case "function":
		if decl := c.functions[call.Name]; decl != nil && len(decl.Params) > 0 {
			return paramNames(decl)
		}
		if workflow := c.workflows[call.Name]; workflow != nil {
			return fieldNames(workflow.Params)
		}
		return nil
	case "workflow":
		if workflow := c.workflows[call.Name]; workflow != nil {
			return fieldNames(workflow.Params)
		}
		return nil
	case "model":
		if model := c.models[call.Name]; model != nil && c.records[model.Input] != nil {
			return fieldNames(c.records[model.Input].Fields)
		}
		return nil
	case "agent":
		if agent := c.agents[call.Name]; agent != nil {
			if model := c.models[agent.Model]; model != nil && c.records[model.Input] != nil {
				return fieldNames(c.records[model.Input].Fields)
			}
		}
		return nil
	}
	if tool := c.tools[call.Name]; tool != nil && tool.Input != "" {
		if record := c.records[tool.Input]; record != nil {
			return fieldNames(record.Fields)
		}
	}
	return nil
}

// finalOutput is the record a step's call gives, or "" when that cannot be
// known before the checks that follow.
func (c *checker) finalOutput(call *StepCall) string {
	switch call.Kind {
	case "workflow":
		if workflow := c.workflows[call.Name]; workflow != nil {
			return workflow.Out
		}
	case "function":
		if decl := c.functions[call.Name]; decl != nil {
			return decl.Out
		}
	case "tool":
		if tool := c.tools[call.Name]; tool != nil {
			return tool.Output
		}
	case "model":
		if model := c.models[call.Name]; model != nil {
			return model.Output
		}
	case "agent":
		if agent := c.agents[call.Name]; agent != nil {
			if model := c.models[agent.Model]; model != nil {
				return model.Output
			}
		}
	}
	return ""
}

func (c *checker) checkWorkflows() {
	for _, workflow := range c.program.Workflows {
		name := pyRepr(workflow.Name)
		if c.records[workflow.Out] == nil {
			checkFail("Workflow %s returns unknown record %s", name, pyRepr(workflow.Out))
		}
		if len(workflow.Steps) == 0 {
			checkFail("Workflow %s has no steps", name)
		}
		var stepNames []string
		for _, step := range workflow.Steps {
			stepNames = append(stepNames, step.Name)
		}
		if len(uniqueNames(stepNames)) != len(stepNames) {
			checkFail("Workflow %s has duplicate step names", name)
		}
		final := workflow.Steps[len(workflow.Steps)-1].Call
		if out := c.finalOutput(final); out != "" && out != workflow.Out {
			kind := "call"
			if final.Kind == "tool" {
				kind = "tool"
			}
			checkFail("Workflow %s returns %s, but final %s returns %s", name, pyRepr(workflow.Out), kind, pyRepr(out))
		}
		env := newEnv()
		for _, param := range workflow.Params {
			if _, taken := env.types[param.Name]; taken {
				checkFail("Duplicate workflow parameter %s", pyRepr(param.Name))
			}
			if c.records[param.Type] == nil {
				checkFail("Workflow parameter %s requires a known record", pyRepr(param.Name))
			}
			env.types[param.Name] = param.Type
		}
		for _, step := range workflow.Steps {
			c.checkStep(workflow, step, env)
		}
	}
}

func (c *checker) checkStep(workflow *WorkflowDecl, step *Step, env *Env) {
	if step.Subject != nil {
		subject := c.typeOf(step.Subject, env.child(), step.Name, "")
		if subject != "Text" && subject != "Number" && subject != "Bool" {
			checkFail("Step %s match expects Text, Number, or Bool, got %s", pyRepr(step.Name), pyRepr(subject))
		}
	}
	call := step.Call
	var params []Param
	var output string
	switch call.Kind {
	case "workflow":
		child := c.workflows[call.Name]
		params, output = child.Params, child.Out
		if child.Approval && !workflow.Approval {
			checkFail("Workflow %s must require approval for %s", pyRepr(workflow.Name), pyRepr(child.Name))
		}
	case "function":
		if t, bound := env.types[call.Name]; bound {
			signatureParams, out, ok := signature(t)
			if !ok {
				checkFail("%s is not a function value", pyRepr(call.Name))
			}
			params, output = signatureParams, out
		} else {
			decl := c.functions[call.Name]
			if decl == nil {
				checkFail("Step %s uses unknown function %s", pyRepr(step.Name), pyRepr(call.Name))
			}
			params, output = decl.Params, decl.Out
		}
	case "tool":
		agent := c.agents[call.Agent]
		if agent == nil {
			checkFail("Step %s uses unknown agent %s", pyRepr(step.Name), pyRepr(call.Agent))
		}
		tool := c.tools[call.Name]
		if tool == nil {
			checkFail("Step %s uses unknown tool %s", pyRepr(step.Name), pyRepr(call.Name))
		}
		if !hasName(agent.Can, call.Name) {
			checkFail("Capability violation: agent %s cannot use tool %s", pyRepr(agent.Name), pyRepr(tool.Name))
		}
		if tool.Access == "execute" && !workflow.Approval {
			checkFail("Workflow %s needs approval before execute tool %s", pyRepr(workflow.Name), pyRepr(tool.Name))
		}
		if tool.Input != "" {
			params = c.recordFields(tool.Input)
		}
		output = tool.Output
	case "model":
		model := c.models[call.Name]
		if model == nil {
			checkFail("Step %s uses unknown model %s", pyRepr(step.Name), pyRepr(call.Name))
		}
		params, output = c.recordFields(model.Input), model.Output
	case "agent":
		agent := c.agents[call.Name]
		if agent == nil || agent.Model == "" {
			checkFail("Step %s requires a known agent with a model", pyRepr(step.Name))
		}
		model := c.models[agent.Model]
		params, output = c.recordFields(model.Input), model.Output
	}
	c.checkArguments(resolveUnnamed(call.Args, c.stepParams(call)), params, env, step.Name)
	if step.BindingType != "" {
		c.validateType(step.BindingType)
		if step.BindingType != output {
			checkFail("Step %s binding %s is %s, but call returns %s", pyRepr(step.Name), pyRepr(step.Binding), pyRepr(step.BindingType), pyRepr(output))
		}
	}
	if step.Return != step.Binding {
		checkFail("Step %s must return its binding %s, got %s", pyRepr(step.Name), pyRepr(step.Binding), pyRepr(step.Return))
	}
	for _, param := range workflow.Params {
		if param.Name == step.Binding {
			checkFail("Step %s cannot shadow workflow input %s", pyRepr(step.Name), pyRepr(step.Binding))
		}
	}
	env.types[step.Binding] = output
}

// workflowRun is the workflow a test line `name = workflow(...)` runs.
func (c *checker) workflowRun(step TestStep) (*WorkflowDecl, []Arg) {
	call, ok := step.Value.(*CallExpr)
	if !ok || step.Expect {
		return nil, nil
	}
	workflow := c.workflows[call.Name]
	if workflow == nil {
		return nil, nil
	}
	return workflow, resolveUnnamed(call.Args, c.stepParamsOf(call.Name))
}

func (c *checker) stepParamsOf(name string) []string {
	return c.stepParams(&StepCall{Kind: "function", Name: name})
}

// workflowArguments holds a test's call of a workflow to its parameters.
func workflowArguments(workflow *WorkflowDecl, args []Arg) string {
	wanted := fieldNames(workflow.Params)
	var given []string
	for _, arg := range args {
		given = append(given, arg.Name)
	}
	if !sameNames(given, wanted) {
		described := strings.Join(wanted, ", ")
		if described == "" {
			described = "nothing"
		}
		return fmt.Sprintf("workflow %s takes %s", pyRepr(workflow.Name), described)
	}
	return ""
}

// checkAnswers holds a test's scripted answers to the tools it may script.
func (c *checker) checkAnswers(test *TestDecl) string {
	seen := map[string]bool{}
	for _, answer := range test.Answers {
		if seen[answer.Name] {
			return fmt.Sprintf("answers names %s more than once", pyRepr(answer.Name))
		}
		seen[answer.Name] = true
		if model := c.models[answer.Name]; model != nil && model.Provider == "" {
			continue
		}
		tool := c.tools[answer.Name]
		if tool == nil {
			var known []string
			for _, declared := range c.program.Models {
				if declared.Provider == "" {
					known = append(known, declared.Name)
				}
			}
			for _, declared := range c.program.Tools {
				known = append(known, declared.Name)
			}
			known = uniqueNames(known)
			sort.Strings(known)
			list := strings.Join(known, ", ")
			if list == "" {
				list = "none"
			}
			return fmt.Sprintf("answers names %s, which is not a declared model or tool; declared: %s", pyRepr(answer.Name), list)
		}
		if tool.Access != "observe" {
			return fmt.Sprintf("answers cannot script tool %s: it changes the world, and running it needs a recorded approval", pyRepr(answer.Name))
		}
	}
	return ""
}

// withSteps is the program with each workflow's steps changed by change.
func withSteps(program *Program, change func(*Step) *Step) *Program {
	copied := *program
	copied.Workflows = nil
	for _, workflow := range program.Workflows {
		changed := *workflow
		changed.Steps = nil
		for _, step := range workflow.Steps {
			changed.Steps = append(changed.Steps, change(step))
		}
		copied.Workflows = append(copied.Workflows, &changed)
	}
	return &copied
}

func patternKind(v Value) string {
	switch v.(type) {
	case bool:
		return "bool"
	case string:
		return "str"
	case float64:
		return "float"
	}
	return "int"
}

// checkBranches checks a match step by checking the program each branch
// would make.
func (c *checker) checkBranches() {
	most := 0
	for _, workflow := range c.program.Workflows {
		for _, step := range workflow.Steps {
			if len(step.Branches) == 0 {
				continue
			}
			most = max(most, len(step.Branches))
			last := len(step.Branches) - 1
			for i, branch := range step.Branches {
				if branch.Any != (i == last) {
					checkFail("Step %s match must end with exactly one `case _`", pyRepr(step.Name))
				}
			}
			seen := map[string]bool{}
			for _, branch := range step.Branches[:last] {
				key := patternKind(branch.Pattern) + ":" + showText(branch.Pattern)
				if seen[key] {
					checkFail("Step %s match lists a value twice", pyRepr(step.Name))
				}
				seen[key] = true
			}
			outputs := map[string]bool{}
			for _, branch := range step.Branches {
				if branch.Call.Kind == "tool" {
					if tool := c.tools[branch.Call.Name]; tool != nil && tool.Access == "execute" {
						checkFail("Step %s match cannot choose execute tool %s", pyRepr(step.Name), pyRepr(tool.Name))
					}
				}
				if out := callOutput(c.program, branch.Call); out != "" {
					outputs[out] = true
				}
			}
			if len(outputs) > 1 {
				var names []string
				for name := range outputs {
					names = append(names, name)
				}
				sort.Strings(names)
				checkFail("Step %s match branches return different types: %s", pyRepr(step.Name), strings.Join(names, ", "))
			}
		}
	}
	for index := 0; index < most; index++ {
		variant := withSteps(c.program, func(step *Step) *Step {
			if len(step.Branches) == 0 {
				return step
			}
			changed := *step
			changed.Call = step.Branches[min(index, len(step.Branches)-1)].Call
			changed.Branches = nil
			return &changed
		})
		newChecker(variant).checkDeclarations()
	}
}

// checkFallbacks checks a step's fallback by checking the program without
// it, and the program with it in the call's place.
func (c *checker) checkFallbacks() {
	for _, workflow := range c.program.Workflows {
		for _, step := range workflow.Steps {
			if step.Fallback == nil {
				continue
			}
			for _, call := range []*StepCall{step.Call, step.Fallback} {
				if call.Kind != "tool" {
					continue
				}
				if tool := c.tools[call.Name]; tool != nil && tool.Access == "execute" {
					checkFail("Step %s cannot use otherwise with execute tool %s: a failed action may still have happened", pyRepr(step.Name), pyRepr(tool.Name))
				}
			}
		}
	}
	without := withSteps(c.program, func(step *Step) *Step {
		changed := *step
		changed.Fallback = nil
		return &changed
	})
	swapped := withSteps(c.program, func(step *Step) *Step {
		changed := *step
		if step.Fallback != nil {
			// The fallback is asked once: what the call is asked again
			// with does not go with it.
			changed.Call, changed.Retries = step.Fallback, 0
		}
		changed.Fallback = nil
		return &changed
	})
	// First sound without its fallbacks, so a fault in a declaration is
	// reported as itself and not as the disagreement it also causes.
	newChecker(without).checkDeclarations()
	for _, workflow := range c.program.Workflows {
		for _, step := range workflow.Steps {
			if step.Fallback == nil {
				continue
			}
			first, second := callOutput(c.program, step.Call), callOutput(c.program, step.Fallback)
			if first != "" && second != "" && first != second {
				checkFail("Step %s otherwise branch returns %s, but the step returns %s", pyRepr(step.Name), pyRepr(second), pyRepr(first))
			}
		}
	}
	newChecker(swapped).checkDeclarations()
}
