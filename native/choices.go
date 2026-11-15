package main

// Choices are tagged unions: `choice Filing { filed(invoice: Invoice)
// pending }` is a type whose value is exactly one of its alternatives.
// An alternative with fields is made by calling it, `filed(invoice)`;
// one without is written as its own name, `pending`. A match names every
// alternative so the checker knows the value was covered.
//
// The checks here follow the reference's `_check_choices` rule for rule,
// message for message.

func (c *checker) checkChoices() {
	taken := map[string]bool{}
	for _, record := range c.program.Records {
		taken[record.Name] = true
	}
	for _, enum := range c.program.Enums {
		taken[enum.Name] = true
	}
	for _, shape := range c.program.Shapes {
		taken[shape.Name] = true
	}
	seen := map[string]bool{}
	choiceNames := map[string]bool{}
	for _, choice := range c.program.Choices {
		choiceNames[choice.Name] = true
	}
	constructors := map[string]string{}
	for _, choice := range c.program.Choices {
		if seen[choice.Name] {
			checkFail("Choice %s is declared more than once", pyRepr(choice.Name))
		}
		if taken[choice.Name] || primitiveTypes[choice.Name] {
			checkFail("Choice %s has the name of a record, enum, shape or type", pyRepr(choice.Name))
		}
		seen[choice.Name] = true
		if len(choice.Alternatives) == 0 {
			checkFail("Choice %s needs at least one alternative", pyRepr(choice.Name))
		}
		names := map[string]bool{}
		for _, alternative := range choice.Alternatives {
			if names[alternative.Name] {
				checkFail("Choice %s names an alternative twice", pyRepr(choice.Name))
			}
			names[alternative.Name] = true
		}
		for _, alternative := range choice.Alternatives {
			if alternative.Name == "true" || alternative.Name == "false" || alternative.Name == "null" || alternative.Name == "_" {
				checkFail("Choice %s cannot name an alternative %s", pyRepr(choice.Name), pyRepr(alternative.Name))
			}
			if owner, exists := constructors[alternative.Name]; exists {
				checkFail("Choice alternatives %s and %s both name %s; constructors are written unqualified, so their names must be unique", pyRepr(owner), pyRepr(choice.Name), pyRepr(alternative.Name))
			}
			if taken[alternative.Name] || c.functions[alternative.Name] != nil || choiceNames[alternative.Name] {
				checkFail("Choice alternative %s has the name of a record, enum, shape, function or type", pyRepr(alternative.Name))
			}
			constructors[alternative.Name] = choice.Name
			fields := map[string]bool{}
			for _, field := range alternative.Fields {
				if fields[field.Name] {
					checkFail("Choice alternative %s names a field twice", pyRepr(alternative.Name))
				}
				fields[field.Name] = true
			}
			for _, field := range alternative.Fields {
				c.validateType(field.Type)
			}
		}
	}
}
