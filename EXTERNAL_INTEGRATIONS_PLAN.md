# Kosika External Integrations Plan

**Integration of HyperFrames, OneTake, GBRO Collage B-roll, Claude Animation Base, and Lemo Opuscar**

## Executive Summary

This document outlines a comprehensive integration strategy to incorporate five video/animation repositories into Kosika's AI-native programming language ecosystem. The integration spans three layers:

1. **Kosika Tool Declarations** - External CLI/API wrappers with typed interfaces
2. **Kosika Skills/Workflows** - Reusable agent capabilities leveraging these tools
3. **Native Language Extensions** - Kosika syntax/semantics where appropriate

## Repository Analysis

### 1. HyperFrames (heygen-com/hyperframes)

**Type:** HTML-to-video rendering framework with AI agent skills
**Capabilities:**
- 21 AI agent skills for video creation workflows
- CLI tools: `init`, `lint`, `check`, `preview`, `render`, `publish`
- Registry of 50+ installable blocks and components
- Multiple animation runtimes (GSAP, Lottie, Three.js, Anime.js, CSS, WAAPI, TypeGPU)
- Composition contract with `data-*` timing attributes
- Deterministic rendering with seekable timelines

**Integration Points:**
- CLI tool wrapper for HyperFrames commands
- Registry integration for blocks/components
- Composition generation from Kosika scene descriptions
- Animation timeline mapping to Kosika motion primitives

### 2. OneTake (feitangyuan/onetake)

**Type:** Claude Agent Skill for continuous-take motion films
**Capabilities:**
- Measured continuity (carry score per boundary)
- Real UI rebuilt in HTML for camera movement
- ~38 measured moves (springs, entrances, carries, contact, sims, camera, fluid grounds)
- Real motion blur with 180° shutter
- Foley and synthesis in one reverb room
- Deterministic rendering at 1080p30 / 4K60

**Integration Points:**
- Continuity measurement tool as Kosika verifier
- Move catalog as Kosika motion primitives
- Camera grammar as Kosika kinematic rules
- Motion blur quality gate in rendering pipeline

### 3. GBRO Collage B-roll (pyang5166/gbro-collage-broll)

**Type:** Halftone paper-collage B-roll generation with 3-gate approval
**Capabilities:**
- 3-gate approval workflow (metaphor → static frame → video)
- Gemini Omni Flash first/last-frame video generation
- Premium editorial paper-collage assemble-from-empty animation
- Stop-motion质感 with piece-by-piece assembly
- 9:16, 5s, 720×1280, 24fps, silent MP4 output

**Integration Points:**
- 3-gate approval workflow as Kosika pattern
- Video generation tool with typed gates
- Contact sheet generation for verification
- QA standards as Kosika test contracts

### 4. Claude Animation Base (JohnHeibel/ClaudeAnimationBase)

**Type:** p5.js/p5.brush character animation starter kit
**Capabilities:**
- p5.js + p5.brush for character animation
- Frame-by-frame rendering with contact sheets
- Model sheets for emotions, views, motion, hats
- Timeline with shots, loops, brush-wipe transitions
- Handmade, alive, one-piece, no-text animation rules

**Integration Points:**
- p5.js rendering engine as Kosika runtime adapter
- Character rig as Kosika record types
- Animation rules as Kosika creative direction patterns
- Model sheet generation as Kosika visualization tool

### 5. Lemo Opuscar (lemomo-ai/lemo-opuscar)

**Type:** 51 film styles library with code-generated demo films
**Capabilities:**
- 51 film styles across 10 categories (Evidence Films, Hand-drawn, East Asian, Print, Graphic, Information, Cartoon, Games, Cinema, Materials)
- Each style has fixed invariants (STYLE.md) and demo film
- Canvas and WebGL rendering frame-by-frame
- Original scores from free sample libraries
- Text-to-speech narration

**Integration Points:**
- Style catalog as Kosika enum type
- Style invariants as Kosika creative direction rules
- Film generation workflow as Kosika agent
- Style selection interface as Kosika skill

## Integration Architecture

### Layer 1: Kosika Tool Declarations

External CLI/API tools wrapped with Kosika's typed tool system:

```kosika
// HyperFrames CLI tool
tool hyperframes_cli {
  source system "shell"
  operation: "execute"
  input: HyperFramesCommand
  output: HyperFramesResult
}

record HyperFramesCommand {
  action: Text  // "init", "lint", "check", "preview", "render", "publish"
  project_path: Text
  args: Text[]?
}

record HyperFramesResult {
  success: Bool
  output: Text
  exit_code: Number
}

// Gemini Omni Flash video generation
tool gemini_video {
  source system "http"
  operation: "post"
  input: GeminiVideoRequest
  output: GeminiVideoResponse
}

record GeminiVideoRequest {
  api_key: Text
  prompt: Text
  first_frame: Text?  // base64 or URL
  last_frame: Text?   // base64 or URL
  duration: Number
}

record GeminiVideoResponse {
  video_url: Text
  status: Text
  error: Text?
}

// p5.js rendering
tool p5js_render {
  source system "shell"
  operation: "execute"
  input: P5RenderRequest
  output: P5RenderResult
}

record P5RenderRequest {
  script_path: Text
  output_path: Text
  frames: Number
  fps: Number
  width: Number
  height: Number
}

record P5RenderResult {
  success: Bool
  frame_count: Number
  output_path: Text
}
```

### Layer 2: Kosika Skills/Workflows

#### Skill: HyperFrames Video Production

```kosika
import hyperframes/scene
import hyperframes/motion

skill hyperframes_video {
  // Create HyperFrames composition from Kosika scene description
  workflow create_composition(scene: SceneDescription) -> Composition {
    // Generate HTML with data-* attributes
    // Apply motion doctrine rules
    // Add registry blocks as needed
  }

  // Validate composition
  workflow validate(comp: Composition) -> ValidationResult {
    // Run hyperframes lint
    // Run hyperframes check
    // Verify determinism
  }

  // Render to video
  workflow render(comp: Composition, output: Text) -> RenderResult {
    // Run hyperframes render
    // Return video path
  }
}
```

#### Skill: OneTake Continuity Film

```kosika
import onetake/moves
import onetake/camera

skill onetake_film {
  // Create continuous-take film with measured continuity
  workflow create_film(brief: FilmBrief) -> Film {
    // Apply camera grammar
    // Compose moves from catalog
    // Generate real UI in HTML
    // Measure continuity at each boundary
  }

  // Verify continuity score
  workflow verify_continuity(film: Film) -> ContinuityReport {
    // Probe each boundary
    // Calculate carry score
    // Fail if below threshold
  }
}
```

#### Skill: GBRO Collage B-roll

```kosika
import gbro/gates

skill gbro_collage {
  // 3-gate approval workflow
  workflow create_broll(script: Text) -> BrollClip {
    gate1: confirm_metaphor = propose_metaphor(script)
    gate2: confirm_static = generate_static_frame(gate1.approved)
    gate3: generate_video(gate2.approved)
    return gate3.result
  }

  workflow propose_metaphor(script: Text) -> MetaphorProposal {
    // Analyze script
    // Propose visual metaphor
    // Wait for approval
  }

  workflow generate_static_frame(metaphor: Metaphor) -> StaticFrame {
    // Generate halftone collage
    // Create contact sheet
    // Wait for approval
  }

  workflow generate_video(frame: StaticFrame) -> BrollClip {
    // Use Gemini Omni Flash
    // Generate first/last frame animation
    // Run QA checks
  }
}
```

#### Skill: Claude Animation Base

```kosika
import animation_base/character
import animation_base/rules

skill claude_animation {
  // Create character animation following rules
  workflow create_animation(brief: AnimationBrief) -> Animation {
    // Apply animation rules (handmade, alive, one-piece, no-text)
    // Build character rig from model sheets
    // Render frame-by-frame with contact sheets
    // Apply brush-wipe transitions
  }

  workflow generate_model_sheet(character: Character) -> ModelSheet {
    // Render emotions
    // Render views
    // Render motion samples
  }
}
```

#### Skill: Lemo Opuscar Film

```kosika
import opuscar/styles
import opuscar/director

skill opuscar_film {
  // Create film in selected style
  workflow create_film(style: Style, topic: Text) -> Film {
    // Load style invariants from STYLE.md
    // Write treatment following DIRECTOR.md
    // Build following TECHNIQUE.md
    // Render frame-by-frame
  }

  workflow select_style(topic: Text) -> Style {
    // Suggest matching styles
    // Return user's choice
  }
}
```

### Layer 3: Native Language Extensions

#### Motion Primitives Extension

Extend Kosika's motion system with OneTake's measured moves:

```kosika
// New motion primitive types
enum MoveType {
  spring, entrance, carry, contact, sim, camera, fluid_ground
}

record Move {
  type: MoveType
  duration: Number
  ease: EaseFunction
  target: Text  // element id
  parameters: Text{}  // move-specific parameters
}

enum EaseFunction {
  linear, in, out, both, bounce, back
}

// Motion rule: vector law
fn apply_vector law(scene: Scene) -> Scene {
  // How you exit determines how you enter
  // Z scale-sign rule enforcement
  // Carrier element tracking
}
```

#### Continuity Measurement Extension

Add continuity verification to Kosika's checker:

```kosika
// Continuity measurement
record Boundary {
  before_frame: Frame
  after_frame: Frame
  carrier_elements: Text[]
  carry_score: Number
}

fn measure_continuity(scene: Scene) -> Boundary[] {
  // Probe each boundary
  // Identify what carried across
  // Calculate carry score (0.0 - 1.0)
}

fn verify_continuity(boundaries: Boundary[], threshold: Number) -> Bool {
  // Fail if any boundary below threshold
  // Return true if all pass
}
```

#### Style System Extension

Add style-based creative direction to Kosika:

```kosika
// Style enum
enum FilmStyle {
  satellite_briefing, engineering_briefing_board, lidar_survey,
  white_model, data_physicalization, exploded_teardown,
  microscope_story, archive_lighttable,
  crayon_picture_book, watercolor_brush, chinese_ink_wash,
  impasto_oil_painting, one_line_drawing, whiteboard_explainer,
  urban_sketch_pen_wash,
  // ... all 51 styles
}

// Style invariants
record StyleInvariants {
  look: Text
  color_palette: Text[]
  type: Text
  motion_rules: Text[]
  camera_grammar: Text[]
  sound_palette: Text[]
  native_moves: Text[]
  pitfalls: Text[]
}

fn load_style_invariants(style: FilmStyle) -> StyleInvariants {
  // Load from styles/<style>/STYLE.md
}
```

## Example Kosika Agents

### Agent 1: Multi-Style Video Producer

```kosika
import external_integrations/hyperframes
import external_integrations/onetake
import external_integrations/opuscar

agent video_producer {
  // Ask user for style preference
  // Route to appropriate workflow
  // Handle all video production end-to-end

  workflow produce_video(brief: VideoBrief) -> Video {
    style = select_style(brief)

    match style {
      case "continuous_take" {
        return onetake_film.create_film(brief)
      }
      case "opuscar_style" {
        return opuscar_film.create_film(brief.style, brief.topic)
      }
      case "hyperframes" {
        comp = hyperframes_video.create_composition(brief.scene)
        validated = hyperframes_video.validate(comp)
        return hyperframes_video.render(validated, brief.output)
      }
    }
  }
}
```

### Agent 2: Animation Studio

```kosika
import external_integrations/claude_animation
import external_integrations/opuscar

agent animation_studio {
  // Combine character animation with style-based films
  // Handle rigging, rendering, and mixing

  workflow create_character_film(character: Character, style: FilmStyle, story: Text) -> Film {
    // Generate model sheet
    model_sheet = claude_animation.generate_model_sheet(character)

    // Create animation following rules
    animation = claude_animation.create_animation({
      character: character,
      brief: story
    })

    // Apply style invariants
    style_invariants = load_style_invariants(style)

    // Render with style-specific pipeline
    return render_with_style(animation, style_invariants)
  }
}
```

### Agent 3: B-roll Generator

```kosika
import external_integrations/gbro_collage

agent broll_generator {
  // Generate B-roll clips for voiceover scripts
  // Handle 3-gate approval workflow

  workflow generate_broll_batch(scripts: Text[]) -> BrollClip[] {
    var clips: BrollClip[] = []

    for script in scripts {
      clip = gbro_collage.create_broll(script)
      clips = clips.append(clip)
    }

    return clips
  }
}
```

## Implementation Roadmap

### Phase 1: Tool Declarations (Week 1-2)
- [ ] Create Kosika tool declarations for HyperFrames CLI
- [ ] Create Kosika tool declarations for Gemini Omni Flash
- [ ] Create Kosika tool declarations for p5.js rendering
- [ ] Add shell and HTTP tool sources to Kosika runtime
- [ ] Write tests for tool invocation

### Phase 2: Basic Skills (Week 3-4)
- [ ] Implement HyperFrames video production skill
- [ ] Implement GBRO 3-gate approval workflow skill
- [ ] Implement basic Lemo Opuscar film skill
- [ ] Write example workflows using these skills
- [ ] Add skill tests with fixtures

### Phase 3: Advanced Integrations (Week 5-6)
- [ ] Implement OneTake continuity measurement
- [ ] Implement Claude Animation Base skill
- [ ] Add motion primitives extension
- [ ] Add continuity measurement extension
- [ ] Add style system extension

### Phase 4: Example Agents (Week 7-8)
- [ ] Create multi-style video producer agent
- [ ] Create animation studio agent
- [ ] Create B-roll generator agent
- [ ] Write comprehensive examples
- [ ] Create integration documentation

### Phase 5: Polish & Documentation (Week 9-10)
- [ ] Add error handling and fallbacks
- [ ] Optimize performance
- [ ] Write user-facing documentation
- [ ] Create tutorials
- [ ] Add to Kosika showcase

## File Structure

```
kosika/
├── external_integrations/
│   ├── tools/
│   │   ├── hyperframes_cli.kos
│   │   ├── gemini_video.kos
│   │   └── p5js_render.kos
│   ├── skills/
│   │   ├── hyperframes_video.kos
│   │   ├── onetake_film.kos
│   │   ├── gbro_collage.kos
│   │   ├── claude_animation.kos
│   │   └── opuscar_film.kos
│   ├── extensions/
│   │   ├── motion_primitives.kos
│   │   ├── continuity_measurement.kos
│   │   └── style_system.kos
│   ├── agents/
│   │   ├── video_producer.kos
│   │   ├── animation_studio.kos
│   │   └── broll_generator.kos
│   └── examples/
│       ├── product_launch.kos
│       ├── character_animation.kos
│       └── broll_batch.kos
└── docs/
    ├── EXTERNAL_INTEGRATIONS.md
    └── INTEGRATION_TUTORIALS.md
```

## Benefits

1. **Unified Programming Model** - All video/animation work expressed in Kosika
2. **Type Safety** - Typed interfaces prevent errors at compile time
3. **Replayability** - All operations traced and replayable
4. **Approval Gates** - Built-in approval system for expensive operations
5. **Determinism** - Consistent outputs across runs
6. **Skill Composition** - Skills can be combined in complex workflows
7. **Multi-Agent Coordination** - Agents can collaborate on video projects

## Risks & Mitigations

### Risk: External Tool Dependencies
**Mitigation:** Tool declarations include fallbacks and error handling. Provide offline mode where possible.

### Risk: API Rate Limits/Costs
**Mitigation:** Approval gates before expensive operations. Budget tracking in Kosika runtime.

### Risk: Version Drift
**Mitigation:** Pin specific versions in tool declarations. Automated testing on updates.

### Risk: Learning Curve
**Mitigation:** Comprehensive documentation, examples, and tutorials. Gradual rollout.

## Success Metrics

- [ ] All 5 repositories integrated with Kosika tool declarations
- [ ] At least 3 production-ready skills implemented
- [ ] 3 example agents demonstrating integration
- [ ] Comprehensive documentation and tutorials
- [ ] Test coverage > 80% for integration code
- [ ] Performance benchmarks showing acceptable overhead
- [ ] User feedback on ease of use

## Next Steps

1. Review and approve this integration plan
2. Begin Phase 1: Tool Declarations
3. Set up CI/CD for integration testing
4. Create tracking board for implementation tasks
5. Begin development following roadmap
