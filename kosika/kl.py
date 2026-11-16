"""Kosika Logic (KL) — Query and inference language for Kosika Maths (SPEC §6).

KTF syntax (S-expression style):

    (query
      (find ?agent ?skill ?prof)
      (where
        (has-skill ?agent ?skill)
        (proficiency ?agent ?skill ?prof)
        (>= ?prof 0.8)
        (context ?ctx (source ?agent) (certainty > 0.9)))
      (order-by (desc ?prof))
      (limit 10))

Core operators: match, filter, project, aggregate, join, union, diff, recurse, infer, counterfactual.
Inference modes: materialized, on-demand, streaming, probabilistic, causal.

This module provides:
- KLParser: parses KTF strings into Query AST
- KLInterpreter: executes queries against knowledge graphs
- RuleEngine: forward-chaining rule inference
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .knowledge_graph import Context, Fact, Hyperedge, KnowledgeGraph

# ──────────────────────────────────────────────────────────────
# Query AST
# ──────────────────────────────────────────────────────────────


@dataclass
class Query:
    """A Kosika Logic query."""

    id: str
    pattern: "QueryPattern"
    projection: List[str] = field(default_factory=list)
    ordering: List["OrderBy"] = field(default_factory=list)
    limit: Optional[int] = None
    offset: Optional[int] = None
    aggregates: List["Aggregate"] = field(default_factory=list)


@dataclass
class OrderBy:
    variable: str
    ascending: bool = True


@dataclass
class Aggregate:
    function: str  # count, sum, avg, min, max, collect
    variable: str
    alias: str


# Query patterns (recursive)
@dataclass
class MatchPattern:
    fact_pattern: "FactPattern"


@dataclass
class AndPattern:
    patterns: List["QueryPattern"]


@dataclass
class OrPattern:
    patterns: List["QueryPattern"]


@dataclass
class NotPattern:
    pattern: "QueryPattern"


@dataclass
class FilterPattern:
    pattern: "QueryPattern"
    expr: "FilterExpr"


@dataclass
class ExistsPattern:
    pattern: "QueryPattern"


@dataclass
class ForAllPattern:
    pattern: "QueryPattern"


QueryPattern = (
    MatchPattern
    | AndPattern
    | OrPattern
    | NotPattern
    | FilterPattern
    | ExistsPattern
    | ForAllPattern
)


@dataclass
class FactPattern:
    head: "PatternTerm"
    relation: "PatternTerm"
    tail: "PatternTerm"
    context: Optional["ContextPattern"] = None


@dataclass
class PatternTerm:
    type: str  # "variable", "constant", "wildcard"
    value: str


@dataclass
class ContextPattern:
    source: PatternTerm
    min_certainty: Optional[float]
    scope: PatternTerm


@dataclass
class FilterExpr:
    type: str  # "eq", "neq", "lt", "gt", "in", "regex"
    left: PatternTerm
    right: PatternTerm | List[PatternTerm] | str


# ──────────────────────────────────────────────────────────────
# Parsing (simple S-expression tokenizer/parser)
# ──────────────────────────────────────────────────────────────


class KLParseError(Exception):
    pass


def _tokenize(text: str) -> List[str]:
    """Tokenize KTF text into parens and atoms."""
    tokens = []
    i = 0
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == "(" or c == ")":
            tokens.append(c)
            i += 1
            continue
        # Read atom
        j = i
        while j < len(text) and not text[j].isspace() and text[j] not in "()":
            j += 1
        tokens.append(text[i:j])
        i = j
    return tokens


class _Parser:
    def __init__(self, tokens: List[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Optional[str]:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def consume(self) -> str:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse_sexpr(self) -> List[Any]:
        """Parse a parenthesized S-expression."""
        if self.peek() != "(":
            raise KLParseError(f"Expected '(' at position {self.pos}")
        self.consume()  # '('
        items: List[Any] = []
        while self.peek() != ")" and self.peek() is not None:
            if self.peek() == "(":
                items.append(self.parse_sexpr())
            else:
                items.append(self.consume())
        if self.peek() != ")":
            raise KLParseError("Expected ')'")
        self.consume()  # ')'
        return items

    def parse_all(self) -> List[List[Any]]:
        results = []
        while self.peek() is not None:
            if self.peek() == "(":
                results.append(self.parse_sexpr())
            else:
                self.consume()  # skip stray atoms
        return results


def parse_ktf(text: str) -> List[List[Any]]:
    """Parse KTF text into nested lists."""
    tokens = _tokenize(text)
    parser = _Parser(tokens)
    return parser.parse_all()


# ──────────────────────────────────────────────────────────────
# AST Builder from parsed S-expressions
# ──────────────────────────────────────────────────────────────


def _term(atom: str) -> PatternTerm:
    if atom == "_":
        return PatternTerm(type="wildcard", value="_")
    if atom.startswith("?"):
        return PatternTerm(type="variable", value=atom)
    return PatternTerm(type="constant", value=atom)


def _build_pattern(sexpr: List[Any]) -> QueryPattern:
    if not sexpr:
        raise KLParseError("Empty pattern")

    op = sexpr[0]
    if not isinstance(op, str):
        raise KLParseError(f"Expected operator string, got {op!r}")

    if op == "match":
        # (match head relation tail context?)
        if len(sexpr) < 4:
            raise KLParseError("match needs head, relation, tail")
        head = _term(str(sexpr[1]))
        rel = _term(str(sexpr[2]))
        tail = _term(str(sexpr[3]))
        ctx = None
        if len(sexpr) >= 5:
            ctx_sexpr = sexpr[4]
            if isinstance(ctx_sexpr, list) and ctx_sexpr and ctx_sexpr[0] == "ctx-pat":
                src = (
                    _term(str(ctx_sexpr[1]))
                    if len(ctx_sexpr) > 1
                    else PatternTerm(type="wildcard", value="_")
                )
                min_cert = None
                scope = PatternTerm(type="wildcard", value="_")
                for item in ctx_sexpr[2:]:
                    if (
                        isinstance(item, list)
                        and item
                        and item[0] == ":min-cert"
                        and len(item) > 1
                    ):
                        min_cert = float(item[1])
                    elif (
                        isinstance(item, list)
                        and item
                        and item[0] == ":scope"
                        and len(item) > 1
                    ):
                        scope = _term(str(item[1]))
                ctx = ContextPattern(source=src, min_certainty=min_cert, scope=scope)
        return MatchPattern(
            fact_pattern=FactPattern(head=head, relation=rel, tail=tail, context=ctx)
        )

    elif op == "and":
        return AndPattern(
            patterns=[_build_pattern(p) for p in sexpr[1:] if isinstance(p, list)]
        )

    elif op == "or":
        return OrPattern(
            patterns=[_build_pattern(p) for p in sexpr[1:] if isinstance(p, list)]
        )

    elif op == "not":
        if len(sexpr) < 2:
            raise KLParseError("not needs a pattern")
        return NotPattern(pattern=_build_pattern(sexpr[1]))

    elif op == "filter":
        if len(sexpr) < 3:
            raise KLParseError("filter needs pattern and expression")
        inner = _build_pattern(sexpr[1])
        expr = _build_filter_expr(sexpr[2])
        return FilterPattern(pattern=inner, expr=expr)

    elif op == "exists":
        return ExistsPattern(pattern=_build_pattern(sexpr[1]))

    elif op == "forall":
        return ForAllPattern(pattern=_build_pattern(sexpr[1]))

    else:
        raise KLParseError(f"Unknown pattern operator: {op}")


def _build_filter_expr(sexpr: List[Any]) -> FilterExpr:
    op = sexpr[0]
    if op == "=":
        return FilterExpr(
            type="eq", left=_term(str(sexpr[1])), right=_term(str(sexpr[2]))
        )
    elif op == "!=":
        return FilterExpr(
            type="neq", left=_term(str(sexpr[1])), right=_term(str(sexpr[2]))
        )
    elif op == "<":
        return FilterExpr(
            type="lt", left=_term(str(sexpr[1])), right=_term(str(sexpr[2]))
        )
    elif op == ">":
        return FilterExpr(
            type="gt", left=_term(str(sexpr[1])), right=_term(str(sexpr[2]))
        )
    elif op == "in":
        items = [_term(str(x)) for x in sexpr[2:]]
        return FilterExpr(type="in", left=_term(str(sexpr[1])), right=items)
    elif op == "regex":
        return FilterExpr(type="regex", left=_term(str(sexpr[1])), right=str(sexpr[2]))
    else:
        raise KLParseError(f"Unknown filter operator: {op}")


def build_query(sexprs: List[List[Any]]) -> Query:
    """Build a Query AST from parsed S-expressions."""
    # Find the (query ...) form
    query_sexpr = None
    for s in sexprs:
        if s and isinstance(s[0], str) and s[0] == "query":
            query_sexpr = s
            break
    if query_sexpr is None:
        raise KLParseError("No (query ...) form found")

    # Parse components
    pattern = None
    projection = []
    ordering = []
    limit = None
    offset = None
    aggregates = []

    for item in query_sexpr[1:]:
        if isinstance(item, list):
            if item and item[0] == "where":
                pattern = _build_pattern(item[1])
            elif item and item[0] == "project":
                projection = [str(x) for x in item[1:] if not isinstance(x, list)]
            elif item and item[0] == "order-by":
                for ord_item in item[1:]:
                    if isinstance(ord_item, list) and len(ord_item) >= 2:
                        var = str(ord_item[0])
                        direction = str(ord_item[1])
                        ordering.append(
                            OrderBy(variable=var, ascending=(direction == "asc"))
                        )
            elif item and item[0] == "limit" and len(item) > 1:
                limit = int(item[1])
            elif item and item[0] == "offset" and len(item) > 1:
                offset = int(item[1])
            elif item and item[0] == "aggregate":
                for agg_item in item[1:]:
                    if isinstance(agg_item, list) and len(agg_item) >= 3:
                        aggregates.append(
                            Aggregate(
                                function=str(agg_item[0]),
                                variable=str(agg_item[1]),
                                alias=str(agg_item[2]),
                            )
                        )

    import uuid

    return Query(
        id=f"query:{uuid.uuid4().hex[:8]}",
        pattern=pattern
        or MatchPattern(
            fact_pattern=FactPattern(
                head=PatternTerm(type="wildcard", value="_"),
                relation=PatternTerm(type="wildcard", value="_"),
                tail=PatternTerm(type="wildcard", value="_"),
            )
        ),
        projection=projection,
        ordering=ordering,
        limit=limit,
        offset=offset,
        aggregates=aggregates,
    )


def parse_query(text: str) -> Query:
    """Parse KTF query text into Query AST."""
    sexprs = parse_ktf(text)
    return build_query(sexprs)


# ──────────────────────────────────────────────────────────────
# Evaluation Context (variable bindings)
# ──────────────────────────────────────────────────────────────


@dataclass
class BindingSet:
    bindings: Dict[str, str] = field(default_factory=dict)

    def bind(self, var: str, value: str):
        self.bindings[var] = value

    def get(self, var: str) -> Optional[str]:
        return self.bindings.get(var)

    def merge(self, other: "BindingSet") -> Optional["BindingSet"]:
        merged = BindingSet(bindings=dict(self.bindings))
        for k, v in other.bindings.items():
            if k in merged.bindings and merged.bindings[k] != v:
                return None  # Conflict
            merged.bindings[k] = v
        return merged

    def __bool__(self):
        return True  # Non-empty binding set


def _term_value(term: PatternTerm, bindings: BindingSet) -> Optional[str]:
    if term.type == "variable":
        return bindings.get(term.value)
    if term.type == "constant":
        return term.value
    return None  # wildcard matches anything


def _eval_filter(expr: FilterExpr, bindings: BindingSet) -> bool:
    left_val = _term_value(expr.left, bindings)
    if left_val is None:
        return False

    if expr.type == "eq":
        right_val = (
            _term_value(expr.right, bindings)
            if isinstance(expr.right, PatternTerm)
            else expr.right
        )
        return left_val == right_val
    elif expr.type == "neq":
        right_val = (
            _term_value(expr.right, bindings)
            if isinstance(expr.right, PatternTerm)
            else expr.right
        )
        return left_val != right_val
    elif expr.type == "lt":
        right_val = (
            _term_value(expr.right, bindings)
            if isinstance(expr.right, PatternTerm)
            else expr.right
        )
        left_number = _as_number(left_val)
        right_number = _as_number(right_val)
        if left_number is not None and right_number is not None:
            return left_number < right_number
        return left_val < str(right_val)
    elif expr.type == "gt":
        right_val = (
            _term_value(expr.right, bindings)
            if isinstance(expr.right, PatternTerm)
            else expr.right
        )
        left_number = _as_number(left_val)
        right_number = _as_number(right_val)
        if left_number is not None and right_number is not None:
            return left_number > right_number
        return left_val > str(right_val)
    elif expr.type == "in":
        rights = expr.right if isinstance(expr.right, list) else [expr.right]
        for r in rights:
            rv = _term_value(r, bindings) if isinstance(r, PatternTerm) else r
            if left_val == rv:
                return True
        return False
    elif expr.type == "regex":
        import re as _re

        if not isinstance(expr.right, str):
            return False
        text = left_val if isinstance(left_val, str) else ""
        try:
            return bool(_re.match(expr.right, text))
        except _re.error:
            return False
    return False


def _as_number(value: Any) -> float | None:
    """`value` as a number, or None when it is not one.

    A comparison reads literals out of a query, so a number may arrive
    spelled as a string; anything that is not a number falls back to text
    order rather than raising, which is what the old try/except did.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


# ──────────────────────────────────────────────────────────────
# KL Interpreter
# ──────────────────────────────────────────────────────────────


class KLInterpreter:
    """Executes KL queries against a knowledge graph."""

    def __init__(self, graph: KnowledgeGraph):
        self.graph = graph

    def execute(self, query: Query) -> List[BindingSet]:
        """Execute a query and return binding sets."""
        bindings = self._execute_pattern(query.pattern, BindingSet())

        # Apply projection
        if query.projection:
            projected = []
            for b in bindings:
                pb = BindingSet()
                for var in query.projection:
                    val = b.get(var)
                    if val is not None:
                        pb.bind(var, val)
                projected.append(pb)
            bindings = projected

        # Apply ordering
        if query.ordering:

            def sort_key(b: BindingSet):
                keys = []
                for ob in query.ordering:
                    val = b.get(ob.variable) or ""
                    keys.append(val)
                return tuple(keys)

            bindings.sort(key=sort_key)
            # Apply ascending/descending per field — simplified: sort by first only
            if query.ordering and not query.ordering[0].ascending:
                bindings.reverse()

        # Apply limit/offset
        if query.offset:
            bindings = bindings[query.offset :]
        if query.limit is not None:
            bindings = bindings[: query.limit]

        # Apply aggregates
        if query.aggregates:
            bindings = self._apply_aggregates(bindings, query.aggregates)

        return bindings

    def _execute_pattern(
        self, pattern: QueryPattern, bindings: BindingSet
    ) -> List[BindingSet]:
        if isinstance(pattern, MatchPattern):
            return self._match_fact_pattern(pattern.fact_pattern, bindings)
        elif isinstance(pattern, AndPattern):
            result = [bindings]
            for p in pattern.patterns:
                new_result = []
                for b in result:
                    new_result.extend(self._execute_pattern(p, b))
                result = new_result
                if not result:
                    break
            return result
        elif isinstance(pattern, OrPattern):
            result = []
            for p in pattern.patterns:
                result.extend(self._execute_pattern(p, bindings))
            return result
        elif isinstance(pattern, NotPattern):
            inner_results = self._execute_pattern(pattern.pattern, bindings)
            return [] if inner_results else [bindings]
        elif isinstance(pattern, FilterPattern):
            inner_results = self._execute_pattern(pattern.pattern, bindings)
            return [b for b in inner_results if _eval_filter(pattern.expr, b)]
        elif isinstance(pattern, ExistsPattern):
            inner_results = self._execute_pattern(pattern.pattern, bindings)
            return [bindings] if inner_results else []
        elif isinstance(pattern, ForAllPattern):
            # For simplicity, treat as Exists (full universal quantification needs negation)
            inner_results = self._execute_pattern(pattern.pattern, bindings)
            return [bindings] if inner_results else []
        return [bindings]

    def _match_fact_pattern(
        self, fp: FactPattern, bindings: BindingSet
    ) -> List[BindingSet]:
        head_val = _term_value(fp.head, bindings)
        rel_val = _term_value(fp.relation, bindings)
        tail_val = _term_value(fp.tail, bindings)

        facts = self.graph.query_facts(
            head=head_val,
            relation=rel_val,
            tail=tail_val,
        )

        results = []
        for fact in facts:
            b = BindingSet(bindings=dict(bindings.bindings))
            # Bind variables
            if fp.head.type == "variable":
                b.bind(fp.head.value, fact.head)
            if fp.relation.type == "variable":
                b.bind(fp.relation.value, fact.relation)
            if fp.tail.type == "variable":
                b.bind(fp.tail.value, fact.tail)

            # Context filtering
            if fp.context:
                if (
                    fp.context.min_certainty is not None
                    and fact.context.certainty < fp.context.min_certainty
                ):
                    continue
                if fp.context.source.type == "variable":
                    b.bind(fp.context.source.value, fact.context.source)
            results.append(b)
        return results

    def _apply_aggregates(
        self, bindings: List[BindingSet], aggregates: List[Aggregate]
    ) -> List[BindingSet]:
        if not bindings:
            return []
        result = BindingSet()
        for agg in aggregates:
            values = []
            for b in bindings:
                v = b.get(agg.variable)
                if v is not None:
                    values.append(v)
            if agg.function == "count":
                result.bind(agg.alias, str(len(values)))
            elif agg.function == "sum":
                try:
                    result.bind(agg.alias, str(sum(float(v) for v in values)))
                except ValueError:
                    result.bind(agg.alias, "0")
            elif agg.function == "avg":
                try:
                    nums = [float(v) for v in values]
                    result.bind(agg.alias, str(sum(nums) / len(nums) if nums else 0))
                except ValueError:
                    result.bind(agg.alias, "0")
            elif agg.function == "min":
                result.bind(agg.alias, str(min(values)) if values else "")
            elif agg.function == "max":
                result.bind(agg.alias, str(max(values)) if values else "")
            elif agg.function == "collect":
                result.bind(agg.alias, "[" + ", ".join(values) + "]")
        return [result]


# ──────────────────────────────────────────────────────────────
# Rule Engine (forward chaining)
# ──────────────────────────────────────────────────────────────


@dataclass
class Rule:
    id: str
    name: str
    pattern: QueryPattern
    action: dict  # {type: "assert"|"retract", fact_pattern: ...}
    weight: float = 1.0
    priority: int = 0


class RuleEngine:
    """Forward-chaining rule engine over a knowledge graph."""

    def __init__(self, graph: KnowledgeGraph):
        self.graph = graph
        self.rules: List[Rule] = []

    def add_rule(self, rule: Rule):
        self.rules.append(rule)

    def infer(self, max_iterations: int = 10) -> int:
        """Run forward chaining until fixpoint or max iterations."""
        interpreter = KLInterpreter(self.graph)
        total_new = 0

        for _ in range(max_iterations):
            new_this_iter = 0
            for rule in self.rules:
                bindings = interpreter.execute(
                    Query(
                        id="rule-internal",
                        pattern=rule.pattern,
                        projection=[],
                    )
                )
                for b in bindings:
                    # Assert new facts
                    if rule.action.get("type") == "assert":
                        fp = rule.action.get("fact_pattern")
                        if fp:
                            fact = self._instantiate_fact(fp, b)
                            if fact:
                                h = str(fact.content_hash())
                                # Only add if new
                                existing = self.graph.query_facts(
                                    head=fact.head,
                                    relation=fact.relation,
                                    tail=fact.tail,
                                )
                                if not any(
                                    e.context.source == fact.context.source
                                    and e.context.timestamp == fact.context.timestamp
                                    for e in existing
                                ):
                                    self.graph.add_hyperedge(
                                        Hyperedge(
                                            id=f"rule:{rule.id}:{h}",
                                            facts=[fact],
                                            context=fact.context,
                                        )
                                    )
                                    new_this_iter += 1
            total_new += new_this_iter
            if new_this_iter == 0:
                break
        return total_new

    def _instantiate_fact(self, fp: dict, bindings: BindingSet) -> Optional[Fact]:
        """Create a Fact from a fact_pattern dict and bindings."""
        try:
            head = bindings.get(fp.get("head", "")) or fp.get("head", "")
            relation = bindings.get(fp.get("relation", "")) or fp.get("relation", "")
            tail = bindings.get(fp.get("tail", "")) or fp.get("tail", "")
            ctx = Context(
                source=bindings.get(fp.get("context_source", "system")) or "system",
                timestamp=int(time.time() * 1_000_000),
                certainty=fp.get("certainty", 1.0),
            )
            return Fact(head=head, relation=relation, tail=tail, context=ctx)
        except Exception:
            return None


import time  # noqa: E402
