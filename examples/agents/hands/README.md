# Hands: Kosika agents with cursors, kits and skills

Kosika agents that act on this Windows laptop while you watch. Each agent has
its own pointer in its own colour, moving beside your mouse (your mouse stays
where you left it). The agent asks you before every action, and holding
Escape stops any action at once.

## An agent's kit, written in Kosika

Every agent's kit is in [kits.kos](kits.kos). Edit it to change an agent:

| field  | what it is |
|--------|------------|
| colour | its pointer's colour: `#rrggbb`, or one of `amber violet sky gold mint rose indigo pink lime cyan red white` |
| skills | notes it reads before it works: `skills/NAME.md`, or another agent's, like `coder/python`. A skill is advice only and never gives the agent a power. |
| tools  | the Kosika tools it may call |
| hands  | what it may do here: `look press write read move click type keys open:notepad open:calculator browse` |
| typing | `steady` (keys, 15 letters a second, to watch), `fast` (straight into the field when the program allows it, else keys at the fastest rate learned exact for that program) or `instant` (the field, else pasted at once, your clipboard put back after) |
| patience | seconds you must have left keyboard and mouse alone before it types, clicks or brings a window forward (0 never waits) |

Everything is decided in Kosika:

- [kit.kos](kit.kos) checks a kit, works out which hand an action needs, refuses a
  job when its kit does not allow every action in it, and checks that typed
  words are really in the field before the agent says so. It also draws a kit
  as a card.
- [main.kos](main.kos) holds the jobs (`note`, `press`, `visit`). It looks up the
  agent's kit, refuses the whole job if one action isn't allowed, and gives
  hands.py the agent's colour and speed.
- [cursor.kos](cursor.kos) and [pointer_look.kos](pointer_look.kos) draw the pointer in three
  states: `idle`, `typing` (its tag says so) and `press` (rings spread from
  the tip).
- [cards.kos](cards.kos) draws every kit as one page of cards.
- [apps.kos](apps.kos) describes the programs the hands may work in: what to
  start, which window is theirs, where their words are, the button for a
  fresh page, and the ways words may be put in. hands.py is told this with
  each action, so a note always goes on a fresh page, never on one of yours.
- [typing.kos](typing.kos) chooses how words go in: straight into the field
  through Windows' accessibility (no keyboard, the window not brought
  forward, so you can go on working), pasted, or typed by keys. For keys it
  learns, per program, the fastest rate that came out exact, from the
  read-back checks kept in `~/.kosika/hands-typing.txt`; a garbled write is
  done again slower on the page the job opened, and no model is asked.
- Every action that takes the keyboard, the mouse or the front window first
  waits until you have stopped for the kit's patience (kit.kos decides which
  actions those are); if you keep working for a minute it does nothing.

The Python files only reach Windows. hands.py sends keys and clicks,
pointer.py shows the pointer window, and ring.py shows the ring round your
mouse. eyes.ps1 reads windows through Windows' accessibility.

## Try it (PowerShell, in this folder)

    .\demo.ps1 -Agent coder-1        # coder-1 (pink) writes a note fast, then checks it
    .\demo-pointers.ps1              # coder-1 (pink) and coder-2 (sky) press menus in Notepad
    kosika run cards.kos --workflow draw --input cards.json --allow-folder . --approve

cards.json is `{"a": {"file": "kits.html"}}`.

## The diary

Every action the hands take is in the run's trace. `diary.kos` draws them on
one animated timeline, in each agent's colour, with who approved each action
and how it ended (typed words are never shown, only how many letters):

    kosika run main.kos --workflow note --input note.json --trace traces/note-1.jsonl ...
    python diary.py traces --out diary.html

## Approving away from the terminal

`--approve-in FOLDER` instead of `--approve` asks for each action in a folder
and waits for the answer there, so it can come from a phone or another window:

    kosika answer FOLDER          # what is waiting
    kosika answer FOLDER yes      # approve the one action waiting

From your phone, on the same Wi-Fi as the laptop:

    kosika answer FOLDER --serve  # prints an address with a key; open it on the phone

The page shows each waiting action with a Yes and a No button. Without the
key in the address nothing is shown or answered. Windows may ask once whether
Python may accept connections on your private network.

## Skills that grow

A run that worked, every step done, can become a skill note. You approve it
before it is kept, and a learned note never replaces another:

    python diary.py traces --lesson 3 --name open-and-write-a-note --when "note, notepad"
    kosika run learn.kos --workflow learn --input lesson.json --allow-shell --allow-folder . --approve

`learn.kos` decides whether the run can be learned from and writes the note
(`skills/learned-NAME.md`); the next job that fits its When words is given it.

## The live board

One card for every agent, in its colour, with what it did last, its counts,
and a glowing "waiting for your yes" when one of its actions is asked about:

    python diary.py traces --board --asks FOLDER --watch    # keeps board.html fresh; open it in a browser

## Real runs kept as tests

    python diary.py traces --tests recorded.kos

writes each recorded run as a Kosika test that gives its real results to
`done` in main.kos and expects the same ending. If a change would judge a
real run differently, `kosika test recorded.kos` fails. Typed words are not kept.

## Told in words, planned by its own model

`plan.kos` lets an agent take a request in words. The model in its kit
(Kosika's virtual models through the router) only picks one job from a menu
of the jobs the kit allows; Kosika checks the pick, makes the commands, and
each waits for your yes:

    kosika run plan.kos --workflow go --input ask.json --allow-shell --allow-folder . --model kosika-virtual --model-url http://127.0.0.1:8791/v1 --model-protocol openai --approve

Runs that worked teach the planner: `python diary.py traces --training planner.jsonl`
adds what it was asked and answered in each run whose every step was done.

## MCP servers

    kosika connect fetch="uvx mcp-server-fetch" --out fetch_tools.kos   # declare a server's tools once
    kosika run job.kos --mcp fetch="uvx mcp-server-fetch" ...            # grant it to one run

Any command starts a server (npx, uvx, node, python, an .exe), several may be
granted at once, and a tool the server does not mark read-only is declared
`access: execute`, so every call waits for a yes. `reach.kos`'s `mcp_flags`
gives the `--mcp` flags for a kit's servers. (The Go engine takes servers
from an environment, each pinned to a manifest.)

## Names and mascots

Every agent has a name and a mascot, drawn and animated in Kosika
(`mascots.kos`): Kosi the sun (Kosika), Nova the fox (coder-1), Quark the
owl (coder-2) and Comet the hare (scout). Each is drawn in its agent's own
colour and moves with how the agent is doing: it breathes and blinks when
idle, bobs with a question bubble while it waits for your yes, twitches its
ears while typing, hops when a job is done and droops when one is not. The
pointer's tag shows the name, and the kit cards, the live board and the
diary show the mascot. An agent with no mascot yet is a spark with its own
name; give it one by adding a line to `mascots()`.

    kosika run mascots.kos --workflow sheet --input sheet.json    # every mascot in every state
