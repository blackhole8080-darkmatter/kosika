# The coder swarm's scorecard

The same requests for every model version, so their scores can be compared.
None of these methods is in Kosika: each run adds them to a fresh copy and
throws the copy away. Each request carries examples, and the coder writes the
tests from them, so a model is scored on the method it writes, never on
tests it invents. `python scorecard.py --label NAME` runs them all.

- [ ] add a Text method surround(left: Text, right: Text) that gives the text with left before it and right after it, giving a Text; examples: `"x".surround("<", ">")` is `"<x>"`, `"".surround("a", "b")` is `"ab"`, `"mid".surround("", "!")` is `"mid!"`
- [ ] add a Number method is_even() that says whether a whole number is even, giving a Bool; a number that is not whole gives false; examples: `(4).is_even()` is `true`, `(3).is_even()` is `false`, `(0).is_even()` is `true`, `(2.5).is_even()` is `false`
- [ ] add a Text method is_upper_letter() that says whether the text is exactly one letter from A to Z, giving a Bool; examples: `"Q".is_upper_letter()` is `true`, `"q".is_upper_letter()` is `false`, `"AB".is_upper_letter()` is `false`, `"".is_upper_letter()` is `false`
- [ ] add a Text method is_blank() that says whether the text is empty or holds only spaces, tabs and line breaks, giving a Bool; examples: `"".is_blank()` is `true`, `"   ".is_blank()` is `true`, `" a ".is_blank()` is `false`
- [ ] add a Number method clamp_to(low: Number, high: Number) that gives the number kept within low and high, giving a Number; examples: `(5).clamp_to(0, 3)` is `3`, `(-1).clamp_to(0, 3)` is `0`, `(2).clamp_to(0, 3)` is `2`
- [ ] add a Text method count_of(part: Text) that gives how many times part appears in the text without overlapping, giving a Number; part must not be empty; examples: `"banana".count_of("an")` is `2`, `"aaaa".count_of("aa")` is `2`, `"abc".count_of("z")` is `0`
- [ ] add a Text method first_word() that gives the first word of the text, as words() finds the words, or "" when it has none, giving a Text; examples: `"hello big world".first_word()` is `"hello"`, `"  spaced out".first_word()` is `"spaced"`, `"".first_word()` is `""`
- [ ] add a Text method vowel_count() that gives how many of the letters a, e, i, o and u, in either case, the text has, giving a Number; examples: `"Kosika".vowel_count()` is `3`, `"rhythm".vowel_count()` is `0`, `"AEIOU".vowel_count()` is `5`
