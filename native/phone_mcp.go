package main

// `phone mcp` lets Kosika programs, and any agent that speaks MCP, use a
// paired phone. It is an MCP server on standard input and output with two
// tools: phone_ask reads a sense, phone_do does an action. A program reaches
// it only when the person running the program names it in an environment
// and pins its manifest (`phone mcp --manifest`), as every MCP server is
// reached. Nothing here skips the phone's own yes: each call is one signed,
// sealed request, and the phone's person answers it on the phone.
//
//   environment phone {
//     mcp phone: ["kos", "phone", "mcp"]
//       pinned "phone_manifest.json"
//   }

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
)

const (
	phoneMCPName    = "kosika-phone"
	phoneMCPVersion = "0.1.0"
	// phoneMCPWait is how long a program waits for one call: the relay's
	// own wait, and the phone's person's minute to answer, with room.
	phoneMCPWait = 150
)

func schemaObject(pairs ...any) *Record {
	record := newRecord()
	for i := 0; i < len(pairs); i += 2 {
		record.set(pairs[i].(string), pairs[i+1])
	}
	return record
}

// recordSchema is a schema as the checker makes one for a record whose
// fields have these names and types, in this order.
func recordSchema(fields ...[2]string) *Record {
	properties := newRecord()
	required := []Value{}
	for _, field := range fields {
		var schema Value
		switch field[1] {
		case "Text":
			schema = schemaObject("type", "string")
		case "Bool":
			schema = schemaObject("type", "boolean")
		case "Text[]":
			schema = schemaObject("type", "array", "items", schemaObject("type", "string"))
		}
		properties.set(field[0], schema)
		required = append(required, field[0])
	}
	return schemaObject("type", "object", "properties", properties, "required", required, "additionalProperties", false)
}

// phoneReplySchema is the record every phone tool gives back:
//
//	record PhoneReply { device: Text  status: Text  note: Text  result: Text  simulated: Bool }
//
// status is done, refused or failed, as the phone said it; result is what
// a sense read, as JSON text.
func phoneReplySchema() *Record {
	return recordSchema([2]string{"device", "Text"}, [2]string{"status", "Text"}, [2]string{"note", "Text"},
		[2]string{"result", "Text"}, [2]string{"simulated", "Bool"})
}

type phoneMCPTool struct {
	about         string
	input, output *Record
}

func phoneMCPTools() map[string]phoneMCPTool {
	return map[string]phoneMCPTool{
		"phone_ask": {"Read one sense of a paired phone (" + strings.Join(sortedKeys(phoneSenses), ", ") +
			"). The phone's person is asked first.",
			recordSchema([2]string{"device", "Text"}, [2]string{"sense", "Text"}), phoneReplySchema()},
		"phone_do": {"Do one action on a paired phone (" + strings.Join(sortedKeys(phoneActions), ", ") +
			"). The phone's person is asked yes every time.",
			recordSchema([2]string{"device", "Text"}, [2]string{"action", "Text"}, [2]string{"args", "Text[]"}), phoneReplySchema()},
	}
}

// phoneManifest is the manifest a person pins to let programs use phones.
func phoneManifest(source string) string {
	server := schemaObject("source", source, "protocolVersion", mcpProtocolVersion, "name", phoneMCPName,
		"version", phoneMCPVersion, "waitSeconds", bigInt(phoneMCPWait))
	tools := newRecord()
	defs := phoneMCPTools()
	for _, name := range sortedKeys(defs) {
		tool := defs[name]
		tools.set(name, schemaObject("inputSchema", tool.input, "outputSchema", tool.output,
			"schemaHash", schemaHash(tool.input, tool.output)))
	}
	manifest := schemaObject("manifestVersion", bigInt(1), "server", server, "tools", tools)
	var pretty strings.Builder
	var raw any
	json.Unmarshal([]byte(pyJSONWith(manifest, false, true)), &raw)
	encoder := json.NewEncoder(&pretty)
	encoder.SetIndent("", "  ")
	encoder.Encode(raw)
	return pretty.String()
}

// phoneCall sends one request to a paired phone and waits for its answer.
func phoneCall(device string, body phoneBody) (phoneBody, error) {
	me, err := loadIdentity("")
	if err != nil {
		return phoneBody{}, err
	}
	peer, err := loadPeer(device)
	if err != nil {
		return phoneBody{}, err
	}
	if peer.Role != "node" {
		return phoneBody{}, fmt.Errorf("%s asks this device; it does not take requests from here", peer.Name)
	}
	return phoneSend(me, peer, body)
}

// phoneToolCall turns one tool call into a phone request and its answer
// into a PhoneReply.
func phoneToolCall(name string, arguments map[string]any) (map[string]any, error) {
	text := func(key string) (string, error) {
		value, ok := arguments[key].(string)
		if !ok {
			return "", fmt.Errorf("%s needs %s as text", name, key)
		}
		return value, nil
	}
	device, err := text("device")
	if err != nil {
		return nil, err
	}
	var body phoneBody
	switch name {
	case "phone_ask":
		sense, err := text("sense")
		if err != nil {
			return nil, err
		}
		if _, ok := phoneSenses[sense]; !ok {
			return nil, fmt.Errorf("no sense called %q (there are: %s)", sense, strings.Join(sortedKeys(phoneSenses), ", "))
		}
		body = phoneBody{Kind: "ask", What: sense}
	case "phone_do":
		action, err := text("action")
		if err != nil {
			return nil, err
		}
		words := []string{device, action}
		given, _ := arguments["args"].([]any)
		for _, arg := range given {
			word, ok := arg.(string)
			if !ok {
				return nil, errors.New("phone_do needs args as a list of text")
			}
			words = append(words, word)
		}
		if body, err = phoneDoBody(words); err != nil {
			return nil, err
		}
	default:
		return nil, fmt.Errorf("no phone tool called %q", name)
	}
	reply, err := phoneCall(device, body)
	if err != nil {
		return nil, err
	}
	result := ""
	switch value := reply.Result.(type) {
	case nil:
	case string:
		result = value
	default:
		raw, _ := json.Marshal(value)
		result = string(raw)
	}
	return map[string]any{"device": device, "status": reply.Status, "note": reply.Note,
		"result": result, "simulated": reply.Simulated}, nil
}

// servePhoneMCP answers MCP messages, one JSON object a line, until in ends.
func servePhoneMCP(in io.Reader, out io.Writer, call func(string, map[string]any) (map[string]any, error)) error {
	reply := func(id any, result any, failure map[string]any) {
		message := map[string]any{"jsonrpc": "2.0", "id": id}
		if failure != nil {
			message["error"] = failure
		} else {
			message["result"] = result
		}
		raw, _ := json.Marshal(message)
		out.Write(append(raw, '\n'))
	}
	tools := phoneMCPTools()
	scanner := bufio.NewScanner(in)
	scanner.Buffer(make([]byte, 1<<20), 8<<20)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var request struct {
			ID     any             `json:"id"`
			Method string          `json:"method"`
			Params json.RawMessage `json:"params"`
		}
		if err := json.Unmarshal([]byte(line), &request); err != nil {
			reply(nil, nil, map[string]any{"code": -32700, "message": "not JSON"})
			continue
		}
		if request.ID == nil {
			continue // a notification
		}
		switch request.Method {
		case "initialize":
			reply(request.ID, map[string]any{"protocolVersion": mcpProtocolVersion,
				"capabilities": map[string]any{"tools": map[string]any{}},
				"serverInfo":   map[string]any{"name": phoneMCPName, "version": phoneMCPVersion}}, nil)
		case "ping":
			reply(request.ID, map[string]any{}, nil)
		case "tools/list":
			list := []any{}
			for _, name := range sortedKeys(tools) {
				tool := tools[name]
				list = append(list, map[string]any{"name": name, "description": tool.about,
					"inputSchema":  json.RawMessage(pyJSONWith(tool.input, false, true)),
					"outputSchema": json.RawMessage(pyJSONWith(tool.output, false, true))})
			}
			reply(request.ID, map[string]any{"tools": list}, nil)
		case "tools/call":
			var params struct {
				Name      string         `json:"name"`
				Arguments map[string]any `json:"arguments"`
			}
			json.Unmarshal(request.Params, &params)
			result, err := call(params.Name, params.Arguments)
			if err != nil {
				reply(request.ID, map[string]any{"isError": true,
					"content": []any{map[string]any{"type": "text", "text": err.Error()}}}, nil)
				continue
			}
			raw, _ := json.Marshal(result)
			reply(request.ID, map[string]any{"structuredContent": result,
				"content": []any{map[string]any{"type": "text", "text": string(raw)}}}, nil)
		default:
			reply(request.ID, nil, map[string]any{"code": -32601, "message": "no method " + request.Method})
		}
	}
	return scanner.Err()
}

func phoneMCPCommand(args []string) error {
	if len(args) > 0 && args[0] == "--manifest" {
		source := "phone"
		if len(args) > 1 {
			source = args[1]
		}
		fmt.Print(phoneManifest(source))
		return nil
	}
	if len(args) > 0 {
		return errors.New("phone mcp takes no words, or --manifest [SOURCE]")
	}
	return servePhoneMCP(os.Stdin, os.Stdout, phoneToolCall)
}
