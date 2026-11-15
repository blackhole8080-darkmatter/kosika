package main

// Evidence: a model's answer must quote the text it was given, as
// kosika/evidence.py checks it. A thinking step written `with evidence`
// asks the model for quotes beside its answer, and every quote must appear
// in the text the step was given. Matching ignores letter case, runs of
// whitespace, and straight against curly quotation marks; a paraphrase is
// not a quote.

import (
	"fmt"
	"strings"
	"unicode/utf8"

	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
)

// evidenceKey carries the quotes in a model's answer and in a trace.
const evidenceKey = "evidence"

// evidenceKinds is the rule each thinking step's evidence follows.
var evidenceKinds = map[string]string{"extract": "fields", "classify": "quotes", "decide": "quotes_if_true"}

// evidenceSources is the argument that holds the text a step was given.
var evidenceSources = map[string]string{"fields": "text", "quotes": "text", "quotes_if_true": "given"}

var evidenceInstructions = map[string]string{
	"fields": " Also set evidence to an object with one entry per field: a short " +
		"exact quote, copied character for character from the text, that " +
		"states that field's value. Use null as the quote, and leave the " +
		"field empty, when the text does not state it.",
	"quotes": " Also set evidence to a list of one or more short exact quotes, " +
		"copied character for character from the text, that support the label.",
	"quotes_if_true": " Also set evidence to a list of short exact quotes, copied character " +
		"for character from the given text, that support the answer. A true " +
		"answer needs at least one quote.",
}

var quoteMarks = strings.NewReplacer("‘", "'", "’", "'", "“", "\"", "”", "\"", " ", " ")

var caseFolder = cases.Fold()

// normalizeQuoted is Python's normalize: NFKC, plain quotation marks,
// casefold, and runs of whitespace made one space.
func normalizeQuoted(text string) string {
	text = caseFolder.String(quoteMarks.Replace(norm.NFKC.String(text)))
	return strings.Join(strings.Fields(text), " ")
}

// answerSchema is the schema a model is asked to fill: the record plus its
// evidence.
func answerSchema(kind string, output Value, fields []string) Value {
	quote := newRecord()
	quote.set("type", "string")
	evidence := newRecord()
	if kind == "fields" {
		properties := newRecord()
		required := []Value{}
		for _, name := range fields {
			null := newRecord()
			null.set("type", "null")
			either := newRecord()
			either.set("anyOf", []Value{quote, null})
			properties.set(name, either)
			required = append(required, name)
		}
		evidence.set("type", "object")
		evidence.set("properties", properties)
		evidence.set("required", required)
		evidence.set("additionalProperties", false)
	} else {
		evidence.set("type", "array")
		evidence.set("items", quote)
	}
	schema := output.(*Record).copy()
	properties := newRecord()
	if given, ok := schema.Fields["properties"].(*Record); ok {
		properties = given.copy()
	}
	properties.set(evidenceKey, evidence)
	schema.set("properties", properties)
	required := []Value{}
	if given, ok := schema.Fields["required"].([]Value); ok {
		required = append(required, given...)
	}
	schema.set("required", append(required, evidenceKey))
	return schema
}

// schemaFields are the names of a record schema's properties, in order.
func schemaFields(schema Value) []string {
	record, ok := schema.(*Record)
	if !ok {
		return nil
	}
	properties, ok := record.Fields["properties"].(*Record)
	if !ok {
		return nil
	}
	return append([]string{}, properties.Keys...)
}

// splitEvidence separates a model's answer into the record and its quotes.
func splitEvidence(answer *Record) (*Record, Value) {
	result := newRecord()
	for _, key := range answer.Keys {
		if key != evidenceKey {
			result.set(key, answer.Fields[key])
		}
	}
	return result, answer.Fields[evidenceKey]
}

func filledValue(value Value) bool {
	switch v := value.(type) {
	case nil:
		return false
	case string:
		return v != ""
	case []Value:
		return len(v) > 0
	case *Record:
		return len(v.Keys) > 0
	}
	return true
}

func quoteProblem(quote Value, source, what string) string {
	text, ok := quote.(string)
	if !ok {
		return what + " has no quote"
	}
	wanted := strings.Trim(normalizeQuoted(text), "\"'. …")
	if utf8.RuneCountInString(strings.ReplaceAll(wanted, " ", "")) < 2 {
		return fmt.Sprintf("%s has a quote too short to check: %s", what, pyRepr(text))
	}
	if !strings.Contains(source, wanted) {
		return fmt.Sprintf("%s quotes %s, which does not appear in the given text", what, pyRepr(text))
	}
	return ""
}

// evidenceProblem is "" when the evidence backs the answer as its kind
// requires, and else what is wrong, as verify in kosika/evidence.py says.
func evidenceProblem(kind string, arguments map[string]Value, result *Record, evidence Value) string {
	source, known := evidenceSources[kind]
	if !known {
		return fmt.Sprintf("Unknown evidence kind %s", pyRepr(kind))
	}
	given, ok := arguments[source].(string)
	if !ok {
		return "Evidence needs the text the step was given"
	}
	text := normalizeQuoted(given)
	if kind == "fields" {
		quotes, ok := evidence.(*Record)
		if !ok {
			return "Evidence must give a quote for each field"
		}
		for _, name := range result.Keys {
			if filledValue(result.Fields[name]) {
				if problem := quoteProblem(quotes.Fields[name], text, "Field "+pyRepr(name)); problem != "" {
					return problem
				}
			}
		}
		return ""
	}
	quotes, ok := evidence.([]Value)
	if !ok {
		return "Evidence must be a list of quotes"
	}
	for _, quote := range quotes {
		if problem := quoteProblem(quote, text, "The answer"); problem != "" {
			return problem
		}
	}
	if (kind == "quotes" || result.Fields["answer"] == true) && len(quotes) == 0 {
		return "The answer has no quote from the given text"
	}
	return ""
}

// statedQuotes is evidence without its empty entries: a missing quote and
// a null one are the same statement.
func statedQuotes(quotes Value) Value {
	record, ok := quotes.(*Record)
	if !ok {
		return quotes
	}
	stated := newRecord()
	for _, key := range record.Keys {
		if record.Fields[key] != nil {
			stated.set(key, record.Fields[key])
		}
	}
	return stated
}
