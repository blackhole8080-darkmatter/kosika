package main

// System tools: a program's way to the files in one folder and to the
// programs the person lets it start, as kosika/system_tools.py has them.
//
//   tool save { source system "files"  operation: "write" ... }
//   tool test { source system "shell"  operation: "python" ... }
//
// Declaring one grants nothing. The person running the program names the
// folder (--allow-folder) and allows programs to be started (--allow-shell);
// a path that leaves the folder is refused; writing a file or starting a
// program changes something, so it must be `access: execute` and waits for
// an approval of exactly that call. The other kinds the reference has (web,
// kosika, meaning, agents, outside) are refused by name as the file is read.

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	systemPrefix     = "system:"
	peekCharacters   = 4000
	shellSeconds     = 60
	maxOutput        = 64 * 1024
	maxFileBytes     = 1024 * 1024
	maxListedNames   = 1000
	maxSearchMatches = 40
	maxSearchFiles   = 5000
	maxSearchSeconds = 8
	maxSearchLine    = 240
)

var systemKinds = []string{"shell", "files", "web", "kosika", "meaning", "agents", "outside"}

// The kinds this engine reaches; the rest are refused as the file is read.
var systemKindsHere = map[string]bool{"files": true, "shell": true}

var fileOperations = []string{"read", "peek", "search", "find", "write", "create", "write_all", "list"}
var readOnlyOperations = map[string]bool{"read": true, "peek": true, "search": true, "find": true, "list": true}
var notSearched = map[string]bool{"node_modules": true, "__pycache__": true, "venv": true, "dist": true, "build": true, "target": true, "_trash": true}

// plannedFiles stands, in a shape, for an array of records that each have
// exactly the fields of a planned file.
const plannedFiles = "<planned files>"

var planFileFields = []Param{{"path", "Text"}, {"text", "Text"}, {"before", "Text"}, {"exists", "Bool"}}

type shape struct{ input, output []Param }

// systemShapes are the record fields each kind of system tool must take
// and give: SHAPES in kosika/system_tools.py, in its order.
var systemShapes = map[string]shape{
	"shell":     {[]Param{{"args", "Text[]"}}, []Param{{"exit_code", "Number"}, {"stdout", "Text"}, {"stderr", "Text"}}},
	"read":      {[]Param{{"path", "Text"}}, []Param{{"text", "Text"}}},
	"peek":      {[]Param{{"path", "Text"}}, []Param{{"text", "Text"}}},
	"search":    {[]Param{{"path", "Text"}, {"query", "Text"}}, []Param{{"matches", "Text[]"}}},
	"find":      {[]Param{{"path", "Text"}, {"query", "Text"}}, []Param{{"matches", "Text[]"}}},
	"write":     {[]Param{{"path", "Text"}, {"text", "Text"}}, []Param{{"bytes", "Number"}}},
	"create":    {[]Param{{"path", "Text"}, {"text", "Text"}}, []Param{{"bytes", "Number"}}},
	"write_all": {[]Param{{"files", plannedFiles}, {"note", "Text"}}, []Param{{"count", "Number"}}},
	"list":      {[]Param{{"path", "Text"}}, []Param{{"names", "Text[]"}}},
}

func systemKind(tool *ToolDecl) string {
	if !strings.HasPrefix(tool.Source, systemPrefix) {
		return ""
	}
	return strings.TrimPrefix(tool.Source, systemPrefix)
}

func fieldsOf(record *RecordDecl) map[string]string {
	if record == nil {
		return nil
	}
	fields := map[string]string{}
	for _, field := range record.Fields {
		fields[field.Name] = field.Type
	}
	return fields
}

// fitsShape is whether a record has exactly the wanted fields.
func (c *checker) fitsShape(name string, wanted []Param) bool {
	actual := fieldsOf(c.records[name])
	if actual == nil || len(actual) != len(wanted) {
		return false
	}
	for _, field := range wanted {
		have, ok := actual[field.Name]
		if !ok {
			return false
		}
		if field.Type != plannedFiles {
			if have != field.Type {
				return false
			}
			continue
		}
		if !strings.HasSuffix(have, "[]") {
			return false
		}
		planned := fieldsOf(c.records[strings.TrimSuffix(have, "[]")])
		if len(planned) != len(planFileFields) {
			return false
		}
		for _, wantedField := range planFileFields {
			if planned[wantedField.Name] != wantedField.Type {
				return false
			}
		}
	}
	return true
}

// systemProblem is why a system tool's declaration is refused, if it is:
// declaration_problem in kosika/system_tools.py.
func (c *checker) systemProblem(tool *ToolDecl) string {
	kind := systemKind(tool)
	if kind == "" {
		return ""
	}
	if !hasName(systemKinds, kind) {
		return fmt.Sprintf("Tool %s has unknown system source %s; use one of: %s", pyRepr(tool.Name), pyRepr(kind), strings.Join(systemKinds, ", "))
	}
	looksOnly := (kind == "files" && readOnlyOperations[tool.Operation]) || kind != "files" && kind != "shell"
	if tool.Access != "execute" && !looksOnly {
		return fmt.Sprintf("Tool %s uses system %s and must declare access: execute, so that every call needs approval", pyRepr(tool.Name), kind)
	}
	if kind == "files" && !hasName(fileOperations, tool.Operation) {
		return fmt.Sprintf("Tool %s has unknown files operation %s; use one of: %s", pyRepr(tool.Name), pyRepr(tool.Operation), strings.Join(fileOperations, ", "))
	}
	if kind == "shell" && (strings.TrimSpace(tool.Operation) == "" || strings.Contains(strings.TrimSpace(tool.Operation), " ")) {
		return fmt.Sprintf("Tool %s must name exactly one program in operation, with its arguments passed in args", pyRepr(tool.Name))
	}
	wanted := systemShapes["shell"]
	if kind == "files" {
		wanted = systemShapes[tool.Operation]
	}
	var plannedText []string
	for _, field := range planFileFields {
		plannedText = append(plannedText, field.Name+": "+field.Type)
	}
	for _, side := range []struct {
		label, record string
		fields        []Param
	}{{"input", tool.Input, wanted.input}, {"output", tool.Output, wanted.output}} {
		if c.fitsShape(side.record, side.fields) {
			continue
		}
		var parts []string
		for _, field := range side.fields {
			kindText := field.Type
			if field.Type == plannedFiles {
				kindText = "R[], where R is a record {" + strings.Join(plannedText, ", ") + "}"
			}
			parts = append(parts, field.Name+": "+kindText)
		}
		return fmt.Sprintf("Tool %s %s record must have exactly the fields {%s}", pyRepr(tool.Name), side.label, strings.Join(parts, ", "))
	}
	return ""
}

// inside is a path within the granted folder, or the step fails.
func withinFolder(folder, relative string) string {
	if strings.TrimSpace(relative) == "" || filepath.IsAbs(relative) || filepath.VolumeName(relative) != "" {
		runFail(fmt.Sprintf("path %s must be relative to the granted folder", pyRepr(relative)))
	}
	if strings.HasPrefix(relative, "/") || strings.HasPrefix(relative, `\`) {
		runFail(fmt.Sprintf("path %s leaves the granted folder", pyRepr(relative)))
	}
	current := folder
	for _, part := range strings.FieldsFunc(relative, func(r rune) bool { return r == '/' || r == '\\' }) {
		if part == "." {
			continue
		}
		if part == ".." {
			runFail(fmt.Sprintf("path %s leaves the granted folder", pyRepr(relative)))
		}
		current = filepath.Join(current, part)
		if info, err := os.Lstat(current); err == nil && info.Mode()&os.ModeSymlink != 0 {
			runFail(fmt.Sprintf("path %s passes through a symlink", pyRepr(relative)))
		}
	}
	resolved, err := filepath.Abs(current)
	if err != nil || !(resolved == folder || strings.HasPrefix(resolved, strings.TrimRight(folder, `\/`)+string(filepath.Separator))) {
		runFail(fmt.Sprintf("path %s leaves the granted folder", pyRepr(relative)))
	}
	return resolved
}

// universal is text with every line end written \n, as Python reads text.
func universal(text string) string {
	return strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n")
}

func clip(text string) string {
	runes := []rune(text)
	if len(runes) <= maxOutput {
		return text
	}
	return string(runes[:maxOutput]) + "\n[output cut]"
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

func isDir(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

func (r *run) startAt(path string) string {
	if path == "." || path == "" {
		return r.folder
	}
	return withinFolder(r.folder, path)
}

// fileTool answers a files tool in the granted folder.
func (r *run) fileTool(tool *ToolDecl, arguments *Record) Value {
	text := func(name string) string {
		value, _ := arguments.Fields[name].(string)
		return value
	}
	result := newRecord()
	switch tool.Operation {
	case "read", "peek":
		path := withinFolder(r.folder, text("path"))
		if !isFile(path) {
			runFail(fmt.Sprintf("%s is not a file", pyRepr(text("path"))))
		}
		info, _ := os.Stat(path)
		if tool.Operation == "read" && info.Size() > maxFileBytes {
			runFail(fmt.Sprintf("%s is larger than 1 MiB", pyRepr(text("path"))))
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			runFail(err.Error())
		}
		if !utf8.Valid(raw) {
			runFail(fmt.Sprintf("%s is not UTF-8 text", pyRepr(text("path"))))
		}
		read := universal(string(raw))
		if tool.Operation == "peek" {
			if runes := []rune(read); len(runes) > peekCharacters {
				read = string(runes[:peekCharacters]) + "\n[the file goes on]"
			}
		}
		result.set("text", read)
	case "list":
		path := r.startAt(text("path"))
		if !isDir(path) {
			runFail(fmt.Sprintf("%s is not a folder", pyRepr(text("path"))))
		}
		entries, err := os.ReadDir(path)
		if err != nil {
			runFail(err.Error())
		}
		names := []string{}
		for _, entry := range entries {
			name := entry.Name()
			if isDir(filepath.Join(path, name)) {
				name += "/"
			}
			names = append(names, name)
		}
		sort.Strings(names)
		if len(names) > maxListedNames {
			runFail(fmt.Sprintf("folder has more than %d entries", maxListedNames))
		}
		values := []Value{}
		for _, name := range names {
			values = append(values, name)
		}
		result.set("names", values)
	case "search":
		result.set("matches", r.search(text("path"), text("query")))
	case "write", "create":
		data := []byte(text("text"))
		if len(data) > maxFileBytes {
			runFail("text to write is larger than 1 MiB")
		}
		path := withinFolder(r.folder, text("path"))
		if tool.Operation == "write" && isDir(path) {
			runFail(fmt.Sprintf("%s is a folder", pyRepr(text("path"))))
		}
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			runFail(err.Error())
		}
		if tool.Operation == "create" {
			file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
			if errors.Is(err, os.ErrExist) {
				runFail(fmt.Sprintf("%s already exists; create never overwrites", pyRepr(text("path"))))
			}
			if err != nil {
				runFail(err.Error())
			}
			_, err = file.Write(data)
			file.Close()
			if err != nil {
				runFail(err.Error())
			}
		} else if err := os.WriteFile(path, data, 0o644); err != nil {
			runFail(err.Error())
		}
		result.set("bytes", bigInt(len(data)))
	case "write_all":
		files, _ := arguments.Fields["files"].([]Value)
		type ready struct {
			path string
			data []byte
		}
		// Every path is looked at before any file is written, so a list
		// with one bad path writes nothing.
		var all []ready
		for _, item := range files {
			file, _ := item.(*Record)
			path, _ := file.Fields["path"].(string)
			body, _ := file.Fields["text"].(string)
			data := []byte(body)
			if len(data) > maxFileBytes {
				runFail(fmt.Sprintf("%s would be larger than 1 MiB", pyRepr(path)))
			}
			at := withinFolder(r.folder, path)
			if isDir(at) {
				runFail(fmt.Sprintf("%s is a folder", pyRepr(path)))
			}
			all = append(all, ready{at, data})
		}
		for _, one := range all {
			if err := os.MkdirAll(filepath.Dir(one.path), 0o755); err != nil {
				runFail(err.Error())
			}
			if err := os.WriteFile(one.path, one.data, 0o644); err != nil {
				runFail(err.Error())
			}
		}
		result.set("count", bigInt(len(all)))
	default:
		runFail(fmt.Sprintf("kosika-native cannot %s files yet", tool.Operation))
	}
	return result
}

type found struct {
	hits         int
	name         string
	number       int
	line         string
	nameSortable string
}

// search is the lines of the text files under a folder that hold the words
// asked for, best first, each "path:line: text".
func (r *run) search(where, query string) []Value {
	start := r.startAt(where)
	if !isDir(start) {
		runFail(fmt.Sprintf("%s is not a folder", pyRepr(where)))
	}
	words := strings.Fields(strings.ToLower(query))
	if len(words) == 0 {
		runFail("a search needs at least one word")
	}
	letter := func(r rune) bool { return (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') }
	// A word is looked for where a word begins, so that "rent" finds
	// "rental" and not "current"; one of three letters or fewer must be
	// the whole word.
	holds := func(line []rune, word []rune) bool {
		for i := 0; i+len(word) <= len(line); i++ {
			if i > 0 && letter(line[i-1]) {
				continue
			}
			if string(line[i:i+len(word)]) != string(word) {
				continue
			}
			end := i + len(word)
			if len(word) <= 3 && end < len(line) && letter(line[end]) {
				continue
			}
			return true
		}
		return false
	}
	deadline := time.Now().Add(maxSearchSeconds * time.Second)
	var all []found
	seen := 0
	pending := []string{start}
	for len(pending) > 0 && seen < maxSearchFiles && time.Now().Before(deadline) {
		current := pending[0]
		pending = pending[1:]
		entries, err := os.ReadDir(current)
		if err != nil {
			continue
		}
		sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
		for _, entry := range entries {
			path := filepath.Join(current, entry.Name())
			info, err := os.Lstat(path)
			if strings.HasPrefix(entry.Name(), ".") || err != nil || info.Mode()&os.ModeSymlink != 0 {
				continue
			}
			if info.IsDir() {
				if !notSearched[entry.Name()] {
					pending = append(pending, path)
				}
				continue
			}
			seen++
			if info.Size() > maxFileBytes {
				continue
			}
			raw, err := os.ReadFile(path)
			if err != nil || !utf8.Valid(raw) {
				continue
			}
			relative, _ := filepath.Rel(r.folder, path)
			name := filepath.ToSlash(relative)
			for number, row := range splitLines(universal(string(raw))) {
				lower := []rune(strings.ToLower(row))
				hits := 0
				for _, word := range words {
					if holds(lower, []rune(word)) {
						hits++
					}
				}
				if hits > 0 {
					all = append(all, found{hits, name, number + 1, strings.TrimSpace(row), name})
				}
			}
		}
	}
	sort.SliceStable(all, func(i, j int) bool {
		if all[i].hits != all[j].hits {
			return all[i].hits > all[j].hits
		}
		if all[i].name != all[j].name {
			return all[i].name < all[j].name
		}
		return all[i].number < all[j].number
	})
	matches := []Value{}
	for i, item := range all {
		if i == maxSearchMatches {
			break
		}
		line := []rune(item.line)
		if len(line) > maxSearchLine {
			line = line[:maxSearchLine]
		}
		matches = append(matches, fmt.Sprintf("%s:%d: %s", item.name, item.number, string(line)))
	}
	return matches
}

// shellTool starts the program a shell tool names, in the granted folder.
func (r *run) shellTool(tool *ToolDecl, arguments *Record) Value {
	executable, err := exec.LookPath(tool.Operation)
	if err != nil {
		runFail(fmt.Sprintf("program %s was not found", pyRepr(tool.Operation)))
	}
	var args []string
	if list, ok := arguments.Fields["args"].([]Value); ok {
		for _, item := range list {
			text, _ := item.(string)
			args = append(args, text)
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), shellSeconds*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, executable, args...)
	command.Dir = r.folder
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	runErr := command.Run()
	if ctx.Err() == context.DeadlineExceeded {
		runFail(fmt.Sprintf("program %s ran longer than %d seconds and was stopped", pyRepr(tool.Operation), shellSeconds))
	}
	code := 0
	var exited *exec.ExitError
	if errors.As(runErr, &exited) {
		code = exited.ExitCode()
	} else if runErr != nil {
		runFail(runErr.Error())
	}
	result := newRecord()
	result.set("exit_code", bigInt(code))
	result.set("stdout", clip(universal(strings.ToValidUTF8(stdout.String(), "�"))))
	result.set("stderr", clip(universal(strings.ToValidUTF8(stderr.String(), "�"))))
	return result
}

// systemBound is whether a run may reach a system tool: files with a
// granted folder, a program started with programs allowed too.
func (r *run) systemBound(tool *ToolDecl) bool {
	switch systemKind(tool) {
	case "files":
		return r.folder != "" && tool.Operation != "find"
	case "shell":
		return r.folder != "" && r.allowShell
	}
	return false
}
