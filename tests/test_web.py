"""Reading web pages, only from sites the runner chose."""

from __future__ import annotations

import unittest
from typing import Any

from kosika.checker import CheckError, check
from kosika.parser import parse
from kosika.runtime import run
from kosika.system_tools import SystemToolError, bind_system_tools
from kosika.web import (
    MAX_BYTES,
    MAX_CHARACTERS,
    fetch_from,
    WebError,
    allowed_address,
    clean_site,
    fetch,
    readable,
    readable_feed,
)

SITES = ("en.wikipedia.org", "example.org")

PAGE = (
    b"""<html><head><title> A  Page </title><style>p{}</style>
<script>steal()</script></head><body>
<nav><a>Menu</a><a>Log in</a></nav>
<header>Site header</header>
<main><h1>Tea</h1><p>Tea is a   drink.</p><p>It is &amp; was popular.</p>
<aside>Advert</aside><form><button>Subscribe</button></form>
"""
    + b"<p>More about tea and how it is made, grown, and served around the world.</p>"
    * 4
    + b"""
</main><footer>Footer links</footer></body></html>"""
)


def opener(pages: dict[str, tuple[int, dict[str, str], bytes]]):
    asked: list[str] = []

    def open_(url: str) -> tuple[int, dict[str, str], bytes]:
        asked.append(url)
        return pages[url]

    open_.asked = asked  # type: ignore[attr-defined]
    return open_


def html(body: bytes = PAGE) -> tuple[int, dict[str, str], bytes]:
    return 200, {"content-type": "text/html; charset=utf-8"}, body


class AddressTests(unittest.TestCase):
    def test_an_allowed_site_and_its_subdomains_pass(self) -> None:
        for url in (
            "https://en.wikipedia.org/wiki/Tea",
            "https://EN.Wikipedia.org/wiki/Tea",
            "https://sub.example.org/a?b=1",
            "https://example.org:443/",
        ):
            with self.subTest(url=url):
                allowed_address(url, SITES)

    def test_everything_else_is_refused(self) -> None:
        for url in (
            "http://en.wikipedia.org/wiki/Tea",
            "ftp://example.org/x",
            "https://evil.com/",
            "https://en.wikipedia.org.evil.com/",
            "https://notexample.org/",
            "https://user:pw@example.org/",
            "https://example.org:8443/",
            "https://127.0.0.1/",
            "https://[::1]/",
            "https://localhost/",
            "file:///C:/Windows/win.ini",
            "example.org/page",
            "",
        ):
            with self.subTest(url=url), self.assertRaises(WebError):
                allowed_address(url, SITES)

    def test_a_site_is_a_bare_host_name(self) -> None:
        self.assertEqual(
            clean_site(" https://En.Wikipedia.org/wiki/x "), "en.wikipedia.org"
        )
        for bad in ("", "localhost", "10.0.0.1", "a b.com", "user@x.com", "x.com:80"):
            with self.subTest(site=bad), self.assertRaises(WebError):
                clean_site(bad)


class FetchTests(unittest.TestCase):
    def test_a_page_becomes_its_readable_text(self) -> None:
        page = fetch(
            "https://example.org/tea",
            SITES,
            opener({"https://example.org/tea": html()}),
        )
        self.assertEqual(page["url"], "https://example.org/tea")
        self.assertEqual(page["title"], "A Page")
        self.assertTrue(
            page["text"].startswith("Tea\nTea is a drink.\nIt is & was popular.")
        )
        for hidden in (
            "steal",
            "Menu",
            "Log in",
            "Advert",
            "Subscribe",
            "Footer",
            "header",
        ):
            self.assertNotIn(hidden, page["text"])

    def test_a_page_without_a_marked_main_part_gives_all_its_text(self) -> None:
        title, text = readable(
            "<html><body><p>Hello</p><div>there</div></body></html>", True
        )
        self.assertEqual((title, text), ("", "Hello\nthere"))
        self.assertEqual(readable("  plain text  ", False), ("", "plain text"))

    def test_a_redirect_is_followed_only_to_an_allowed_site(self) -> None:
        pages = {
            "https://example.org/a": (301, {"location": "/b"}, b""),
            "https://example.org/b": (
                302,
                {"location": "https://en.wikipedia.org/c"},
                b"",
            ),
            "https://en.wikipedia.org/c": html(),
            "https://example.org/out": (302, {"location": "https://evil.com/x"}, b""),
        }
        open_ = opener(pages)
        self.assertEqual(
            fetch("https://example.org/a", SITES, open_)["url"],
            "https://en.wikipedia.org/c",
        )
        with self.assertRaisesRegex(WebError, "evil.com is not one of the allowed"):
            fetch("https://example.org/out", SITES, open_)
        self.assertNotIn("https://evil.com/x", open_.asked)

    def test_endless_redirects_stop(self) -> None:
        loop = {"https://example.org/a": (302, {"location": "/a"}, b"")}
        with self.assertRaisesRegex(WebError, "redirected more than"):
            fetch("https://example.org/a", SITES, opener(loop))

    def test_what_is_not_a_readable_page_is_refused(self) -> None:
        cases = {
            "status 404": (404, {"content-type": "text/html"}, b""),
            "not a text page": (200, {"content-type": "application/pdf"}, b"%PDF"),
            "larger than 2 MiB": (
                200,
                {"content-type": "text/html"},
                b"x" * (MAX_BYTES + 1),
            ),
        }
        for reason, answer in cases.items():
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(WebError, reason):
                    fetch(
                        "https://example.org/x",
                        SITES,
                        opener({"https://example.org/x": answer}),
                    )

    def test_a_feed_becomes_one_line_for_each_part_of_each_entry(self) -> None:
        feed = (
            b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            b"<title>Search results</title><entry><title>Tea   leaves</title>"
            b"<published>2026-10-14T10:00:00Z</published>"
            b'<link href="https://example.org/abs/1" rel="alternate"/>'
            b'<link href="https://example.org/pdf/1" rel="related"/>'
            b"<summary>Tea &amp; light.</summary><author><name>Ann Lee</name></author>"
            b"</entry><entry><title>Second</title></entry></feed>"
        )
        got = fetch(
            "https://example.org/feed",
            SITES,
            opener({"https://example.org/feed": (200, {"content-type": "application/atom+xml"}, feed)}),
        )
        self.assertEqual(
            got["text"],
            "---\nTitle: Tea leaves\nPublished: 2026-10-14T10:00:00Z\n"
            "Link: https://example.org/abs/1\nSummary: Tea & light.\nAuthor: Ann Lee\n"
            "---\nTitle: Second",
        )
        self.assertEqual(
            readable_feed("<rss><item><title>A</title><description>B</description></item></rss>"),
            "---\nTitle: A\nSummary: B",
        )

    def test_an_rdf_feed_is_read_too_links_authors_dates_and_all(self) -> None:
        # The shape bioRxiv and other RSS 1.0 feeds use: the link as the
        # item's about and again as the link tag's own words, authors and
        # dates behind a dc: prefix, and the long fields in CDATA.
        rdf = (
            "<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
            "<channel><title>Channel</title><link>https://x.org</link></channel>"
            "<item rdf:about='https://x.org/content/10.1/2026.10.22.1v1'>"
            "<title><![CDATA[ Tea and light ]]></title>"
            "<link>https://x.org/content/10.1/2026.10.22.1v1</link>"
            "<description><![CDATA[What the paper found.]]></description>"
            "<dc:creator><![CDATA[ Lee, A. ]]></dc:creator>"
            "<dc:date>2026-10-25</dc:date>"
            "</item></rdf:RDF>"
        )
        self.assertEqual(
            readable_feed(rdf),
            "---\n"
            "Link: https://x.org/content/10.1/2026.10.22.1v1\n"
            "Title: Tea and light\n"
            "Summary: What the paper found.\n"
            "Author: Lee, A.\n"
            "Published: 2026-10-25",
        )

    def test_the_same_line_is_not_said_twice_in_one_entry(self) -> None:
        # A link said three ways, and a title said as a title and a dc:title.
        self.assertEqual(
            readable_feed(
                "<item rdf:about='https://x.org/1'><link>https://x.org/1</link>"
                "<link href='https://x.org/1'/><title>Tea</title><dc:title>Tea</dc:title></item>"
            ),
            "---\nLink: https://x.org/1\nTitle: Tea",
        )
        # Two different authors are two lines.
        self.assertEqual(
            readable_feed("<item><creator>Ann</creator><creator>Bo</creator></item>"),
            "---\nAuthor: Ann\nAuthor: Bo",
        )

    def test_cdata_is_words_in_a_feed_and_stays_dropped_in_a_page(self) -> None:
        self.assertEqual(
            readable_feed("<item><description><![CDATA[<p>Bold words</p>]]></description></item>"),
            "---\nSummary: <p>Bold words</p>",
        )
        _title, text = readable("<p>before<![CDATA[hidden]]>after</p>", True)
        self.assertNotIn("hidden", text)

    def test_text_is_clipped(self) -> None:
        body = b"<main><p>" + b"word " * 20000 + b"</p></main>"
        page = fetch(
            "https://example.org/x",
            SITES,
            opener({"https://example.org/x": html(body)}),
        )
        self.assertEqual(len(page["text"]), MAX_CHARACTERS)

    def test_a_page_sent_packed_unasked_is_unpacked(self) -> None:
        import gzip

        packed = gzip.compress(b"<title>Packed</title><p>Unpacked text</p>")
        page = fetch(
            "https://example.org/x",
            SITES,
            opener({"https://example.org/x": (200, {"content-type": "text/html", "content-encoding": "gzip"}, packed)}),
        )
        self.assertEqual((page["title"], page["text"]), ("Packed", "Unpacked text"))
        with self.assertRaisesRegex(WebError, "cannot be read"):
            fetch(
                "https://example.org/x",
                SITES,
                opener({"https://example.org/x": (200, {"content-type": "text/html", "content-encoding": "gzip"}, b"not packed")}),
            )

    def test_a_long_page_is_read_a_part_at_a_time(self) -> None:
        body = b"<main><p>" + b"word " * 20000 + b"</p></main>"
        pages = opener({"https://example.org/x": html(body)})
        whole = fetch("https://example.org/x", SITES, pages)["text"]
        parts, start = [], 0
        while start != -1:
            part = fetch_from("https://example.org/x", start, SITES, pages)
            parts.append(part["text"])
            start = part["next"]
        self.assertEqual(len(parts), 5)
        self.assertTrue(all(len(part) == MAX_CHARACTERS for part in parts[:-1]))
        self.assertEqual(parts[0], whole)
        self.assertEqual("".join(parts), "word " * 19999 + "word")
        past = fetch_from("https://example.org/x", 10**6, SITES, pages)
        self.assertEqual((past["text"], past["next"]), ("", -1))
        for bad in (-1, 1.5, True):
            with self.subTest(start=bad), self.assertRaisesRegex(WebError, "whole number"):
                fetch_from("https://example.org/x", bad, SITES, pages)

    def test_an_address_that_is_not_allowed_is_never_requested(self) -> None:
        open_ = opener({})
        with self.assertRaises(WebError):
            fetch("https://evil.com/", SITES, open_)
        self.assertEqual(open_.asked, [])


SOURCE = """
record PageRequest { url: Text }
record Page { url: Text title: Text text: Text }
record Out { title: Text source: Text }

tool read_page {
  source system "web"
  operation: "fetch"
  access: observe
  input: PageRequest
  output: Page
}

agent browser {
  can: [read_page]
  max_steps: 1
}

fn nothing(request: PageRequest) -> Page {
  return {url: "", title: "", text: ""}
}

fn out(page: Page) -> Out {
  return {title: page.title, source: page.url}
}

workflow main(request: PageRequest) -> Out {
  page   = execute browser.read_page(request.url) otherwise nothing(request)
  result = out(page)
}
"""


def checked(source: str = SOURCE):
    program = parse(source)
    check(program)
    return program


class ToolTests(unittest.TestCase):
    def look(self, url: str, sites: tuple[str, ...] = ("example.org",)) -> Any:
        program = checked()
        tools = bind_system_tools(
            program,
            folder=None,
            sites=sites,
            web_opener=opener({"https://example.org/tea": html()}),
        )
        return run(
            program, "main", None, inputs={"request": {"url": url}}, tool_bindings=tools
        )

    def test_a_program_reads_an_allowed_page_with_no_approval(self) -> None:
        self.assertEqual(
            self.look("https://example.org/tea"),
            {"title": "A Page", "source": "https://example.org/tea"},
        )

    def test_a_page_elsewhere_is_refused_and_the_fallback_runs(self) -> None:
        self.assertEqual(self.look("https://evil.com/x"), {"title": "", "source": ""})

    def test_without_a_site_the_tool_is_not_bound(self) -> None:
        self.assertEqual(bind_system_tools(checked(), folder=None), {})
        self.assertEqual(
            self.look("https://example.org/tea", sites=()), {"title": "", "source": ""}
        )

    def test_a_bad_site_name_is_refused_at_the_start(self) -> None:
        with self.assertRaisesRegex(SystemToolError, "not a site name"):
            bind_system_tools(checked(), folder=None, sites=("not a site",))

    def test_the_declaration_is_checked(self) -> None:
        with self.assertRaisesRegex(CheckError, "use one of: fetch, fetch_from"):
            checked(SOURCE.replace('operation: "fetch"', 'operation: "post"'))
        with self.assertRaisesRegex(CheckError, "must have exactly the fields"):
            checked(
                SOURCE.replace(
                    "record PageRequest { url: Text }",
                    "record PageRequest { link: Text }",
                ).replace("request.url", "request.link")
            )


if __name__ == "__main__":
    unittest.main()
