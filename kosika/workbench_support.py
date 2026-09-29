"""Workbench presentation metadata and explicitly granted local inference."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .ast import Program
from .controls import ControlledProvider, ModelLimits
from .model import ModelProviderError, OpenAICompatibleProvider
from .schema import schema_for_record


def describe_inputs(program: Program, workflow: str) -> dict[str, Any]:
    selected = next(item for item in program.workflows if item.name == workflow)
    return {
        "type": "object",
        "properties": {
            field.name: schema_for_record(program, field.type_name)
            for field in selected.parameters
        },
        "required": [field.name for field in selected.parameters],
        "additionalProperties": False,
    }


def diagnostic(message: str) -> dict[str, Any]:
    """Only report exact locations supplied by the parser, never guessed lines."""
    location = re.search(r"(?:^|\s)([A-Za-z0-9_/-]+\.kos): .*?at (\d+):(\d+)", message)
    result: dict[str, Any] = {
        "message": message,
        "hint": "Check declared record types, capabilities, and model responses.",
    }
    if location:
        result.update(
            file=location[1],
            line=int(location[2]),
            column=int(location[3]),
            hint="Open the source location, correct the syntax, then check again.",
        )
    if "Local model" in message:
        result["hint"] = (
            "Check the local model server and its logs, or select Fixtures to continue offline."
        )
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        fp.close()
        raise ModelProviderError("Local model redirects are not permitted.")


@dataclass(frozen=True)
class LiveConfig:
    base_url: str
    model: str

    def __post_init__(self) -> None:
        url = urlsplit(self.base_url)
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path.rstrip("/") != "/v1"
            or not url.port
        ):
            raise ValueError(
                "Live inference requires http://127.0.0.1:PORT/v1 with no credentials or query."
            )
        if (
            not isinstance(self.model, str)
            or not self.model.strip()
            or len(self.model) > 200
        ):
            raise ValueError("A host-configured model name is required.")

    def public(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "endpoint": self.base_url,
            "max_requests": 4,
            "max_output_tokens": 1024,
            "timeout_seconds": 20,
            "deadline_seconds": 60,
        }

    def provider(self) -> ControlledProvider:
        endpoint = self.base_url.rstrip("/") + "/chat/completions"

        def transport(request: urllib.request.Request, timeout: float) -> bytes:
            if request.full_url != endpoint:
                raise ModelProviderError("Unexpected local model endpoint.")
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), NoRedirect()
            )
            try:
                with opener.open(request, timeout=timeout) as response:
                    body = response.read(262145)
                if len(body) > 262144:
                    raise ModelProviderError("Local model response exceeds 256 KiB.")
                return body
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
                raise ModelProviderError(f"Local model returned HTTP {code}.") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise ModelProviderError(
                    "Local model unavailable or timed out (20-second request timeout)."
                ) from None

        return ControlledProvider(
            OpenAICompatibleProvider(
                self.base_url, self.model, timeout=20, transport=transport
            ),
            ModelLimits(max_requests=4, max_output_tokens=1024, deadline_seconds=60),
        )
