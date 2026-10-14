package main

// Reading a model declaration, as kosika/parser.py reads one. A general
// model, which takes and gives text in records the language supplies, is
// not read yet.

import "strconv"

func (p *Parser) parseModel() *ModelDecl {
	opening := p.expect("model")
	model := &ModelDecl{Name: p.identifier()}
	general := func() {
		parseFail("kosika-native cannot read a general model yet (at %d:%d); give the model input and output records", opening.Line, opening.Column)
	}
	if !p.check("{") {
		general()
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
		}
	}
	if len(values) == 0 {
		general()
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
