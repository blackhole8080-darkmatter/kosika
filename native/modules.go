package main

// A program in several files: `import "path.kos"` and `export [names]`,
// loaded as load_program in kosika/modules.py loads them. A module sees
// its own declarations and what each file it imports exports; nothing else.

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"reflect"
	"sort"
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
	for _, choice := range module.Choices {
		names = append(names, choice.Name)
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
// stdFiles are the standard library: Kosika modules shipped inside this
// program, imported as `import "std/NAME.kos"`. They are a copy of
// kosika/std, which tests/test_conformance.py holds them to.
//
//go:embed std/*.kos
var stdFiles embed.FS

// stdMarker begins the name of a module of the standard library.
const stdMarker = "std/"

func loadModules(entry string) (merged *Program, failure *moduleError) {
	boundary := realPath(filepath.Dir(entry))
	merged = &Program{}
	hashes := map[string]string{}
	loaded := map[string]*Program{}
	active := map[string]bool{}
	// Who declares each name, in the order the modules finished loading,
	// and each module once in that order: imports first.
	owners := map[string][]string{}
	var named []string
	var order []loadedModule
	resolvedOf := map[*Program]string{}
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
		standard := strings.HasPrefix(candidate, stdMarker)
		resolved := candidate
		if !standard {
			resolved = realPath(candidate)
			if resolved == "" {
				refuse("No such module: %s", candidate)
			}
			if info, err := os.Lstat(candidate); err == nil && info.Mode()&os.ModeSymlink != 0 || !inside(resolved, boundary) || filepath.Ext(resolved) != ".kos" {
				refuse("Import outside the granted .kos module root: %s", candidate)
			}
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
		var raw []byte
		var err error
		if standard {
			if raw, err = stdFiles.ReadFile(resolved); err != nil {
				refuse("No such module: %s", resolved)
			}
		} else if raw, err = os.ReadFile(resolved); err != nil {
			refuse("Cannot read %s: %v", resolved, err)
		}
		if len(raw) > 262144 {
			refuse("Module exceeds 256 KiB: %s", resolved)
		}
		// The module's digest, under the name the reference records it by:
		// its path relative to the program's folder, or `std/NAME.kos`.
		key := resolved
		if !standard {
			if relative, err := filepath.Rel(boundary, resolved); err == nil {
				key = filepath.ToSlash(relative)
			}
		}
		digest := sha256.Sum256(raw)
		hashes[key] = hex.EncodeToString(digest[:])
		module, parseErr := Parse(string(raw))
		if parseErr != nil {
			panic(&moduleError{message: fmt.Sprintf("%s: %v", resolved, parseErr), parse: true})
		}
		active[resolved] = true
		var imported []string
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
			next := ""
			sibling := filepath.Join(at, filepath.FromSlash(strings.TrimPrefix(name, stdMarker)))
			switch {
			case strings.HasPrefix(name, stdMarker) && !standard && filepath.Base(at) == "std" && fileExists(sibling):
				// A library file read from its own folder, as its tests
				// are, reaches the others in that folder the same way
				// whether it writes `std/math.kos` or `math.kos`; one
				// file, not two copies of it that would clash.
				next = sibling
			case strings.HasPrefix(name, stdMarker):
				// `std/NAME.kos` is the standard library, wherever it is
				// written.
				next = stdMarker + path.Clean(strings.TrimPrefix(name, stdMarker))
			case standard:
				// A module of the library imports others of it.
				next = stdMarker + path.Clean(path.Join(path.Dir(strings.TrimPrefix(resolved, stdMarker)), name))
			default:
				next = pyPath(at, name)
			}
			dependency := load(next)
			imported = append(imported, resolvedOf[dependency])
			for _, exported := range dependency.Exports {
				visible[exported] = true
			}
			for _, choice := range dependency.Choices {
				if !hasName(dependency.Exports, choice.Name) {
					continue
				}
				// A choice is used through its constructors, so exporting
				// the choice exports them too.
				for _, alternative := range choice.Alternatives {
					visible[alternative.Name] = true
				}
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
			if owners[name] == nil {
				named = append(named, name)
			}
			if !hasName(owners[name], resolved) {
				owners[name] = append(owners[name], resolved)
			}
		}
		order = append(order, loadedModule{resolved, module, key, imported})
		delete(active, resolved)
		loaded[resolved] = module
		resolvedOf[module] = resolved
		return module
	}
	load(entry)
	renames := moduleScopedNames(order, named, owners, realPath(entry), refuse)
	for _, item := range order {
		module := item.module
		if names := renames[item.resolved]; names != nil {
			renameScoped(reflect.ValueOf(module), names)
		}
		merged.Records = append(merged.Records, module.Records...)
		merged.Enums = append(merged.Enums, module.Enums...)
		merged.Choices = append(merged.Choices, module.Choices...)
		merged.Shapes = append(merged.Shapes, module.Shapes...)
		merged.Tools = append(merged.Tools, module.Tools...)
		merged.Agents = append(merged.Agents, module.Agents...)
		merged.Models = append(merged.Models, module.Models...)
		merged.Functions = append(merged.Functions, module.Functions...)
		merged.Workflows = append(merged.Workflows, module.Workflows...)
		merged.Tests = append(merged.Tests, module.Tests...)
		merged.Memories = append(merged.Memories, module.Memories...)
		merged.Boards = append(merged.Boards, module.Boards...)
	}
	merged.Identity = identityOf(hashes)
	return merged, nil
}

func fileExists(name string) bool {
	info, err := os.Stat(name)
	return err == nil && !info.IsDir()
}

// A loadedModule is one file of a program, by its resolved path and the
// key its digest is recorded under.
type loadedModule struct {
	resolved string
	module   *Program
	key      string
	imports  []string // the resolved paths it imports directly
}

// moduleScopedNames says what each file's functions and workflows are
// called once merged (proposal 2), as _module_scoped_names in
// kosika/modules.py: in each file a name means the file's own declaration,
// or else the one a direct import exports. Where one name is declared in
// several files, every copy outside the program's own file is kept under
// `MODULE.NAME`, and each file's uses point at the copy they mean.
func moduleScopedNames(order []loadedModule, named []string, owners map[string][]string, entry string, refuse func(string, ...any)) map[string]map[string]string {
	modules := map[string]loadedModule{}
	for _, item := range order {
		modules[item.resolved] = item
	}
	short := func(place string) string { return strings.TrimSuffix(modules[place].key, ".kos") }
	renames := map[string]map[string]string{}
	for _, name := range named {
		places := owners[name]
		if len(places) < 2 {
			continue
		}
		kinds := map[string]bool{}
		var shown []string
		for _, place := range places {
			kinds[kindOf(modules[place].module, name)] = true
			shown = append(shown, short(place))
		}
		if hasOther(kinds, "function", "workflow") {
			var listed []string
			for kind := range kinds {
				listed = append(listed, kind)
			}
			sort.Strings(listed)
			refuse("Conflicting module declaration %s in %s; rename one: a %s name is shared by the whole program, since records, models, tools and the like are what a run is given and reports by name", pyRepr(name), strings.Join(shown, " and "), strings.Join(listed, "/"))
		}
		final := map[string]string{}
		for _, place := range places {
			final[place] = name
			if place != entry {
				final[place] = short(place) + "." + name
			}
		}
		for _, item := range order {
			meant, declares := final[item.resolved]
			if !declares {
				var exporters []string
				for _, found := range item.imports {
					if _, has := final[found]; has && hasName(modules[found].module.Exports, name) && !hasName(exporters, found) {
						exporters = append(exporters, found)
					}
				}
				if len(exporters) == 0 {
					continue
				}
				if len(exporters) > 1 {
					var from []string
					for _, found := range exporters {
						from = append(from, short(found))
					}
					refuse("Conflicting module declaration %s in %s; %s imports it from both, so declare its own or import only one", pyRepr(name), strings.Join(from, " and "), short(item.resolved))
				}
				meant = final[exporters[0]]
			}
			if meant != name {
				if renames[item.resolved] == nil {
					renames[item.resolved] = map[string]string{}
				}
				renames[item.resolved][name] = meant
			}
		}
	}
	return renames
}

func hasOther(kinds map[string]bool, allowed ...string) bool {
	for kind := range kinds {
		if !hasName(allowed, kind) {
			return true
		}
	}
	return false
}

// kindOf names what a module declares under name, as the reference's
// category names say it.
func kindOf(module *Program, name string) string {
	for _, item := range []struct {
		kind  string
		names func() []string
	}{
		{"record", func() (out []string) {
			for _, x := range module.Records {
				out = append(out, x.Name)
			}
			return
		}},
		{"choice", func() (out []string) {
			for _, x := range module.Choices {
				out = append(out, x.Name)
			}
			return
		}},
		{"enum", func() (out []string) {
			for _, x := range module.Enums {
				out = append(out, x.Name)
			}
			return
		}},
		{"shape", func() (out []string) {
			for _, x := range module.Shapes {
				out = append(out, x.Name)
			}
			return
		}},
		{"tool", func() (out []string) {
			for _, x := range module.Tools {
				out = append(out, x.Name)
			}
			return
		}},
		{"agent", func() (out []string) {
			for _, x := range module.Agents {
				out = append(out, x.Name)
			}
			return
		}},
		{"model", func() (out []string) {
			for _, x := range module.Models {
				out = append(out, x.Name)
			}
			return
		}},
		{"function", func() (out []string) {
			for _, x := range module.Functions {
				out = append(out, x.Name)
			}
			return
		}},
		{"workflow", func() (out []string) {
			for _, x := range module.Workflows {
				out = append(out, x.Name)
			}
			return
		}},
	} {
		if hasName(item.names(), name) {
			return item.kind
		}
	}
	return "name"
}

// renameScoped gives the functions and workflows in names their new names
// everywhere in a freshly parsed module: their declarations, calls,
// references and test runs.
func renameScoped(value reflect.Value, names map[string]string) {
	switch value.Kind() {
	case reflect.Pointer, reflect.Interface:
		if value.IsNil() {
			return
		}
		if value.Kind() == reflect.Pointer {
			switch node := value.Interface().(type) {
			case *FuncDecl:
				node.Name = renamedTo(node.Name, names)
			case *WorkflowDecl:
				node.Name = renamedTo(node.Name, names)
			case *CallExpr:
				node.Name = renamedTo(node.Name, names)
			case *FuncRefExpr:
				node.Name = renamedTo(node.Name, names)
			case *StepCall:
				if node.Kind == "function" || node.Kind == "workflow" {
					node.Name = renamedTo(node.Name, names)
				}
			}
		}
		renameScoped(value.Elem(), names)
	case reflect.Struct:
		for i := 0; i < value.NumField(); i++ {
			if value.Type().Field(i).IsExported() {
				renameScoped(value.Field(i), names)
			}
		}
	case reflect.Slice, reflect.Array:
		for i := 0; i < value.Len(); i++ {
			renameScoped(value.Index(i), names)
		}
	case reflect.Map:
		for _, key := range value.MapKeys() {
			renameScoped(value.MapIndex(key), names)
		}
	}
}

func renamedTo(name string, names map[string]string) string {
	if renamed, ok := names[name]; ok {
		return renamed
	}
	return name
}
