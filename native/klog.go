package main

// Kosika logs: typed, add-only, tamper-evident JSON Lines, for a
// `system "log"` tool. kosika/klog.py says what a log is; this writes the
// same bytes for the same entries and reads a log the same way.

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	logShapeDigits = 16
	maxLogBytes    = 16 * 1024 * 1024
	maxLogRows     = 100000
)

// logEntry stands, in a shape, for any record of the program; logRows for
// an array of one.
const (
	logEntry = "<log entry>"
	logRows  = "<log rows>"
)

func hashText(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])
}

// logEntryType is the record a log tool keeps.
func (r *run) logEntryType(tool *ToolDecl) string {
	if tool.Operation == "append" {
		return fieldsOf(r.checker.records[tool.Input])["entry"]
	}
	return strings.TrimSuffix(fieldsOf(r.checker.records[tool.Output])["rows"], "[]")
}

func (r *run) logShape(name string) string {
	record := r.checker.records[name]
	var parts []string
	for _, field := range record.Fields {
		parts = append(parts, field.Name+":"+field.Type)
	}
	return hashText(name + "{" + strings.Join(parts, ",") + "}")[:logShapeDigits]
}

// logString writes text as Python's json.dumps(ensure_ascii=False) does,
// with U+2028 and U+2029 escaped as klog.line_of escapes them.
func logString(text string) string {
	var out strings.Builder
	out.WriteByte('"')
	for _, ch := range text {
		switch ch {
		case '"':
			out.WriteString(`\"`)
		case '\\':
			out.WriteString(`\\`)
		case '\n':
			out.WriteString(`\n`)
		case '\r':
			out.WriteString(`\r`)
		case '\t':
			out.WriteString(`\t`)
		case '\b':
			out.WriteString(`\b`)
		case '\f':
			out.WriteString(`\f`)
		case ' ', ' ':
			fmt.Fprintf(&out, `\u%04x`, ch)
		default:
			if ch < 0x20 {
				fmt.Fprintf(&out, `\u%04x`, ch)
			} else if ch == utf8.RuneError {
				out.WriteString("�")
			} else {
				out.WriteRune(ch)
			}
		}
	}
	out.WriteByte('"')
	return out.String()
}

// logLine is one line of a log: compact JSON, whole numbers without a
// decimal point, records inside with their keys sorted.
func logLine(v Value) string {
	var out strings.Builder
	var write func(Value)
	write = func(v Value) {
		switch x := v.(type) {
		case nil:
			out.WriteString("null")
		case bool:
			if x {
				out.WriteString("true")
			} else {
				out.WriteString("false")
			}
		case *big.Int:
			out.WriteString(x.String())
		case float64:
			if math.IsInf(x, 0) || math.IsNaN(x) {
				runFail("a log cannot hold a number that is not finite")
			}
			if x == math.Trunc(x) && math.Abs(x) < 1<<53 {
				out.WriteString(big.NewInt(int64(x)).String())
			} else {
				out.WriteString(pyNumber(x))
			}
		case string:
			out.WriteString(logString(x))
		case []Value:
			out.WriteString("[")
			for i, item := range x {
				if i > 0 {
					out.WriteString(",")
				}
				write(item)
			}
			out.WriteString("]")
		case []string:
			items := make([]Value, len(x))
			for i, s := range x {
				items[i] = s
			}
			write(items)
		case *Record:
			keys := append([]string{}, x.Keys...)
			sort.Strings(keys)
			out.WriteString("{")
			for i, key := range keys {
				if i > 0 {
					out.WriteString(",")
				}
				out.WriteString(logString(key))
				out.WriteString(":")
				write(x.Fields[key])
			}
			out.WriteString("}")
		default:
			runFail("a log cannot hold a function")
		}
	}
	write(v)
	return out.String()
}

func (r *run) logFields(name string) []string {
	var names []string
	for _, field := range r.checker.records[name].Fields {
		names = append(names, field.Name)
	}
	return names
}

// logHeader is a log's first line. Its keys are written in this order,
// as klog.header writes them.
func (r *run) logHeader(name string) string {
	fields := []Value{}
	for _, f := range r.logFields(name) {
		fields = append(fields, f)
	}
	return "{" + `"kosika":"log","type":` + logString(name) + `,"fields":` + logLine(fields) +
		`,"shape":` + logString(r.logShape(name)) + "}"
}

func logLines(path string) []string {
	info, err := os.Stat(path)
	if err != nil {
		return nil
	}
	if info.Size() > maxLogBytes {
		runFail(fmt.Sprintf("%s is larger than 16 MiB", pyRepr(filepath.Base(path))))
	}
	data, _ := os.ReadFile(path)
	var lines []string
	for _, line := range strings.Split(string(data), "\n") {
		if line != "" {
			lines = append(lines, line)
		}
	}
	return lines
}

// logHeaderProblem is why a first line is not a header for the record.
func (r *run) logHeaderProblem(name, first string) string {
	value, problem := parseStrictJSON(first)
	if problem != "" {
		return "line 1 is not JSON"
	}
	head, ok := value.(*Record)
	if !ok || head.Fields["kosika"] != "log" {
		return "line 1 is not a Kosika log header"
	}
	if head.Fields["type"] != name {
		return fmt.Sprintf("the log holds %s, not %s", pyReprAny(head.Fields["type"]), pyRepr(name))
	}
	if head.Fields["shape"] != r.logShape(name) {
		return fmt.Sprintf("the log's %s has another shape (%v); this program's is %s", name, head.Fields["shape"], r.logShape(name))
	}
	return ""
}

func (r *run) logTool(tool *ToolDecl, arguments *Record) Value {
	name := r.logEntryType(tool)
	relative, _ := arguments.Fields["path"].(string)
	path := withinFolder(r.folder, relative)
	lines := logLines(path)
	fields := r.logFields(name)
	result := newRecord()
	if tool.Operation == "append" {
		entry, _ := arguments.Fields["entry"].(*Record)
		r.validate(entry, name, "$")
		by, _ := arguments.Fields["by"].(string)
		var added []string
		if len(lines) == 0 {
			lines = []string{r.logHeader(name)}
			added = append(added, lines[0])
		} else if problem := r.logProblem(name, lines, fields); problem != "" && !strings.HasPrefix(problem, "more than") {
			// Nothing is added to a log that was changed: the new entry
			// would chain onto the change and hide it.
			runFail(filepath.Base(path) + ": " + problem + "; nothing was added")
		}
		cells := []Value{bigInt(len(lines)), time.Now().UTC().Format("2006-01-02T15:04:05Z"), by, hashText(lines[len(lines)-1])}
		for _, f := range fields {
			cells = append(cells, entry.Fields[f])
		}
		line := logLine(cells)
		added = append(added, line)
		os.MkdirAll(filepath.Dir(path), 0o755)
		file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err != nil {
			runFail(fmt.Sprintf("%s cannot be written", pyRepr(relative)))
		}
		file.WriteString(strings.Join(added, "\n") + "\n")
		file.Close()
		result.set("n", bigInt(len(lines)))
		result.set("hash", hashText(line))
		return result
	}
	rows, problem := r.logRead(name, lines, fields)
	result.set("rows", rows)
	result.set("ok", problem == "")
	result.set("problem", problem)
	return result
}

func (r *run) logProblem(name string, lines, fields []string) string {
	_, problem := r.logRead(name, lines, fields)
	return problem
}

// logRead is a log's entries, checked, and the first problem: the entries
// before it are given, none after it.
func (r *run) logRead(name string, lines, fields []string) ([]Value, string) {
	rows := []Value{}
	problem := ""
	if len(lines) > 0 {
		problem = r.logHeaderProblem(name, lines[0])
		prev := hashText(lines[0])
		for i := 1; problem == "" && i < len(lines); i++ {
			at := i + 1
			if len(rows) >= maxLogRows {
				problem = fmt.Sprintf("more than %d entries; only the first are given", maxLogRows)
				break
			}
			value, bad := parseStrictJSON(lines[i])
			cells, isList := value.([]Value)
			if bad != "" {
				problem = fmt.Sprintf("line %d is not JSON", at)
				break
			}
			if !isList || len(cells) != 4+len(fields) {
				problem = fmt.Sprintf("line %d is not an entry of %d fields", at, len(fields))
				break
			}
			if n, ok := cells[0].(*big.Int); !ok || n.Int64() != int64(at-1) {
				problem = fmt.Sprintf("line %d says entry %s, but it is entry %d: an entry was removed or moved", at, pyReprAny(cells[0]), at-1)
				break
			}
			if cells[3] != prev {
				problem = fmt.Sprintf("line %d does not follow line %d: line %d was changed", at, at-1, at-1)
				break
			}
			entry := newRecord()
			for j, f := range fields {
				entry.set(f, cells[4+j])
			}
			if why := r.problem(entry, name, "$"); why != "" {
				problem = fmt.Sprintf("line %d is not a %s: %s", at, name, why)
				break
			}
			rows = append(rows, entry)
			prev = hashText(lines[i])
		}
		if strings.HasPrefix(problem, "the log") || strings.HasPrefix(problem, "line 1") {
			rows = []Value{}
		}
	}
	return rows, problem
}
