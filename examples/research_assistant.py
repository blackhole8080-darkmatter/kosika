"""Run the deterministic Kosika research assistant."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from kosika.ast import ToolDecl
from kosika.local_documents import LocalDocuments
from kosika.model import ModelProvider, ModelRequest, ModelResponse, ModelToolCall
from kosika.model_steps import strict_json
from kosika.parser import parse
from kosika.runtime import run


class ResearchFixture:
    """Deterministic model fixture that searches before answering."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer

    def complete(self, request: ModelRequest) -> ModelResponse:
        last = request.messages[-1]
        if last.role == "user":
            question = json.loads(last.content)["text"]
            return ModelResponse(
                tool_calls=(
                    ModelToolCall("search-1", "search_docs", {"query": question}),
                )
            )
        if last.role == "tool":
            return ModelResponse(content=json.dumps(self.answer))
        raise ValueError("Research fixture expects a user input or search result")


def run_research(
    question: str,
    document_root: Path,
    trace: Path,
    fixture: dict[str, Any],
    provider: ModelProvider | None = None,
) -> dict[str, Any]:
    """Run the research workflow with a deterministic or supplied provider."""
    source = Path(__file__).with_suffix(".kos").read_text(encoding="utf-8")
    program = parse(source)
    documents = LocalDocuments(document_root)
    retrieved: set[str] = set()

    def search(tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        if (tool.name, tool.source, tool.operation, tool.access) != (
            "search_docs",
            "research_documents",
            "search",
            "observe",
        ):
            raise PermissionError(
                "Research host only permits the declared read-only search"
            )
        result = documents.search(arguments)
        retrieved.update(hit["source"] for hit in result["hits"])
        return result

    delegate = provider or ResearchFixture(fixture["reader"][0])

    class CitedProvider:
        def complete(self, request: ModelRequest) -> ModelResponse:
            response = delegate.complete(request)
            if not response.tool_calls:
                candidate = strict_json(response.content or "")
                sources = (
                    candidate.get("sources") if isinstance(candidate, dict) else None
                )
                if not isinstance(sources, list) or not all(
                    isinstance(source, str) for source in sources
                ):
                    raise ValueError("Answer requires a list of source filenames")
                if not set(sources) <= retrieved:
                    raise ValueError("Answer cites a source that was not retrieved")
            return response

    return run(
        program,
        "answer",
        trace,
        inputs={"question": {"text": question}},
        model_bindings={"reader": CitedProvider()},
        tool_bindings={"search_docs": search},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).with_name("research_input.json"),
    )
    parser.add_argument(
        "--documents",
        type=Path,
        default=Path(__file__).with_name("research_documents"),
    )
    parser.add_argument(
        "--model-fixture",
        type=Path,
        default=Path(__file__).with_name("research_model_answers.json"),
    )
    parser.add_argument("--trace", type=Path, default=Path("research_trace.jsonl"))
    args = parser.parse_args()

    inputs = json.loads(args.input.read_text(encoding="utf-8"))
    question = inputs["question"]["text"]
    fixture = json.loads(args.model_fixture.read_text(encoding="utf-8"))
    answer = run_research(question, args.documents, args.trace, fixture)
    print(json.dumps(answer, indent=2))
    print(f"Trace: {args.trace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
