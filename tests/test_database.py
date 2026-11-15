"""A db tool reaches only the database the host names, and only as far as its
operation allows: a query and a rows call cannot write, a run needs a yes, and
all three replay. A rows call maps the columns onto the fields of the record
the program declares, and refuses a row that does not fit."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.agent import action_hash
from kosika.checker import CheckError, check
from kosika.database import (
    MAX_CELL_CHARACTERS,
    MAX_PARAMETERS,
    MAX_ROWS,
    MAX_SQL_CHARACTERS,
    DatabaseError,
    bind_database_tool,
    granted_tables,
    named_file,
    sent_problem,
)
from kosika.parser import parse
from kosika.runtime import run
from kosika.system_tools import DB_SOURCE, SystemToolError, bind_system_tools

QUERY = """
record Statement { sql: Text values: Text[] }
record Grid { columns: Text[] rows: Text[][] more: Bool }

tool ask_db {
  source system "db"
  operation: "query"
  access: observe
  input: Statement
  output: Grid
}

agent hands {
  can: [ask_db]
  max_steps: 1
}

workflow main(ask: Statement) -> Grid {
  found = execute hands.ask_db(ask.sql, ask.values)
}
"""

TYPED = """
record Statement { sql: Text values: Text[] }
record Note { id: Number title: Text done: Bool }
record Notes { rows: Note[] more: Bool }

tool read_notes {
  source system "db"
  operation: "rows"
  access: observe
  input: Statement
  output: Notes
}

agent hands {
  can: [read_notes]
  max_steps: 1
}

workflow main(ask: Statement) -> Notes {
  found = execute hands.read_notes(ask.sql, ask.values)
}
"""

WRITE = """
record Statement { sql: Text values: Text[] }
record Changed { changed: Number }

tool add_row {
  source system "db"
  operation: "run"
  access: execute
  input: Statement
  output: Changed
}

agent hands {
  can: [add_row]
  max_steps: 1
}

workflow main(one: Statement) -> Changed {
  require approval
  done = execute hands.add_row(one.sql, one.values)
}
"""


def checked(source: str):
    program = parse(source)
    check(program)
    return program


class DeclarationTests(unittest.TestCase):
    def test_an_unknown_db_operation_is_refused(self) -> None:
        with self.assertRaisesRegex(CheckError, "unknown db operation 'read'"):
            checked(QUERY.replace('operation: "query"', 'operation: "read"'))

    def test_a_query_may_only_look_and_a_run_must_not(self) -> None:
        checked(QUERY)  # observe is allowed for a query
        with self.assertRaisesRegex(CheckError, "must declare access: execute"):
            checked(WRITE.replace("access: execute", "access: observe"))

    def test_a_workflow_with_a_run_must_state_require_approval(self) -> None:
        with self.assertRaisesRegex(
            CheckError, "needs approval before execute tool 'add_row'"
        ):
            checked(WRITE.replace("  require approval\n", ""))

    def test_the_records_must_have_exactly_the_declared_fields(self) -> None:
        with self.assertRaisesRegex(CheckError, "must have exactly the fields"):
            checked(QUERY.replace("rows: Text[][]", "rows: Text[]"))
        with self.assertRaisesRegex(CheckError, "must have exactly the fields"):
            checked(QUERY.replace("more: Bool", "more: Number"))

    def test_a_rows_answer_is_the_records_the_program_declares(self) -> None:
        checked(TYPED)  # {rows: Note[], more: Bool}, every field a cell can be
        with self.assertRaisesRegex(CheckError, "must have exactly the fields"):
            checked(TYPED.replace("rows: Note[] more: Bool", "rows: Note[]"))
        with self.assertRaisesRegex(CheckError, "must have exactly the fields"):
            checked(TYPED.replace("more: Bool", "more: Text"))
        # Only the types a cell can be read as may be a field of a row.
        with self.assertRaisesRegex(CheckError, "every field is Text, Number or Bool"):
            checked(TYPED.replace("title: Text", "tags: Text[]"))
        with self.assertRaisesRegex(CheckError, "Unknown field type 'Ghost'"):
            checked(TYPED.replace("rows: Note[]", "rows: Ghost[]"))
        with self.assertRaisesRegex(CheckError, "unknown db operation 'read'"):
            checked(TYPED.replace('operation: "rows"', 'operation: "read"'))

    def test_a_rows_call_may_only_look_and_shares_the_grant(self) -> None:
        program = checked(TYPED)
        with tempfile.TemporaryDirectory() as directory:
            bound = bind_system_tools(
                program, folder=None, database=Path(directory) / "notes.db"
            )
        self.assertEqual(list(bound), ["read_notes"])
        self.assertEqual(bind_system_tools(program, folder=None), {})

    def test_without_the_grant_nothing_is_bound(self) -> None:
        self.assertEqual(bind_system_tools(checked(QUERY), folder=None), {})
        with tempfile.TemporaryDirectory() as directory:
            bound = bind_system_tools(
                checked(QUERY),
                folder=None,
                database=Path(directory) / "notes.db",
            )
        self.assertEqual(list(bound), ["ask_db"])

    def test_a_database_name_that_is_not_a_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DatabaseError, "is a folder"):
                named_file(Path(directory))
            with self.assertRaisesRegex(DatabaseError, "not an existing folder"):
                named_file(Path(directory) / "gone" / "notes.db")


class QueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.file = self.root / "notes.db"
        connection = sqlite3.connect(self.file)
        connection.execute(
            "create table notes (id integer primary key, title text, note text)"
        )
        connection.execute("insert into notes (title, note) values ('first', null)")
        connection.execute("insert into notes (title, note) values ('second', 1.5)")
        connection.commit()
        connection.close()
        self.look = bind_database_tool(self.file)

    def tool(self, operation: str = "query"):
        from kosika.ast import ToolDecl

        return ToolDecl("ask_db", "system:db", operation, "observe", "Grid", "Statement")

    def ask(self, sql: str, values: list[str] | None = None, operation="query"):
        return self.look(self.tool(operation), {"sql": sql, "values": values or []})

    def test_a_query_gives_a_grid_of_text(self) -> None:
        found = self.ask("select id, title from notes order by id")
        self.assertEqual(found["columns"], ["id", "title"])
        self.assertEqual(found["rows"], [["1", "first"], ["2", "second"]])
        self.assertFalse(found["more"])

    def test_a_number_is_written_as_kosika_writes_numbers_and_absent_is_empty(
        self,
    ) -> None:
        found = self.ask("select note from notes order by id")
        self.assertEqual(found["rows"], [[""], ["1.5"]])

    def test_a_query_cannot_change_the_database(self) -> None:
        for sql in (
            "delete from notes",
            "insert into notes (title) values ('sneaky')",
            "update notes set title = 'changed'",
            "drop table notes",
        ):
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(DatabaseError, "a query may only look"):
                    self.ask(sql)
        self.assertEqual(self.ask("select count(*) from notes")["rows"], [["2"]])

    def test_a_query_never_creates_a_database(self) -> None:
        gone = bind_database_tool(self.root / "missing.db")
        with self.assertRaisesRegex(DatabaseError, "a query never creates one"):
            gone(self.tool(), {"sql": "select 1", "values": []})
        self.assertFalse((self.root / "missing.db").exists())

    def test_two_statements_at_once_are_refused(self) -> None:
        with self.assertRaisesRegex(DatabaseError, "one statement at a time"):
            self.ask("select 1; select 2")

    def test_only_values_are_bound_and_a_missing_one_is_refused(self) -> None:
        found = self.ask("select title from notes where id = ?", ["2"])
        self.assertEqual(found["rows"], [["second"]])
        with self.assertRaises(DatabaseError):
            self.ask("select title from notes where id = ?")

    def test_more_says_when_the_row_limit_cut_the_answer_short(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.executemany(
            "insert into notes (title) values (?)",
            [(f"row {at}",) for at in range(MAX_ROWS + 50)],
        )
        connection.commit()
        connection.close()
        found = self.ask("select title from notes")
        self.assertEqual(len(found["rows"]), MAX_ROWS)
        self.assertTrue(found["more"])

    def test_a_long_cell_is_cut_and_says_so(self) -> None:
        found = self.ask("select length(?)", ["x" * 100])
        self.assertEqual(found["rows"], [["100"]])
        long = self.ask("select ?", ["y" * (MAX_CELL_CHARACTERS + 10)])
        self.assertEqual(long["rows"][0][0], "y" * MAX_CELL_CHARACTERS + "[cell cut]")

    def test_a_statement_that_never_ends_is_stopped(self) -> None:
        with patch("kosika.database.STATEMENT_SECONDS", 0.05):
            with self.assertRaisesRegex(DatabaseError, "was stopped"):
                self.ask(
                    "with recursive counting(x) as "
                    "(select 1 union all select x + 1 from counting) "
                    "select max(x) from counting"
                )

    def test_the_reason_the_database_gives_is_the_reason_it_was_refused(self) -> None:
        with self.assertRaisesRegex(DatabaseError, "no such table: nowhere"):
            self.ask("select * from nowhere")


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.file = self.root / "notes.db"
        self.change = bind_database_tool(self.file)

    def tool(self):
        from kosika.ast import ToolDecl

        return ToolDecl("add_row", "system:db", "run", "execute", "Changed", "Statement")

    def do(self, sql: str, values: list[str] | None = None):
        return self.change(self.tool(), {"sql": sql, "values": values or []})

    def look(self, sql: str):
        """The same file, reached through a tool that may only query it."""

        from kosika.ast import ToolDecl

        tool = ToolDecl("ask_db", "system:db", "query", "observe", "Grid", "Statement")
        return bind_database_tool(self.file)(tool, {"sql": sql, "values": []})

    def test_a_run_changes_the_database_and_says_how_many_rows(self) -> None:
        self.assertEqual(
            self.do("create table notes (title text unique)"), {"changed": 0}
        )
        self.assertEqual(
            self.do("insert into notes (title) values (?)", ["written"]), {"changed": 1}
        )
        self.assertEqual(self.do("select count(*) from notes"), {"changed": 0})

    def test_a_statement_that_fails_changes_nothing(self) -> None:
        self.do("create table notes (title text unique)")
        self.do("insert into notes (title) values (?)", ["only one"])
        with self.assertRaisesRegex(DatabaseError, "UNIQUE constraint failed"):
            self.do("insert into notes (title) values (?)", ["only one"])
        self.assertEqual(self.look("select count(*) from notes")["rows"], [["1"]])

    def test_an_empty_statement_is_refused(self) -> None:
        with self.assertRaisesRegex(DatabaseError, "cannot be empty"):
            self.do("   ")


class TypedRowsTests(unittest.TestCase):
    """The columns of an answer, mapped onto the fields of a declared record."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.file = self.root / "notes.db"
        self.program = checked(TYPED)
        self.tool = self.program.tools[0]
        self.read = bind_database_tool(self.file, self.program)

    def seed(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute(
            "create table notes (id integer primary key, title text, wordy text, "
            "done integer)"
        )
        connection.execute(
            "insert into notes (title, wordy, done) values ('first', '1.5', 1)"
        )
        connection.execute("insert into notes (title, wordy, done) values ('second', 'later', 0)")
        connection.execute("insert into notes (title, wordy, done) values ('third', null, 1)"
        )
        connection.commit()
        connection.close()

    def ask(self, sql: str, values: list[str] | None = None):
        return self.read(self.tool, {"sql": sql, "values": values or []})

    def test_the_columns_are_mapped_onto_the_fields(self) -> None:
        self.seed()
        found = self.ask("select id, title, done from notes where id < 3 order by id")
        self.assertEqual(
            found,
            {
                "rows": [
                    {"id": 1, "title": "first", "done": True},
                    {"id": 2, "title": "second", "done": False},
                ],
                "more": False,
            },
        )

    def test_a_rows_answer_comes_through_the_runtime_with_no_approval(self) -> None:
        self.seed()
        found = run(
            self.program,
            "main",
            None,
            inputs={
                "ask": {
                    "sql": "select id, title, done from notes where id = 1",
                    "values": [],
                }
            },
            tool_bindings=bind_system_tools(
                self.program, folder=None, database=self.file
            ),
        )
        self.assertEqual(
            found, {"rows": [{"id": 1, "title": "first", "done": True}], "more": False}
        )

    def test_the_answer_and_the_row_must_name_the_same_things(self) -> None:
        self.seed()
        with self.assertRaisesRegex(DatabaseError, "has a column 'wordy'"):
            self.ask("select id, title, done, wordy from notes")
        with self.assertRaisesRegex(DatabaseError, "has a field 'done'"):
            self.ask("select id, title from notes")
        with self.assertRaisesRegex(DatabaseError, "more than one column named 'done'"):
            self.ask("select id, title, done, id as done from notes")
        # The answer's order does not matter: the fields name the columns.
        self.assertEqual(
            self.ask("select title, done, id from notes where id = 1")["rows"],
            [{"title": "first", "done": True, "id": 1}],
        )

    def test_a_cell_must_be_what_its_field_says_it_is(self) -> None:
        self.seed()
        # A text where the field is Number, and a number where it is Text.
        with self.assertRaisesRegex(
            DatabaseError, r"holds the text 'first' in column 'id'"
        ):
            self.ask("select title as id, title, done from notes order by id")
        with self.assertRaisesRegex(DatabaseError, r"cast\(id as real\)"):
            self.ask("select title as id, title, done from notes order by id")
        with self.assertRaisesRegex(
            DatabaseError, r"holds the number 1 in column 'title'"
        ):
            self.ask("select id, id as title, done from notes order by id")
        with self.assertRaisesRegex(DatabaseError, r"cast\(title as text\)"):
            self.ask("select id, id as title, done from notes order by id")
        # A Bool field is true or false: neither text, nor another integer.
        for value in ("wordy", "2", "1.5"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(DatabaseError, "which is true or false"):
                    self.ask(f"select id, title, {value} as done from notes")
        # What the statement made of a cell is what is read.
        self.assertEqual(
            self.ask(
                "select id, cast(done as text) as title, done from notes where id = 1"
            )["rows"],
            [{"id": 1, "title": "1", "done": True}],
        )

    def test_an_absent_value_has_no_field_to_go_in(self) -> None:
        # A field is never absent, so the statement must say what to put there.
        self.seed()
        with self.assertRaisesRegex(
            DatabaseError, r"row 3 of the answer has nothing in column 'title'"
        ):
            self.ask("select id, wordy as title, done from notes order by id")
        with self.assertRaisesRegex(DatabaseError, "coalesce"):
            self.ask("select id, wordy as title, done from notes order by id")
        self.assertEqual(
            len(
                self.ask(
                    "select id, coalesce(wordy, '') as title, done from notes"
                )["rows"]
            ),
            3,
        )

    def test_a_rows_call_only_looks_and_never_creates_a_database(self) -> None:
        self.seed()
        with self.assertRaisesRegex(DatabaseError, "a query may only look"):
            self.ask("delete from notes")
        gone = bind_database_tool(self.root / "missing.db", self.program)
        with self.assertRaisesRegex(DatabaseError, "a rows call never creates one"):
            gone(self.tool, {"sql": "select 1", "values": []})
        self.assertFalse((self.root / "missing.db").exists())

    def test_more_says_when_the_row_limit_cut_the_answer_short(self) -> None:
        self.seed()
        connection = sqlite3.connect(self.file)
        connection.executemany(
            "insert into notes (title, done) values (?, 0)",
            [(f"row {at}",) for at in range(MAX_ROWS + 50)],
        )
        connection.commit()
        connection.close()
        found = self.ask("select id, title, done from notes")
        self.assertEqual(len(found["rows"]), MAX_ROWS)
        self.assertTrue(found["more"])


class GrantTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.file = self.root / "notes.db"
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (title text)")
        connection.commit()
        connection.close()

    def rows(self) -> list[list[str]]:
        connection = sqlite3.connect(self.file)
        found = connection.execute("select title from notes").fetchall()
        connection.close()
        return [list(row) for row in found]

    def test_a_query_runs_with_no_approval_and_gives_its_grid(self) -> None:
        program = checked(QUERY)
        found = run(
            program,
            "main",
            None,
            inputs={"ask": {"sql": "select title from notes", "values": []}},
            tool_bindings=bind_system_tools(
                program, folder=None, database=self.file
            ),
        )
        self.assertEqual(found, {"columns": ["title"], "rows": [], "more": False})

    def test_a_run_without_an_approval_is_refused_and_writes_nothing(self) -> None:
        program = checked(WRITE)
        with self.assertRaisesRegex(PermissionError, "verified approval record"):
            run(
                program,
                "main",
                None,
                inputs={
                    "one": {
                        "sql": "insert into notes (title) values (?)",
                        "values": ["unapproved"],
                    }
                },
                tool_bindings=bind_system_tools(
                    program, folder=None, database=self.file
                ),
            )
        self.assertEqual(self.rows(), [])

    def test_an_environment_may_not_be_widened_by_a_database_flag(self) -> None:
        # A run given an environment takes no grant from the command line, so
        # what it may reach is what the file says and nothing else.
        (self.root / "kosika.env").write_text(
            "environment bare {\n  keep \"./.kosika\"\n}\n", encoding="utf-8"
        )
        program = self.root / "ask.kos"
        program.write_text(QUERY, encoding="utf-8")
        inputs = self.root / "in.json"
        inputs.write_text(
            json.dumps({"ask": {"sql": "select title from notes", "values": []}}),
            encoding="utf-8",
        )
        output = io.StringIO()
        errors = io.StringIO()
        with (
            patch(
                "sys.argv",
                [
                    "kosika",
                    "run",
                    str(program),
                    "--env",
                    "bare",
                    "--database",
                    str(self.file),
                    "--input",
                    str(inputs),
                ],
            ),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            code = cli.main()
        self.assertEqual(code, 2)
        self.assertIn("--database cannot be used with --env", errors.getvalue())

    def test_an_ungranted_db_tool_fails_before_the_first_step(self) -> None:
        program = checked(QUERY)
        with self.assertRaisesRegex(SystemToolError, "not an existing folder"):
            bind_system_tools(program, folder=None, database=self.root / "gone" / "x.db")


class ApprovalAndReplayTests(unittest.TestCase):
    """The whole way a database call travels: a pause, a yes, a trace, a replay."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.file = self.root / "notes.db"
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (title text)")
        connection.commit()
        connection.close()
        self.program = self.root / "add.kos"
        self.program.write_text(WRITE, encoding="utf-8")
        self.input = self.root / "input.json"
        self.input.write_text(
            json.dumps(
                {
                    "one": {
                        "sql": "insert into notes (title) values (?)",
                        "values": ["approved"],
                    }
                }
            ),
            encoding="utf-8",
        )
        self.trace = self.root / "run.jsonl"
        self.args = [
            "kosika",
            "run",
            str(self.program),
            "--database",
            str(self.file),
            "--input",
            str(self.input),
            "--trace",
            str(self.trace),
            "--journal",
            str(self.root / "journal.sqlite"),
            "--binding-id",
            "test",
        ]
        self.values: list[str] = []

    def rows(self) -> list[list[str]]:
        connection = sqlite3.connect(self.file)
        found = connection.execute("select title from notes").fetchall()
        connection.close()
        return [list(row) for row in found]

    def tell(self, argv: list[str]) -> tuple[int, str]:
        output = io.StringIO()
        with patch("sys.argv", argv), contextlib.redirect_stdout(output):
            code = cli.main()
        return code, output.getvalue()

    def test_a_run_waits_for_a_yes_then_the_trace_replays_without_the_database(
        self,
    ) -> None:
        code, said = self.tell(self.args)
        self.assertEqual(code, 3)
        pending = json.loads(said)
        self.assertEqual(
            pending["arguments"],
            {"sql": "insert into notes (title) values (?)", "values": ["approved"]},
        )
        self.assertEqual(self.rows(), [])  # nothing happened without a yes

        approval = self.root / "approval.json"
        approval.write_text(
            json.dumps(
                {
                    "plan_hash": pending["plan_hash"],
                    "action_hash": pending["action_hash"],
                    "approved_by": "reviewer",
                }
            ),
            encoding="utf-8",
        )
        code, said = self.tell([*self.args, "--approval-record", str(approval)])
        self.assertEqual(code, 0, said)
        self.assertEqual(self.rows(), [["approved"]])

        # A replay reads the recorded answer, so it needs no database at all.
        self.file.unlink()
        code, said = self.tell(["kosika", "replay", str(self.trace)])
        self.assertEqual(code, 0, said)
        self.assertIn("changed", said)
        self.assertFalse(self.file.exists())

    def test_a_rows_answer_replays_without_the_database(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("create table typed (id integer primary key, title text, done integer)")
        connection.execute("insert into typed (title, done) values ('first', 1)")
        connection.commit()
        connection.close()
        read = self.root / "read.kos"
        read.write_text(TYPED, encoding="utf-8")
        inputs = self.root / "read.json"
        inputs.write_text(
            json.dumps(
                {"ask": {"sql": "select id, title, done from typed", "values": []}}
            ),
            encoding="utf-8",
        )
        trace = self.root / "read.jsonl"
        args = [
            "kosika",
            "run",
            str(read),
            "--database",
            str(self.file),
            "--input",
            str(inputs),
            "--trace",
            str(trace),
        ]
        code, said = self.tell(args)
        self.assertEqual(code, 0, said)
        self.assertIn("'done': True", said)
        self.file.unlink()
        code, said = self.tell(["kosika", "replay", str(trace)])
        self.assertEqual(code, 0, said)
        self.assertIn("'done': True", said)
        self.assertFalse(self.file.exists())

    def test_a_query_needs_no_approval_and_replays(self) -> None:
        ask = self.root / "ask.kos"
        ask.write_text(QUERY, encoding="utf-8")
        inputs = self.root / "ask.json"
        inputs.write_text(
            json.dumps({"ask": {"sql": "select title from notes", "values": []}}),
            encoding="utf-8",
        )
        args = [
            "kosika",
            "run",
            str(ask),
            "--database",
            str(self.file),
            "--input",
            str(inputs),
            "--trace",
            str(self.trace),
        ]
        code, said = self.tell(args)
        self.assertEqual(code, 0, said)
        self.assertIn("'columns': ['title']", said)
        self.file.unlink()
        code, said = self.tell(["kosika", "replay", str(self.trace)])
        self.assertEqual(code, 0, said)
        self.assertIn("'columns': ['title']", said)


class SentStatementTests(unittest.TestCase):
    """A recorded statement, put back to SQLite with no database present.

    `sent_problem` is the whole judgement: SQLite is asked what the statement
    binds and whether it changes anything, against the tables a run recorded.
    """

    def setUp(self) -> None:
        self.tables = {"notes": {"id": "INTEGER", "title": "TEXT"}}

    def ask(
        self,
        operation: str,
        sql: str,
        values: list[str] | None = None,
        tables: dict[str, dict[str, str]] | None = None,
    ) -> str | None:
        return sent_problem(
            operation,
            {"sql": sql, "values": values or []},
            self.tables if tables is None else tables,
        )

    def test_the_source_a_db_tool_declares_is_the_one_replay_judges(self) -> None:
        self.assertEqual(checked(QUERY).tools[0].source, DB_SOURCE)

    def test_a_read_that_binds_its_values_is_left_alone(self) -> None:
        for operation in ("query", "rows", "run"):
            with self.subTest(operation=operation):
                self.assertIsNone(
                    self.ask(operation, "select title from notes where id = ?", ["1"])
                )
                self.assertIsNone(self.ask(operation, "select title from notes"))

    def test_a_statement_that_changes_the_database_under_a_looking_tool(self) -> None:
        for operation in ("query", "rows"):
            for sql, values in (
                ("delete from notes", []),
                ("insert into notes (title) values (?)", ["x"]),
                ("update notes set title = ?", ["x"]),
                ("drop table notes", []),
                ("alter table notes add column done integer", []),
            ):
                with self.subTest(operation=operation, sql=sql):
                    problem = self.ask(operation, sql, values)
                    self.assertIn(f"a {operation} tool only looks", problem or "")

    def test_a_run_tool_may_change_the_database(self) -> None:
        # A `run` tool is what changes a database, so nothing about a statement
        # of its own is refused for changing one: what is judged is that it
        # binds the values that were sent, and that it is one statement.
        self.assertIsNone(self.ask("run", "delete from notes"))
        self.assertIsNone(self.ask("run", "insert into notes (title) values (?)", ["x"]))
        self.assertIn("uses 2", self.ask("run", "select ?, ?", ["x"]) or "")

    def test_a_statement_that_is_not_one_statement_is_refused(self) -> None:
        problem = self.ask("query", "select title from notes; drop table notes")
        self.assertIn("more than one statement", problem or "")

    def test_a_statement_binding_other_values_than_were_sent_is_refused(self) -> None:
        self.assertIn("it sends 0 values where the statement uses 1", self.ask("query", "select ?") or "")
        self.assertIn(
            "it sends 1 value where the statement uses 0",
            self.ask("query", "select title from notes", ["x"]) or "",
        )

    def test_a_statement_that_is_not_a_statement_is_refused(self) -> None:
        problem = self.ask("query", "select title frm notes")
        self.assertIn("not a statement SQLite can read", problem or "")

    def test_a_statement_on_tables_the_run_never_found_is_left_alone(self) -> None:
        # A run may make a table of its own, and a recorded schema is a
        # snapshot, so what it does not have is not judged.
        for sql in (
            "select tag from tags",
            "select nosuch from notes",
            "insert into notes (nosuch) values (?)",
            "select json_extract(title, '$.a') from missing",
        ):
            with self.subTest(sql=sql):
                self.assertIsNone(self.ask("query", sql, ["x"]))

    def test_a_pragma_is_left_alone(self) -> None:
        # One pragma reads and another writes, and SQLite reports both the same
        # way, so a pragma is not guessed at.
        for sql in ("pragma table_info(notes)", "pragma journal_mode=wal"):
            with self.subTest(sql=sql):
                self.assertIsNone(self.ask("query", sql))

    def test_an_explain_is_left_alone(self) -> None:
        # An `explain` lists a program without running it, so a tool that only
        # looks may send one of a statement that would change the database.
        for sql in ("explain delete from notes", "explain select title from notes"):
            with self.subTest(sql=sql):
                self.assertIsNone(self.ask("query", sql))

    def test_a_trace_that_records_no_tables_is_judged_on_nothing(self) -> None:
        self.assertIsNone(self.ask("query", "delete from notes", tables={}))

    def test_limits_and_shapes_are_refused_by_name(self) -> None:
        self.assertEqual(self.ask("query", ""), "its statement is empty")
        self.assertEqual(self.ask("query", "   "), "its statement is empty")
        self.assertEqual(
            self.ask("query", "select 1" * MAX_SQL_CHARACTERS),
            f"its statement is longer than {MAX_SQL_CHARACTERS} characters",
        )
        self.assertEqual(
            self.ask("query", "select ?", ["x"] * (MAX_PARAMETERS + 1)),
            f"it binds more than {MAX_PARAMETERS} values",
        )
        self.assertIn(
            "names a db operation",
            sent_problem("read", {"sql": "select 1", "values": []}, self.tables)
            or "",
        )
        self.assertEqual(sent_problem("query", None, self.tables), "its arguments are not a record")
        self.assertEqual(
            sent_problem("query", {"sql": "select 1", "values": [1]}, self.tables),
            "its values are not the text a db call binds",
        )


class GrantedTablesTests(unittest.TestCase):
    """What a run records about the database it was given."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def test_the_tables_of_a_database_are_recorded(self) -> None:
        file = self.root / "notes.db"
        connection = sqlite3.connect(file)
        connection.execute("create table notes (id integer primary key, title text)")
        connection.execute("create table tags (tag text)")
        connection.commit()
        connection.close()
        self.assertEqual(
            granted_tables(file),
            {"notes": {"id": "INTEGER", "title": "TEXT"}, "tags": {"tag": "TEXT"}},
        )

    def test_nothing_is_recorded_for_no_database_or_an_empty_one(self) -> None:
        self.assertIsNone(granted_tables(None))
        untouchable = self.root / "not-a-database.txt"
        untouchable.write_text("notes, and not a database", encoding="utf-8")
        self.assertIsNone(granted_tables(untouchable))
        # A `run` tool makes the file, so a file that is not there yet is an
        # empty record of tables rather than nothing at all.
        self.assertEqual(granted_tables(self.root / "later.db"), {})


class RecordedStatementReplayTests(unittest.TestCase):
    """A recorded database run, and what `kosika replay` makes of it.

    The tables the run found are in the trace, so a statement that is not one
    the tool could have sent is refused with no database and nothing run.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.file = self.root / "notes.db"
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (id integer primary key, title text)")
        connection.execute("create table tags (tag text)")
        connection.execute("insert into notes (title) values ('first')")
        connection.commit()
        connection.close()
        self.program = self.root / "ask.kos"
        self.program.write_text(QUERY, encoding="utf-8")
        self.input = self.root / "ask.json"
        self.input.write_text(
            json.dumps({"ask": {"sql": "select title from notes", "values": []}}),
            encoding="utf-8",
        )
        self.trace = self.root / "run.jsonl"
        self.args = [
            "kosika",
            "run",
            str(self.program),
            "--database",
            str(self.file),
            "--input",
            str(self.input),
            "--trace",
            str(self.trace),
        ]
        code, said, errors = self.tell(self.args)
        self.assertEqual(code, 0, errors or said)

    def tell(self, argv: list[str]) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch("sys.argv", argv),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            code = cli.main()
        return code, output.getvalue(), errors.getvalue()

    def events(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.trace.read_text(encoding="utf-8").splitlines()
        ]

    def written(self, events: list[dict], name: str) -> tuple[int, str, str]:
        path = self.root / f"{name}.jsonl"
        path.write_text(
            "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
            encoding="utf-8",
        )
        return self.tell(["kosika", "replay", str(path)])

    def doctored(
        self,
        name: str,
        sql: str | None,
        values: list[str] | None = None,
    ) -> tuple[int, str, str]:
        events = self.events()
        if sql is not None:
            for event in events:
                if event.get("event") == "tool_call":
                    event["arguments"] = {"sql": sql, "values": values or []}
        return self.written(events, name)

    def test_a_run_records_the_tables_its_database_had(self) -> None:
        self.assertEqual(
            self.events()[0]["database"],
            {"notes": {"id": "INTEGER", "title": "TEXT"}, "tags": {"tag": "TEXT"}},
        )

    def test_the_run_it_made_replays_with_no_database_present(self) -> None:
        self.file.unlink()
        code, said, errors = self.tell(["kosika", "replay", str(self.trace)])
        self.assertEqual(code, 0, errors)
        self.assertIn("'title'", said)
        self.assertFalse(self.file.exists())

    def test_a_statement_that_changes_the_database_is_refused(self) -> None:
        for name, sql in (
            ("delete", "delete from notes"),
            ("drop", "drop table notes"),
            ("insert", "insert into notes (title) values ('sneaky')"),
        ):
            with self.subTest(sql=sql):
                code, _said, errors = self.doctored(name, sql)
                self.assertEqual(code, 2)
                self.assertIn("could not have been sent", errors)
                self.assertIn("it changes the database, and a query tool only looks", errors)

    def test_a_statement_binding_other_values_is_refused(self) -> None:
        code, _said, errors = self.doctored("few", "select title from notes where id = ?")
        self.assertEqual(code, 2)
        self.assertIn("it sends 0 values where the statement uses 1", errors)
        code, _said, errors = self.doctored("many", "select title from notes", ["first"])
        self.assertEqual(code, 2)
        self.assertIn("it sends 1 value where the statement uses 0", errors)

    def test_two_statements_at_once_are_refused(self) -> None:
        code, _said, errors = self.doctored(
            "two", "select title from notes; drop table notes"
        )
        self.assertEqual(code, 2)
        self.assertIn("it is more than one statement", errors)

    def test_a_statement_that_is_not_a_statement_is_refused(self) -> None:
        code, _said, errors = self.doctored("broken", "select title frm notes")
        self.assertEqual(code, 2)
        self.assertIn("not a statement SQLite can read", errors)

    def test_a_statement_on_a_table_the_run_never_found_is_left_alone(self) -> None:
        for name, sql in (("other", "select tag from tags"), ("gone", "select title from elsewhere")):
            with self.subTest(sql=sql):
                code, said, errors = self.doctored(name, sql)
                self.assertEqual(code, 0, errors)
                self.assertIn("'title'", said)

    def test_an_explain_of_a_change_is_left_alone(self) -> None:
        # An `explain` never runs what it names, so a run that only looks may
        # have sent one, and a refusal here would refuse a run that happened.
        code, _said, errors = self.doctored("explain", "explain delete from notes")
        self.assertEqual(code, 0, errors)

    def test_a_trace_that_records_no_tables_still_replays(self) -> None:
        events = self.events()
        del events[0]["database"]
        code, said, errors = self.written(events, "older")
        self.assertEqual(code, 0, errors)
        self.assertIn("'title'", said)
        # And one whose recorded tables are not a record of tables is left be.
        events = self.events()
        events[0]["database"] = "notes"
        code, _said, errors = self.written(events, "garbled")
        self.assertEqual(code, 0, errors)


NESTED_WRITE = """
record Statement { sql: Text values: Text[] }
record Changed { changed: Number }

tool add_row {
  source system "db"
  operation: "run"
  access: execute
  input: Statement
  output: Changed
}

agent hands {
  can: [add_row]
  max_steps: 1
}

workflow inner(one: Statement) -> Changed {
  require approval
  done = execute hands.add_row(one.sql, one.values)
}

workflow main(one: Statement) -> Changed {
  require approval
  changed = call inner(one)
}
"""


class RecordedApprovalTests(unittest.TestCase):
    """The approval a write was given: in its trace, and what a replay makes
    of it.

    A run records who approved the call, the plan it was given for and the
    hash the approval ledger claimed. That hash covers the step path, the tool
    and the exact arguments, so a replay recomputes it and refuses a statement
    or values that were edited after the yes.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.file = self.root / "notes.db"
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (title text)")
        connection.commit()
        connection.close()
        self.program = self.root / "add.kos"
        self.program.write_text(WRITE, encoding="utf-8")
        self.input = self.root / "input.json"
        self.input.write_text(
            json.dumps(
                {
                    "one": {
                        "sql": "insert into notes (title) values (?)",
                        "values": ["approved"],
                    }
                }
            ),
            encoding="utf-8",
        )
        self.trace = self.root / "run.jsonl"
        self.args = [
            "kosika",
            "run",
            str(self.program),
            "--database",
            str(self.file),
            "--input",
            str(self.input),
            "--trace",
            str(self.trace),
            "--journal",
            str(self.root / "journal.sqlite"),
            "--binding-id",
            "test",
        ]
        code, said, errors = self.tell(self.args)
        self.assertEqual(code, 3, errors or said)
        self.request = json.loads(said)
        approval = self.root / "approval.json"
        approval.write_text(
            json.dumps(
                {
                    "plan_hash": self.request["plan_hash"],
                    "action_hash": self.request["action_hash"],
                    "approved_by": "reviewer",
                }
            ),
            encoding="utf-8",
        )
        code, said, errors = self.tell([*self.args, "--approval-record", str(approval)])
        self.assertEqual(code, 0, errors or said)

    def tell(self, argv: list[str]) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch("sys.argv", argv),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            code = cli.main()
        return code, output.getvalue(), errors.getvalue()

    def events(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.trace.read_text(encoding="utf-8").splitlines()
        ]

    def call(self) -> dict:
        """The one recorded call to the database."""

        return next(event for event in self.events() if event.get("event") == "tool_call")

    def written(self, events: list[dict], name: str) -> tuple[int, str, str]:
        path = self.root / f"{name}.jsonl"
        path.write_text(
            "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
            encoding="utf-8",
        )
        return self.tell(["kosika", "replay", str(path)])

    def doctored(self, name: str, change) -> tuple[int, str, str]:
        events = self.events()
        for event in events:
            if event.get("event") == "tool_call":
                change(event)
        return self.written(events, name)

    def test_a_write_records_the_approval_it_was_given(self) -> None:
        approval = self.call()["approval"]
        self.assertEqual(
            approval,
            {
                "plan_hash": self.request["plan_hash"],
                "action_hash": self.request["action_hash"],
                "approved_by": "reviewer",
                # the whole step path the hash is over
                "step": ["main", "done"],
            },
        )
        # The hash the ledger claimed is over this step, this tool and the
        # arguments as they were sent, so a replay can recompute it.
        self.assertEqual(
            approval["action_hash"],
            action_hash(
                approval["plan_hash"],
                json.dumps(["main", "done", "add_row"]),
                self.call()["arguments"],
            ),
        )

    def test_the_approved_write_replays_with_no_database_present(self) -> None:
        self.file.unlink()
        code, said, errors = self.tell(["kosika", "replay", str(self.trace)])
        self.assertEqual(code, 0, errors)
        self.assertIn("changed", said)
        self.assertFalse(self.file.exists())

    def test_values_the_approval_does_not_cover_are_refused(self) -> None:
        def replace(event: dict) -> None:
            event["arguments"] = {
                "sql": "insert into notes (title) values (?)",
                "values": ["sneaky"],
            }

        code, _said, errors = self.doctored("values", replace)
        self.assertEqual(code, 2)
        self.assertIn("was not approved as recorded", errors)
        self.assertIn("its action hash is over other arguments", errors)

    def test_a_statement_the_approval_does_not_cover_is_refused(self) -> None:
        # One value either way, and a `run` tool may change the database, so
        # only the approval can tell this from the statement that was sent.
        def replace(event: dict) -> None:
            event["arguments"] = {
                "sql": "insert into notes (title) values (upper(?))",
                "values": ["approved"],
            }

        code, _said, errors = self.doctored("statement", replace)
        self.assertEqual(code, 2)
        self.assertIn("was not approved as recorded", errors)

    def test_an_approval_that_is_not_a_record_is_refused(self) -> None:
        for name, approval in (
            ("short", {"action_hash": self.request["action_hash"]}),
            (
                "blank",
                {
                    "plan_hash": self.request["plan_hash"],
                    "action_hash": self.request["action_hash"],
                    "approved_by": " ",
                },
            ),
            (
                "extra",
                {
                    "plan_hash": self.request["plan_hash"],
                    "action_hash": self.request["action_hash"],
                    "approved_by": "reviewer",
                    "reason": "because",
                },
            ),
        ):
            with self.subTest(approval=name):
                code, _said, errors = self.doctored(
                    name, lambda event, value=approval: event.update(approval=value)
                )
                self.assertEqual(code, 2)
                self.assertIn("was not approved as recorded", errors)

    def test_a_trace_whose_approval_was_taken_out_still_replays(self) -> None:
        # What is left alone is said here rather than hidden: a trace records
        # the approval it was given, and one that does not say it was given
        # one is judged on the call alone, as an older trace is.
        code, said, errors = self.doctored("stripped", lambda event: event.pop("approval"))
        self.assertEqual(code, 0, errors)
        self.assertIn("changed", said)

    def test_a_write_in_a_called_workflow_is_tied_to_its_own_step(self) -> None:
        program = self.root / "nested.kos"
        program.write_text(NESTED_WRITE, encoding="utf-8")
        trace = self.root / "nested.jsonl"
        args = [
            "kosika",
            "run",
            str(program),
            "--database",
            str(self.file),
            "--input",
            str(self.input),
            "--trace",
            str(trace),
            "--journal",
            str(self.root / "nested.sqlite"),
            "--binding-id",
            "test",
        ]
        code, said, errors = self.tell(args)
        self.assertEqual(code, 3, errors or said)
        request = json.loads(said)
        approval = self.root / "nested-approval.json"
        approval.write_text(
            json.dumps(
                {
                    "plan_hash": request["plan_hash"],
                    "action_hash": request["action_hash"],
                    "approved_by": "reviewer",
                }
            ),
            encoding="utf-8",
        )
        code, said, errors = self.tell([*args, "--approval-record", str(approval)])
        self.assertEqual(code, 0, errors or said)
        self.assertEqual(self.tell(["kosika", "replay", str(trace)])[0], 0)

        def nested_named(events: list[dict]) -> dict:
            for event in events:
                if event.get("event") == "workflow_return":
                    return event
            raise AssertionError("no called workflow in the trace")

        top = [
            json.loads(line)
            for line in trace.read_text(encoding="utf-8").splitlines()
        ]
        called = nested_named(top)["events"]
        inner_call = next(
            event for event in called if event.get("event") == "tool_call"
        )
        self.assertEqual(
            inner_call["approval"]["action_hash"],
            action_hash(
                inner_call["approval"]["plan_hash"],
                json.dumps(["main", "changed", "inner", "done", "add_row"]),
                inner_call["arguments"],
            ),
        )


@unittest.skipUnless(
    shutil.which("go"), "Go is not installed, so the native engine is not built"
)
class NativeEngineTests(unittest.TestCase):
    """The two engines must agree that `db` is a declared kind, not a typo.

    The native engine does not reach a database yet, so it refuses the
    declaration by name; what it must never say is that the source is
    unknown, which would mean the two checkers disagree about the language.
    """

    engine: Path
    _dir: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._dir.cleanup)
        cls.engine = Path(cls._dir.name) / "kosika-native.exe"
        built = subprocess.run(
            ["go", "build", "-o", str(cls.engine), "."],
            cwd=Path(__file__).resolve().parents[1] / "native",
            capture_output=True,
            text=True,
        )
        if built.returncode != 0:
            raise AssertionError("the native engine does not build:\n" + built.stderr)

    def test_the_native_engine_refuses_a_db_tool_by_name(self) -> None:
        for source in (QUERY, TYPED, WRITE):
            with self.subTest(source=source.split("operation")[1][:20]):
                with tempfile.TemporaryDirectory() as scratch:
                    program = Path(scratch) / "ask.kos"
                    program.write_text(source, encoding="utf-8")
                    ran = subprocess.run(
                        [str(self.engine), "test", str(program)], capture_output=True
                    )
                said = ran.stderr.decode("utf-8", "replace")
                self.assertEqual(ran.returncode, 1)
                self.assertIn("cannot read a system db tool yet", said)
                self.assertNotIn("unknown system source", said)


if __name__ == "__main__":
    unittest.main()
