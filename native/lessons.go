package main

// What a program has learned, as kosika/lessons.py keeps it: the answers a
// step marked `learn` was given and accepted, with what it was asked, so it
// can answer again without its model. Lessons live in the memory folder,
// one JSON file for each step, which either engine reads and writes.

import (
	"crypto/sha256"
	"encoding/hex"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

const (
	maxLessons      = 500
	closeEnough     = 0.88
	voteExamples    = 6
	voteNeighbours  = 7
	voteAgreement   = 0.8
	voteNearestLeft = 0.4
)

// wordsOf are a text's words as Python's `[^\W_]+` finds them in its lower
// case: runs of letters and numbers.
func wordsOf(text string) []string {
	return strings.FieldsFunc(strings.ToLower(text), func(r rune) bool {
		return !(unicode.IsLetter(r) || unicode.IsNumber(r))
	})
}

func likeness(a, b map[string]bool) float64 {
	if len(a) == 0 && len(b) == 0 {
		return 1
	}
	shared := 0
	for word := range a {
		if b[word] {
			shared++
		}
	}
	return float64(shared) / float64(len(a)+len(b)-shared)
}

// round3 is Python's round(x, 3).
func round3(x float64) float64 {
	rounded, _ := strconv.ParseFloat(strconv.FormatFloat(x, 'f', 3, 64), 64)
	return rounded
}

// unicodeJSON is Python's json.dumps(..., ensure_ascii=False): the text
// itself rather than \u escapes, control characters apart.
func unicodeJSON(v Value, sortKeys, compact bool) string {
	text := pyJSONWith(v, sortKeys, compact)
	var out strings.Builder
	for i := 0; i < len(text); i++ {
		if text[i] == '\\' && i+1 < len(text) && text[i+1] == '\\' {
			out.WriteString(`\\`)
			i++
			continue
		}
		if text[i] == '\\' && i+5 < len(text) && text[i+1] == 'u' {
			code, err := strconv.ParseUint(text[i+2:i+6], 16, 32)
			if err == nil && code >= 0x7f {
				r := rune(code)
				used := 6
				if code >= 0xd800 && code < 0xdc00 && i+11 < len(text) && text[i+6] == '\\' && text[i+7] == 'u' {
					if low, err := strconv.ParseUint(text[i+8:i+12], 16, 32); err == nil && low >= 0xdc00 && low < 0xe000 {
						r = rune(0x10000 + (code-0xd800)<<10 + (low - 0xdc00))
						used = 12
					}
				}
				out.WriteRune(r)
				i += used - 1
				continue
			}
		}
		out.WriteByte(text[i])
	}
	return out.String()
}

// A lessonStore keeps the lessons of every learning step of one program.
type lessonStore struct {
	folder string
	loaded map[string][]*Record
}

func newLessonStore(folder string) *lessonStore {
	return &lessonStore{folder: folder, loaded: map[string][]*Record{}}
}

var unsafeLessonName = regexp.MustCompile(`[^A-Za-z0-9_.-]+`)

func (s *lessonStore) path(step string) string {
	safe := unsafeLessonName.ReplaceAllString(step, "_")
	if utf8.RuneCountInString(safe) > 80 {
		safe = string([]rune(safe)[:80])
	}
	digest := sha256.Sum256([]byte(step))
	return filepath.Join(s.folder, "lessons", safe+"-"+hex.EncodeToString(digest[:])[:10]+".json")
}

func (s *lessonStore) book(step string) []*Record {
	if kept, ok := s.loaded[step]; ok {
		return kept
	}
	var kept []*Record
	if data, err := os.ReadFile(s.path(step)); err == nil {
		if parsed, problem := parseStrictJSON(string(data)); problem == "" {
			if items, ok := parsed.([]Value); ok {
				for _, item := range items {
					if record, ok := item.(*Record); ok {
						kept = append(kept, record)
					}
				}
			}
		}
	}
	s.loaded[step] = kept
	return kept
}

// A recalled answer is what was learned for a question, and how it was
// found: the same words, close words, or examples that agree.
type recalled struct {
	answer   Value
	how      string
	likeness float64
	lessons  int
}

func withoutConfidence(answer Value) Value {
	record, ok := answer.(*Record)
	if !ok {
		return answer
	}
	out := newRecord()
	for _, key := range record.Keys {
		if key != "confidence" {
			out.set(key, record.Fields[key])
		}
	}
	return out
}

func (s *lessonStore) recall(step, question string, voting bool) *recalled {
	asked := wordsOf(question)
	plain := strings.Join(asked, " ")
	book := s.book(step)
	if len(book) == 0 {
		return nil
	}
	for i := len(book) - 1; i >= 0; i-- {
		if book[i].Fields["plain"] == plain {
			return &recalled{book[i].Fields["answer"], "same", 1.0, len(book)}
		}
	}
	mine := map[string]bool{}
	for _, word := range asked {
		mine[word] = true
	}
	type ranked struct {
		score float64
		index int
		item  *Record
	}
	var order []ranked
	for index, item := range book {
		text, _ := item.Fields["plain"].(string)
		theirs := map[string]bool{}
		for _, word := range strings.Fields(text) {
			theirs[word] = true
		}
		order = append(order, ranked{likeness(mine, theirs), index, item})
	}
	sort.SliceStable(order, func(i, j int) bool {
		if order[i].score != order[j].score {
			return order[i].score > order[j].score
		}
		return order[i].index > order[j].index
	})
	nearest := order[0]
	if nearest.score >= closeEnough && len(mine) >= 3 {
		return &recalled{nearest.item.Fields["answer"], "close", round3(nearest.score), len(book)}
	}
	if !voting || len(book) < voteExamples || nearest.score < voteNearestLeft {
		return nil
	}
	votes := map[string]float64{}
	answers := map[string]Value{}
	var keys []string
	weight := 0.0
	for i, found := range order {
		if i >= voteNeighbours {
			break
		}
		if found.score <= 0 {
			continue
		}
		answer := found.item.Fields["answer"]
		key := unicodeJSON(withoutConfidence(answer), true, true)
		if _, seen := votes[key]; !seen {
			keys = append(keys, key)
		}
		votes[key] += found.score
		answers[key] = answer
		weight += found.score
	}
	if len(votes) == 0 || weight == 0 {
		return nil
	}
	winner := keys[0]
	for _, key := range keys[1:] {
		if votes[key] > votes[winner] {
			winner = key
		}
	}
	agreement := votes[winner] / weight
	if agreement < voteAgreement {
		return nil
	}
	answer := answers[winner]
	if record, ok := answer.(*Record); ok {
		record = record.copy()
		if _, has := record.Fields["confidence"]; has {
			record.set("confidence", round3(agreement))
		}
		answer = record
	}
	return &recalled{answer, "examples", round3(nearest.score), len(book)}
}

func clipped(text string, most int) string {
	if utf8.RuneCountInString(text) <= most {
		return text
	}
	return string([]rune(text)[:most])
}

// keep keeps a model's accepted answer to a question.
func (s *lessonStore) keep(step, question string, answer Value) {
	plain := strings.Join(wordsOf(question), " ")
	if plain == "" {
		return
	}
	var book []*Record
	for _, item := range s.book(step) {
		if item.Fields["plain"] != plain {
			book = append(book, item)
		}
	}
	lesson := newRecord()
	lesson.set("question", clipped(question, 2000))
	lesson.set("plain", clipped(plain, 2000))
	lesson.set("answer", answer)
	lesson.set("at", bigInt(int(math.Round(float64(time.Now().UnixNano())/1e9))))
	book = append(book, lesson)
	if len(book) > maxLessons {
		book = book[len(book)-maxLessons:]
	}
	s.loaded[step] = book
	path := s.path(step)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return
	}
	items := make([]Value, len(book))
	for i, item := range book {
		items[i] = item
	}
	spare := strings.TrimSuffix(path, ".json") + ".tmp"
	if os.WriteFile(spare, []byte(unicodeJSON(items, false, false)), 0o644) == nil {
		os.Rename(spare, path)
	}
}

// lessonName names one step's lessons, kept apart from any other step's
// and from those of an earlier version of its model: a model declared
// differently starts learning again. Both engines make the same name from
// the declaration written out in one fixed form.
func lessonName(workflow, step string, model *ModelDecl) string {
	declared := newRecord()
	text := func(value *string) Value {
		if value == nil {
			return nil
		}
		return *value
	}
	number := func(value *float64) Value {
		if value == nil {
			return nil
		}
		return lessonNumber(*value)
	}
	orNone := func(value string) Value {
		if value == "" {
			return nil
		}
		return value
	}
	declared.set("name", model.Name)
	declared.set("input", model.Input)
	declared.set("output", model.Output)
	declared.set("deployment", text(model.Deployment))
	declared.set("provider", orNone(model.Provider))
	declared.set("verb", orNone(model.Verb))
	declared.set("evidence", orNone(model.Evidence))
	declared.set("plain", model.Plain)
	declared.set("temperature", number(model.Temperature))
	if model.MaxOutputTokens != nil {
		declared.set("max_output_tokens", bigInt(*model.MaxOutputTokens))
	} else {
		declared.set("max_output_tokens", nil)
	}
	declared.set("top_p", number(model.TopP))
	declared.set("frequency_penalty", number(model.FrequencyPenalty))
	declared.set("presence_penalty", number(model.PresencePenalty))
	stops := []Value{}
	for _, stop := range model.Stop {
		stops = append(stops, stop)
	}
	declared.set("stop", stops)
	digest := sha256.Sum256([]byte(unicodeJSON(declared, true, true)))
	return workflow + "." + step + "." + model.Name + "." + hex.EncodeToString(digest[:])[:10]
}

// lessonNumber writes a declared number as both engines agree to: whole
// numbers without a point, others as their shortest decimal.
func lessonNumber(value float64) Value {
	if value == math.Trunc(value) && math.Abs(value) < 1e15 {
		return bigInt(int(value))
	}
	return value
}

// questionOf is what a model step is asked, as words: the text it is
// given, without field names, and without lists such as the choices of a
// classify, which are the same every time.
func questionOf(arguments map[string]Value) string {
	var found []string
	var collect func(Value)
	collect = func(value Value) {
		switch v := value.(type) {
		case string:
			found = append(found, v)
		case *Record:
			keys := append([]string{}, v.Keys...)
			sort.Strings(keys)
			for _, key := range keys {
				collect(v.Fields[key])
			}
		}
	}
	names := make([]string, 0, len(arguments))
	for name := range arguments {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		collect(arguments[name])
	}
	return strings.Join(found, "\n")
}
