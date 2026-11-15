package main

// Shapes, as the reference has them: `shape Priced { fn price(self) ->
// Number }` is a type that every record with those functions is, taking
// the same parameters and giving the same type. A shape's value is called
// by its functions; which record it is, is told from its fields when the
// program runs, as for any record's function.

import "strings"

type ShapeMethod struct {
	Name   string
	Params []Param
	Out    string
}

type ShapeDecl struct {
	Name    string
	Methods []ShapeMethod
}

// shapeRecords are which records each shape of the program being checked
// is, by name.
var shapeRecords = map[string]map[string]bool{}

func (p *Parser) parseShape() *ShapeDecl {
	p.expect("shape")
	shape := &ShapeDecl{Name: p.identifier()}
	p.expect("{")
	for !p.accept("}") {
		p.expect("fn")
		method := ShapeMethod{Name: p.identifier()}
		p.expect("(")
		p.expect("self")
		if !p.check(")") {
			p.expect(",")
		}
		method.Params = p.parseParams()
		p.expect("->")
		method.Out = p.parseTypeName(0)
		shape.Methods = append(shape.Methods, method)
	}
	return shape
}

func (c *checker) shape(name string) *ShapeDecl {
	for _, shape := range c.program.Shapes {
		if shape.Name == name {
			return shape
		}
	}
	return nil
}

// implements is whether a record is a shape.
func (c *checker) implements(recordName string, shape *ShapeDecl) bool {
	record := c.records[recordName]
	if record == nil {
		return false
	}
	for _, method := range shape.Methods {
		decl := c.functions[recordName+"$"+method.Name]
		if !hasName(record.Methods, method.Name) || decl == nil || decl.Out != method.Out || len(decl.Params) != len(method.Params)+1 {
			return false
		}
		for i, param := range method.Params {
			if decl.Params[i+1] != param {
				return false
			}
		}
	}
	return true
}

// shapeMembers are the records that are a shape, in the order declared.
func (c *checker) shapeMembers(shape *ShapeDecl) []string {
	var names []string
	for _, record := range c.program.Records {
		if c.implements(record.Name, shape) {
			names = append(names, record.Name)
		}
	}
	return names
}

func (c *checker) noteShapes() {
	shapeRecords = map[string]map[string]bool{}
	for _, shape := range c.program.Shapes {
		members := map[string]bool{}
		for _, name := range c.shapeMembers(shape) {
			members[name] = true
		}
		shapeRecords[shape.Name] = members
	}
}

func (c *checker) checkShapes() {
	seen := map[string]bool{}
	for _, shape := range c.program.Shapes {
		if seen[shape.Name] {
			checkFail("Shape %s is declared more than once", pyRepr(shape.Name))
		}
		if c.records[shape.Name] != nil || c.enumValues(shape.Name) != nil || c.choices[shape.Name] != nil || primitiveTypes[shape.Name] {
			checkFail("Shape %s has the name of a record, an enum or a type", pyRepr(shape.Name))
		}
		seen[shape.Name] = true
		if len(shape.Methods) == 0 {
			checkFail("Shape %s needs at least one function", pyRepr(shape.Name))
		}
		names := map[string]bool{}
		for _, method := range shape.Methods {
			if names[method.Name] {
				checkFail("Shape %s names a function twice", pyRepr(shape.Name))
			}
			names[method.Name] = true
		}
		for _, method := range shape.Methods {
			for _, param := range method.Params {
				c.validateType(param.Type)
			}
			c.validateType(method.Out)
		}
	}
	c.noteShapes()
}

// shapeCall is the type a call of a shape's function gives.
func (c *checker) shapeCall(shape *ShapeDecl, e *MethodExpr, env *Env, step string) string {
	var method *ShapeMethod
	var names []string
	for i := range shape.Methods {
		names = append(names, shape.Methods[i].Name)
		if shape.Methods[i].Name == e.Name {
			method = &shape.Methods[i]
		}
	}
	if method == nil {
		checkFail("Step %s shape %s has no function %s; it has: %s", pyRepr(step), pyRepr(shape.Name), pyRepr(e.Name), strings.Join(names, ", "))
	}
	rest := resolveUnnamed(e.Args, paramNames(&FuncDecl{Params: method.Params}))
	args := append([]Arg{{"self", e.Target}}, rest...)
	c.checkArguments(args, append([]Param{{"self", shape.Name}}, method.Params...), env, step)
	return method.Out
}
