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
| typing | `steady` (15 letters a second), `fast` (about 60) or `instant` (pasted at once, your clipboard put back after) |

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
