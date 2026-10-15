from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast

from .ast import (
    AgentCall,
    AgentDecl,
    ArrayExpr,
    Assignment,
    BinaryExpr,
    Block,
    BoardDecl,
    BreakExpr,
    ConditionalExpr,
    ContinueExpr,
    EnumDecl,
    Expression,
    FailExpr,
    Field,
    FieldExpr,
    ForExpr,
    FunctionCall,
    FunctionCallExpr,
    FunctionDecl,
    FunctionExpr,
    FunctionRefExpr,
    IndexExpr,
    LiteralExpr,
    LocalBinding,
    MatchArm,
    MatchExpr,
    MemoryDecl,
    MethodCallExpr,
    ModelCall,
    ModelDecl,
    ModelParameters,
    Program,
    RecordDecl,
    RecordExpr,
    RepeatExpr,
    ReturnExpr,
    ShapeDecl,
    ShapeMethod,
    Step,
    SwarmDecl,
    TestDecl,
    TestStep,
    ThinkCall,
    ToolCall,
    ToolDecl,
    TryExpr,
    UnaryExpr,
    VariableExpr,
    WorkflowCall,
    WorkflowDecl,
    method_function,
)
from .lexer import Token, lex
from .sugar import VALUE_CALL, inferred_name
from .thinking import GENERAL_INPUT, GENERAL_OUTPUT
from .thinking import VERBS as THINKING_VERBS


class ParseError(ValueError):
    pass


_COMPOUND = ("+=", "-=", "*=", "/=")
#: A `while` is a repeat with more passes than any run's loop budget allows,
#: so a loop that never ends is stopped by the budget, never silently.
WHILE_PASSES = 1_000_000_000


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        token = self.current
        self.index += 1
        return token

    def check(self, value: str) -> bool:
        return (
            self.current.kind in {"IDENT", "SYMBOL", "OP", "ARROW"}
            and self.current.value == value
        )

    def accept(self, value: str) -> bool:
        if self.check(value):
            self.advance()
            return True
        return False

    def expect(self, value: str) -> Token:
        if not self.check(value):
            token = self.current
            raise ParseError(
                f"Expected {value!r} at {token.line}:{token.column}, got {token.value!r}"
            )
        return self.advance()

    def identifier(self) -> str:
        token = self.current
        if token.kind != "IDENT":
            raise ParseError(f"Expected identifier at {token.line}:{token.column}")
        self.advance()
        return token.value

    def at_assignment(self) -> bool:
        """True when the next two tokens are `name =`.

        `==` is a single distinct token, so an identifier followed by `=` is
        unambiguously an assignment target.
        """
        if self.current.kind != "IDENT" or self.index + 1 >= len(self.tokens):
            return False
        following = self.tokens[self.index + 1]
        return following.value == "=" and following.kind in {"SYMBOL", "OP"}

    def parse_binding(self) -> LocalBinding:
        """`let name [: Type] = expr` or the mutable `var name [: Type] = expr`."""
        mutable = self.accept("var")
        if not mutable:
            self.expect("let")
        name = self.identifier()
        type_name = None
        if self.accept(":"):
            type_name = self.parse_type_name()
        self.expect("=")
        return LocalBinding(name, type_name, self.expression(), mutable)

    def at_update(self) -> bool:
        """True at `name += ...` or at `name[index] = ...` and their kin."""

        if self.current.kind != "IDENT" or self.index + 1 >= len(self.tokens):
            return False
        position = self.index + 1
        if self.tokens[position].value == "[":
            depth = 0
            while position < len(self.tokens):
                value = self.tokens[position].value
                kind = self.tokens[position].kind
                if kind == "SYMBOL" and value == "[":
                    depth += 1
                elif kind == "SYMBOL" and value == "]":
                    depth -= 1
                    if depth == 0:
                        break
                elif kind == "EOF" or (kind == "SYMBOL" and value in "{}"):
                    return False
                position += 1
            position += 1
            if position >= len(self.tokens):
                return False
            following = self.tokens[position]
            return following.kind in {"SYMBOL", "OP"} and following.value in (
                "=",
                *_COMPOUND,
            )
        following = self.tokens[position]
        return following.kind == "OP" and following.value in _COMPOUND

    def at_statement(self) -> bool:
        return (
            self.check("let")
            or self.check("var")
            or self.at_assignment()
            or self.at_update()
        )

    def parse_statement(self) -> LocalBinding | Assignment:
        if self.check("let") or self.check("var"):
            return self.parse_binding()
        target = self.identifier()
        index: Expression | None = None
        if self.accept("["):
            index = self.expression()
            self.expect("]")
        operator = self.advance()
        if operator.value not in ("=", *_COMPOUND):
            raise ParseError(
                f"Expected '=' at {operator.line}:{operator.column}, "
                f"got {operator.value!r}"
            )
        value = self.expression()
        current: Expression = VariableExpr(target)
        if index is not None:
            current = IndexExpr(current, index)
        if operator.value != "=":
            # `total += x` is `total = total + x`.
            value = BinaryExpr(current, operator.value[0], value)
        if index is not None:
            # `items[i] = v` is `items = items.set(key: i, value: v)`: the
            # variable gets a new array or map, and the old one is untouched.
            value = MethodCallExpr(
                VariableExpr(target), "set", (("key", index), ("value", value))
            )
        return Assignment(target, value)

    def parse_block(self) -> Expression:
        """Parse `{ statement* result? }` as an expression.

        A body with no trailing expression evaluates to Null, which is what
        makes a statement-only loop body read naturally:
        ``for x in values { total = total + x }``.

        Returns the bare result expression when there are no statements, so
        every program written before blocks existed produces the same AST as
        before and no existing consumer has to learn about Block.
        """
        self.expect("{")
        statements: list[LocalBinding | Assignment | Expression] = []
        result: Expression | None = None
        # As in a function body, declarations, assignments, and expressions
        # may be interleaved. An expression followed by another line was a
        # statement, such as `if not n.exists() { continue }`; only the last
        # one is the block's value.
        while not self.check("}"):
            if result is not None:
                statements.append(result)
                result = None
            if self.at_statement():
                statements.append(self.parse_statement())
            else:
                result = self.expression()
        self.expect("}")
        if not statements:
            if result is None:
                raise ParseError("Empty block")
            return result
        return Block(tuple(statements), result)

    def string_or_identifier(self) -> str:
        token = self.current
        if token.kind not in {"STRING", "IDENT"}:
            raise ParseError(
                f"Expected string or identifier at {token.line}:{token.column}"
            )
        self.advance()
        return token.value

    def expression(self) -> Expression:
        if self.accept("break"):
            if self.current.kind == "NUMBER":
                token = self.advance()
                self.expect(":")
                # BreakExpr carries no label, so accepting one would silently
                # break the innermost loop instead of the labelled one.
                raise ParseError(
                    f"Labelled break is not supported at {token.line}:{token.column}"
                )
            return BreakExpr()
        if self.accept("continue"):
            return ContinueExpr()
        if self.accept("return"):
            # An early return. A function's final `return` is consumed by
            # parse_function before any expression is parsed, so reaching
            # this means the `return` is nested inside a block.
            return ReturnExpr(self.expression())
        if self.accept("repeat"):
            return self.parse_repeat_expression()
        if self.accept("while"):
            # `while c { body }` repeats until c is false. It is a repeat
            # that leaves when the condition fails, so every pass over the
            # tree already understands it; the run's loop budget bounds it.
            condition = self.expression()
            body = self.parse_block()
            leave = ConditionalExpr(
                UnaryExpr("not", condition), BreakExpr(), LiteralExpr(None)
            )
            return RepeatExpr(LiteralExpr(WHILE_PASSES), Block((leave,), body))
        if self.accept("try"):
            return self.parse_try_expression()
        # `fail "reason"` or `fail f"..."`. Only a text may follow, so a
        # variable that happens to be called fail is still a variable.
        if self.check("fail") and (
            self.peek().kind == "STRING"
            or (self.peek().kind == "IDENT" and self.peek().value == "f")
        ):
            self.advance()
            return FailExpr(self.expression())
        if self.accept("match"):
            subject = self.expression()
            self.expect("{")
            arms: list[MatchArm] = []
            while not self.accept("}"):
                self.expect("case")
                pattern = None
                if not self.accept("_"):
                    negative = self.accept("-")
                    token = self.current
                    if token.kind == "STRING" and not negative:
                        pattern = LiteralExpr(self.advance().value)
                    elif token.kind == "NUMBER":
                        self.advance()
                        number = (
                            float(token.value)
                            if "." in token.value
                            else int(token.value)
                        )
                        pattern = LiteralExpr(-number if negative else number)
                    elif (
                        token.kind == "IDENT"
                        and token.value in {"true", "false", "null"}
                        and not negative
                    ):
                        self.advance()
                        pattern = LiteralExpr(
                            {"true": True, "false": False, "null": None}[token.value]
                        )
                    else:
                        raise ParseError("Match patterns must be scalar literals or _")
                self.expect("{")
                value = self.expression()
                self.expect("}")
                arms.append(MatchArm(pattern, value))
            return MatchExpr(subject, tuple(arms))
        if self.accept("if"):
            condition = self.expression()
            when_true = self.parse_block()
            if not self.accept("else"):
                # `if` with no `else` does nothing otherwise: its other
                # branch is null, so as a value it is optional.
                return ConditionalExpr(condition, when_true, LiteralExpr(None))
            # `else if` chains: the else branch is itself a conditional.
            when_false = self.expression() if self.check("if") else self.parse_block()
            return ConditionalExpr(condition, when_true, when_false)
        if self.accept("for"):
            item_name = self.identifier()
            self.expect("in")
            sequence = self.expression()
            # `for i in 1 to n` counts through n; `for i in 0 until n` stops
            # before n. Both are the Number methods of the same names.
            for word in ("to", "until"):
                if self.accept(word):
                    sequence = MethodCallExpr(
                        sequence, word, (("end", self.expression()),)
                    )
                    break
            where = self.expression() if self.accept("where") else None
            limit = 128
            capped = False
            if self.accept("limit"):
                token = self.advance()
                if (
                    token.kind != "NUMBER"
                    or not token.value.isdigit()
                    or not 1 <= int(token.value) <= 1_000_000
                ):
                    raise ParseError(
                        "for loop limit must be an integer from 1 to 1000000"
                    )
                limit = int(token.value)
                capped = True
            body = self.parse_block()
            return ForExpr(item_name, sequence, body, limit, where, capped)
        return self.parse_or_expression()

    def parse_or_expression(self) -> Expression:
        value = self.parse_and_expression()
        while self.accept("or"):
            value = BinaryExpr(value, "or", self.parse_and_expression())
        return value

    def parse_and_expression(self) -> Expression:
        value = self.parse_equality_expression()
        while self.accept("and"):
            value = BinaryExpr(value, "and", self.parse_equality_expression())
        return value

    def parse_equality_expression(self) -> Expression:
        value = self.parse_comparison_expression()
        while any(self.check(operator) for operator in ("==", "!=")):
            operator = self.advance().value
            value = BinaryExpr(value, operator, self.parse_comparison_expression())
        return value

    def parse_comparison_expression(self) -> Expression:
        value = self.parse_term_expression()
        while any(self.check(operator) for operator in ("<", "<=", ">", ">=")):
            operator = self.advance().value
            value = BinaryExpr(value, operator, self.parse_term_expression())
        return value

    def parse_term_expression(self) -> Expression:
        value = self.parse_factor_expression()
        while any(self.check(operator) for operator in ("+", "-")):
            operator = self.advance().value
            value = BinaryExpr(value, operator, self.parse_factor_expression())
        return value

    def parse_factor_expression(self) -> Expression:
        value = self.parse_unary_expression()
        while any(self.check(operator) for operator in ("*", "/", "%")):
            operator = self.advance().value
            value = BinaryExpr(value, operator, self.parse_unary_expression())
        return value

    def parse_unary_expression(self) -> Expression:
        if self.check("-") or self.check("not"):
            operator = self.advance().value
            return UnaryExpr(operator, self.parse_unary_expression())
        return self.parse_postfix_expression()

    def peek(self) -> Token:
        if self.index + 1 < len(self.tokens):
            return self.tokens[self.index + 1]
        return self.tokens[-1]

    def parse_postfix_expression(self) -> Expression:
        value = self.parse_primary_expression()
        while True:
            if self.accept("."):
                if self.current.kind == "IDENT" and (
                    self.peek().kind == "SYMBOL" and self.peek().value == "("
                ):
                    method = self.advance().value
                    self.expect("(")
                    args = self.parse_arguments()
                    value = MethodCallExpr(value, method, args)
                else:
                    value = FieldExpr(value, self.identifier())
            elif self.accept("["):
                index = self.expression()
                self.expect("]")
                value = IndexExpr(value, index)
            else:
                return value

    def parse_primary_expression(self) -> Expression:
        if self.accept("ref"):
            return FunctionRefExpr(self.identifier())

        if self.accept("fn"):
            return self.parse_function_expression()

        if self.check("["):
            value: Expression = self.parse_array_expression()
        elif self.check("{"):
            value = self.parse_record_expression()
        elif self.accept("("):
            value = self.expression()
            self.expect(")")
        else:
            token = self.current
            if token.kind == "STRING":
                self.advance()
                value = LiteralExpr(token.value)
            elif token.kind == "NUMBER":
                self.advance()
                number = float(token.value) if "." in token.value else int(token.value)
                value = LiteralExpr(number)
            elif token.kind == "IDENT":
                self.advance()
                if token.value in {"true", "false", "null"}:
                    literal = {"true": True, "false": False, "null": None}
                    value = LiteralExpr(literal[token.value])
                elif (
                    token.value == "f"
                    and self.current.kind == "STRING"
                    and self.current.line == token.line
                    and self.current.column == token.column + 1
                ):
                    # `f"Hello {name}"`: text with values written into it.
                    value = self.parse_template(self.advance())
                elif self.accept("("):
                    value = FunctionCallExpr(token.value, self.parse_arguments())
                else:
                    value = VariableExpr(token.value)
            else:
                raise ParseError(
                    f"Expected expression at {token.line}:{token.column}, "
                    f"got {token.value!r}"
                )
        return value

    def parse_template(self, token: Token) -> Expression:
        """Turn `f"Total: {order.total} for {name}"` into the joining it means.

        Each `{...}` holds one expression. The result is exactly the text
        joined with `+`, so everything the checker and the trace know about
        joining text applies and nothing new exists at run time. A value
        written in must be Text or a Number. `{{` and `}}` write a brace.
        """

        text = token.value
        parts: list[Expression] = []
        literal = ""
        index = 0
        while index < len(text):
            char = text[index]
            if char in "{}" and text[index + 1 : index + 2] == char:
                literal += char
                index += 2
            elif char == "{":
                end = text.find("}", index)
                if end < 0:
                    raise ParseError(
                        f"Expected '}}' at {token.line}:{token.column}, "
                        "got 'the end of the text'"
                    )
                inner = text[index + 1 : end].strip()
                if not inner:
                    raise ParseError(
                        f"Expected expression at {token.line}:{token.column}, got '}}'"
                    )
                if literal:
                    parts.append(LiteralExpr(literal))
                    literal = ""
                sub = Parser(lex(inner))
                parts.append(sub.expression())
                if sub.current.kind != "EOF":
                    raise ParseError(
                        f"Expected '}}' at {token.line}:{token.column}, "
                        f"got {sub.current.value!r}"
                    )
                index = end + 1
            elif char == "}":
                raise ParseError(
                    f"Expected '{{' at {token.line}:{token.column}, got '}}'"
                )
            else:
                literal += char
                index += 1
        if literal:
            parts.append(LiteralExpr(literal))
        # Begin with text, so that the first join is text joined to a value.
        joined: Expression = LiteralExpr("")
        if parts and isinstance(parts[0], LiteralExpr):
            joined, parts = parts[0], parts[1:]
        for part in parts:
            joined = BinaryExpr(joined, "+", part)
        return joined

    def parse_function_expression(self) -> FunctionExpr:
        parameters: list[Field] = []
        self.expect("(")
        while True:
            if self.accept(")"):
                break
            parameter_name = self.identifier()
            self.expect(":")
            type_name = self.parse_type_name()
            parameters.append(Field(parameter_name, type_name))
            if self.accept(")"):
                break
            self.expect(",")
        output_type: str | None = None
        if self.accept("->"):
            output_type = self.parse_type_name()
        self.expect("{")
        body = self.expression()
        self.expect("}")
        return FunctionExpr(tuple(parameters), output_type, body)

    def parse_repeat_expression(self) -> RepeatExpr:
        token = self.current
        following = self.tokens[min(self.index + 1, len(self.tokens) - 1)]
        if token.kind == "NUMBER" and following.value == "times":
            self.advance()
            if not token.value.isdigit() or not 1 <= int(token.value) <= 1_000_000:
                raise ParseError("repeat count must be an integer from 1 to 1000000")
            count: Expression = LiteralExpr(int(token.value))
        else:
            # Any Number expression: `repeat items.length() times { ... }`.
            count = self.parse_or_expression()
        self.expect("times")
        body = self.parse_block()
        return RepeatExpr(count, body)

    def parse_try_expression(self) -> TryExpr:
        body = self.parse_block()
        retry = 0
        if self.accept("retry"):
            token = self.advance()
            if (
                token.kind != "NUMBER"
                or not token.value.isdigit()
                or not 1 <= int(token.value) <= 32
            ):
                raise ParseError("try retry count must be an integer from 1 to 32")
            retry = int(token.value)
        if not self.accept("otherwise"):
            return TryExpr(body, retry)
        # `otherwise problem { ... }` names the reason; `otherwise { ... }`
        # does not need it.
        error_name = None if self.check("{") else self.identifier()
        return TryExpr(body, retry, self.parse_block(), error_name)

    def parse_array_expression(self) -> ArrayExpr:
        self.expect("[")
        items: list[Expression] = []
        if self.accept("]"):
            return ArrayExpr(())
        while True:
            items.append(self.expression())
            if self.accept("]"):
                return ArrayExpr(tuple(items))
            self.expect(",")

    def parse_record_expression(self) -> RecordExpr:
        self.expect("{")
        fields: list[tuple[str, Expression]] = []
        if self.accept("}"):
            return RecordExpr(())
        base: Expression | None = None
        if self.check(".") and self.peek().value == ".":
            # `{..task, done: true}`: the record `task` with fields changed.
            self.advance()
            self.advance()
            base = self.expression()
            if self.accept("}"):
                return RecordExpr((), base)
            self.expect(",")
        while True:
            # A quoted key writes a map entry: `{"north": 4}`. Both spellings
            # produce the same literal; its expected type decides which it is.
            if self.current.kind == "STRING":
                name = self.advance().value
            else:
                name = self.identifier()
            self.expect(":")
            fields.append((name, self.expression()))
            if self.accept("}"):
                return RecordExpr(tuple(fields), base)
            self.expect(",")

    def parse(self) -> Program:
        program = Program()
        self.record_methods: list[FunctionDecl] = []
        while self.current.kind != "EOF":
            keyword = self.current.value if self.current.kind == "IDENT" else None
            if keyword == "import":
                self.advance()
                if self.current.kind != "STRING":
                    raise ParseError("Import requires a quoted local .kos path")
                program.imports.append(self.advance().value)
            elif keyword == "export":
                self.advance()
                program.exports.extend(self.parse_name_list())
            elif keyword == "record":
                program.records.append(self.parse_record())
            elif keyword == "tool":
                program.tools.append(self.parse_tool())
            elif keyword == "agent":
                program.agents.append(self.parse_agent())
            elif keyword == "swarm":
                program.swarms.append(self.parse_swarm())
            elif keyword == "workflow":
                program.workflows.append(self.parse_workflow())
            elif keyword == "fn":
                program.functions.append(self.parse_function())
            elif keyword == "model":
                program.models.append(self.parse_model())
            elif keyword == "default" and self.peek().value == "model":
                self.advance()
                program.models.append(replace(self.parse_model(), default=True))
            elif keyword == "shape" and self.peek().kind == "IDENT":
                program.shapes.append(self.parse_shape())
            elif keyword == "enum" and self.peek().kind == "IDENT":
                self.advance()
                enum_name = self.identifier()
                self.expect("{")
                values: list[str] = []
                while not self.accept("}"):
                    values.append(self.identifier())
                    self.accept(",")
                program.enums.append(EnumDecl(enum_name, tuple(values)))
            elif keyword == "board" and self.peek().kind == "IDENT":
                self.advance()
                board_name = self.identifier()
                self.expect("of")
                program.boards.append(BoardDecl(board_name, self.identifier()))
            elif keyword == "memory" and self.peek().kind == "IDENT":
                self.advance()
                memory_name = self.identifier()
                self.expect("of")
                program.memories.append(MemoryDecl(memory_name, self.identifier()))
            elif keyword in ("test", "eval") and self.peek().kind == "STRING":
                program.tests.append(self.parse_test())
            else:
                token = self.current
                raise ParseError(
                    f"Unexpected declaration {token.value!r} at {token.line}:{token.column}"
                )
        program.functions.extend(self.record_methods)
        return program

    def parse_test(self) -> TestDecl:
        """Parse `test "name" { answers / name = ... / expect ... }`."""

        opening = self.advance()
        line, live = opening.line, opening.value == "eval"
        name = self.advance().value
        self.expect("{")
        answers: list[tuple[str, Expression]] = []
        steps: list[TestStep] = []
        while not self.accept("}"):
            token = self.current
            if self.at_assignment() or (
                token.kind == "IDENT" and self.peek().value == ":"
            ):
                target = self.identifier()
                type_name = self.parse_type_name() if self.accept(":") else None
                self.expect("=")
                steps.append(
                    TestStep(
                        "let", self.expression(), target, token.line, "", type_name
                    )
                )
            elif self.accept("answers"):
                model = self.identifier()
                self.expect(":")
                answers.append((model, self.expression()))
            else:
                self.expect("expect")
                start = self.index
                value = self.expression()
                text = self.written(start, self.index)
                steps.append(TestStep("expect", value, "", token.line, text))
        return TestDecl(name, tuple(answers), tuple(steps), line, live)

    def written(self, start: int, end: int) -> str:
        """The tokens from `start` to `end` roughly as they were typed."""

        pieces: list[str] = []
        column = None
        line = None
        for token in self.tokens[start:end]:
            raw = json.dumps(token.value) if token.kind == "STRING" else token.value
            if pieces and (token.line != line or token.column > (column or 0)):
                pieces.append(" ")
            pieces.append(raw)
            line, column = token.line, token.column + len(raw)
        return "".join(pieces)

    def parse_model(self) -> ModelDecl:
        self.expect("model")
        name = self.identifier()
        if not self.check("{"):
            # `model assistant`: a general model that takes and returns text.
            return ModelDecl(name, GENERAL_INPUT, GENERAL_OUTPUT)
        self.expect("{")
        values: dict[str, str] = {}
        deployment: str | None = None
        parameters = ModelParameters()
        seen: set[str] = set()
        while not self.accept("}"):
            key = self.identifier()
            if key in seen:
                raise ParseError(f"Invalid or duplicate model field {key!r}")
            seen.add(key)
            if key == "parameters":
                self.expect(":")
                parameters = self.parse_model_parameters()
                continue
            if key not in {"input", "output", "deployment"}:
                raise ParseError(f"Invalid model field {key!r}")
            self.expect(":")
            if key == "deployment":
                deployment = self.string_or_identifier()
            else:
                values[key] = self.identifier()
        if not values:
            # Only a deployment or parameters: still a general text model.
            values = {"input": GENERAL_INPUT, "output": GENERAL_OUTPUT}
        if set(values) != {"input", "output"}:
            raise ParseError(f"Model {name!r} requires input and output records")
        return ModelDecl(
            name, values["input"], values["output"], deployment, parameters
        )

    def parse_model_parameters(self) -> ModelParameters:
        self.expect("{")
        temperature: float | None = None
        max_output_tokens: int | None = None
        top_p: float | None = None
        frequency_penalty: float | None = None
        presence_penalty: float | None = None
        stop: tuple[str, ...] = ()
        seen: set[str] = set()
        while not self.accept("}"):
            key = self.identifier()
            if key in seen:
                raise ParseError(f"Duplicate model parameter {key!r}")
            seen.add(key)
            self.expect(":")
            if key == "stop":
                stop = self.parse_string_list()
                continue
            if key == "max_output_tokens":
                token = self.advance()
                if token.kind != "NUMBER" or not token.value.isdigit():
                    raise ParseError(
                        "Model max_output_tokens must be a positive integer"
                    )
                max_output_tokens = int(token.value)
                continue
            if key not in {
                "temperature",
                "top_p",
                "frequency_penalty",
                "presence_penalty",
            }:
                raise ParseError(f"Unknown model parameter {key!r}")
            sign = -1 if self.accept("-") else 1
            token = self.advance()
            if token.kind != "NUMBER":
                raise ParseError(f"Model {key} must be a number")
            value = sign * float(token.value)
            if key == "temperature":
                temperature = value
            elif key == "top_p":
                top_p = value
            elif key == "frequency_penalty":
                frequency_penalty = value
            else:
                presence_penalty = value
        return ModelParameters(
            temperature,
            max_output_tokens,
            top_p,
            frequency_penalty,
            presence_penalty,
            stop,
        )

    def parse_string_list(self) -> tuple[str, ...]:
        self.expect("[")
        values: list[str] = []
        while not self.accept("]"):
            values.append(self.string_or_identifier())
            self.accept(",")
        return tuple(values)

    def _at_map_suffix(self) -> bool:
        """True at a `{}` written directly against the type, as in `Number{}`.

        A body brace is always separated from the type before it by a space,
        so requiring the three tokens to touch keeps `-> Report { }` a body.
        """

        if not self.check("{") or self.index == 0:
            return False
        previous, opening, closing = (
            self.tokens[self.index - 1],
            self.current,
            self.peek(),
        )
        return (
            closing.value == "}"
            and previous.line == opening.line == closing.line
            and previous.column + len(previous.value) == opening.column
            and opening.column + 1 == closing.column
        )

    def parse_type_name(self, depth: int = 0) -> str:
        if depth >= 32:
            raise ParseError("Function type nesting exceeds 32")
        if self.accept("fn"):
            from .function_types import function_type

            self.expect("(")
            parameters: list[Field] = []
            while not self.accept(")"):
                name = self.identifier()
                self.expect(":")
                parameters.append(Field(name, self.parse_type_name(depth + 1)))
                if not self.check(")"):
                    self.expect(",")
            self.expect("->")
            return function_type(tuple(parameters), self.parse_type_name(depth + 1))
        name = self.identifier()
        # Suffixes: `T[]` list, `T{}` map from Text to T, `T?` optional.
        while True:
            if self.check("[") and self.peek().value == "]":
                self.advance()
                self.advance()
                name += "[]"
            elif self._at_map_suffix():
                self.advance()
                self.advance()
                name += "{}"
            elif self.check("?"):
                if name.endswith("?"):
                    token = self.current
                    raise ParseError(
                        f"A type is optional once, not twice, at "
                        f"{token.line}:{token.column}"
                    )
                self.advance()
                name += "?"
            else:
                return name

    def parse_record(self) -> RecordDecl:
        self.expect("record")
        name = self.identifier()
        self.expect("{")
        fields: list[Field] = []
        methods: list[str] = []
        while not self.accept("}"):
            if self.check("fn"):
                # A function that belongs to the record. It is kept with the
                # program's other functions, under the record's name.
                method = self.parse_function(owner=name)
                methods.append(method.name.split("$", 1)[1])
                self.record_methods.append(method)
                continue
            field_name = self.identifier()
            self.expect(":")
            type_name = self.parse_type_name()
            fields.append(Field(field_name, type_name, self.parse_default()))
            self.accept(",")
        return RecordDecl(name, tuple(fields), tuple(methods))

    def parse_tool(self) -> ToolDecl:
        self.expect("tool")
        name = self.identifier()
        self.expect("{")
        values: dict[str, str] = {}
        while not self.accept("}"):
            key = self.identifier()
            if key == "source":
                # `source mcp "name"` is an MCP server; `source system "shell"`
                # or `"files"` asks for approved access to this machine.
                if self.accept("system"):
                    values["source"] = "system:" + self.string_or_identifier()
                else:
                    self.expect("mcp")
                    values["source"] = self.string_or_identifier()
            else:
                self.expect(":")
                values[key] = self.string_or_identifier()
        required = {"source", "operation", "access", "output"}
        missing = required - values.keys()
        if missing:
            raise ParseError(f"Tool {name!r} is missing: {', '.join(sorted(missing))}")
        return ToolDecl(
            name,
            values["source"],
            values["operation"],
            values["access"],
            values["output"],
            values.get("input"),
        )

    def parse_name_list(self) -> tuple[str, ...]:
        self.expect("[")
        values: list[str] = []
        while not self.accept("]"):
            values.append(self.identifier())
            self.accept(",")
        return tuple(values)

    def parse_agent(self) -> AgentDecl:
        self.expect("agent")
        name = self.identifier()
        self.expect("{")
        can: tuple[str, ...] = ()
        cannot: tuple[str, ...] = ()
        model: str | None = None
        max_steps = 8
        max_tool_calls = 256
        max_model_calls = 64
        instructions: str | None = None
        deadline_seconds: float | None = None
        plan: str | None = None
        check: str | None = None
        seen: set[str] = set()
        while not self.accept("}"):
            key = self.identifier()
            if key in seen:
                raise ParseError(f"Duplicate agent field {key!r}")
            seen.add(key)
            self.expect(":")
            if key == "can":
                can = self.parse_name_list()
            elif key == "cannot":
                cannot = self.parse_name_list()
            elif key == "model":
                model = self.identifier()
            elif key == "instructions":
                instructions = self.string_or_identifier()
            elif key in {"max_steps", "max_tool_calls", "max_model_calls"}:
                token = self.advance()
                if token.kind != "NUMBER" or not token.value.isdigit():
                    raise ParseError(f"Agent {key} must be a positive integer")
                value = int(token.value)
                if key == "max_steps":
                    max_steps = value
                elif key == "max_tool_calls":
                    max_tool_calls = value
                else:
                    max_model_calls = value
            elif key == "deadline_seconds":
                token = self.advance()
                if token.kind != "NUMBER":
                    raise ParseError("Agent deadline_seconds must be a positive number")
                deadline_seconds = float(token.value)
            elif key == "plan":
                plan = self.identifier()
                if plan not in ("first", "off"):
                    raise ParseError("Agent plan must be `first` or `off`")
            elif key == "check":
                check = self.identifier()
            else:
                raise ParseError(f"Unknown agent field {key!r}")
        return AgentDecl(
            name,
            can,
            cannot,
            model,
            max_steps,
            max_tool_calls,
            max_model_calls,
            instructions,
            deadline_seconds,
            plan,
            check,
        )

    def parse_swarm(self) -> SwarmDecl:
        """Parse ``swarm Name { agents: [..] workflow: w max_rounds: n require approval }``.

        ``require approval`` is mandatory: a swarm without a human approval
        gate is refused here rather than silently downgraded at run time.
        """

        self.expect("swarm")
        name = self.identifier()
        self.expect("{")
        agents: tuple[str, ...] | None = None
        workflow: str | None = None
        max_rounds = 1
        approval_required = False
        seen: set[str] = set()
        while not self.accept("}"):
            if self.accept("require"):
                self.expect("approval")
                if "require" in seen:
                    raise ParseError("Duplicate swarm require approval clause")
                seen.add("require")
                approval_required = True
                continue
            key = self.identifier()
            if key in seen:
                raise ParseError(f"Duplicate swarm field {key!r}")
            seen.add(key)
            self.expect(":")
            if key == "agents":
                agents = self.parse_name_list()
                if not agents:
                    raise ParseError(f"Swarm {name!r} needs at least one agent")
            elif key == "workflow":
                workflow = self.identifier()
            elif key == "max_rounds":
                token = self.advance()
                if token.kind != "NUMBER" or not token.value.isdigit():
                    raise ParseError("Swarm max_rounds must be a positive integer")
                max_rounds = int(token.value)
            else:
                raise ParseError(f"Unknown swarm field {key!r}")
        if agents is None:
            raise ParseError(f"Swarm {name!r} requires an agents list")
        if workflow is None:
            raise ParseError(f"Swarm {name!r} requires a workflow")
        if not approval_required:
            raise ParseError(
                f"Swarm {name!r} must declare `require approval`; "
                "Kosika does not run an unreviewed swarm"
            )
        return SwarmDecl(name, agents, workflow, max_rounds, approval_required)

    def parse_workflow(self) -> WorkflowDecl:
        self.expect("workflow")
        name = self.identifier()
        self.expect("(")
        parameters: list[Field] = []
        while not self.accept(")"):
            parameter_name = self.identifier()
            self.expect(":")
            parameters.append(Field(parameter_name, self.identifier()))
            if not self.check(")"):
                self.expect(",")
        self.expect("->")
        output_type = self.identifier()
        self.expect("{")
        steps: list[Step] = []
        approval_required = False
        budget: list[tuple[str, int | float]] = []
        groups = 0
        while not self.accept("}"):
            if self.accept("step"):
                steps.append(self.parse_step())
            elif self.at_assignment():
                steps.append(self.parse_short_step())
            elif self.check("parallel") and self.peek().value == "{":
                # `parallel { a = ...  b = ... }`: see Step.group.
                self.advance()
                self.advance()
                groups += 1
                while not self.accept("}"):
                    if not self.at_assignment():
                        token = self.current
                        raise ParseError(
                            f"Unexpected workflow item {token.value!r} at "
                            f"{token.line}:{token.column}"
                        )
                    steps.append(replace(self.parse_short_step(), group=groups))
            elif self.accept("require"):
                self.expect("approval")
                approval_required = True
            elif self.accept("budget"):
                budget.extend(self.parse_budget())
            else:
                token = self.current
                raise ParseError(
                    f"Unexpected workflow item {token.value!r} at {token.line}:{token.column}"
                )
        return WorkflowDecl(
            name,
            output_type,
            tuple(steps),
            approval_required,
            tuple(parameters),
            tuple(budget),
        )

    def parse_budget(self) -> list[tuple[str, int | float]]:
        """Parse `<n> model calls, <n> tool calls, <n> seconds` in any order."""

        items: list[tuple[str, int | float]] = []
        while True:
            token = self.advance()
            if token.kind != "NUMBER":
                raise ParseError(
                    f"Expected 'amount' at {token.line}:{token.column}, "
                    f"got {token.value!r}"
                )
            amount = float(token.value) if "." in token.value else int(token.value)
            if self.accept("model"):
                self.expect("calls")
                items.append(("model_calls", amount))
            elif self.accept("tool"):
                self.expect("calls")
                items.append(("tool_calls", amount))
            else:
                self.expect("seconds")
                items.append(("seconds", amount))
            if not self.accept(","):
                return items

    def parse_shape(self) -> ShapeDecl:
        """`shape Priced { fn price(self) -> Number ... }`: functions
        without bodies, each taking the value first as `self`."""

        self.expect("shape")
        name = self.identifier()
        self.expect("{")
        methods: list[ShapeMethod] = []
        while not self.accept("}"):
            self.expect("fn")
            method = self.identifier()
            self.expect("(")
            self.expect("self")
            parameters: list[Field] = []
            if not self.check(")"):
                self.expect(",")
            while not self.accept(")"):
                parameter_name = self.identifier()
                self.expect(":")
                parameters.append(Field(parameter_name, self.parse_type_name()))
                if not self.check(")"):
                    self.expect(",")
            self.expect("->")
            methods.append(ShapeMethod(method, tuple(parameters), self.parse_type_name()))
        return ShapeDecl(name, tuple(methods))

    def parse_default(self) -> Expression | None:
        """`= value` after a parameter or a field: what it is when left out."""

        if not self.accept("="):
            return None
        token = self.current
        value = self.expression()
        constant = (
            isinstance(value, LiteralExpr)
            or (
                isinstance(value, UnaryExpr)
                and value.operator == "-"
                and isinstance(value.operand, LiteralExpr)
                and type(value.operand.value) in (int, float)
            )
            or (isinstance(value, ArrayExpr) and not value.items)
        )
        if not constant:
            raise ParseError(
                f"A default value at {token.line}:{token.column} must be written "
                "out: a number, text, true, false, null or []"
            )
        return value

    def parse_function(self, depth: int = 0, owner: str | None = None) -> FunctionDecl:
        if depth >= 32:
            raise ParseError("Function declaration nesting exceeds 32")
        self.expect("fn")
        name = self.identifier()
        self.expect("(")
        parameters: list[Field] = []
        if owner is not None:
            # A record's function always takes the record first, as `self`.
            name = method_function(owner, name)
            self.expect("self")
            parameters.append(Field("self", owner))
            if not self.check(")"):
                self.expect(",")
        while not self.accept(")"):
            parameter_name = self.identifier()
            self.expect(":")
            type_name = self.parse_type_name()
            default = self.parse_default()
            if default is not None and owner is not None:
                raise ParseError(
                    "A record's function cannot give a parameter a default value yet"
                )
            if default is not None and depth > 0:
                raise ParseError(
                    "A nested function cannot give a parameter a default value yet"
                )
            parameters.append(Field(parameter_name, type_name, default))
            if not self.check(")"):
                self.expect(",")
        self.expect("->")
        output_type = self.parse_type_name()
        recursion_limit = None
        if self.accept("recurse"):
            token = self.advance()
            if (
                token.kind != "NUMBER"
                or not token.value.isdigit()
                or not 1 <= int(token.value) <= 128
            ):
                raise ParseError("Recursion limit must be an integer from 1 to 128")
            recursion_limit = int(token.value)
        self.expect("{")
        body: list[LocalBinding | Assignment | Expression] = []
        # Declarations, assignments, and value-discarding expressions may be
        # interleaved, but `return` unambiguously ends the body, so the loop can
        # stop without needing a terminator token.
        while not (self.check("return") or self.check("}") or self.check("fn")):
            if self.at_statement():
                body.append(self.parse_statement())
            else:
                body.append(self.expression())
        bindings = tuple(item for item in body if isinstance(item, LocalBinding))
        statements = tuple(item for item in body if not isinstance(item, LocalBinding))
        # The body runs in the order it is written. That order is recorded only
        # when a declaration follows a statement, so a body that already lists
        # its declarations first parses to the same tree as it always has.
        order: list[tuple[str, int]] = []
        counts = {"binding": 0, "statement": 0}
        for item in body:
            kind = "binding" if isinstance(item, LocalBinding) else "statement"
            order.append((kind, counts[kind]))
            counts[kind] += 1
        if [kind for kind, _ in order] == sorted(kind for kind, _ in order):
            order = []
        nested: list[FunctionDecl] = []
        while self.check("fn"):
            nested.append(self.parse_function(depth + 1))
        self.expect("return")
        return_value = self.expression()
        self.expect("}")
        return FunctionDecl(
            name,
            tuple(parameters),
            output_type,
            return_value,
            bindings,
            tuple(nested),
            recursion_limit,
            statements,
            tuple(order),
        )

    def parse_arguments(
        self,
    ) -> tuple[tuple[str, Expression], ...]:
        arguments: list[tuple[str, Expression]] = []
        unnamed: list[tuple[Token, Expression]] = []
        if self.accept(")"):
            return ()
        while True:
            following = self.peek()
            if self.current.kind == "IDENT" and following.value == ":":
                name = self.identifier()
                self.expect(":")
                arguments.append((name, self.expression()))
            else:
                # Short form. Several unnamed arguments each take their name
                # from the expression: `f(order, question.text)` means
                # `f(order: order, text: question.text)`. A call's only
                # argument may be any expression; its empty name is filled in
                # from the callee's single parameter by `kosika.sugar`.
                start = self.current
                value = self.expression()
                unnamed.append((start, value))
                arguments.append(("", value))
            if self.accept(")"):
                break
            self.expect(",")
        if len(arguments) == 1 or len(unnamed) == len(arguments):
            # Entirely unnamed: `kosika.sugar` names them once the callee's
            # parameters are known, by matching names or else by position.
            return tuple(arguments)
        named: list[tuple[str, Expression]] = []
        for name, value in arguments:
            if name == "":
                inferred = inferred_name(value)
                if inferred is None:
                    start = next(token for token, item in unnamed if item is value)
                    raise ParseError(
                        f"Argument at {start.line}:{start.column} needs a name; "
                        "write name: value"
                    )
                name = inferred
            named.append((name, value))
        return tuple(named)

    def parse_short_step(self) -> Step:
        """Parse `name = call`, the one-line form of a step.

        It is exactly `step name { let name = call  return name }`: the step
        and its result share one name, and the result type is inferred.
        """

        name = self.identifier()
        self.expect("=")
        if self.accept("match"):
            # The step makes one of several calls, chosen by a value.
            subject = self.parse_or_expression()
            self.expect("{")
            branches: list[tuple[Any, Any]] = []
            while not self.accept("}"):
                self.expect("case")
                pattern: Any = None
                if not self.accept("_"):
                    token = self.advance()
                    if token.kind == "STRING":
                        pattern = token.value
                    elif token.kind == "NUMBER":
                        pattern = (
                            float(token.value)
                            if "." in token.value
                            else int(token.value)
                        )
                    elif token.kind == "IDENT" and token.value in ("true", "false"):
                        pattern = token.value == "true"
                    else:
                        raise ParseError(
                            f"Expected 'value' at {token.line}:{token.column}, "
                            f"got {token.value!r}"
                        )
                self.expect("{")
                branches.append((pattern, self.parse_step_call()))
                self.expect("}")
            if not branches:
                self.expect("case")
            return Step(
                name,
                branches[0][1],
                name,
                None,
                name,
                subject=subject,
                branches=tuple(branches),
            )
        call = self.parse_step_call()
        verify = self.parse_step_call() if self.accept("verify") else None
        votes = self.parse_step_count("vote")
        retries = self.parse_step_count("retry")
        escalate = self.identifier() if self.accept("escalate") else None
        fallback = self.parse_step_call() if self.accept("otherwise") else None
        return Step(
            name,
            call,
            name,
            None,
            name,
            fallback,
            retries,
            votes,
            verify=verify,
            escalate=escalate,
        )

    def parse_step_count(self, word: str) -> int:
        """Parse an optional `vote N` or `retry N` after a step's call."""

        if not self.accept(word):
            return 0
        token = self.advance()
        if token.kind != "NUMBER" or not token.value.isdigit():
            raise ParseError(
                f"Expected 'count' at {token.line}:{token.column}, got {token.value!r}"
            )
        return int(token.value)

    def parse_step(self) -> Step:
        name = self.identifier()
        self.expect("{")
        self.expect("let")
        binding_name = self.identifier()
        binding_type = None
        if self.accept(":"):
            binding_type = self.parse_type_name()
        self.expect("=")
        call = self.parse_step_call()
        verify = self.parse_step_call() if self.accept("verify") else None
        votes = self.parse_step_count("vote")
        retries = self.parse_step_count("retry")
        escalate = self.identifier() if self.accept("escalate") else None
        fallback = self.parse_step_call() if self.accept("otherwise") else None
        self.expect("return")
        return_name = self.identifier()
        self.expect("}")
        return Step(
            name,
            call,
            binding_name,
            binding_type,
            return_name,
            fallback,
            retries,
            votes,
            verify=verify,
            escalate=escalate,
        )

    def parse_think_call(self) -> ThinkCall:
        """Parse a thinking step: a verb, its subject, and its keyword parts."""

        verb = self.advance().value
        record: str | None = None
        extra: Expression | None = None
        if verb == "extract":
            record = self.identifier()
            self.expect("from")
        subject = self.expression()
        if verb == "classify":
            self.expect("into")
            extra = self.expression()
        elif verb == "decide":
            self.expect("given")
            extra = self.expression()
        evidence = False
        if self.accept("with"):
            self.expect("evidence")
            evidence = True
        model = self.identifier() if self.accept("using") else None
        more: list[str] = []
        while model is not None and self.accept(","):
            more.append(self.identifier())
        return ThinkCall(verb, subject, extra, record, model, evidence, tuple(more))

    def parse_step_call(
        self,
    ) -> ToolCall | ModelCall | AgentCall | WorkflowCall | FunctionCall:
        call: ToolCall | ModelCall | AgentCall | WorkflowCall | FunctionCall
        if self.check("{"):
            # A value where a call is expected: `otherwise {matches: []}`.
            # `kosika.sugar` turns it into a call of a function that
            # returns it.
            return FunctionCall(VALUE_CALL, (("value", self.expression()),))
        if (
            self.current.kind == "IDENT"
            and self.current.value in THINKING_VERBS
            and self.peek().value != "("
        ):
            # Replaced by a ModelCall in `lower_thinking` before anything reads it.
            return cast(ModelCall, self.parse_think_call())
        if self.check("remember") and self.peek().value != "(":
            # `remember note in customers under key`: an ordinary call of the
            # memory's generated tool; see `kosika.memory`.
            self.advance()
            value = self.expression()
            self.expect("in")
            memory_name = self.identifier()
            self.expect("under")
            return ToolCall(
                "memory",
                f"remember_{memory_name}",
                (("key", self.expression()), ("value", value)),
            )
        if self.check("post") and self.peek().value != "(":
            # `post remark to desk as critic`: an ordinary call of the
            # board's generated tool; see `kosika.board`.
            self.advance()
            body = self.expression()
            self.expect("to")
            board_name = self.identifier()
            self.expect("as")
            return ToolCall(
                "board",
                f"post_{board_name}",
                (("author", LiteralExpr(self.identifier())), ("body", body)),
            )
        if (
            self.check("read")
            and self.peek().kind == "IDENT"
            and self.peek().value not in ("(", "file")
        ):
            self.advance()
            return ToolCall("board", f"read_{self.identifier()}", ())
        # Files and pages in one line: see kosika.direct.
        if self.check("read") and self.peek().value == "file":
            self.advance()
            self.advance()
            return ToolCall("direct", "file_read", (("path", self.expression()),))
        if self.check("list") and self.peek().value == "files":
            self.advance()
            self.advance()
            return ToolCall("direct", "file_list", (("path", self.expression()),))
        if self.check("write") and self.peek().value == "file":
            self.advance()
            self.advance()
            target = self.expression()
            self.expect("with")
            return ToolCall(
                "direct", "file_write", (("path", target), ("text", self.expression()))
            )
        if self.check("fetch") and self.peek().value not in ("(", ".", "="):
            self.advance()
            return ToolCall("direct", "web_fetch", (("url", self.expression()),))
        # `today = now` and `roll = random 1 to 6`: see kosika.ambient.
        if self.check("now") and self.peek().value not in ("(", "."):
            self.advance()
            return ToolCall("world", "clock_now", ())
        if self.check("random") and self.peek().value not in ("(", ".", "="):
            self.advance()
            low = self.expression()
            self.expect("to")
            return ToolCall(
                "world", "random_number", (("low", low), ("high", self.expression()))
            )
        if self.check("recall") and self.peek().value != "(":
            self.advance()
            memory_name = self.identifier()
            self.expect("under")
            return ToolCall(
                "memory", f"recall_{memory_name}", (("key", self.expression()),)
            )
        if self.accept("call"):
            workflow_name = self.identifier()
            self.expect("(")
            call = WorkflowCall(workflow_name, self.parse_arguments())
        elif self.accept("ask"):
            agent_name = self.identifier()
            self.expect("(")
            call = AgentCall(agent_name, self.parse_arguments())
        elif self.accept("use_model"):
            model_name = self.identifier()
            self.expect("(")
            call = ModelCall(model_name, self.parse_arguments())
        elif self.current.kind == "IDENT" and self.current.value != "execute":
            function_name = self.identifier()
            self.expect("(")
            call = FunctionCall(function_name, self.parse_arguments())
        else:
            self.expect("execute")
            agent_name = self.identifier()
            self.expect(".")
            tool_name = self.identifier()
            self.expect("(")
            call = ToolCall(agent_name, tool_name, self.parse_arguments())
        return call


def parse(source: str, *, lower_thinking_steps: bool = True) -> Program:
    """Parse one source file.

    Thinking steps are lowered to model calls here unless the caller will
    lower the whole merged program itself, as the module loader does.
    """

    from .lexical import lower_nested_functions
    from .sugar import lower_sugar
    from .thinking import lower_thinking

    try:
        program = lower_nested_functions(Parser(lex(source)).parse())
        if not lower_thinking_steps:
            return program
        from .ambient import lower_ambient
        from .board import lower_board
        from .direct import lower_direct
        from .memory import lower_memory

        return lower_sugar(
            lower_thinking(
                lower_board(lower_memory(lower_direct(lower_ambient(program))))
            )
        )
    except ValueError as exc:
        raise ParseError(str(exc)) from exc
