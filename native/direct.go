package main

// Files in one line, with no tool to declare, as kosika/direct.py has them:
//
//   note  = read file "notes/todo.txt"           // FileContent
//   names = list files "notes"                    // FileNames
//   saved = write file "out/report.txt" with text // FileSaved
//
// Each step is lowered to a call of a `system "files"` tool through a
// generated agent named `direct`, so the folder grant and the approval of
// a write are what they are for any files tool.

const directAgent = "direct"

var directRecords = map[string][]Param{
	"FilePath":    {{"path", "Text"}},
	"FileContent": {{"text", "Text"}},
	"FileNames":   {{"names", "Text[]"}},
	"FileChange":  {{"path", "Text"}, {"text", "Text"}},
	"FileSaved":   {{"bytes", "Number"}},
}

type directTool struct {
	tool    *ToolDecl
	records []string
}

var directTools = map[string]directTool{
	"file_read":  {&ToolDecl{Name: "file_read", Source: "system:files", Operation: "read", Access: "observe", Output: "FileContent", Input: "FilePath"}, []string{"FilePath", "FileContent"}},
	"file_list":  {&ToolDecl{Name: "file_list", Source: "system:files", Operation: "list", Access: "observe", Output: "FileNames", Input: "FilePath"}, []string{"FilePath", "FileNames"}},
	"file_write": {&ToolDecl{Name: "file_write", Source: "system:files", Operation: "write", Access: "execute", Output: "FileSaved", Input: "FileChange"}, []string{"FileChange", "FileSaved"}},
}

func directCalls(program *Program) []string {
	used := map[string]bool{}
	for _, workflow := range program.Workflows {
		for _, step := range workflow.Steps {
			calls := []*StepCall{step.Call, step.Fallback}
			for _, branch := range step.Branches {
				calls = append(calls, branch.Call)
			}
			for _, call := range calls {
				if call != nil && call.Kind == "tool" && call.Agent == directAgent {
					if _, ok := directTools[call.Name]; ok {
						used[call.Name] = true
					}
				}
			}
		}
	}
	return sortedKeys(used)
}

// directNames are the names a module may use because it has these steps.
func directNames(program *Program) map[string]bool {
	names := map[string]bool{}
	used := directCalls(program)
	if len(used) == 0 {
		return names
	}
	names[directAgent] = true
	for _, name := range used {
		names[name] = true
		for _, record := range directTools[name].records {
			names[record] = true
		}
	}
	return names
}

// LowerDirect adds the records, tools and agent the one-line steps stand
// for. A name the program already declares is left as written.
func LowerDirect(program *Program) *Program {
	used := directCalls(program)
	if len(used) == 0 {
		return program
	}
	for _, agent := range program.Agents {
		if agent.Name == directAgent {
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
	lowered := *program
	lowered.Records = append([]*RecordDecl{}, program.Records...)
	lowered.Tools = append([]*ToolDecl{}, program.Tools...)
	for _, name := range used {
		for _, record := range directTools[name].records {
			if !known[record] {
				known[record] = true
				lowered.Records = append(lowered.Records, &RecordDecl{Name: record, Fields: directRecords[record]})
			}
		}
	}
	for _, name := range used {
		if !taken[name] {
			tool := *directTools[name].tool
			lowered.Tools = append(lowered.Tools, &tool)
		}
	}
	lowered.Agents = append(append([]*AgentDecl{}, program.Agents...),
		&AgentDecl{Name: directAgent, Can: used, MaxSteps: 1, MaxToolCalls: 256, MaxModelCalls: 64})
	return &lowered
}
