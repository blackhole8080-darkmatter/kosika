package main

// Searching by meaning, as kosika/embeddings.py does: an embedding model
// the person running the program grants (`--embed-model NAME`) turns texts
// into vectors that are close when their meanings are, so `find` ranks a
// folder's lines by nearness to a question and `near` says how near two
// texts are. Vectors are remembered outside the granted folder, so a line
// is embedded once; a search makes at most 400 new ones, and later
// searches pick up the rest.

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	embedBatch    = 32
	embedMaxText  = 2000
	maxFindMatch  = 12
	maxFindLines  = 4000
	maxFindNew    = 400
	minFindLine   = 12
	vectorKeySize = sha256.Size
)

// A meaning is the granted embedding model, and what it has made.
type meaning struct {
	model, url string
	store      string
	memory     map[[vectorKeySize]byte][]float64
	loaded     bool
	queryLabel string
	textLabel  string
}

func newMeaning(model, modelURL string) *meaning {
	base := strings.TrimRight(modelURL, "/")
	base = strings.TrimSuffix(base, "/v1")
	m := &meaning{model: model, url: base, memory: map[[vectorKeySize]byte][]float64{}}
	if home, err := os.UserHomeDir(); err == nil {
		// Not the reference's store, which is SQLite: one of this engine's
		// own, beside it.
		m.store = filepath.Join(home, ".kosika", "embeddings-native.bin")
	}
	// nomic-embed-text was trained with these two labels.
	if strings.HasPrefix(strings.SplitN(model, ":", 2)[0], "nomic-embed") {
		m.queryLabel, m.textLabel = "search_query: ", "search_document: "
	}
	return m
}

func (m *meaning) key(text string) [vectorKeySize]byte {
	return sha256.Sum256([]byte(m.model + "\x00" + text))
}

// embed asks the model for one vector per text.
func (m *meaning) embed(texts []string) [][]float64 {
	var vectors [][]float64
	client := &http.Client{Timeout: 120 * time.Second}
	for start := 0; start < len(texts); start += embedBatch {
		end := start + embedBatch
		if end > len(texts) {
			end = len(texts)
		}
		batch := make([]string, 0, end-start)
		for _, text := range texts[start:end] {
			if runes := []rune(text); len(runes) > embedMaxText {
				text = string(runes[:embedMaxText])
			}
			batch = append(batch, text)
		}
		body, _ := json.Marshal(map[string]any{"model": m.model, "input": batch})
		response, err := client.Post(m.url+"/api/embed", "application/json", bytes.NewReader(body))
		if err != nil {
			runFail(fmt.Sprintf("embedding model %s gave no answer: %v", pyRepr(m.model), err))
		}
		raw, _ := io.ReadAll(response.Body)
		response.Body.Close()
		var reply struct {
			Embeddings [][]float64 `json:"embeddings"`
		}
		if response.StatusCode != 200 || json.Unmarshal(raw, &reply) != nil || reply.Embeddings == nil {
			runFail(fmt.Sprintf("embedding model %s gave no answer: status %d", pyRepr(m.model), response.StatusCode))
		}
		if len(reply.Embeddings) != len(batch) {
			runFail(fmt.Sprintf("embedding model %s answered %d of %d texts", pyRepr(m.model), len(reply.Embeddings), len(batch)))
		}
		vectors = append(vectors, reply.Embeddings...)
	}
	return vectors
}

// load reads the vectors remembered from earlier runs: records of a key,
// a count, and that many 32-bit floats.
func (m *meaning) load() {
	if m.loaded || m.store == "" {
		return
	}
	m.loaded = true
	raw, err := os.ReadFile(m.store)
	if err != nil {
		return
	}
	for len(raw) >= vectorKeySize+4 {
		var key [vectorKeySize]byte
		copy(key[:], raw)
		count := int(binary.LittleEndian.Uint32(raw[vectorKeySize:]))
		size := vectorKeySize + 4 + 4*count
		if count <= 0 || len(raw) < size {
			return
		}
		vector := make([]float64, count)
		for i := range vector {
			vector[i] = float64(math.Float32frombits(binary.LittleEndian.Uint32(raw[vectorKeySize+4+4*i:])))
		}
		if _, has := m.memory[key]; !has {
			m.memory[key] = vector
		}
		raw = raw[size:]
	}
}

func (m *meaning) keep(keys [][vectorKeySize]byte) {
	if m.store == "" || len(keys) == 0 {
		return
	}
	var out bytes.Buffer
	for _, key := range keys {
		vector := m.memory[key]
		out.Write(key[:])
		_ = binary.Write(&out, binary.LittleEndian, uint32(len(vector)))
		for _, number := range vector {
			_ = binary.Write(&out, binary.LittleEndian, math.Float32bits(float32(number)))
		}
	}
	if os.MkdirAll(filepath.Dir(m.store), 0o755) != nil {
		return
	}
	file, err := os.OpenFile(m.store, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	_, _ = file.Write(out.Bytes())
	file.Close()
}

// vectors gives one vector per text, making at most `limit` new ones
// (none when limit is 0 or less means no limit); a text beyond that with
// none remembered gets nil.
func (m *meaning) vectors(texts []string, limit int) [][]float64 {
	m.load()
	keys := make([][vectorKeySize]byte, len(texts))
	var missingKeys [][vectorKeySize]byte
	var missingTexts []string
	wanted := map[[vectorKeySize]byte]bool{}
	for i, text := range texts {
		keys[i] = m.key(text)
		if _, has := m.memory[keys[i]]; has || wanted[keys[i]] {
			continue
		}
		if limit > 0 && len(missingKeys) >= limit {
			continue
		}
		wanted[keys[i]] = true
		missingKeys = append(missingKeys, keys[i])
		missingTexts = append(missingTexts, text)
	}
	if len(missingTexts) > 0 {
		made := m.embed(missingTexts)
		for i, key := range missingKeys {
			m.memory[key] = made[i]
		}
		m.keep(missingKeys)
	}
	out := make([][]float64, len(texts))
	for i, key := range keys {
		out[i] = m.memory[key]
	}
	return out
}

type nearness struct {
	score    float64
	position int
}

// nearest is (score, position) for every text with a vector, nearest
// first; of two as near, the earlier.
func (m *meaning) nearest(question string, texts []string, limit int) []nearness {
	asked := m.embed([]string{m.queryLabel + question})[0]
	length := vectorLength(asked)
	labelled := make([]string, len(texts))
	for i, text := range texts {
		labelled[i] = m.textLabel + text
	}
	var scored []nearness
	for position, vector := range m.vectors(labelled, limit) {
		if vector == nil || len(vector) != len(asked) {
			continue
		}
		dot := 0.0
		for i := range asked {
			dot += asked[i] * vector[i]
		}
		scored = append(scored, nearness{dot / (length * vectorLength(vector)), position})
	}
	sort.SliceStable(scored, func(i, j int) bool {
		if scored[i].score != scored[j].score {
			return scored[i].score > scored[j].score
		}
		return scored[i].position < scored[j].position
	})
	return scored
}

func vectorLength(vector []float64) float64 {
	total := 0.0
	for _, number := range vector {
		total += number * number
	}
	if total == 0 {
		return 1
	}
	return math.Sqrt(total)
}

// nearTool is a `system "meaning"` tool: how near two texts are, -1 to 1.
func (r *run) nearTool(arguments *Record) Value {
	first, _ := arguments.Fields["a"].(string)
	second, _ := arguments.Fields["b"].(string)
	first, second = pyStrip(first), pyStrip(second)
	if first == "" || second == "" {
		runFail("near needs two texts to compare")
	}
	ranked := r.meaning.nearest(first, []string{second}, 0)
	if len(ranked) == 0 {
		runFail("the embedding model gave nothing to compare")
	}
	rounded, _ := strconv.ParseFloat(strconv.FormatFloat(ranked[0].score, 'f', 4, 64), 64)
	result := newRecord()
	result.set("score", rounded)
	return result
}

// find is a files tool's search by meaning: the lines of the folder
// nearest in meaning to the question, as `path:line: text`.
func (r *run) find(start, query string) []Value {
	folder := r.folder
	if start != "." && start != "" {
		folder = withinFolder(r.folder, start)
	}
	if !isDir(folder) {
		runFail(fmt.Sprintf("%s is not a folder", pyRepr(start)))
	}
	question := pyStrip(query)
	if question == "" {
		runFail("a search by meaning needs a question")
	}
	type row struct {
		name   string
		number int
		line   string
	}
	var rows []row
	deadline := time.Now().Add(maxSearchSeconds * time.Second)
	seen := 0
	pending := []string{folder}
	for len(pending) > 0 && seen < maxSearchFiles && time.Now().Before(deadline) && len(rows) < maxFindLines {
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
			for number, text := range splitLines(universal(string(raw))) {
				line := pyStrip(text)
				if utf8.RuneCountInString(line) >= minFindLine {
					if runes := []rune(line); len(runes) > maxSearchLine {
						line = string(runes[:maxSearchLine])
					}
					rows = append(rows, row{name, number + 1, line})
					if len(rows) >= maxFindLines {
						break
					}
				}
			}
			if len(rows) >= maxFindLines {
				break
			}
		}
	}
	matches := []Value{}
	if len(rows) == 0 {
		return matches
	}
	lines := make([]string, len(rows))
	for i, item := range rows {
		lines[i] = item.line
	}
	for i, ranked := range r.meaning.nearest(question, lines, maxFindNew) {
		if i >= maxFindMatch {
			break
		}
		item := rows[ranked.position]
		matches = append(matches, fmt.Sprintf("%s:%d: %s", item.name, item.number, item.line))
	}
	return matches
}
