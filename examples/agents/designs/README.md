# Kosika Designs

You ask a question and get back a drawing that explains it. The explanation
is written into the drawing, one step at a time. Hover over a step and the
part it talks about lights up. Kosika checks the drawing's mathematics and
corrects it, and it shows real photos only where they help.

Kosika Designs runs on free and open models only, through Groq and
OpenRouter. Its photos are freely licensed, and each one is credited on the
page.

## What it draws

### A cycle, with icons inside its boxes

![The water cycle](shots/water-cycle.png)

Each box gets an emoji that moves the way its thing does. The sun turns, a
cloud drifts and rain drips. When a step is marked, its icon moves faster.

![The water cycle's icons moving](shots/water-cycle-icons.gif)

### A mind map

![A mind map of renewable energy](shots/renewable-energy-mind-map.png)

### A sequence, an orbit and containers

![What happens when you type a URL](shots/sequence-url.png)

A **sequence** puts the actors along the top, each with a dashed lifeline.
Their messages run between the lifelines in time order, one numbered row
each, so a request and its reply read from top to bottom.

![The solar system in order](shots/orbit-solar-system.png)

An **orbit** puts one thing at the centre and the rest on rings, in order
outward from it. Each card is placed round its ring by the golden angle,
so no two land on the same side.

![The architecture of a Kubernetes cluster](shots/containers-kubernetes.png)

**Containers** are boxes holding cards, with links between any two cards.
They suit a system's architecture, a cluster or a cell's organelles.

Kosika also picks these kinds from the question itself. "When I type",
"handshake" or "sequence diagram" gives a sequence. "Solar system",
"planets" or "electron shell" gives an orbit. "Architecture", "cluster" or
"organelles" gives containers.

### A comparison, as a table

![TCP, UDP and QUIC compared](shots/comparison-table.png)

Any question that sets things against each other is drawn as a table,
whatever the planner first chose. That covers "compare", "vs", "difference
between" and "should I learn Rust or Go". Each row is named on the left and
sized to fit its longest cell. When the question asks which to choose, the
last step gives a recommendation and the reason for it.

### A flow, and when Kosika shows no photo

![Photosynthesis](shots/photosynthesis.png)

Kosika decides when a photo helps. It shows at most four. Some parts never
get a photo:

- graphs, comparisons, timelines and figures of mathematics
- chemicals, gases, energy and particles, which no camera can show
- any part where the matching Wikipedia article's own picture is a drawing
  or is missing

For example, oxygen and glucose get an icon here and no photo.

### A graph, with its mathematics worked and checked

![Dijkstra's shortest path, checked](shots/dijkstra-checked.png)

Kosika works the shortest path itself, one line at a time. It also checks
the handshake lemma and Euler's theorem, and shows the distance table and
the adjacency matrix. If the model's explanation gets a number wrong,
Kosika writes the correction next to it.

### Mathematics written as a paper

![Expanding (x+2)^3, every line checked](shots/paper-expand.png)

The model writes the working. Kosika checks every line against its own
result (`std/math.kos`) and corrects any line that is wrong. The page can
be exported as LaTeX.

### When the answer is words

Some questions are better answered in words than with a drawing. Kosika
answers these in words: something to write (a poem, an email), a greeting,
or a short fact such as the capital of a country.

### A real photo, measured

![Why a sunflower counts in Fibonacci numbers](shots/sunflower-fibonacci.jpg)

Kosika traces the sunflower's spirals on a real photo and counts them. A
count is called Fibonacci only when it is close to Fibonacci numbers.

### Palettes

![Three drawings in six palettes](shots/palettes.jpg)

There are eight palettes. Dark Matter is the default, and Clear is safe for
colour-blind readers (Okabe–Ito). Kosika checks every palette's contrast
against WCAG before using it (`palette.kos`).

## The files

| File | What it does |
| --- | --- |
| `main.kos` | Reads the question, plans the drawing and describes it |
| `draw.kos` | Lays out and draws every kind of drawing, checks it, and writes the page |
| `palette.kos` | Holds the palettes and checks their contrast |
| `paper.kos` | Writes mathematics as a paper and checks each line |
| `sunflower.kos` | Traces and measures a real sunflower photo |
| `eyes.py` | Finds freely licensed photos on Wikipedia and Wikimedia Commons |

Kosika AI Chat (`examples/apps/chat`) shows these drawings as its answers.

Photos in the screenshots are from Wikimedia Commons, under the licences
credited on each page.
