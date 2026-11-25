package main

// What `step` units remember, as kosika/remembered.py keeps it: each
// result with the inputs it was made for. Within one run a second call of
// the same step with the same inputs gives the first call's result. With a
// memory folder the results outlive the run: each is one JSON file in
// FOLDER/steps, named by a digest of the program, the step and its inputs,
// which either engine reads and writes.

import (
	"os"
	"path/filepath"
	"strings"
	"sync"
)

type rememberedStep struct {
	result Value
	events []Value
}

type rememberedSteps struct {
	folder string // "" when the run keeps no memory folder
	mu     sync.Mutex
	known  map[string]rememberedStep
}

func newRememberedSteps(folder string) *rememberedSteps {
	return &rememberedSteps{folder: folder, known: map[string]rememberedStep{}}
}

// stepKey is the name a step's result is remembered by.
func stepKey(programHash, step string, inputs *Record) string {
	key := newRecord()
	key.set("inputs", inputs)
	key.set("program", programHash)
	key.set("step", step)
	return digest(key)
}

func (s *rememberedSteps) path(key string) string {
	return filepath.Join(s.folder, "steps", key+".json")
}

// recall gives the result kept under key and the trace of the call that
// made it.
func (s *rememberedSteps) recall(key string) (rememberedStep, bool) {
	s.mu.Lock()
	found, ok := s.known[key]
	s.mu.Unlock()
	if ok || s.folder == "" {
		return found, ok
	}
	data, err := os.ReadFile(s.path(key))
	if err != nil {
		return rememberedStep{}, false
	}
	parsed, problem := parseStrictJSON(string(data))
	kept, isRecord := parsed.(*Record)
	if problem != "" || !isRecord {
		return rememberedStep{}, false
	}
	result, has := kept.Fields["result"]
	if !has {
		return rememberedStep{}, false
	}
	events, _ := kept.Fields["events"].([]Value)
	found = rememberedStep{result, events}
	s.mu.Lock()
	s.known[key] = found
	s.mu.Unlock()
	return found, true
}

func (s *rememberedSteps) keep(key, step string, inputs *Record, result Value, events []Value) {
	s.mu.Lock()
	s.known[key] = rememberedStep{result, events}
	s.mu.Unlock()
	if s.folder == "" {
		return
	}
	path := s.path(key)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return
	}
	kept := newRecord()
	kept.set("events", eventList(events))
	kept.set("inputs", inputs)
	kept.set("result", result)
	kept.set("step", step)
	spare := strings.TrimSuffix(path, ".json") + ".tmp"
	if os.WriteFile(spare, []byte(unicodeJSON(kept, true, false)), 0o644) == nil {
		os.Rename(spare, path)
	}
}
