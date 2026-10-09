"""Files and web pages in one line, with no tool to declare.

    workflow main(msg: Msg) -> Out {
      note  = read file "notes/todo.txt"            // FileContent: note.text
      names = list files "notes"                     // FileNames: names.names
      page  = fetch "https://en.wikipedia.org/wiki/Tea"   // WebPage
      saved = write file "out/report.txt" with report.text  // FileSaved
      out   = build(note, page)
      require approval
    }

These are short ways to write what `tool` declarations say at length. Each
step is lowered to an ordinary tool call on a `system "files"` or
`system "web"` tool, so everything that is true of those tools is true here:

* Writing the step reaches nothing. The person running the program still
  grants the folder (`--allow-folder`) or the site (`--allow-site`), and a
  path that leaves the folder is refused.
* `write file` changes something, so it is an `execute` tool: the workflow
  must say `require approval` and each write waits for a yes that shows the
  path and the text.
* What was read is in the trace, so a run that read a file replays without
  the file.

They are workflow steps, not functions: a function must give the same result
for the same arguments, and a file can change.
"""

from __future__ import annotations

from dataclasses import replace

from .ast import AgentDecl, Field, Program, RecordDecl, ToolCall, ToolDecl

#: The agent these steps run through. It can use only the tools below.
AGENT = "direct"

RECORDS: dict[str, tuple[Field, ...]] = {
    "FilePath": (Field("path", "Text"),),
    "FileContent": (Field("text", "Text"),),
    "FileNames": (Field("names", "Text[]"),),
    "FileChange": (Field("path", "Text"), Field("text", "Text")),
    "FileSaved": (Field("bytes", "Number"),),
    "WebAddress": (Field("url", "Text"),),
    "WebPage": (Field("url", "Text"), Field("title", "Text"), Field("text", "Text")),
}

#: tool name -> (declaration, the records it needs)
TOOLS: dict[str, tuple[ToolDecl, tuple[str, ...]]] = {
    "file_read": (
        ToolDecl(
            "file_read", "system:files", "read", "observe", "FileContent", "FilePath"
        ),
        ("FilePath", "FileContent"),
    ),
    "file_list": (
        ToolDecl(
            "file_list", "system:files", "list", "observe", "FileNames", "FilePath"
        ),
        ("FilePath", "FileNames"),
    ),
    "file_write": (
        ToolDecl(
            "file_write", "system:files", "write", "execute", "FileSaved", "FileChange"
        ),
        ("FileChange", "FileSaved"),
    ),
    "web_fetch": (
        ToolDecl(
            "web_fetch", "system:web", "fetch", "observe", "WebPage", "WebAddress"
        ),
        ("WebAddress", "WebPage"),
    ),
}


def _calls(program: Program) -> list[str]:
    used: list[str] = []
    for workflow in program.workflows:
        for step in workflow.steps:
            for call in (
                step.call,
                step.fallback,
                *(branch for _, branch in step.branches),
            ):
                if (
                    isinstance(call, ToolCall)
                    and call.agent_name == AGENT
                    and call.tool_name in TOOLS
                    and call.tool_name not in used
                ):
                    used.append(call.tool_name)
    return sorted(used)


def supplied_names(program: Program) -> set[str]:
    """The names a module may use because it has these steps."""

    used = _calls(program)
    names: set[str] = {AGENT, *used} if used else set()
    for tool in used:
        names |= set(TOOLS[tool][1])
    return names


def lower_direct(program: Program) -> Program:
    """Add the records, tools and agent that the one-line steps stand for.

    A program with none of these steps is returned unchanged. A name the
    program already declares is left as written.
    """

    used = _calls(program)
    if not used or any(agent.name == AGENT for agent in program.agents):
        return program
    known = {record.name for record in program.records}
    taken = {tool.name for tool in program.tools}
    wanted: list[str] = []
    for tool in used:
        for record in TOOLS[tool][1]:
            if record not in known and record not in wanted:
                wanted.append(record)
    return replace(
        program,
        records=[
            *program.records,
            *(RecordDecl(name, RECORDS[name]) for name in wanted),
        ],
        tools=[
            *program.tools,
            *(TOOLS[name][0] for name in used if name not in taken),
        ],
        agents=[*program.agents, AgentDecl(AGENT, tuple(used), (), max_steps=1)],
    )
