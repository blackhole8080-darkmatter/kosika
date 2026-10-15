"""Reading web pages, from sites the person running the program chose.

A program asks for the web by declaring a tool with a `system "web"` source:

    tool read_page {
      source system "web"
      operation: "fetch"
      access: observe
      input: PageRequest      // {url: Text}
      output: Page            // {url: Text, title: Text, text: Text}
    }

Declaring it reaches nothing. The person running the program names the sites
(`--allow-site en.wikipedia.org`, repeatable), and a fetch is refused unless
the address is on one of them.

What a fetch is, exactly:

* One GET of an `https` address on the standard port, to a named host that is
  an allowed site or a subdomain of one. No other scheme, no IP address, no
  user name or password in the address.
* Nothing is sent but the request itself: no cookies, no login, no form, no
  body. It cannot post, submit, or sign in to anything.
* A redirect is followed only to another allowed address, at most five times.
* At most 2 MiB is read, only `text/html`, `text/plain` or a feed (Atom or
  RSS, such as arXiv's lists of new papers), within 20 seconds.
* The page is reduced to its readable text, at most 20,000 characters, with
  scripts, styles, and markup removed. The address actually read is returned
  with it, so an answer can say where it came from.

A web page is written by a stranger and may contain text aimed at a model
("ignore your instructions and ..."). Kosika treats fetched text as data: it
is a `Text` value like any other, and a model that reads it in a thinking
step can only return that step's typed answer. It cannot call a tool, fetch
another page, or change anything because of what a page said. It can still
be fooled into a wrong answer, which is why the allowed sites are the
runner's choice and should be ones they trust.
"""

from __future__ import annotations

import html.parser
import ipaddress
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence
from urllib.parse import urljoin, urlsplit

from .ast import ToolDecl

MAX_BYTES = 2 * 1024 * 1024
MAX_CHARACTERS = 20_000
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 20
USER_AGENT = "Kosika/0.1 (read-only page fetch for a local assistant)"

#: (status, headers, body). Raises nothing for 3xx; returns it to be followed.
Opener = Callable[[str], tuple[int, dict[str, str], bytes]]


class WebError(ValueError):
    """A page cannot be fetched, or fetching it is not allowed."""


def clean_site(site: str) -> str:
    """A site as the runner may write it, reduced to a bare host name."""

    host = site.strip().lower()
    host = re.sub(r"^https?://", "", host).split("/")[0].rstrip(".")
    if (
        not host
        or ":" in host
        or "@" in host
        or not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", host)
        or "." not in host
    ):
        raise WebError(f"{site!r} is not a site name such as en.wikipedia.org")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise WebError(f"{site!r} is an address, not a site name")


def allowed_address(url: str, sites: Sequence[str]) -> str:
    """Return `url` if it may be fetched, else raise saying why not."""

    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise WebError(f"{url!r} is not a web address") from exc
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https":
        raise WebError(f"{url!r} must start with https://")
    if parts.username or parts.password or port not in (None, 443) or not host:
        raise WebError(f"{url!r} is not a plain https address")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise WebError(f"{url!r} names an address, not a site")
    if not any(host == site or host.endswith("." + site) for site in sites):
        allowed = ", ".join(sites) or "none"
        raise WebError(f"{host} is not one of the allowed sites ({allowed})")
    return parts.geturl()


class _Text(html.parser.HTMLParser):
    """The readable text of a page: no scripts, styles, menus, or markup."""

    # Not part of what a page says: code, and the menus around the content.
    _SKIP = frozenset(
        "script style noscript template svg head nav header footer aside "
        "form button select".split()
    )
    #: Where a page keeps what it is about, when it says so.
    _MAIN = frozenset(("main", "article"))
    _BREAK = frozenset(
        "p br div li tr h1 h2 h3 h4 h5 h6 section blockquote pre table ul ol".split()
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.main: list[str] = []
        self.title: list[str] = []
        self._skipping = 0
        self._in_main = 0
        self._in_title = False

    def _add(self, text: str) -> None:
        self.parts.append(text)
        if self._in_main:
            self.main.append(text)

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in self._SKIP:
            self._skipping += 1
        elif tag in self._MAIN:
            self._in_main += 1
            self._add("\n")
        elif tag in self._BREAK:
            self._add("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self._SKIP and self._skipping:
            self._skipping -= 1
        elif tag in self._MAIN and self._in_main:
            self._in_main -= 1
        elif tag in self._BREAK:
            self._add("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
        elif not self._skipping:
            self._add(data)


#: Feeds: what a site lists as new, such as arXiv's newest papers.
FEED_TYPES = (
    "application/atom+xml",
    "application/rss+xml",
    "application/xml",
    "text/xml",
)


class _Feed(html.parser.HTMLParser):
    """A feed as plain lines: each entry begins with `---`, then one line for
    each of its title, date, authors, link and summary that it has."""

    _ENTRY = frozenset(("entry", "item"))
    _LABELS = {
        "title": "Title",
        "published": "Published",
        "pubdate": "Published",
        "updated": "Updated",
        "name": "Author",
        "summary": "Summary",
        "description": "Summary",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._entry = 0
        self._label: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._ENTRY:
            self._entry += 1
            self.lines.append("---")
        elif self._entry and tag in self._LABELS:
            self._label, self._text = self._LABELS[tag], []
        elif self._entry and tag == "link":
            found = dict(attrs)
            if found.get("href") and found.get("rel", "alternate") == "alternate":
                self.lines.append(f"Link: {found['href']}")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ENTRY and self._entry:
            self._entry -= 1
        elif self._label is not None and self._LABELS.get(tag) == self._label:
            said = " ".join("".join(self._text).split())
            if said:
                self.lines.append(f"{self._label}: {said}")
            self._label = None

    def handle_data(self, data: str) -> None:
        if self._label is not None:
            self._text.append(data)


def readable_feed(body: str) -> str:
    """The entries of an Atom or RSS feed, one labelled line each."""

    parser = _Feed()
    parser.feed(body)
    return "\n".join(parser.lines)[:MAX_CHARACTERS]


def _tidy(parts: list[str]) -> str:
    lines = [" ".join(line.split()) for line in "".join(parts).splitlines()]
    return "\n".join(line for line in lines if line)


def readable(body: str, is_html: bool) -> tuple[str, str]:
    """(title, text) of a page body."""

    if not is_html:
        return "", body.strip()[:MAX_CHARACTERS]
    parser = _Text()
    parser.feed(body)
    main = _tidy(parser.main)
    # A page that marks its content gets just that; others get everything.
    text = main if len(main) >= 200 else _tidy(parser.parts)
    return " ".join("".join(parser.title).split()), text[:MAX_CHARACTERS]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # every redirect comes back to be checked first


def _open(url: str) -> tuple[int, dict[str, str], bytes]:
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html, text/plain, application/atom+xml, application/rss+xml",
        },
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return (
                response.status,
                {key.lower(): value for key, value in response.headers.items()},
                response.read(MAX_BYTES + 1),
            )
    except urllib.error.HTTPError as error:
        return (
            error.code,
            {key.lower(): value for key, value in error.headers.items()},
            b"",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise WebError(f"Could not reach {url}: {exc}") from exc


def fetch(url: str, sites: Sequence[str], opener: Opener = _open) -> dict[str, str]:
    """Read one page from an allowed site as `{url, title, text}`."""

    current = allowed_address(url, sites)
    for _ in range(MAX_REDIRECTS + 1):
        status, headers, body = opener(current)
        if status in (301, 302, 303, 307, 308):
            target = headers.get("location")
            if not target:
                raise WebError(f"{current} redirected to nowhere")
            current = allowed_address(urljoin(current, target), sites)
            continue
        if status != 200:
            raise WebError(f"{current} answered with status {status}")
        kind = headers.get("content-type", "").split(";")[0].strip().lower()
        if kind not in ("text/html", "text/plain", "application/xhtml+xml", *FEED_TYPES):
            raise WebError(f"{current} is {kind or 'of unknown type'}, not a text page")
        if len(body) > MAX_BYTES:
            raise WebError(f"{current} is larger than 2 MiB")
        match = re.search(r"charset=([\w-]+)", headers.get("content-type", ""))
        try:
            decoded = body.decode(
                match.group(1) if match else "utf-8", errors="replace"
            )
        except LookupError:
            decoded = body.decode("utf-8", errors="replace")
        if kind in FEED_TYPES:
            title, text = "", readable_feed(decoded)
        else:
            title, text = readable(decoded, kind != "text/plain")
        return {"url": current, "title": title, "text": text}
    raise WebError(f"{url} redirected more than {MAX_REDIRECTS} times")


def bind_web_tool(
    sites: Sequence[str], opener: Opener = _open
) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    allowed = tuple(clean_site(site) for site in sites)

    def run(_tool: ToolDecl, arguments: dict[str, Any]) -> Any:
        return fetch(arguments["url"], allowed, opener)

    return run
