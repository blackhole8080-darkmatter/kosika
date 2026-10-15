package main

// Asking a model: the request a step makes, and the providers that answer
// it. A test's scripted answers are one provider; a model server on the
// network, spoken to as kosika/model.py speaks to one (the OpenAI-compatible
// /chat/completions shape, or Ollama's own /api/chat), is the other.

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
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
	ToolCalls     []toolCall // what an assistant's reply asked for
	ToolCallID    string     // the call a tool's reply answers
	Name          string     // the tool that replied
}

// A toolCall is a model asking for one of the tools it was offered.
type toolCall struct {
	ID, Name  string
	Arguments *Record
}

// A modelTool is a tool offered to a model: its name, what it is for, and
// the schema of what it takes.
type modelTool struct {
	Name, Description string
	Schema            Value
}

// A modelRequest is one question to a model. Model is a deployment name
// that overrides the provider's own; the parameters are the model's.
type modelRequest struct {
	Messages   []chatMessage
	Tools      []modelTool
	Model      string
	Decl       *ModelDecl
	JSONObject bool
}

type modelResponse struct {
	Content   string
	Finish    string
	ToolCalls []toolCall
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
		messages[i] = h.message(message)
	}
	payload := map[string]any{"model": model, "messages": messages}
	if len(request.Tools) > 0 {
		var tools []any
		for _, tool := range request.Tools {
			tools = append(tools, map[string]any{"type": "function", "function": map[string]any{
				"name": tool.Name, "description": tool.Description, "parameters": plainJSON(tool.Schema)}})
		}
		payload["tools"] = tools
	}
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
		options["num_ctx"] = ollamaContext(request, capped)
	} else {
		options["max_tokens"] = capped
	}
	if h.protocol == "ollama" && len(options) > 0 {
		payload["options"] = options
	}
	if request.JSONObject && (h.protocol == "ollama" || len(request.Tools) == 0) {
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
		raw, _ := call.(map[string]any)
		function, _ := raw["function"].(map[string]any)
		name, _ := function["name"].(string)
		if name == "" {
			stopProvider("Model tool call lacked a function name")
		}
		var arguments Value = newRecord()
		switch given := function["arguments"].(type) {
		case string:
			parsed, problem := parseStrictJSON(given)
			if problem != "" {
				stopProvider("Model tool call %s had invalid JSON arguments", pyRepr(name))
			}
			arguments = parsed
		case nil:
		default:
			encoded, _ := json.Marshal(given)
			arguments, _ = parseStrictJSON(string(encoded))
		}
		record, ok := arguments.(*Record)
		if !ok {
			stopProvider("Model tool call %s arguments must be an object", pyRepr(name))
		}
		// Each call needs an id of its own, and Ollama does not always give one.
		id, _ := raw["id"].(string)
		if id == "" {
			id = newCallID()
		}
		response.ToolCalls = append(response.ToolCalls, toolCall{id, name, record})
	}
	return response
}

func newCallID() string {
	var bytes [6]byte
	_, _ = rand.Read(bytes[:])
	return hex.EncodeToString(bytes[:])
}

// message is one message as the server's wire format writes it.
func (h *httpProvider) message(message chatMessage) map[string]any {
	payload := map[string]any{"role": message.Role, "content": message.Content}
	if len(message.ToolCalls) > 0 {
		var calls []any
		for _, call := range message.ToolCalls {
			if h.protocol == "ollama" {
				// Ollama wants a call's arguments as an object, not as text.
				calls = append(calls, map[string]any{"function": map[string]any{"name": call.Name, "arguments": plainJSON(call.Arguments)}})
			} else {
				calls = append(calls, map[string]any{"id": call.ID, "type": "function", "function": map[string]any{
					"name": call.Name, "arguments": pyJSONWith(call.Arguments, true, true)}})
			}
		}
		payload["tool_calls"] = calls
	}
	if h.protocol == "ollama" {
		if message.Role == "tool" && message.Name != "" {
			payload["tool_name"] = message.Name
		}
		return payload
	}
	if message.ToolCallID != "" {
		payload["tool_call_id"] = message.ToolCallID
	}
	if message.Name != "" {
		payload["name"] = message.Name
	}
	return payload
}

// plainJSON is a value as encoding/json writes it, whole numbers exact.
func plainJSON(v Value) any {
	return json.RawMessage(pyJSONWith(v, false, true))
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
	if values := c.enumValues(typeName); values != nil {
		return object("type", "string", "enum", stringValues(values))
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

// record is a message as the reference writes it in a trace: asdict of
// its ChatMessage.
func (message chatMessage) record() *Record {
	calls := []Value{}
	for _, call := range message.ToolCalls {
		recorded := newRecord()
		recorded.set("id", call.ID)
		recorded.set("name", call.Name)
		recorded.set("arguments", call.Arguments)
		calls = append(calls, recorded)
	}
	record := newRecord()
	record.set("role", message.Role)
	record.set("content", message.Content)
	record.set("tool_calls", calls)
	record.set("tool_call_id", optionalText(message.ToolCallID))
	record.set("name", optionalText(message.Name))
	return record
}

// ollamaContexts are the context windows Ollama is asked for, smallest
// first: ollama_context in kosika/model.py. Ollama gives a model 4096
// tokens unless told otherwise, and quietly drops the start of a
// conversation that does not fit, which is where an agent's instructions
// are.
var ollamaContexts = []int{8192, 16384, 32768}

// ollamaContext is the smallest window that holds a request, at about three
// characters a token, its answer and a margin.
func ollamaContext(request *modelRequest, answer int) int {
	text := 0
	for _, message := range request.Messages {
		text += len([]rune(message.Content))
		for _, call := range message.ToolCalls {
			arguments := Value(call.Arguments)
			if call.Arguments == nil {
				arguments = newRecord()
			}
			text += len([]rune(call.Name)) + len([]rune(pyJSON(arguments, true)))
		}
	}
	for _, tool := range request.Tools {
		text += len([]rune(tool.Name)) + len([]rune(tool.Description)) + len([]rune(pyJSON(tool.Schema, true)))
	}
	wanted := text/3 + answer + 512
	for _, size := range ollamaContexts {
		if size >= wanted {
			return size
		}
	}
	return ollamaContexts[len(ollamaContexts)-1]
}
