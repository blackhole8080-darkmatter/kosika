# Kosika External Integrations

This directory contains integrations between Kosika and five external video/animation repositories:

- **HyperFrames** (heygen-com/hyperframes) - HTML-to-video rendering framework
- **OneTake** (feitangyuan/onetake) - Continuous-take motion films
- **GBRO Collage B-roll** (pyang5166/gbro-collage-broll) - Halftone paper-collage B-roll
- **Claude Animation Base** (JohnHeibel/ClaudeAnimationBase) - p5.js character animation
- **Lemo Opuscar** (lemomo-ai/lemo-opuscar) - 51 film styles library

## Directory Structure

```
external_integrations/
├── tools/              # Kosika tool declarations for external CLI/API calls
│   ├── hyperframes_cli.kos
│   ├── gemini_video.kos
│   └── p5js_render.kos
├── skills/             # Kosika skills/workflows leveraging these tools
│   ├── hyperframes_video.kos
│   ├── onetake_film.kos
│   ├── gbro_collage.kos
│   ├── claude_animation.kos
│   └── opuscar_film.kos
├── extensions/         # Native language extensions
│   ├── motion_primitives.kos
│   ├── continuity_measurement.kos
│   └── style_system.kos
├── agents/             # Example agents demonstrating integrated capabilities
│   ├── video_producer.kos
│   ├── animation_studio.kos
│   └── broll_generator.kos
└── examples/           # Usage examples
    ├── product_launch.kos
    ├── character_animation.kos
    ├── broll_batch.kos
    ├── opuscar_film.kos
    └── animation_studio.kos
```

## Quick Start

### Using HyperFrames

```kosika
import external_integrations/skills/hyperframes_video

workflow main() -> RenderResult {
  scene = {
    title: "My Video",
    duration: 30,
    scenes: [
      {
        id: "scene1",
        start_time: 0,
        duration: 5,
        elements: [
          {
            id: "title",
            type: "text",
            content: "Hello World",
            x: 100,
            y: 100,
            width: 200,
            height: 50,
            animations: []
          }
        ],
        transitions: []
      }
    ],
    background_color: "#000000",
    width: 1920,
    height: 1080
  }

  return create_and_render_video(scene, "./my-video", "output.mp4")
}
```

### Using GBRO Collage B-roll

```kosika
import external_integrations/skills/gbro_collage

workflow main() -> BrollClip {
  script = "很多人以为 AI 是来替你思考的，其实它更像一面镜子，会把你问题里的漏洞照出来。"
  api_key = "your-gemini-api-key"

  return create_broll(script, api_key)
}
```

### Using Lemo Opuscar

```kosika
import external_integrations/skills/opuscar_film

workflow main() -> Film {
  brief = {
    style: watercolor_brush,
    topic: "The coffee farm my family runs",
    duration: 45,
    narrator_gender: "female",
    custom_voice: null,
    custom_music: null,
    storyboard_first: false
  }

  return create_film(brief)
}
```

### Using the Video Producer Agent

```kosika
import external_integrations/agents/video_producer

workflow main() -> Video {
  brief = {
    type: "opuscar_film",
    topic: "My coffee farm",
    style: "watercolor_brush",
    script: null,
    scene: null,
    duration: 45,
    output_path: "output.mp4"
  }

  return produce_video(brief)
}
```

## Tool Declarations

### HyperFrames CLI

Wraps the HyperFrames CLI commands:
- `init` - Create new project
- `lint` - Static HTML structure check
- `check` - Browser gate validation
- `preview` - Preview in browser
- `render` - Render to MP4
- `publish` - Publish to cloud

### Gemini Video

Wraps Google's Gemini Omni Flash video generation API for first/last-frame animation.

### p5.js Render

Wraps p5.js frame-by-frame rendering for character animation.

## Skills

### HyperFrames Video

Creates HyperFrames compositions from Kosika scene descriptions, validates them, and renders to video.

### OneTake Film

Creates continuous-take films with measured continuity following OneTake's principles:
- Camera grammar (single camera, no cuts, follows subject)
- ~38 measured moves from catalog
- Continuity measurement at each boundary
- Real UI rebuilt in HTML for camera movement

### GBRO Collage

Implements the 3-gate approval workflow for halftone paper-collage B-roll generation:
1. Gate 1: Metaphor confirmation
2. Gate 2: Static frame confirmation
3. Gate 3: Video generation

### Claude Animation

Creates p5.js/p5.brush character animations following handmade, alive, one-piece, no-text rules:
- Frame-by-frame rendering with contact sheets
- Model sheets for emotions, views, motions
- Brush-wipe transitions
- Animation rules enforcement

### Opuscar Film

Creates films in 51 different styles with style invariants, treatment writing, and frame-by-frame rendering.

## Extensions

### Motion Primitives

Adds OneTake's measured moves to Kosika's motion system:
- Move types: spring, entrance, carry, contact, sim, camera, fluid_ground
- Vector law: how you exit determines how you enter
- Z scale-sign rule
- Ease functions: linear, in, out, both, bounce, back

### Continuity Measurement

Adds continuity verification to Kosika's checker:
- Frame analysis to detect boundaries
- Carrier element identification
- Carry score calculation
- Velocity matching (vector law)
- Comprehensive continuity reports

### Style System

Adds style-based creative direction to Kosika:
- 51 film styles across 10 categories
- Style invariants (look, color palette, motion rules, etc.)
- Style suggestion for topics
- Style validation and comparison

## Agents

### Video Producer

Multi-style video producer that routes requests to appropriate workflows:
- HyperFrames compositions
- Opuscar films
- GBRO B-roll clips

### Animation Studio

Combines character animation with style-based films:
- Character rig with emotions, views, motions
- Style application to character animations
- Multi-character scenes
- Animation library generation
- Lip-sync animation

### B-roll Generator

Generates B-roll clips for voiceover scripts:
- 3-gate approval workflow
- Batch processing
- Style variations
- Custom color palettes
- Compilation creation

## Examples

### Product Launch (`product_launch.kos`)

Demonstrates creating a product launch video using HyperFrames with motion primitives.

### Character Animation (`character_animation.kos`)

Shows character animation creation with p5.js, model sheets, and emotion customization.

### B-roll Batch (`broll_batch.kos`)

Demonstrates batch B-roll generation, approval workflows, and compilation.

### Opuscar Film (`opuscar_film.kos`)

Shows creating films in various Lemo Opuscar styles with different configurations.

### Animation Studio (`animation_studio.kos`)

Demonstrates combining character animation with styles, multi-character scenes, and lip-sync.

## Implementation Status

| Component | Status |
|-----------|--------|
| Tool Declarations | ✅ Complete (3/3) |
| HyperFrames Skill | ✅ Complete |
| GBRO Collage Skill | ✅ Complete |
| Opuscar Film Skill | ✅ Complete |
| OneTake Skill | ✅ Complete |
| Claude Animation Skill | ✅ Complete |
| Motion Primitives Extension | ✅ Complete |
| Continuity Measurement Extension | ✅ Complete |
| Style System Extension | ✅ Complete |
| Video Producer Agent | ✅ Complete |
| Animation Studio Agent | ✅ Complete |
| B-roll Generator Agent | ✅ Complete |
| Examples | ✅ Complete (5/5) |

## Next Steps

All components are now implemented. Remaining work:

1. Add error handling and fallbacks
2. Write unit tests with fixtures
3. Add to Kosika documentation
4. Set up CI/CD for integration testing
5. Create user-facing tutorials

## See Also

- [EXTERNAL_INTEGRATIONS_PLAN.md](../EXTERNAL_INTEGRATIONS_PLAN.md) - Comprehensive integration plan
- [INTEGRATION_SUMMARY.md](../INTEGRATION_SUMMARY.md) - Implementation summary
- [KOSIKA.md](../KOSIKA.md) - Kosika language contract
- [AGENT_GUIDE.md](../AGENT_GUIDE.md) - Guide for AI authors
