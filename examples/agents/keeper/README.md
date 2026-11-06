# The keeper

Agents that run on their own, safely.

```bash
mkdir out
kosika-native keep keeper.json
```

`keeper.json` lists jobs: a program, a workflow, its input, how often (`every_minutes`) and how many times (`times`, 0 for ever). Each run is its own process of the engine. Every run leaves its output, trace and log under `keeper.runs/JOB/`, and one line in `keeper.runs/keeper.jsonl`. A run that fails is written down and tried again at its next turn.

`rules.json` is a standing permission: the note agent may write files under `notes/` and nowhere else. Anything a rule does not cover still waits for a person.
