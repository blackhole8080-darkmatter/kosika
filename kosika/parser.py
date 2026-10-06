from __future__ import annotations

from typing import cast

from .ast import (
    AgentCall,
    AgentDecl,
    ArrayExpr,
    Assignment,
    BinaryExpr,
    Block,
    BreakExpr,
    ConditionalExpr,
    ContinueExpr,
    Expression,
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
    MethodCallExpr,
    ModelCall,
    ModelDecl,
    ModelParameters,
    Program,
    RecordDecl,
    RecordExpr,
    RepeatExpr,
    ReturnExpr,
    Step,
    SwarmDecl,
    ThinkCall,
    ToolCall,
    ToolDecl,
    TryExpr,
    UnaryExpr,
    VariableExpr,
    WorkflowCall,
    WorkflowDecl,
)
from .lexer import Token, lex
from .sugar import inferred_name
from .thinking import GENERAL_INPUT, GENERAL_OUTPUT
from .thinking import VERBS as THINKING_VERBS


class ParseError(ValueError):
    pass


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

    def parse_statement(self) -> LocalBinding | Assignment:
        if self.check("let") or self.check("var"):
            return self.parse_binding()
        target = self.identifier()
        self.expect("=")
        return Assignment(target, self.expression())

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
            if self.check("let") or self.check("var") or self.at_assignment():
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
        if self.accept("try"):
            return self.parse_try_expression()
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
            where = self.expression() if self.accept("where") else None
            limit = 128
            if self.accept("limit"):
                token = self.advance()
                if (
                    token.kind != "NUMBER"
                    or not token.value.isdigit()
                    or not 1 <= int(token.value) <= 512
                ):
                    raise ParseError("for loop limit must be an integer from 1 to 512")
                limit = int(token.value)
            body = self.parse_block()
            return ForExpr(item_name, sequence, body, limit, where)
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
        token = self.advance()
        if (
            token.kind != "NUMBER"
            or not token.value.isdigit()
            or not 1 <= int(token.value) <= 512
        ):
            raise ParseError("repeat count must be an integer from 1 to 512")
        self.expect("times")
        body = self.parse_block()
        return RepeatExpr(LiteralExpr(int(token.value)), body)

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
        return TryExpr(body, retry)

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
                return RecordExpr(tuple(fields))
            self.expect(",")

    def parse(self) -> Program:
        program = Program()
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
            else:
                token = self.current
                raise ParseError(
                    f"Unexpected declaration {token.value!r} at {token.line}:{token.column}"
                )
        return program

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
        while not self.accept("}"):
            field_name = self.identifier()
            self.expect(":")
            type_name = self.parse_type_name()
            fields.append(Field(field_name, type_name))
            self.accept(",")
        return RecordDecl(name, tuple(fields))

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
        while not self.accept("}"):
            if self.accept("step"):
                steps.append(self.parse_step())
            elif self.at_assignment():
                steps.append(self.parse_short_step())
            elif self.accept("require"):
                self.expect("approval")
                approval_required = True
            else:
                token = self.current
                raise ParseError(
                    f"Unexpected workflow item {token.value!r} at {token.line}:{token.column}"
                )
        return WorkflowDecl(
            name, output_type, tuple(steps), approval_required, tuple(parameters)
        )

    def parse_function(self, depth: int = 0) -> FunctionDecl:
        if depth >= 32:
            raise ParseError("Function declaration nesting exceeds 32")
        self.expect("fn")
        name = self.identifier()
        self.expect("(")
        parameters: list[Field] = []
        while not self.accept(")"):
            parameter_name = self.identifier()
            self.expect(":")
            parameters.append(Field(parameter_name, self.parse_type_name()))
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
                or not 1 <= int(token.value) <= 32
            ):
                raise ParseError("Recursion limit must be an integer from 1 to 32")
            recursion_limit = int(token.value)
        self.expect("{")
        body: list[LocalBinding | Assignment | Expression] = []
        # Declarations, assignments, and value-discarding expressions may be
        # interleaved, but `return` unambiguously ends the body, so the loop can
        # stop without needing a terminator token.
        while not (self.check("return") or self.check("}") or self.check("fn")):
            if self.check("let") or self.check("var") or self.at_assignment():
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
        if len(arguments) == 1:
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
        call = self.parse_step_call()
        fallback = self.parse_step_call() if self.accept("otherwise") else None
        return Step(name, call, name, None, name, fallback)

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
        model = self.identifier() if self.accept("using") else None
        return ThinkCall(verb, subject, extra, record, model)

    def parse_step_call(
        self,
    ) -> ToolCall | ModelCall | AgentCall | WorkflowCall | FunctionCall:
        call: ToolCall | ModelCall | AgentCall | WorkflowCall | FunctionCall
        if (
            self.current.kind == "IDENT"
            and self.current.value in THINKING_VERBS
            and self.peek().value != "("
        ):
            # Replaced by a ModelCall in `lower_thinking` before anything reads it.
            return cast(ModelCall, self.parse_think_call())
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
        return lower_sugar(lower_thinking(program)) if lower_thinking_steps else program
    except ValueError as exc:
        raise ParseError(str(exc)) from exc
