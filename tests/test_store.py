"""A program may declare the tables it means in a database file. The words of
that declaration are checked with no file at all; the file itself is checked
before the first step of a run, and a table or column that has drifted is
refused by name. `kosika db init` writes what a store declares, and
`kosika db inspect` says what a file holds."""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.checker import CheckError, check
from kosika.database import (
    DatabaseError,
    affinity_of,
    apply_init,
    check_stores,
    describe,
    plan_init,
    store_for,
    store_problems,
)
from kosika.modules import load_program
from kosika.parser import ParseError, parse

SOURCE = """
record Statement { sql: Text values: Text[] }
record Grid { columns: Text[] rows: Text[][] more: Bool }

store Ledger at "./notes.db" {
  table notes {
    id: Number
    title: Text
    done: Bool
  }
  table log {
    at: Text
  }
}

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


def checked(source: str):
    program = parse(source)
    check(program)
    return program


class DeclarationTests(unittest.TestCase):
    def test_a_store_says_its_file_and_its_tables(self) -> None:
        store = checked(SOURCE).stores[0]
        self.assertEqual(store.name, "Ledger")
        self.assertEqual(store.file, "./notes.db")
        self.assertEqual([table.name for table in store.tables], ["notes", "log"])
        self.assertEqual(
            [(column.name, column.type_name) for column in store.tables[0].columns],
            [("id", "Number"), ("title", "Text"), ("done", "Bool")],
        )

    def test_the_declaration_is_checked_with_no_database_at_all(self) -> None:
        program = checked(SOURCE)
        self.assertEqual(len(program.stores), 1)

    def test_a_column_that_is_not_a_row_type_is_refused(self) -> None:
        source = SOURCE.replace("id: Number", "id: Moment")
        with self.assertRaisesRegex(
            CheckError, "a column of a store is one of: Text, Number, Bool"
        ):
            checked(source)

    def test_a_column_may_not_carry_a_default(self) -> None:
        source = SOURCE.replace("done: Bool", "done: Bool = false")
        with self.assertRaisesRegex(ParseError, "a default belongs in the database"):
            parse(source)

    def test_a_store_needs_a_quoted_file(self) -> None:
        with self.assertRaisesRegex(ParseError, "needs a quoted file"):
            parse('store A at notes.db { table t { id: Number } }')

    def test_a_store_may_not_name_an_absolute_file(self) -> None:
        source = SOURCE.replace('"./notes.db"', '"/etc/notes.db"')
        with self.assertRaisesRegex(CheckError, "a path beside the program"):
            checked(source)

    def test_a_store_declares_at_least_one_table_with_one_column(self) -> None:
        with self.assertRaisesRegex(CheckError, "'A' declares no table"):
            checked("store A at \"./a.db\" { }")
        with self.assertRaisesRegex(CheckError, "declares no column"):
            checked('store A at "./a.db" { table t { } }')

    def test_nothing_is_declared_twice(self) -> None:
        with self.assertRaisesRegex(CheckError, "'A' is declared more than once"):
            checked(
                'store A at "./a.db" { table t { id: Number } }\n'
                'store A at "./b.db" { table t { id: Number } }'
            )
        with self.assertRaisesRegex(CheckError, "declares the table 't' twice"):
            checked(
                'store A at "./a.db" {\n'
                "  table t { id: Number }\n"
                "  table t { id: Number }\n"
                "}"
            )
        with self.assertRaisesRegex(CheckError, "declares the column 'id' twice"):
            checked('store A at "./a.db" { table t { id: Number id: Text } }')

    def test_two_stores_may_not_name_one_file(self) -> None:
        with self.assertRaisesRegex(CheckError, "one store says what a file holds"):
            checked(
                'store A at "./a.db" { table t { id: Number } }\n'
                'store B at "./a.db" { table u { id: Number } }'
            )

    def test_a_store_survives_being_loaded_with_its_imports(self) -> None:
        # A store is a declaration like any other: the loader may not drop it.
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "main.kos").write_text(SOURCE, encoding="utf-8")
            program = load_program(root / "main.kos")
        self.assertEqual([store.name for store in program.stores], ["Ledger"])


class SchemaTests(unittest.TestCase):
    """A store against a file: the preflight before the first step."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.program_path = self.root / "main.kos"
        self.program_path.write_text(SOURCE, encoding="utf-8")
        self.program = load_program(self.program_path)
        self.store = self.program.stores[0]
        self.file = self.root / "notes.db"
        connection = sqlite3.connect(self.file)
        connection.execute(
            "create table notes (id integer, title text, done integer)"
        )
        connection.execute("create table log (at text)")
        connection.execute("create table spare (note text)")
        connection.commit()
        connection.close()

    def test_a_store_that_agrees_passes(self) -> None:
        self.assertIsNone(check_stores(None, None, self.root))
        self.assertEqual(
            check_stores(self.program, self.file, self.root), self.file.resolve()
        )
        self.assertEqual(store_problems(self.file, self.store), [])

    def test_a_store_with_no_database_named_is_refused(self) -> None:
        with self.assertRaisesRegex(
            DatabaseError, "declares a store \\(Ledger\\), and no database was named"
        ):
            check_stores(self.program, None, self.root)

    def test_the_file_must_be_the_one_the_runner_named(self) -> None:
        with self.assertRaisesRegex(DatabaseError, "the runner named"):
            check_stores(self.program, self.root / "other.db", self.root)

    def test_a_file_that_is_not_there_is_refused(self) -> None:
        self.file.unlink()
        with self.assertRaisesRegex(DatabaseError, "kosika db init"):
            check_stores(self.program, self.file, self.root)

    def test_a_missing_table_is_named_with_what_the_file_has(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("drop table log")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            DatabaseError,
            "declares table 'log', and the database has no table of that name; "
            "it has: notes, spare",
        ):
            check_stores(self.program, self.file, self.root)

    def test_a_missing_column_is_named_with_what_the_table_has(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("drop table notes")
        connection.execute("create table notes (id integer, title text)")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            DatabaseError,
            "table 'notes' has no column 'done', and store 'Ledger' declares "
            "it; the table has: id, title",
        ):
            check_stores(self.program, self.file, self.root)

    def test_a_column_of_another_affinity_is_refused_with_the_column_named(
        self,
    ) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("drop table notes")
        connection.execute("create table notes (id integer, title text, done text)")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            DatabaseError,
            "column notes.done is TEXT \\('TEXT'\\), and store 'Ledger' "
            "declares it Bool, which is INTEGER and holds true or false",
        ):
            check_stores(self.program, self.file, self.root)

    def test_a_number_column_may_be_any_numeric_affinity(self) -> None:
        for declared in ("integer", "numeric", "real", "double precision"):
            with self.subTest(declared=declared):
                connection = sqlite3.connect(self.file)
                connection.execute("drop table notes")
                connection.execute(
                    f"create table notes (id {declared}, title text, done integer)"
                )
                connection.commit()
                connection.close()
                self.assertEqual(store_problems(self.file, self.store), [])

    def test_a_bool_column_must_be_integer(self) -> None:
        for declared in ("text", "real", "numeric"):
            with self.subTest(declared=declared):
                connection = sqlite3.connect(self.file)
                connection.execute("drop table notes")
                connection.execute(
                    f"create table notes (id integer, title text, done {declared} "
                    "check (done in (0, 1)))"
                )
                connection.commit()
                connection.close()
                self.assertTrue(store_problems(self.file, self.store))

    def test_a_table_the_store_does_not_mention_is_left_alone(self) -> None:
        self.assertEqual(store_problems(self.file, self.store), [])

    def test_the_affinity_rule_is_sqlites_own(self) -> None:
        self.assertEqual(affinity_of("VARCHAR(20)"), "TEXT")
        self.assertEqual(affinity_of("BIGINT"), "INTEGER")
        self.assertEqual(affinity_of(""), "BLOB")
        self.assertEqual(affinity_of("DOUBLE"), "REAL")
        self.assertEqual(affinity_of("DECIMAL(10,5)"), "NUMERIC")

    def test_the_store_is_found_by_the_file_it_names(self) -> None:
        self.assertEqual(
            store_for(self.program, self.file.resolve(), self.root).name, "Ledger"
        )
        with self.assertRaisesRegex(DatabaseError, "declares no store for 'other.db'"):
            store_for(self.program, self.root / "other.db", self.root)


class InitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.program_path = self.root / "main.kos"
        self.program_path.write_text(SOURCE, encoding="utf-8")
        self.store = load_program(self.program_path).stores[0]
        self.file = self.root / "notes.db"

    def test_init_writes_the_tables_a_program_declares(self) -> None:
        plan = plan_init(self.file, self.store)
        self.assertEqual([name for name, _statement in plan.creates], ["notes", "log"])
        self.assertEqual(plan.problems, [])
        self.assertEqual(apply_init(plan), ["notes", "log"])
        connection = sqlite3.connect(self.file)
        columns = connection.execute("pragma table_info(notes)").fetchall()
        connection.close()
        self.assertEqual(
            [(row[1], row[2]) for row in columns],
            [("id", "INTEGER"), ("title", "TEXT"), ("done", "INTEGER")],
        )
        self.assertEqual(store_problems(self.file, self.store), [])

    def test_init_reads_a_table_that_is_already_there_and_keeps_it(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (id integer, title text, done integer)")
        connection.execute("insert into notes values (1, 'kept', 1)")
        connection.commit()
        connection.close()
        plan = plan_init(self.file, self.store)
        self.assertEqual(plan.keeps, ["notes"])
        self.assertEqual([name for name, _statement in plan.creates], ["log"])
        apply_init(plan)
        connection = sqlite3.connect(self.file)
        rows = connection.execute("select title from notes").fetchall()
        connection.close()
        self.assertEqual([list(row) for row in rows], [["kept"]])

    def test_init_changes_no_column_and_writes_nothing_when_one_disagrees(
        self,
    ) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (id integer, title text, done text)")
        connection.execute("insert into notes values (1, 'there', 'yes')")
        connection.commit()
        connection.close()
        plan = plan_init(self.file, self.store)
        self.assertEqual(plan.keeps, [])
        self.assertEqual(len(plan.problems), 1)
        self.assertIn("column notes.done is TEXT", plan.problems[0])
        with self.assertRaisesRegex(
            DatabaseError, "nothing was written: notes.db: column notes.done is TEXT"
        ):
            apply_init(plan)
        connection = sqlite3.connect(self.file)
        columns = connection.execute("pragma table_info(notes)").fetchall()
        connection.close()
        # SQLite keeps the column as it was written, so it is still TEXT.
        self.assertEqual([row[2].lower() for row in columns], ["integer", "text", "text"])

    def test_init_writes_a_table_the_store_does_not_declare_only_if_asked(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("create table spare (note text)")
        connection.commit()
        connection.close()
        apply_init(plan_init(self.file, self.store))
        connection = sqlite3.connect(self.file)
        names = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table' order by name"
            ).fetchall()
        ]
        connection.close()
        self.assertEqual(names, ["log", "notes", "spare"])


class DescribeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.file = self.root / "notes.db"
        self.file.write_text("", encoding="utf-8")

    def test_describe_says_each_column_and_its_affinity(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (id integer, title varchar(20))")
        connection.execute("insert into notes values (1, 'a')")
        connection.commit()
        connection.close()
        described = describe(self.file)
        self.assertEqual(described["database"], str(self.file))
        self.assertEqual(
            described["tables"],
            [
                {
                    "name": "notes",
                    "rows": 1,
                    "columns": [
                        # SQLite writes one of its own type names in capitals.
                        {"name": "id", "declared": "INTEGER", "affinity": "INTEGER"},
                        {
                            "name": "title",
                            "declared": "varchar(20)",
                            "affinity": "TEXT",
                        },
                    ],
                }
            ],
        )


class CommandTests(unittest.TestCase):
    """`kosika db init` and `kosika db inspect`, as the runner is typed them."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.program = self.root / "main.kos"
        self.program.write_text(SOURCE, encoding="utf-8")
        self.file = self.root / "notes.db"
        self.input = self.root / "input.json"
        self.input.write_text(
            json.dumps(
                {"ask": {"sql": "select title from notes", "values": []}}
            ),
            encoding="utf-8",
        )
        self.trace = self.root / "run.jsonl"

    def tell(self, argv: list[str]) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch("sys.argv", ["kosika", *argv]),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            code = cli.main()
        return code, output.getvalue(), errors.getvalue()

    def seed(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (id integer, title text, done integer)")
        connection.execute("create table log (at text)")
        connection.execute("insert into notes values (1, 'Buy milk', 1)")
        connection.commit()
        connection.close()

    def table_names(self) -> list[str]:
        connection = sqlite3.connect(self.file)
        names = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table' order by name"
            ).fetchall()
        ]
        connection.close()
        return names

    def test_init_prints_its_plan_and_writes_the_tables(self) -> None:
        code, said, errors = self.tell(
            ["db", "init", str(self.file), "--from", str(self.program)]
        )
        self.assertEqual(code, 0, errors)
        self.assertIn("Plan for", said)
        self.assertIn('write table notes: create table "notes"', said)
        self.assertIn('"id" INTEGER, "title" TEXT, "done" INTEGER', said)
        self.assertIn("Wrote 2 tables: notes, log", said)
        self.assertEqual(self.table_names(), ["log", "notes"])

    def test_init_says_nothing_is_to_write_when_every_table_is_there(self) -> None:
        self.seed()
        code, said, errors = self.tell(
            ["db", "init", str(self.file), "--from", str(self.program)]
        )
        self.assertEqual(code, 0, errors)
        self.assertIn("keep table notes: it is there and agrees", said)
        self.assertIn("Nothing to write", said)

    def test_init_refuses_a_table_that_disagrees_and_writes_nothing(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (id integer, title text, done text)")
        connection.commit()
        connection.close()
        code, said, errors = self.tell(
            ["db", "init", str(self.file), "--from", str(self.program)]
        )
        self.assertEqual(code, 2)
        self.assertIn("cannot: notes.db: column notes.done is TEXT", said)
        self.assertIn("Nothing was written", errors)
        self.assertEqual(self.table_names(), ["notes"])  # `log` was not written

    def test_init_without_a_store_in_the_program_is_refused(self) -> None:
        plain = self.root / "plain.kos"
        plain.write_text(
            SOURCE.replace(
                'store Ledger at "./notes.db" {\n'
                "  table notes {\n"
                "    id: Number\n"
                "    title: Text\n"
                "    done: Bool\n"
                "  }\n"
                "  table log {\n"
                "    at: Text\n"
                "  }\n"
                "}\n",
                "",
            ),
            encoding="utf-8",
        )
        code, _said, errors = self.tell(
            ["db", "init", str(self.file), "--from", str(plain)]
        )
        self.assertEqual(code, 2)
        self.assertIn("declares no store", errors)

    def test_inspect_says_what_is_there_and_whether_the_store_agrees(self) -> None:
        self.seed()
        code, said, errors = self.tell(
            ["db", "inspect", str(self.file), "--from", str(self.program)]
        )
        self.assertEqual(code, 0, errors)
        self.assertIn("table notes (1 rows)", said)
        self.assertIn("done", said)
        self.assertIn("store Ledger: the database agrees", said)

    def test_inspect_says_the_database_does_not_agree(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (id integer, title text)")
        connection.commit()
        connection.close()
        code, said, errors = self.tell(
            ["db", "inspect", str(self.file), "--from", str(self.program)]
        )
        self.assertEqual(code, 2)
        self.assertIn("the database does not agree", said)
        self.assertIn("has no column 'done'", said)

    def test_inspect_json_is_a_payload(self) -> None:
        self.seed()
        code, said, errors = self.tell(
            ["db", "inspect", str(self.file), "--json"]
        )
        self.assertEqual(code, 0, errors)
        payload = json.loads(said)
        self.assertEqual(payload["store"], None)
        self.assertEqual(payload["tables"][0]["name"], "log")

    def test_inspect_without_a_file_is_refused(self) -> None:
        code, _said, errors = self.tell(["db", "inspect", str(self.file)])
        self.assertEqual(code, 2)
        self.assertIn("there is no database at", errors)


class RunPreflightTests(unittest.TestCase):
    """A run against a store: the preflight, then the answer, then the trace."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.program = self.root / "main.kos"
        self.program.write_text(SOURCE, encoding="utf-8")
        self.file = self.root / "notes.db"
        self.input = self.root / "input.json"
        self.input.write_text(
            json.dumps({"ask": {"sql": "select title from notes", "values": []}}),
            encoding="utf-8",
        )
        self.trace = self.root / "run.jsonl"

    def tell(self, argv: list[str]) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch("sys.argv", ["kosika", *argv]),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            code = cli.main()
        return code, output.getvalue(), errors.getvalue()

    def run_args(self, *extra: str) -> list[str]:
        return [
            "run",
            str(self.program),
            "--database",
            str(self.file),
            "--input",
            str(self.input),
            "--trace",
            str(self.trace),
            *extra,
        ]

    def agreeing_file(self) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (id integer, title text, done integer)")
        connection.execute("create table log (at text)")
        connection.execute("insert into notes values (1, 'Buy milk', 1)")
        connection.commit()
        connection.close()

    def test_a_run_against_a_store_that_drifted_is_refused_before_any_step(
        self,
    ) -> None:
        connection = sqlite3.connect(self.file)
        connection.execute("create table notes (id integer, title text, done text)")
        connection.commit()
        connection.close()
        code, _said, errors = self.tell(self.run_args())
        self.assertEqual(code, 2)
        self.assertIn("Workflow failed: notes.db: column notes.done is TEXT", errors)
        # Nothing ran: the trace a run writes was never begun.
        self.assertFalse(self.trace.exists())

    def test_a_run_that_names_no_database_is_refused(self) -> None:
        code, _said, errors = self.tell(
            [
                "run",
                str(self.program),
                "--input",
                str(self.input),
                "--trace",
                str(self.trace),
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("and no database was named", errors)
        self.assertFalse(self.trace.exists())

    def test_a_run_against_a_store_that_agrees_gives_the_answer(self) -> None:
        self.agreeing_file()
        code, said, errors = self.tell(self.run_args())
        self.assertEqual(code, 0, errors)
        self.assertIn("Buy milk", said)

    def test_the_trace_replays_with_the_database_taken_away(self) -> None:
        self.agreeing_file()
        code, _said, errors = self.tell(self.run_args())
        self.assertEqual(code, 0, errors)
        self.file.unlink()
        code, said, errors = self.tell(["replay", str(self.trace)])
        self.assertEqual(code, 0, errors)
        self.assertIn("Buy milk", said)
