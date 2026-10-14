package main

// Asking a model: the request a step makes, and the providers that answer
// it. A test's scripted answers are one provider; a model server on the
// network, spoken to as kosika/model.py speaks to one (the OpenAI-compatible
// /chat/completions shape, or Ollama's own /api/chat), is the other.

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"math/big"
	"net"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"
)

type chatMessage struct {
	Role, Content string
}

// A modelRequest is one question to a model. Model is a deployment name
// that overrides the provider's own; the parameters are the model's.
type modelRequest struct {
	Messages   []chatMessage
	Model      string
	Decl       *ModelDecl
	JSONObject bool
}

type modelResponse struct {
	Content   string
	Finish    string
	ToolCalls []string // the names of any tools the model asked for
}

// A modelProvider answers requests, or fails the step with runFail.
type modelProvider interface {
	complete(request *modelRequest) *modelResponse
}

// scriptedProvider gives a test's answers in order, as JSON text, as the
// reference's ScriptedModelProvider does.
type scriptedProvider struct{ script *scriptedTool }

func (s *scriptedProvider) complete(*modelRequest) *modelResponse {
	s.script.asked++
	if s.script.asked > len(s.script.answers) {
		runFail("Scripted model provider has no response left")
	}
	return &modelResponse{Content: pyJSON(s.script.answers[s.script.asked-1], false), Finish: "stop"}
}

// httpProvider speaks to a model server. Every request is capped at
// maxOutput tokens, as the reference's ControlledProvider caps it.
type httpProvider struct {
	endpoint, model, protocol, apiKey string
	timeout                           time.Duration
	maxOutput                         int
}

// stopProvider ends the run: a model server that cannot answer is not a
// failure of the work, and no fallback is asked instead.
func stopProvider(format string, args ...any) {
	panic(&budgetError{fmt.Sprintf(format, args...), "provider"})
}

const defaultLocalModelURL = "http://localhost:11434/v1"

func newHTTPProvider(url, model, protocol string, seconds float64, maxOutput int) *httpProvider {
	trimmed := strings.TrimRight(url, "/")
	endpoint := trimmed + "/chat/completions"
	if strings.HasSuffix(trimmed, "/chat/completions") {
		endpoint = url
	}
	if protocol == "ollama" {
		endpoint = trimmed + "/api/chat"
		if strings.HasSuffix(trimmed, "/api/chat") {
			endpoint = trimmed
		}
	}
	return &httpProvider{endpoint, model, protocol, os.Getenv("KOSIKA_MODEL_API_KEY"), time.Duration(seconds * float64(time.Second)), maxOutput}
}

func (h *httpProvider) complete(request *modelRequest) *modelResponse {
	model := h.model
	if request.Model != "" {
		model = request.Model
	}
	messages := make([]map[string]any, len(request.Messages))
	for i, message := range request.Messages {
		messages[i] = map[string]any{"role": message.Role, "content": message.Content}
	}
	payload := map[string]any{"model": model, "messages": messages}
	options := payload
	if h.protocol == "ollama" {
		options = map[string]any{}
		payload["stream"] = false
		payload["keep_alive"] = "30m"
	}
	if decl := request.Decl; decl != nil {
		if decl.Temperature != nil {
			options["temperature"] = *decl.Temperature
		}
		for name, value := range map[string]*float64{"top_p": decl.TopP, "frequency_penalty": decl.FrequencyPenalty, "presence_penalty": decl.PresencePenalty} {
			if value != nil {
				options[name] = *value
			}
		}
		if len(decl.Stop) > 0 {
			options["stop"] = decl.Stop
		}
	}
	capped := h.maxOutput
	if decl := request.Decl; decl != nil && decl.MaxOutputTokens != nil && *decl.MaxOutputTokens < capped {
		capped = *decl.MaxOutputTokens
	}
	if h.protocol == "ollama" {
		options["num_predict"] = capped
	} else {
		options["max_tokens"] = capped
	}
	if h.protocol == "ollama" && len(options) > 0 {
		payload["options"] = options
	}
	if request.JSONObject {
		if h.protocol == "ollama" {
			payload["format"] = "json"
		} else {
			payload["response_format"] = map[string]any{"type": "json_object"}
		}
	}
	body, _ := json.Marshal(payload)
	httpRequest, err := http.NewRequest("POST", h.endpoint, bytes.NewReader(body))
	if err != nil {
		stopProvider("Model provider request failed: %v", err)
	}
	httpRequest.Header.Set("Content-Type", "application/json")
	if h.apiKey != "" {
		httpRequest.Header.Set("Authorization", "Bearer "+h.apiKey)
	}
	answer, err := (&http.Client{Timeout: h.timeout}).Do(httpRequest)
	if err != nil {
		var timeout net.Error
		if errors.As(err, &timeout) && timeout.Timeout() {
			stopProvider("Model did not answer within %s seconds", pyShortFloat(h.timeout.Seconds()))
		}
		stopProvider("Model provider request failed: %v", err)
	}
	defer answer.Body.Close()
	raw, err := io.ReadAll(answer.Body)
	if err != nil {
		stopProvider("Model provider request failed: %v", err)
	}
	if answer.StatusCode >= 400 {
		stopProvider("Model provider returned HTTP %d: %s", answer.StatusCode, raw)
	}
	var decoded map[string]any
	if json.Unmarshal(raw, &decoded) != nil {
		stopProvider("Model provider returned invalid JSON")
	}
	var message map[string]any
	finish := "stop"
	if h.protocol == "ollama" {
		message, _ = decoded["message"].(map[string]any)
		if message == nil {
			stopProvider("Model response did not contain a message")
		}
		if done, ok := decoded["done"].(bool); ok && !done {
			finish = "length"
		}
	} else {
		choices, _ := decoded["choices"].([]any)
		if len(choices) == 0 {
			stopProvider("Model response did not contain choices")
		}
		choice, _ := choices[0].(map[string]any)
		message, _ = choice["message"].(map[string]any)
		if message == nil {
			stopProvider("Model response choice did not contain a message")
		}
		if reason, ok := choice["finish_reason"].(string); ok && reason != "" {
			finish = reason
		}
	}
	response := &modelResponse{Finish: finish}
	if content, ok := message["content"].(string); ok {
		response.Content = content
	} else if message["content"] != nil {
		stopProvider("Model response content must be text or null")
	}
	calls, _ := message["tool_calls"].([]any)
	for _, call := range calls {
		function, _ := call.(map[string]any)["function"].(map[string]any)
		name, _ := function["name"].(string)
		if name == "" {
			stopProvider("Model tool call lacked a function name")
		}
		response.ToolCalls = append(response.ToolCalls, name)
	}
	return response
}

// pyShortFloat writes a number of seconds as Python's {:g} does.
func pyShortFloat(f float64) string {
	// Go's %g, like Python's, writes no trailing zeros: 60, 0.5, 1e+06.
	return fmt.Sprintf("%.6g", f)
}

// parseStrictJSON reads a model's answer as strict_json does: duplicate
// keys and numbers that are not finite are refused. Whole numbers stay
// whole, and an object keeps the order of its keys.
func parseStrictJSON(text string) (value Value, problem string) {
	decoder := json.NewDecoder(strings.NewReader(text))
	decoder.UseNumber()
	var read func() Value
	read = func() Value {
		token, err := decoder.Token()
		if err != nil {
			panic(fmt.Sprintf("Expecting value: %v", err))
		}
		switch t := token.(type) {
		case json.Delim:
			if t == '[' {
				items := []Value{}
				for decoder.More() {
					items = append(items, read())
				}
				decoder.Token()
				return items
			}
			record := newRecord()
			for decoder.More() {
				key, _ := decoder.Token()
				name := key.(string)
				if _, taken := record.Fields[name]; taken {
					panic(fmt.Sprintf("Duplicate JSON key %s", pyRepr(name)))
				}
				record.set(name, read())
			}
			decoder.Token()
			return record
		case json.Number:
			text := t.String()
			if !strings.ContainsAny(text, ".eE") {
				n, _ := new(big.Int).SetString(text, 10)
				return n
			}
			f, err := t.Float64()
			if err != nil || math.IsInf(f, 0) {
				panic("Out of range float values are not JSON compliant")
			}
			return f
		case string, bool, nil:
			return t
		}
		panic("invalid JSON")
	}
	defer func() {
		if caught := recover(); caught != nil {
			text, ok := caught.(string)
			if !ok {
				panic(caught)
			}
			value, problem = nil, text
		}
	}()
	value = read()
	if decoder.More() {
		return nil, "Extra data"
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, "Extra data"
	}
	return value, ""
}

// pyJSON writes a value as Python's json.dumps does: ", " and ": " between
// parts, text outside ASCII escaped, and keys sorted when asked.
func pyJSON(v Value, sortKeys bool) string { return pyJSONWith(v, sortKeys, false) }

// pyJSONWith is pyJSON, written compactly (separators=(",", ":")) when
// asked.
func pyJSONWith(v Value, sortKeys, compact bool) string {
	comma, colon := ", ", ": "
	if compact {
		comma, colon = ",", ":"
	}
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
			out.WriteString(pyNumber(x))
		case string:
			out.WriteString(pyJSONString(x))
		case []Value:
			out.WriteString("[")
			for i, item := range x {
				if i > 0 {
					out.WriteString(comma)
				}
				write(item)
			}
			out.WriteString("]")
		case *Record:
			keys := append([]string{}, x.Keys...)
			if sortKeys {
				sort.Strings(keys)
			}
			out.WriteString("{")
			for i, key := range keys {
				if i > 0 {
					out.WriteString(comma)
				}
				out.WriteString(pyJSONString(key))
				out.WriteString(colon)
				write(x.Fields[key])
			}
			out.WriteString("}")
		default:
			out.WriteString(`"<function>"`)
		}
	}
	write(v)
	return out.String()
}

// pyReprAny writes a value as Python's repr does, as `kosika run` prints a
// workflow's result.
func pyReprAny(v Value) string {
	switch x := v.(type) {
	case nil:
		return "None"
	case bool:
		if x {
			return "True"
		}
		return "False"
	case string:
		return pyRepr(x)
	case []Value:
		parts := make([]string, len(x))
		for i, item := range x {
			parts[i] = pyReprAny(item)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case *Record:
		parts := make([]string, len(x.Keys))
		for i, key := range x.Keys {
			parts[i] = pyRepr(key) + ": " + pyReprAny(x.Fields[key])
		}
		return "{" + strings.Join(parts, ", ") + "}"
	case *big.Int, float64:
		return pyNumber(x)
	}
	return "<function>"
}

// schemaFor is the JSON schema the reference makes from a type, built as a
// record so that it is written in its keys' order: schema_for_type.
func (c *checker) schemaFor(typeName string) Value {
	object := func(pairs ...any) *Record {
		record := newRecord()
		for i := 0; i < len(pairs); i += 2 {
			record.set(pairs[i].(string), pairs[i+1])
		}
		return record
	}
	if isFunctionType(typeName) {
		return object("type", "object",
			"properties", object("$function", object("type", "string"), "$type", object("const", typeName), "$captures", object("type", "object")),
			"required", []Value{"$function", "$type", "$captures"}, "additionalProperties", false)
	}
	primitive := map[string]string{"Text": "string", "Number": "number", "Bool": "boolean", "Null": "null"}
	if kind, ok := primitive[typeName]; ok {
		return object("type", kind)
	}
	switch {
	case strings.HasSuffix(typeName, "?"):
		return object("anyOf", []Value{c.schemaFor(typeName[:len(typeName)-1]), object("type", "null")})
	case strings.HasSuffix(typeName, "{}"):
		return object("type", "object", "additionalProperties", c.schemaFor(typeName[:len(typeName)-2]))
	case strings.HasSuffix(typeName, "[]"):
		return object("type", "array", "items", c.schemaFor(typeName[:len(typeName)-2]))
	}
	properties := newRecord()
	var required []Value
	for _, field := range c.records[typeName].Fields {
		properties.set(field.Name, c.schemaFor(field.Type))
		required = append(required, field.Name)
	}
	if required == nil {
		required = []Value{}
	}
	return object("type", "object", "properties", properties, "required", required, "additionalProperties", false)
}

// thinkingInstructions are the reference's words for each thinking step.
var thinkingInstructions = map[string]string{
	"classify":  "Classify the text. Set label to exactly one of the given choices, copied character for character, and confidence to your confidence from 0 to 1.",
	"extract":   "Extract the requested fields from the text. Use only what the text states; do not invent values.",
	"summarize": "Summarize the text briefly and faithfully, adding nothing.",
	"decide":    "Answer the question using only the given text. Set answer to true or false, confidence to your confidence from 0 to 1, and reason to one sentence that cites the given text.",
}
