# Kosika Vision

## Purpose

Kosika is intended to become an AI-native programming language and execution platform for building intelligent systems, not merely another Python library or model wrapper.

Its purpose is to give developers one coherent place to define:

- Programs, modules, functions, and data types.
- Models and model-provider policies.
- Agents and bounded reasoning loops.
- Tools, MCP servers, and external capabilities.
- Workflows, approvals, retries, and durable state.
- Memory, assumptions, judgments, evidence, and personality.
- Mathematical computation with vectors, matrices, tensors, sequences, and signals.
- Mindmaps, diagrams, visual explanations, and animations.
- Tests, traces, replay, evaluation, and deployment.

Kosika should reduce the scattered collection of Python and JavaScript scripts commonly required to assemble an AI system. Python, JavaScript, Rust, and external services remain valuable implementation layers, but Kosika becomes the source of truth for the system's structure, authority, and execution.

## The central idea

Traditional languages treat AI as a library call:

```python
response = model.generate(prompt)
```

Kosika should treat intelligence as a native program concept:

```kosika
model analyst {
  input: ResearchQuestion
  output: ResearchAnswer
  max_tokens: 2048
}
```

A Kosika program should make clear:

- What the system knows.
- What the system assumes.
- What the system can do.
- Which models it can use.
- Which tools it can access.
- What it is allowed to change.
- How much it may spend or execute.
- How its decisions can be explained.
- How its execution can be replayed.

## The critical limitation, clarified

Kosika should have minimal artificial language limitations, but it must not have unlimited execution authority.

These are different things.

Kosika should be expressive enough to support:

- Nested functions.
- Closures.
- Recursion.
- Higher-order functions.
- Dynamic workflows.
- Mathematical computation.
- Rich data types.
- Agents with flexible reasoning.
- Complex animations.
- Distributed execution.

However, the runtime must still enforce boundaries:

- A recursive function may have a depth or time budget.
- An agent may have a maximum number of steps.
- A model may have token and cost limits.
- A tool may require approval.
- A memory may be private or read-only.
- An animation may have frame and resource limits.
- A remote worker may have a declared capability set.

The principle is:

> Unlimited expression, bounded authority.

A developer should be free to describe complex behavior. The runtime should decide whether that behavior is safe, authorized, valid, and within resource limits.

## Language layers

### 1. Code layer

The code layer should support:

- Modules and imports.
- Records and structured data.
- Functions and nested functions.
- Closures.
- Functions as values.
- Recursion.
- Pattern matching.
- Generators and lazy sequences.
- Async functions.
- Workflows.
- Models.
- Agents.
- Tools.
- Memory declarations.
- Event handlers.
- Scenes and animations.

Illustrative nested-function syntax:

```kosika
fn make_threshold_filter(limit: Number) -> Function {
  fn accepts(value: Number) -> Boolean {
    return value > limit
  }

  return accepts
}
```

A normal function, workflow, agent, and scene should have different semantics:

- A function performs computation.
- A workflow manages durable, observable execution.
- An agent performs bounded reasoning inside a workflow.
- A scene reacts to data and runtime events for visualization.

### 2. Compiler and type layer

The compiler should understand more than syntax. It should check:

- Record and field types.
- Function inputs and outputs.
- Model schemas.
- Tool schemas.
- Agent capabilities.
- Workflow data flow.
- Memory access.
- Approval requirements.
- Event types.
- Module boundaries.
- External side effects.
- Recursive and resource-sensitive behavior.

The compiler should reject unknown tools, missing model bindings, invalid workflow calls, type mismatches, unsafe capability paths, and unresolved modules.

The deterministic compiler graph is the source for Kosika mindmaps.

### 3. Model layer

Models should be declared through a provider-neutral interface:

```kosika
model analyst {
  provider: local
  model: "qwen2.5"
  input: ResearchQuestion
  output: ResearchAnswer
  temperature: 0.2
  max_tokens: 2048
  structured: true
}
```

The model layer should support:

- Local and cloud models.
- Text, embedding, vision, audio, image, and classifier models.
- Structured-output validation.
- Provider routing and fallback.
- Token, cost, latency, and request budgets.
- Timeouts and retries.
- Version pinning.
- Caching.
- Privacy and local/cloud policies.
- Usage telemetry.
- Capability discovery.
- Model evaluation and comparison.

Declaring a model must not automatically grant network access or credentials.

### 4. Agent and workflow layer

Agents should be bounded reasoning components:

```kosika
agent researcher {
  model: analyst
  can: [search_documents, search_web]
  cannot: [write_files, deploy_services]
  max_steps: 8
  max_tool_calls: 20
}
```

Workflows should support:

- Sequential steps.
- Conditions.
- Bounded loops.
- Parallel branches.
- Child workflows.
- Queues and scheduling.
- Checkpoints.
- Retries.
- Deadlines.
- Cancellation.
- Approvals.
- Compensation and rollback.
- Local and distributed workers.

The model proposes actions. The workflow runtime and capability system determine whether those actions are valid and authorized.

### 5. Tool and MCP layer

MCP should be a controlled adapter beneath Kosika's general tool abstraction, alongside Python, JavaScript, HTTP, database, filesystem, and local adapters.

```kosika
tool search_documents {
  source: mcp "local_documents"
  operation: "search"
  access: observe
  input: SearchQuery
  output: SearchResults
}
```

MCP execution requires:

- A declared server.
- Pinned server identity.
- A pinned manifest.
- An allowlisted tool.
- Validated arguments.
- Validated output.
- Correct observe or execute access.
- Required approval.
- Timeouts and cleanup.
- Transport-failure handling.
- Replay records and audit events.

A model must not be allowed to discover and execute arbitrary MCP servers.

### 6. Cognitive layer

Kosika should not pretend to reproduce the entire human brain. It should provide explicit, composable constructs for useful cognitive patterns:

- Assumptions.
- Observations.
- Hypotheses.
- Judgments.
- Intentions.
- Attention.
- Memory.
- Evidence.
- Uncertainty.
- Revisions.
- Conflicting beliefs.
- Personality policies.

#### Assumptions

```kosika
assumption local_server_available {
  statement: "The model server is reachable"
  confidence: 0.7
  source: environment
  invalidated_by: [connection_failure]
}
```

An assumption is not automatically a fact. It records its source, confidence, and invalidation conditions.

#### Judgments

```kosika
judgment deployment_safe {
  claim: "The deployment can proceed"
  confidence: 0.82
  evidence: [tests_passed, security_scan_passed, approval_received]
  action: deploy_service
}
```

A judgment should retain its evidence, alternatives, timestamp, expiration, and approval state.

#### Memory bubbles

A memory bubble is a bounded, contextual knowledge item:

```kosika
memory_bubble project_context {
  content: "The user prefers local-first AI systems"
  kind: preference
  source: user_confirmed
  confidence: 1.0
  scope: user
  retention: permanent
}
```

Memory bubbles may represent:

- Episodic events.
- Semantic knowledge.
- Procedures.
- Preferences.
- Hypotheses.
- Conversation context.
- Relationships.
- Execution history.

Each item should track content, source, timestamp, owner, confidence, scope, permissions, version, evidence, and status.

Possible states include:

```text
unverified -> supported -> trusted -> contradicted -> archived
```

Model-generated claims must not automatically become trusted memory.

#### Personality

Personality should influence communication, planning, risk preference, attention, and explanation style:

```kosika
personality technical_reviewer {
  tone: precise
  priorities: [correctness, safety, simplicity]
  uncertainty_style: explicit
  risk_tolerance: low
  explanation_depth: detailed
}
```

Personality must not override permissions, approvals, type validation, user authorization, or safety policies.

### 7. Mathematical and scientific layer

Kosika should provide richer mathematical primitives than ordinary scripting languages:

- Scalars.
- Vectors.
- Matrices.
- Tensors.
- Complex numbers.
- Quaternions.
- Graphs.
- Points and transforms.
- Distributions.
- Signals.
- Time series.
- Embeddings.
- Images, audio, and video.

The language should support:

- Vector arithmetic.
- Matrix multiplication.
- Broadcasting.
- Slicing.
- Transposition.
- Shape checking.
- Interpolation.
- Convolution.
- Pooling.
- Fourier transforms.
- Attention operations.
- Tensor adapters for hardware accelerators.

Illustrative syntax:

```kosika
matrix weights: [128, 768]
vector embedding: [768]
vector output = weights @ embedding
```

The compiler should detect incompatible shapes before runtime when dimensions are known.

Sequences should also be first-class:

```kosika
sequence fibonacci() -> Stream[Integer] {
  let a = 0
  let b = 1

  while true {
    yield a
    let next = a + b
    a = b
    b = next
  }
}
```

### 8. Runtime layer

The runtime is the execution authority. It should manage:

- Local and remote execution.
- Model calls.
- Tool calls.
- Permissions.
- Approvals.
- Sessions.
- Checkpoints.
- Cancellation.
- Deadlines.
- Resource limits.
- Failure recovery.
- Durable state.
- Replay.
- Normalized events.

Important events include:

```text
program_start
workflow_start
step_start
agent_call
model_call
tool_call
tool_return
model_return
approval_required
step_complete
workflow_complete
failure
```

### 9. Design, mindmap, and animation layer

The design layer should consume the checked AST, workflow graph, runtime events, traces, and evaluation data.

It should produce:

- Code mindmaps.
- Workflow graphs.
- Data-flow diagrams.
- Capability maps.
- Dependency graphs.
- Arrays and matrices.
- Venn diagrams.
- Timelines.
- Execution heatmaps.
- Neural-field animations.

The mindmap must be deterministic and compiler-generated. AI may explain the graph or suggest a layout, but AI must not invent structural nodes or edges.

Mathematical functions can drive animation:

```kosika
fn spiral_position(t: Number) -> Vector2 {
  let radius = 20 * t
  let angle = 6.28 * t
  return [radius * cos(angle), radius * sin(angle)]
}
```

Runtime events can drive visual effects:

```kosika
on model_call(event) {
  emit pulse(
    origin: node[event.model],
    color: palette.model,
    energy: event.token_count
  )
}
```

The CLI, Workbench, Synapse interface, replay viewer, and AI assistant should use the same program graph and event model.

### 10. Evaluation and testing layer

AI programs require specialized tests:

- Fixture-based model tests.
- Deterministic fake tools.
- Offline replay.
- Prompt regression.
- Schema validation.
- Agent trajectory tests.
- Permission tests.
- Safety tests.
- Citation tests.
- Cost and latency budgets.
- Evaluation datasets.
- Model comparison.
- Golden traces.

A program can be syntactically correct but behaviorally wrong, so evaluation is part of the language platform.

### 11. Source translation and program modernization

Kosika should be able to read programs written in Python, JavaScript, TypeScript, Rust, Go, and other supported languages, then produce a clearer, safer, lighter, and more maintainable Kosika equivalent.

This is not merely text-to-text conversion. The intended pipeline is:

```text
Source program
    -> language-specific parser
    -> normalized program representation
    -> semantic and side-effect analysis
    -> Kosika program proposal
    -> Kosika parser and type checker
    -> tests and behavior comparison
    -> performance and resource benchmarks
    -> approved migration
```

The feature should support several explicit modes:

- `translate`: Convert a supported program or module into Kosika.
- `simplify`: Reduce unnecessary abstraction, duplication, and scattered configuration.
- `modernize`: Replace ad-hoc AI scripts with typed models, agents, tools, and workflows.
- `optimize`: Improve runtime, memory, token, or network behavior when benchmarks support the claim.
- `explain`: Produce a mindmap and plain-language explanation of the original program.
- `verify`: Compare outputs, exceptions, side effects, traces, and resource usage.
- `migrate`: Move a project incrementally while retaining adapters for unsupported behavior.

A future command family could look like:

```text
kosika convert legacy_agent.py --to kosika --output src/
kosika simplify src/main.syn --proposal rewrite.json
kosika verify rewrite.json --fixtures tests/fixtures
kosika benchmark rewrite.json
kosika migrate project/ --language python
```

The converter must preserve observable behavior unless the developer explicitly requests a behavior change. Verification should consider:

- Return values.
- Exceptions and error categories.
- File, network, database, and tool side effects.
- Ordering and concurrency behavior.
- Security and permission behavior.
- Model inputs and structured outputs.
- Trace events.
- Token, memory, latency, and network usage.

The converter should never claim that a rewrite is faster, simpler, or lighter without evidence. It should report a comparison such as:

```text
behavior: equivalent for 42 fixtures
memory: 31% lower in benchmark set
latency: 18% lower in benchmark set
source size: 46% smaller
unsupported behavior: dynamic import retained as Python adapter
confidence: verified
```

Dynamic or unsupported language behavior should not be silently discarded. Examples include reflection, runtime-generated code, monkey patching, native extensions, unknown network effects, and implicit global state. Kosika should retain such behavior behind a typed adapter and mark it clearly in the mindmap and migration report.

The AI may propose a rewrite, but it must not be the final authority. The final authority remains:

```text
Parser
  -> semantic checker
  -> capability checker
  -> tests
  -> behavior comparison
  -> benchmarks
  -> explicit approval
```

This feature would make Kosika useful even before developers write new Kosika programs. It could first serve as an AI-native code modernization and explanation tool, then become a migration path from existing Python and JavaScript systems into a more structured Kosika project.

## Built-in mindmap and AI integration

Kosika should provide two distinct systems.

### Deterministic layer

The compiler generates the authoritative graph from:

- The AST.
- Records and fields.
- Modules and imports.
- Models.
- Agents.
- Tools and access levels.
- Workflows and steps.
- Bindings.
- Data flow.
- Calls and dependencies.
- Approval boundaries.

### AI layer

The optional assistant receives bounded, checked context:

- Selected mindmap nodes.
- Diagnostics.
- Trace summaries.
- Program metadata.
- User questions.

It may provide:

- Natural-language explanations.
- Workflow walkthroughs.
- Failure explanations.
- Risk observations.
- Refactoring proposals.
- Test suggestions.
- Layout suggestions.
- Documentation.

It must not:

- Authorize a capability.
- Execute an undeclared tool.
- Treat a model guess as structural truth.
- Mutate a resource without approval.
- Bypass the checker.

The safe pipeline is:

```text
Checked program
    -> deterministic graph
    -> bounded AI context
    -> AI explanation or proposal
    -> schema validation
    -> tests and checker
    -> explicit approval for changes
```

## Project and platform structure

A future Kosika project should look like:

```text
project/
├── kosika.toml
├── src/
│   ├── types.syn
│   ├── models.syn
│   ├── tools.syn
│   ├── agents.syn
│   ├── memory.syn
│   ├── workflows.syn
│   └── main.syn
├── adapters/
│   ├── python/
│   └── javascript/
├── mcp/
├── design/
├── tests/
├── fixtures/
├── traces/
├── generated/
└── ui/
```

The standard CLI should eventually support:

```text
kosika init
kosika add
kosika check
kosika test
kosika run
kosika trace
kosika replay
kosika explain
kosika mindmap
kosika package
kosika deploy
```

## Integrated AI-system capabilities

The following capabilities are part of the same Kosika plan, not separate framework features. They should share one typed program graph, one capability model, one event format, and one trace/replay system.

### Intent, goals, constraints, and invariants

Kosika should let developers describe what a system is trying to achieve as well as how it operates:

```kosika
goal produce_reliable_answer {
  objective: answer_question
  require: citations
  avoid: unsupported_claims
  budget: 0.10
}
```

Intent declarations may include goals, priorities, constraints, invariants, preferences, acceptable trade-offs, and success conditions. The checker and evaluation layer should turn them into testable requirements.

### Context compilation

A context compiler should construct model context from declared sources rather than scattered prompt concatenation:

```kosika
context research_context {
  include: [question, approved_documents, recent_results]
  exclude: [credentials, private_notes]
  max_tokens: 8000
  require_citations: true
  prioritize: recent_and_relevant
}
```

It should handle retrieval, ranking, summarization, deduplication, token limits, privacy filtering, memory selection, citation tracking, and context expiration.

### Uncertainty and evidence

AI results should support confidence, evidence, alternatives, expiration, and contradiction rather than reducing every result to plain text or a Boolean. Beliefs and judgments should remain distinguishable from verified facts.

### Semantic AI testing

Kosika tests should be able to assert behavior, safety, permissions, citations, cost, latency, tool use, model trajectories, and trace outcomes. AI programs require more than ordinary unit tests.

### Simulation and counterfactual execution

Before a workflow performs a real external action, it should be able to run against a staging adapter, dry-run environment, or simulated tool. The result should show expected effects without mutating production resources.

### Multi-agent coordination

Kosika should support typed agent teams with specialist roles, supervisors, parallel review, consensus, disagreement handling, escalation, and shared permission-controlled memory.

### Temporal and event-driven execution

The language should support scheduled workflows, event triggers, long-running streams, deadlines, human responses, periodic memory review, and workflows that continue after a process restart.

### Multimodal and embodied data

Text, images, audio, video, documents, embeddings, sensor data, spatial maps, motion, and scene objects should have typed interfaces. These types should be usable by models, workflows, mathematical functions, and visual scenes.

### Safe self-improvement

A Kosika system may inspect itself and propose improvements, but changes must pass through analysis, patch generation, parsing, checking, tests, benchmarks, and explicit approval before application.

### Semantic diffs and resource-aware optimization

Kosika should compare programs by changed capabilities, model calls, memory policies, side effects, workflow structure, and behavior—not only changed lines. It should report measured changes in latency, memory, token usage, network usage, source size, and cloud cost rather than making unsupported optimization claims.

### AI-native packages and human collaboration

Packages should declare model requirements, hardware requirements, permissions, privacy policies, MCP dependencies, evaluation results, and expected resource usage. Humans should be able to participate through typed approval, review, ranking, correction, annotation, and conflict-resolution steps.

## Extension ecosystem

Kosika should have a first-class extension system for AI capabilities, similar in spirit to language packages and editor extensions but governed by typed contracts and runtime authority. An extension is not merely an importable collection of code: it is a declared capability bundle that Kosika can inspect, check, sandbox, observe, replay, upgrade, disable, and roll back.

An extension manifest should declare:

- Extension identity, author, version, license, and compatibility range.
- Kosika language/runtime versions and dependency constraints.
- Contributed models, providers, agents, tools, workflows, records, memory stores, and language adapters.
- MCP servers, Python/JavaScript/Rust adapters, local processes, network endpoints, and required transports.
- Diagram, mindmap, visualization, theme, animation, scene, and event-rendering contributions.
- Deployment targets, hardware requirements, resource estimates, and local/cloud preferences.
- Permissions, data classifications, secrets requirements, retention rules, and approval requirements.
- Health checks, startup/shutdown behavior, migration steps, evaluation fixtures, and rollback metadata.

Extensions should support a lifecycle of discovery, validation, installation, permission review, activation, health checking, use, upgrade, deactivation, rollback, and removal. Dependency resolution must be deterministic and fail closed when compatibility, integrity, permissions, or manifests are unclear. A broken extension should be isolated without corrupting the host program, and its process, memory, network, and event resources should be cleaned up within bounded timeouts.

Every extension call should produce normalized provenance and replay data: the selected extension version, manifest identity, capability, arguments, redacted context, result schema, resource usage, approval decision, and failure state. This makes extensions inspectable in the code mindmap and runtime visualizations. An extension may propose new capabilities, but it must not silently grant itself permissions, discover arbitrary MCP servers, bypass approval, or convert untrusted model output into executable authority.

The ecosystem should provide official and community extension classes without forcing every contributor to implement every layer:

1. Model extensions provide local, cloud, embedding, vision, audio, image, classifier, or simulation backends.
2. Tool extensions provide typed APIs for files, data, browsers, infrastructure, devices, and external services.
3. Cognitive extensions provide memory stores, retrieval, evaluators, policies, and reasoning strategies.
4. Language extensions provide syntax, parsers, formatters, linters, source converters, and adapters.
5. Design extensions provide diagrams, mindmaps, layouts, themes, animations, scenes, and telemetry views.
6. Deployment extensions provide workers, queues, containers, Kubernetes targets, edge runtimes, and cloud services.

## Cognitive runtime

Kosika should model intelligent behavior as an explicit, inspectable runtime rather than hiding it inside an opaque assistant loop. The runtime may represent human-inspired concepts such as assumptions, judgments, memory bubbles, attention, personality, and metacognition, but these remain typed mechanisms with provenance and limits—not claims that the system is conscious or infallible.

A reference cognitive pipeline is:

1. Perception receives typed text, documents, images, audio, video, events, sensors, and tool results.
2. Attention selects relevant signals using goals, priorities, permissions, recency, uncertainty, and context budgets.
3. Working memory holds bounded task state, active hypotheses, intermediate values, and unresolved questions.
4. Long-term memory stores durable facts, procedures, preferences, episodes, embeddings, and knowledge-graph relations with provenance and retention policy.
5. Reasoning composes functions, nested functions, workflows, agents, mathematical operations, retrieval, and model calls.
6. Judgment evaluates evidence, alternatives, constraints, risks, confidence, and expected consequences.
7. Personality applies declared communication and interaction policies without changing safety or authorization rules.
8. Metacognition records what the system knows, does not know, assumed, attempted, verified, and should ask a human to review.
9. Action emits a proposal, simulation, approval request, or authorized effect through a typed capability.

Kosika should keep the following boundaries explicit:

- Memory is state with source, owner, timestamp, confidence, permissions, and provenance; memory is not automatically truth.
- An assumption is a provisional premise that can be challenged, expired, replaced, or tested; it must not silently become a fact.
- A judgment is an attributed evaluation over evidence and criteria; it should expose uncertainty and competing interpretations.
- Personality controls style, priorities, and interaction patterns within policy; personality never grants permissions or overrides safety checks.
- A model or agent produces a proposal; the checker, capability registry, approval system, and runtime remain authoritative.
- Confidence describes estimated uncertainty; proof, tests, citations, replay, and verified observations establish what is supported.
- Context is compiled from permitted sources and should record inclusion, exclusion, redaction, budget, and privacy decisions.

Cognitive state should be inspectable through deterministic records, traces, diagnostics, and mindmaps. A user should be able to ask why a memory was retrieved, which assumption influenced a decision, what evidence supported a judgment, why a tool was allowed or denied, and where an answer remains uncertain. The explanation should link back to source spans, event IDs, tests, citations, and replayable execution steps whenever available.

The runtime should allow deeply nested composition—functions inside functions, workflows inside workflows, agents invoking bounded sub-agents, and cognitive policies calling typed evaluators—while preserving cancellation, deadlines, resource budgets, recursion guards, maximum steps, failure telemetry, and approval boundaries. “Minimal language limitation” should mean expressive composition and clear escape hatches, not unbounded execution that can exhaust resources or bypass authority.

## Implementation strategy

Kosika should be developed in stages.

### Stage 1: Expressive language core

- Functions and nested functions.
- Closures.
- Recursion.
- Pattern matching.
- Rich records and collections.
- Streams and async operations.
- Goals, constraints, priorities, and invariants.
- Semantic program graph and structural diffs.
- Better diagnostics.

### Stage 2: AI-native types and runtime

- Model declarations.
- Agent declarations.
- Tool capabilities.
- Structured model outputs.
- Context compiler with privacy and token policies.
- Confidence, uncertainty, evidence, and judgment types.
- Budgets and retries.
- Durable workflows.
- Approval gates.
- Simulation and dry-run adapters.
- Replay and telemetry.
- Semantic AI tests and evaluation contracts.

### Stage 3: Cognitive and data systems

- Memory bubbles.
- Assumptions.
- Judgments.
- Evidence.
- Personality policies.
- Attention and context policies.
- Documents.
- Search.
- Embeddings.
- Citations.
- Knowledge graphs.
- Multi-agent teams and consensus.
- Human review and escalation.
- Scheduled and event-triggered workflows.
- Long-running streams and resumable processes.

### Stage 4: Mathematical and visual systems

- Vectors and matrices.
- Tensors and shape validation.
- Convolution and signal operations.
- Multimodal and sensor data types.
- Spatial maps, motion, and scene objects.
- Simulation and counterfactual visualizations.
- Graph layouts.
- Diagram models.
- Themes.
- Animation functions.
- Runtime event visualizations.

### Stage 5: Platform and ecosystem

- `kosika.toml`.
- AI-native package management.
- Python and JavaScript adapters.
- MCP package contracts.
- Semantic source conversion and migration.
- Safe AI-generated improvement proposals.
- Resource-aware compilation and optimization.
- Local and cloud workers.
- Deployment targets.
- Stable runtime APIs.
- Documentation and examples.
- Self-hosting.

## Current reality

The current implementation is a Python-first experimental foundation. It already contains important first slices:

- Typed records and workflows.
- Model declarations and provider bindings.
- Bounded agents.
- MCP stdio clients and pinned manifests.
- Capability and approval checks.
- Durable sessions.
- Model budgets and telemetry.
- Offline traces and replay.
- Deterministic mindmaps.
- Focused explanations.
- Extension manifest validation.
- Extension-aware mindmaps and explanations.
- Assistant context handling.
- Workbench visualization.
- Normalized runtime events.
- SSE-oriented live telemetry prototypes.

The current implementation does not yet provide the complete future language described here. The vision is deliberately broader than the current prototype. New features should be introduced through small, typed, tested, replayable slices rather than a large uncontrolled rewrite.

## Success criteria

Kosika will be successful when an AI developer can create one project and use it to:

1. Declare models, agents, tools, memory, and workflows.
2. Connect Python, JavaScript, MCP, and local services through typed adapters.
3. Run locally or remotely with explicit permissions.
4. Test model behavior with deterministic fixtures.
5. Replay an execution without network access.
6. Inspect a deterministic code and data-flow mindmap.
7. Ask an AI assistant for a grounded explanation.
8. Visualize live execution through events and animations.
9. Apply AI-generated changes only after checking and approval.
10. Package and deploy the same project reproducibly.
11. Declare goals, constraints, invariants, and measurable success conditions.
12. Construct bounded, privacy-aware model context automatically.
13. Represent uncertainty, evidence, assumptions, judgments, and memory state explicitly.
14. Test agent behavior, tool permissions, citations, cost, latency, and safety.
15. Simulate external actions before applying them to real systems.
16. Coordinate multiple agents and humans through typed review and escalation steps.
17. Convert and modernize existing programs with behavior verification and benchmarks.
18. Propose self-improvements without applying them until checking, testing, benchmarking, and approval succeed.

The long-term goal is not merely to create a language that can call AI models. It is to create a language that expresses what an intelligent system knows, assumes, remembers, believes, intends, can do, is allowed to do, how it can improve, and how its behavior can be understood.

> Kosika defines system structure and authority. Python, JavaScript, Rust, models, and MCP servers provide specialized implementation capabilities.
