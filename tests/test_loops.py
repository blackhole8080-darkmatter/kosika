"""Tests for Kosika's two loop forms.

``for`` and ``repeat`` were both parsed, both documented in ``kosika.ast``, and
both dead at run time: ``for`` was rejected by the checker outright and
``repeat`` was accepted by the checker and then refused by the runtime. These
tests pin the behaviour the AST actually specifies.
"""

from __future__ import annotations

import unittest

from kosika.checker import CheckError, check
from kosika.parser import ParseError, parse
from kosika.runtime import run


def _result(source: str) -> object:
    return run(parse(source), "main", None)


class ForLoopTests(unittest.TestCase):
    def test_for_maps_over_an_array(self) -> None:
        self.assertEqual(
            _result(
                """
                record R { out: Text[] }
                fn go(items: Text[]) -> R { return {out: for i in items { i + "!" }} }
                workflow main() -> R { step s { let r = go(items: ["a","b"]) return r } }
                """
            ),
            {"out": ["a!", "b!"]},
        )

    def test_for_maps_over_record_fields(self) -> None:
        self.assertEqual(
            _result(
                """
                record Agent { name: Text district: Text }
                record R { out: Text[] }
                fn go(agents: Agent[]) -> R {
                  return {out: for a in agents { a.name + "@" + a.district }}
                }
                workflow main() -> R {
                  step s {
                    let r = go(agents: [{name:"a",district:"X"},{name:"b",district:"Y"}])
                    return r
                  }
                }
                """
            ),
            {"out": ["a@X", "b@Y"]},
        )

    def test_for_can_call_a_function_per_element(self) -> None:
        self.assertEqual(
            _result(
                """
                record Agent { name: Text }
                record Badge { label: Text size: Number }
                fn badge(a: Agent) -> Badge { return {label: a.name, size: 10} }
                record R { out: Badge[] }
                fn go(agents: Agent[]) -> R { return {out: for a in agents { badge(a: a) }} }
                workflow main() -> R {
                  step s { let r = go(agents: [{name:"a"},{name:"b"}]) return r }
                }
                """
            ),
            {"out": [{"label": "a", "size": 10}, {"label": "b", "size": 10}]},
        )

    def test_for_over_a_non_array_is_rejected(self) -> None:
        with self.assertRaisesRegex(CheckError, "requires an array"):
            check(
                parse(
                    """
                    record R { out: Text[] }
                    fn go(n: Number) -> R { return {out: for i in n { i }} }
                    workflow main() -> R { step s { let r = go(n: 3) return r } }
                    """
                )
            )

    def test_for_over_a_function_array_is_rejected(self) -> None:
        # `fn(...)[]` is not a list of values, so the item type cannot be named.
        with self.assertRaisesRegex(CheckError, "requires an array"):
            check(
                parse(
                    """
                    record R { out: Text[] }
                    fn go(fns: fn(v: Number) -> Number[]) -> R {
                      return {out: for i in fns { i }}
                    }
                    workflow main() -> R { step s { let r = go(fns: []) return r } }
                    """
                )
            )

    def test_for_honours_its_limit(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "limit"):
            _result(
                """
                record R { out: Number[] }
                fn go(items: Number[]) -> R { return {out: for i in items limit 2 { i }} }
                workflow main() -> R { step s { let r = go(items: [1,2,3,4]) return r } }
                """
            )

    def test_for_within_its_limit_maps_everything(self) -> None:
        self.assertEqual(
            _result(
                """
                record R { out: Number[] }
                fn go(items: Number[]) -> R {
                  return {out: for i in items limit 8 { i * 2 }}
                }
                workflow main() -> R { step s { let r = go(items: [1,2,3,4]) return r } }
                """
            ),
            {"out": [2, 4, 6, 8]},
        )

    def test_the_item_binding_does_not_leak_out_of_the_loop(self) -> None:
        # The body sees the element in a child scope, so the item name is not
        # available after the loop.
        with self.assertRaisesRegex(CheckError, "unknown variable 'a'"):
            check(
                parse(
                    """
                    record Agent { name: Text }
                    record R { out: Number[] }
                    fn go(agents: Agent[]) -> R {
                      let out: Number[] = for a in agents { 1 }
                      return {out: out + a}
                    }
                    workflow main() -> R {
                      step s { let r = go(agents: [{name:"a"}]) return r }
                    }
                    """
                )
            )


class RepeatLoopTests(unittest.TestCase):
    def test_repeat_yields_the_last_result(self) -> None:
        self.assertEqual(
            _result(
                """
                record R { v: Number }
                fn go() -> R { let v: Number = repeat 4 times { 7 } return {v: v} }
                workflow main() -> R { step s { let r = go() return r } }
                """
            ),
            {"v": 7},
        )

    def test_repeat_can_index_through_an_array(self) -> None:
        # Without `for`, a counted loop plus indexing is the manual escape
        # hatch; this is what a hand-unrolled loop looks like.
        self.assertEqual(
            _result(
                """
                record R { last: Text }
                fn go(items: Text[]) -> R {
                  let last: Text = repeat 3 times { items[2] }
                  return {last: last}
                }
                workflow main() -> R {
                  step s { let r = go(items: ["a","b","c"]) return r }
                }
                """
            ),
            {"last": "c"},
        )

    def test_repeat_rejects_a_non_numeric_count(self) -> None:
        with self.assertRaises((CheckError, ParseError)) as caught:
            check(
                parse(
                    """
                    record R { v: Text }
                    fn go(t: Text) -> R { let v: Text = repeat t times { "x" } return {v: v} }
                    workflow main() -> R { step s { let r = go(t: "a") return r } }
                    """
                )
            )
        self.assertIn("repeat", str(caught.exception))

    def test_repeat_count_must_be_at_least_one(self) -> None:
        # A zero-count repeat has no result, and a Number field rejects Null,
        # so the parser refuses the count outright rather than producing one.
        with self.assertRaisesRegex(ParseError, "repeat count must be an integer"):
            parse(
                """
                record R { v: Number }
                fn go() -> R { let v: Number = repeat 0 times { 1 } return {v: v} }
                workflow main() -> R { step s { let r = go() return r } }
                """
            )


class BreakAndContinueTests(unittest.TestCase):
    def test_break_and_continue_need_a_null_aware_context(self) -> None:
        # `break` and `continue` type as Null, so they cannot be one arm of a
        # conditional whose other arm yields the loop element. This is a known
        # gap rather than a silent wrong answer.
        with self.assertRaisesRegex(CheckError, "branches differ"):
            check(
                parse(
                    """
                    record R { out: Text[] }
                    fn go(items: Text[]) -> R {
                      return {out: for i in items { if i == "b" { break } else { i } }}
                    }
                    workflow main() -> R {
                      step s { let r = go(items: ["a","b"]) return r }
                    }
                    """
                )
            )

    def test_labelled_break_is_rejected(self) -> None:
        # BreakExpr carries no label, so accepting one would silently break
        # the innermost loop instead of the labelled one.
        with self.assertRaisesRegex(ParseError, "Labelled break is not supported"):
            parse(
                """
                record R { v: Number }
                fn go() -> R {
                  let v: Number = repeat 3 times { break 2: 1 }
                  return {v: v}
                }
                workflow main() -> R { step s { let r = go() return r } }
                """
            )


if __name__ == "__main__":
    unittest.main()
