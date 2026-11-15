"""The standard library's path search, checked against every way there is.

The conformance case pins a few networks by hand; this one builds random
ones, works out the cheapest way by trying every simple route, and makes
the search answer the same. Every answer it gives must also be a real
walk: each step a link, and the links adding up to the cost it reports.
"""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from kosika.modules import load_program
from kosika.testing import TestResult as Case
from kosika.testing import run_tests

HELPER = """
import "std/paths.kos"

// A way is real when each step is a link and the links add up to the cost.
fn way_is_real(route: Route, links: Link[]) -> Bool {
  var i = 0
  var total = 0
  while i + 1 < route.places.length() {
    var cheapest = -1
    for link in links {
      if link.from == route.places[i] and link.to == route.places[i + 1] {
        if cheapest < 0 or link.cost < cheapest { cheapest = link.cost }
      }
    }
    if cheapest < 0 { return false }
    total = total + cheapest
    i = i + 1
  }
  return total == route.cost
}

// Every step of a way is a link of the network.
fn steps_are_links(route: Route, links: Link[]) -> Bool {
  var i = 0
  while i + 1 < route.places.length() {
    var found = false
    for link in links {
      if link.from == route.places[i] and link.to == route.places[i + 1] { found = true }
    }
    if not found { return false }
    i = i + 1
  }
  return true
}
"""


def links_source(links: list[tuple[str, str, int]]) -> str:
    rows = ",\n    ".join(
        f'Link(from: "{a}", to: "{b}", cost: {cost})' for a, b, cost in links
    )
    return f"[\n    {rows}\n  ]" if links else "[]"


def cheapest(links: list[tuple[str, str, int]], start: str, goal: str) -> int | None:
    """The cheapest simple route, by trying every one of them."""

    best: int | None = None
    stack: list[tuple[str, int, tuple[str, ...]]] = [(start, 0, (start,))]
    while stack:
        here, cost, seen = stack.pop()
        if here == goal:
            best = cost if best is None else min(best, cost)
            continue
        if best is not None and cost >= best:
            continue
        for a, b, weight in links:
            if a == here and b not in seen:
                stack.append((b, cost + weight, seen + (b,)))
    return best


def fewest_links(
    links: list[tuple[str, str, int]], start: str, goal: str
) -> int | None:
    """The fewest steps from start to goal, by breadth first."""

    seen = {start: 0}
    queue = [start]
    while queue:
        here = queue.pop(0)
        if here == goal:
            return seen[here]
        for a, b, _ in links:
            if a == here and b not in seen:
                seen[b] = seen[here] + 1
                queue.append(b)
    return None


def random_network(rng: random.Random, size: int) -> list[tuple[str, str, int]]:
    nodes = [chr(ord("a") + i) for i in range(size)]
    links: list[tuple[str, str, int]] = []
    for a in nodes:
        for b in nodes:
            if a != b and rng.random() < 0.45:
                links.append((a, b, rng.randint(0, 9)))
    return links


class PathTests(unittest.TestCase):
    def results(self, source: str) -> list[Case]:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "paths_case.kos"
            path.write_text(source, encoding="utf-8")
            return run_tests(load_program(path))

    def assert_passed(self, results: list[Case]) -> None:
        failed = [f"{item.name}: {item.message}" for item in results if not item.passed]
        self.assertEqual(failed, [])
        self.assertGreater(len(results), 0)

    def test_it_finds_the_cheapest_way_of_a_random_network(self) -> None:
        rng = random.Random(11)
        asked = 0
        for _ in range(40):
            links = random_network(rng, 5)
            cost = cheapest(links, "a", "e")
            if cost is None:
                continue
            asked += 1
            source = HELPER + (
                'test "the cheapest way through a random network" {\n'
                f"  links = {links_source(links)}\n"
                '  route = shortest("a", "e", links, {})\n'
                "  expect route.found == true\n"
                f"  expect route.cost == {cost}\n"
                "  expect way_is_real(route, links) == true\n"
                "}\n"
            )
            self.assert_passed(self.results(source))
            if asked == 12:
                break
        self.assertEqual(asked, 12, "not enough reachable random networks were drawn")

    def test_it_takes_the_fewest_links_when_asked_for_them(self) -> None:
        rng = random.Random(7)
        asked = 0
        for _ in range(40):
            links = random_network(rng, 5)
            steps = fewest_links(links, "a", "e")
            if steps is None:
                continue
            asked += 1
            source = HELPER + (
                'test "the way with the fewest links" {\n'
                f"  links = {links_source(links)}\n"
                '  route = hops("a", "e", links)\n'
                "  expect route.found == true\n"
                f"  expect route.places.length() == {steps + 1}\n"
                f"  expect route.cost == {steps}\n"
                "  expect steps_are_links(route, links) == true\n"
                "}\n"
            )
            self.assert_passed(self.results(source))
            if asked == 12:
                break
        self.assertEqual(asked, 12, "not enough reachable random networks were drawn")

    def test_a_place_no_way_reaches_is_reported_unfound(self) -> None:
        source = HELPER + (
            'test "nowhere to go" {\n'
            '  links = [Link(from: "a", to: "b", cost: 3)]\n'
            '  nowhere = shortest("b", "a", links, {})\n'
            "  expect nowhere.found == false\n"
            "  expect nowhere.places == []\n"
            "  expect nowhere.cost == 0\n"
            "}\n"
        )
        self.assert_passed(self.results(source))

    def test_a_link_that_pays_back_is_refused(self) -> None:
        source = (
            'import "std/paths.kos"\n'
            'test "a link that pays back" {\n'
            '  links = [Link(from: "a", to: "b", cost: -1)]\n'
            '  route = shortest("a", "b", links, {})\n'
            "  expect route.found == true\n"
            "}\n"
        )
        results = self.results(source)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].passed)
        self.assertIn("links that cost 0 or more", results[0].message or "")


if __name__ == "__main__":
    unittest.main()
