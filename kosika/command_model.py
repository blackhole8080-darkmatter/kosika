"""Use a command-line model as a Kosika model provider.

Some models are reached through a program rather than an HTTP endpoint, for
example a vendor's signed-in command-line tool. `CommandModelProvider` writes
the request to that program's standard input and reads its reply from standard
output.

The program is named by the person running Kosika, never by a `.kos` file. It
runs without a shell, in an empty temporary folder, so an agentic tool started
this way has no project files in front of it. It receives no tools: a provider
that is asked for tool calling refuses, because a text-only program cannot
return them.

Models often wrap JSON in a Markdown code fence. When the reply is not valid
JSON but contains exactly one fenced block that is, the block's content is
used. Nothing else is repaired: any other reply is passed on as it is and
fails Kosika's ordinary validation.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from typing import Sequence

from .model import ModelProviderError, ModelRequest, ModelResponse

_FENCE = re.compile(r"```[A-Za-z]*[ \t]*\r?\n(.*?)```", re.DOTALL)


def unwrap_json(reply: str) -> str:
    """Return the reply's JSON text, taking it out of a single code fence."""

    text = reply.strip()
    try:
        json.loads(text)
        return text
    except ValueError:
        pass
    blocks = [block.strip() for block in _FENCE.findall(text)]
    valid = []
    for block in blocks:
        try:
            json.loads(block)
            valid.append(block)
        except ValueError:
            continue
    return valid[0] if len(valid) == 1 else text


class CommandModelProvider:
    """Ask a command-line program for each model response."""

    def __init__(self, command: Sequence[str], *, timeout: float = 300.0) -> None:
        if not command:
            raise ValueError("A model command cannot be empty")
        if timeout <= 0:
            raise ValueError("Model timeout must be positive")
        executable = shutil.which(command[0])
        if executable is None:
            raise ValueError(f"Model command {command[0]!r} was not found")
        self.command = [executable, *command[1:]]
        self.timeout = timeout

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.tools:
            raise ModelProviderError(
                "A command-line model cannot be given tools; use an HTTP provider "
                "for agents that call tools"
            )
        prompt = "\n\n".join(
            f"[{message.role}]\n{message.content}" for message in request.messages
        )
        with tempfile.TemporaryDirectory(prefix="kosika-model-") as directory:
            try:
                completed = subprocess.run(
                    self.command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                    cwd=directory,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ModelProviderError(
                    f"Model command ran longer than {self.timeout:g} seconds"
                ) from exc
            except OSError as exc:
                raise ModelProviderError(
                    f"Model command failed to start: {exc}"
                ) from exc
        if not completed.stdout.strip():
            detail = completed.stderr.strip()[-300:] or "no output"
            raise ModelProviderError(
                f"Model command exited {completed.returncode} without a reply: {detail}"
            )
        return ModelResponse(content=unwrap_json(completed.stdout))
