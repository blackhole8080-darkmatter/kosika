"""Ask twenty real questions through the served profile and count citations.

The question is not whether a 7B *can* cite; it is whether, asked plainly and
given the grounding rule, it cites at all, and whether the lines it names are
real. Every answer goes through the gateway a client would use -- the CLI's
`virtual-model serve` behind `POST /v1/chat/completions` -- so the rule, the
refusal check and the journal are the shipped ones, not a test double.

Writes one JSON line per answer as it arrives, so a stopped run keeps what it
paid for, and a summary at the end. Run from the repository root:

    python examples/agents/designs/bench/stress/citation_probe.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
PROFILE = ROOT / "docs/specialists/selfdev.json"
PORT = 8829
KEY = "cite-probe-key"
#: A second run must not overwrite the first one's evidence; set CITATION_OUT.
OUT = Path(os.environ.get("CITATION_OUT") or ROOT / ".kosika/selfdev/citations")
STATE = OUT / "journal.jsonl"
ANSWERS = OUT / "answers.jsonl"

SUFFIX = (
    "\n\nAnswer in at most 100 words. For each claim, cite the file and line "
    "as [path:line]."
)

QUESTIONS = [
    "What must every Kosika model call declare before it runs?",
    "How does a Kosika workflow bound the number of model calls?",
    "What does `kosika check` do when a model input type does not match?",
    "What is a virtual-model profile's `max_context_chars` for?",
    "How does `kosika selfdev` decide whether a change is kept?",
    "Which files does the selfdev ledger record?",
    "What happens when a selfdev attempt does not compile?",
    "What does the checker say about duplicate model fields?",
    "How are `agent` and `model` declarations different?",
    "What is the purpose of the `sources` field in a specialist profile?",
    "How does Kosika keep a model's answer from being trusted as fact?",
    "What is a Kosika `route` and when is one refused?",
    "How does a Kosika workflow declare a budget?",
    "What does `kosika run` do when the checker fails?",
    "How do verified examples get into a virtual-model profile?",
    "What is the diagnostic code for an unknown field?",
    "How does the conformance suite compare the Python and Go engines?",
    "What does `kosika virtual-model usage` report?",
    "What happens to a cached answer after the answer age expires?",
    "Which command trains a ranker for a virtual-model profile?",
]

# `[path:line]` as asked for, and a bare `path.ext:line` a model sometimes
# writes instead. Both are citations, so both are counted and resolved.
BRACKETED = re.compile(r"\[\s*([A-Za-z0-9_./\\-]+?)\s*:\s*(\d+)\s*\]")
BARE = re.compile(
    r"(?<![\w/\\[])([A-Za-z0-9_./\\-]+\.(?:py|md|kos|json|toml|txt|cfg|ini|yaml|yml))"
    r"\s*:\s*(\d+)"
)


def start_server() -> subprocess.Popen:
    OUT.mkdir(parents=True, exist_ok=True)
    for path in (STATE, ANSWERS):
        if path.exists():
            path.unlink()
    command = [
        sys.executable,
        "-u",
        "-m",
        "kosika.cli",
        "virtual-model",
        "serve",
        str(PROFILE),
        "--model",
        "qwen2.5-coder:7b",
        "--protocol",
        "ollama",
        "--port",
        str(PORT),
        "--calls-per-day",
        "40",
        "--tokens-per-day",
        "400000",
        "--tokens-per-call",
        "4000",
        "--model-timeout",
        "1500",
        "--state",
        str(STATE),
        "--key",
        f"editor={KEY}",
        "--warm",
    ]
    log = (OUT / "serve.log").open("w", encoding="utf-8")
    return subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)


def ready(timeout: float = 400.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/v1/models", timeout=2
            ) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1.0)
    return False


def ask(question: str) -> dict:
    body = json.dumps(
        {
            "model": "kosika-selfdev",
            "messages": [{"role": "user", "content": question + SUFFIX}],
            "max_tokens": 256,
            "temperature": 0,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=1500) as response:
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
    except urllib.error.HTTPError as error:
        payload = {"error": error.read().decode("utf-8", "replace")}
        status = error.code
    return {
        "question": question,
        "status": status,
        "seconds": round(time.monotonic() - started, 1),
        "content": (payload.get("choices") or [{}])[0].get("message", {}).get("content"),
        "kosika": payload.get("kosika"),
        "usage": payload.get("usage"),
        "error": payload.get("error"),
    }


def citations_in(text: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for pattern in (BRACKETED, BARE):
        for match in pattern.finditer(text or ""):
            item = (match.group(1), int(match.group(2)))
            if item not in found:
                found.append(item)
    return found


def analyse(rows: list[dict]) -> dict:
    from kosika.model import ScriptedModelProvider
    from kosika.virtual_model import VirtualModelProvider

    specialist = VirtualModelProvider(ScriptedModelProvider([]), PROFILE)
    # The gateway writes the refusal verdict to the journal, not to the answer,
    # so this is where a refusal is counted from.
    refused = 0
    if STATE.exists():
        for line in STATE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                refused += 1 if json.loads(line).get("refused") else 0
    cited = 0
    total = 0
    resolved = 0
    bad: list[dict] = []
    for row in rows:
        text = row.get("content") or ""
        found = citations_in(text)
        if found:
            cited += 1
        for source, line in found:
            total += 1
            if specialist.line_at(source, line) is not None:
                resolved += 1
            else:
                bad.append({"question": row["question"], "citation": f"{source}:{line}"})
    return {
        "answers": len(rows),
        "answers_with_a_citation": cited,
        "answers_without_a_citation": len(rows) - cited,
        "citations": total,
        "citations_resolved": resolved,
        "citations_unresolved": total - resolved,
        "answers_whose_citations_all_resolve": sum(
            1
            for row in rows
            if citations_in(row.get("content") or "")
            and all(
                specialist.line_at(source, line) is not None
                for source, line in citations_in(row.get("content") or "")
            )
        ),
        "refused": refused,
        "unresolved": bad,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ANSWERS.unlink(missing_ok=True)
    server = start_server()
    try:
        if not ready():
            print("server never answered /v1/models; see", OUT / "serve.log")
            return 1
        print(f"ready after warm-up; asking {len(QUESTIONS)} questions", flush=True)
        rows: list[dict] = []
        with ANSWERS.open("a", encoding="utf-8") as handle:
            for index, question in enumerate(QUESTIONS, 1):
                row = ask(question)
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                cited = len(citations_in(row.get("content") or ""))
                print(
                    f"{index:>2}/{len(QUESTIONS)} {row['seconds']:>6.1f}s "
                    f"status={row['status']} citations={cited} "
                    f"refused={(row.get('kosika') or {}).get('refused')}",
                    flush=True,
                )
        summary = analyse(rows)
        summary["seconds_total"] = round(sum(row["seconds"] for row in rows), 1)
        (OUT / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
        print("server stopped", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
