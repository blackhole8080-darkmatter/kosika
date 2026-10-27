"""The eyes of Kosika Designs: find a free photo, and see where its florets are.

Kosika's web tool reads pages, not JSON or JPEGs, and its model steps cannot
carry an image yet, so this helper does only what needs those: it searches
Wikimedia Commons for the plan's words, keeps photos under a free licence,
downloads the first that is large enough, finds the bright tip of every
floret, and asks a free vision model for a first guess at the head. Every
decision after that is made in sunflower.kos, by Kosika's own maths.

    python eyes.py plan.json measured.json [--model MODEL --via groq|openrouter]
    python eyes.py plan.json picture.json --only-photo    (a reference photo for draw.kos)

plan.json is what `kosika run main.kos --workflow think` printed: a
record with ask, example, kind, why, search and others. search holds one
or more searches separated by |, tried in order.
"""

import argparse
import base64
import io
import json
import os
import re
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image
from scipy import ndimage

AGENT = "KosikaDesigns/0.1 (https://github.com/blackhole8080-darkmatter/kosika)"
FREE = re.compile(r"^(CC0|CC BY(-SA)? [0-9.]+|Public domain|PD.*)$", re.IGNORECASE)
ASK = (
    "This photo is {w} by {h} pixels. Find the centre of the sunflower's seed head (where the spirals start) "
    "in pixel coordinates, the radius in pixels of the disc of florets, and how many spirals you count "
    "turning each way. Answer only JSON: "
    '{{"center_x":0,"center_y":0,"disc_radius":0,"spirals_clockwise":0,"spirals_counterclockwise":0}}'
)
SITES = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=60).read()


def plain(html):
    return re.sub(r"<[^>]+>", "", html or "").strip()


def candidates(words, most=12, width=640):
    """Commons photos for these words, best match first, free licences only."""
    query = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": words + " filetype:bitmap", "gsrlimit": most, "prop": "imageinfo",
        "iiprop": "url|size|extmetadata", "iiurlwidth": width,
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist",
    })
    pages = json.loads(get("https://commons.wikimedia.org/w/api.php?" + query)).get("query", {}).get("pages", {}).values()
    found = []
    for page in sorted(pages, key=lambda p: p.get("index", 0)):
        info = page["imageinfo"][0]
        meta = info.get("extmetadata", {})
        licence = plain(meta.get("LicenseShortName", {}).get("value"))
        if not FREE.match(licence) or min(info["width"], info["height"]) < 400:
            continue
        found.append({
            "title": page["title"].removeprefix("File:"),
            "author": (plain(meta.get("Artist", {}).get("value")).splitlines() or ["unknown"])[0][:80],
            "license": licence,
            "license_url": meta.get("LicenseUrl", {}).get("value", "https://commons.wikimedia.org/wiki/Commons:Licensing"),
            "page_url": info["descriptionurl"],
            "file_url": info.get("thumburl") or info["url"],
        })
    return found


def thumbs(searches):
    """One small free photo for each search, as draw.kos's Picture, for a row of examples."""
    out = []
    for words in searches:
        found = candidates(words, most=6, width=200)
        if found:
            photo = found[0]
            raw = get(photo.pop("file_url"))
            photo["image"] = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
            out.append(photo)
    return out


def florets(image):
    """Bright local peaks of the photo, one per floret tip."""
    grey = np.asarray(image.convert("L")).astype(float)
    smooth = ndimage.gaussian_filter(grey, 2.0)
    peaks = (smooth == ndimage.maximum_filter(smooth, size=9)) & (smooth > ndimage.uniform_filter(smooth, 21) + 2)
    ys, xs = np.nonzero(peaks)
    return [int(x) for x in xs], [int(y) for y in ys]


def guess(model, via, data_uri, w, h):
    url, key_name = SITES[via]
    body = {
        "model": model, "temperature": 0, "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": ASK.format(w=w, h=h)},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]}],
    }
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + os.environ[key_name], "Content-Type": "application/json", "User-Agent": AGENT,
    })
    said = json.load(urllib.request.urlopen(request, timeout=180))["choices"][0]["message"]["content"]
    found = json.loads(said[said.index("{"): said.rindex("}") + 1])
    keys = ["center_x", "center_y", "disc_radius", "spirals_clockwise", "spirals_counterclockwise"]
    return {"model": model, **{k: float(found.get(k, 0) or 0) for k in keys}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plan")
    parser.add_argument("out")
    parser.add_argument("--model", default="")
    parser.add_argument("--via", default="groq", choices=sorted(SITES))
    parser.add_argument("--pick", type=int, default=0, help="Use the n-th free photo found instead of the first.")
    parser.add_argument("--only-photo", action="store_true", help="Write just the photo and its credit, as draw.kos's Picture.")
    parser.add_argument("--examples", default="", help="Searches separated by |: also write one small photo for each to OUT.examples.json.")
    args = parser.parse_args()
    plan = json.load(open(args.plan, encoding="utf-8"))
    found, seen = [], set()
    for words in plan["search"].split("|"):
        for photo in candidates(words.strip()):
            named = not plan.get("needs") or any(w in photo["title"].lower() for w in plan["needs"])
            if named and photo["title"] not in seen:
                seen.add(photo["title"])
                found.append(photo)
    if not found:
        raise SystemExit("No freely licensed photo found for: " + plan["search"])
    source = found[min(args.pick, len(found) - 1)]
    raw = get(source.pop("file_url"))
    if args.only_photo:
        source["image"] = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
        json.dump(source, open(args.out, "w"))
        print(f"{source['title']} ({source['license']})")
        return
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = image.size
    data_uri = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    xs, ys = florets(image)
    said = {"model": "", "center_x": w / 2, "center_y": h / 2, "disc_radius": min(w, h) * 0.4,
            "spirals_clockwise": 0, "spirals_counterclockwise": 0}
    if args.model:
        # A free model may be busy (429) or say nothing usable; Kosika then
        # searches from the middle of the picture, as with no model at all.
        try:
            said = guess(args.model, args.via, data_uri, w, h)
        except (OSError, ValueError, KeyError) as problem:
            print(f"The vision model gave no guess ({problem}); searching from the middle.")
    source.update({"width": w, "height": h, "image": data_uri})
    json.dump({"m": {"plan": plan, "source": source, "xs": xs, "ys": ys, "guess": said}}, open(args.out, "w"))
    if args.examples:
        json.dump(thumbs([w.strip() for w in args.examples.split("|")]), open(args.out + ".examples.json", "w"))
    print(f"{source['title']} ({source['license']}): {len(xs)} floret tips; guess from {said['model'] or 'nobody'}")


if __name__ == "__main__":
    main()
