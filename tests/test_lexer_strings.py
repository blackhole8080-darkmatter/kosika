"""Lexer string decoding: escapes decode, literal non-ASCII survives."""

import unittest

from kosika.lexer import lex

BS = chr(92)


def _first_string(source: str) -> str:
    return next(token.value for token in lex(source) if token.kind == "STRING")


class LexerStringTests(unittest.TestCase):
    def test_ascii_escapes_still_decode(self):
        self.assertEqual(_first_string(f'x "a{BS}nb"'), "a\nb")
        self.assertEqual(_first_string(f'x "a{BS}tb"'), "a\tb")
        self.assertEqual(_first_string(f'x "a{BS}rb"'), "a\rb")
        self.assertEqual(_first_string(f'x "a{BS}fb"'), "a\x0cb")
        self.assertEqual(_first_string(f'x "a{BS}0b"'), "a\x00b")
        self.assertEqual(_first_string(f'x "a{BS}"b"'), 'a"b')
        self.assertEqual(_first_string(f'x "a{BS}{BS}b"'), "a\\b")
        self.assertEqual(_first_string(f'x "a{BS}xb41b"'), "a´1b")
        self.assertEqual(_first_string(f'x "a{BS}141b"'), "aab")
        self.assertEqual(_first_string(f'x "caf{BS}u00e9"'), "caf\u00e9")
        self.assertEqual(_first_string(f'x "caf{BS}U000000E9"'), "caf\u00e9")

    def test_literal_non_ascii_is_preserved(self):
        # Whole-token unicode_escape produced mojibake for these inputs.
        self.assertEqual(_first_string('x "caf\u00e9"'), "caf\u00e9")
        self.assertEqual(_first_string('x "caf\u65e5"'), "caf\u65e5")
        self.assertEqual(_first_string('x "\U0001f389"'), "\U0001f389")
        self.assertEqual(_first_string('x "caf\u0081"'), "caf\u0081")

    def test_escape_before_non_ascii_is_literal(self):
        # The tokenizer's escape matcher cannot know these two characters as a
        # Python escape: both must survive unchanged.
        self.assertEqual(_first_string(f'x "caf{BS}\u2013done"'), f"caf{BS}\u2013done")

    def test_lone_backslash_before_quote_never_lexes(self):
        # The token grammar consumes a backslash only when followed by another
        # character, so a string cannot end in a lone backslash.
        with self.assertRaises(ValueError):
            lex(f'x "end{BS}"')


if __name__ == "__main__":
    unittest.main()
