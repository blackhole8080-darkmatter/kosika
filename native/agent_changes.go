package main

// An agent's own changes: a tool with `access: execute` that the agent's
// model asks for. As in the reference (execute_agent_tool and
// approve_agent_plan in kosika/runtime.py), a change happens only with an
// approval of exactly it, claimed once, in the run's journal. An agent
// that plans first asks for all its changes together, as one plan, and
// the plan is what is approved; one that does not may make one approved
// change in a step.

import (
	"fmt"
	"sort"
)

const planTool = "$plan"

// planFirst is what an agent that plans first is told, on top of the
// usual words: PLAN_FIRST in kosika/agent_steps.py.
const planFirst = " You plan before you change anything. First look, using the tools that " +
	"only read. When you know every change that is needed, request all of " +
	"them together in one reply, in the order they should happen. That list " +
	"is shown to the person as your plan, and they say yes or no to it once. " +
	"You cannot make changes a second time, so leave nothing out. Once the " +
	"changes have happened you are shown what each one gave back; then give " +
	"your answer."

// plansFirst is whether an agent shows all its changes as one plan before
// any: when the program says `plan: first`; not when it says `plan: off`;
// and when it says nothing, when it can: it has a model, a tool that
// changes something, and at least two turns.
func (r *run) plansFirst(agent *AgentDecl) bool {
	if agent.Plan != "" {
		return agent.Plan == "first"
	}
	return agent.Model != "" && agent.MaxSteps >= 2 && len(r.changes(agent)) > 0
}

// changes are the tools an agent may use that change the world, by name.
func (r *run) changes(agent *AgentDecl) []string {
	var names []string
	for _, name := range agent.Can {
		if r.checker.tools[name].Access == "execute" {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	return names
}

type plannedChange struct {
	tool      *ToolDecl
	arguments *Record
}

// agentChanges is what one agent step has changed, or plans to.
type agentChanges struct {
	agent   *AgentDecl
	plans   bool
	scope   []string // the step's own, in the journal
	changed []string
	// A planning agent's plan once approved, its action, and what each of
	// its changes gave back so far.
	planned []plannedChange
	action  string
	results []Value
	done    bool
	// What the model said, kept with the plan: a step begun again after
	// the plan was approved is given the same answers, not asked anew.
	said []Value
	at   int
	// request is what the agent was asked, its text arguments in order,
	// and sentBack how many plans with something wrong went back.
	request  string
	sentBack int
}

func (r *run) newAgentChanges(agent *AgentDecl) *agentChanges {
	changes := &agentChanges{agent: agent, plans: r.plansFirst(agent), scope: append([]string{}, r.stepScope...), request: r.agentRequest}
	if changes.plans && r.session != nil {
		if kept, ok := r.session.remembered(changes.saidScope()).([]Value); ok {
			changes.said = kept
		}
	}
	return changes
}

func (c *agentChanges) saidScope() []string {
	return append(append([]string{}, c.scope...), planTool+":said")
}

// ask asks the agent's model, or gives back what it said before the plan
// was approved, in order.
func (r *run) askAgentModel(changes *agentChanges, model *ModelDecl, request *modelRequest) *modelResponse {
	if changes.at < len(changes.said) {
		changes.at++
		return responseFrom(changes.said[changes.at-1])
	}
	response := r.ask(model, request)
	if changes.plans {
		changes.said = append(changes.said, responseRecord(response))
		changes.at++
	}
	return response
}

func responseRecord(response *modelResponse) Value {
	record := newRecord()
	record.set("content", response.Content)
	record.set("finish_reason", response.Finish)
	calls := []Value{}
	for _, call := range response.ToolCalls {
		item := newRecord()
		item.set("id", call.ID)
		item.set("name", call.Name)
		item.set("arguments", call.Arguments)
		calls = append(calls, item)
	}
	record.set("tool_calls", calls)
	return record
}

func responseFrom(value Value) *modelResponse {
	record, ok := value.(*Record)
	if !ok {
		stopRun("journal_invalid", "Agent journal model response is invalid")
	}
	response := &modelResponse{}
	response.Content, _ = record.Fields["content"].(string)
	response.Finish, _ = record.Fields["finish_reason"].(string)
	calls, _ := record.Fields["tool_calls"].([]Value)
	for _, item := range calls {
		call, ok := item.(*Record)
		if !ok {
			stopRun("journal_invalid", "Agent journal model response is invalid")
		}
		id, _ := call.Fields["id"].(string)
		name, _ := call.Fields["name"].(string)
		arguments, _ := call.Fields["arguments"].(*Record)
		if arguments == nil {
			arguments = newRecord()
		}
		response.ToolCalls = append(response.ToolCalls, toolCall{id, name, arguments})
	}
	return response
}

// claim begins a change in the journal: with its approval, or the step
// stops to wait for one. Nothing has changed yet, so the step is marked
// not begun, to begin again once a person has answered.
func (r *run) claim(changes *agentChanges, scope []string, tool string, arguments *Record) string {
	defer func() {
		if caught := recover(); caught != nil {
			if _, waiting := caught.(*approvalPending); waiting {
				r.session.recover(changes.scope)
			}
			panic(caught)
		}
	}()
	return r.session.start(scope, tool, arguments)
}

// approvePlan shows a planning agent's changes as one plan, to be approved
// once.
func (r *run) approvePlan(changes *agentChanges, planned []plannedChange) {
	if r.session == nil {
		panic(&permissionError{fmt.Sprintf("Agent %s plans changes, which requires a verified approval record", pyRepr(changes.agent.Name))})
	}
	actions := []Value{}
	for _, change := range planned {
		action := newRecord()
		action.set("tool", change.tool.Name)
		action.set("arguments", change.arguments)
		actions = append(actions, action)
	}
	wanted := newRecord()
	wanted.set("agent", changes.agent.Name)
	wanted.set("actions", actions)
	// A plan that was sent back and still has something wrong is shown all
	// the same, with what is wrong.
	if wrong := r.reviewPlan(changes, planned); len(wrong) > 0 {
		wanted.set("warnings", stringValues(wrong))
	}
	r.session.save(changes.saidScope(), "note", append([]Value{}, changes.said...))
	changes.action = r.claim(changes, append(append([]string{}, changes.scope...), planTool), planTool, wanted)
	changes.planned = append(changes.planned, planned...)
}

// agentChange makes a change an agent asked for: the next one of its
// approved plan, or, for an agent that does not plan, one approved change.
func (r *run) agentChange(changes *agentChanges, tool *ToolDecl, arguments *Record) Value {
	r.checkpoint()
	if r.toolCalls >= r.maxTools {
		panic(&budgetError{Message: fmt.Sprintf("tool calls budget exceeds %d", r.maxTools)})
	}
	r.toolCalls++
	if r.session == nil {
		panic(&permissionError{fmt.Sprintf("Workflow ask tool %s requires a verified approval record", pyRepr(tool.Name))})
	}
	if changes.plans {
		at := len(changes.results)
		if at >= len(changes.planned) || changes.planned[at].tool.Name != tool.Name || digest(changes.planned[at].arguments) != digest(arguments) {
			panic(&permissionError{fmt.Sprintf("Agent %s asked for a change that is not the next one in its approved plan", pyRepr(changes.agent.Name))})
		}
		outcome := r.changeOutcome(tool, arguments)
		result := newRecord()
		result.set("tool", tool.Name)
		result.set("result", outcome)
		changes.results = append(changes.results, result)
		if len(changes.results) == len(changes.planned) {
			all := newRecord()
			all.set("results", changes.results)
			r.session.complete(append(append([]string{}, changes.scope...), planTool),
				[]Value{event("tool_return", "agent", changes.agent.Name, "tool", planTool, "transport", "host", "result", all)},
				changes.action)
		}
		return outcome
	}
	inner := append(append([]string{}, changes.scope...), "ask:"+tool.Name)
	if len(changes.changed) > 0 {
		// One approved change can be paused for and resumed safely. A
		// second in the same step could not be: resuming asks the model
		// again, and the first change must not happen twice.
		panic(&permissionError{fmt.Sprintf("Agent %s already made one approved change in this step; a second change needs a step of its own", pyRepr(changes.agent.Name))})
	}
	action := r.claim(changes, inner, tool.Name, arguments)
	changes.changed = append(changes.changed, tool.Name)
	outcome := r.changeOutcome(tool, arguments)
	r.session.complete(inner, []Value{event("tool_return", "agent", changes.agent.Name, "tool", tool.Name, "transport", "host", "result", outcome)}, action)
	return outcome
}

// changeOutcome runs a change, which must give back what its tool
// promises. A change that goes wrong is not told to the model: the step
// fails.
func (r *run) changeOutcome(tool *ToolDecl, arguments *Record) Value {
	var outcome Value
	if scripted := r.tools[tool.Name]; scripted != nil {
		if scripted.asked >= len(scripted.answers) {
			runFail(fmt.Sprintf("Scripted tool %s has no answer left", pyRepr(scripted.name)))
		}
		scripted.asked++
		outcome = scripted.answers[scripted.asked-1]
	} else if hosted, ok := r.hostTool(tool, arguments); ok {
		outcome = hosted
	} else if r.servers[tool.Source] != nil {
		outcome = r.callDeclaredTool(tool, arguments)
	} else {
		outcome = mockTool(tool)
	}
	r.validate(outcome, tool.Output, "$")
	return outcome
}

// remembered is what was kept under a scope beside the steps.
func (j *journal) remembered(scope []string) Value {
	entry, ok := j.data().Fields["steps"].(*Record).Fields[scopeKey(scope)].(*Record)
	if !ok || entry.Fields["state"] != "note" {
		return nil
	}
	return entry.Fields["payload"]
}

func stringValues(texts []string) []Value {
	values := []Value{}
	for _, text := range texts {
		values = append(values, text)
	}
	return values
}
