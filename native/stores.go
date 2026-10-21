package main

// Memories and boards, as kosika/memory.py and kosika/board.py have them.
//
// A memory keeps a program's records between runs, under Text keys, one
// JSON file a memory in the folder the person running the program grants.
// A board is where the agents of one run leave messages for each other;
// nothing of it outlives the run. Neither is a second way of running: each
// declaration is lowered to two ordinary tools that only look, used through
// a generated agent, so the checker, traces and limits treat them like any
// other tool.

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// A StoreDecl is `memory NAME of Record` or `board NAME of Record`.
type StoreDecl struct{ Name, Record string }

const (
	memoryAgent       = "memory"
	memorySource      = "memory:"
	memoryKeyRecord   = "MemoryKey"
	memorySavedRecord = "MemorySaved"
	memoryMaxEntries  = 10_000
	memoryMaxKey      = 200
	boardAgent        = "board"
	boardSource       = "board:"
	boardPostedRecord = "BoardPosted"
	boardMaxPosts     = 1000
)

func titled(name string) string {
	var out strings.Builder
	for _, part := range strings.Split(name, "_") {
		if runes := []rune(part); len(runes) > 0 {
			out.WriteString(strings.ToUpper(string(runes[:1])) + strings.ToLower(string(runes[1:])))
		}
	}
	return out.String()
}

// storeNames are the names a program may use because it declares these
// memories and boards.
func storeNames(program *Program) map[string]bool {
	names := ambientNames(program)
	for name := range directNames(program) {
		names[name] = true
	}
	for _, memory := range program.Memories {
		for _, name := range []string{memoryAgent, memoryKeyRecord, memorySavedRecord,
			titled(memory.Name) + "Entry", titled(memory.Name) + "Recalled",
			"remember_" + memory.Name, "recall_" + memory.Name} {
			names[name] = true
		}
	}
	for _, board := range program.Boards {
		for _, name := range []string{boardAgent, boardPostedRecord,
			titled(board.Name) + "Post", titled(board.Name) + "Board",
			"post_" + board.Name, "read_" + board.Name} {
			names[name] = true
		}
	}
	return names
}

type lowering struct {
	program *Program
	known   map[string]*RecordDecl
	taken   map[string]bool
	what    string // "Memory" or "Board"
	needed  string // "memory" or "boards"
}

func (l *lowering) addRecord(name string, fields []Param) {
	existing := l.known[name]
	if existing == nil {
		record := &RecordDecl{Name: name, Fields: fields}
		l.known[name] = record
		l.program.Records = append(l.program.Records, record)
		return
	}
	same := len(existing.Fields) == len(fields)
	for i := 0; same && i < len(fields); i++ {
		same = existing.Fields[i] == fields[i]
	}
	if !same {
		panic(&storeRefusal{fmt.Sprintf("%s cannot be used: record %s is needed for %s and is already declared differently", l.what, pyRepr(name), l.needed)})
	}
}

type storeRefusal struct{ message string }

// LowerStores adds the records, tools and agents that a program's memories
// and boards stand for, as lower_memory and lower_board do, in that order.
func LowerStores(program *Program) (lowered *Program, refused string) {
	if len(program.Memories) == 0 && len(program.Boards) == 0 {
		return program, ""
	}
	defer func() {
		if caught := recover(); caught != nil {
			problem, ok := caught.(*storeRefusal)
			if !ok {
				panic(caught)
			}
			lowered, refused = nil, problem.message
		}
	}()
	copied := *program
	copied.Records = append([]*RecordDecl{}, program.Records...)
	copied.Tools = append([]*ToolDecl{}, program.Tools...)
	copied.Agents = append([]*AgentDecl{}, program.Agents...)
	lowerOne(&copied, true)
	lowerOne(&copied, false)
	return &copied, ""
}

func lowerOne(program *Program, memories bool) {
	declared := program.Memories
	l := &lowering{program: program, known: map[string]*RecordDecl{}, taken: map[string]bool{}, what: "Memory", needed: "memory"}
	agentName := memoryAgent
	if !memories {
		declared, l.what, l.needed, agentName = program.Boards, "Board", "boards", boardAgent
	}
	if len(declared) == 0 {
		return
	}
	for _, record := range program.Records {
		if l.known[record.Name] == nil {
			l.known[record.Name] = record
		}
	}
	agents := map[string]bool{}
	for _, tool := range program.Tools {
		l.taken[tool.Name] = true
	}
	for _, agent := range program.Agents {
		l.taken[agent.Name] = true
		agents[agent.Name] = true
	}
	for _, function := range program.Functions {
		l.taken[function.Name] = true
	}
	if l.taken[agentName] {
		panic(&storeRefusal{fmt.Sprintf("%s cannot be used: the name %s is already declared", l.what, pyRepr(agentName))})
	}
	original := map[string]bool{}
	for _, record := range program.Records {
		original[record.Name] = true
	}
	seen := map[string]bool{}
	var granted []string
	for _, store := range declared {
		if seen[store.Name] {
			panic(&storeRefusal{fmt.Sprintf("%s %s is declared more than once", l.what, pyRepr(store.Name))})
		}
		seen[store.Name] = true
		if !original[store.Record] {
			panic(&storeRefusal{fmt.Sprintf("%s %s holds unknown record %s", l.what, pyRepr(store.Name), pyRepr(store.Record))})
		}
		title := titled(store.Name)
		var tools []*ToolDecl
		if memories {
			l.addRecord(memoryKeyRecord, []Param{{"key", "Text"}})
			l.addRecord(memorySavedRecord, []Param{{"key", "Text"}, {"entries", "Number"}})
			l.addRecord(title+"Entry", []Param{{"key", "Text"}, {"value", store.Record}})
			l.addRecord(title+"Recalled", []Param{{"found", "Bool"}, {"value", store.Record + "?"}})
			tools = []*ToolDecl{
				{Name: "remember_" + store.Name, Source: memorySource + store.Name, Operation: "remember", Access: "observe", Output: memorySavedRecord, Input: title + "Entry"},
				{Name: "recall_" + store.Name, Source: memorySource + store.Name, Operation: "recall", Access: "observe", Output: title + "Recalled", Input: memoryKeyRecord},
			}
		} else {
			l.addRecord(boardPostedRecord, []Param{{"posts", "Number"}})
			l.addRecord(title+"Post", []Param{{"author", "Text"}, {"body", store.Record}})
			l.addRecord(title+"Board", []Param{{"posts", title + "Post[]"}})
			tools = []*ToolDecl{
				{Name: "post_" + store.Name, Source: boardSource + store.Name, Operation: "post", Access: "observe", Output: boardPostedRecord, Input: title + "Post"},
				{Name: "read_" + store.Name, Source: boardSource + store.Name, Operation: "read", Access: "observe", Output: title + "Board"},
			}
		}
		for _, tool := range tools {
			if l.taken[tool.Name] {
				panic(&storeRefusal{fmt.Sprintf("%s %s cannot be used: the name %s is already declared", l.what, pyRepr(store.Name), pyRepr(tool.Name))})
			}
		}
		program.Tools = append(program.Tools, tools...)
		granted = append(granted, tools[0].Name, tools[1].Name)
	}
	if !memories {
		// A post is signed with the name of a declared agent.
		posting := map[string]string{}
		for _, board := range program.Boards {
			posting["post_"+board.Name] = board.Name
		}
		known := make([]string, 0, len(agents))
		for name := range agents {
			known = append(known, name)
		}
		sort.Strings(known)
		list := strings.Join(known, ", ")
		if list == "" {
			list = "none"
		}
		for _, workflow := range program.Workflows {
			for _, step := range workflow.Steps {
				calls := []*StepCall{step.Call, step.Fallback}
				for _, branch := range step.Branches {
					calls = append(calls, branch.Call)
				}
				for _, call := range calls {
					if call == nil || call.Kind != "tool" || posting[call.Name] == "" {
						continue
					}
					signed := ""
					for _, arg := range call.Args {
						if literal, ok := arg.Value.(*Literal); ok && arg.Name == "author" {
							signed, _ = literal.Value.(string)
						}
					}
					if !agents[signed] {
						panic(&storeRefusal{fmt.Sprintf("Board %s post in step %s is signed %s, which is not a declared agent; declared: %s", pyRepr(posting[call.Name]), pyRepr(step.Name), pyReprAny(signed), list)})
					}
				}
			}
		}
	}
	program.Agents = append(program.Agents, &AgentDecl{Name: agentName, Can: granted, MaxSteps: 1, MaxToolCalls: 256, MaxModelCalls: 64})
}

// A memoryStore is the memories of one program, in a folder, or only in
// this process when folder is "".
type memoryStore struct {
	folder string
	data   map[string]*Record
}

func newMemoryStore(folder string) *memoryStore {
	return &memoryStore{folder: folder, data: map[string]*Record{}}
}

func (m *memoryStore) path(name string) string { return filepath.Join(m.folder, name+".json") }

func (m *memoryStore) entries(name string) *Record {
	if m.data[name] == nil {
		loaded := newRecord()
		if m.folder != "" {
			if raw, err := os.ReadFile(m.path(name)); err == nil {
				value, problem := parseStrictJSON(string(raw))
				if problem != "" {
					runFail(fmt.Sprintf("Memory %s cannot be read: %s", pyRepr(name), problem))
				}
				record, ok := value.(*Record)
				if !ok {
					runFail(fmt.Sprintf("Memory %s is not a map of entries", pyRepr(name)))
				}
				loaded = record
			}
		}
		m.data[name] = loaded
	}
	return m.data[name]
}

func (m *memoryStore) recall(name, key string) Value {
	entries := m.entries(name)
	value, found := entries.Fields[key]
	result := newRecord()
	result.set("found", found)
	result.set("value", value)
	return result
}

func (m *memoryStore) remember(name, key string, value Value) Value {
	entries := m.entries(name)
	if key == "" || len([]rune(key)) > memoryMaxKey {
		runFail(fmt.Sprintf("A memory key must have 1 to %d characters", memoryMaxKey))
	}
	if _, has := entries.Fields[key]; !has && len(entries.Keys) >= memoryMaxEntries {
		runFail(fmt.Sprintf("Memory %s is full (%d entries)", pyRepr(name), memoryMaxEntries))
	}
	entries.set(key, value)
	if m.folder != "" {
		// Written whole to a neighbour and swapped in, so a crash cannot
		// leave half a file where the memory was.
		if err := os.MkdirAll(m.folder, 0o755); err != nil {
			runFail(err.Error())
		}
		sorted := newRecord()
		keys := append([]string{}, entries.Keys...)
		sort.Strings(keys)
		for _, k := range keys {
			sorted.set(k, sortedDeep(entries.Fields[k]))
		}
		temporary, err := os.CreateTemp(m.folder, "."+name+"-*.tmp")
		if err != nil {
			runFail(err.Error())
		}
		_, err = temporary.WriteString(pyJSONIndent(sorted, "  ", ""))
		temporary.Close()
		if err == nil {
			err = os.Rename(temporary.Name(), m.path(name))
		}
		if err != nil {
			os.Remove(temporary.Name())
			runFail(err.Error())
		}
	}
	saved := newRecord()
	saved.set("key", key)
	saved.set("entries", bigInt(len(entries.Keys)))
	return saved
}

// sortedDeep is a value with every record's fields in the order of their
// names, as Python's json.dump(sort_keys=True) writes them.
func sortedDeep(v Value) Value {
	switch x := v.(type) {
	case *Record:
		out := newRecord()
		keys := append([]string{}, x.Keys...)
		sort.Strings(keys)
		for _, key := range keys {
			out.set(key, sortedDeep(x.Fields[key]))
		}
		return out
	case []Value:
		out := make([]Value, len(x))
		for i, item := range x {
			out[i] = sortedDeep(item)
		}
		return out
	}
	return v
}

// boardStore is the boards of one run.
type boardStore struct{ posts map[string][]Value }

func (b *boardStore) post(name string, author, body Value) Value {
	if len(b.posts[name]) >= boardMaxPosts {
		runFail(fmt.Sprintf("Board %s is full (%d posts)", pyRepr(name), boardMaxPosts))
	}
	post := newRecord()
	post.set("author", author)
	post.set("body", body)
	b.posts[name] = append(b.posts[name], post)
	posted := newRecord()
	posted.set("posts", bigInt(len(b.posts[name])))
	return posted
}

func (b *boardStore) read(name string) Value {
	posts := append([]Value{}, b.posts[name]...)
	read := newRecord()
	read.set("posts", posts)
	return read
}

// hostTool answers a memory or board tool from the run's own stores, or
// says it is not one.
func (r *run) hostTool(tool *ToolDecl, arguments *Record) (Value, bool) {
	switch {
	case strings.HasPrefix(tool.Source, memorySource) && r.memory != nil:
		name := strings.TrimPrefix(tool.Source, memorySource)
		key, _ := arguments.Fields["key"].(string)
		if tool.Operation == "recall" {
			return r.memory.recall(name, key), true
		}
		return r.memory.remember(name, key, arguments.Fields["value"]), true
	case systemKind(tool) != "" && r.systemBound(tool):
		if systemKind(tool) == "shell" {
			return r.shellTool(tool, arguments), true
		}
		if systemKind(tool) == "services" {
			return r.servicesTool(tool, arguments), true
		}
		return r.fileTool(tool, arguments), true
	case tool.Source == "world:clock":
		return moment(), true
	case tool.Source == "world:random":
		return draw(arguments), true
	case strings.HasPrefix(tool.Source, boardSource):
		name := strings.TrimPrefix(tool.Source, boardSource)
		if tool.Operation == "read" {
			return r.boards.read(name), true
		}
		return r.boards.post(name, arguments.Fields["author"], arguments.Fields["body"]), true
	}
	return nil, false
}
