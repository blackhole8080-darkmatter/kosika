package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestPhoneMCPManifestPinsTheTools(t *testing.T) {
	path := filepath.Join(t.TempDir(), "phone_manifest.json")
	if err := os.WriteFile(path, []byte(phoneManifest("phone")), 0o600); err != nil {
		t.Fatal(err)
	}
	manifest, problem := loadManifest(path)
	if problem != "" {
		t.Fatal(problem)
	}
	if manifest.wait != phoneMCPWait*time.Second || manifest.name != phoneMCPName || len(manifest.tools) != 2 {
		t.Fatalf("manifest read as %+v", manifest)
	}
	// The example ships the same manifest.
	shipped, err := os.ReadFile("../examples/phone/phone_manifest.json")
	if err != nil || string(shipped) != phoneManifest("phone") {
		t.Fatalf("examples/phone/phone_manifest.json is not `phone mcp --manifest` (%v)", err)
	}
}

func TestPhoneMCPWaitIsChecked(t *testing.T) {
	path := filepath.Join(t.TempDir(), "m.json")
	bad := strings.Replace(phoneManifest("phone"), `"waitSeconds": 150`, `"waitSeconds": 9000`, 1)
	os.WriteFile(path, []byte(bad), 0o600)
	if _, problem := loadManifest(path); !strings.Contains(problem, "waitSeconds") {
		t.Fatalf("a wait of 9000 seconds was let through: %q", problem)
	}
}

func TestPhoneMCPServerAnswers(t *testing.T) {
	in := strings.Join([]string{
		`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`,
		`{"jsonrpc":"2.0","method":"notifications/initialized"}`,
		`{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}`,
		`{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"phone_do","arguments":{"device":"iphone","action":"show","args":["hi"]}}}`,
		`{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"phone_ask","arguments":{"device":"iphone","sense":"nothing"}}}`,
	}, "\n")
	var out bytes.Buffer
	var asked []string
	call := func(name string, arguments map[string]any) (map[string]any, error) {
		asked = append(asked, name)
		if name == "phone_ask" {
			return phoneToolCall(name, arguments) // refused before anything is sent
		}
		return map[string]any{"device": "iphone", "status": "done", "note": "", "result": "", "simulated": true}, nil
	}
	if err := servePhoneMCP(strings.NewReader(in), &out, call); err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	if len(lines) != 4 {
		t.Fatalf("want 4 answers (the notification gets none), got %d:\n%s", len(lines), out.String())
	}
	var answers []map[string]any
	for _, line := range lines {
		var answer map[string]any
		if err := json.Unmarshal([]byte(line), &answer); err != nil {
			t.Fatal(err)
		}
		answers = append(answers, answer)
	}
	info := answers[0]["result"].(map[string]any)["serverInfo"].(map[string]any)
	if info["name"] != phoneMCPName {
		t.Fatalf("serverInfo %v", info)
	}
	if tools := answers[1]["result"].(map[string]any)["tools"].([]any); len(tools) != 2 {
		t.Fatalf("tools %v", tools)
	}
	done := answers[2]["result"].(map[string]any)["structuredContent"].(map[string]any)
	if done["status"] != "done" || done["simulated"] != true {
		t.Fatalf("phone_do gave %v", done)
	}
	failed := answers[3]["result"].(map[string]any)
	if failed["isError"] != true || !strings.Contains(failed["content"].([]any)[0].(map[string]any)["text"].(string), "no sense called") {
		t.Fatalf("an unknown sense was not refused: %v", failed)
	}
}
