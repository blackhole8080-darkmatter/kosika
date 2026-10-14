package main

// The lexer: a program's text in, its tokens out.
//
// It gives the same tokens as the reference engine's lexer for every text
// the conformance suite holds. Where the reference has a quirk that
// programs can see, the quirk is kept and named here, so the two engines
// cannot drift apart silently:
//
//   - a line break inside a string does not count as a new line;
//   - a backslash before a character Kosika gives no meaning to is kept,
//     so "C:\Users" is what it looks like.

import (
	"fmt"
	"strings"
)

type Token struct {
	Kind   string `json:"kind"`
	Value  string `json:"value"`
	Line   int    `json:"line"`
	Column int    `json:"column"`
}

// A LexError says where a text stopped being Kosika.
type LexError struct {
	Line, Column int
	Excerpt      string
}

func (e *LexError) Error() string {
	return fmt.Sprintf("Unexpected character at %d:%d: %s", e.Line, e.Column, e.Excerpt)
}

func isDigit(r rune) bool  { return r >= '0' && r <= '9' }
func isLetter(r rune) bool { return r == '_' || (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') }
func isOctal(r rune) bool  { return r >= '0' && r <= '7' }
func isHex(r rune) bool {
	return isDigit(r) || (r >= 'a' && r <= 'f') || (r >= 'A' && r <= 'F')
}

func hexRun(text []rune, start, most int) int {
	count := 0
	for count < most && start+count < len(text) && isHex(text[start+count]) {
		count++
	}
	return count
}

func digitsValue(digits []rune, base int) int {
	value := 0
	for _, r := range digits {
		value = value*base + strings.IndexRune("0123456789abcdef", toLower(r))
	}
	return value
}

func toLower(r rune) rune {
	if r >= 'A' && r <= 'Z' {
		return r + ('a' - 'A')
	}
	return r
}

// readString reads a string from its opening quote. It gives the decoded
// value and how many characters of the text it took; ok is false when no
// string starts here: it is never closed, or a backslash ends a line.
func readString(text []rune, start int) (value string, length int, ok bool) {
	var out strings.Builder
	at := start + 1
	for at < len(text) {
		c := text[at]
		if c == '"' {
			return out.String(), at - start + 1, true
		}
		if c != '\\' {
			out.WriteRune(c)
			at++
			continue
		}
		if at+1 >= len(text) || text[at+1] == '\n' {
			return "", 0, false
		}
		next := text[at+1]
		took := 1
		switch {
		case next == 'n':
			out.WriteRune('\n')
		case next == 't':
			out.WriteRune('\t')
		case next == 'r':
			out.WriteRune('\r')
		case next == '\\':
			out.WriteRune('\\')
		case next == '"':
			out.WriteRune('"')
		case next == '\'':
			out.WriteRune('\'')
		case next == 'a':
			out.WriteRune(7)
		case next == 'b':
			out.WriteRune(8)
		case next == 'f':
			out.WriteRune(12)
		case next == 'v':
			out.WriteRune(11)
		case isOctal(next):
			end := at + 2
			for end < len(text) && end < at+4 && isOctal(text[end]) {
				end++
			}
			took = end - at - 1
			out.WriteRune(rune(digitsValue(text[at+1:end], 8)))
		case next == 'x' && hexRun(text, at+2, 2) == 2:
			took = 3
			out.WriteRune(rune(digitsValue(text[at+2:at+4], 16)))
		case next == 'u' && hexRun(text, at+2, 4) == 4:
			took = 5
			out.WriteRune(rune(digitsValue(text[at+2:at+6], 16)))
		case next == 'U' && hexRun(text, at+2, 8) == 8 && digitsValue(text[at+2:at+10], 16) <= 0x10FFFF:
			took = 9
			out.WriteRune(rune(digitsValue(text[at+2:at+10], 16)))
		default:
			// Not an escape Kosika knows: both characters stay.
			out.WriteRune('\\')
			out.WriteRune(next)
		}
		at += 1 + took
	}
	return "", 0, false
}

func excerpt(text []rune, start int) string {
	end := start + 20
	if end > len(text) {
		end = len(text)
	}
	return string(text[start:end])
}

// Lex gives the tokens of a text, ending with EOF.
func Lex(source string) ([]Token, error) {
	text := []rune(source)
	count := len(text)
	tokens := []Token{}
	at, line, column := 0, 1, 1
	for at < count {
		c := text[at]
		var next rune
		if at+1 < count {
			next = text[at+1]
		}
		switch {
		case c == ' ' || c == '\t' || c == '\r':
			at++
			column++
		case c == '/' && next == '/':
			end := at
			for end < count && text[end] != '\n' {
				end++
			}
			column += end - at
			at = end
		case c == '\n':
			at++
			line++
			column = 1
		case c == '-' && next == '>':
			tokens = append(tokens, Token{"ARROW", "->", line, column})
			at += 2
			column += 2
		case next == '=' && strings.ContainsRune("=!<>+-*/", c):
			tokens = append(tokens, Token{"OP", string([]rune{c, next}), line, column})
			at += 2
			column += 2
		case c == '"':
			value, length, ok := readString(text, at)
			if !ok {
				return tokens, &LexError{line, column, excerpt(text, at)}
			}
			tokens = append(tokens, Token{"STRING", value, line, column})
			at += length
			column += length
		case isDigit(c):
			end := at
			for end < count && isDigit(text[end]) {
				end++
			}
			if end+1 < count && text[end] == '.' && isDigit(text[end+1]) {
				end++
				for end < count && isDigit(text[end]) {
					end++
				}
			}
			tokens = append(tokens, Token{"NUMBER", string(text[at:end]), line, column})
			column += end - at
			at = end
		case isLetter(c):
			end := at
			for end < count && (isLetter(text[end]) || isDigit(text[end])) {
				end++
			}
			tokens = append(tokens, Token{"IDENT", string(text[at:end]), line, column})
			column += end - at
			at = end
		case strings.ContainsRune("{}[]():,.?=+-*/%<>", c):
			tokens = append(tokens, Token{"SYMBOL", string(c), line, column})
			at++
			column++
		default:
			return tokens, &LexError{line, column, excerpt(text, at)}
		}
	}
	return append(tokens, Token{"EOF", "", line, column}), nil
}
