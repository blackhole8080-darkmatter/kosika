package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const askTwo = `record Job { text: Text }
record Said { text: Text }
record Both { text: Text }

model fast {
  input: Job
  output: Said
}

model slow {
  input: Job
  output: Said
}

fn both(x: Said, y: Said) -> Both {
  return {text: x.text + " / " + y.text}
}

workflow main(a: Job) -> Both {
  parallel {
    x = use_model fast(a.text)
    y = use_model slow(a.text + "!")
  }
  out = both(x, y)
}
`

// A model server that takes a while and says back what it was asked.
func slowServer(t *testing.T, wait time.Duration, most *int64) *httptest.Server {
	var now int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		in := atomic.AddInt64(&now, 1)
		for {
			seen := atomic.LoadInt64(most)
			if in <= seen || atomic.CompareAndSwapInt64(most, seen, in) {
				break
			}
		}
		time.Sleep(wait)
		atomic.AddInt64(&now, -1)
		body := make([]byte, r.ContentLength)
		r.Body.Read(body)
		said := "one"
		if strings.Contains(string(body), "!") {
			said = "two"
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"choices": [{"message": {"role": "assistant", "content": "{\"text\": \"` + said + `\"}"}, "finish_reason": "stop"}]}`))
	}))
	t.Cleanup(server.Close)
	return server
}

// The model calls of a parallel block are made at the same time, and the
// trace is the one running them in order writes.
func TestParallelBlockAsksItsModelsAtOnce(t *testing.T) {
	var most int64
	server := slowServer(t, 600*time.Millisecond, &most)
	folder := t.TempDir()
	program := filepath.Join(folder, "two.kos")
	given := filepath.Join(folder, "two.json")
	trace := filepath.Join(folder, "trace.jsonl")
	os.WriteFile(program, []byte(askTwo), 0o644)
	os.WriteFile(given, []byte(`{"a": {"text": "hello"}}`), 0o644)

	began := time.Now()
	code := runCommand([]string{program, "--input", given, "--model", "m", "--model-url", server.URL, "--trace", trace})
	took := time.Since(began)
	if code != 0 {
		t.Fatalf("the run failed with exit %d", code)
	}
	if most != 2 {
		t.Fatalf("at most %d calls were made at once; the block's two should overlap", most)
	}
	if took > 1100*time.Millisecond {
		t.Fatalf("the run took %s; two 0.6 s calls at once take about 0.6 s", took)
	}
	written, _ := os.ReadFile(trace)
	text := string(written)
	first, second := strings.Index(text, `"step": "x"`), strings.Index(text, `"step": "y"`)
	if first < 0 || second < first || !strings.Contains(text, `one / two`) {
		t.Fatalf("the trace is not the one written order gives:\n%s", text)
	}
}
