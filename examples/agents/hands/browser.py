"""The Kosika hands in a web browser: Chrome, driven through its own
developer connection, reading pages as text instead of guessing pixels.

    python browser.py start               # a Chrome window of the hands' own
    python browser.py open URL            # go there (starting Chrome if need be)
                                          # and show the page, as read does
    python browser.py read                # the page's words, then its links,
                                          # buttons and fields, numbered
    python browser.py click N             # click thing number N from read
    python browser.py fill N "words"      # type into field number N

It is a separate Chrome with its own profile (kosika-daily/browser-profile):
none of your sign-ins, cookies or saved passwords are in it. Like hands.py,
each action is one run that a Kosika agent asks for through its shell tool,
so the person is asked before each one. It never submits a form by itself;
"click" on a submit button is an action the person sees and answers first.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

PORT = 9333
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PROFILE = Path.home() / "Aryan_Private" / "kosika-daily" / "browser-profile"
MOST_WORDS = 3000

# The page's words without drop-down option lists, menus and footers (a
# search page's list of countries would fill the whole allowance otherwise).
PAGE_WORDS = r"""
(() => {
  if (!document.body) return '';
  const hidden = [...document.querySelectorAll('select, nav, footer')];
  const was = hidden.map(e => e.style.display);
  hidden.forEach(e => { e.style.display = 'none'; });
  const words = document.body.innerText;
  hidden.forEach((e, i) => { e.style.display = was[i]; });
  return words.replace(/\n{3,}/g, '\n\n');
})()
"""

# Marks each link, button and field with a number the person and the model
# can name, and lists them; only the ones a person could see and use.
LIST_THINGS = r"""
(() => {
  const picked = [...document.querySelectorAll(
    'a[href], button, input:not([type=hidden]), textarea, select, [role=button], [role=link]')];
  const out = [];
  let n = 0;
  for (const e of picked) {
    const box = e.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) continue;
    const style = getComputedStyle(e);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    n += 1;
    e.setAttribute('data-kosika', String(n));
    const tag = e.tagName.toLowerCase();
    const kind = tag === 'a' ? 'link' : tag === 'input' ? 'field ' + (e.type || 'text') :
                 tag === 'textarea' ? 'field text' : tag === 'select' ? 'choice' : 'button';
    const words = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') ||
                   e.title || e.name || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    out.push(n + '. ' + kind + ': ' + words);
    if (n >= 120) break;
  }
  return out.join('\n');
})()
"""


def tabs() -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5) as answer:
        return [t for t in json.load(answer) if t.get("type") == "page"]


def running() -> bool:
    try:
        tabs()
        return True
    except OSError:
        return False


class Page:
    """The first tab of the hands' Chrome, spoken to over its websocket."""

    def __init__(self) -> None:
        pages = tabs()
        if not pages:
            raise SystemExit("the hands' Chrome has no tab open")
        self.socket = connect(pages[0]["webSocketDebuggerUrl"], max_size=None, open_timeout=10)
        self.next = 0

    def call(self, method: str, **params) -> dict:
        self.next += 1
        self.socket.send(json.dumps({"id": self.next, "method": method, "params": params}))
        while True:
            message = json.loads(self.socket.recv(timeout=30))
            if message.get("id") == self.next:
                if "error" in message:
                    raise SystemExit(f"Chrome said: {message['error'].get('message')}")
                return message.get("result", {})

    def run(self, script: str):
        result = self.call("Runtime.evaluate", expression=script, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result:
            raise SystemExit("the page refused: " + result["exceptionDetails"].get("text", ""))
        return result.get("result", {}).get("value")

    def settle(self) -> None:
        for _ in range(50):
            if self.run("document.readyState") == "complete":
                return
            time.sleep(0.2)


def show(page: "Page") -> None:
    """The page as text: its title, address and words, then its links,
    buttons and fields, numbered for click and fill."""
    page.settle()
    title = page.run("document.title")
    words = (page.run(PAGE_WORDS) or "").strip()
    if len(words) > MOST_WORDS:
        words = words[:MOST_WORDS] + f"\n... ({len(words) - MOST_WORDS} more letters)"
    things = page.run(LIST_THINGS) or ""
    print(f"# {title}\n{page.run('location.href')}\n\n{words}\n\n## What can be used\n{things}")


def start() -> None:
    if running():
        print("the hands' Chrome is already open")
        return
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([str(CHROME), f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
                      "--no-first-run", "--no-default-browser-check", "about:blank"])
    for _ in range(50):
        if running():
            print("opened the hands' own Chrome (its own profile, none of your sign-ins)")
            return
        time.sleep(0.2)
    raise SystemExit("Chrome did not open its developer connection")


def main(args: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if not args:
        raise SystemExit(__doc__)
    what, rest = args[0], args[1:]
    if what == "start":
        return start()
    if not running():
        if what != "open":
            print("the hands' Chrome is not open: open a page first")
            sys.exit(2)
        start()
    page = Page()
    if what == "open" and len(rest) == 1 and rest[0].startswith(("https://", "http://")):
        page.call("Page.navigate", url=rest[0])
        time.sleep(0.5)
        page.settle()
        # What is there is shown at once, so the next step is chosen from it.
        show(page)
    elif what == "read":
        show(page)
    elif what == "click" and len(rest) == 1 and rest[0].isdigit():
        said = page.run(f"""(() => {{
            const e = document.querySelector('[data-kosika="{int(rest[0])}"]');
            if (!e) return null;
            e.scrollIntoView({{block: 'center'}}); e.click();
            return (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 80);
        }})()""")
        if said is None:
            print(f"no thing number {rest[0]}: read the page again")
            sys.exit(2)
        time.sleep(0.5)
        page.settle()
        print(f"clicked {rest[0]} ({said})\n")
        show(page)
    elif what == "fill" and len(rest) >= 2 and rest[0].isdigit():
        words = " ".join(rest[1:])
        done = page.run(f"""(() => {{
            const e = document.querySelector('[data-kosika="{int(rest[0])}"]');
            if (!e || !('value' in e)) return false;
            if (e.type === 'password') return 'password';
            e.focus(); e.value = {json.dumps(words)};
            e.dispatchEvent(new Event('input', {{bubbles: true}}));
            e.dispatchEvent(new Event('change', {{bubbles: true}}));
            return true;
        }})()""")
        if done == "password":
            print("the hands never type into a password field")
            sys.exit(2)
        if not done:
            print(f"thing number {rest[0]} is not a field: read the page again")
            sys.exit(2)
        print(f"typed {len(words)} letters into {rest[0]}")
    else:
        print(f"not something the browser hands do: {' '.join(args)}")
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
