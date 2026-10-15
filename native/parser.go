package main

// The parser: tokens in, a Program out. It follows the reference parser
// rule for rule, so that the same text means the same thing in both
// engines. What it does not have yet (swarms) is refused by name, never
// guessed at.

import (
	"fmt"
	"math/big"
	"strconv"
	"strings"
)

type ParseError struct{ Message string }

func (e *ParseError) Error() string { return e.Message }

func parseFail(format string, args ...any) {
	panic(&ParseError{fmt.Sprintf(format, args...)})
}

var compound = []string{"+=", "-=", "*=", "/="}

const whilePasses = 1_000_000_000

type Parser struct {
	tokens        []Token
	index         int
	recordMethods []*FuncDecl
}

func (p *Parser) current() Token { return p.tokens[p.index] }

func (p *Parser) advance() Token {
	token := p.current()
	p.index++
	return token
}

func (p *Parser) peek() Token {
	if p.index+1 < len(p.tokens) {
		return p.tokens[p.index+1]
	}
	return p.tokens[len(p.tokens)-1]
}

func (p *Parser) check(value string) bool {
	token := p.current()
	switch token.Kind {
	case "IDENT", "SYMBOL", "OP", "ARROW":
		return token.Value == value
	}
	return false
}

func (p *Parser) accept(value string) bool {
	if p.check(value) {
		p.advance()
		return true
	}
	return false
}

// quoted writes a text the way the reference writes one in a message.
func quoted(text string) string {
	if !strings.Contains(text, "'") {
		return "'" + text + "'"
	}
	return strconv.Quote(text)
}

func (p *Parser) expect(value string) Token {
	if !p.check(value) {
		token := p.current()
		parseFail("Expected %s at %d:%d, got %s", quoted(value), token.Line, token.Column, quoted(token.Value))
	}
	return p.advance()
}

func (p *Parser) identifier() string {
	token := p.current()
	if token.Kind != "IDENT" {
		parseFail("Expected identifier at %d:%d", token.Line, token.Column)
	}
	p.advance()
	return token.Value
}

func isCompound(value string) bool {
	for _, operator := range compound {
		if operator == value {
			return true
		}
	}
	return false
}

func (p *Parser) atAssignment() bool {
	if p.current().Kind != "IDENT" || p.index+1 >= len(p.tokens) {
		return false
	}
	following := p.tokens[p.index+1]
	return following.Value == "=" && (following.Kind == "SYMBOL" || following.Kind == "OP")
}

func (p *Parser) atUpdate() bool {
	if p.current().Kind != "IDENT" || p.index+1 >= len(p.tokens) {
		return false
	}
	position := p.index + 1
	if p.tokens[position].Value == "[" {
		depth := 0
		for position < len(p.tokens) {
			token := p.tokens[position]
			if token.Kind == "SYMBOL" && token.Value == "[" {
				depth++
			} else if token.Kind == "SYMBOL" && token.Value == "]" {
				depth--
				if depth == 0 {
					break
				}
			} else if token.Kind == "EOF" || (token.Kind == "SYMBOL" && (token.Value == "{" || token.Value == "}")) {
				return false
			}
			position++
		}
		position++
		if position >= len(p.tokens) {
			return false
		}
		following := p.tokens[position]
		return (following.Kind == "SYMBOL" || following.Kind == "OP") && (following.Value == "=" || isCompound(following.Value))
	}
	following := p.tokens[position]
	return following.Kind == "OP" && isCompound(following.Value)
}

func (p *Parser) atStatement() bool {
	return p.check("let") || p.check("var") || p.atAssignment() || p.atUpdate()
}

func (p *Parser) parseBinding() *Binding {
	mutable := p.accept("var")
	if !mutable {
		p.expect("let")
	}
	name := p.identifier()
	typeName := ""
	if p.accept(":") {
		typeName = p.parseTypeName(0)
	}
	p.expect("=")
	return &Binding{name, typeName, p.expression(), mutable}
}

func (p *Parser) parseStatement() Expr {
	if p.check("let") || p.check("var") {
		return p.parseBinding()
	}
	target := p.identifier()
	var index Expr
	if p.accept("[") {
		index = p.expression()
		p.expect("]")
	}
	operator := p.advance()
	if operator.Value != "=" && !isCompound(operator.Value) {
		parseFail("Expected '=' at %d:%d, got %s", operator.Line, operator.Column, quoted(operator.Value))
	}
	value := p.expression()
	var current Expr = &VarExpr{target}
	if index != nil {
		current = &IndexExpr{current, index}
	}
	if operator.Value != "=" {
		value = &BinaryExpr{current, operator.Value[:1], value}
	}
	if index != nil {
		value = &MethodExpr{&VarExpr{target}, "set", []Arg{{"key", index}, {"value", value}}, operator.Line}
	}
	return &Assign{target, value}
}

func (p *Parser) parseBlock() Expr {
	p.expect("{")
	var statements []Expr
	var result Expr
	for !p.check("}") {
		if result != nil {
			statements = append(statements, result)
			result = nil
		}
		if p.atStatement() {
			statements = append(statements, p.parseStatement())
		} else {
			result = p.expression()
		}
	}
	p.expect("}")
	if len(statements) == 0 {
		if result == nil {
			parseFail("Empty block")
		}
		return result
	}
	return &BlockExpr{statements, result}
}

func isDigits(text string) bool {
	if text == "" {
		return false
	}
	for _, r := range text {
		if !isDigit(r) {
			return false
		}
	}
	return true
}

// boundedCount reads a whole number in a range, or gives -1.
func boundedCount(token Token, low, high int) int {
	if token.Kind != "NUMBER" || !isDigits(token.Value) {
		return -1
	}
	n, err := strconv.Atoi(token.Value)
	if err != nil || n < low || n > high {
		return -1
	}
	return n
}

func numberLiteral(text string, negative bool) Value {
	if strings.Contains(text, ".") {
		f, _ := strconv.ParseFloat(text, 64)
		if negative {
			f = -f
		}
		return f
	}
	n, _ := new(big.Int).SetString(text, 10)
	if negative {
		n.Neg(n)
	}
	return n
}

func (p *Parser) expression() Expr {
	if p.accept("break") {
		if p.current().Kind == "NUMBER" {
			token := p.advance()
			p.expect(":")
			parseFail("Labelled break is not supported at %d:%d", token.Line, token.Column)
		}
		return &BreakExpr{}
	}
	if p.accept("continue") {
		return &ContinueExpr{}
	}
	if p.accept("return") {
		return &ReturnExpr{p.expression()}
	}
	if p.accept("repeat") {
		return p.parseRepeat()
	}
	if p.accept("while") {
		condition := p.expression()
		body := p.parseBlock()
		leave := &CondExpr{&UnaryExpr{"not", condition}, &BreakExpr{}, &Literal{nil}}
		return &RepeatExpr{&Literal{big.NewInt(whilePasses)}, &BlockExpr{[]Expr{leave}, body}}
	}
	if p.accept("try") {
		return p.parseTry()
	}
	if p.check("fail") && (p.peek().Kind == "STRING" || (p.peek().Kind == "IDENT" && p.peek().Value == "f")) {
		p.advance()
		return &FailExpr{p.expression()}
	}
	if p.accept("match") {
		subject := p.expression()
		p.expect("{")
		var arms []MatchArm
		for !p.accept("}") {
			p.expect("case")
			arm := MatchArm{}
			if p.accept("_") {
				arm.Any = true
			} else {
				negative := p.accept("-")
				token := p.current()
				switch {
				case token.Kind == "STRING" && !negative:
					arm.Pattern = p.advance().Value
				case token.Kind == "NUMBER":
					p.advance()
					arm.Pattern = numberLiteral(token.Value, negative)
				case token.Kind == "IDENT" && !negative && (token.Value == "true" || token.Value == "false" || token.Value == "null"):
					p.advance()
					arm.Pattern = wordLiteral(token.Value)
				default:
					parseFail("Match patterns must be scalar literals or _")
				}
			}
			p.expect("{")
			arm.Value = p.expression()
			p.expect("}")
			arms = append(arms, arm)
		}
		return &MatchExpr{subject, arms}
	}
	if p.accept("if") {
		condition := p.expression()
		whenTrue := p.parseBlock()
		if !p.accept("else") {
			return &CondExpr{condition, whenTrue, &Literal{nil}}
		}
		var whenFalse Expr
		if p.check("if") {
			whenFalse = p.expression()
		} else {
			whenFalse = p.parseBlock()
		}
		return &CondExpr{condition, whenTrue, whenFalse}
	}
	if p.accept("for") {
		item := p.identifier()
		p.expect("in")
		sequence := p.expression()
		for _, word := range []string{"to", "until"} {
			if p.accept(word) {
				sequence = &MethodExpr{sequence, word, []Arg{{"end", p.expression()}}, p.current().Line}
				break
			}
		}
		var where Expr
		if p.accept("where") {
			where = p.expression()
		}
		limit, capped := 128, false
		if p.accept("limit") {
			limit = boundedCount(p.advance(), 1, 1_000_000)
			if limit < 0 {
				parseFail("for loop limit must be an integer from 1 to 1000000")
			}
			capped = true
		}
		return &ForExpr{item, sequence, p.parseBlock(), limit, where, capped}
	}
	return p.parseOr()
}

func wordLiteral(word string) Value {
	switch word {
	case "true":
		return true
	case "false":
		return false
	}
	return nil
}

func (p *Parser) parseOr() Expr {
	value := p.parseAnd()
	for p.accept("or") {
		value = &BinaryExpr{value, "or", p.parseAnd()}
	}
	return value
}

func (p *Parser) parseAnd() Expr {
	value := p.parseEquality()
	for p.accept("and") {
		value = &BinaryExpr{value, "and", p.parseEquality()}
	}
	return value
}

func (p *Parser) binaryLevel(operators []string, next func() Expr) Expr {
	value := next()
	for {
		found := false
		for _, operator := range operators {
			if p.check(operator) {
				p.advance()
				value = &BinaryExpr{value, operator, next()}
				found = true
				break
			}
		}
		if !found {
			return value
		}
	}
}

func (p *Parser) parseEquality() Expr {
	return p.binaryLevel([]string{"==", "!="}, p.parseComparison)
}

func (p *Parser) parseComparison() Expr {
	return p.binaryLevel([]string{"<", "<=", ">", ">="}, p.parseTerm)
}

func (p *Parser) parseTerm() Expr {
	return p.binaryLevel([]string{"+", "-"}, p.parseFactor)
}

func (p *Parser) parseFactor() Expr {
	return p.binaryLevel([]string{"*", "/", "%"}, p.parseUnary)
}

func (p *Parser) parseUnary() Expr {
	if p.check("-") || p.check("not") {
		operator := p.advance().Value
		return &UnaryExpr{operator, p.parseUnary()}
	}
	return p.parsePostfix()
}

func (p *Parser) parsePostfix() Expr {
	value := p.parsePrimary()
	for {
		if p.accept(".") {
			if p.current().Kind == "IDENT" && p.peek().Kind == "SYMBOL" && p.peek().Value == "(" {
				method := p.advance()
				p.expect("(")
				value = &MethodExpr{value, method.Value, p.parseArguments(), method.Line}
			} else {
				value = &FieldExpr{value, p.identifier()}
			}
		} else if p.accept("[") {
			index := p.expression()
			p.expect("]")
			value = &IndexExpr{value, index}
		} else {
			return value
		}
	}
}

func (p *Parser) parsePrimary() Expr {
	if p.accept("ref") {
		return &FuncRefExpr{p.identifier()}
	}
	if p.accept("fn") {
		return p.parseFunctionExpression()
	}
	if p.check("[") {
		return p.parseArray()
	}
	if p.check("{") {
		return p.parseRecordExpression()
	}
	if p.accept("(") {
		value := p.expression()
		p.expect(")")
		return value
	}
	token := p.current()
	switch token.Kind {
	case "STRING":
		p.advance()
		return &Literal{token.Value}
	case "NUMBER":
		p.advance()
		return &Literal{numberLiteral(token.Value, false)}
	case "IDENT":
		p.advance()
		if token.Value == "true" || token.Value == "false" || token.Value == "null" {
			return &Literal{wordLiteral(token.Value)}
		}
		following := p.current()
		if token.Value == "f" && following.Kind == "STRING" && following.Line == token.Line && following.Column == token.Column+1 {
			return p.parseTemplate(p.advance())
		}
		if p.accept("(") {
			return &CallExpr{token.Value, p.parseArguments(), token.Line}
		}
		return &VarExpr{token.Value}
	}
	parseFail("Expected expression at %d:%d, got %s", token.Line, token.Column, quoted(token.Value))
	return nil
}

// parseTemplate turns f"Total: {n}" into the joining it means.
func (p *Parser) parseTemplate(token Token) Expr {
	text := []rune(token.Value)
	var parts []Expr
	var literal strings.Builder
	flush := func() {
		if literal.Len() > 0 {
			parts = append(parts, &Literal{literal.String()})
			literal.Reset()
		}
	}
	index := 0
	for index < len(text) {
		char := text[index]
		switch {
		case (char == '{' || char == '}') && index+1 < len(text) && text[index+1] == char:
			literal.WriteRune(char)
			index += 2
		case char == '{':
			end := -1
			for at := index; at < len(text); at++ {
				if text[at] == '}' {
					end = at
					break
				}
			}
			if end < 0 {
				parseFail("Expected '}' at %d:%d, got 'the end of the text'", token.Line, token.Column)
			}
			inner := strings.TrimSpace(string(text[index+1 : end]))
			if inner == "" {
				parseFail("Expected expression at %d:%d, got '}'", token.Line, token.Column)
			}
			flush()
			tokens, err := Lex(inner)
			if err != nil {
				panic(err)
			}
			sub := &Parser{tokens: tokens}
			parts = append(parts, sub.expression())
			if sub.current().Kind != "EOF" {
				parseFail("Expected '}' at %d:%d, got %s", token.Line, token.Column, quoted(sub.current().Value))
			}
			index = end + 1
		case char == '}':
			parseFail("Expected '{' at %d:%d, got '}'", token.Line, token.Column)
		default:
			literal.WriteRune(char)
			index++
		}
	}
	flush()
	var joined Expr = &Literal{""}
	if len(parts) > 0 {
		if first, ok := parts[0].(*Literal); ok {
			joined, parts = first, parts[1:]
		}
	}
	for _, part := range parts {
		joined = &BinaryExpr{joined, "+", part}
	}
	return joined
}

func (p *Parser) parseParams() []Param {
	var params []Param
	for !p.accept(")") {
		name := p.identifier()
		p.expect(":")
		params = append(params, Param{name, p.parseTypeName(0)})
		if !p.check(")") {
			p.expect(",")
		}
	}
	return params
}

func (p *Parser) parseFunctionExpression() Expr {
	p.expect("(")
	params := p.parseParams()
	out := ""
	if p.accept("->") {
		out = p.parseTypeName(0)
	}
	p.expect("{")
	body := p.expression()
	p.expect("}")
	return &FuncExpr{params, out, body}
}

func (p *Parser) parseRepeat() Expr {
	token := p.current()
	var count Expr
	if token.Kind == "NUMBER" && p.peek().Value == "times" {
		p.advance()
		n := boundedCount(token, 1, 1_000_000)
		if n < 0 {
			parseFail("repeat count must be an integer from 1 to 1000000")
		}
		count = &Literal{big.NewInt(int64(n))}
	} else {
		count = p.parseOr()
	}
	p.expect("times")
	return &RepeatExpr{count, p.parseBlock()}
}

func (p *Parser) parseTry() Expr {
	body := p.parseBlock()
	retry := 0
	if p.accept("retry") {
		retry = boundedCount(p.advance(), 1, 32)
		if retry < 0 {
			parseFail("try retry count must be an integer from 1 to 32")
		}
	}
	if !p.accept("otherwise") {
		return &TryExpr{Body: body, Retry: retry}
	}
	name := ""
	if !p.check("{") {
		name = p.identifier()
	}
	return &TryExpr{body, retry, p.parseBlock(), name}
}

func (p *Parser) parseArray() Expr {
	p.expect("[")
	items := []Expr{}
	if p.accept("]") {
		return &ArrayExpr{items}
	}
	for {
		items = append(items, p.expression())
		if p.accept("]") {
			return &ArrayExpr{items}
		}
		p.expect(",")
	}
}

func (p *Parser) parseRecordExpression() Expr {
	p.expect("{")
	record := &RecordExpr{}
	if p.accept("}") {
		return record
	}
	if p.check(".") && p.peek().Value == "." {
		p.advance()
		p.advance()
		record.Base = p.expression()
		if p.accept("}") {
			return record
		}
		p.expect(",")
	}
	for {
		var name string
		if p.current().Kind == "STRING" {
			name = p.advance().Value
		} else {
			name = p.identifier()
		}
		p.expect(":")
		record.Fields = append(record.Fields, RecordField{name, p.expression()})
		if p.accept("}") {
			return record
		}
		p.expect(",")
	}
}

func inferredName(value Expr) string {
	switch v := value.(type) {
	case *VarExpr:
		return v.Name
	case *FieldExpr:
		return v.Name
	}
	return ""
}

func (p *Parser) parseArguments() []Arg {
	var arguments []Arg
	var starts []Token
	unnamed := 0
	if p.accept(")") {
		return arguments
	}
	for {
		if p.current().Kind == "IDENT" && p.peek().Value == ":" {
			name := p.identifier()
			p.expect(":")
			arguments = append(arguments, Arg{name, p.expression()})
			starts = append(starts, Token{})
		} else {
			start := p.current()
			arguments = append(arguments, Arg{"", p.expression()})
			starts = append(starts, start)
			unnamed++
		}
		if p.accept(")") {
			break
		}
		p.expect(",")
	}
	if len(arguments) == 1 || unnamed == len(arguments) {
		return arguments
	}
	for i := range arguments {
		if arguments[i].Name == "" {
			name := inferredName(arguments[i].Value)
			if name == "" {
				parseFail("Argument at %d:%d needs a name; write name: value", starts[i].Line, starts[i].Column)
			}
			arguments[i].Name = name
		}
	}
	return arguments
}

func (p *Parser) atMapSuffix() bool {
	if !p.check("{") || p.index == 0 {
		return false
	}
	previous, opening, closing := p.tokens[p.index-1], p.current(), p.peek()
	return closing.Value == "}" &&
		previous.Line == opening.Line && opening.Line == closing.Line &&
		previous.Column+len([]rune(previous.Value)) == opening.Column &&
		opening.Column+1 == closing.Column
}

func (p *Parser) parseTypeName(depth int) string {
	if depth >= 32 {
		parseFail("Function type nesting exceeds 32")
	}
	if p.accept("fn") {
		p.expect("(")
		var parts []string
		for !p.accept(")") {
			name := p.identifier()
			p.expect(":")
			parts = append(parts, name+":"+p.parseTypeName(depth+1))
			if !p.check(")") {
				p.expect(",")
			}
		}
		p.expect("->")
		// Written as the reference writes it, so that a checker message names
		// the type in the same words: fn(value:Number)->Number.
		return "fn(" + strings.Join(parts, ",") + ")->" + p.parseTypeName(depth+1)
	}
	name := p.identifier()
	for {
		switch {
		case p.check("[") && p.peek().Value == "]":
			p.advance()
			p.advance()
			name += "[]"
		case p.atMapSuffix():
			p.advance()
			p.advance()
			name += "{}"
		case p.check("?"):
			if strings.HasSuffix(name, "?") {
				token := p.current()
				parseFail("A type is optional once, not twice, at %d:%d", token.Line, token.Column)
			}
			p.advance()
			name += "?"
		default:
			return name
		}
	}
}

func (p *Parser) parseRecord() *RecordDecl {
	p.expect("record")
	record := &RecordDecl{Name: p.identifier()}
	p.expect("{")
	for !p.accept("}") {
		if p.check("fn") {
			method := p.parseFunction(0, record.Name)
			record.Methods = append(record.Methods, strings.SplitN(method.Name, "$", 2)[1])
			p.recordMethods = append(p.recordMethods, method)
			continue
		}
		name := p.identifier()
		p.expect(":")
		record.Fields = append(record.Fields, Param{name, p.parseTypeName(0)})
		if given := p.parseDefault(); given != nil {
			if record.Defaults == nil {
				record.Defaults = map[string]Expr{}
			}
			record.Defaults[name] = given
		}
		p.accept(",")
	}
	return record
}

func (p *Parser) parseFunction(depth int, owner string) *FuncDecl {
	if depth >= 32 {
		parseFail("Function declaration nesting exceeds 32")
	}
	line := p.expect("fn").Line
	function := &FuncDecl{Name: p.identifier(), Line: line}
	p.expect("(")
	if owner != "" {
		function.Name = owner + "$" + function.Name
		p.expect("self")
		function.Params = append(function.Params, Param{"self", owner})
		if !p.check(")") {
			p.expect(",")
		}
	}
	p.parseFunctionParams(function, depth, owner)
	p.expect("->")
	function.Out = p.parseTypeName(0)
	if p.accept("recurse") {
		function.Recurse = boundedCount(p.advance(), 1, 128)
		if function.Recurse < 0 {
			parseFail("Recursion limit must be an integer from 1 to 128")
		}
	}
	p.expect("{")
	for !(p.check("return") || p.check("}") || p.check("fn")) {
		if p.atStatement() {
			function.Body = append(function.Body, p.parseStatement())
		} else {
			function.Body = append(function.Body, p.expression())
		}
	}
	for p.check("fn") {
		function.Nested = append(function.Nested, p.parseFunction(depth+1, ""))
	}
	p.expect("return")
	function.Return = p.expression()
	p.expect("}")
	return function
}

// written gives the tokens from start to end roughly as they were typed.
func (p *Parser) written(start, end int) string {
	var out strings.Builder
	line, column := -1, -1
	for i, token := range p.tokens[start:end] {
		raw := token.Value
		if token.Kind == "STRING" {
			raw = pyJSONString(token.Value)
		}
		if i > 0 && (token.Line != line || token.Column > column) {
			out.WriteString(" ")
		}
		out.WriteString(raw)
		line, column = token.Line, token.Column+len([]rune(raw))
	}
	return out.String()
}

func (p *Parser) parseTest() *TestDecl {
	opening := p.advance()
	test := &TestDecl{Line: opening.Line, Live: opening.Value == "eval"}
	test.Name = p.advance().Value
	p.expect("{")
	for !p.accept("}") {
		token := p.current()
		if p.atAssignment() || (token.Kind == "IDENT" && p.peek().Value == ":") {
			step := TestStep{Target: p.identifier(), Line: token.Line}
			if p.accept(":") {
				step.Type = p.parseTypeName(0)
			}
			p.expect("=")
			step.Value = p.expression()
			test.Steps = append(test.Steps, step)
		} else if p.accept("answers") {
			name := p.identifier()
			p.expect(":")
			test.Answers = append(test.Answers, Answer{name, p.expression()})
		} else {
			p.expect("expect")
			start := p.index
			value := p.expression()
			test.Steps = append(test.Steps, TestStep{Expect: true, Value: value, Line: token.Line, Text: p.written(start, p.index)})
		}
	}
	return test
}

// notYet are the declarations this engine does not read yet.
var notYet = map[string]bool{
	"swarm": true, "default": true,
}

func (p *Parser) parseProgram() *Program {
	program := &Program{}
	for p.current().Kind != "EOF" {
		token := p.current()
		keyword := ""
		if token.Kind == "IDENT" {
			keyword = token.Value
		}
		switch {
		case keyword == "record":
			program.Records = append(program.Records, p.parseRecord())
		case keyword == "fn":
			program.Functions = append(program.Functions, p.parseFunction(0, ""))
		case keyword == "model":
			program.Models = append(program.Models, p.parseModel())
		case keyword == "default" && p.peek().Value == "model":
			p.advance()
			model := p.parseModel()
			model.Default = true
			program.Models = append(program.Models, model)
		case keyword == "enum" && p.peek().Kind == "IDENT":
			p.advance()
			enum := &EnumDecl{Name: p.identifier()}
			p.expect("{")
			for !p.accept("}") {
				enum.Values = append(enum.Values, p.identifier())
				p.accept(",")
			}
			program.Enums = append(program.Enums, enum)
		case (keyword == "memory" || keyword == "board") && p.peek().Kind == "IDENT":
			p.advance()
			store := &StoreDecl{Name: p.identifier()}
			p.expect("of")
			store.Record = p.identifier()
			if keyword == "memory" {
				program.Memories = append(program.Memories, store)
			} else {
				program.Boards = append(program.Boards, store)
			}
		case keyword == "import":
			p.advance()
			if p.current().Kind != "STRING" {
				parseFail("Import requires a quoted local .kos path")
			}
			program.Imports = append(program.Imports, p.advance().Value)
		case keyword == "export":
			p.advance()
			p.expect("[")
			for !p.accept("]") {
				program.Exports = append(program.Exports, p.identifier())
				p.accept(",")
			}
		case keyword == "tool":
			program.Tools = append(program.Tools, p.parseTool())
		case keyword == "agent":
			program.Agents = append(program.Agents, p.parseAgent())
		case keyword == "workflow":
			program.Workflows = append(program.Workflows, p.parseWorkflow())
		case (keyword == "test" || keyword == "eval") && p.peek().Kind == "STRING":
			program.Tests = append(program.Tests, p.parseTest())
		case notYet[keyword]:
			article := "a"
			if keyword == "import" || keyword == "export" {
				article = "an"
			}
			parseFail("kosika-native cannot read %s %s yet (at %d:%d); it has models, records, functions, tools, agents, workflows, memories, boards, imports and tests", article, keyword, token.Line, token.Column)
		default:
			parseFail("Unexpected declaration %s at %d:%d", quoted(token.Value), token.Line, token.Column)
		}
	}
	program.Functions = append(program.Functions, p.recordMethods...)
	return program
}

// Parse reads a program's text. The error is a *LexError or a *ParseError.
func Parse(source string) (program *Program, err error) {
	tokens, lexErr := Lex(source)
	if lexErr != nil {
		return nil, lexErr
	}
	defer func() {
		if caught := recover(); caught != nil {
			switch e := caught.(type) {
			case *ParseError:
				err = e
			case *LexError:
				err = e
			default:
				panic(caught)
			}
		}
	}()
	parser := &Parser{tokens: tokens}
	return parser.parseProgram(), nil
}
