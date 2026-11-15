"""Use a Langflow flow as a Kosika model provider.

A `.kos` file names a model such as `writer`; a `--models` file says what
answers for it. One route is a command, and this module is that command for a
Langflow flow:

    {
      "writer": {"command": ["kosika-langflow", "3f0c9d1e-..."]}
    }

Kosika writes the model request to standard input and reads the reply from
standard output, so this prints exactly the text the flow answered with.
Labels in the request are Langflow's input: a flow of Chat Input, a model, and
Chat Output is enough, and the schema Kosika asks for travels with the prompt.

    kosika-langflow FLOW [--url URL] [--timeout SECONDS]

`FLOW` is the flow's id or its endpoint name. The server is
`LANGFLOW_URL` (default http://127.0.0.1:7860). A key in `LANGFLOW_API_KEY`
is sent as `x-api-key`; without one, a Langflow Desktop server that allows
auto-login is asked for a session at `/api/v1/auto_login`. The flow runs with
the authority Langflow gives it, so grant only a server you trust.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator, Mapping

#: Where Langflow listens unless told otherwise.
DEFAULT_URL = "http://127.0.0.1:7860"
#: The longest one flow run may take, under the provisioner's 300 seconds.
DEFAULT_TIMEOUT = 280.0


class LangflowError(Exception):
    """A flow could not be run, or answered with nothing to give back."""


def _post(url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300].strip()
        raise LangflowError(f"Langflow answered {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LangflowError(f"Cannot reach Langflow at {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LangflowError(f"Langflow did not answer {url} within {timeout:g} seconds") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise LangflowError(f"Langflow answered {url} with something other than JSON") from exc


def _get(url: str, headers: Mapping[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300].strip()
        raise LangflowError(f"Langflow answered {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LangflowError(f"Cannot reach Langflow at {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LangflowError(f"Langflow did not answer {url} within {timeout:g} seconds") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise LangflowError(f"Langflow answered {url} with something other than JSON") from exc


def auto_login_token(base_url: str, timeout: float) -> str:
    """A session token from a server that allows auto-login, such as Desktop."""

    payload = _get(f"{base_url.rstrip('/')}/api/v1/auto_login", {}, timeout)
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise LangflowError(
            "Langflow did not give an auto-login session; set LANGFLOW_API_KEY"
        )
    return token


def _texts(value: Any, depth: int = 0) -> Iterator[str]:
    """Every answer text, in the order Langflow wrote them."""

    if depth > 6:
        return
    if isinstance(value, dict):
        results = value.get("results")
        if isinstance(results, dict):
            for key in ("message", "text", "content"):
                found = results.get(key)
                if isinstance(found, str) and found.strip():
                    yield found
                elif isinstance(found, dict) and isinstance(found.get("text"), str):
                    yield found["text"]
        for key in ("outputs", "message"):
            if key in value:
                yield from _texts(value[key], depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item, depth + 1)


def answer_text(payload: Any) -> str:
    """The text a run answered with, from Langflow's nested result shape."""

    for text in _texts(payload):
        if text.strip():
            return text
    raise LangflowError("The flow answered without any text")


def run_flow(
    prompt: str,
    flow: str,
    *,
    base_url: str = DEFAULT_URL,
    api_key: str | None = None,
    session_id: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Run one flow on `prompt` and return the text it answered with."""

    if not prompt.strip():
        raise LangflowError("There is no prompt to send")
    if not flow.strip():
        raise LangflowError("No flow was named")
    base = base_url.rstrip("/")
    headers = {"x-api-key": api_key} if api_key else {}
    if not headers:
        headers = {"Authorization": f"Bearer {auto_login_token(base, timeout)}"}
    body = {
        "input_value": prompt,
        "input_type": "chat",
        "output_type": "chat",
        "session_id": session_id or f"kosika-{os.getpid()}",
    }
    url = f"{base}/api/v1/run/{urllib.parse.quote(flow, safe='')}"
    return answer_text(_post(url, body, headers, timeout))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kosika-langflow",
        description="Answer a Kosika model request with a Langflow flow.",
    )
    parser.add_argument("flow", help="The flow's id or endpoint name.")
    parser.add_argument(
        "--url",
        default=os.environ.get("LANGFLOW_URL", DEFAULT_URL),
        help=f"Where Langflow listens (default {DEFAULT_URL}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("LANGFLOW_TIMEOUT", DEFAULT_TIMEOUT)),
        help=f"Seconds to wait for the flow (default {DEFAULT_TIMEOUT:g}).",
    )
    args = parser.parse_args(argv)
    try:
        answer = run_flow(
            sys.stdin.read(),
            args.flow,
            base_url=args.url,
            api_key=os.environ.get("LANGFLOW_API_KEY") or None,
            timeout=args.timeout,
        )
    except LangflowError as exc:
        print(f"kosika-langflow: {exc}", file=sys.stderr)
        return 1
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
