# Kosika Motion & Animation Guide

Kosika supports declarative motion and animation through scene descriptions. Programs describe shapes and their movements; the browser runtime renders them as HTML/CSS animations.

## Overview

Motion programs in Kosika:
- **Do not draw** - they build scene descriptions
- Are **declarative** - describe what should happen, not how
- Generate **standalone HTML** - run in any browser
- Support **easing functions** - bounce, back, linear, etc.
- Support **infinite loops** - animations that run forever
- Respect **prefers-reduced-motion** - accessibility built-in

## Running Motion Programs

```bash
# Check motion program
kosika check examples/motion/logo.kos

# Generate HTML animation
kosika motion examples/motion/logo.kos --input examples/motion/input.json --out logo.html

# Test motion program
kosika test examples/motion/logo.kos
```

## Motion Core Concepts

### Shape Record

```kosika
record Shape {
  id: Text        // Unique identifier
  kind: Text      // "circle", "square", "ring", "text"
  x: Number       // X position
  y: Number       // Y position
  size: Number    // Size (radius, width, font size)
  color: Text     // CSS color
  text: Text      // Text content (for kind: "text")
}
```

### Move Record

```kosika
record Move {
  shape: Text     // Shape ID to animate
  what: Text      // Property to animate: "x", "y", "size", "fade", "turn", "color"
  to: Number      // Target value
  start: Number   // Start time in seconds
  seconds: Number // Duration in seconds
  ease: Text      // Easing function: "out", "in", "bounce", "back", "linear"
  repeat: Number  // Number of repeats (0 = infinite)
  color: Text     // Target color (for "color" moves)
}
```

### Scene Record

```kosika
record Scene {
  width: Number      // Canvas width
  height: Number     // Canvas height
  seconds: Number    // Scene duration
  background: Text   // Background color
  shapes: Shape[]    // All shapes in the scene
  moves: Move[]      // All moves to animate
}
```

## Example: Logo Animation

The `examples/motion/logo.kos` demonstrates:
- **Staggered entry** - dots fly out one after another
- **Color variety** - warm colors alternating pattern
- **Easing variety** - bounce, back, linear easing
- **Infinite loops** - rotating ring that never stops
- **Nested composition** - helper functions for reusable moves

### Key Patterns

**Hidden start:**
```kosika
fn hidden(shape: Text) -> Move {
  return {shape: shape, what: "fade", to: 0, start: 0, seconds: 0, ease: "linear", repeat: 1, color: ""}
}
```

**Staggered timing:**
```kosika
let leave = 0.2 + i * 0.07  // Each dot leaves slightly later
```

**Ease modification:**
```kosika
fn eased(m: Move, ease: Text) -> Move {
  return {..m, ease: ease}
}
```

**Color flash:**
```kosika
fn flash(shape: Text, color: Text, start: Number) -> Move {
  return {shape: shape, what: "color", to: 0, start: start, seconds: 0.5, ease: "back", repeat: 1, color: color}
}
```

**Infinite loop:**
```kosika
{..eased(move("halo", "turn", 360, 1.0, 12), "linear"), repeat: 0}
```

## Animation Properties

### Position
- `x` - horizontal position
- `y` - vertical position

### Appearance
- `size` - radius, width, or font size
- `fade` - opacity (0 = hidden, 1 = visible)
- `color` - CSS color

### Transform
- `turn` - rotation in degrees

## Easing Functions

- `out` - slow at end (default)
- `in` - slow at start
- `linear` - constant speed
- `bounce` - bounce at end
- `back` - overshoot then return

## Animation Testing

Motion programs support declarative tests:

```kosika
test "scene has expected shapes" {
  a = main({dots: 12})
  expect a.shapes.length() == 16
  expect a.moves.length() == 12 * 6 + 10
}
```

## Path Motion

The `examples/path_motion/` directory contains examples of:
- Object movement along paths
- Bezier curve interpolation
- Trajectory planning

Run:
```bash
kosika motion examples/path_motion/curve.kos --out curve.html
```

## Design Principles

### Deterministic
- Same input → same output
- Randomness only from explicit input
- Traceable and replayable

### Accessible
- Respects `prefers-reduced-motion` media query
- Provides fallback for screen readers
- Color contrast considerations

### Portable
- Single HTML file output
- No external dependencies
- Runs in any modern browser

### Performant
- CSS-based rendering
- GPU-accelerated transforms
- Minimal JavaScript

## Building Motion Programs

1. **Define shapes** - static elements
2. **Define moves** - animations over time
3. **Compose with helpers** - reusable functions
4. **Test behavior** - check shape counts, move counts
5. **Generate HTML** - view in browser

## Advanced Patterns

### Sequential choreography
```kosika
moves = moves.concat([
  move("shape1", "x", 100, 0, 1),
  move("shape2", "x", 100, 1, 1),  // Starts after shape1
  move("shape3", "x", 100, 2, 1)   // Starts after shape2
])
```

### Parallel animation
```kosika
moves = moves.concat([
  move("shape", "x", 100, 0, 1),
  move("shape", "y", 100, 0, 1)   // Same start time
])
```

### Looping sequences
```kosika
{..move("shape", "turn", 360, 0, 2), repeat: 0}  // Forever
{..move("shape", "turn", 360, 0, 2), repeat: 3}  // 3 times
```

## Motion vs UI

Motion programs are for **presentation and storytelling**:
- Animated logos
- Explainer visuals
- Data visualization
- Narrative sequences

For **interactive UI**, use Kosika's browser workbench with reactive patterns.

## Workbench Integration

Motion scenes can be integrated into the browser workbench:
- Live preview during development
- Interactive timing adjustments
- Export to production HTML
- Version control for animations

## Future Enhancements

Planned motion features (see KOSIKA_VISION.md):
- Motion templates library
- Path-based interpolation
- Physics-based animation
- Multi-track timelines
- Motion presets
- Interactive motion controls
