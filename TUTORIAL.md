# Kosika Tutorial Progression

A structured learning path from beginner to advanced Kosika programming.

## Prerequisites

- Basic programming knowledge (functions, variables, types)
- Command line familiarity
- Python 3.10+ installed (for Kosika runtime)
- Text editor or IDE (VS Code recommended with Kosika LSP)

## Level 1: Hello World (10 minutes)

**Goal:** Run your first Kosika program

**File:** `examples/hello_world.kos`

**What you'll learn:**
- Kosika file structure (.kos extension)
- Record declarations
- Pure functions
- Workflow basics
- Running and checking programs

**Steps:**
1. Read `examples/hello_world.kos`
2. Run `kosika check examples/hello_world.kos`
3. Run `kosika run examples/hello_world.kos --trace hello.jsonl`
4. Read the trace output
5. Run `kosika replay hello.jsonl`

**Quiz:**
- What is the input type of the workflow?
- What does the function return?
- How many records are declared?

## Level 2: Typed Input/Output (15 minutes)

**Goal:** Work with typed data and validation

**File:** `examples/typed_input.kos`

**What you'll learn:**
- Type checking on input
- Record schemas
- Type validation
- Working with JSON input

**Steps:**
1. Read `examples/typed_input.kos`
2. Read `examples/typed_input.json`
3. Run `kosika check examples/typed_input.kos`
4. Run with the correct input
5. Try running with an invalid input (modify the JSON)

**Quiz:**
- What happens if you pass an invalid type?
- How does Kosika validate input at runtime?
- What is the output type?

## Level 3: Local Bindings (20 minutes)

**Goal:** Understand mutable and immutable bindings

**File:** `examples/local_bindings.kos`

**What you'll learn:**
- `let` vs `var` bindings
- Assignment and reassignment
- Scope rules
- Mutation patterns

**Steps:**
1. Read `examples/local_bindings.kos`
2. Run the program
3. Modify a `let` to `var` and observe the difference
4. Try reassigning a `let` (should fail type check)

**Quiz:**
- When should you use `let` vs `var`?
- What happens if you try to reassign a `let`?
- What is the scope of a local binding?

## Level 4: Closures (30 minutes)

**Goal:** Master nested functions and closures

**File:** `examples/closures.kos`

**What you'll learn:**
- Nested function declarations
- Lexical closures
- Function values
- `ref` references
- Higher-order functions

**Steps:**
1. Read `examples/closures.kos`
2. Run the program
3. Trace the closure behavior
4. Create your own closure example

**Quiz:**
- What is a closure?
- How do nested functions capture variables?
- What is `ref` used for?

## Level 5: Recursion and Pattern Matching (45 minutes)

**Goal:** Work with recursion and pattern matching

**File:** `examples/recursion_match.kos`

**What you'll learn:**
- Bounded recursion with `recurse N`
- Pattern matching on enum values
- Complex conditional logic
- Termination guarantees

**Steps:**
1. Read `examples/recursion_match.kos`
2. Run the program
3. Trace the recursion depth
4. Modify the recursion limit
5. Add a new pattern match case

**Quiz:**
- What does `recurse N` do?
- How does Kosika guarantee recursion terminates?
- When should you use pattern matching?

## Level 6: Multi-File Programs (1 hour)

**Goal:** Build programs across multiple files

**Directory:** `examples/composed/`

**What you'll learn:**
- Module imports
- Explicit exports
- Type checking across modules
- Shared types
- Module organization

**Steps:**
1. Read `examples/composed/main.kos`
2. Read the imported files
3. Run the composed program
4. Create your own module
5. Import it into main

**Quiz:**
- How do you export a function?
- What happens if you import a non-existent file?
- How are types checked across modules?

## Level 7: Workflows and Steps (1.5 hours)

**Goal:** Build multi-step workflows

**File:** `examples/multi_agent.kos`

**What you'll learn:**
- Workflow structure
- Step definitions
- Step sequencing
- Budget declarations
- Agent integration

**Steps:**
1. Read `examples/multi_agent.kos`
2. Read `examples/research_agent.kos`
3. Compare the approaches
4. Run a workflow with model fixtures
5. Try adding a new step

**Quiz:**
- What is a workflow budget?
- How do steps communicate?
- Can steps call each other?

## Level 8: Agents and Tools (2 hours)

**Goal:** Build AI agents with tools

**File:** `examples/research_agent.kos`

**What you'll learn:**
- Agent declarations
- Tool declarations
- Model integration
- Tool invocation
- Agent composition

**Steps:**
1. Read `examples/research_agent.kos`
2. Read the tool definitions
3. Run with fixtures
4. Create your own agent
5. Add a custom tool

**Quiz:**
- What is the difference between an agent and a function?
- How do tools get declared?
- What happens when a tool fails?

## Level 9: Motion and Animation (2 hours)

**Goal:** Create declarative animations

**File:** `examples/motion/logo.kos`

**What you'll learn:**
- Scene descriptions
- Shape definitions
- Move definitions
- Easing functions
- HTML generation

**Steps:**
1. Read `examples/motion/logo.kos`
2. Run `kosika motion examples/motion/logo.kos --out logo.html`
3. Open logo.html in a browser
4. Modify the animation timing
5. Create your own motion scene

**Quiz:**
- What is the difference between motion and drawing?
- How do you create an infinite loop?
- What easing functions are available?

## Level 10: Advanced Patterns (3 hours)

**Goal:** Combine multiple concepts

**Files:**
- `examples/knowledge.kos`
- `examples/learning_agent.kos`
- `examples/skills.kos`

**What you'll learn:**
- Knowledge representation
- Learning patterns
- Skill composition
- Complex type hierarchies
- Performance considerations

**Steps:**
1. Read each advanced example
2. Run each one
3. Identify patterns they share
4. Combine patterns into your own program
5. Profile performance

**Quiz:**
- How do you represent knowledge in Kosika?
- What is a skill?
- How do agents learn?

## Level 11: MCP Integration (3 hours)

**Goal:** Integrate with external tools via MCP

**Files:**
- `examples/mcp_host_server.py`
- `examples/agent_factory.kos`
- `examples/approval_mutation.kos`

**What you'll learn:**
- MCP protocol
- Tool registration
- Server hosting
- Approval workflows
- Dynamic capabilities

**Steps:**
1. Read the MCP server implementation
2. Run the MCP server
3. Call it from a Kosika program
4. Add a new tool to the server
5. Test the integration

**Quiz:**
- What is MCP?
- How do tools get registered?
- What is an approval workflow?

## Level 12: Building a Complete Application (5+ hours)

**Goal:** Build a production-ready Kosika application

**Project:** Choose one:
- Research assistant
- Document processor
- Multi-agent debate system
- Monitoring agent

**What you'll learn:**
- Architecture design
- Error handling
- Testing strategies
- Deployment
- Documentation

**Steps:**
1. Design the application
2. Implement core functionality
3. Add tests
4. Integrate with tools/models
5. Document the application
6. Deploy and monitor

**Deliverables:**
- Working Kosika program
- Test suite
- Documentation
- Deployment guide

## Learning Resources

### Documentation
- `KOSIKA.md` - Language contract (normative)
- `KOSIKA_PLAN.md` - Implementation roadmap
- `KOSIKA_VISION.md` - Long-term design
- `EXAMPLES_GUIDE.md` - Example catalog
- `MOTION_GUIDE.md` - Motion and animation
- `DEVELOPMENT.md` - Development guide

### Tools
- `kosika check` - Type checking
- `kosika run` - Execute programs
- `kosika replay` - Replay traces
- `kosika motion` - Generate animations
- `kosika mindmap` - Generate visualizations
- `kosika test` - Run tests

### Editor Support
- VS Code extension with LSP
- Syntax highlighting
- Type checking in editor
- Diagnostics

## Common Pitfalls

1. **Forgot to declare types** - Always declare record fields
2. **Mixed up `let` and `var`** - Use `let` for immutable, `var` for mutable
3. **Incorrect model bindings** - Check model and tool fixtures
4. **Missing imports** - Ensure all imports are declared
5. **Workflow syntax errors** - Workflows have specific syntax rules

## Getting Help

- Check the documentation
- Look at similar examples
- Run `kosika check` for diagnostics
- Review error messages carefully
- Start simple, then add complexity

## Next Steps

After completing the tutorial:
- Build your own project
- Contribute examples
- Explore advanced features
- Integrate with external services
- Share your work with the community
