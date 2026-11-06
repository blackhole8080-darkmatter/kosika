"""A database a program is granted: one file the runner names, three operations.

A program asks for a database by declaring a tool with a `system "db"` source:

    record Statement { sql: Text values: Text[] }
    record Grid { columns: Text[] rows: Text[][] more: Bool }
    record Note { id: Number title: Text }
    record Notes { rows: Note[] more: Bool }
    record Changed { changed: Number }

    tool find_orders {
      source system "db"
      operation: "query"        // only looks, and answers with a grid of text
      access: observe           // so it needs no approval for each call
      input: Statement
      output: Grid
    }

    tool read_notes {
      source system "db"
      operation: "rows"         // only looks, and answers with the program's
      access: observe           // own records
      input: Statement
      output: Notes             // {rows: Note[], more: Bool}
    }

    tool add_order {
      source system "db"
      operation: "run"          // changes the database
      access: execute           // so the workflow must say `require approval`
      input: Statement
      output: Changed
    }

Declaring one reaches nothing. The person running the program names the file
(`--database notes.db`, see kosika.cli); without that every `db` tool stays
unreachable, like any ungranted one.

What each operation is, exactly:

* `query` and `rows` open the file read-only, with SQLite's own `mode=ro`.
  They cannot write, whatever the statement says; a statement that tries is
  refused with the reason. The file must already exist: neither creates one.
* `run` opens the same file read-write, and the workflow needs the person's
  yes for that exact statement and its bound values before it happens. The
  file is created if it is not there yet.
* Only `values` are ever bound, one parameter per `?`, so nothing a model said
  can become part of the statement. `run` commits its work, and a statement
  that fails changes nothing.

A grid is every cell as text, because a SQLite column is whatever each row put
in it: a number is written the way Kosika writes numbers, an absent value
(`NULL`) is the empty text, and a blob is `<N bytes>`. A grid therefore cannot
say which of its cells were numbers and which of its empty ones were absent.

`rows` is the same statement answered as the program's own records: each
column is mapped by name onto the field of the declared row record, and a row
that does not fit is refused, naming the row and the column — an absent value
because a field is never absent (say what the statement should put there), and
a text where a `Number` field is declared because a declaration decides what a
cell means. Only `Text`, `Number` and `Bool` fields can be a row; `more` says
the row limit cut the answer short, as it does for a grid.

Limits that hold even after an approval: one statement per call (SQLite
refuses two), at most 8 KiB of SQL, at most 100 parameters, at most 500 rows
and 100 columns given back, one cell at most 4,000 characters, and the whole
statement stopped after 5 seconds. A read of more rows than that says so in
`more` instead of quietly lying about what is there.

The trace holds the whole call: the statement, the values that were bound, and
the answer, so `kosika replay` reproduces a database-backed run with no
database present. A run also records the tables the file had when it began
(`granted_tables`), so a replay can put a recorded statement back to SQLite
(`sent_problem`) and refuse one that this tool could not have sent: a
statement binding other values than were bound, or one that changes the
database under an operation that only looks.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .ast import Program, StoreDecl, TableDecl, ToolDecl
from .builtin_methods import format_number

#: One statement at a time, and not a long one. A model that writes SQL is
#: writing text, and this is where its text stops being able to be enormous.
MAX_SQL_CHARACTERS = 8 * 1024
#: A statement takes at most this many bound values.
MAX_PARAMETERS = 100
#: How much of a result comes back. `more` says when there was more.
MAX_ROWS = 500
MAX_COLUMNS = 100
MAX_CELL_CHARACTERS = 4000
#: How long one statement may run before SQLite is told to stop it.
STATEMENT_SECONDS = 5.0
#: What a field of a row may be declared as. A cell is mapped onto the field
#: of that name, and a cell that is not one is refused rather than guessed at.
ROW_FIELD_TYPES = ("Text", "Number", "Bool")

#: What a column of a store may hold, and the affinity `kosika db init`
#: writes for it: a Bool column holds 0 or 1, a Number column holds a number
#: whole or not, and a Text column holds text. INTEGER is what SQLite calls
#: NUMERIC: a whole number stays whole rather than being read back as 1.0,
#: and a fraction is kept as one, which REAL would not do.
AFFINITY_OF = {"Text": "TEXT", "Number": "INTEGER", "Bool": "INTEGER"}

#: The three operations a db tool may declare, and the two that only look.
#: Nothing else is a db tool, so a trace naming another one is refused.
DB_OPERATIONS = ("query", "rows", "run")
LOOKING_OPERATIONS = ("query", "rows")

#: What SQLite's authorizer reports for a statement that changes something.
#: `pragma` is deliberately absent: one pragma reads and another writes, and
#: SQLite reports both the same way, so a pragma is left alone rather than
#: guessed at. So are transactions, which Python's own connection manages.
_CHANGING_ACTIONS = frozenset(
    (
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
    )
)


class DatabaseError(ValueError):
    """A database cannot be opened, or a statement or a row is refused."""


def named_file(path: str | Path) -> Path:
    """The one database file the runner named, checked before anything opens it."""

    candidate = Path(str(path))
    if not str(path).strip():
        raise DatabaseError("a database needs a file name")
    if candidate.is_symlink():
        raise DatabaseError(f"database file {str(path)!r} is a symlink")
    if candidate.parent and not candidate.parent.is_dir():
        raise DatabaseError(
            f"the folder {str(candidate.parent)!r} for database {str(path)!r} "
            "is not an existing folder"
        )
    if candidate.is_dir():
        raise DatabaseError(f"{str(path)!r} is a folder, not a database file")
    return candidate.resolve()


def affinity_of(declared_type: str) -> str:
    """SQLite's own rule for what a declared column type means.

    A column is what its type name says: `INTEGER` for a name holding INT,
    `TEXT` for CHAR, CLOB or TEXT, `BLOB` for BLOB or for no name at all,
    `REAL` for REAL, FLOA or DOUB, and `NUMERIC` for anything else.
    """

    upper = declared_type.upper()
    if "INT" in upper:
        return "INTEGER"
    if "CHAR" in upper or "CLOB" in upper or "TEXT" in upper:
        return "TEXT"
    if not upper.strip() or "BLOB" in upper:
        return "BLOB"
    if "REAL" in upper or "FLOA" in upper or "DOUB" in upper:
        return "REAL"
    return "NUMERIC"


def affinities_for(kind: str) -> tuple[str, ...]:
    """The stored affinities a column declared as `kind` may have.

    A `Bool` column is INTEGER, because a boolean is stored as 0 or 1. A
    `Number` column may be any numeric affinity: the INTEGER a store writes,
    the NUMERIC SQLite gives a type name it does not know, or the REAL someone
    else's table already has, since all three give back a number. A `Text`
    column is TEXT.
    """

    if kind == "Number":
        return ("INTEGER", "NUMERIC", "REAL")
    return (AFFINITY_OF[kind],)


def _table_columns(connection: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Every table in an open file, and the declared type of its columns."""

    names = [
        str(row[0])
        for row in connection.execute(
            "select name from sqlite_master where type = 'table' "
            "and name not like 'sqlite_%' order by name"
        ).fetchall()
    ]
    tables: dict[str, dict[str, str]] = {}
    for name in names:
        quoted = name.replace('"', '""')
        columns: dict[str, str] = {}
        for row in connection.execute(f'pragma table_info("{quoted}")').fetchall():
            columns[str(row[1])] = str(row[2])
        tables[name] = columns
    return tables


def stored_schema(path: Path) -> dict[str, dict[str, str]]:
    """What a database file holds: each table, and each column's own type."""

    connection = _opened(path, writable=False)
    try:
        try:
            return _table_columns(connection)
        except sqlite3.Error as exc:
            raise _refused(exc, path, writable=False) from exc
    finally:
        connection.close()


def store_file(store: StoreDecl, folder: Path) -> Path:
    """The file a store names, read as a path beside the program."""

    return named_file(folder / store.file)


def _column_problems(
    path: Path,
    store_name: str,
    table: TableDecl,
    columns: dict[str, str],
) -> list[str]:
    """What a table's declaration wants that the columns it has do not give."""

    problems: list[str] = []
    for column in table.columns:
        if column.name not in columns:
            problems.append(
                f"{path.name}: table {table.name!r} has no column "
                f"{column.name!r}, and store {store_name!r} declares it; the "
                f"table has: {', '.join(columns) or 'no columns'}"
            )
            continue
        actual = affinity_of(columns[column.name])
        wanted = affinities_for(column.type_name)
        if actual not in wanted:
            problems.append(
                f"{path.name}: column {table.name}.{column.name} is {actual} "
                f"({columns[column.name]!r}), and store {store_name!r} declares "
                f"it {column.type_name}, which is "
                f"{AFFINITY_OF[column.type_name]} and holds "
                f"{_holds(column.type_name)}"
            )
    return problems


def _holds(kind: str) -> str:
    """What a column declared as `kind` holds, said as a refusal can."""

    if kind == "Bool":
        return "true or false (the integers 0 and 1)"
    if kind == "Number":
        return "a number, whole or not"
    return "text"


def store_problems(path: Path, store: StoreDecl) -> list[str]:
    """What a store declares that the file does not have: one line each.

    A table or a column the file has and the store does not mention is left
    alone: a store says what a program means, not everything a file holds.
    """

    schema = stored_schema(path)
    problems: list[str] = []
    for table in store.tables:
        columns = schema.get(table.name)
        if columns is None:
            problems.append(
                f"{path.name}: store {store.name!r} declares table "
                f"{table.name!r}, and the database has no table of that name; "
                f"it has: {', '.join(sorted(schema)) or 'none'}"
            )
            continue
        problems.extend(_column_problems(path, store.name, table, columns))
    return problems


def check_store(path: Path, store: StoreDecl) -> None:
    """One store against one file, or a refusal naming the table or column."""

    if not path.is_file():
        raise DatabaseError(
            f"there is no database at {str(path)!r}, and store {store.name!r} "
            "declares tables in it; `kosika db init` writes the tables a "
            "program declares"
        )
    problems = store_problems(path, store)
    if problems:
        raise DatabaseError(problems[0])


def check_stores(
    program: Program | None, database: str | Path | None, folder: Path
) -> Path | None:
    """Check every store a program declares against the file the runner named.

    A store is a promise about a database file, so it is kept before the first
    step: the file must be the one the runner named with `--database`, and
    every table and column the store declares must be there with the affinity
    the declaration means. A refusal names the file and the column. This is
    the only place a schema is compared, so `kosika db init` and
    `kosika db inspect` say the same thing about the same file.
    """

    if program is None or not program.stores:
        return None
    if database is None:
        names = ", ".join(store.name for store in program.stores)
        raise DatabaseError(
            f"the program declares a store ({names}), and no database was "
            "named; a store is a promise about a file, so name it with "
            "--database, or remove the store"
        )
    granted = named_file(database)
    for store in program.stores:
        named = store_file(store, folder)
        if named != granted:
            raise DatabaseError(
                f"store {store.name!r} names the file {str(named)!r}, and the "
                f"runner named {str(granted)!r}; a store is checked against "
                "the database the runner names"
            )
    for store in program.stores:
        check_store(granted, store)
    return granted


def store_for(program: Program, path: Path, folder: Path) -> StoreDecl:
    """The one store a program declares for this file."""

    matching = [
        store for store in program.stores if store_file(store, folder) == path
    ]
    if matching:
        return matching[0]
    declared = ", ".join(
        f"{store.name} ({store.file})" for store in program.stores
    )
    raise DatabaseError(
        f"the program declares no store for {path.name!r}"
        + (f"; its stores name: {declared}" if declared else "; it declares no store")
    )


def granted_tables(database: str | Path | None) -> dict[str, dict[str, str]] | None:
    """The tables of the database a run was given, for its trace to record.

    A run records this so that a replay can put a recorded statement to SQLite
    with no database present. A file that cannot be read records nothing rather
    than stopping the run: the call that reaches it says what is wrong with it
    in its own words, and a program that never reaches the database should not
    mind that it is not one.
    """

    if database is None:
        return None
    try:
        where = named_file(database)
        return stored_schema(where) if where.is_file() else {}
    except (DatabaseError, OSError):
        return None


def _quoted(name: str) -> str:
    """A table or column name, written as SQLite writes one."""

    return '"' + name.replace('"', '""') + '"'


def _scratch(tables: Mapping[str, Mapping[str, str]]) -> sqlite3.Connection:
    """A database holding the tables a run found: enough for SQLite to read a
    recorded statement with no database present.

    The columns are made without a type. A statement is only read here, never
    run, so no affinity decides anything; and a column's declared type is text
    out of a file, which is not text to build a statement out of.
    """

    connection = sqlite3.connect(":memory:")
    for name, columns in tables.items():
        parts = ", ".join(_quoted(column) for column in columns)
        if parts:
            connection.execute(f"create table {_quoted(name)} ({parts})")
    return connection


def sent_problem(
    operation: Any,
    arguments: Any,
    tables: Mapping[str, Mapping[str, str]] | None,
) -> str | None:
    """Why a recorded database call is not one this tool could have sent.

    Replay has no database, so the statement a run recorded is put to SQLite
    itself: a scratch database holding the tables that run found is asked to
    read the statement with exactly the values that were sent. SQLite decides
    -- how many values the statement binds, whether it is one statement at all,
    and whether it changes anything -- so nothing here reads SQL. A statement
    naming a table or column the recorded tables do not have is left alone: a
    run may have made one, and a recorded schema is a snapshot of a file rather
    than the whole truth about it. What cannot be judged is left alone rather
    than guessed at, because a refusal that is wrong is worse than a check that
    is missing.
    """

    if not isinstance(arguments, dict):
        return "its arguments are not a record"
    sql, values = arguments.get("sql"), arguments.get("values")
    if not isinstance(sql, str) or not sql.strip():
        return "its statement is empty"
    if len(sql) > MAX_SQL_CHARACTERS:
        return f"its statement is longer than {MAX_SQL_CHARACTERS} characters"
    if not isinstance(values, list) or any(
        not isinstance(value, str) for value in values
    ):
        return "its values are not the text a db call binds"
    if len(values) > MAX_PARAMETERS:
        return f"it binds more than {MAX_PARAMETERS} values"
    if operation not in DB_OPERATIONS:
        return f"it names a db operation {operation!r}, and a db tool has three"
    if tables is None:
        return None
    connection = _scratch(tables)
    actions: list[int] = []

    def note(action: int, *_rest: Any) -> int:
        """Take down what SQLite says the statement does, and allow it."""

        actions.append(action)
        return sqlite3.SQLITE_OK

    try:
        connection.set_authorizer(note)
        # `explain` has SQLite read a statement without running it: it resolves
        # the names, asks the authorizer and binds the values, then lists a
        # program it never executes. A statement that is itself an `explain`
        # cannot be wrapped in another, so it is offered as it stands.
        wrapped = False
        for attempt, candidate in enumerate(("explain " + sql, sql)):
            if attempt:
                del actions[:]
            try:
                connection.execute(candidate, values)
            except sqlite3.ProgrammingError as exc:
                said = str(exc)
                if "one statement" in said:
                    return "it is more than one statement, and a db call runs one"
                used = re.search(r"uses (\d+)", said)
                if used is None:
                    return "it does not bind the values that were sent"
                many = "value" if len(values) == 1 else "values"
                return (
                    f"it sends {len(values)} {many} where the statement "
                    f"uses {used.group(1)}"
                )
            except sqlite3.OperationalError as exc:
                if "syntax error" not in str(exc):
                    # A table or column this snapshot does not have, or a
                    # function this SQLite does not: nothing here can judge it.
                    return None
                if attempt:
                    return f"it is not a statement SQLite can read ({exc})"
                continue
            wrapped = attempt == 0
            break
    finally:
        connection.close()
    # Whether it changes anything is only judged when SQLite read the statement
    # itself: a statement that is itself an `explain` was read by the other
    # attempt, and an `explain` is never run, so even a tool that only looks may
    # send `explain delete from notes`. Said as a change and not named further,
    # because SQLite reports a dropped table and a removed row the same way, and
    # a refusal should not guess which one it was.
    if (
        wrapped
        and operation in LOOKING_OPERATIONS
        and any(action in _CHANGING_ACTIONS for action in actions)
    ):
        return f"it changes the database, and a {operation} tool only looks"
    return None


@dataclass
class StorePlan:
    """What `kosika db init` would write, before it writes it."""

    path: Path
    #: (table name, statement) for each table the file does not have.
    creates: list[tuple[str, str]] = field(default_factory=list)
    #: Tables already there and agreeing with the declaration.
    keeps: list[str] = field(default_factory=list)
    #: What the declaration cannot be given: nothing is written when nonempty.
    problems: list[str] = field(default_factory=list)


def create_statement(table: TableDecl) -> str:
    """The statement that makes one declared table, with plain columns."""

    columns = ", ".join(
        f'"{column.name}" {AFFINITY_OF[column.type_name]}'
        for column in table.columns
    )
    return f'create table "{table.name}" ({columns})'


def plan_init(path: Path, store: StoreDecl) -> StorePlan:
    """What writing this store's tables into this file would take.

    A table the file does not have is written. A table it already has is only
    read: a column that is missing, or holds a different affinity, is a
    problem rather than something silently changed, because changing a column
    of a table that holds rows is a decision for the person who keeps it.
    """

    plan = StorePlan(path)
    schema = stored_schema(path) if path.is_file() else {}
    for table in store.tables:
        columns = schema.get(table.name)
        if columns is None:
            plan.creates.append((table.name, create_statement(table)))
            continue
        problems = _column_problems(path, store.name, table, columns)
        plan.problems.extend(problems)
        if not problems:
            plan.keeps.append(table.name)
    return plan


def apply_init(plan: StorePlan) -> list[str]:
    """Write the tables the file does not have, and nothing else."""

    if plan.problems:
        raise DatabaseError("nothing was written: " + "; ".join(plan.problems))
    if not plan.creates:
        return []
    connection = _opened(plan.path, writable=True)
    try:
        try:
            for _name, statement in plan.creates:
                connection.execute(statement)
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise _refused(exc, plan.path, writable=True) from exc
    finally:
        connection.close()
    return [name for name, _statement in plan.creates]


def describe(path: Path) -> dict[str, Any]:
    """What is in a database file: every table, its columns, and its rows."""

    connection = _opened(path, writable=False)
    try:
        try:
            schema = _table_columns(connection)
            tables = []
            for name, columns in schema.items():
                quoted = name.replace('"', '""')
                try:
                    counted = connection.execute(
                        f'select count(*) from "{quoted}"'
                    ).fetchone()
                    rows = int(counted[0]) if counted is not None else None
                except sqlite3.Error:
                    rows = None
                tables.append(
                    {
                        "name": name,
                        "rows": rows,
                        "columns": [
                            {
                                "name": column,
                                "declared": declared,
                                "affinity": affinity_of(declared),
                            }
                            for column, declared in columns.items()
                        ],
                    }
                )
        except sqlite3.Error as exc:
            raise _refused(exc, path, writable=False) from exc
    finally:
        connection.close()
    return {"database": str(path), "tables": tables}


def row_shape(program: Program | None, tool: ToolDecl) -> tuple[str, dict[str, str]]:
    """The row record a `rows` tool declares: its name, and its fields.

    The declaration is checked before a program runs (see
    `kosika.system_tools.declaration_problem`), and read again here, when a
    call is made, so that a binder used without a checker cannot guess what a
    row is.
    """

    if program is None:
        raise DatabaseError(
            f"tool {tool.name!r} answers with rows, and the row record it names "
            "is read from the program, which was not given"
        )
    records = {
        record.name: {field.name: field.type_name for field in record.fields}
        for record in program.records
    }
    outer = records.get(tool.output or "") or {}
    field = outer.get("rows", "")
    if not field.endswith("[]"):
        raise DatabaseError(
            f"tool {tool.name!r} answers with rows, so its output record needs "
            f"a field `rows: R[]`, and {tool.output!r} has none"
        )
    name = field[: -len("[]")]
    fields = records.get(name)
    if not fields or not all(kind in ROW_FIELD_TYPES for kind in fields.values()):
        raise DatabaseError(
            f"tool {tool.name!r} answers with rows as {name!r}, and every field "
            f"of a row must be one of: {', '.join(ROW_FIELD_TYPES)}"
        )
    return name, fields


def _statement(arguments: dict[str, Any]) -> str:
    sql = arguments["sql"]
    if not isinstance(sql, str) or not sql.strip():
        raise DatabaseError("a statement to run cannot be empty")
    if len(sql) > MAX_SQL_CHARACTERS:
        raise DatabaseError(
            f"the statement is longer than {MAX_SQL_CHARACTERS} characters"
        )
    return sql


def _values(arguments: dict[str, Any]) -> tuple[str, ...]:
    given = arguments["values"]
    if len(given) > MAX_PARAMETERS:
        raise DatabaseError(f"a statement takes at most {MAX_PARAMETERS} values")
    return tuple(str(item) for item in given)


def _cell(value: Any) -> str:
    """One value of a row, as the text a grid holds."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format_number(value)
    text = str(value)
    if len(text) > MAX_CELL_CHARACTERS:
        return text[:MAX_CELL_CHARACTERS] + "[cell cut]"
    return text


def _read_only_uri(path: Path) -> str:
    return f"{path.as_uri()}?mode=ro"


def _opened(path: Path, writable: bool) -> sqlite3.Connection:
    try:
        connection = (
            sqlite3.connect(str(path))
            if writable
            else sqlite3.connect(_read_only_uri(path), uri=True)
        )
    except sqlite3.Error as exc:
        raise DatabaseError(f"database {str(path)!r} cannot be opened: {exc}") from exc
    deadline = time.monotonic() + STATEMENT_SECONDS
    connection.set_progress_handler(
        lambda: 1 if time.monotonic() > deadline else 0, 2000
    )
    return connection


def _refused(exc: sqlite3.Error, path: Path, writable: bool) -> DatabaseError:
    """A database's own words, said as the reason a call was refused."""

    said = str(exc)
    if "readonly database" in said and not writable:
        return DatabaseError(
            "a query may only look; this statement would change the database, "
            "so it needs a tool whose operation is `run` and the person's approval"
        )
    if "interrupted" in said:
        return DatabaseError(
            f"the statement ran longer than {STATEMENT_SECONDS:.0f} seconds "
            "and was stopped"
        )
    if "one statement at a time" in said:
        return DatabaseError("a database call runs one statement at a time")
    return DatabaseError(f"{said} (in database {path.name!r})")


def _existing(path: Path, what: str) -> None:
    if not path.is_file():
        raise DatabaseError(
            f"there is no database at {str(path)!r}; {what} never creates one"
        )


def _answer(
    path: Path, sql: str, values: tuple[str, ...]
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """One read: the answer's column names, and at most `MAX_ROWS + 1` rows."""

    connection = _opened(path, writable=False)
    try:
        try:
            cursor = connection.execute(sql, values)
            if cursor.description is None:
                raise DatabaseError(
                    "a read must be a statement that comes back with rows; "
                    "a tool whose operation is `run` is what changes a database"
                )
            columns = [str(column[0]) for column in cursor.description]
            if len(columns) > MAX_COLUMNS:
                raise DatabaseError(
                    f"the answer came back with {len(columns)} columns, "
                    f"at most {MAX_COLUMNS} are given back"
                )
            taken = cursor.fetchmany(MAX_ROWS + 1)
        except sqlite3.Error as exc:
            raise _refused(exc, path, writable=False) from exc
    finally:
        connection.close()
    return columns, taken


def _query(path: Path, sql: str, values: tuple[str, ...]) -> dict[str, Any]:
    _existing(path, "a query")
    columns, taken = _answer(path, sql, values)
    return {
        "columns": columns,
        "rows": [[_cell(value) for value in row] for row in taken[:MAX_ROWS]],
        "more": len(taken) > MAX_ROWS,
    }


def _held(value: Any) -> str:
    """A cell, said as something a refusal can name."""

    if value is None:
        return "nothing"
    if isinstance(value, str):
        return f"the text {value!r}"
    if isinstance(value, bool):
        return f"{'true' if value else 'false'}"
    if isinstance(value, bytes):
        return f"{len(value)} bytes"
    return f"the number {value!r}"


def _hint(kind: str, column: str) -> str:
    """What to do about a cell that is not the field it was declared as."""

    if kind == "Bool":
        return ", which is true or false (the integers 0 and 1)"
    if kind == "Number":
        return (
            f"; say so in the statement (cast({column} as real)), or declare "
            "a Text field"
        )
    return (
        f"; say so in the statement (cast({column} as text)), or declare "
        "a Number or Bool field"
    )


def _as_field(field: str, kind: str, number: int, column: str, value: Any) -> Any:
    """One cell as the field it was declared to be, or a refusal saying why."""

    if value is None:
        raise DatabaseError(
            f"row {number} of the answer has nothing in column {column!r}, and "
            f"field {field!r} is {kind}; a field is never absent, so say in the "
            "statement what to put there (coalesce), or read the answer as a grid"
        )
    if kind == "Text" and isinstance(value, str):
        return value
    if (
        kind == "Number"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return value
    if (
        kind == "Bool"
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value in (0, 1)
    ):
        return bool(value)
    raise DatabaseError(
        f"row {number} of the answer holds {_held(value)} in column {column!r}, "
        f"and field {field!r} is {kind}{_hint(kind, column)}"
    )


def _rows(
    path: Path,
    sql: str,
    values: tuple[str, ...],
    shape: tuple[str, dict[str, str]],
) -> dict[str, Any]:
    name, fields = shape
    _existing(path, "a rows call")
    columns, taken = _answer(path, sql, values)
    seen: set[str] = set()
    for column in columns:
        if column in seen:
            raise DatabaseError(
                f"the answer has more than one column named {column!r}; "
                "give each column a name of its own"
            )
        seen.add(column)
        if column not in fields:
            raise DatabaseError(
                f"the answer has a column {column!r}, and row {name!r} has no "
                f"field of that name; its fields are: {', '.join(fields)}"
            )
    for wanted in fields:
        if wanted not in seen:
            raise DatabaseError(
                f"row {name!r} has a field {wanted!r}, and the answer has no "
                f"column of that name; its columns are: {', '.join(columns)}"
            )
    built = [
        {
            column: _as_field(column, fields[column], number, column, value)
            for column, value in zip(columns, row)
        }
        for number, row in enumerate(taken[:MAX_ROWS], start=1)
    ]
    return {"rows": built, "more": len(taken) > MAX_ROWS}


def _run(path: Path, sql: str, values: tuple[str, ...]) -> dict[str, Any]:
    connection = _opened(path, writable=True)
    try:
        try:
            cursor = connection.execute(sql, values)
            changed = cursor.rowcount if cursor.rowcount > 0 else 0
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise _refused(exc, path, writable=True) from exc
    finally:
        connection.close()
    return {"changed": changed}


def bind_database_tool(
    path: str | Path, program: Program | None = None
) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    """The program's `db` tools, bound to the one file the runner named.

    A read reaches the file through a read-only connection and a `run` through
    a read-write one, so the declaration cannot ask for more than the
    operation it chose. The runtime still refuses a `run` call with no approval
    for that exact statement and its values, as for any `execute` tool.

    `program` is what a `rows` tool reads its row record from; a program with
    no such tool does not need it.
    """

    where = named_file(path)

    def call(tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        sql, values = _statement(arguments), _values(arguments)
        if tool.operation == "query":
            return _query(where, sql, values)
        if tool.operation == "rows":
            return _rows(where, sql, values, row_shape(program, tool))
        return _run(where, sql, values)

    return call
