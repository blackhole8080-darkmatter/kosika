from __future__ import annotations

from .ast import (
    AgentCall,
    AgentDecl,
    ArrayExpr,
    Expression,
    Field,
    FieldExpr,
    IndexExpr,
    LiteralExpr,
    ModelCall,
    ModelDecl,
    Program,
    RecordDecl,
    RecordExpr,
    Step,
    ToolCall,
    ToolDecl,
    VariableExpr,
    WorkflowCall,
    WorkflowDecl,
)
from .lexer import Token, lex


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
        return self.current.value == value

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

    def string_or_identifier(self) -> str:
        token = self.current
        if token.kind not in {"STRING", "IDENT"}:
            raise ParseError(
                f"Expected string or identifier at {token.line}:{token.column}"
            )
        self.advance()
        return token.value

    def expression(self) -> Expression:
        if self.check("["):
            value: Expression = self.parse_array_expression()
        elif self.check("{"):
            value = self.parse_record_expression()
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
                else:
                    value = VariableExpr(token.value)
            else:
                raise ParseError(
                    f"Expected expression at {token.line}:{token.column}, "
                    f"got {token.value!r}"
                )
        while True:
            if self.accept("."):
                value = FieldExpr(value, self.identifier())
            elif self.accept("["):
                index = self.expression()
                self.expect("]")
                value = IndexExpr(value, index)
            else:
                return value

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
            name = self.identifier()
            self.expect(":")
            fields.append((name, self.expression()))
            if self.accept("}"):
                return RecordExpr(tuple(fields))
            self.expect(",")

    def parse(self) -> Program:
        program = Program()
        while self.current.kind != "EOF":
            keyword = self.current.value
            if keyword == "import":
                self.advance()
                if self.current.kind != "STRING":
                    raise ParseError("Import requires a quoted local .syn path")
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
            elif keyword == "workflow":
                program.workflows.append(self.parse_workflow())
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
        self.expect("{")
        values: dict[str, str] = {}
        while not self.accept("}"):
            key = self.identifier()
            if key not in {"input", "output"} or key in values:
                raise ParseError(f"Invalid or duplicate model field {key!r}")
            self.expect(":")
            values[key] = self.identifier()
        if set(values) != {"input", "output"}:
            raise ParseError(f"Model {name!r} requires input and output records")
        return ModelDecl(name, values["input"], values["output"])

    def parse_type_name(self) -> str:
        name = self.identifier()
        if self.accept("["):
            self.expect("]")
            return f"{name}[]"
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
            elif key == "max_steps":
                token = self.advance()
                if token.kind != "NUMBER" or not token.value.isdigit():
                    raise ParseError("Agent max_steps must be a positive integer")
                max_steps = int(token.value)
            else:
                raise ParseError(f"Unknown agent field {key!r}")
        return AgentDecl(name, can, cannot, model, max_steps)

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

    def parse_arguments(
        self,
    ) -> tuple[tuple[str, Expression], ...]:
        arguments: list[tuple[str, Expression]] = []
        if self.accept(")"):
            return ()
        while True:
            name = self.identifier()
            self.expect(":")
            arguments.append((name, self.expression()))
            if self.accept(")"):
                break
            self.expect(",")
        return tuple(arguments)

    def parse_step(self) -> Step:
        name = self.identifier()
        self.expect("{")
        self.expect("let")
        binding_name = self.identifier()
        binding_type = None
        if self.accept(":"):
            binding_type = self.identifier()
        self.expect("=")
        call: ToolCall | ModelCall | AgentCall | WorkflowCall
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
        else:
            self.expect("execute")
            agent_name = self.identifier()
            self.expect(".")
            tool_name = self.identifier()
            self.expect("(")
            call = ToolCall(agent_name, tool_name, self.parse_arguments())
        self.expect("return")
        return_name = self.identifier()
        self.expect("}")
        return Step(
            name,
            call,
            binding_name,
            binding_type,
            return_name,
        )


def parse(source: str) -> Program:
    return Parser(lex(source)).parse()
