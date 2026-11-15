"""Sending a note to the person, at the one address they named.

A program asks for it by declaring a tool with a `system "notify"` source:

    tool tell_me {
      source system "notify"
      operation: "send"
      access: observe
      input: Note          // {title, text, link, file_name, file}
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
most 4,000 bytes of text are sent; a longer text is cut and says so.

Two things can ride along, both optional, both empty for a plain note:

* `link`, one http(s) address, sent as the note's `Click`: tapping the note
  on the phone opens it, so a note about a paper can carry the paper.
* `file_name` and `file`, one text file sent as the note's attachment (both
  together, or neither). The words of the note move into the request's
  message parameter and the file becomes the body, so the note still reads
  on the phone and carries its page with it. A file past 512 KB is not
  attached; the note says so instead.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from .ast import ToolDecl

MAX_NOTE_BYTES = 4000
#: The largest page sent as one note's attachment.
MAX_PAGE_BYTES = 512 * 1024
TIMEOUT_SECONDS = 20
MAX_LINK_CHARS = 2048
MAX_NAME_CHARS = 100

#: (url, headers, body) -> status. Raises nothing for an answered request.
Sender = Callable[[str, dict[str, str], bytes], int]


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


def _send(url: str, headers: dict[str, str], body: bytes) -> int:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NotifyError(f"Could not reach {urlsplit(url).hostname}: {exc}") from exc


def clean_link(link: str) -> str:
    """The address tapping the note opens: one http(s) address with a host,
    no credentials, no spaces; "" when there is none."""

    text = link.strip()
    if not text:
        return ""
    if len(text) > MAX_LINK_CHARS or any(character.isspace() for character in text):
        raise NotifyError(
            f"notify link {link!r} must be one short http(s) address, like https://arxiv.org/abs/2610.23850"
        )
    parts = urlsplit(text)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise NotifyError(
            f"notify link {link!r} must be one http(s) address, like https://arxiv.org/abs/2610.23850"
        )
    return text


def clean_file_name(name: str) -> str:
    """A file name with no path in it: letters, digits, dot, dash, underscore."""

    cleaned = "".join(
        character
        if character.isascii() and (character.isalnum() or character in "._-")
        else "-"
        for character in name.strip()
    )[:MAX_NAME_CHARS]
    if not cleaned or cleaned in (".", ".."):
        raise NotifyError(f"notify file name {name!r} is not a file name")
    return cleaned


def bind_notify_tool(address: str, sender: Sender = _send) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    """The executor of a notify tool, sending only to `address`."""

    url = clean_address(address)

    def send(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        title = " ".join(str(arguments["title"]).split())[:200]
        text = str(arguments["text"])
        link = clean_link(str(arguments.get("link", "")))
        file_name = str(arguments.get("file_name", "")).strip()
        page = str(arguments.get("file", ""))
        if bool(file_name) != bool(page):
            raise NotifyError(
                "A note's attached file needs a file_name too; give both, or neither for a plain note"
            )
        attachment = page.encode("utf-8") if file_name else b""
        if file_name and len(attachment) > MAX_PAGE_BYTES:
            attachment = b""
            text += "\n[the page was too big to attach]"
        data = text.encode("utf-8")
        if len(data) > MAX_NOTE_BYTES:
            cut = data[: MAX_NOTE_BYTES - 40].decode("utf-8", errors="ignore")
            data = (cut + "\n[cut: the rest is on the computer]").encode("utf-8")
            text = data.decode("utf-8")
        headers = {
            "Title": title.encode("ascii", "replace").decode("ascii"),
            "Content-Type": "text/plain; charset=utf-8",
        }
        if link:
            headers["Click"] = link
        where = url
        if attachment:
            headers["Filename"] = clean_file_name(file_name)
            headers["Content-Type"] = "text/html; charset=utf-8"
            # The body is the file now, so the words of the note travel as
            # the message parameter; ntfy turns each written \n back into a
            # line, and the parameter carries every character of the text.
            where = url + "?message=" + quote(text.replace("\n", "\\n"), safe="")
            body = attachment
        else:
            body = data
        status = sender(where, headers, body)
        if not 200 <= status < 300:
            raise NotifyError(f"The note was not taken: {urlsplit(url).hostname} answered {status}")
        return {"sent": True}

    return send
