# How to make things move well
When: animation, animate, motion, moving, transition, motion graphics, kinetic, spring, scroll, website, page, design
Files: *.kos, *.html, *.css, *.svg

Use Kosika Designs' motion library, `import "std/designs_motion.kos"`, instead
of writing keyframes by hand:

1. Pick a move by name with `motion_moves([...])` and play it with
   `motion_use(selector, move, ease, ms)`. Arriving things use the `out` ease,
   leaving things `in`, moving across the screen `in-out`, playful badges `back`.
2. For anything that should feel physical, use a real spring:
   `motion_spring(stiffness, damping, mass).css` as the easing, and its `.ms`
   as the duration. Less damping bounces more.
3. Many things arriving together: `motion_stagger(selector, count, 40 to 90)`.
   Long pages: `motion_reveal(selector, "rise")` so parts arrive as they scroll in.
4. Headlines can move letter by letter with `motion_kinetic(text, "rise")`;
   lines can draw themselves with `motion_draw(d, colour, width, ms)`;
   a quiet background can have `motion_particles(...)`.
5. Move with transform, fade with opacity. Never animate width, height, top,
   left, margin or padding.
6. Before showing anything, run `motion_check(css)` and fix every problem it
   names. Say what it said. Nothing is done until the check is empty: that
   includes stopping for people who asked for reduced motion, and nothing
   flashing more than three times a second.
