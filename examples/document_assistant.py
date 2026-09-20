"""Run the Kosika document assistant with a deterministic fixture or explicit model."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from kosika.ast import ToolDecl
from kosika.local_documents import LocalDocuments
from kosika.model import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    OpenAICompatibleProvider,
)
from kosika.model_steps import strict_json
from kosika.parser import parse
from kosika.runtime import run


class DocumentFixture:
    """Deterministic extractive fixture. This is not an AI model."""

    def complete(self, request: ModelRequest) -> ModelResponse:
        last = request.messages[-1]
        if last.role == "user":
            question = json.loads(last.content)["text"]
            return ModelResponse(
                tool_calls=(
                    ModelToolCall("search-1", "search_docs", {"query": question}),
                )
            )
        if last.role != "tool":
            raise ValueError("Fixture expects a user input or search result")
        hits = json.loads(last.content)["hits"]
        return ModelResponse(
            content=json.dumps(
                {
                    "text": " ".join(hit["excerpt"] for hit in hits)
                    if hits
                    else "No matching documents found.",
                    "sources": sorted({hit["source"] for hit in hits}),
                }
            )
        )


def run_assistant(
    question: str,
    document_root: Path,
    trace: Path,
    provider: ModelProvider | None = None,
) -> dict[str, Any]:
    program = parse(Path(__file__).with_suffix(".syn").read_text(encoding="utf-8"))
    documents = LocalDocuments(document_root)
    retrieved: set[str] = set()

    def search(tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        if (tool.name, tool.source, tool.operation, tool.access) != (
            "search_docs",
            "documents",
            "search",
            "observe",
        ):
            raise PermissionError(
                "Document host only permits the declared read-only search"
            )
        result = documents.search(arguments)
        retrieved.update(hit["source"] for hit in result["hits"])
        return result

    delegate = provider if provider is not None else DocumentFixture()

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

    answer = run(
        program,
        "answer",
        trace,
        inputs={"question": {"text": question}},
        model_bindings={"reader": CitedProvider()},
        tool_bindings={"search_docs": search},
    )
    return answer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default="approval")
    parser.add_argument(
        "--docs-root", type=Path, default=Path(__file__).parent / "documents"
    )
    parser.add_argument("--trace", type=Path, default=Path("document_trace.jsonl"))
    parser.add_argument("--model")
    parser.add_argument("--model-url")
    args = parser.parse_args()
    if bool(args.model) != bool(args.model_url):
        parser.error("Live inference requires both --model and --model-url")
    provider = (
        OpenAICompatibleProvider(
            args.model_url, args.model, api_key=os.environ.get("KOSIKA_MODEL_API_KEY")
        )
        if args.model
        else None
    )
    print(
        "Mode: live model"
        if provider
        else "Mode: deterministic fixture (no AI inference)"
    )
    answer = run_assistant(args.question, args.docs_root, args.trace, provider)
    print(json.dumps(answer, indent=2))
    print(f"Trace: {args.trace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
