package main

// Reaching a tool on an MCP server: a program started on this machine and
// spoken to in newline-delimited JSON-RPC over its standard input and
// output, as kosika/mcp_stdio.py speaks to it. A server is reached only
// when the person running the program names it and pins what it offers in
// a manifest (kosika/manifest.py); the manifest is checked against the
// program's records and against the server before any tool is called.

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math/big"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const mcpProtocolVersion = "2025-11-25"

// A mcpServer is how to start the server that answers for one source.
type mcpServer struct {
	command  []string
	manifest *mcpManifest
}

type pinnedTool struct {
	input, output Value
}

type mcpManifest struct {
	source, protocol, name, version string
	tools                           map[string]pinnedTool
}

// canonicalJSON is json.dumps(value, sort_keys=True, separators=(",", ":")).
func canonicalJSON(v Value) string { return pyJSONWith(v, true, true) }

func schemaHash(input, output Value) string {
	payload := newRecord()
	payload.set("inputSchema", input)
	payload.set("outputSchema", output)
	sum := sha256.Sum256([]byte(canonicalJSON(payload)))
	return hex.EncodeToString(sum[:])
}

// loadManifest reads a pinned manifest, as load_manifest does.
func loadManifest(path string) (*mcpManifest, string) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Sprintf("Could not read MCP manifest %s: %v", path, err)
	}
	value, problem := parseStrictJSON(strings.TrimPrefix(string(raw), string(rune(0xFEFF))))
	if problem != "" {
		return nil, fmt.Sprintf("Could not read MCP manifest %s: %s", path, problem)
	}
	data, ok := value.(*Record)
	if !ok {
		return nil, "MCP manifest must be a JSON object"
	}
	server, serverOK := data.Fields["server"].(*Record)
	tools, toolsOK := data.Fields["tools"].(*Record)
	if !serverOK || !toolsOK {
		return nil, "MCP manifest requires server and tools objects"
	}
	var missing []string
	for _, key := range []string{"name", "protocolVersion", "source", "version"} {
		if _, has := server.Fields[key]; !has {
			missing = append(missing, key)
		}
	}
	if len(missing) > 0 {
		return nil, fmt.Sprintf("MCP manifest server is missing %s", pyList(missing))
	}
	manifest := &mcpManifest{
		source:   showText(server.Fields["source"]),
		protocol: showText(server.Fields["protocolVersion"]),
		name:     showText(server.Fields["name"]),
		version:  showText(server.Fields["version"]),
		tools:    map[string]pinnedTool{},
	}
	for _, name := range tools.Keys {
		tool, ok := tools.Fields[name].(*Record)
		if !ok {
			return nil, fmt.Sprintf("Manifest tool %s must be an object", pyRepr(name))
		}
		input, inputOK := tool.Fields["inputSchema"].(*Record)
		output, outputOK := tool.Fields["outputSchema"].(*Record)
		pinned, hashOK := tool.Fields["schemaHash"].(string)
		if !inputOK || !outputOK || !hashOK {
			return nil, fmt.Sprintf("Manifest tool %s requires inputSchema, outputSchema, and schemaHash", pyRepr(name))
		}
		if schemaHash(input, output) != pinned {
			return nil, fmt.Sprintf("Manifest tool %s has an invalid schemaHash", pyRepr(name))
		}
		manifest.tools[name] = pinnedTool{input, output}
	}
	return manifest, ""
}

// serverCommand is how a server named on the command line is started: a
// Python file with the Python on this machine, as the reference starts it
// with its own; anything else as it is.
func serverCommand(path string) []string {
	if strings.EqualFold(filepath.Ext(path), ".py") {
		for _, name := range []string{"python", "python3", "py"} {
			if found, err := exec.LookPath(name); err == nil {
				return []string{found, path}
			}
		}
	}
	return []string{path}
}

// mcpClient is one started server.
type mcpClient struct {
	process    *exec.Cmd
	stdin      *bufio.Writer
	messages   chan any
	nextID     int
	timeout    time.Duration
	negotiated string
	serverInfo *Record
}

type mcpTool struct {
	name          string
	input, output Value
}

func mcpFail(format string, args ...any) { runFail(fmt.Sprintf(format, args...)) }

func startMCP(command []string) *mcpClient {
	process := exec.Command(command[0], command[1:]...)
	stdin, _ := process.StdinPipe()
	stdout, _ := process.StdoutPipe()
	if err := process.Start(); err != nil {
		mcpFail("MCP server could not be started: %v", err)
	}
	client := &mcpClient{process: process, stdin: bufio.NewWriter(stdin), messages: make(chan any, 64), nextID: 1, timeout: 10 * time.Second}
	go func() {
		scanner := bufio.NewScanner(stdout)
		scanner.Buffer(make([]byte, 1<<20), 64<<20)
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" {
				continue
			}
			value, problem := parseStrictJSON(line)
			if problem != "" {
				client.messages <- "Invalid JSON from MCP stdout: " + problem
				continue
			}
			if record, ok := value.(*Record); ok {
				client.messages <- record
			} else {
				client.messages <- "MCP message must be a JSON object"
			}
		}
		client.messages <- "MCP server closed stdout"
	}()
	params := newRecord()
	params.set("protocolVersion", mcpProtocolVersion)
	params.set("capabilities", newRecord())
	info := newRecord()
	info.set("name", "kosika")
	info.set("version", "0.1.0")
	params.set("clientInfo", info)
	result, ok := client.request("initialize", params).Fields["result"].(*Record)
	if !ok {
		client.close()
		mcpFail("initialize response did not contain a result object")
	}
	client.negotiated, _ = result.Fields["protocolVersion"].(string)
	client.serverInfo, _ = result.Fields["serverInfo"].(*Record)
	if client.serverInfo == nil {
		client.serverInfo = newRecord()
	}
	client.send(map[string]Value{"method": "notifications/initialized"})
	return client
}

func (c *mcpClient) send(fields map[string]Value) {
	message := newRecord()
	message.set("jsonrpc", "2.0")
	for _, key := range []string{"id", "method", "params"} {
		if value, ok := fields[key]; ok {
			message.set(key, value)
		}
	}
	c.stdin.WriteString(pyJSONWith(message, false, true) + "\n")
	c.stdin.Flush()
}

func (c *mcpClient) request(method string, params *Record) *Record {
	id := c.nextID
	c.nextID++
	c.send(map[string]Value{"id": big.NewInt(int64(id)), "method": method, "params": params})
	deadline := time.After(c.timeout)
	for {
		select {
		case incoming := <-c.messages:
			if problem, ok := incoming.(string); ok {
				mcpFail("%s", problem)
			}
			message := incoming.(*Record)
			if got, ok := message.Fields["id"].(*big.Int); !ok || got.Int64() != int64(id) {
				continue
			}
			if failure, has := message.Fields["error"]; has {
				mcpFail("MCP %s failed: %s", method, pyReprAny(failure))
			}
			return message
		case <-deadline:
			panic(&permissionError{fmt.Sprintf("Timed out waiting for MCP response to %s", pyRepr(method))})
		}
	}
}

func (c *mcpClient) listTools() []mcpTool {
	result, _ := c.request("tools/list", newRecord()).Fields["result"].(*Record)
	var items []Value
	if result != nil {
		items, _ = result.Fields["tools"].([]Value)
	}
	if result == nil || items == nil {
		mcpFail("tools/list response did not contain a tools list")
	}
	var tools []mcpTool
	for _, item := range items {
		record, ok := item.(*Record)
		name, named := "", false
		if ok {
			name, named = record.Fields["name"].(string)
		}
		if !named {
			mcpFail("tools/list returned an invalid tool definition")
		}
		input, has := record.Fields["inputSchema"]
		if !has {
			input = newRecord()
		}
		output, has := record.Fields["outputSchema"]
		if !has {
			output = newRecord()
		}
		tools = append(tools, mcpTool{name, input, output})
	}
	return tools
}

// verify holds the server to its manifest: verify_server.
func (c *mcpClient) verify(manifest *mcpManifest) {
	if c.negotiated != manifest.protocol {
		mcpFail("MCP protocol mismatch: manifest pins %s, server negotiated %s", pyRepr(manifest.protocol), pyRepr(c.negotiated))
	}
	name, _ := c.serverInfo.Fields["name"].(string)
	if name != manifest.name {
		mcpFail("MCP server identity mismatch: expected %s, got %s", pyRepr(manifest.name), pyReprAny(c.serverInfo.Fields["name"]))
	}
	version, _ := c.serverInfo.Fields["version"].(string)
	if version != manifest.version {
		mcpFail("MCP server version mismatch: expected %s, got %s", pyRepr(manifest.version), pyReprAny(c.serverInfo.Fields["version"]))
	}
	advertised := map[string]mcpTool{}
	for _, tool := range c.listTools() {
		advertised[tool.name] = tool
	}
	var unexpected []string
	for name := range advertised {
		if _, pinned := manifest.tools[name]; !pinned {
			unexpected = append(unexpected, name)
		}
	}
	if len(unexpected) > 0 {
		sort.Strings(unexpected)
		mcpFail("MCP server advertised unpinned tools: %s", pyList(unexpected))
	}
	for _, name := range sortedKeys(manifest.tools) {
		pinned := manifest.tools[name]
		actual, ok := advertised[name]
		if !ok {
			mcpFail("Pinned MCP tool %s is not advertised by the server", pyRepr(name))
		}
		if !valueEqual(actual.input, pinned.input) {
			mcpFail("Input schema mismatch for pinned MCP tool %s", pyRepr(name))
		}
		if record, ok := actual.output.(*Record); (!ok || len(record.Keys) > 0) && !valueEqual(actual.output, pinned.output) {
			mcpFail("Output schema mismatch for pinned MCP tool %s", pyRepr(name))
		}
	}
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for key := range m {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func (c *mcpClient) require(name string) {
	for _, tool := range c.listTools() {
		if tool.name == name {
			return
		}
	}
	mcpFail("Declared MCP tool %s was not advertised by the server", pyRepr(name))
}

// call calls a tool and gives back what it answered: call_tool.
func (c *mcpClient) call(name string, arguments *Record, run *run, outputType string) Value {
	c.require(name)
	params := newRecord()
	params.set("name", name)
	params.set("arguments", arguments)
	result, ok := c.request("tools/call", params).Fields["result"].(*Record)
	if !ok {
		mcpFail("tools/call response did not contain a result object")
	}
	if failed, _ := result.Fields["isError"].(bool); failed {
		mcpFail("MCP tool %s returned an error: %s", pyRepr(name), pyReprAny(result.Fields["content"]))
	}
	var value Value
	if structured, has := result.Fields["structuredContent"]; has {
		value = structured
	} else {
		content, _ := result.Fields["content"].([]Value)
		value = content
		if len(content) == 1 {
			if part, ok := content[0].(*Record); ok && part.Fields["type"] == "text" {
				text, _ := part.Fields["text"].(string)
				if parsed, problem := parseStrictJSON(text); problem == "" {
					value = parsed
				} else {
					value = text
				}
			}
		}
	}
	if problem := run.problem(value, outputType, "$"); problem != "" {
		mcpFail("Invalid result from MCP tool %s: %s", pyRepr(name), problem)
	}
	return value
}

func (c *mcpClient) close() {
	if c.process.Process == nil {
		return
	}
	if closer, ok := c.process.Stdin.(interface{ Close() error }); ok {
		closer.Close()
	}
	done := make(chan struct{})
	go func() { c.process.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		c.process.Process.Kill()
		<-done
	}
}

// callDeclaredTool reaches a tool through its pinned server, as the
// reference's _call_declared_tool does: the manifest must pin the tool,
// and with exactly the schemas the program's records make.
func (r *run) callDeclaredTool(tool *ToolDecl, arguments *Record) Value {
	server := r.servers[tool.Source]
	manifest := server.manifest
	if manifest.source != tool.Source {
		mcpFail("Pinned manifest source %s does not match tool source %s", pyRepr(manifest.source), pyRepr(tool.Source))
	}
	pinned, ok := manifest.tools[tool.Operation]
	if !ok {
		mcpFail("No pinned manifest entry for MCP tool %s", pyRepr(tool.Operation))
	}
	if !valueEqual(pinned.input, r.inputSchema(tool)) {
		mcpFail("Pinned input schema for %s does not match Kosika record %s", pyRepr(tool.Operation), pyReprAny(optionalText(tool.Input)))
	}
	if !valueEqual(pinned.output, r.checker.schemaFor(tool.Output)) {
		mcpFail("Pinned output schema for %s does not match Kosika record %s", pyRepr(tool.Operation), pyRepr(tool.Output))
	}
	client := startMCP(server.command)
	defer client.close()
	client.verify(manifest)
	// The reference asks for the tool by name before it calls it, and the
	// call asks again.
	client.require(tool.Operation)
	return client.call(tool.Operation, arguments, r, tool.Output)
}

func optionalText(text string) Value {
	if text == "" {
		return nil
	}
	return text
}

// inputSchema is schema_for_tool_input: a tool that takes nothing takes an
// empty object.
func (r *run) inputSchema(tool *ToolDecl) Value {
	if tool.Input != "" {
		return r.checker.schemaFor(tool.Input)
	}
	schema := newRecord()
	schema.set("type", "object")
	schema.set("properties", newRecord())
	schema.set("additionalProperties", false)
	return schema
}
