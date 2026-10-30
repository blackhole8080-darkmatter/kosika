# Kosika Examples Guide

A comprehensive guide to all example programs demonstrating Kosika's capabilities.

## Quick Start

Run any example:
```bash
kosika check examples/hello_world.kos
kosika run examples/hello_world.kos --input examples/person.json --model-fixture examples/responses.json
```

## Example Categories

### Beginner (Start Here)

#### hello_world.kos
**Purpose:** Simplest complete Kosika program - a typed greeting
**Demonstrates:**
- Record declarations
- Pure functions
- Workflows with typed input/output
- Basic execution and tracing

**Run:**
```bash
kosika run examples/hello_world.kos --trace hello_world.jsonl
```

#### typed_input.kos
**Purpose:** Typed input/output with validation
**Demonstrates:**
- Record schemas
- Type checking on input
- Pure computation functions
- Workflow output validation

**Run:**
```bash
kosika run examples/typed_input.kos --input examples/typed_input.json --trace typed_input.jsonl
```

#### local_bindings.kos
**Purpose:** Local variable bindings and mutations
**Demonstrates:**
- `let` (immutable) and `var` (mutable) bindings
- Assignment and reassignment
- Scope rules

**Run:**
```bash
kosika run examples/local_bindings.kos
```

### Intermediate

#### closures.kos
**Purpose:** Nested functions and closures
**Demonstrates:**
- Nested function declarations
- Lexical closures
- Function values and `ref` references
- Higher-order functions

**Run:**
```bash
kosika run examples/closures.kos --trace closures.jsonl
```

#### recursion_match.kos
**Demonstrates:**
- Bounded recursion with `recurse N`
- Pattern matching on enum values
- Complex conditional logic

**Run:**
```bash
kosika run examples/recursion_match.kos --trace recursion.jsonl
```

#### composed/main.kos
**Purpose:** Multi-file programs with imports
**Demonstrates:**
- Module imports
- Explicit exports
- Type checking across module boundaries
- Shared types

**Run:**
```bash
kosika run examples/composed/main.kos --input examples/composed/inputs.json --model-fixture examples/composed/responses.json
```

### Advanced

#### multi_agent.kos
**Purpose:** Multi-agent workflow
**Demonstrates:**
- Multiple agent declarations
- Agent communication
- Shared context
- Bounded agent steps

#### knowledge.kos
**Purpose:** Knowledge representation
**Demonstrates:**
- Knowledge graphs
- Evidence tracking
- Assumption management

#### learning_agent.kos
**Purpose:** Adaptive learning agents
**Demonstrates:**
- Learning from feedback
- Memory management
- Policy updates

#### skills.kos
**Purpose:** Skill-based agents
**Demonstrates:**
- Skill declarations
- Skill invocation
- Dynamic capability selection

### Specialized

#### algorithms.kos
**Purpose:** Mathematical algorithms
**Demonstrstrates:**
- Number operations
- Built-in math methods
- Algorithmic patterns

#### discrete_maths.kos
**Purpose:** Discrete mathematics
**Demonstrstrates:**
- Set operations
- Combinatorics
- Graph algorithms

#### tensors_demo.kos
**Purpose:** Tensor operations (experimental)
**Demonstrates:**
- Tensor creation
- Tensor operations
- Matrix math (experimental feature)

#### choices.kos
**Purpose:** Union types and choices
**Demonstrates:**
- Choice declarations
- Pattern matching on choices
- Alternative extraction

### Motion & Animation

#### motion/
**Directory:** Contains motion/animation examples
**Demonstrates:**
- Scene description
- Animation definitions
- Motion choreography
- Visual composition

**Run:**
```bash
kosika motion examples/motion/logo.kos --input ask.json --out logo.html
```

#### path_motion/
**Directory:** Path-based motion examples
**Demonstrates:**
- Object movement along paths
- Bezier curves
- Trajectory planning

### Research & Analysis

#### research_agent.kos
**Purpose:** Research with document search
**Demonstrstrates:**
- Document retrieval
- Search across knowledge base
- Evidence synthesis

#### research_assistant.kos
**Purpose:** Research assistance
**Demonstrates:**
- Question answering
- Knowledge extraction
- Structured output

#### document_assistant.kos
**Purpose:** Document processing
**Demonstrates:**
- Document analysis
- Information extraction
- Text processing

### MCP Integration

#### mcp_host_server.py
**Purpose:** MCP server host example
**Demonstrstrates:**
- MCP server integration
- Tool registration
- Protocol handling

#### agent_factory.kos
**Purpose:** Dynamic agent creation
**Demonstrates:**
- Agent composition
- Factory pattern
- Dynamic capabilities

#### approval_mutation.kos
**Purpose:** Approval-controlled changes
**Demonstrates:**
- Approval workflow
- Change validation
- Safety gates

### Testing

#### test_host.kos
**Purpose:** Testing infrastructure
**Demonstrstrates:**
- Test definitions
- Assertions
- Test execution

#### test_host_live.kos
**Purpose:** Live testing with real tools
**Demonstrates:**
- Tool testing
- Integration tests
- Live execution

### Swarm

#### swarm_review.kos
**Purpose:** Swarm coordination
**Demonstrates:**
- Swarm declarations
- Member agents
- Consensus patterns

### Python SDK

#### sdk_hello.py
**Purpose:** Python SDK example
**Demonstrstrates:**
- Python API usage
- Programmatic program building
- Runtime invocation

**Run:**
```bash
python examples/sdk_hello.py
```

### TypeScript Analysis

#### order_service.ts
**Purpose:** TypeScript source analysis
**Demonstrates:**
- Cross-language structure extraction
- TypeScript parsing
- Type information extraction

**Run:**
```bash
kosika analyze examples/order_service.ts --format text
```

## Running Examples

### With Fixtures (Fast, No Model)

```bash
kosika run examples/hello_world.kos --trace output.jsonl
kosika replay output.jsonl
```

### With Live Model

```bash
# With OpenAI-compatible endpoint
kosika run examples/research_agent.kos --input examples/research_agent_input.json --model gpt-4

# With Ollama
kosika run examples/research_agent.kos --input examples/research_agent_input.json --model qwen2.5:7b --protocol ollama --model-url http://localhost:11434
```

### Checking Programs

```bash
kosika check examples/multi_agent.kos
```

### Mindmaps and Visualizations

```bash
kosika mindmap examples/composed/main.kos --format text
kosika mindmap examples/composed/main.kos --format svg --view mindmap
```

## Example Learning Path

1. **Start Simple:** hello_world.kos → typed_input.kos → local_bindings.kos
2. **Add Complexity:** closures.kos → recursion_match.kos
3. **Modules:** composed/main.kos
4. **Agents:** multi_agent.kos → research_agent.kos
5. **Advanced:** knowledge.kos → learning_agent.kos
6. **Motion:** motion/logo.kos → path_motion/
7. **Integration:** agent_factory.kos → mcp_host_server.py

## Fixtures

Many examples include fixture data files (JSON) for offline testing without model calls:
- `*_input.json` - Test inputs
- `*_responses.json` - Model responses
- `*_manifest.json` - MCP server manifests
- `*_models.json` - Model bindings
- `*_tools.json` - tool bindings

## Special Folders

- `examples/agents/` - Agent-specific examples
- `examples/motion/` - Motion and animation examples
- `examples/path_motion/` - Path-based motion
- `examples/composed/` - Multi-file module examples
- `examples/research_documents/` - Research document test data
- `examples/documents/` - Document processing test data
- `examples/mcp/` - MCP server examples
- `examples/studios/` - Studio-related examples
- `examples/worlds/` - World simulation examples
- `examples/virtual_models/` - Virtual model examples
- `examples/self_improve/` - Self-improvement examples

## Troubleshooting

**Check failed:**
- Verify syntax matches Kosika v0.1.0 contract (see KOSIKA.md)
- Check all imported files exist
- Ensure record names match declarations

**Run failed:**
- Verify input JSON structure matches expected schema
- Check model bindings are correct
- Review error messages for specific issues

**MCP tools not found:**
- Ensure MCP server is running
- Check manifest.json is correct
- Verify MCP server registration

## Contributing Examples

When adding new examples:
1. Follow `.kos` file naming convention
2. Include fixture data for offline testing
3. Add to this guide with category and purpose
4. Ensure it passes `kosika check`
5. Document required dependencies (models, tools, MCP servers)
