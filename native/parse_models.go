package main

// Reading a model declaration, as kosika/parser.py reads one. A model with
// no records is a general one, which takes and gives text in records the
// language supplies (see thinking.go).

import "strconv"

func (p *Parser) parseModel() *ModelDecl {
	p.expect("model")
	model := &ModelDecl{Name: p.identifier()}
	if !p.check("{") {
		// `model assistant`: a general model, which takes and gives text.
		model.Input, model.Output = generalInput, generalOutput
		return model
	}
	p.expect("{")
	values := map[string]string{}
	seen := map[string]bool{}
	for !p.accept("}") {
		key := p.identifier()
		if seen[key] {
			parseFail("Invalid or duplicate model field %s", pyRepr(key))
		}
		seen[key] = true
		if key == "parameters" {
			p.expect(":")
			p.parseModelParameters(model)
			continue
		}
		if key != "input" && key != "output" && key != "deployment" {
			parseFail("Invalid model field %s", pyRepr(key))
		}
		p.expect(":")
		if key == "deployment" {
			deployment := p.stringOrIdentifier()
			model.Deployment = &deployment
		} else {
			values[key] = p.identifier()
			if key == "output" && p.check("as") && p.peek().Value == "text" {
				p.advance()
				p.advance()
				model.Plain = true
			}
		}
	}
	if len(values) == 0 {
		// Only a deployment or parameters: still a general model.
		values = map[string]string{"input": generalInput, "output": generalOutput}
	}
	if len(values) != 2 {
		parseFail("Model %s requires input and output records", pyRepr(model.Name))
	}
	model.Input, model.Output = values["input"], values["output"]
	return model
}

func (p *Parser) parseModelParameters(model *ModelDecl) {
	p.expect("{")
	seen := map[string]bool{}
	for !p.accept("}") {
		key := p.identifier()
		if seen[key] {
			parseFail("Duplicate model parameter %s", pyRepr(key))
		}
		seen[key] = true
		p.expect(":")
		switch key {
		case "stop":
			p.expect("[")
			model.Stop = []string{}
			for !p.accept("]") {
				model.Stop = append(model.Stop, p.stringOrIdentifier())
				p.accept(",")
			}
			continue
		case "max_output_tokens":
			token := p.advance()
			if token.Kind != "NUMBER" || !isDigits(token.Value) {
				parseFail("Model max_output_tokens must be a positive integer")
			}
			n, _ := strconv.Atoi(token.Value)
			model.MaxOutputTokens = &n
			continue
		case "temperature", "top_p", "frequency_penalty", "presence_penalty":
		default:
			parseFail("Unknown model parameter %s", pyRepr(key))
		}
		sign := 1.0
		if p.accept("-") {
			sign = -1
		}
		token := p.advance()
		if token.Kind != "NUMBER" {
			parseFail("Model %s must be a number", key)
		}
		f, _ := strconv.ParseFloat(token.Value, 64)
		value := sign * f
		switch key {
		case "temperature":
			model.Temperature = &value
		case "top_p":
			model.TopP = &value
		case "frequency_penalty":
			model.FrequencyPenalty = &value
		default:
			model.PresencePenalty = &value
		}
	}
}
