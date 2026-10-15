# How a web page is built
When: website, web site, web page, webpage, landing page, homepage, page, html, site, portfolio
Files: *.html
Check: contains "<main"
Check: contains "viewport"

A page is read before it is looked at, by people and by screen readers. Its
HTML says what each part is; the stylesheet says how it looks.

The frame
- Start with `<!doctype html>`, `<html lang="en">`, a `<meta charset>`, the
  viewport meta (`width=device-width, initial-scale=1`), a real `<title>` and
  a one-sentence `<meta name="description">`.
- Link `styles.css` in the head. Load `script.js` at the end of the body
  with `defer`.
- One `<header>` with the name and a `<nav>` of in-page links, one `<main>`,
  one `<footer>`. Each part of the main is a `<section>` with an `id` the
  nav links to and one heading. Headings go in order: one `<h1>`, then
  `<h2>` for each section.

The content
- Write it for this subject: a name, a promise in one line, what is
  offered, why it is good, proof (numbers, a few words from people), and
  one clear thing to do next. Never lorem ipsum, never "Your text here".
- The hero has the `<h1>`, one sentence under it, and one or two buttons.
  Make the first button the thing the page most wants people to do.
- Lists of things (offers, features, prices, steps) are cards in a grid:
  `<ul>` with `<li>` cards, or `<article>`s.
- Every image has `alt` words that say what it shows; a purely decorative
  one has `alt=""`. Prefer shapes drawn in CSS or inline SVG to pictures
  from elsewhere.

For the style and the motion
- Give the hero the class `hero`, each section and card the class `reveal`,
  and the cards in one grid `--i` from 0 upward (`style="--i:2"`), so the
  motion can arrive one after another.
- Links are `<a href>`, actions are `<button>`. Nothing that can be clicked
  is a bare `<div>`.
