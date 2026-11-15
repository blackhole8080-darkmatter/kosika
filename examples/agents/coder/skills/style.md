# How a web page is styled
When: website, web site, web page, webpage, landing page, homepage, design, style, styles, css, beautiful, modern, professional, site
Files: *.css
Check: contains ":root"
Check: contains "@media"

A page looks professional when it is calm and consistent: few colours,
one type scale, one spacing scale, and plenty of room.

The system, first, in `:root`
- Colours: a background, a surface a step lighter or darker, the text, a
  muted text, one accent, and the accent's soft tint. Name them
  (`--bg`, `--surface`, `--text`, `--muted`, `--accent`, `--accent-soft`).
  Text on the background must be easy to read: dark on light or light on
  dark, never grey on grey.
- Type: a system font stack, or one display face for headings beside it.
  Sizes from a scale, with the large ones fluid:
  `--step-5: clamp(2.6rem, 6vw, 5rem)` for the hero heading down to
  `--step-0: 1rem` for body text. Body text 1rem to 1.125rem with a line
  height near 1.6; headings tighter, near 1.1, with slight negative letter
  spacing.
- Space: `--space-1` to `--space-8` doubling or nearly so (0.25rem up to
  8rem). Sections get large space above and below (`--space-7` or more).
- Shape: one radius for cards and buttons, one soft shadow.

Layout
- A centred container: `width: min(1120px, 100% - 2.5rem); margin-inline:
  auto`.
- Grids with `grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr))`
  so cards wrap on a phone without a media query.
- The hero fills most of the first screen (`min-height: 85svh`), its heading
  large and short lines (`max-width: 14ch` or so).
- A `@media (max-width: 720px)` block for what must change on a phone: the
  nav, the hero's size, the space between sections.

Detail that reads as quality
- The header stays at the top (`position: sticky`) with a translucent
  background and `backdrop-filter: blur(12px)`.
- Buttons: a filled main one in the accent, a quiet outlined second one;
  both with padding of about 0.9rem 1.4rem, the shared radius, and a clear
  hover and `:focus-visible` state.
- Cards: the surface colour, the radius, a hairline border
  (`1px solid` the text colour at low opacity), the shadow on hover.
- A hero background drawn in CSS: layered `radial-gradient`s in the accent
  and its tint, softly blurred, rather than a picture.
- `::selection` in the accent's tint. `scroll-behavior: smooth` only when
  motion is not reduced.
