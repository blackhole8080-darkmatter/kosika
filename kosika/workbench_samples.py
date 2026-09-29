"""Self-contained workbench examples. No credentials or live providers."""

SAMPLES = [
    {
        "id": "research",
        "name": "Research pipeline",
        "description": "Compose typed workflows across three modules.",
        "tag": "Modules",
        "entry": "main.kos",
        "workflow": "main",
        "files": {
            "main.kos": '// A small workflow. A clear contract.\nimport "types.kos"\nimport "research.kos"\n\nworkflow main(question: Question) -> Answer {\n  step investigate {\n    let answer: Answer = call research(\n      question: question\n    )\n    return answer\n  }\n}\n',
            "types.kos": "record Question {\n  text: Text\n}\n\nrecord Answer {\n  text: Text\n  confidence: Number\n}\n\nexport [Question, Answer]\n",
            "research.kos": 'import "types.kos"\n\nmodel reader {\n  input: Question\n  output: Answer\n}\n\nworkflow research(question: Question) -> Answer {\n  step respond {\n    let result: Answer = use_model reader(\n      text: question.text\n    )\n    return result\n  }\n}\n\nexport [research]\n',
        },
        "inputs": {"question": {"text": "What makes a reliable AI workflow?"}},
        "fixtures": {
            "reader": [
                {
                    "text": "Explicit capabilities, typed boundaries, and a trace you can replay.",
                    "confidence": 0.98,
                }
            ]
        },
    },
    {
        "id": "model",
        "name": "Your first model",
        "description": "Turn a question into a validated record.",
        "tag": "Getting started",
        "entry": "main.kos",
        "workflow": "main",
        "files": {
            "main.kos": "record Question { text: Text }\nrecord Answer { text: Text }\n\nmodel writer {\n  input: Question\n  output: Answer\n}\n\nworkflow main(question: Question) -> Answer {\n  step answer {\n    let result: Answer = use_model writer(\n      text: question.text\n    )\n    return result\n  }\n}\n"
        },
        "inputs": {"question": {"text": "Say hello to Kosika."}},
        "fixtures": {
            "writer": [{"text": "Hello, Kosika. Let's build something thoughtful."}]
        },
    },
    {
        "id": "tools",
        "name": "A tool with boundaries",
        "description": "Grant one read-only, deterministic mock tool.",
        "tag": "Capabilities",
        "entry": "main.kos",
        "workflow": "main",
        "files": {
            "main.kos": 'record DiskStatus {\n  mount: Text\n  used_percent: Number\n}\n\ntool check_disk {\n  source mcp "local_mock"\n  operation: "get_disk_info"\n  access: observe\n  output: DiskStatus\n}\n\nagent monitor {\n  can: [check_disk]\n  cannot: [write_files, arbitrary_shell]\n}\n\nworkflow main() -> DiskStatus {\n  step inspect {\n    let status: DiskStatus = execute monitor.check_disk()\n    return status\n  }\n}\n'
        },
        "inputs": {},
        "fixtures": {},
    },
    {
        "id": "agent",
        "name": "A bounded agent",
        "description": "Give an agent a typed model and a step limit.",
        "tag": "Agents",
        "entry": "main.kos",
        "workflow": "main",
        "files": {
            "main.kos": "record Question { text: Text }\nrecord Answer { text: Text }\n\nmodel reader {\n  input: Question\n  output: Answer\n}\n\nagent researcher {\n  model: reader\n  can: []\n  cannot: [arbitrary_shell]\n  max_steps: 4\n}\n\nworkflow main(question: Question) -> Answer {\n  step research {\n    let answer: Answer = ask researcher(\n      text: question.text\n    )\n    return answer\n  }\n}\n"
        },
        "inputs": {"question": {"text": "Explain an agent's capability boundary."}},
        "fixtures": {
            "reader": [
                {
                    "text": "An agent can only call tools that its declaration and host both permit."
                }
            ]
        },
    },
]

SAMPLES.append(
    {
        "id": "document",
        "name": "Document assistant",
        "description": "Turn supplied document text into a typed summary. No file access or writes.",
        "tag": "Application",
        "entry": "main.kos",
        "workflow": "main",
        "files": {
            "main.kos": """// Supply text explicitly. This workflow has no filesystem capability.
record Document { text: Text }
record Summary { summary: Text }
model summarizer { input: Document output: Summary }
workflow main(document: Document) -> Summary {
  step summarize {
    let result: Summary = use_model summarizer(text: document.text)
    return result
  }
}
"""
        },
        "inputs": {
            "document": {
                "text": "Summarize this document: Kosika uses typed records, explicit capabilities, and replayable workflows to build AI applications."
            }
        },
        "fixtures": {
            "summarizer": [
                {
                    "summary": "Kosika combines typed data, capability boundaries, and replay for AI applications."
                }
            ]
        },
    }
)

SAMPLES.append(
    {
        "id": "closures",
        "name": "Nested functions",
        "description": "Inner fn helpers capture outer variables; no model, no tools.",
        "tag": "Functions",
        "entry": "main.kos",
        "workflow": "main",
        "files": {
            "main.kos": "// Nested pure functions. Inner fn helpers capture outer variables.\n"
            "record Total {\n"
            "  value: Number\n"
            "}\n"
            "\n"
            "fn calculate(base: Number) -> Total {\n"
            "  let adjustment: Number = 2\n"
            "\n"
            "  fn adjusted() -> Number {\n"
            "    return base + adjustment\n"
            "  }\n"
            "\n"
            "  fn scale(factor: Number) -> Number {\n"
            "    return adjusted() * factor\n"
            "  }\n"
            "\n"
            "  return {value: scale(factor: 3)}\n"
            "}\n"
            "\n"
            "workflow main() -> Total {\n"
            "  step calculate_total {\n"
            "    let total = calculate(base: 10)\n"
            "    return total\n"
            "  }\n"
            "}\n",
        },
        "inputs": {},
        "fixtures": {},
    },
)
