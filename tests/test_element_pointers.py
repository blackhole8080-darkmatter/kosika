"""Pointers: the comment above a declaration that says what it is.

Both comment styles are read, so a Python or JavaScript file is held to the
same rule as a Kosika one.
"""

from __future__ import annotations

from kosika import elements

KOSIKA = """// element: greet.hello — greets the person by the name they gave
fn greet(who: Text) -> Text {
  return "hello " + who
}
"""

PYTHON = """# element: calc.add — adds two numbers
def add(a, b):
    return a + b
"""

JAVASCRIPT = """// element: cart.total — adds a row's line total
function lineTotal(row) {
  return row.price;
}
"""


def test_a_kosika_pointer_belongs_to_the_declaration_below_it() -> None:
    found = elements.read(KOSIKA, "greeting.kos")
    assert len(found) == 1
    assert found[0].element == "greet.hello"
    assert found[0].what == "greets the person by the name they gave"
    assert found[0].declaration == "fn greet"
    assert found[0].line == 1


def test_a_python_pointer_is_read_too() -> None:
    found = elements.read(PYTHON, "calc.py")
    assert found[0].element == "calc.add"
    assert found[0].what == "adds two numbers"
    assert found[0].declaration == "def add"
    assert found[0].name == "add"


def test_a_javascript_pointer_is_read_too() -> None:
    found = elements.read(JAVASCRIPT, "cart.js")
    assert found[0].element == "cart.total"
    assert found[0].declaration == "function lineTotal"


def test_a_const_binding_counts_as_a_declaration() -> None:
    source = "// element: cart.add — adds one row\nconst total = (row) => row.price;\n"
    found = elements.read(source, "cart.js")
    assert found[0].declaration == "const total"
    assert found[0].name == "total"


def test_a_python_class_is_a_declaration() -> None:
    source = "# element: cart.rows — the rows of one order\nclass Rows:\n    pass\n"
    assert elements.read(source, "cart.py")[0].declaration == "class Rows"


def test_a_blank_line_or_a_note_may_sit_between() -> None:
    source = "\n".join(
        [
            "# element: calc.add — adds two numbers",
            "# the header above is long, so this note is kept under it",
            "",
            "def add(a, b):",
            "    return a + b",
            "",
        ]
    )
    assert elements.read(source, "calc.py")[0].declaration == "def add"


def test_a_pointer_with_nothing_under_it_is_an_orphan() -> None:
    source = "def add(a, b):\n    return a + b\n\n# element: calc.late — too late\n"
    found = elements.read(source, "calc.py")
    assert found[-1].declaration == ""


def test_code_between_the_pointer_and_the_next_declaration_orphans_it() -> None:
    source = "# element: calc.add — adds two numbers\nx = 1\ndef add(a, b):\n    return a + b\n"
    assert elements.read(source, "calc.py")[0].declaration == ""


def test_the_dash_may_be_any_of_the_three() -> None:
    for dash in ("-", "–", "—"):
        source = f"# element: one.two {dash} says it\ndef one():\n    pass\n"
        pointer = elements.read(source, "one.py")[0]
        assert pointer.element == "one.two"
        assert pointer.what == "says it"
