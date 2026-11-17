# Kosika math drawings: a guide for agents

`std/designs_plot.kos` draws mathematics as white lines on a dark page.
Kosika math reads the formula and computes every point itself (its own
complex arithmetic, derivatives by rule, Simpson's rule, step-by-step
solutions), and Designs lays out the figure with the formula in MathML
notation above it. An agent that needs to show how some mathematics
behaves should use it instead of describing the picture in words.

Every drawing returns a `PlFigure`:

| field | what it holds |
| --- | --- |
| `kind` | which drawing was made, such as `taylor` or `twins` |
| `title` | the figure's name, written the way a person writes the formula |
| `formula` | the formula that was drawn |
| `notation` | MathML for the formula and the worked steps |
| `svg` | the still drawing, ready to save as a picture |
| `live` | for 3D figures, a canvas you can drag to turn and scroll to zoom |
| `page` | a whole HTML page: kicker, title, notation, drawing, notes |
| `notes` | short sentences saying what the drawing shows and how Kosika worked it |
| `problems` | anything Kosika could not compute. It is never guessed |

When `problems` is not empty, show the problems to the person and do not
claim the drawing is complete.

## Three ways to call it

1. **From a plain ask.** `pl_for(text)` reads the ask, picks the drawing and
   reads the formula and any range ("from 0 to 2", "about x = 1",
   "to order 5", "at 0.8c"). `pl_kind(text)` names the drawing without
   making it, and `pl_wants(text)` says whether an ask wants mathematics drawn.
2. **Through Designs.** Any ask that `pl_wants` accepts goes to the `plot`
   kind in `std/designs.kos` and comes back as a Designs result. An ask
   for an art piece stays art.
3. **Directly.** Import the module and call one drawing with exact
   arguments, as in the table below.

```kosika
import "std/designs_plot.kos"

workflow main(ask: PlAsk) -> PlFigure {
  out = pl_for(ask.text)
}
```

From the command line:

```
kosika run kosika/std/designs_plot.kos --workflow plot --input ask.json
```

where `ask.json` holds `{"ask": {"text": "taylor series of sin(x)"}}`.

## Every drawing

`pl_catalogue()` returns this list as `PlEntry` records (`kind`, `ask`,
`draws`, `call`), so an agent can read it at run time.

| kind | example ask | what it draws | direct call |
| --- | --- | --- | --- |
| curve | plot y = x^3 - 3x | a graph with its derivative, roots and turning points | `pl_curve(formula, x0, x1)` |
| taylor | taylor series of sin(x) | a function and its Taylor polynomials, with derivatives worked by rule | `pl_taylor(formula, centre, order)` |
| integral | integral of x^2 from 0 to 2 | the area under a curve, midpoint strips, and Simpson's value | `pl_integral(formula, a, b)` |
| polar | polar curve r = cos(4*theta) | a polar curve on a polar grid | `pl_polar(formula, turns)` |
| parametric | parametric x = sin(3t), y = sin(2t) | a curve traced by t, with its start and direction | `pl_param(x_formula, y_formula, t0, t1)` |
| slope | slope field dy/dx = x - y | a slope field and solution curves | `pl_flow("1", formula, span, true)` |
| field | vector field F = (-y, x) | a 2D vector field with flow lines | `pl_flow(p_formula, q_formula, span, false)` |
| complex | complex function f(z) = z^2 | a z-plane grid and its image under f | `pl_complex(formula, radius)` |
| modulus | complex function 1/(z^2+1) in 3d | the 3D landscape of \|f(z)\|, with zeros and poles | `pl_modulus(formula, radius)` |
| surface | plot z = sin(x)*cos(y) | a 3D surface with hidden lines removed, live to turn | `pl_surface(formula, span)` |
| path | (direct call only) | a curve in 3D space | `pl_path(x_formula, y_formula, z_formula, t0, t1)` |
| spacetime | space-time with the light cone | a 3D light cone with world lines, beside a Minkowski diagram | `pl_spacetime(v)` |
| twins | twin paradox at 0.8c | the twin paradox on a Minkowski diagram, with each twin's years | `pl_twins(v, distance)` |
| well | space-time curved by a mass | a rubber-sheet picture of curved space-time with an orbit | `pl_well(mass)` |
| shape | draw a klein bottle | a named 3D shape, or any surface x, y, z in u and v, shaded and turning | `pl_named(name)`, `pl_shape(x, y, z, u0, u1, v0, v1, title, about)` |
| knot | trefoil knot | a knot or space curve as a tube that does not twist, with a point running along it | `pl_knot(text)`, `pl_tube(x, y, z, t0, t1, radius, title)` |
| implicit | gyroid | the surface where F(x, y, z) = 0 | `pl_implicit(equation, span)` |
| revolve | solid of revolution of y = sin(x) from 0 to pi | a curve turned about the x axis, with its volume | `pl_revolve(formula, a, b)` |
| solid | dodecahedron | a Platonic solid, with Euler's V − E + F = 2 | `pl_solid(name)` |
| wave | animate z = sin(x - t)*cos(y) | a surface that moves in time t, played as a loop | `pl_wave(formula, span)` |

Formulas are written the way `std/math.kos` reads them: `x^2`, `sin(x)`,
`exp(-x^2)`, `3x`, `pi`, `e`, `i`. Angles inside `sin` and `cos` are in
radians in a formula. Polar curves use `theta` (or θ), parametric curves
use `t`, and complex functions use `z`.

## 3D shapes

Kosika models a 3D shape in four ways. Each is shaded by how squarely
each face looks at the eye, and its live view turns slowly by itself
until someone drags it.

- **Parametric surfaces.** Any surface written as x, y and z in `u` and
  `v`: `pl_shape("cos(u)*sin(v)", "sin(u)*sin(v)", "cos(v)", 0, 2*pi, 0, pi, "sphere", "")`.
  `pl_shapes()` lists the named ones: sphere, torus, Möbius strip, Klein
  bottle, seashell, helicoid, catenoid, Enneper surface, Dini's surface
  and hyperboloid. An ask such as "parametric surface x = ..., y = ...,
  z = ..." reads all three formulas over 0 to 2π.
- **Implicit surfaces.** The surface where F(x, y, z) = 0, found by
  marching tetrahedra through a 16 × 16 × 16 grid of the box from −span
  to span. `pl_blobs()` lists the named ones: gyroid, two spheres
  melting together, Schwarz P surface and Chmutov surface. Any equation
  in x, y and z with "=" (but not "z = ...", which is a height surface)
  is read as implicit.
- **Tubes and solids of revolution.** `pl_tube` puts a ring round a space
  curve at each point and turns each ring as little as possible from the
  last, so knots do not twist. `pl_knots()` lists trefoil,
  figure-eight, (2, 5) torus knot and helix. `pl_revolve` turns y = f(x)
  about the x axis and works the volume π∫y² dx by Simpson's rule.
- **Platonic solids.** `pl_solid` builds the five solids from their
  corners alone: edges are pairs of corners one edge length apart, faces
  are planes with every other corner on one side, and the notation
  checks Euler's V − E + F = 2.

`pl_wave` draws a height surface z = f(x, y, t) at 12 moments and plays
them in a loop in the live view.

What it cannot do yet: free-form models such as a face or a car need a
mesh file, which Kosika does not read. A run's 10,000-call budget limits
a parametric surface to about 1,000 points (such as 72 × 14) and an implicit surface to
a 16-step grid, so fine detail smaller than that is lost.

## Rules to keep

- **One drawing per run.** A run may make 10,000 calls to Kosika
  functions, and one drawing uses most of them. Draw a gallery as several
  runs, as `pl_examples()` does.
- **Keep the work inline.** Only calls to functions you write count. Method
  calls (`.sin()`, `.to_fixed(2)`) and comprehensions do not. When you add a
  drawing, call `pl_eval` once per point, project points in batches, and
  write colours and numbers in place rather than through small helpers.
- **Both engines must agree.** After changing the module, copy it to
  `native/std/`, rebuild the Go engine, run `kosika test` with both engines,
  and compare the output of `--workflow plot` for a few asks.
- **Nothing is guessed.** A point Kosika cannot compute (a division by
  zero, the log of a negative number) is left out of the line, and the
  reason goes into `problems` when the drawing cannot be made.

## Motion

The drawings move, using plain SVG animation, so they move in Studio, in a
browser and in a saved `.svg` file, and no script runs:

- The pen draws each curve, then an amber point travels along it. On
  graphs, Taylor series, integrals and parametric curves, a short tangent
  line turns with the point.
- Taylor polynomials appear one after another, from the lowest order up.
  Integral strips fill in from left to right.
- On polar curves the radius r runs round as the angle grows.
- Flow lines in slope and vector fields stream in the direction of flow.
- In the twin paradox both twins move up their world lines in step with
  home time, and a counter shows each twin's own birthdays.

When a viewer asks for reduced motion, the page shows the still drawing.
`pl_tracer(path, seconds, tangent)` and `pl_spoke(...)` add the same
motion to a new drawing.

## Pictures

The pictures of every drawing are in the project's
`kosika-math-drawings` folder. The look: background `#07080d`, white ink
`#f4f1ea`, amber `#f5b942` for the one line to notice, and indigo
`#8b8cff` for lines of simultaneity.
