# How motion is scripted on a web page
When: animation, animations, animate, animated, motion, scroll, scrolling, parallax, cursor, pointer, mouse, moving
Files: *.js
Check: contains "prefers-reduced-motion"
Check: contains "IntersectionObserver"

The script decides when things move; the stylesheet decides how. Keep the
script small, with no libraries, and let CSS do the animating.

Before anything else
- Ask once whether motion is wanted:
  `matchMedia('(prefers-reduced-motion: reduce)').matches`. If it is not,
  reveal everything at once and set up no pointer or scroll effects.
- Add the class `js` to `<html>` first, so the stylesheet may hide what
  will be revealed. Without the script nothing is hidden.

Scroll
- Reveal with one `IntersectionObserver` (threshold about 0.15, a bottom
  root margin of -10%). When an item is seen, add `is-visible` and stop
  observing it.
- Stagger a group by setting `--delay` on each child from its position.
- For the reading bar and the hero's drift, listen to `scroll` with
  `{ passive: true }`, and do the work in `requestAnimationFrame`: keep the
  latest position in a variable, and draw at most once a frame. Write the
  result to a CSS variable, such as `--progress`, and let CSS use it.

Cursor
- Set these up only when `matchMedia('(hover: hover) and (pointer: fine)')`
  matches. A phone has no cursor.
- One `pointermove` listener on the window for the glow. Do not move the
  glow straight to the pointer: each frame, move it a part of the way
  (about 0.15 of the distance), so it trails softly.
- For tilt, listen on each card: work out where the pointer is inside it
  from `getBoundingClientRect()`, as numbers from -1 to 1, and set
  `--rx`, `--ry`, `--x` and `--y`. On `pointerleave`, set them back to 0.
- For buttons that lean, do the same with a smaller pull.

Good manners
- No `setInterval` for animation. No work on every scroll event outside a
  frame. No layout reads and writes mixed in one loop.
- Stop the frame loop when the page is hidden (`visibilitychange`).
- Everything must still work with the keyboard alone.
