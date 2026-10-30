package main

// The kosika tools, which reach nothing and need no grant, as
// check_source and files_checker in kosika/system_tools.py: one checks a
// Kosika program given as text and runs its tests, the other says what is
// wrong with files a program means to write. And notify, which sends a
// note to the one address the person running the program named.

import (
	"bytes"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"
)

const (
	maxCheckedSource = 64 * 1024
	maxCheckedTests  = 30
	maxNoteBytes     = 4000
	maxPageBytes     = 512 * 1024
	maxLinkChars     = 2048
	maxNameChars     = 100
)

// checkSource is a `system "kosika"` tool's `check`: the program is put in
// a folder made for the purpose, checked, and its tests run as `test` runs
// them.
func checkSource(source string) Value {
	result := newRecord()
	refuse := func(problem string) Value {
		result.set("ok", false)
		result.set("problem", problem)
		result.set("tests", bigInt(0))
		result.set("passed", bigInt(0))
		return result
	}
	if len(source) > maxCheckedSource {
		return refuse("The program is larger than 64 KiB.")
	}
	scratch, err := os.MkdirTemp("", "kosika-check-")
	if err != nil {
		return refuse("The program could not be put anywhere to check.")
	}
	defer os.RemoveAll(scratch)
	path := filepath.Join(realPath(scratch), "main.kos")
	if os.WriteFile(path, []byte(source), 0o644) != nil {
		return refuse("The program could not be put anywhere to check.")
	}
	program, problem := loadChecked(path)
	if problem != "" {
		said := strings.ReplaceAll(strings.ReplaceAll(problem, path+": ", ""), path, "main.kos")
		return refuse(clipText(said, 600))
	}
	results, failure := testsOf(program)
	if failure != "" {
		return refuse("The tests could not be run: " + clipText(failure, 500))
	}
	if len(results) > maxCheckedTests {
		results = results[:maxCheckedTests]
	}
	passed := 0
	problem = ""
	for _, test := range results {
		if test.Passed {
			passed++
		} else if problem == "" {
			problem = clipText(fmt.Sprintf(`Test "%s" failed at line %d: %s`, test.Name, test.Line, test.Message), 600)
		}
	}
	result.set("ok", problem == "")
	result.set("problem", problem)
	result.set("tests", bigInt(len(results)))
	result.set("passed", bigInt(passed))
	return result
}

// checkFiles is a `system "kosika"` tool's `check_files`: what is wrong
// with the files a program means to write, read as a plan's are.
func checkFiles(arguments *Record) Value {
	request, _ := arguments.Fields["text"].(string)
	var writes []planWrite
	if files, ok := arguments.Fields["files"].([]Value); ok {
		for _, item := range files {
			if file, ok := item.(*Record); ok {
				path, _ := file.Fields["path"].(string)
				text, _ := file.Fields["text"].(string)
				writes = append(writes, planWrite{path, text})
			}
		}
	}
	problems, python := planFileProblems(request, writes)
	if python != "" {
		runFail(fmt.Sprintf("kosika-native cannot check Python files yet: %s is one", pyRepr(python)))
	}
	result := newRecord()
	result.set("problems", stringValues(problems))
	return result
}

// cleanNoteAddress is the address notes go to: one https address with a
// host and a path, no user name or password, no query; or why not.
func cleanNoteAddress(address string) (string, string) {
	trimmed := strings.TrimSpace(address)
	parts, err := url.Parse(trimmed)
	bad := fmt.Sprintf("notify address %s must be one https address with a path, such as https://ntfy.sh/your-private-topic", pyRepr(address))
	if err != nil || parts.Scheme != "https" || parts.Hostname() == "" || parts.User != nil ||
		(parts.Port() != "" && parts.Port() != "443") || parts.RawQuery != "" || parts.Fragment != "" ||
		parts.Path == "" || parts.Path == "/" || strings.Contains(trimmed, "?") || strings.Contains(trimmed, "#") {
		return "", bad
	}
	return trimmed, ""
}

// cleanNoteLink is the address tapping the note opens: one http(s) address
// with a host, no credentials, no spaces; "" when there is none.
func cleanNoteLink(link string) (string, string) {
	trimmed := strings.TrimSpace(link)
	if trimmed == "" {
		return "", ""
	}
	bad := fmt.Sprintf("notify link %s must be one short http(s) address, like https://arxiv.org/abs/2610.23850", pyRepr(link))
	if len(trimmed) > maxLinkChars || strings.ContainsFunc(trimmed, unicode.IsSpace) {
		return "", bad
	}
	parts, err := url.Parse(trimmed)
	if err != nil || (parts.Scheme != "http" && parts.Scheme != "https") || parts.Hostname() == "" || parts.User != nil {
		return "", bad
	}
	return trimmed, ""
}

// cleanNoteFileName is a file name with no path in it: letters, digits,
// dot, dash, underscore.
func cleanNoteFileName(name string) (string, string) {
	kept := strings.Builder{}
	for _, letter := range strings.TrimSpace(name) {
		if letter < 128 && (unicode.IsLetter(letter) || unicode.IsDigit(letter) || letter == '.' || letter == '_' || letter == '-') {
			kept.WriteRune(letter)
		} else {
			kept.WriteByte('-')
		}
	}
	cleaned := clipText(kept.String(), maxNameChars)
	if cleaned == "" || cleaned == "." || cleaned == ".." {
		return "", fmt.Sprintf("notify file name %s is not a file name", pyRepr(name))
	}
	return cleaned, ""
}

// noteTool is a `system "notify"` tool's `send`: one note, sent only to
// the address given, with an optional link to open on tap and an optional
// attached page.
func (r *run) noteTool(arguments *Record) Value {
	title, _ := arguments.Fields["title"].(string)
	text, _ := arguments.Fields["text"].(string)
	link, _ := arguments.Fields["link"].(string)
	fileName, _ := arguments.Fields["file_name"].(string)
	page, _ := arguments.Fields["file"].(string)
	title = clipText(strings.Join(strings.Fields(title), " "), 200)
	cleanedLink, problem := cleanNoteLink(link)
	if problem != "" {
		runFail(problem)
	}
	if (strings.TrimSpace(fileName) != "") != (page != "") {
		runFail("A note's attached file needs a file_name too; give both, or neither for a plain note")
	}
	attachment := []byte(page)
	if page != "" && len(attachment) > maxPageBytes {
		attachment = nil
		text += "\n[the page was too big to attach]"
	}
	data := []byte(text)
	if len(data) > maxNoteBytes {
		cut := strings.ToValidUTF8(string(data[:maxNoteBytes-40]), "")
		data = []byte(cut + "\n[cut: the rest is on the computer]")
		text = string(data)
	}
	ascii := []rune(title)
	for i, letter := range ascii {
		if letter > 127 {
			ascii[i] = '?'
		}
	}
	host := ""
	if parts, err := url.Parse(r.notify); err == nil {
		host = parts.Hostname()
	}
	where := r.notify
	body := data
	attachedName := ""
	var request *http.Request
	var err error
	if attachment != nil {
		cleaned, nameProblem := cleanNoteFileName(fileName)
		if nameProblem != "" {
			runFail(nameProblem)
		}
		attachedName = cleaned
		// The body is the file now, so the words of the note travel as the
		// message parameter; ntfy turns each written \n back into a line.
		message := strings.ReplaceAll(text, "\n", "\\n")
		where = r.notify + "?message=" + strings.ReplaceAll(url.QueryEscape(message), "+", "%20")
		request, err = http.NewRequest("POST", where, bytes.NewReader(attachment))
	} else {
		request, err = http.NewRequest("POST", where, bytes.NewReader(body))
	}
	if err != nil {
		runFail(fmt.Sprintf("Could not reach %s: %v", host, err))
	}
	request.Header.Set("Title", string(ascii))
	if attachment != nil {
		request.Header.Set("Filename", attachedName)
		request.Header.Set("Content-Type", "text/html; charset=utf-8")
	} else {
		request.Header.Set("Content-Type", "text/plain; charset=utf-8")
	}
	if cleanedLink != "" {
		request.Header.Set("Click", cleanedLink)
	}
	response, err := (&http.Client{Timeout: 20 * time.Second}).Do(request)
	if err != nil {
		runFail(fmt.Sprintf("Could not reach %s: %v", host, err))
	}
	response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		runFail(fmt.Sprintf("The note was not taken: %s answered %d", host, response.StatusCode))
	}
	result := newRecord()
	result.set("sent", true)
	return result
}
