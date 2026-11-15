# Ananta-Style Debate System

A multi-agent knowledge engine that debates problems to grounded answers through adversarial reasoning.

## Architecture

- **Proposer Agent**: Presents an initial solution with evidence
- **Critic Agent**: Challenges the proposal, identifies weaknesses
- **Judge Agents**: Multiple judges evaluate arguments, vote on consensus
- **Constitution**: Five-part rule system governing debate behavior
- **Bounded Execution**: Hard round and cost ceilings, step limits per agent

The workflow is a single step: one model call answers the debate, and the
critic and judge agents are declared but not yet asked, so the answer is the
proposer's own.

## Running

```bash
# With scripted answers (fast demonstration)
kosika run main.kos --input debate_input.json --model-fixture debate_responses.json --trace debate_trace.jsonl

# With a live model
kosika run main.kos --input debate_input.json --model YOUR_MODEL

# Replay a trace
kosika replay debate_trace.jsonl
```

## Constitution Rules

1. **Groundedness**: Every claim must cite evidence
2. **Relevance**: Arguments must address the core question
3. **Coherence**: Claims must be logically consistent
4. **Charity**: Interpret opposing arguments fairly
5. **Transparency**: Clearly state uncertainties

## Input Format

The fields sit under the workflow's parameter name, `input`:

```json
{
  "input": {
    "question": "What is the best approach to X?",
    "context": "Additional background information...",
    "max_rounds": 3,
    "max_cost": 0.50
  }
}
```

## Output Format

```json
{
  "answer": "The consensus answer...",
  "confidence": 0.87,
  "reasoning": "Summary of the debate...",
  "evidence": ["key evidence citations..."],
  "rounds": 3,
  "total_cost": 0.42,
  "agreement_score": 0.85
}
```
