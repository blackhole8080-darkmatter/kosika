package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

const askOnce = `record Job { text: Text }
record Said { text: Text }

model thinker {
  input: Job
  output: Said as text
}

workflow main(a: Job) -> Said {
  said = use_model thinker(a.text)
}
`

// A service that is always busy is asked again for minutes; a run given
// --deadline-seconds stops when its time is up, and says that it was the
// deadline, not the service, that stopped it.
func TestRunStopsAtItsDeadline(t *testing.T) {
	busy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		w.Write([]byte(`{"error": {"message": "busy"}}`))
	}))
	defer busy.Close()
	t.Cleanup(func() { runEnds = time.Time{} })

	folder := t.TempDir()
	program := filepath.Join(folder, "ask.kos")
	given := filepath.Join(folder, "ask.json")
	os.WriteFile(program, []byte(askOnce), 0o644)
	os.WriteFile(given, []byte(`{"a": {"text": "hello"}}`), 0o644)
	trace := filepath.Join(folder, "trace.jsonl")

	began := time.Now()
	code := runCommand([]string{program, "--input", given, "--model", "busy", "--model-url", busy.URL,
		"--model-timeout", "60", "--deadline-seconds", "2", "--trace", trace})
	took := time.Since(began)
	if code == 0 {
		t.Fatalf("a run past its deadline must not succeed")
	}
	if took > 10*time.Second {
		t.Fatalf("the run took %s; it should stop at its 2 s deadline", took)
	}
	written, _ := os.ReadFile(trace)
	if !contains(string(written), `"code": "deadline"`) {
		t.Fatalf("the trace does not say the deadline stopped the run:\n%s", written)
	}
}

func TestDeadlineSecondsMustBeSensible(t *testing.T) {
	folder := t.TempDir()
	program := filepath.Join(folder, "ask.kos")
	os.WriteFile(program, []byte(askOnce), 0o644)
	for _, bad := range []string{"0", "-1", "abc", "90000"} {
		if code := runCommand([]string{program, "--deadline-seconds", bad}); code != 2 {
			t.Errorf("--deadline-seconds %s gave exit %d, want 2", bad, code)
		}
	}
}

func contains(text, part string) bool {
	return len(part) == 0 || len(text) >= len(part) && (func() bool {
		for i := 0; i+len(part) <= len(text); i++ {
			if text[i:i+len(part)] == part {
				return true
			}
		}
		return false
	})()
}
