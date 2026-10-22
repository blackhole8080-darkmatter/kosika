# Cognitive Layer Integration Design for Kosika

## Overview

This document describes how to integrate cognitive constructs (memory bubbles, assumptions, judgments, personality) into the Kosika language as first-class features. This layer differentiates Kosika from Python by making AI reasoning traceable, auditable, and explicit.

## Goals

1. **Explicit cognitive state:** All beliefs, assumptions, and preferences are tracked
2. **Provenance tracking:** Every cognitive item has a clear source and timestamp
3. **Confidence quantification:** Uncertainty is explicit, not implicit
4. **Permission scoping:** Cognitive items have access controls
5. **Contradiction detection:** The system detects conflicting beliefs
6. **Traceable state changes:** All cognitive operations are recorded in traces

## Language Extensions

### 1. Memory Bubble Syntax

```kosika
memory_bubble user_preference {
  content: "User prefers concise answers"
  kind: preference
  source: user_confirmed
  confidence: 1.0
  scope: session
  retention: permanent
}

memory_bubble conversation_context {
  content: {topic: "AI programming", last_message: "how to implement tensors"}
  kind: conversation
  source: dialogue
  confidence: 0.9
  scope: session
  retention: ephemeral
}
```

### 2. Assumption Syntax

```kosika
assumption model_available {
  statement: "Local model server is reachable"
  confidence: 0.8
  source: environment
  invalidated_by: [connection_failure]
}

assumption user_online {
  statement: "User is currently online"
  confidence: 0.95
  source: telemetry
  invalidated_by: [disconnect_event, timeout_event]
}
```

### 3. Judgment Syntax

```kosika
judgment answer_correct {
  claim: "The generated answer is correct"
  confidence: 0.85
  evidence: [source_citation, fact_check]
  action: return_answer
}

judgment deployment_safe {
  claim: "The deployment can proceed"
  confidence: 0.92
  evidence: [tests_passed, security_scan, approval_received]
  action: deploy_service
  approved: true
  approver: "human_reviewer"
}
```

### 4. Personality Syntax

```kosika
personality technical_reviewer {
  tone: precise
  priorities: [correctness, safety, simplicity]
  uncertainty_style: explicit
  risk_tolerance: low
  explanation_depth: detailed
  attention_style: focused
}

personality friendly_assistant {
  tone: casual
  priorities: [helpfulness, clarity, engagement]
  uncertainty_style: probabilistic
  risk_tolerance: medium
  explanation_depth: brief
  attention_style: adaptive
}
```

### 5. Cognitive Operations

```kosika
// Add memory
memory.add(user_preference)

// Query memories
let relevant_memories = memory.query(
  kind: preference,
  min_confidence: 0.8,
  scope: session
)

// Check assumptions
let invalidated = assumption.check(state: current_state)

// Add judgment
judgment.add(answer_correct)

// Approve judgment
judgment.approve(answer_correct, approver: "human")

// Apply personality
let response = personality.apply(friendly_assistant, content: draft)
```

## Implementation Plan

### Phase 1: Parser Extension

**File:** `kosika/parser.py`

Add support for:
- `memory_bubble` declaration
- `assumption` declaration
- `judgment` declaration
- `personality` declaration
- Cognitive operation syntax

```python
# New tokens
MEMORY_BUBBLE = "memory_bubble"
ASSUMPTION = "assumption"
JUDGMENT = "judgment"
PERSONALITY = "personality"

# Parse memory bubble
def parse_memory_bubble(self):
    self.expect(MEMORY_BUBBLE)
    name = self.expect(IDENTIFIER)
    self.expect(LBRACE)
    fields = self.parse_record_fields()
    self.expect(RBRACE)
    return MemoryBubbleDecl(name, fields)

# Parse assumption
def parse_assumption(self):
    self.expect(ASSUMPTION)
    name = self.expect(IDENTIFIER)
    self.expect(LBRACE)
    fields = self.parse_record_fields()
    self.expect(RBRACE)
    return AssumptionDecl(name, fields)

# Parse judgment
def parse_judgment(self):
    self.expect(JUDGMENT)
    name = self.expect(IDENTIFIER)
    self.expect(LBRACE)
    fields = self.parse_record_fields()
    self.expect(RBRACE)
    return JudgmentDecl(name, fields)

# Parse personality
def parse_personality(self):
    self.expect(PERSONALITY)
    name = self.expect(IDENTIFIER)
    self.expect(LBRACE)
    fields = self.parse_record_fields()
    self.expect(RBRACE)
    return PersonalityDecl(name, fields)
```

### Phase 2: AST Extension

**File:** `kosika/ast.py`

Add new AST nodes:

```python
@dataclass
class MemoryBubbleDecl(Declaration):
    name: str
    fields: Dict[str, Expression]

@dataclass
class AssumptionDecl(Declaration):
    name: str
    fields: Dict[str, Expression]

@dataclass
class JudgmentDecl(Declaration):
    name: str
    fields: Dict[str, Expression]

@dataclass
class PersonalityDecl(Declaration):
    name: str
    fields: Dict[str, Expression]

@dataclass
class MemoryAdd(Expression):
    memory: Expression

@dataclass
class MemoryQuery(Expression):
    kind: Optional[Expression]
    min_confidence: Optional[Expression]
    scope: Optional[Expression]

@dataclass
class AssumptionCheck(Expression):
    assumption: Expression
    state: Expression

@dataclass
class JudgmentAdd(Expression):
    judgment: Expression

@dataclass
class JudgmentApprove(Expression):
    judgment: Expression
    approver: Expression
```

### Phase 3: Type Checker Extension

**File:** `kosika/checker.py`

Add cognitive type checking:
- Validate memory bubble fields
- Check assumption field types
- Validate judgment evidence types
- Check personality field values

```python
def check_memory_bubble_decl(self, decl: MemoryBubbleDecl):
    # Check required fields: content, kind, source, confidence, scope, retention
    required = ["content", "kind", "source", "confidence", "scope", "retention"]
    for field in required:
        if field not in decl.fields:
            self.error(f"Memory bubble missing required field: {field}")

    # Check field types
    if "confidence" in decl.fields:
        self.check_type(decl.fields["confidence"], NumberType())

    # Validate kind is a valid MemoryKind
    if "kind" in decl.fields:
        kind_expr = decl.fields["kind"]
        if isinstance(kind_expr, Literal):
            if kind_expr.value not in ["episodic", "semantic", "procedural",
                                       "preference", "hypothesis", "conversation",
                                       "relationship", "execution"]:
                self.error(f"Invalid memory kind: {kind_expr.value}")
```

### Phase 4: Runtime Integration

**File:** `kosika/runtime.py`

Add cognitive operation execution:
- Import the `cognitive` module
- Execute cognitive operations
- Record cognitive state changes in traces

```python
from . import cognitive

def execute_memory_add(self, memory: MemoryBubble):
    store = cognitive.get_cognitive_store()
    store.add_memory(memory)
    self.record_cognitive_operation("memory_add", memory.id)

def execute_memory_query(self, kind, min_confidence, scope):
    store = cognitive.get_cognitive_store()
    results = store.query_memories(
        kind=kind,
        min_confidence=min_confidence,
        scope=scope
    )
    self.record_cognitive_operation("memory_query", len(results))
    return results

def execute_assumption_check(self, assumption_id, state):
    store = cognitive.get_cognitive_store()
    assumption = store.get_assumption(assumption_id)
    if assumption:
        is_valid = assumption.check(state)
        self.record_cognitive_operation("assumption_check", assumption_id, is_valid)
        return is_valid
    return False
```

### Phase 5: Trace Extension

**File:** `kosika/trace_replay.py`

Add cognitive events to trace format:
- Memory additions
- Memory queries
- Assumption checks
- Judgment additions
- Personality applications

```python
class CognitiveEvent(TraceEvent):
    event_type: str  # "memory_add", "memory_query", "assumption_check", etc.
    cognitive_id: str
    result: Any
    timestamp: float
```

## Examples

### Example 1: Using Memory Bubbles

```kosika
memory_bubble user_context {
  content: "User is building an AI programming language"
  kind: semantic
  source: user_statement
  confidence: 1.0
  scope: session
  retention: permanent
}

workflow main(query: Text) -> Answer {
  step respond {
    // Add user's query to conversation memory
    memory.add(memory_bubble {
      content: {query: query, timestamp: now}
      kind: conversation
      source: dialogue
      confidence: 1.0
      scope: session
      retention: ephemeral
    })

    // Query relevant memories
    let context = memory.query(
      kind: semantic,
      min_confidence: 0.8,
      scope: session
    )

    // Generate response with context
    let answer = use_model assistant(
      prompt: query,
      context: context
    )

    return answer
  }
}
```

### Example 2: Assumptions and Judgments

```kosika
assumption model_server_available {
  statement: "Model server is reachable"
  confidence: 0.9
  source: environment
  invalidated_by: [connection_error, timeout]
}

judgment response_quality {
  claim: "Response meets quality standards"
  confidence: 0.0  // Initially unknown
  evidence: []
  action: return_response
}

workflow main(question: Text) -> Answer {
  step check_assumptions {
    let state = {connection_active: check_connection()}
    let valid = assumption.check(model_server_available, state: state)

    if not valid {
      return {text: "Model server unavailable", error: true}
    }
  }

  step generate {
    let response = use_model assistant(prompt: question)

    // Evaluate response quality
    judgment.add(judgment {
      claim: "Response meets quality standards"
      confidence: evaluate_quality(response)
      evidence: [fact_check(response), citation_check(response)]
      action: return_response
    })

    return response
  }
}
```

### Example 3: Personality-Driven Behavior

```kosika
personality technical_reviewer {
  tone: precise
  priorities: [correctness, safety, simplicity]
  uncertainty_style: explicit
  risk_tolerance: low
  explanation_depth: detailed
  attention_style: focused
}

workflow main(code: Text) -> Review {
  step analyze {
    let analysis = use_model analyzer(
      code: code,
      personality: technical_reviewer
    )

    // Personality influences the analysis
    // - Precise tone: exact error locations
    // - Low risk tolerance: flag all potential issues
    // - Explicit uncertainty: state confidence levels
    // - Detailed explanation: provide context for each issue

    return analysis
  }
}
```

### Example 4: Contradiction Detection

```kosika
memory_bubble fact_A {
  content: "Python is the best language for AI"
  kind: semantic
  source: user_statement
  confidence: 0.9
  scope: session
  retention: permanent
}

memory_bubble fact_B {
  content: "Kosika is better than Python for AI"
  kind: semantic
  source: system_knowledge
  confidence: 0.8
  scope: session
  retention: permanent
}

workflow main() -> Resolution {
  step detect {
    // System detects contradiction between fact_A and fact_B
    let contradictions = memory.get_contradictions()

    if contradictions.length() > 0 {
      // Ask for clarification
      let clarification = ask_user(
        question: "I've detected conflicting beliefs. Which should I trust?"
      )

      // Update memory based on clarification
      memory.update(fact_A, confidence: 0.3)
      memory.update(fact_B, confidence: 0.95)
    }

    return {resolved: true}
  }
}
```

## Testing Strategy

1. **Unit Tests:** Test each cognitive operation independently
2. **Integration Tests:** Test cognitive operations in workflows
3. **Trace Tests:** Ensure cognitive operations are properly traced
4. **Permission Tests:** Test access control and scoping
5. **Contradiction Tests:** Test contradiction detection and resolution

## Success Criteria

- [ ] All cognitive constructs are declarable in Kosika syntax
- [ ] Cognitive operations are type-safe
- [ ] All cognitive state changes are traceable
- [ ] Permission scoping works correctly
- [ ] Contradiction detection works
- [ ] Personality policies influence agent behavior
- [ ] Cognitive state is replayable from traces

## Open Questions

1. Should cognitive items be persisted across sessions?
2. How should we handle memory eviction when the store is full?
3. Should we support automatic confidence decay over time?
4. How should personality conflicts be resolved when multiple agents interact?

## References

- Cognitive science literature on memory and belief tracking
- Symbolic AI approaches to knowledge representation
- Bayesian reasoning and confidence updating
