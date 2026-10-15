package main

// A program in several files: `import "path.kos"` and `export [names]`,
// loaded as load_program in kosika/modules.py loads them. A module sees
// its own declarations and what each file it imports exports; nothing else.

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// A moduleError ends loading. A text that is not Kosika is reported as the
// parser reports it; anything else is a refusal, as the checker's are.
type moduleError struct {
	message string
	parse   bool
}

// pyPath writes a path as Python's pathlib does: its parts joined with the
// system's separator, with "." parts and doubled separators dropped and
// ".." kept.
func pyPath(base, relative string) string {
	parts := []string{strings.TrimRight(base, `\/`)}
	for _, part := range strings.FieldsFunc(relative, func(r rune) bool { return r == '/' || r == '\\' }) {
		if part != "." {
			parts = append(parts, part)
		}
	}
	return strings.Join(parts, string(filepath.Separator))
}

// realPath is a file's path with every link followed, or "" when there is
// no such file.
func realPath(path string) string {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return ""
	}
	real, err := filepath.EvalSymlinks(absolute)
	if err != nil {
		return ""
	}
	return real
}

func inside(path, root string) bool {
	relative, err := filepath.Rel(root, path)
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) && !filepath.IsAbs(relative)
}

// localNames are a module's own declarations: what it may export, and
// what no other module may declare again.
func localNames(module *Program) []string {
	var names []string
	for _, record := range module.Records {
		names = append(names, record.Name)
	}
	for _, enum := range module.Enums {
		names = append(names, enum.Name)
	}
	for _, shape := range module.Shapes {
		names = append(names, shape.Name)
	}
	for _, tool := range module.Tools {
		names = append(names, tool.Name)
	}
	for _, agent := range module.Agents {
		names = append(names, agent.Name)
	}
	for _, model := range module.Models {
		names = append(names, model.Name)
	}
	for _, function := range module.Functions {
		names = append(names, function.Name)
	}
	for _, workflow := range module.Workflows {
		names = append(names, workflow.Name)
	}
	return names
}

// loadModules reads a program and every file it imports, checks that each
// uses only what it may see, and gives back them all as one program.
func loadModules(entry string) (merged *Program, failure *moduleError) {
	boundary := realPath(filepath.Dir(entry))
	merged = &Program{}
	loaded := map[string]*Program{}
	active := map[string]bool{}
	owners := map[string]string{}
	defer func() {
		if caught := recover(); caught != nil {
			problem, ok := caught.(*moduleError)
			if !ok {
				panic(caught)
			}
			merged, failure = nil, problem
		}
	}()
	refuse := func(format string, args ...any) {
		panic(&moduleError{message: fmt.Sprintf(format, args...)})
	}
	var load func(candidate string) *Program
	load = func(candidate string) *Program {
		resolved := realPath(candidate)
		if resolved == "" {
			refuse("No such module: %s", candidate)
		}
		if info, err := os.Lstat(candidate); err == nil && info.Mode()&os.ModeSymlink != 0 || !inside(resolved, boundary) || filepath.Ext(resolved) != ".kos" {
			refuse("Import outside the granted .kos module root: %s", candidate)
		}
		if active[resolved] {
			refuse("Import cycle at %s", resolved)
		}
		if module := loaded[resolved]; module != nil {
			return module
		}
		if len(loaded)+len(active) >= 64 || len(active) >= 16 {
			refuse("Module graph exceeds file/depth limit")
		}
		raw, err := os.ReadFile(resolved)
		if err != nil {
			refuse("Cannot read %s: %v", resolved, err)
		}
		if len(raw) > 262144 {
			refuse("Module exceeds 256 KiB: %s", resolved)
		}
		module, parseErr := Parse(string(raw))
		if parseErr != nil {
			panic(&moduleError{message: fmt.Sprintf("%s: %v", resolved, parseErr), parse: true})
		}
		active[resolved] = true
		visible := map[string]bool{}
		for _, name := range module.Imports {
			if filepath.IsAbs(name) || filepath.VolumeName(name) != "" {
				refuse("Import paths must be relative")
			}
			at := filepath.Dir(resolved)
			if strings.HasPrefix(name, `\`) || strings.HasPrefix(name, "/") {
				// On Windows a path that begins at the root is not whole
				// without its drive: it is read from the drive's root.
				at = filepath.VolumeName(resolved)
			}
			dependency := load(pyPath(at, name))
			for _, exported := range dependency.Exports {
				visible[exported] = true
			}
		}
		local := map[string]bool{}
		for _, name := range localNames(module) {
			local[name] = true
		}
		unique := map[string]bool{}
		for _, name := range module.Exports {
			if unique[name] || !local[name] {
				refuse("%s: exports must name unique local declarations", resolved)
			}
			unique[name] = true
		}
		if problem := ModuleProblem(module, resolved, visible); problem != "" {
			refuse("%s", problem)
		}
		for _, name := range localNames(module) {
			if owner, taken := owners[name]; taken && owner != resolved {
				refuse("Conflicting module declaration %s", pyRepr(name))
			}
			owners[name] = resolved
		}
		merged.Records = append(merged.Records, module.Records...)
		merged.Enums = append(merged.Enums, module.Enums...)
		merged.Shapes = append(merged.Shapes, module.Shapes...)
		merged.Tools = append(merged.Tools, module.Tools...)
		merged.Agents = append(merged.Agents, module.Agents...)
		merged.Models = append(merged.Models, module.Models...)
		merged.Functions = append(merged.Functions, module.Functions...)
		merged.Workflows = append(merged.Workflows, module.Workflows...)
		merged.Tests = append(merged.Tests, module.Tests...)
		merged.Memories = append(merged.Memories, module.Memories...)
		merged.Boards = append(merged.Boards, module.Boards...)
		delete(active, resolved)
		loaded[resolved] = module
		return module
	}
	load(entry)
	return merged, nil
}
