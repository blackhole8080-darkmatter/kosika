"""Build site/designs.html: Kosika Designs, drawn live for the website.

Each picture on the page is asked for through the same front door Chat,
Studio and the agents use (kosika/designs.py, which runs
std/designs.kos), with place "site". Kosika chooses
for each ask whether it is a diagram, an animated illustration or moving
art. The page is static: run this again to draw it again.

    python site/tools/build-designs.py

Needs a free model, as kosika/designs.py says (GROQ_API_KEY, or
KOSIKA_DESIGNS_MODEL and KOSIKA_DESIGNS_MODEL_URL).
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kosika.designs import DesignsError, design  # noqa: E402

ASKS = [
    "how do bicycle gears make climbing easier",
    "how does a rainbow form",
    "what is a black hole",
    "a calm abstract background about the ocean",
    "compare solar and wind power",
]

STYLE = """
@font-face{font-family:Figtree;src:url(fonts/Figtree.ttf) format('truetype');font-weight:300 900;font-display:swap}
:root{--bg:#06070d;--panel:#0d0f1a;--ink:#eef0f7;--soft:#a3a8bd;--gold:#f5b45c;--amber:#ff8a4c;--line:rgba(238,240,247,.1);color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 Figtree,system-ui,-apple-system,'Segoe UI',sans-serif}
header,main{max-width:1180px;margin:0 auto;padding-left:max(20px,env(safe-area-inset-left,0px));padding-right:max(20px,env(safe-area-inset-right,0px))}
header{display:flex;align-items:center;justify-content:space-between;padding-block:22px}
header a{color:var(--soft);text-decoration:none;font-size:14px}
header a.home{color:var(--ink);font-weight:800;letter-spacing:-.01em;font-size:17px}
.eyebrow{font:600 11px ui-monospace,Consolas,monospace;letter-spacing:.3em;text-transform:uppercase;color:var(--gold)}
h1{font-size:clamp(32px,5vw,52px);font-weight:850;letter-spacing:-.03em;line-height:1.04;margin:10px 0 12px;text-wrap:balance}
.lede{color:var(--soft);max-width:64ch;margin:0 0 30px}
.ways{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:12px;margin-bottom:40px}
.ways div{border:1px solid var(--line);border-radius:14px;padding:14px 16px;background:var(--panel)}
.ways b{display:block;color:var(--gold);font:600 11px ui-monospace,Consolas,monospace;letter-spacing:.18em;text-transform:uppercase;margin-bottom:4px}
.ways p{margin:0;color:var(--soft);font-size:14.5px}
section{margin:0 0 46px}
.asked{font:600 12px ui-monospace,Consolas,monospace;letter-spacing:.12em;text-transform:uppercase;color:var(--soft);margin-bottom:10px}
.asked span{color:var(--amber)}
.why{color:var(--soft);font-size:13.5px;margin:10px 4px 0}
iframe{width:100%;border:0;border-radius:22px;background:transparent}
footer{max-width:1180px;margin:0 auto;padding:30px 20px 60px;color:var(--soft);font-size:13px}
"""


def framed(embed: str, kind: str) -> str:
    page = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>html,body{margin:0;background:#06070d;color-scheme:dark}</style></head>"
        f"<body>{embed}</body></html>"
    )
    height = 760 if kind == "diagram" else 900 if kind == "illustration" else 520
    return f'<iframe loading="lazy" style="height:{height}px" srcdoc="{html.escape(page, quote=True)}"></iframe>'


def main() -> int:
    sections = []
    for ask in ASKS:
        # a free model answers differently each time: two more tries
        made = {"problems": ["not tried"]}
        for _ in range(3):
            try:
                made = design(ask, place="site")
            except DesignsError as exc:
                made = {"problems": [str(exc)]}
            if not made["problems"]:
                break
        if made["problems"]:
            print(f"skipped {ask!r}: {'; '.join(made['problems'])}")
            continue
        print(f"drew {ask!r} as {made['kind']}: {made['title']}")
        sections.append(
            f'<section><div class="asked">{html.escape(made["kind"])} &#183; asked: <span>&ldquo;{html.escape(ask)}&rdquo;</span></div>'
            f'{framed(made["embed"], made["kind"])}'
            f'<p class="why">Kosika {html.escape(made["why"])}.</p></section>'
        )
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kosika Designs</title>
<meta name="description" content="Kosika Designs draws diagrams, animated illustrations and moving art, and every part of Kosika uses it.">
<link rel="icon" href="assets/mark.svg" type="image/svg+xml">
<style>{STYLE}</style>
</head>
<body>
<header><a class="home" href="index.html">Kosika</a><a href="index.html#start">Get started</a></header>
<main>
<div class="eyebrow">Kosika Designs</div>
<h1>Pictures that explain, drawn by Kosika</h1>
<p class="lede">Chat, Studio, this website and every agent ask Kosika Designs for pictures in the same way. A free model only says what is in a picture. Kosika checks it, works out the geometry and the motion itself, and draws it. Everything below was drawn that way.</p>
<div class="ways">
<div><b>Diagram</b><p>The structure of a thing: parts, steps, comparisons, timelines.</p></div>
<div><b>Illustration</b><p>How something works, as a small animated picture beside a few sentences.</p></div>
<div><b>Moving art</b><p>An abstract piece for a mood, a background or a cover.</p></div>
</div>
{"".join(sections)}
</main>
<footer>Drawn by site/tools/build-designs.py through std/designs.kos.</footer>
</body>
</html>
"""
    out = ROOT / "site" / "designs.html"
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
