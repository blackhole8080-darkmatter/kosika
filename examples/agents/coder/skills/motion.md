# How motion is styled on a web page
When: animation, animations, animate, animated, motion, scroll, scrolling, parallax, hover, transition, transitions, moving, cursor
Files: *.css
Check: contains "prefers-reduced-motion"
Check: contains "transition"

Motion is there to guide the eye and to make the page feel alive. It must
never get in the way of reading. One page, one personality: pick one easing
curve and a small set of durations, name them as CSS variables, and use
them everywhere.

    --ease: cubic-bezier(0.22, 1, 0.36, 1);
    --fast: 200ms;  --base: 400ms;  --slow: 700ms;

What moves as the page is scrolled
- Sections and cards start slightly lower (16 to 32px) and transparent, and
  rise into place once when they come into view. Give them a class such as
  `reveal`, and let the script add `is-visible`. They do not hide again.
- Things in a group arrive one after another, 60 to 90ms apart, by setting
  a `--delay` on each and using it as the `transition-delay`.
- A thin bar at the very top grows with how far the page has been read.
- Only the hero may drift at a different speed from the page (parallax),
  and only by a little: at most 40px over the whole scroll.
- Where the browser supports it, prefer `animation-timeline: view()` and
  `scroll()` inside `@supports`, with the script as the fallback.

What moves with the cursor
- A soft glow, a large blurred circle in the accent colour at low opacity,
  that follows the pointer behind the content. It is `position: fixed`,
  `pointer-events: none`, and moved with `transform` only.
- Cards tilt a few degrees toward the pointer (at most 6) with
  `transform: perspective(900px) rotateX() rotateY()`, and settle back when
  it leaves. A highlight on the card may follow the pointer through the
  variables `--x` and `--y`.
- The main buttons lean up to 6px toward the pointer when it is near.

Rules that keep it smooth
- Animate `transform` and `opacity` and nothing else. Never `top`, `left`,
  `width`, `height`, `margin` or `box-shadow` size.
- Put `will-change: transform` only on the few things that follow the
  pointer, not on everything.
- Nothing loops for ever, except one slow, quiet accent if the page needs
  one (a gradient that drifts over 12 seconds or more).

Rules that keep it kind
- Everything above goes inside
  `@media (prefers-reduced-motion: no-preference) { ... }`, or is switched
  off in `@media (prefers-reduced-motion: reduce)`. With motion reduced the
  page shows everything at once, in place.
- Cursor effects are for a real pointer: wrap them in
  `@media (hover: hover) and (pointer: fine)`.
- Nothing is hidden until a script runs. If the script fails, the content
  must still be there: hide `reveal` items only under a class the script
  itself puts on `<html>`, such as `js`.
- Focus must stay visible and hover effects must have a `:focus-visible`
  twin.
