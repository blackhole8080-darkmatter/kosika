package main

// The journal: what a run has done, kept on disk so that a run paused for
// an approval can begin again without doing anything twice; and the
// approvals given, each for one action, used once. It follows
// kosika/durable.py and kosika/approval_ledger.py, but is this engine's
// own: two JSON files beside each other, written whole each time, in place
// of the reference's SQLite. A journal or an approval from one engine does
// not serve the other, since each names a run by its own plan hash.

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"strings"
	"time"
)

// An approvalRecord is a person's yes to one action of one plan.
type approvalRecord struct {
	planHash, actionHash, approvedBy, reason string
}

// approvalPending ends a run that has reached an action no one approved.
// It is not a failure: the run waits, and begins again once approved.
type approvalPending struct{ request *Record }

type journal struct {
	path, bindingID string
	approvals       []approvalRecord
	planHash        string
	lockPath        string
}

func digest(v Value) string {
	sum := sha256.Sum256([]byte(canonicalJSON(v)))
	return hex.EncodeToString(sum[:])
}

func stopRun(code, format string, args ...any) {
	panic(&budgetError{fmt.Sprintf(format, args...), code})
}

func openJournal(path, bindingID string, approvals []approvalRecord) *journal {
	return &journal{path: path, bindingID: bindingID, approvals: approvals, lockPath: path + ".lock"}
}

// lock claims the journal for this process. A second run of the same
// journal at the same time is refused.
func (j *journal) lock() {
	file, err := os.OpenFile(j.lockPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
	if err != nil {
		stopRun("run_busy", "Another process owns this run journal")
	}
	file.Close()
}

func (j *journal) unlock() { os.Remove(j.lockPath) }

func readJSONFile(path string) *Record {
	raw, err := os.ReadFile(path)
	if err != nil {
		return newRecord()
	}
	value, problem := parseStrictJSON(string(raw))
	record, ok := value.(*Record)
	if problem != "" || !ok {
		stopRun("journal_invalid", "Journal %s is not readable", path)
	}
	return record
}

func writeJSONFile(path string, record *Record) {
	temporary := path + ".writing"
	if err := os.WriteFile(temporary, []byte(pyJSON(record, true)+"\n"), 0o644); err != nil {
		stopRun("journal_invalid", "Cannot write journal %s: %v", path, err)
	}
	if err := os.Rename(temporary, path); err != nil {
		stopRun("journal_invalid", "Cannot write journal %s: %v", path, err)
	}
}

func (j *journal) data() *Record {
	data := readJSONFile(j.path)
	if _, ok := data.Fields["steps"].(*Record); !ok {
		data.set("steps", newRecord())
	}
	return data
}

// bind names the run this journal is for: its program, workflow, inputs
// and the host's binding. A journal of another run is refused.
func (j *journal) bind(plan Value) {
	payload := newRecord()
	payload.set("plan", plan)
	payload.set("binding_id", j.bindingID)
	j.planHash = digest(payload)
	data := j.data()
	if existing, has := data.Fields["plan"]; has && existing != j.planHash {
		stopRun("plan_mismatch", "Journal belongs to a different program, input or binding configuration")
	}
	data.set("plan", j.planHash)
	writeJSONFile(j.path, data)
}

func scopeKey(scope []string) string {
	parts := make([]Value, len(scope))
	for i, part := range scope {
		parts[i] = part
	}
	return pyJSON(parts, false)
}

func (j *journal) save(scope []string, state string, payload Value) {
	data := j.data()
	entry := newRecord()
	entry.set("state", state)
	entry.set("payload", payload)
	data.Fields["steps"].(*Record).set(scopeKey(scope), entry)
	writeJSONFile(j.path, data)
}

// cached gives a step's events when it completed in an earlier attempt.
func (j *journal) cached(scope []string, arguments *Record) []Value {
	entry, ok := j.data().Fields["steps"].(*Record).Fields[scopeKey(scope)].(*Record)
	if !ok || entry.Fields["state"] == "pending" {
		return nil
	}
	if entry.Fields["state"] != "completed" {
		stopRun("uncertain_outcome", "An interrupted or failed step requires reconciliation; it will not be repeated")
	}
	events, ok := entry.Fields["payload"].([]Value)
	if !ok || len(events) != 4 {
		stopRun("checkpoint_mismatch", "Cached step arguments differ")
	}
	call, ok := events[1].(*Record)
	if !ok || digest(call.Fields["arguments"]) != digest(arguments) {
		stopRun("checkpoint_mismatch", "Cached step arguments differ")
	}
	return events
}

// start notes that a step begins. A step that changes the world begins
// only with an approval of exactly this action, claimed here, once.
func (j *journal) start(scope []string, tool string, arguments *Record) string {
	action := ""
	if tool != "" {
		named := make([]Value, 0, len(scope)+1)
		for _, part := range scope {
			named = append(named, part)
		}
		named = append(named, tool)
		payload := newRecord()
		payload.set("plan_hash", j.planHash)
		payload.set("tool", pyJSON(named, false))
		payload.set("arguments", arguments)
		action = digest(payload)
		var approval *approvalRecord
		for i := range j.approvals {
			given := &j.approvals[i]
			if given.planHash == j.planHash && given.actionHash == action && strings.TrimSpace(given.approvedBy) != "" {
				approval = given
				break
			}
		}
		if approval == nil {
			steps := make([]Value, len(scope))
			for i, part := range scope {
				steps[i] = part
			}
			request := newRecord()
			request.set("plan_hash", j.planHash)
			request.set("action_hash", action)
			request.set("tool", tool)
			request.set("arguments", arguments)
			request.set("step", steps)
			j.save(scope, "pending", request)
			panic(&approvalPending{request})
		}
		j.claim(action, approval.approvedBy, tool)
	}
	started := newRecord()
	started.set("arguments", arguments)
	j.save(scope, "started", started)
	return action
}

// recover marks a failed step that changed nothing as not begun, when its
// fallback takes over.
func (j *journal) recover(scope []string) {
	payload := newRecord()
	payload.set("recovered", true)
	j.save(scope, "pending", payload)
}

func (j *journal) complete(scope []string, events []Value, action string) {
	if action != "" {
		j.finish(action, events[len(events)-1].(*Record).Fields["result"])
	}
	j.save(scope, "completed", events)
}

func (j *journal) ledgerPath() string { return j.path + ".approvals.json" }

// claim uses an approval: the same one can never be used twice.
func (j *journal) claim(action, approvedBy, tool string) {
	ledger := readJSONFile(j.ledgerPath())
	if _, used := ledger.Fields[action]; used {
		runFail(fmt.Sprintf("Approval for action %s was already claimed", action))
	}
	entry := newRecord()
	entry.set("plan_hash", j.planHash)
	entry.set("approved_by", approvedBy)
	entry.set("tool", tool)
	entry.set("state", "claimed")
	entry.set("claimed_at", timestamp())
	ledger.set(action, entry)
	writeJSONFile(j.ledgerPath(), ledger)
}

func (j *journal) finish(action string, result Value) {
	ledger := readJSONFile(j.ledgerPath())
	entry, ok := ledger.Fields[action].(*Record)
	if !ok || entry.Fields["state"] != "claimed" {
		runFail(fmt.Sprintf("Approval for action %s has no open claim", action))
	}
	sum := sha256.Sum256([]byte(canonicalJSON(result)))
	entry.set("state", "succeeded")
	entry.set("outcome_hash", hex.EncodeToString(sum[:]))
	entry.set("finished_at", time.Now().UTC().Format("2006-01-02T15:04:05.000000+00:00"))
	writeJSONFile(j.ledgerPath(), ledger)
}

// readApproval reads an approval record file, as the reference's
// --approval-record does.
func readApproval(path string) (approvalRecord, string) {
	value, problem := parseStrictJSON(read(path))
	record, ok := value.(*Record)
	bad := "Approval record needs plan_hash, action_hash and approved_by strings"
	if problem != "" || !ok {
		return approvalRecord{}, bad
	}
	for _, key := range record.Keys {
		if key != "plan_hash" && key != "action_hash" && key != "approved_by" && key != "reason" {
			return approvalRecord{}, bad
		}
	}
	var fields [3]string
	for i, key := range []string{"plan_hash", "action_hash", "approved_by"} {
		text, ok := record.Fields[key].(string)
		if !ok || strings.TrimSpace(text) == "" {
			return approvalRecord{}, bad
		}
		fields[i] = text
	}
	reason, _ := record.Fields["reason"].(string)
	return approvalRecord{fields[0], fields[1], fields[2], reason}, ""
}
