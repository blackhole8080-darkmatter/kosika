package main

// Enums, as the reference has them: `enum Size { small, medium, large }`
// is a type whose value is one of those names, kept as text. `Size.small`
// is one of its values; a match on one that names every value needs no
// `case _`; a value from outside, a workflow's input or a model's answer,
// must be one of them.

type EnumDecl struct {
	Name   string
	Values []string
}

// enumValues are an enum's values, or nil when the type is no enum.
func (c *checker) enumValues(typeName string) []string {
	for _, enum := range c.program.Enums {
		if enum.Name == typeName {
			return enum.Values
		}
	}
	return nil
}

// enumOf is the enum `Size.small` names, or "".
func (c *checker) enumOf(e *FieldExpr) string {
	if variable, ok := e.Target.(*VarExpr); ok && c.enumValues(variable.Name) != nil {
		return variable.Name
	}
	return ""
}

func (c *checker) checkEnums() {
	seen := map[string]bool{}
	for _, enum := range c.program.Enums {
		if seen[enum.Name] {
			checkFail("Enum %s is declared more than once", pyRepr(enum.Name))
		}
		if c.records[enum.Name] != nil || c.choices[enum.Name] != nil || primitiveTypes[enum.Name] {
			checkFail("Enum %s has the name of a record or a type", pyRepr(enum.Name))
		}
		seen[enum.Name] = true
		if len(enum.Values) == 0 {
			checkFail("Enum %s needs at least one value", pyRepr(enum.Name))
		}
		unique := map[string]bool{}
		for _, value := range enum.Values {
			if unique[value] {
				checkFail("Enum %s names a value twice", pyRepr(enum.Name))
			}
			unique[value] = true
		}
	}
}
