"""Sending a note to the person, at the one address they named.

A program asks for it by declaring a tool with a `system "notify"` source:

    tool tell_me {
      source system "notify"
      operation: "send"
      access: observe
      input: Note          // {title: Text, text: Text}
      output: Sent         // {sent: Bool}
    }

Declaring it reaches nothing. The person running the program names one
address (`--notify https://ntfy.sh/SOME-TOPIC`), and every note goes there
and nowhere else: the program cannot choose where a note goes, only what it
says. Like a memory folder, the address is a place the person set aside for
exactly this, so a note needs no approval each time; that is what lets a
program that runs by itself at a set hour send what it found.

A note is one POST of plain text to that address, with the title in a
`Title` header, as ntfy (https://ntfy.sh) and services like it take it. At
most 4,000 bytes are sent; a longer text is cut and says so.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import urlsplit

from .ast import ToolDecl

MAX_NOTE_BYTES = 4000
TIMEOUT_SECONDS = 20

#: (url, title, body) -> status. Raises nothing for an answered request.
Sender = Callable[[str, str, bytes], int]


class NotifyError(ValueError):
    """A note cannot be sent, or the address cannot be used."""


def clean_address(address: str) -> str:
    """The address notes go to: one https address with a host and a path,
    no user name or password, no query."""

    parts = urlsplit(address.strip())
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or parts.query
        or parts.fragment
        or parts.path in ("", "/")
    ):
        raise NotifyError(
            f"notify address {address!r} must be one https address with a path, "
            "such as https://ntfy.sh/your-private-topic"
        )
    return address.strip()


def _send(url: str, title: str, body: bytes) -> int:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Title": title.encode("ascii", "replace").decode("ascii"), "Content-Type": "text/plain; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NotifyError(f"Could not reach {urlsplit(url).hostname}: {exc}") from exc


def bind_notify_tool(address: str, sender: Sender = _send) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    """The executor of a notify tool, sending only to `address`."""

    url = clean_address(address)

    def send(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        title = " ".join(str(arguments["title"]).split())[:200]
        data = str(arguments["text"]).encode("utf-8")
        if len(data) > MAX_NOTE_BYTES:
            cut = data[: MAX_NOTE_BYTES - 40].decode("utf-8", errors="ignore")
            data = (cut + "\n[cut: the rest is on the computer]").encode("utf-8")
        status = sender(url, title, data)
        if not 200 <= status < 300:
            raise NotifyError(f"The note was not taken: {urlsplit(url).hostname} answered {status}")
        return {"sent": True}

    return send
